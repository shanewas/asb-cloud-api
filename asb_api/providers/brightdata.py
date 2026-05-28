import httpx
from .base import ProxyProviderInterface, ProxyConfig, PoolExhaustedError

BRIGHTDATA_API_URL = "https://api.brightdata.com/v1/zones"


class BrightDataProvider(ProxyProviderInterface):
    def __init__(self, api_key: str, zones: list[str] | None = None):
        self.api_key = api_key
        self.zones = zones or ["residential"]

    @property
    def name(self) -> str:
        return "brightdata"

    async def _get_zone_creds(self, zone: str, country: str | None = None) -> ProxyConfig | None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = f"{BRIGHTDATA_API_URL}/{zone}/get_creds"
        params = {}
        if country:
            params["country"] = country
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return ProxyConfig(
                    host=data.get("host", ""),
                    port=data.get("port", 22225),
                    username=data.get("username"),
                    password=data.get("password"),
                    protocol="http",
                    region=country,
                    session_token=data.get("session_id"),
                )
        return None

    async def get_proxy(self, region: str | None = None) -> ProxyConfig:
        for zone in self.zones:
            proxy = await self._get_zone_creds(zone, country=region)
            if proxy:
                return proxy
        raise PoolExhaustedError("No BrightData proxies available")

    async def release_proxy(self, proxy: ProxyConfig) -> None:
        pass

    async def health_check(self) -> bool:
        if not self.api_key or not self.zones:
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{BRIGHTDATA_API_URL}/{self.zones[0]}", headers=headers)
                return resp.status_code == 200
        except Exception:
            return False
