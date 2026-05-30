import asyncio
import uuid
import time
from asb_api.providers.base import ProxyProviderInterface
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.session.models import ScrapeRequest, ScrapeResponse, ScrapeMetadata
from .asb_runner import ASBRunner


class ASBWorker:
    def __init__(
        self,
        worker_id: str,
        provider: ProxyProviderInterface,
        fingerprint_generator: FingerprintGenerator,
        screenshot_dir: str | None = None,
    ):
        self.worker_id = worker_id
        self.provider = provider
        self.fingerprint_generator = fingerprint_generator
        self.screenshot_dir = screenshot_dir
        self.runner: ASBRunner | None = None
        self._busy = False

    async def start(self):
        self.runner = ASBRunner()
        await self.runner.__aenter__()

    async def stop(self):
        if self.runner:
            await self.runner.close()

    async def scrape(self, request: ScrapeRequest) -> ScrapeResponse:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start = time.monotonic()
        proxy = None
        fp = None

        try:
            if self.provider.name != "null":
                proxy = await self.provider.get_proxy(request.region)

            fp = self.fingerprint_generator.get(
                request.fingerprint or "general"
            )

            result = await self.runner.run(
                url=request.url,
                method=request.method,
                headers=request.headers,
                data=request.data,
                proxy=proxy,
                fingerprint=fp,
                timeout=request.timeout,
                screenshot=request.screenshot,
                screenshot_dir=self.screenshot_dir,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return ScrapeResponse(
                request_id=request_id,
                status="success",
                html=result["html"],
                screenshot_url=result.get("screenshot_url"),
                cookies=result.get("cookies", {}),
                headers=result.get("headers", {}),
                metadata=ScrapeMetadata(
                    request_id=request_id,
                    provider=self.provider.name,
                    region=request.region,
                    fingerprint_id=fp.user_agent[:50],
                    worker_id=self.worker_id,
                    duration_ms=duration_ms,
                    block_detected=result.get("block_detected", False),
                    retries=0,
                ),
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ScrapeResponse(
                request_id=request_id,
                status="error",
                error_code="WORKER_ERROR",
                message=str(e),
                metadata=ScrapeMetadata(
                    request_id=request_id,
                    provider=self.provider.name,
                    region=request.region,
                    fingerprint_id=getattr(fp, "user_agent", "")[:50] if fp else "",
                    worker_id=self.worker_id,
                    duration_ms=duration_ms,
                    block_detected=False,
                    retries=0,
                ),
            )
        finally:
            if proxy:
                await self.provider.release_proxy(proxy)
