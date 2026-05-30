import asyncio
import httpx
from .base import ProxyProviderInterface, ProxyConfig, PoolExhaustedError

DECODO_API_URL = "https://api.decodo.com/v2/proxy"
DECODO_STATUS_URL = "https://api.decodo.com/v2/status"


class DecodoProvider(ProxyProviderInterface):
    def __init__(self, api_key: str, pool_size: int = 10, regions: list[str] | None = None, default_region: str = "jp", refresh_interval: int = 300):
        self.api_key = api_key
        self.pool_size = pool_size
        self.regions = regions or ["jp", "us", "eu"]
        self.default_region = default_region
        self.refresh_interval = refresh_interval
        self._proxies: dict[str, list[ProxyConfig]] = {}
        self._in_use: set[str] = set()
        self._lock = asyncio.Lock()
        self._last_refresh: float = 0
        self._refresh_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return "decodo"

    async def _fetch_pool(self) -> dict[str, list[ProxyConfig]]:
        pools: dict[str, list[ProxyConfig]] = {}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            for zone in ["residential", "isp", "mobile"]:
                for region in self.regions:
                    params = {"zone": zone, "country": region, "pool_size": self.pool_size}
                    try:
                        resp = await client.get(DECODO_API_URL, params=params, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            for p in data.get("proxy", []):
                                proxy = ProxyConfig(
                                    host=p["host"],
                                    port=p["port"],
                                    username=p.get("username"),
                                    password=p.get("password"),
                                    protocol="http",
                                    region=region,
                                )
                                pools.setdefault(region, []).append(proxy)
                    except Exception:
                        continue
        return pools

    def _pool_keys(self, pools: dict[str, list[ProxyConfig]]) -> set[str]:
        return {
            self._proxy_key(proxy)
            for proxies in pools.values()
            for proxy in proxies
        }

    async def _replace_pool(self, new_pools: dict[str, list[ProxyConfig]]) -> None:
        async with self._lock:
            self._proxies = new_pools
            self._in_use.intersection_update(self._pool_keys(new_pools))
            self._last_refresh = asyncio.get_event_loop().time()

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(self.refresh_interval)
            try:
                new_pools = await self._fetch_pool()
                await self._replace_pool(new_pools)
            except Exception:
                pass

    async def start(self):
        if self._refresh_task and not self._refresh_task.done():
            return
        await self._replace_pool(await self._fetch_pool())
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self):
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    def _proxy_key(self, proxy: ProxyConfig) -> str:
        return f"{proxy.host}:{proxy.port}"

    async def get_proxy(self, region: str | None = None) -> ProxyConfig:
        region = region or self.default_region
        async with self._lock:
            region_proxies = self._proxies.get(region, [])
            if not region_proxies:
                for r, proxies in self._proxies.items():
                    region_proxies = proxies
                    break
            if not region_proxies:
                raise PoolExhaustedError(f"No Decodo proxies available for region={region}")
            for proxy in region_proxies:
                key = self._proxy_key(proxy)
                if key not in self._in_use:
                    self._in_use.add(key)
                    return proxy
            raise PoolExhaustedError(f"All Decodo proxies in use for region={region}")

    async def release_proxy(self, proxy: ProxyConfig) -> None:
        key = self._proxy_key(proxy)
        async with self._lock:
            self._in_use.discard(key)

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(DECODO_STATUS_URL, headers=headers)
                return resp.status_code == 200
        except Exception:
            return False
