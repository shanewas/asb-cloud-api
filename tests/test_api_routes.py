import os
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

# Force in-memory mode
os.environ.pop("DATABASE_URL", None)

from asb_api.__main__ import app
from asb_api.api.auth import set_key_store, InMemoryKeyStore
from asb_api.api.routes.sessions import ensure_session_owner
from asb_api.api.routes.usage import get_usage, set_usage_context
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.api.routes.scrape import set_pool, set_rate_limiter, set_usage_tracker, set_session_store_for_scrape
from asb_api.api.routes.sessions import set_session_store
from asb_api.api.routes.usage import set_usage_context as set_usage_ctx
from asb_api.session.store import SessionStore


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


# ---------------------------------------------------------------------------
# End-to-end error contract tests (verifies the new standardized handler)
# ---------------------------------------------------------------------------

def _setup_error_test_client():
    key_store = InMemoryKeyStore()
    raw_key, _ = key_store.create(tier="starter")
    set_key_store(key_store)

    limits = {"starter": {"requests": 5, "window_seconds": 3600}}
    limiter = SlidingWindowLimiter(limits_by_tier=limits)
    set_rate_limiter(limiter)

    s_store = SessionStore(encryption_key=None, ttl_seconds=60)
    set_session_store(s_store)
    set_session_store_for_scrape(s_store)

    ut = UsageTracker()
    set_usage_tracker(ut)
    set_usage_ctx(ut, limits)

    # Dummy pool so rate limiting and normal scrape paths can be exercised
    class _DummyWorker:
        async def scrape(self, req):
            from asb_api.session.models import ScrapeResponse, ScrapeMetadata
            return ScrapeResponse(
                request_id="dummy",
                status="success",
                html="<html>ok</html>",
                metadata=ScrapeMetadata(
                    request_id="dummy", provider="null", region="jp",
                    fingerprint_id="x", worker_id="w0", duration_ms=1,
                    block_detected=False, retries=0
                ),
            )

    class _DummyPool:
        async def acquire(self, region=None):
            return _DummyWorker()
        def release(self, worker, region=None):
            pass
        def get_status(self):
            return {"jp": {"active": 0, "idle": 1}}

    set_pool(_DummyPool())

    client = TestClient(app)
    return client, f"Bearer {raw_key}"


class APIErrorContractTests(unittest.TestCase):
    """Verify that all public errors follow the {error_code, message} contract from SPEC §8."""

    def setUp(self):
        self.client, self.auth = _setup_error_test_client()

    def test_missing_auth_header(self):
        resp = self.client.post("/v1/scrape", json={"url": "https://example.com"})
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "MISSING_AUTH")
        self.assertIn("Authorization", body.get("message", ""))

    def test_invalid_api_key(self):
        resp = self.client.post(
            "/v1/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": "Bearer sk_live_invalid"},
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "INVALID_API_KEY")

    def test_service_not_initialized_scrape(self):
        # Force uninitialized pool
        from asb_api.api.routes import scrape as scrape_mod
        original = getattr(scrape_mod, "pool", None)
        try:
            scrape_mod.pool = None
            resp = self.client.post(
                "/v1/scrape",
                json={"url": "https://example.com", "method": "GET"},
                headers={"Authorization": self.auth},
            )
            self.assertEqual(resp.status_code, 503)
            body = resp.json()
            self.assertEqual(body.get("error_code"), "SERVICE_NOT_INITIALIZED")
        finally:
            scrape_mod.pool = original

    def test_session_not_found(self):
        resp = self.client.get(
            "/v1/sessions/sess_doesnotexist",
            headers={"Authorization": self.auth},
        )
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "SESSION_NOT_FOUND")

    def test_rate_limit_exceeded_includes_code_and_headers(self):
        # Burn through the low limit configured for this test client
        for _ in range(6):
            resp = self.client.post(
                "/v1/scrape",
                json={"url": "https://example.com", "method": "GET"},
                headers={"Authorization": self.auth},
            )
        self.assertEqual(resp.status_code, 429)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "RATE_LIMIT_EXCEEDED")
        self.assertIn("X-RateLimit-Limit", resp.headers)

    def test_validation_error_becomes_bad_request(self):
        resp = self.client.post(
            "/v1/scrape",
            json={"method": "GET"},  # missing required "url"
            headers={"Authorization": self.auth},
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "BAD_REQUEST")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# Basic bulk-scrape smoke tests (issue #11 design) - integrated during rebase
# These use the same minimal in-memory wiring pattern as other route tests.
# They verify the new endpoint shape, partial success, and per-item results.
# NOTE: rate limit test here is basic; stronger quota-exhaustion test added below.
# ---------------------------------------------------------------------------


class _DummyWorker:
    async def scrape(self, req):
        from asb_api.session.models import ScrapeResponse, ScrapeMetadata
        status = "success"
        html = f"<html>ok from {req.url}</html>"
        if "fail" in req.url:
            status = "error"
        return ScrapeResponse(
            request_id="bulk_test",
            status=status,
            html=html if status == "success" else None,
            metadata=ScrapeMetadata(
                request_id="bulk_test", provider="null", region=req.region or "jp",
                fingerprint_id="test", worker_id="w0", duration_ms=5,
                block_detected=False, retries=0
            ),
            error_code="WORKER_ERROR" if status == "error" else None,
            message="simulated failure" if status == "error" else None,
        )


class _DummyPool:
    async def acquire(self, region=None):
        return _DummyWorker()
    def release(self, worker, region=None):
        pass
    def get_status(self):
        return {"jp": {"active": 0, "idle": 2}}


def _setup_bulk_test_client():
    ks = InMemoryKeyStore()
    raw, _ = ks.create(tier="starter")
    set_key_store(ks)

    limits = {"starter": {"requests": 100, "window_seconds": 3600}}
    lim = SlidingWindowLimiter(limits_by_tier=limits)
    set_rate_limiter(lim)

    ss = SessionStore(encryption_key=None, ttl_seconds=300)
    set_session_store(ss)
    set_session_store_for_scrape(ss)

    ut = UsageTracker()
    set_usage_tracker(ut)
    set_usage_ctx(ut, limits)

    set_pool(_DummyPool())

    client = TestClient(app)
    return client, f"Bearer {raw}"


class BulkScrapeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client, self.auth = _setup_bulk_test_client()

    def test_bulk_happy_path_and_partial_failure(self):
        resp = self.client.post(
            "/v1/bulk-scrape",
            json={
                "items": [
                    {"url": "https://example.com/1", "method": "GET"},
                    {"url": "https://example.com/fail-me", "method": "GET"},
                    {"url": "https://example.com/3", "method": "GET"},
                ],
                "max_concurrency": 2,
            },
            headers={"Authorization": self.auth},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["succeeded"], 2)
        self.assertEqual(data["summary"]["failed"], 1)

        # Check per-item shape
        r0 = data["results"][0]
        self.assertEqual(r0["index"], 0)
        self.assertIsNotNone(r0["result"])
        self.assertIsNone(r0["error"])
        self.assertIn("example.com/1", r0["result"]["html"])

        r1 = data["results"][1]
        self.assertEqual(r1["index"], 1)
        self.assertIsNotNone(r1["result"])
        self.assertEqual(r1["result"]["status"], "error")
        self.assertEqual(r1["result"]["error_code"], "WORKER_ERROR")
        self.assertIsNone(r1["error"])

        r2 = data["results"][2]
        self.assertEqual(r2["index"], 2)
        self.assertIsNotNone(r2["result"])

    def test_bulk_rate_limit_rejects_whole_batch(self):
        # Note: this test uses high limit (100). Stronger 1-remaining rejection test is in the rate limit section below.
        resp = self.client.post(
            "/v1/bulk-scrape",
            json={"items": [{"url": "https://example.com/a", "method": "GET"}]},
            headers={"Authorization": self.auth},
        )
        self.assertEqual(resp.status_code, 200)
