from .base import ProxyProviderInterface, ProxyConfig


class NullProvider(ProxyProviderInterface):
    @property
    def name(self) -> str:
        return "null"

    async def get_proxy(self, region: str | None = None) -> ProxyConfig:
        return ProxyConfig(host="DIRECT", port=0)

    async def release_proxy(self, proxy: ProxyConfig) -> None:
        pass

    async def health_check(self) -> bool:
        return True
