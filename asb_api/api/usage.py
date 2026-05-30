import asyncio
from datetime import datetime, timezone


class UsageTracker:
    def __init__(self):
        self._counts: dict[str, int] = {}
        self._daily: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _normalize_day_locked(self, key_id: str) -> None:
        today = self._today()
        if self._daily.get(key_id) != today:
            self._counts[key_id] = 0
            self._daily[key_id] = today

    async def increment(self, key_id: str):
        async with self._lock:
            self._normalize_day_locked(key_id)
            self._counts[key_id] = self._counts.get(key_id, 0) + 1

    async def get(self, key_id: str) -> int:
        async with self._lock:
            self._normalize_day_locked(key_id)
            return self._counts.get(key_id, 0)

    async def get_usage_info(self, key_id: str, tier: str, limits_cfg: dict) -> dict:
        count = await self.get(key_id)
        tier_cfg = limits_cfg.get(tier, limits_cfg.get("free", {}))
        limit = tier_cfg.get("requests", 500)
        return {
            "tier": tier,
            "requests_used": count,
            "requests_limit": limit,
            "reset_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z"),
        }
