import logging
import os
from fastapi import FastAPI
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

# Phase 3: Billing routes
from asb_api.api.routes import checkout, webhooks, billing as billing_routes, licenses

# Phase 2: PostgreSQL persistence
from asb_api.db import db, run_migrations
from asb_api.db.auth_store import PostgresKeyStore
from asb_api.db.rate_limiter import PostgresRateLimiter
from asb_api.db.session_store import PostgresSessionStore
from asb_api.db.usage import PostgresUsageTracker
from asb_api.db.audit import AuditLogger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASB Cloud API")
app.include_router(scrape_router)
app.include_router(sessions_router)
app.include_router(health_router)

# Phase 3: Billing
app.include_router(checkout.router)
app.include_router(webhooks.router)
app.include_router(billing_routes.router)
app.include_router(licenses.router)


@app.on_event("startup")
async def startup():
    config = load_config()

    # === Phase 2: Connect to PostgreSQL and run migrations ===
    db_cfg = config.get("database", {})
    dsn = db_cfg.get("dsn") or os.environ.get("DATABASE_URL")
    if dsn:
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
    set_pool(pool)

    security_cfg = config.get("security", {})
    encryption_key = security_cfg.get("cookie_encryption_key")

    # === Phase 2: Wire PostgreSQL-backed stores (replace in-memory) ===
    if dsn:
        key_store = PostgresKeyStore()
        # Create a default test key on startup (idempotent if exists; for dev)
        try:
            raw, _ = await key_store.create(tier="free", owner_email="default@asb.local")
            logger.info(f"Default test API key created: {raw[:12]}...")
        except Exception:
            # Key may already exist from previous run (unique hash) — ignore
            pass
        set_key_store(key_store)

        # Phase 3: wire webhook store (uses same PostgresKeyStore)
        from asb_api.api.routes.webhooks import set_store as set_webhook_store
        set_webhook_store(key_store)

        limits_cfg = config.get("rate_limits", {})
        ut = PostgresUsageTracker()
        set_usage_tracker(ut)

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

    set_health_context(pool, breakers, registry)

    logger.info(f"ASB Cloud API started with primary={primary_name}, regions={list(workers_per_region.keys())}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down ASB Cloud API...")
    try:
        await db.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "asb_api.__main__:app",
        host=cfg.get("app", {}).get("host", "0.0.0.0"),
        port=cfg.get("app", {}).get("port", 8000),
        reload=False,
    )
