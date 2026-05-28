"""Daily rollup job — run at midnight UTC via cron or scheduler.

Example cron:
0 0 * * * cd /app && python -m asb_api.jobs.daily_rollup
"""
import asyncio
from datetime import datetime, timezone, timedelta
from asb_api.db.usage import PostgresUsageTracker


async def run():
    tracker = PostgresUsageTracker()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    await tracker.rollup_daily(yesterday)
    print(f"Daily rollup completed for {yesterday}")


if __name__ == "__main__":
    asyncio.run(run())
