from fastapi import APIRouter, HTTPException, Depends
from asb_api.session.models import ScrapeRequest, ScrapeResponse
from asb_api.workers.pool import RegionWorkerPool
from asb_api.api.auth import get_api_key, get_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.session.store import SessionStore

router = APIRouter()
pool: RegionWorkerPool | None = None
rate_limiter: SlidingWindowLimiter | None = None
usage_tracker: UsageTracker | None = None
session_store: SessionStore | None = None


def set_pool(p: RegionWorkerPool):
    global pool
    pool = p


def set_rate_limiter(rl: SlidingWindowLimiter):
    global rate_limiter
    rate_limiter = rl


def set_usage_tracker(ut: UsageTracker):
    global usage_tracker
    usage_tracker = ut


def set_session_store_for_scrape(ss: SessionStore):
    global session_store
    session_store = ss


@router.post("/v1/scrape", response_model=ScrapeResponse)
async def scrape(
    request: ScrapeRequest,
    key_id: str = Depends(get_api_key),
):
    key_store = get_key_store()
    api_key = key_store.get(key_id)
    tier = api_key.tier if api_key else "free"

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
            await usage_tracker.increment(key_id)

        return result
    finally:
        pool.release(worker, request.region)
