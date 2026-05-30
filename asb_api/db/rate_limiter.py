import hashlib
import time
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from .connection import db

# Reuse the HTTP-aware exception so rate limits return proper 429 + headers
from ..api.rate_limiter import RateLimitExceeded, OverageLimitExceeded


class PostgresRateLimiter:
    """Sliding window rate limiter using PostgreSQL advisory locks for safety."""

    def __init__(self, limits_by_tier: dict[str, dict], usage_tracker: Any = None):
        self.limits_by_tier = limits_by_tier
        self.usage_tracker = usage_tracker

    def _lock_id(self, key_id: str) -> int:
        """Deterministic lock ID from key_id (int32 range for advisory lock)."""
        return int(hashlib.sha256(key_id.encode()).hexdigest()[:12], 16) % (1 << 31)

    async def check(self, key_id: str, tier: str = "free") -> tuple[bool, int, int]:
        """Returns (allowed, remaining, reset_at_unix). Raises RateLimitExceeded on limit.
        Also raises OverageLimitExceeded (402) if monthly overage threshold crossed for paid tiers.
        """
        # Phase 3: Overage check (monthly included quota)
        if self.usage_tracker is not None:
            try:
                is_over, _overage_cnt, cost = await self.usage_tracker.check_overage(key_id, tier)
                if is_over:
                    raise OverageLimitExceeded(cost)
            except OverageLimitExceeded:
                raise
            except Exception:
                # If overage check fails (e.g. no data yet), proceed to window rate limit
                pass

        limits = self.limits_by_tier.get(tier, self.limits_by_tier.get("free", {}))
        max_requests = limits.get("requests", 500)
        window_seconds = limits.get("window_seconds", 3600)

        if max_requests == -1:
            return True, -1, 0

        lock_id = self._lock_id(key_id)
        now_dt = datetime.now(timezone.utc)
        now = time.time()
        reset_at = int(now + window_seconds)

        conn = await db.pool.acquire()
        try:
            # Advisory lock serializes per-key checks (prevents race in count)
            async with conn.transaction():
                acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
                if not acquired:
                    await asyncio.sleep(0.05)
                    acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
                    if not acquired:
                        # Could not get lock quickly — conservative deny
                        raise RateLimitExceeded(max_requests, 0, reset_at)

                try:
                    cutoff = now_dt - timedelta(seconds=window_seconds)
                    count_row = await conn.fetchrow(
                        """SELECT COUNT(*) as cnt FROM usage_records
                           WHERE key_id = $1 AND created_at > $2""",
                        key_id, cutoff
                    )
                    count = count_row["cnt"] if count_row else 0

                    if count >= max_requests:
                        oldest_row = await conn.fetchrow(
                            """SELECT created_at FROM usage_records
                               WHERE key_id = $1 AND created_at > $2
                               ORDER BY created_at ASC LIMIT 1""",
                            key_id, cutoff
                        )
                        if oldest_row:
                            reset_at = int(oldest_row["created_at"].timestamp() + window_seconds)
                        raise RateLimitExceeded(max_requests, 0, reset_at)

                    remaining = max_requests - count - 1
                    return True, max(0, remaining), reset_at
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
        finally:
            await db.pool.release(conn)
