import asyncio
from asb_api.providers.base import ProxyProviderInterface
from asb_api.fingerprint.generator import FingerprintGenerator
from .worker import ASBWorker


class WorkerPool:
    def __init__(
        self,
        size: int,
        provider: ProxyProviderInterface,
        fingerprint_generator: FingerprintGenerator,
        fallback_provider: ProxyProviderInterface | None = None,
    ):
        self.size = size
        self.semaphore = asyncio.Semaphore(size)
        self.workers = [
            ASBWorker(f"worker-{i}", provider, fingerprint_generator, fallback_provider=fallback_provider)
            for i in range(size)
        ]

    async def start_all(self):
        for w in self.workers:
            await w.start()

    async def stop_all(self):
        for w in self.workers:
            await w.stop()

    async def acquire(self, region: str | None = None) -> ASBWorker:
        await self.semaphore.acquire()
        for w in self.workers:
            if not w._busy:
                w._busy = True
                return w
        return self.workers[0]

    def release(self, worker: ASBWorker):
        worker._busy = False
        self.semaphore.release()


class RegionWorkerPool:
    def __init__(
        self,
        workers_per_region: dict[str, int],
        provider: ProxyProviderInterface,
        fingerprint_generator: FingerprintGenerator,
        default_region: str = "jp",
        fallback_provider: ProxyProviderInterface | None = None,
    ):
        self.default_region = default_region
        self.pools: dict[str, asyncio.Semaphore] = {}
        self.workers: dict[str, list[ASBWorker]] = {}
        for region, size in workers_per_region.items():
            self.pools[region] = asyncio.BoundedSemaphore(size)
            self.workers[region] = [
                ASBWorker(f"worker-{region}-{i}", provider, fingerprint_generator, fallback_provider=fallback_provider)
                for i in range(size)
            ]

    def _normalize_region(self, region: str | None = None) -> str:
        region = region or self.default_region
        if region not in self.pools:
            return self.default_region
        return region

    async def start_all(self):
        for region_workers in self.workers.values():
            for w in region_workers:
                await w.start()

    async def stop_all(self):
        for region_workers in self.workers.values():
            for w in region_workers:
                await w.stop()

    async def acquire(self, region: str | None = None) -> ASBWorker:
        region = self._normalize_region(region)
        await self.pools[region].acquire()
        for w in self.workers[region]:
            if not getattr(w, "_busy", False):
                w._busy = True
                w._lease_region = region
                return w
        self.workers[region][0]._busy = True
        self.workers[region][0]._lease_region = region
        return self.workers[region][0]

    def release(self, worker: ASBWorker, region: str | None = None):
        region = getattr(worker, "_lease_region", None) or self._normalize_region(region)
        worker._busy = False
        if hasattr(worker, "_lease_region"):
            delattr(worker, "_lease_region")
        self.pools[region].release()

    def get_status(self) -> dict:
        status = {}
        for region, workers in self.workers.items():
            active = sum(1 for w in workers if getattr(w, "_busy", False))
            idle = len(workers) - active
            status[region] = {"active": active, "idle": idle}
        return status
