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


if __name__ == "__main__":
    unittest.main()
