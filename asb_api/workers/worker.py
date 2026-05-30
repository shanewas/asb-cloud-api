import asyncio
import uuid
import time
from asb_api.providers.base import ProxyProviderInterface, PoolExhaustedError, ProviderError
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
        fallback_provider: ProxyProviderInterface | None = None,
    ):
        self.worker_id = worker_id
        self.provider = provider
        self.fallback_provider = fallback_provider
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
        used_provider_name = self.provider.name

        try:
            # Proxy acquisition with fallback support
            if self.provider.name != "null":
                proxy_breaker = self.provider
                try:
                    proxy = await proxy_breaker.get_proxy(request.region)
                except (PoolExhaustedError, ProviderError) as primary_err:
                    if self.fallback_provider and self.fallback_provider is not self.provider:
                        try:
                            proxy_breaker = self.fallback_provider
                            proxy = await proxy_breaker.get_proxy(request.region)
                            used_provider_name = self.fallback_provider.name
                        except (PoolExhaustedError, ProviderError, Exception):
                            # Both primary and fallback failed; re-raise the primary error
                            # so the caller sees a clear failure from the configured primary.
                            raise primary_err
                    else:
                        raise
                else:
                    # Success on primary
                    pass
                # Remember which breaker we got the proxy from for release
                self._last_proxy_breaker = proxy_breaker  # type: ignore[attr-defined]
            else:
                self._last_proxy_breaker = None  # type: ignore[attr-defined]

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
                    provider=used_provider_name,
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
                    provider=used_provider_name,
                    region=request.region,
                    fingerprint_id=getattr(fp, "user_agent", "")[:50] if fp else "",
                    worker_id=self.worker_id,
                    duration_ms=duration_ms,
                    block_detected=False,
                    retries=0,
                ),
            )
        finally:
            # Release to the correct breaker (primary or fallback) that supplied the proxy
            last_breaker = getattr(self, "_last_proxy_breaker", None)
            if proxy and last_breaker:
                await last_breaker.release_proxy(proxy)
            self._last_proxy_breaker = None  # type: ignore[attr-defined]
