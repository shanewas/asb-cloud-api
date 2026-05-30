# asb-cloud-client

Thin Python client for [ASB Cloud API](https://github.com/shanewas/asb-cloud-api).

## Installation

```bash
pip install asb-cloud-client
```

Or from source (monorepo):

```bash
pip install -e clients/python
```

## Quick Start

```python
from asb_client import AsbClient, AsbError

client = AsbClient(
    base_url="http://localhost:8000",
    api_key="sk_live_your_key_here"
)

# Scrape
result = client.scrape(
    url="https://example.com",
    method="GET",
    region="jp",
    timeout=30
)
print(result["html"][:200])

# Create session
sess = client.create_session(region="jp", fingerprint="general")
print(sess["session_id"])

# Usage
usage = client.get_usage()
print(usage["requests_used"], "/", usage["requests_limit"])

# Billing portal (if Stripe configured for the key)
try:
    portal = client.get_billing_portal()
    print(portal["portal_url"])
except AsbError as e:
    print("Billing not available:", e)
```

## Async Usage

```python
import asyncio
from asb_client import AsbClient

async def main():
    async with AsbClient(base_url=..., api_key=...) as client:
        result = await client.scrape_async(url=...)

asyncio.run(main())
```

## Configuration

- `base_url`: API base (default https://api.asbcloud.io but for self-hosted use local)
- `api_key`: Bearer token (or set `ASB_API_KEY` env var)
- `timeout`: default request timeout seconds (default 60)

All methods raise `AsbError` (or subclasses) on failure. Inspect `e.status_code`, `e.error_code`, `e.message`.

## Supported Endpoints

- `scrape(...)` / `scrape_async(...)`
- `create_session(...)`, `get_session(...)`, `delete_session(...)`
- `get_usage()`
- `get_billing_portal()`
- `health()` (no auth required)

See the main [SPEC.md](../../../SPEC.md) for request/response shapes and error codes.

## Local Self-Hosted Example

See `../../examples/python/` for complete runnable examples against a local `python -m asb_api` instance.
