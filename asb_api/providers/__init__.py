from .base import ProxyProviderInterface
from .null import NullProvider
from .custom import CustomProvider
from .decodo import DecodoProvider
from .brightdata import BrightDataProvider


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProxyProviderInterface] = {}
        self._classes: dict[str, type] = {}

    def register(self, name: str, cls: type):
        self._classes[name] = cls

    def get(self, name: str) -> ProxyProviderInterface:
        return self._providers[name]

    def get_all_providers(self) -> dict[str, ProxyProviderInterface]:
        return dict(self._providers)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def initialize_from_config(self, config: dict) -> None:
        for provider_name, provider_cfg in config.items():
            if not provider_cfg.get("enabled", False):
                continue
            if provider_name == "null":
                self._providers["null"] = NullProvider()
            elif provider_name == "custom":
                proxies = provider_cfg.get("proxies", [])
                self._providers["custom"] = CustomProvider(proxies)
            elif provider_name == "decodo":
                api_key = provider_cfg.get("api_key", "")
                pool_size = provider_cfg.get("pool_size", 10)
                regions = provider_cfg.get("regions", ["jp", "us", "eu"])
                default_region = provider_cfg.get("default_region", "jp")
                self._providers["decodo"] = DecodoProvider(
                    api_key=api_key,
                    pool_size=pool_size,
                    regions=regions,
                    default_region=default_region,
                )
            elif provider_name == "brightdata":
                api_key = provider_cfg.get("api_key", "")
                zones = provider_cfg.get("zones", ["residential"])
                self._providers["brightdata"] = BrightDataProvider(
                    api_key=api_key,
                    zones=zones,
                )

    async def start_all(self) -> None:
        for provider in self._providers.values():
            start = getattr(provider, "start", None)
            if callable(start):
                await start()

    async def stop_all(self) -> None:
        for provider in self._providers.values():
            stop = getattr(provider, "stop", None)
            if callable(stop):
                await stop()
