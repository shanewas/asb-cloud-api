import asyncio
from .base import ProxyProviderInterface, ProxyConfig, PoolExhaustedError


class CustomProvider(ProxyProviderInterface):
    def __init__(self, proxies: list[dict]):
        self._proxies = [self._build_proxy(p) for p in proxies]
        self._index = 0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "custom"

    def _build_proxy(self, cfg: dict) -> ProxyConfig:
        return ProxyConfig(
            host=cfg.get("host", ""),
            port=cfg.get("port", 8080),
            username=cfg.get("username"),
            password=cfg.get("password"),
            protocol=cfg.get("protocol", "http"),
            region=cfg.get("region"),
        )

    async def get_proxy(self, region: str | None = None) -> ProxyConfig:
        if not self._proxies:
            raise PoolExhaustedError("No custom proxies configured")
        async with self._lock:
            proxy = self._proxies[self._index]
            self._index = (self._index + 1) % len(self._proxies)
            return proxy

    async def release_proxy(self, proxy: ProxyConfig) -> None:
        pass

    async def health_check(self) -> bool:
        return len(self._proxies) > 0
