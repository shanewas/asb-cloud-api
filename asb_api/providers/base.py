from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


class PoolExhaustedError(Exception):
    pass


class ProviderError(Exception):
    pass


@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    protocol: Literal["http", "socks5", "socks4"] = "http"
    region: str | None = None
    sticky: bool = False
    session_token: str | None = None


class ProxyProviderInterface(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def get_proxy(self, region: str | None = None) -> ProxyConfig: ...

    @abstractmethod
    async def release_proxy(self, proxy: ProxyConfig) -> None: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
