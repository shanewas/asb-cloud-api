from .base import ProxyProviderInterface
from .null import NullProvider
from .custom import CustomProvider


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProxyProviderInterface] = {}
        self._classes: dict[str, type] = {}

    def register(self, name: str, cls: type):
        self._classes[name] = cls

    def get(self, name: str) -> ProxyProviderInterface:
        return self._providers[name]

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
