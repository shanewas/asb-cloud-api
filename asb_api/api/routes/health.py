from fastapi import APIRouter
from asb_api.providers.health import CircuitBreaker
from asb_api.workers.pool import WorkerPool

router = APIRouter()
_pool: WorkerPool | None = None
_breakers: dict[str, CircuitBreaker] = {}
_registry = None


def set_health_context(pool: WorkerPool, breakers: dict[str, CircuitBreaker], registry):
    global _pool, _breakers, _registry
    _pool = pool
    _breakers = breakers
    _registry = registry


@router.get("/v1/health")
async def health():
    providers_status = {}
    for name, breaker in _breakers.items():
        healthy = False
        try:
            healthy = await breaker.health_check()
        except Exception:
            pass
        providers_status[name] = {"status": breaker.state.lower(), "healthy": healthy}

    workers_status = {}
    if _pool and hasattr(_pool, "workers"):
        workers_status = _pool.get_status()

    return {
        "status": "healthy",
        "providers": providers_status,
        "workers": workers_status,
    }
