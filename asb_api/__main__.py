import logging
from fastapi import FastAPI
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.workers.pool import WorkerPool
from asb_api.api.routes.scrape import router as scrape_router, set_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASB Cloud API")
app.include_router(scrape_router)


@app.on_event("startup")
async def startup():
    config = load_config()

    registry = ProviderRegistry()
    registry.initialize_from_config(config.get("providers", {}))
    active = config.get("provider_priority", {}).get("primary", "null")
    provider = registry.get(active)

    fp_gen = FingerprintGenerator(config.get("fingerprint", {}).get("presets", {}))

    pool = WorkerPool(
        size=config.get("pool", {}).get("max_workers", 5),
        provider=provider,
        fingerprint_generator=fp_gen,
    )
    await pool.start_all()
    set_pool(pool)

    logger.info(f"ASB Cloud API started with provider={active}, workers={pool.size}")


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
