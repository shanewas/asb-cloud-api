from datetime import datetime, timezone
from .connection import db


class PostgresUsageTracker:
    """Track every request in PostgreSQL + support daily rollups."""

    async def record(
        self,
        key_id: str,
        request_id: str,
        domain: str | None,
        status: str,
        duration_ms: int,
        block_detected: bool,
        region: str | None,
    ):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO usage_records
                   (key_id, request_id, domain, status, duration_ms, block_detected, region)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, request_id, domain, status, duration_ms, block_detected, region
            )
        finally:
            await db.pool.release(conn)

    async def increment(self, key_id: str):
        """Compatibility shim: record a minimal entry (used only if full data unavailable)."""
        await self.record(
            key_id=key_id,
            request_id="unknown",
            domain=None,
            status="success",
            duration_ms=0,
            block_detected=False,
            region=None,
        )

    async def get(self, key_id: str) -> int:
        """Return today's request count for this key (from rollup or raw)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = await self.get_daily_usage(key_id, today)
        return usage.get("total_requests", 0)

    async def get_daily_usage(self, key_id: str, date: str) -> dict:
        """date format: YYYY-MM-DD. Falls back to raw count if no rollup yet."""
        conn = await db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT total_requests, total_duration_ms, block_count
                   FROM daily_usage WHERE key_id = $1 AND date = $2""",
                key_id, date
            )
            if row:
                return {
                    "total_requests": row["total_requests"],
                    "total_duration_ms": row["total_duration_ms"],
                    "block_count": row["block_count"],
                }
            # Fallback: compute live from raw records
            raw = await conn.fetchrow(
                """SELECT COUNT(*) as cnt,
                          COALESCE(SUM(duration_ms), 0) as dur,
                          COALESCE(SUM(CASE WHEN block_detected THEN 1 ELSE 0 END), 0) as blocks
                   FROM usage_records
                   WHERE key_id = $1 AND DATE(created_at) = $2""",
                key_id, date
            )
            return {
                "total_requests": raw["cnt"] if raw else 0,
                "total_duration_ms": raw["dur"] if raw else 0,
                "block_count": raw["blocks"] if raw else 0,
            }
        finally:
            await db.pool.release(conn)

    async def get_usage_info(self, key_id: str, tier: str, limits_cfg: dict) -> dict:
        """Compatibility with old UsageTracker for any existing /usage code."""
        count = await self.get(key_id)
        tier_cfg = limits_cfg.get(tier, limits_cfg.get("free", {}))
        limit = tier_cfg.get("requests", 500)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")
        return {
            "tier": tier,
            "requests_used": count,
            "requests_limit": limit,
            "reset_at": today,
        }

    async def rollup_daily(self, date: str):
        """Aggregate raw usage_records into daily_usage for a given date (idempotent)."""
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO daily_usage (key_id, date, total_requests, total_duration_ms, block_count)
                   SELECT key_id, DATE(created_at) as date,
                          COUNT(*) as total_requests,
                          COALESCE(SUM(duration_ms), 0) as total_duration_ms,
                          COALESCE(SUM(CASE WHEN block_detected THEN 1 ELSE 0 END), 0) as block_count
                   FROM usage_records
                   WHERE DATE(created_at) = $1
                   GROUP BY key_id, DATE(created_at)
                   ON CONFLICT (key_id, date) DO UPDATE SET
                       total_requests = EXCLUDED.total_requests,
                       total_duration_ms = EXCLUDED.total_duration_ms,
                       block_count = EXCLUDED.block_count""",
                date
            )
        finally:
            await db.pool.release(conn)
