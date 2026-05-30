import asyncio
import json
import unittest

try:
    from asb_api.providers.base import ProxyConfig
    from asb_api.session.models import ScrapeRequest
    from asb_api.workers.asb_runner import ASBRunner
    from asb_api.workers.pool import RegionWorkerPool
    from asb_api.workers.worker import ASBWorker
except ModuleNotFoundError as exc:
    if exc.name == "playwright":
        raise unittest.SkipTest("playwright is not installed in this interpreter") from exc
    raise


class FakeProvider:
    name = "custom"

    def __init__(self):
        self.released = 0

    async def get_proxy(self, region=None):
        return ProxyConfig(host="127.0.0.1", port=8080, region=region)

    async def release_proxy(self, proxy):
        self.released += 1

    async def health_check(self):
        return True


class FakeFingerprintGenerator:
    def get(self, name):
        return object()


class BrokenFingerprintGenerator:
    def get(self, name):
        raise RuntimeError("fingerprint unavailable")


class WorkerRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_region_release_uses_actual_acquired_region(self):
        pool = RegionWorkerPool(
            {"jp": 1, "us": 1},
            provider=FakeProvider(),
            fingerprint_generator=FakeFingerprintGenerator(),
            default_region="jp",
        )

        worker = await pool.acquire("us")
        pool.release(worker)

        reacquired = await asyncio.wait_for(pool.acquire("us"), timeout=0.1)
        pool.release(reacquired)

    async def test_unknown_region_release_uses_normalized_region(self):
        pool = RegionWorkerPool(
            {"jp": 1},
            provider=FakeProvider(),
            fingerprint_generator=FakeFingerprintGenerator(),
            default_region="jp",
        )

        worker = await pool.acquire("unknown")
        pool.release(worker, "unknown")

        reacquired = await asyncio.wait_for(pool.acquire("jp"), timeout=0.1)
        pool.release(reacquired)

    async def test_proxy_is_released_when_fingerprint_setup_fails(self):
        provider = FakeProvider()
        worker = ASBWorker("worker-test", provider, BrokenFingerprintGenerator())

        response = await worker.scrape(ScrapeRequest(url="https://example.com"))

        self.assertEqual(response.status, "error")
        self.assertEqual(provider.released, 1)


class RunnerRegressionTests(unittest.TestCase):
    def test_post_dict_body_is_serialized_as_json(self):
        headers, body = ASBRunner._prepare_body({"X-Test": "1"}, {"a": 1})

        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"a": 1})


class ScreenshotConfigRegressionTests(unittest.TestCase):
    def test_worker_accepts_screenshot_dir(self):
        try:
            from asb_api.workers.worker import ASBWorker
            from asb_api.providers.base import ProxyConfig
        except ModuleNotFoundError as exc:
            if exc.name == "playwright":
                self.skipTest("playwright not installed")
            raise

        class DummyProvider:
            name = "null"
            async def get_proxy(self, region=None):
                return ProxyConfig(host="DIRECT", port=0, region=region)
            async def release_proxy(self, proxy):
                pass
            async def health_check(self):
                return True

        class DummyFPGen:
            def get(self, name):
                return type("FP", (), {"user_agent": "test", "viewport": (800, 600)})()

        # Should construct without error; dir stored for later runner use
        w = ASBWorker("w-1", DummyProvider(), DummyFPGen(), screenshot_dir="/tmp/test-shots")
        self.assertEqual(w.screenshot_dir, "/tmp/test-shots")

        w2 = ASBWorker("w-2", DummyProvider(), DummyFPGen())
        self.assertIsNone(w2.screenshot_dir)

    def test_region_pool_accepts_screenshot_dir(self):
        try:
            from asb_api.workers.pool import RegionWorkerPool
        except ModuleNotFoundError as exc:
            if exc.name == "playwright":
                self.skipTest("playwright not installed")
            raise

        class DummyProvider:
            name = "null"
            async def get_proxy(self, region=None):
                return None
            async def release_proxy(self, proxy):
                pass
            async def health_check(self):
                return True

        class DummyFPGen:
            def get(self, name):
                return type("FP", (), {"user_agent": "test", "viewport": (800, 600)})()

        pool = RegionWorkerPool(
            {"jp": 1},
            provider=DummyProvider(),
            fingerprint_generator=DummyFPGen(),
            default_region="jp",
            screenshot_dir="/var/screenshots",
        )
        self.assertEqual(pool.workers["jp"][0].screenshot_dir, "/var/screenshots")


if __name__ == "__main__":
    unittest.main()
