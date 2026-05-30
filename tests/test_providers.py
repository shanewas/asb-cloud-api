import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from asb_api.providers import ProviderRegistry
from asb_api.providers.base import ProxyConfig
from asb_api.providers.decodo import DecodoProvider


class DecodoProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_preserves_leased_proxy_still_in_pool(self):
        provider = DecodoProvider(api_key="test", refresh_interval=3600)
        leased = ProxyConfig(host="proxy.example", port=8080, region="jp")
        provider._proxies = {"jp": [leased]}
        provider._in_use.add(provider._proxy_key(leased))

        await provider._replace_pool({"jp": [leased]})

        self.assertIn(provider._proxy_key(leased), provider._in_use)

    async def test_start_is_idempotent_and_stop_clears_task(self):
        provider = DecodoProvider(api_key="test", refresh_interval=3600)
        provider._fetch_pool = AsyncMock(return_value={})  # type: ignore[method-assign]

        await provider.start()
        first_task = provider._refresh_task
        self.assertIsNotNone(first_task)
        await provider.start()
        self.assertIs(provider._refresh_task, first_task)
        await provider.stop()

        self.assertIsNone(provider._refresh_task)
        self.assertEqual(provider._fetch_pool.await_count, 1)


class ProviderRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_all_starts_decodo_provider(self):
        registry = ProviderRegistry()
        registry.initialize_from_config({"decodo": {"enabled": True, "api_key": "test"}})

        with patch.object(DecodoProvider, "start", new_callable=AsyncMock) as start:
            await registry.start_all()

        start.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
