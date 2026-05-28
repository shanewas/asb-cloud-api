import time
import asyncio
from collections import deque
from fastapi import HTTPException


class RateLimitExceeded(HTTPException):
    def __init__(self, limit: int, remaining: int, reset_at: int):
        super().__init__(
            status_code=429,
            detail={
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": "Rate limit reached",
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at,
            },
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_at),
            },
        )


class SlidingWindowLimiter:
    def __init__(self, limits_by_tier: dict):
        self.limits_by_tier = limits_by_tier
        self._windows: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    def _get_limits(self, tier: str) -> tuple[int, int]:
        tier_cfg = self.limits_by_tier.get(tier, self.limits_by_tier.get("free", {}))
        max_requests = tier_cfg.get("requests", 500)
        window_seconds = tier_cfg.get("window_seconds", 3600)
        return max_requests, window_seconds

    async def check(self, key_id: str, tier: str = "free") -> tuple[bool, int, int]:
        max_requests, window_seconds = self._get_limits(tier)
        if max_requests == -1:
            return True, -1, 0
        now = time.time()
        async with self._lock:
            if key_id not in self._windows:
                self._windows[key_id] = deque()
            window = self._windows[key_id]
            cutoff = now - window_seconds
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= max_requests:
                reset_at = int(window[0] + window_seconds)
                raise RateLimitExceeded(
                    limit=max_requests,
                    remaining=0,
                    reset_at=reset_at,
                )
            window.append(now)
            remaining = max_requests - len(window)
            reset_at = int(now + window_seconds)
            return True, remaining, reset_at
