import unittest

from fastapi import HTTPException

from asb_api.api.auth import set_key_store
from asb_api.api.routes.sessions import ensure_session_owner
from asb_api.api.routes.usage import get_usage, set_usage_context


class FakeKeyStore:
    def get(self, key_id):
        return {"key_id": key_id, "tier": "starter"}


class FakeUsageTracker:
    async def get_usage_info(self, key_id, tier, limits_cfg):
        return {
            "key_id": key_id,
            "tier": tier,
            "limit": limits_cfg[tier]["requests"],
        }


class FakeSession:
    def __init__(self, key_id):
        self.key_id = key_id


class RouteRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_route_uses_authenticated_key_tier(self):
        set_key_store(FakeKeyStore())
        set_usage_context(FakeUsageTracker(), {"starter": {"requests": 25}})

        response = await get_usage("key_123")

        self.assertEqual(response["key_id"], "key_123")
        self.assertEqual(response["tier"], "starter")
        self.assertEqual(response["limit"], 25)

    async def test_session_owner_mismatch_returns_not_found(self):
        with self.assertRaises(HTTPException) as ctx:
            ensure_session_owner(FakeSession("key_owner"), "key_other")

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
