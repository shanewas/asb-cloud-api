"""
Release Smoke Tests (automated)

These tests provide repeatable, deterministic coverage of the most important
items from SPEC.md section 16 "Manual Smoke Tests" without requiring:
- Real PostgreSQL
- Real Playwright / browsers
- Real proxy providers (Decodo, Bright Data, etc.)
- External websites (example.com, httpbin, etc.)
- Stripe or billing secrets

Run locally:
    python -m unittest tests.test_smoke -v

They are intentionally lightweight so they can run in CI on every commit.
"""

import os
import tempfile
import unittest
from typing import Any

# Force in-memory mode and a known config before any app imports
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("ASB_CONFIG_PATH", "config.yaml")

from fastapi.testclient import TestClient

from asb_api.__main__ import app
from asb_api.api.auth import set_key_store, InMemoryKeyStore, get_key_store
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.api.usage import UsageTracker
from asb_api.api.routes.scrape import set_pool, set_rate_limiter, set_usage_tracker, set_session_store_for_scrape
from asb_api.api.routes.sessions import set_session_store
from asb_api.api.routes.usage import set_usage_context
from asb_api.session.store import SessionStore
from asb_api.session.models import ScrapeResponse, ScrapeMetadata


# ---------------------------------------------------------------------------
# Fake worker / pool (no playwright, no network, fully deterministic)
# ---------------------------------------------------------------------------

class FakeWorker:
    """A worker that returns canned but realistic responses for smoke tests."""

    def __init__(self, worker_id: str = "worker-smoke-0"):
        self.worker_id = worker_id
        self._call_count = 0

    async def scrape(self, request) -> ScrapeResponse:
        self._call_count += 1
        req_id = f"req_smoke{self._call_count:04d}"

        # Simulate different behavior for GET vs POST to exercise the route
        if request.method.upper() == "POST":
            html = f"<html><body>POST echo: {request.data}</body></html>"
        else:
            html = f"<html><body>GET from {request.url}</body></html>"

        cookies = {"session": "smoke123"} if request.session_id else {}

        # Screenshot handling for smoke (create a temp file when requested)
        screenshot_url = None
        if request.screenshot:
            fd, screenshot_url = tempfile.mkstemp(suffix=".png", prefix="smoke_")
            os.close(fd)
            # Write a tiny fake PNG header so the file "exists" and is non-empty
            with open(screenshot_url, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"fake" * 10)

        return ScrapeResponse(
            request_id=req_id,
            status="success",
            html=html,
            screenshot_url=screenshot_url,
            cookies=cookies,
            headers={"content-type": "text/html; charset=utf-8"},
            metadata=ScrapeMetadata(
                request_id=req_id,
                provider="null",
                region=request.region or "jp",
                fingerprint_id="Mozilla/5.0 (Smoke Test)",
                worker_id=self.worker_id,
                duration_ms=42,
                block_detected=False,
                retries=0,
            ),
        )


class FakeRegionWorkerPool:
    """Minimal pool that satisfies the interface used by /v1/scrape and /v1/health."""

    def __init__(self, default_region: str = "jp"):
        self.default_region = default_region
        self._workers = [FakeWorker(f"worker-smoke-{i}") for i in range(2)]
        self._next = 0

    async def acquire(self, region: str | None = None):
        w = self._workers[self._next % len(self._workers)]
        self._next += 1
        w._lease_region = region or self.default_region
        w._busy = True
        return w

    def release(self, worker: Any, region: str | None = None):
        if hasattr(worker, "_busy"):
            worker._busy = False
        if hasattr(worker, "_lease_region"):
            delattr(worker, "_lease_region")

    def get_status(self) -> dict:
        return {
            "jp": {"active": 0, "idle": 2},
            "us": {"active": 0, "idle": 1},
        }


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

def _setup_in_memory_smoke_app() -> tuple[TestClient, str, InMemoryKeyStore]:
    """
    Wire a fully functional in-memory app for smoke testing.

    Returns:
        (client, raw_dev_key, key_store)
    """
    # 1. Key store + dev key (simulates the "default key in logs")
    key_store = InMemoryKeyStore()
    raw_key, _ = key_store.create(tier="starter", owner_email="smoke@local.test")
    set_key_store(key_store)

    # 2. Rate limiter (in-memory)
    limits = {
        "free": {"requests": 500, "window_seconds": 3600, "concurrent_sessions": 2},
        "starter": {"requests": 25000, "window_seconds": 86400, "concurrent_sessions": 10},
        "pro": {"requests": 200000, "window_seconds": 86400, "concurrent_sessions": 50},
        "enterprise": {"requests": -1, "window_seconds": 86400, "concurrent_sessions": 200},
    }
    limiter = SlidingWindowLimiter(limits_by_tier=limits)
    set_rate_limiter(limiter)

    # 3. Session store (in-memory)
    s_store = SessionStore(encryption_key=None, ttl_seconds=300)
    set_session_store(s_store)
    set_session_store_for_scrape(s_store)

    # 4. Usage tracker (in-memory)
    ut = UsageTracker()
    set_usage_tracker(ut)
    set_usage_context(ut, limits)

    # 5. Fake worker pool (the key to no-net, no-playwright smoke tests)
    fake_pool = FakeRegionWorkerPool(default_region="jp")
    set_pool(fake_pool)

    # 6. Create the TestClient (exercises full HTTP + dependency stack)
    client = TestClient(app)

    return client, raw_key, key_store


class ReleaseSmokeTests(unittest.TestCase):
    """Automated end-to-end smoke tests mapping to SPEC.md §16."""

    @classmethod
    def setUpClass(cls):
        cls.client, cls.raw_key, cls.key_store = _setup_in_memory_smoke_app()
        cls.auth = {"Authorization": f"Bearer {cls.raw_key}"}

    def test_03_health_endpoint(self):
        """SPEC §16 item 3: GET /v1/health works."""
        resp = self.client.get("/v1/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("workers", data)
        self.assertIn("providers", data)

    def test_04_scrape_get(self):
        """SPEC §16 item 4: Authenticated GET scrape returns HTML + metadata."""
        payload = {
            "url": "https://example.com/",
            "method": "GET",
            "screenshot": False,
        }
        resp = self.client.post("/v1/scrape", json=payload, headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("example.com", data["html"])
        self.assertIsNotNone(data["metadata"])
        self.assertEqual(data["metadata"]["provider"], "null")

    def test_05_scrape_post_with_data(self):
        """SPEC §16 item 5: POST scrape with JSON data (simulated local echo)."""
        payload = {
            "url": "http://localhost:9999/echo",
            "method": "POST",
            "data": {"hello": "from-smoke-test", "count": 42},
            "screenshot": False,
        }
        resp = self.client.post("/v1/scrape", json=payload, headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("POST echo", data["html"])
        self.assertIn("from-smoke-test", data["html"])

    def test_06_sessions_lifecycle_and_ownership(self):
        """SPEC §16 item 6: Create, use in scrape, get, delete + ownership isolation."""
        # Create session
        create_resp = self.client.post(
            "/v1/sessions",
            json={"region": "jp", "fingerprint": "general"},
            headers=self.auth,
        )
        self.assertEqual(create_resp.status_code, 200)
        sess = create_resp.json()
        sid = sess["session_id"]
        self.assertTrue(sid.startswith("sess_"))

        # Use the session in a scrape (should increment request_count)
        scrape_resp = self.client.post(
            "/v1/scrape",
            json={"url": "https://example.com/", "method": "GET", "session_id": sid},
            headers=self.auth,
        )
        self.assertEqual(scrape_resp.status_code, 200)

        # Get session and verify count increased
        get_resp = self.client.get(f"/v1/sessions/{sid}", headers=self.auth)
        self.assertEqual(get_resp.status_code, 200)
        self.assertGreaterEqual(get_resp.json()["request_count"], 1)

        # Ownership isolation: another key cannot see or delete it
        other_store = InMemoryKeyStore()
        other_raw, _ = other_store.create(tier="free")
        other_auth = {"Authorization": f"Bearer {other_raw}"}
        set_key_store(other_store)  # temporarily swap

        bad_get = self.client.get(f"/v1/sessions/{sid}", headers=other_auth)
        self.assertEqual(bad_get.status_code, 404)

        bad_del = self.client.delete(f"/v1/sessions/{sid}", headers=other_auth)
        self.assertEqual(bad_del.status_code, 404)

        # Restore original key store for remaining tests
        set_key_store(self.key_store)

        # Legitimate owner can delete
        del_resp = self.client.delete(f"/v1/sessions/{sid}", headers=self.auth)
        self.assertEqual(del_resp.status_code, 204)

        # After delete, gone
        gone = self.client.get(f"/v1/sessions/{sid}", headers=self.auth)
        self.assertEqual(gone.status_code, 404)

    def test_07_screenshot_returns_path_and_file_exists(self):
        """SPEC §16 item 7: screenshot=true returns a usable local path."""
        payload = {
            "url": "https://example.com/",
            "method": "GET",
            "screenshot": True,
        }
        resp = self.client.post("/v1/scrape", json=payload, headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNotNone(data["screenshot_url"])
        self.assertTrue(os.path.exists(data["screenshot_url"]))
        # Cleanup the temp file created by the fake
        try:
            os.unlink(data["screenshot_url"])
        except OSError:
            pass

    def test_10_usage_increments_in_memory(self):
        """SPEC §16 item 10: /v1/usage reflects scrape activity (in-memory path)."""
        # Make a couple of scrapes
        for _ in range(3):
            r = self.client.post(
                "/v1/scrape",
                json={"url": "https://example.com/", "method": "GET"},
                headers=self.auth,
            )
            self.assertEqual(r.status_code, 200)

        usage = self.client.get("/v1/usage", headers=self.auth)
        self.assertEqual(usage.status_code, 200)
        u = usage.json()
        self.assertEqual(u["tier"], "starter")
        self.assertGreaterEqual(u["requests_used"], 3)
        self.assertEqual(u["requests_limit"], 25000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
