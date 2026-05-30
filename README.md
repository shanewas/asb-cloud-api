# ASB Cloud API

ASB Cloud API is a FastAPI service for browser-backed scraping. It runs Playwright workers behind a REST API and adds API-key authentication, rate limits, stateful sessions, proxy-provider abstraction, usage tracking, and optional Stripe/self-hosted licensing primitives.

The project is currently release-candidate quality, not production-ready. See [SPEC.md](SPEC.md) for the release contract and known blockers.

## What It Does

- Executes browser-backed `GET` and `POST` scrape requests.
- Returns rendered HTML, response headers, cookies, metadata, and optional screenshots.
- Supports stateless and stateful session flows.
- Routes work through region-aware worker pools.
- Supports pluggable proxy providers: direct/null, custom proxies, Decodo, and Bright Data.
- Runs with in-memory stores for local development or PostgreSQL stores for persistent mode.
- Includes Stripe checkout/webhook and self-hosted license verification endpoints.

## Project Status

This repository is suitable for local development and private beta hardening. Before public production use, close the release blockers listed in [SPEC.md](SPEC.md#17-known-release-blockers), especially:

- Stripe test-mode verification.
- A final legal/product review of the OSS license, patent positioning, and self-hosted/commercial terms.

## Quick Start

Requirements:

- Python 3.11+
- Playwright Chromium browser dependencies
- Optional: PostgreSQL for persistent mode

Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

Start the API:

```bash
python -m asb_api
```

By default, if `DATABASE_URL` is not set, the app starts in local in-memory mode and creates a default development API key in the startup logs.

## Docker

Build and run:

```bash
docker build -t asb-cloud-api .
docker run --rm -p 8000:8000 --env-file .env asb-cloud-api
```

Copy `.env.example` to `.env` for local configuration:

```bash
copy .env.example .env
```

Smoke test the running container:

```bash
curl http://localhost:8000/v1/health
```

For authenticated endpoints, copy the default development key from container startup logs or create a persistent key with the admin CLI in PostgreSQL mode.

## API Examples

Health check:

```bash
curl http://localhost:8000/v1/health
```

Scrape a page:

```bash
curl -X POST http://localhost:8000/v1/scrape ^
  -H "Authorization: Bearer sk_live_your_key" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://example.com\",\"method\":\"GET\"}"
```

POST through the browser runner:

```bash
curl -X POST http://localhost:8000/v1/scrape ^
  -H "Authorization: Bearer sk_live_your_key" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://httpbin.org/post\",\"method\":\"POST\",\"data\":{\"hello\":\"world\"}}"
```

Create a stateful session:

```bash
curl -X POST http://localhost:8000/v1/sessions ^
  -H "Authorization: Bearer sk_live_your_key" ^
  -H "Content-Type: application/json" ^
  -d "{\"region\":\"jp\",\"fingerprint\":\"general\"}"
```

## Configuration

The API loads `config.yaml` by default. Set `ASB_CONFIG_PATH` to use another file.

Common environment variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | No | Enables PostgreSQL-backed persistence. |
| `COOKIE_ENCRYPTION_KEY` | Recommended | Encrypts persisted session cookies. |
| `ASB_SCREENSHOT_DIR` | No | Screenshot output directory. Defaults to `/tmp/screenshots`. |
| `STRIPE_SECRET_KEY` | Billing only | Stripe API key. |
| `STRIPE_WEBHOOK_SECRET` | Billing only | Stripe webhook signature verification. |
| `STRIPE_PRICE_STARTER` | Billing only | Stripe price ID for starter subscriptions. |
| `STRIPE_PRICE_PRO` | Billing only | Stripe price ID for pro subscriptions. |
| `STRIPE_PRICE_ENTERPRISE` | Billing only | Stripe price ID for enterprise subscriptions. |
| `STRIPE_LICENSE_SOLO` | Billing only | Stripe price ID for solo self-hosted licenses. |
| `STRIPE_LICENSE_TEAM` | Billing only | Stripe price ID for team self-hosted licenses. |
| `STRIPE_LICENSE_ENTERPRISE` | Billing only | Stripe price ID for enterprise self-hosted licenses. |
| `LICENSE_SECRET_KEY` | License only | Self-hosted license verification secret. |

See [.env.example](.env.example) for a full template.

Stripe billing routes are mounted only when `billing.enabled` is `true` in `config.yaml`. License verification remains available separately and requires `LICENSE_SECRET_KEY`. See [docs/BILLING_TEST_MODE.md](docs/BILLING_TEST_MODE.md) before enabling Stripe-backed billing in a shared environment.

## API Key Storage

Generated API keys contain high-entropy random material and are stored as SHA-256 hashes with timing-safe comparison. This is appropriate for randomly generated bearer secrets; do not accept user-chosen low-entropy API keys without switching to a password hashing scheme.

## Development

Run the lightweight checks:

```bash
python -m compileall -q asb_api tests
python -m unittest discover -s tests -v
python -m pytest
```

Run the automated release smoke tests (covers the key items from SPEC §16 without external services):

```bash
python -m unittest tests.test_smoke -v
```

Generate a Clawpatch report if the local provider is available:

```bash
clawpatch map
clawpatch review --limit 10
clawpatch report
```

## Repository Layout

```text
asb_api/        Server implementation
clients/
  python/       asb-cloud-client (PyPI)
  node/         asb-cloud-client (npm)
  cli/          asb-cli (PyPI)
examples/       Runnable smoke tests for all clients
tests/          Regression tests
SPEC.md         Release specification
clients/README.md  Client package names, versioning, and usage
```

## Clients & SDKs

Thin official clients are provided for fast integration (see [clients/README.md](clients/README.md)):

- **Python**: `pip install asb-cloud-client` → `from asb_client import AsbClient`
- **Node.js**: `npm install asb-cloud-client`
- **CLI**: `pip install asb-cli` → `asb` command (scrape, sessions, usage, portal)

All examples in `examples/` run against a local self-hosted instance (`python -m asb_api`).

## Responsible Use

Use this software only where you have permission and where your use complies with applicable law, website terms, privacy rules, and rate limits. Do not use it for credential abuse, unauthorized access, spam, fraud, or evading security controls.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and please open an issue before large behavior changes.

## Security

Please report vulnerabilities using the process in [SECURITY.md](SECURITY.md). Do not open public issues for sensitive reports.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
