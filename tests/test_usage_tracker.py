import unittest

from asb_api.api.usage import UsageTracker


class UsageTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_resets_stale_daily_count(self):
        tracker = UsageTracker()
        await tracker.increment("key_1")

        tracker._daily["key_1"] = "2000-01-01"

        info = await tracker.get_usage_info("key_1", "free", {"free": {"requests": 10}})

        self.assertEqual(info["requests_used"], 0)
        self.assertEqual(info["requests_limit"], 10)


if __name__ == "__main__":
    unittest.main()
