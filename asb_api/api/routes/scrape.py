from fastapi import APIRouter, HTTPException
from asb_api.session.models import ScrapeRequest, ScrapeResponse
from asb_api.workers.pool import WorkerPool

router = APIRouter()
pool: WorkerPool | None = None


def set_pool(p: WorkerPool):
    global pool
    pool = p


@router.post("/v1/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):
    if not pool:
        raise HTTPException(status_code=503, detail="Service not initialized")

    worker = await pool.acquire()
    try:
        result = await worker.scrape(request)
        return result
    finally:
        pool.release(worker)


@router.get("/v1/health")
async def health():
    return {"status": "ok", "pool_size": pool.size if pool else 0}
