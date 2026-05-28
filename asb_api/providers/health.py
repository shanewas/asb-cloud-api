import asyncio
import time
from typing import Literal
from .base import ProxyProviderInterface, PoolExhaustedError, ProviderError


class CircuitBreaker:
    def __init__(self, provider: ProxyProviderInterface, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure: float | None = None
        self.state: Literal["UP", "DEGRADED", "DOWN", "RECOVERING"] = "UP"
        self._lock = asyncio.Lock()

    async def record_success(self):
        async with self._lock:
            self.failures = 0
            if self.state == "RECOVERING":
                self.state = "UP"

    async def record_failure(self):
        async with self._lock:
            self.failures += 1
            self.last_failure = time.monotonic()
            if self.failures >= self.failure_threshold:
                self.state = "DOWN"

    async def get_proxy(self, region: str | None = None):
        async with self._lock:
            if self.state == "DOWN":
                if self.last_failure and (time.monotonic() - self.last_failure) > self.recovery_timeout:
                    self.state = "RECOVERING"
                else:
                    raise PoolExhaustedError(f"Provider {self.provider.name} is DOWN, try fallback")
        try:
            result = await self.provider.get_proxy(region)
            await self.record_success()
            return result
        except (PoolExhaustedError, ProviderError):
            await self.record_failure()
            raise

    async def release_proxy(self, proxy):
        await self.provider.release_proxy(proxy)

    async def health_check(self) -> bool:
        return await self.provider.health_check()

    @property
    def name(self) -> str:
        return self.provider.name


class ProviderHealthChecker:
    def __init__(self, breakers: dict[str, CircuitBreaker], check_interval: int = 30):
        self.breakers = breakers
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None

    async def _check_loop(self):
        while True:
            await asyncio.sleep(self.check_interval)
            for name, breaker in self.breakers.items():
                try:
                    healthy = await breaker.health_check()
                    if healthy and breaker.state in ("DOWN", "DEGRADED"):
                        breaker.state = "RECOVERING"
                    elif not healthy and breaker.state == "UP":
                        breaker.state = "DEGRADED"
                        breaker.failures = 1
                except Exception:
                    breaker.state = "DOWN"

    async def start(self):
        self._task = asyncio.create_task(self._check_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
