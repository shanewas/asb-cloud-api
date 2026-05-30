import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.providers.health import CircuitBreaker, ProviderHealthChecker
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.workers.pool import RegionWorkerPool
from asb_api.session.store import SessionStore
from asb_api.api.auth import set_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.api.routes.scrape import router as scrape_router, set_pool, set_rate_limiter, set_usage_tracker, set_session_store_for_scrape
from asb_api.api.routes.sessions import router as sessions_router, set_session_store
from asb_api.api.routes.health import router as health_router, set_health_context
from asb_api.api.routes.usage import router as usage_router, set_usage_context
from asb_api.api.errors import install_error_handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASB Cloud API")
install_error_handlers(app)

# CORS for explicit dashboard origins only (secure default: no middleware if unset).
# Supports DASHBOARD_ORIGINS env (comma-separated) or dashboard.origins in config.yaml.
# No wildcard ever. See docs/DASHBOARD_ARCHITECTURE.md §7.
_config_for_cors = load_config()
_dash_origins_raw = os.environ.get("DASHBOARD_ORIGINS", "").strip()
if _dash_origins_raw:
    _dash_origins = [o.strip() for o in _dash_origins_raw.split(",") if o.strip()]
else:
    _dash = _config_for_cors.get("dashboard", {}) or {}
    _origins = _dash.get("origins", []) or []
    if isinstance(_origins, str):
        _dash_origins = [o.strip() for o in _origins.split(",") if o.strip()]
    else:
        _dash_origins = [str(o).strip() for o in _origins if str(o).strip()]
if _dash_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_dash_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    logging.getLogger(__name__).info(f"CORS enabled for dashboard origins: {_dash_origins}")
else:
    logging.getLogger(__name__).info("CORS disabled (no dashboard.origins / DASHBOARD_ORIGINS; secure default)")

app.include_router(scrape_router)
app.include_router(sessions_router)
app.include_router(health_router)
app.include_router(usage_router)

# Self-hosted license verification does not require Stripe.
from asb_api.api.routes import licenses
app.include_router(licenses.router)

_worker_pool: RegionWorkerPool | None = None
_health_checker: ProviderHealthChecker | None = None
_db = None

if load_config().get("billing", {}).get("enabled", False):
    from asb_api.api.routes import checkout, webhooks, billing as billing_routes

    app.include_router(checkout.router)
    app.include_router(webhooks.router)
    app.include_router(billing_routes.router)


@app.on_event("startup")
async def startup():
    global _worker_pool, _health_checker, _db
    config = load_config()
    billing_enabled = config.get("billing", {}).get("enabled", False)

    # === Phase 2: Connect to PostgreSQL and run migrations ===
    db_cfg = config.get("database", {})
    dsn = db_cfg.get("dsn") or os.environ.get("DATABASE_URL")
    if dsn:
        from asb_api.db import db, run_migrations

        _db = db
        # Reconfigure singleton pool sizes from config if provided
        db.dsn = dsn
        db.min_pool = db_cfg.get("min_pool", 5)
        db.max_pool = db_cfg.get("max_pool", 20)
        await db.connect()
        await run_migrations()
        logger.info("PostgreSQL connected and migrations applied")
    else:
        logger.warning("DATABASE_URL not set — running without persistence (dev only)")

    registry = ProviderRegistry()
    registry.initialize_from_config(config.get("providers", {}))

    priority_cfg = config.get("provider_priority", {})
    primary_name = priority_cfg.get("primary", "null")
    fallback_name = priority_cfg.get("fallback", "null")

    breakers = {}
    for name in registry.list_providers():
        provider = registry.get(name)
        breakers[name] = CircuitBreaker(provider, failure_threshold=3, recovery_timeout=60)

    primary_breaker = breakers.get(primary_name, breakers.get("null"))
    if not primary_breaker:
        primary_breaker = CircuitBreaker(registry.get("null"))

    health_checker = ProviderHealthChecker(breakers, check_interval=30)
    await health_checker.start()
    _health_checker = health_checker

    fp_gen = FingerprintGenerator(config.get("fingerprint", {}).get("presets", {}))

    pool_cfg = config.get("pool", {})
    workers_per_region = pool_cfg.get("workers_per_region", {"jp": 5})
    default_region = pool_cfg.get("default_region", "jp")

    pool = RegionWorkerPool(
        workers_per_region=workers_per_region,
        provider=primary_breaker,
        fingerprint_generator=fp_gen,
        default_region=default_region,
    )
    await pool.start_all()
    _worker_pool = pool
    set_pool(pool)

    security_cfg = config.get("security", {})
    encryption_key = security_cfg.get("cookie_encryption_key")

    # Wire security config (URL safety + log redaction rules) for use by routes and utilities
    from asb_api.security import set_security_config
    set_security_config(config)

    # === Phase 2: Wire PostgreSQL-backed stores (replace in-memory) ===
    if dsn:
        from asb_api.db.auth_store import PostgresKeyStore
        from asb_api.db.rate_limiter import PostgresRateLimiter
        from asb_api.db.session_store import PostgresSessionStore
        from asb_api.db.usage import PostgresUsageTracker
        from asb_api.db.audit import AuditLogger

        key_store = PostgresKeyStore()
        # Create a default test key on startup (idempotent if exists; for dev)
        try:
            raw, _ = await key_store.create(tier="free", owner_email="default@asb.local")
            logger.info(f"Default test API key created: {raw[:12]}...")
        except Exception:
            # Key may already exist from previous run (unique hash) — ignore
            pass
        set_key_store(key_store)

        if billing_enabled:
            # Phase 3: wire webhook store only when the webhook route is mounted.
            from asb_api.api.routes.webhooks import set_store as set_webhook_store
            set_webhook_store(key_store)

        limits_cfg = config.get("rate_limits", {})
        ut = PostgresUsageTracker()
        set_usage_tracker(ut)
        set_usage_context(ut, limits_cfg)

        limiter = PostgresRateLimiter(limits_by_tier=limits_cfg, usage_tracker=ut)
        set_rate_limiter(limiter)

        s_store = PostgresSessionStore(
            encryption_key=encryption_key,
            ttl_seconds=pool_cfg.get("session_ttl_seconds", 300),
        )
        set_session_store(s_store)
        set_session_store_for_scrape(s_store)


        # Audit logger available via from asb_api.db import AuditLogger
        _ = AuditLogger()
    else:
        # Fallback to legacy in-memory (only when no DB)
        from asb_api.api.auth import InMemoryKeyStore
        key_store = InMemoryKeyStore()
        raw, _ = key_store.create(tier="free", owner_email="default@asb.local")
        logger.info(f"Default test API key created: {raw[:12]}...")
        set_key_store(key_store)

        limits_cfg = config.get("rate_limits", {})
        limiter = SlidingWindowLimiter(limits_by_tier=limits_cfg)
        set_rate_limiter(limiter)

        s_store = SessionStore(
            encryption_key=encryption_key,
            ttl_seconds=pool_cfg.get("session_ttl_seconds", 300),
        )
        set_session_store(s_store)
        set_session_store_for_scrape(s_store)

        ut = UsageTracker()
        set_usage_tracker(ut)
        set_usage_context(ut, limits_cfg)

    set_health_context(pool, breakers, registry)

    logger.info(f"ASB Cloud API started with primary={primary_name}, regions={list(workers_per_region.keys())}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down ASB Cloud API...")
    if _health_checker:
        try:
            await _health_checker.stop()
        except Exception:
            logger.exception("Failed to stop provider health checker")
    if _worker_pool:
        try:
            await _worker_pool.stop_all()
        except Exception:
            logger.exception("Failed to stop worker pool")
    if _db:
        try:
            await _db.disconnect()
        except Exception:
            logger.exception("Failed to disconnect database")


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "asb_api.__main__:app",
        host=cfg.get("app", {}).get("host", "0.0.0.0"),
        port=cfg.get("app", {}).get("port", 8000),
        reload=False,
    )
