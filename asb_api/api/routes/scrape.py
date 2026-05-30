import asyncio
from fastapi import APIRouter, HTTPException, Depends
from typing import Any
from asb_api.session.models import (
    ScrapeRequest, ScrapeResponse,
    BulkScrapeRequest, BulkItemResult, BulkScrapeResponse
)
from asb_api.workers.pool import RegionWorkerPool
from asb_api.api.auth import get_api_key, get_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.session.store import SessionStore
from asb_api.api.routes.sessions import ensure_session_owner
from asb_api.api.errors import APIError
from asb_api.security import validate_scrape_url, redact_url_for_logging

router = APIRouter()
pool: RegionWorkerPool | None = None
rate_limiter: Any = None
usage_tracker: Any = None
session_store: Any = None
MAX_BULK_ITEMS = 50
MAX_BULK_CONCURRENCY = 16


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


async def _get_key_tier(key_id: str) -> str:
    key_store = get_key_store()
    api_key = key_store.get(key_id)
    if hasattr(api_key, "__await__"):
        api_key = await api_key
    return api_key.get("tier", "free") if isinstance(api_key, dict) else getattr(api_key, "tier", "free")


async def _check_rate_limit_units(key_id: str, tier: str, units: int = 1) -> None:
    if not rate_limiter:
        return
    units = max(1, units)
    check_many = getattr(rate_limiter, "check_many", None)
    if units > 1 and callable(check_many):
        await check_many(key_id, tier=tier, count=units)
        return
    for _ in range(units):
        await rate_limiter.check(key_id, tier)


def _bulk_error_from_http_exception(exc: HTTPException) -> dict:
    if isinstance(exc.detail, dict):
        return {
            "error_code": exc.detail.get("error_code", "BAD_REQUEST"),
            "message": exc.detail.get("message", str(exc.detail)),
        }
    return {
        "error_code": "WORKER_ERROR" if exc.status_code >= 500 else "BAD_REQUEST",
        "message": str(exc.detail),
    }


# Internal helper containing the core single-scrape logic (after rate-limit check).
# Used by both the single /v1/scrape and the bulk endpoint.
async def _execute_one_scrape(
    request: ScrapeRequest,
    key_id: str,
    skip_rate_limit_check: bool = False,
) -> ScrapeResponse:
    """Core execution path for one scrape request. Does NOT perform rate limiting when skip_rate_limit_check=True."""
    # URL safety validation (from #8) — applies to both single scrape and every bulk item.
    # Happens before rate limiting or worker acquisition.
    validate_scrape_url(request.url)

    if not skip_rate_limit_check and rate_limiter:
        tier = await _get_key_tier(key_id)
        await _check_rate_limit_units(key_id, tier)

    if not pool:
        raise APIError(503, "SERVICE_NOT_INITIALIZED", "Worker pool or backing service is unavailable")

    session = None
    if request.session_id and not session_store:
        raise APIError(503, "SERVICE_NOT_INITIALIZED", "Session store not initialized")

    if request.session_id:
        session = await session_store.get(request.session_id)
        if not session:
            raise APIError(404, "SESSION_NOT_FOUND", "Session does not exist, expired, or is not owned by the key")
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
            if hasattr(usage_tracker, "record"):
                domain = None
                try:
                    domain = redact_url_for_logging(request.url, domains_only=True)
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


@router.post("/v1/scrape", response_model=ScrapeResponse)
async def scrape(
    request: ScrapeRequest,
    key_id: str = Depends(get_api_key),
):
    return await _execute_one_scrape(request, key_id, skip_rate_limit_check=False)


@router.post("/v1/bulk-scrape", response_model=BulkScrapeResponse)
async def bulk_scrape(
    payload: BulkScrapeRequest,
    key_id: str = Depends(get_api_key),
):
    items = payload.items
    if not items:
        raise APIError(400, "BAD_REQUEST", "items array must not be empty")
    if len(items) > MAX_BULK_ITEMS:
        raise APIError(400, "BAD_REQUEST", f"bulk scrape supports at most {MAX_BULK_ITEMS} items")

    # Batch rate limit check: consume exactly N = len(items) quota units up front.
    # If any single check fails (429), the whole batch is rejected before any execution.
    # This matches the documented contract (per-item accounting for bulk).
    if rate_limiter:
        tier = await _get_key_tier(key_id)
        await _check_rate_limit_units(key_id, tier, units=len(items))

    # Execute items with bounded concurrency
    max_conc = max(1, min(payload.max_concurrency or 8, MAX_BULK_CONCURRENCY))
    semaphore = asyncio.Semaphore(max_conc)

    async def _run_one(index: int, req: ScrapeRequest) -> BulkItemResult:
        async with semaphore:
            try:
                res = await _execute_one_scrape(req, key_id, skip_rate_limit_check=True)
                return BulkItemResult(index=index, result=res, error=None)
            except HTTPException as e:
                return BulkItemResult(index=index, result=None, error=_bulk_error_from_http_exception(e))
            except Exception as e:
                return BulkItemResult(
                    index=index,
                    result=None,
                    error={"error_code": "INTERNAL_ERROR", "message": str(e)}
                )

    tasks = [_run_one(i, item) for i, item in enumerate(items)]
    results = await asyncio.gather(*tasks)

    succeeded = sum(1 for r in results if r.result is not None and r.result.status == "success")
    failed = len(results) - succeeded

    return BulkScrapeResponse(
        results=results,
        summary={
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        },
    )
