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
    ):
        self.size = size
        self.semaphore = asyncio.Semaphore(size)
        self.workers = [
            ASBWorker(f"worker-{i}", provider, fingerprint_generator)
            for i in range(size)
        ]

    async def start_all(self):
        for w in self.workers:
            await w.start()

    async def stop_all(self):
        for w in self.workers:
            await w.stop()

    async def acquire(self) -> ASBWorker:
        await self.semaphore.acquire()
        for w in self.workers:
            if not w._busy:
                w._busy = True
                return w
        return self.workers[0]

    def release(self, worker: ASBWorker):
        worker._busy = False
        self.semaphore.release()
