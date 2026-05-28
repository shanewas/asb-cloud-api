from fastapi import APIRouter, HTTPException, Depends
from urllib.parse import urlparse
from typing import Any
from asb_api.session.models import ScrapeRequest, ScrapeResponse
from asb_api.workers.pool import RegionWorkerPool
from asb_api.api.auth import get_api_key, get_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.session.store import SessionStore

router = APIRouter()
pool: RegionWorkerPool | None = None
rate_limiter: Any = None
usage_tracker: Any = None
session_store: Any = None


def set_pool(p: RegionWorkerPool):
    global pool
    pool = p


def set_rate_limiter(rl: Any):
    global rate_limiter
    rate_limiter = rl


def set_usage_tracker(ut: Any):
    global usage_tracker
    usage_tracker = ut


def set_session_store_for_scrape(ss: Any):
    global session_store
    session_store = ss


@router.post("/v1/scrape", response_model=ScrapeResponse)
async def scrape(
    request: ScrapeRequest,
    key_id: str = Depends(get_api_key),
):
    key_store = get_key_store()
    # Support async (Postgres) or sync (InMemory) get()
    api_key = key_store.get(key_id)
    if hasattr(api_key, "__await__"):
        api_key = await api_key  # in case future
    tier = api_key.tier if api_key else (api_key.get("tier") if isinstance(api_key, dict) else "free")

    if rate_limiter:
        await rate_limiter.check(key_id, tier)

    if not pool:
        raise HTTPException(status_code=503, detail="Service not initialized")

    if request.session_id and session_store:
        session = await session_store.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if request.session_type == "stateful_reset":
            await session_store.update_cookies(request.session_id, {})

    worker = await pool.acquire(request.region)
    try:
        if request.session_id and session_store:
            session = await session_store.get(request.session_id)
            if session and session.cookies:
                request.headers = request.headers or {}
                cookie_header = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
                request.headers["Cookie"] = request.headers.get("Cookie", "") + ";" + cookie_header

        result = await worker.scrape(request)

        if request.session_id and session_store:
            await session_store.update_cookies(request.session_id, result.cookies)
            await session_store.increment_count(request.session_id)

        if usage_tracker:
            # Phase 2 rich record if available (PostgresUsageTracker), else legacy increment
            if hasattr(usage_tracker, "record"):
                domain = None
                try:
                    domain = urlparse(request.url).netloc or request.url.split("/")[2] if "://" in request.url else request.url.split("/")[0]
                except Exception:
                    domain = None
                meta = getattr(result, "metadata", None)
                req_id = meta.request_id if meta else "unknown"
                status = getattr(result, "status", "success")
                dur = meta.duration_ms if meta else 0
                block = meta.block_detected if meta else False
                reg = meta.region if meta else request.region
                await usage_tracker.record(
                    key_id=key_id,
                    request_id=req_id,
                    domain=domain,
                    status=status,
                    duration_ms=dur,
                    block_detected=block,
                    region=reg,
                )
            else:
                await usage_tracker.increment(key_id)

        return result
    finally:
        pool.release(worker, request.region)
