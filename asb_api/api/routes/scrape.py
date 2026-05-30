from fastapi import APIRouter, HTTPException, Depends
from urllib.parse import urlparse
from typing import Any
from asb_api.session.models import ScrapeRequest, ScrapeResponse
from asb_api.workers.pool import RegionWorkerPool
from asb_api.api.auth import get_api_key, get_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.session.store import SessionStore
from asb_api.api.routes.sessions import ensure_session_owner

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
    if isinstance(api_key, dict):
        tier = api_key.get("tier", "free")
    else:
        tier = getattr(api_key, "tier", "free")

    if rate_limiter:
        await rate_limiter.check(key_id, tier)

    if not pool:
        raise HTTPException(status_code=503, detail="Service not initialized")

    session = None
    if request.session_id and not session_store:
        raise HTTPException(status_code=503, detail="Session store not initialized")

    if request.session_id:
        session = await session_store.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        ensure_session_owner(session, key_id)
        if request.session_type == "stateful_reset":
            await session_store.update_cookies(request.session_id, {})
            session.cookies = {}

    worker = await pool.acquire(request.region)
    try:
        if session and session.cookies:
            request.headers = request.headers or {}
            cookie_header = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
            existing = request.headers.get("Cookie")
            request.headers["Cookie"] = f"{existing}; {cookie_header}" if existing else cookie_header

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
