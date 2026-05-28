import logging
from fastapi import FastAPI
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.providers.health import CircuitBreaker, ProviderHealthChecker
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.workers.pool import RegionWorkerPool
from asb_api.session.store import SessionStore
from asb_api.api.auth import InMemoryKeyStore, set_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.api.routes.scrape import router as scrape_router, set_pool, set_rate_limiter, set_usage_tracker, set_session_store_for_scrape
from asb_api.api.routes.sessions import router as sessions_router, set_session_store
from asb_api.api.routes.health import router as health_router, set_health_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASB Cloud API")
app.include_router(scrape_router)
app.include_router(sessions_router)
app.include_router(health_router)


@app.on_event("startup")
async def startup():
    config = load_config()

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
    s_store = SessionStore(
        encryption_key=encryption_key,
        ttl_seconds=pool_cfg.get("session_ttl_seconds", 300),
    )
    set_session_store(s_store)
    set_session_store_for_scrape(s_store)

    key_store = InMemoryKeyStore()
    raw, _ = key_store.create(tier="free", owner_email="default@asb.local")
    logger.info(f"Default test API key created: {raw[:12]}...")
    set_key_store(key_store)

    limits_cfg = config.get("rate_limits", {})
    limiter = SlidingWindowLimiter(limits_by_tier=limits_cfg)
    set_rate_limiter(limiter)

    ut = UsageTracker()
    set_usage_tracker(ut)

    set_health_context(pool, breakers, registry)

    logger.info(f"ASB Cloud API started with primary={primary_name}, regions={list(workers_per_region.keys())}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down ASB Cloud API...")


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "asb_api.__main__:app",
        host=cfg.get("app", {}).get("host", "0.0.0.0"),
        port=cfg.get("app", {}).get("port", 8000),
        reload=False,
    )
