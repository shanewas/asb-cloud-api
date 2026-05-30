# ASB Cloud API - Release Specification

Status: Release Candidate Spec
Owner: Shanewas Ahmed Nabil
Last updated: 2026-05-30
License: Apache-2.0

## 1. Release Goal

ASB Cloud API is a paid REST API for programmatic browser-backed scraping. Customers send a URL and execution options, and the service returns browser-rendered HTML, response headers, cookies, optional screenshots, and request metadata.

The first releasable version must prove one narrow promise:

> An authenticated customer can call `POST /v1/scrape` and receive a reliable browser-rendered response with predictable rate limits, session behavior, provider routing, usage tracking, and deployable self-hosted packaging.

This release must not depend on dashboard, SDK, Redis, multi-region orchestration, or automated signup. Those are post-release features.

## 2. Release Scope

### In Scope

- FastAPI REST service.
- API key authentication with bearer tokens.
- `POST /v1/scrape` for browser-backed GET and POST requests.
- Optional screenshot capture.
- Stateless and stateful session modes.
- In-memory stores for local development.
- PostgreSQL-backed API keys, sessions, usage records, rate limits, and audit records.
- Pluggable proxy provider interface with `null`, `custom`, Decodo, and Bright Data providers.
- Region-aware worker pools.
- Provider health checks and circuit breaker state.
- Docker image for self-hosted deployment.
- Stripe checkout and webhook endpoints behind environment configuration.
- Self-hosted license verification endpoint.
- Admin CLI for manual API key management.

### Out of Scope for v1

- Customer dashboard.
- Self-service signup.
- Python, Node, or CLI SDKs.
- Bulk scrape endpoint.
- Redis rate limiting (see [docs/REDIS_RATE_LIMITING.md](docs/REDIS_RATE_LIMITING.md) for evaluation and migration plan).
- ClickHouse or analytics warehouse.
- Auto-scaling and multi-VPS worker scheduling.
- Public CDN-backed screenshot hosting.
- Full anti-bot retry orchestration beyond a single worker attempt.
- Public patent or legal grant language. Legal text must be handled outside this technical spec.

## 3. Release Readiness Definition

The release is ready only when all "MUST" items below are true.

### Functional MUSTs

- `python -m asb_api` starts without missing imports after `pip install -r requirements.txt`.
- `GET /v1/health` returns provider and worker status.
- `POST /v1/scrape` accepts authenticated GET requests and returns HTML.
- `POST /v1/scrape` accepts authenticated POST requests and sends the declared method and body upstream.
- `POST /v1/scrape` records usage for both success and error responses.
- `POST /v1/scrape` always releases the worker pool permit after execution.
- Non-default and unknown-region worker acquisitions release the exact semaphore acquired.
- Any proxy borrowed from a provider is returned even when fingerprint setup or runner setup fails.
- Screenshot requests create their output directory before writing.
- Stateful sessions persist cookies and request count until TTL expiry.
- PostgreSQL-backed mode uses timestamp values compatible with `TIMESTAMPTZ`.
- API-key tier lookup works for both in-memory key objects and PostgreSQL key dictionaries.
- Docker image starts the API with the bundled config.

### Security MUSTs

- Authorization is required for all customer endpoints except health, billing webhooks, and license checkout/verification where intentionally public.
- API keys are never logged in full.
- Session cookies are encrypted at rest when `COOKIE_ENCRYPTION_KEY` is configured.
- URLs and headers in logs are redacted according to `security` config.
- Stripe webhooks verify `STRIPE_WEBHOOK_SECRET`.
- Self-hosted license verification requires `LICENSE_SECRET_KEY`.

### Operational MUSTs

- Startup logs show selected provider, configured regions, and persistence mode.
- Shutdown closes database and browser resources.
- Health checks do not require customer authentication.
- All release-blocking runtime errors have a regression test or a documented manual verification step.
- `.pyc`, `.clawpatch`, secrets, and local artifacts are excluded from release commits unless intentionally versioned.

### Documentation MUSTs

- README includes local run, Docker run, required environment variables, and first scrape example.
- API reference documents auth, request/response schema, and error codes.
- Self-hosted setup documents license mode and required secrets.
- Billing setup documents Stripe environment variables and webhook URL.

## 4. Architecture

```text
Client
  |
  v
FastAPI Gateway
  - auth
  - rate limit
  - request validation
  - usage tracking
  |
  v
Session Orchestrator
  - cookie persistence
  - session TTL
  - region selection
  |
  v
RegionWorkerPool
  - bounded worker concurrency
  - per-region semaphores
  |
  v
ASBWorker
  - fingerprint selection
  - proxy lease lifecycle
  - Playwright runner
  |
  v
ProxyProviderInterface
  - null
  - custom
  - Decodo
  - Bright Data
```

All request-path I/O must be async. Synchronous blocking in customer endpoints is a bug unless isolated behind a bounded executor.

## 5. Configuration

The default config file is `config.yaml`. `ASB_CONFIG_PATH` may point to another YAML file.

Environment variables in the form `${NAME}` are resolved at load time. Missing values resolve to an empty string.

### Required for Cloud/PostgreSQL Mode

- `DATABASE_URL`
- `COOKIE_ENCRYPTION_KEY`
- `STRIPE_SECRET_KEY` if billing endpoints are enabled.
- `STRIPE_WEBHOOK_SECRET` if webhook endpoint is enabled.
- `LICENSE_SECRET_KEY` if license verification is enabled.

### Required for Local Dev Mode

- No database is required. If `DATABASE_URL` is absent, the app uses in-memory API keys, sessions, rate limits, and usage tracking.

### Provider Priority

```yaml
provider_priority:
  primary: null
  fallback: null
```

The v1 release may run with only the primary provider wired into workers. Fallback routing is a post-v1 hardening item unless implemented and tested before launch.

## 6. API Authentication

Customer API calls use:

```http
Authorization: Bearer sk_live_xxx
```

Supported key tiers:

- `free`
- `starter`
- `pro`
- `enterprise`

API keys are generated as high-entropy bearer secrets and stored as SHA-256 hashes with timing-safe comparison. This is acceptable for randomly generated secrets; user-chosen or low-entropy keys are not supported without switching to a password hashing scheme.

## 7. API Reference

### `GET /v1/health`

Purpose: service, provider, and worker health.

Authentication: none.

Response:

```json
{
  "status": "healthy",
  "providers": {
    "null": {
      "status": "up",
      "healthy": true
    }
  },
  "workers": {
    "jp": {
      "active": 0,
      "idle": 5
    }
  }
}
```

### `POST /v1/scrape`

Purpose: execute one browser-backed scrape.

Authentication: required.

Request:

```json
{
  "url": "https://example.com",
  "method": "GET",
  "headers": {},
  "data": null,
  "proxy_provider": null,
  "region": "jp",
  "fingerprint": "general",
  "timeout": 30,
  "screenshot": false,
  "session_id": null,
  "session_type": "stateless"
}
```

Rules:

- `method` is `GET` or `POST`.
- `GET` navigates the browser page to `url`.
- `POST` sends `data` upstream as the request body. Dict-like data is JSON serialized unless the caller supplies a `Content-Type`.
- `POST` response text is loaded into the browser page before screenshot capture.
- `timeout` is in seconds.
- `screenshot=true` returns a local screenshot path in self-hosted mode.
- `session_id` requires an existing session owned by the authenticated key.
- `stateful_reset` clears stored cookies before the request.

Response:

```json
{
  "request_id": "req_abc123",
  "status": "success",
  "html": "<!doctype html>...",
  "screenshot_url": null,
  "cookies": {
    "session_id": "xyz"
  },
  "headers": {
    "content-type": "text/html"
  },
  "metadata": {
    "request_id": "req_abc123",
    "provider": "null",
    "region": "jp",
    "fingerprint_id": "Mozilla/5.0 ...",
    "worker_id": "worker-jp-0",
    "duration_ms": 120,
    "block_detected": false,
    "retries": 0
  },
  "error_code": null,
  "message": null
}
```

Error response body for worker-level failures:

```json
{
  "request_id": "req_abc123",
  "status": "error",
  "html": null,
  "screenshot_url": null,
  "cookies": {},
  "headers": {},
  "metadata": {
    "request_id": "req_abc123",
    "provider": "custom",
    "region": "jp",
    "fingerprint_id": "",
    "worker_id": "worker-jp-0",
    "duration_ms": 10,
    "block_detected": false,
    "retries": 0
  },
  "error_code": "WORKER_ERROR",
  "message": "human-readable error"
}
```

### `POST /v1/sessions`

Purpose: create a stateful browser session record.

Authentication: required.

Request:

```json
{
  "region": "jp",
  "fingerprint": "japan_ecommerce"
}
```

Response:

```json
{
  "session_id": "sess_abc123",
  "created_at": 1716892800.0,
  "expires_at": 1716893100.0
}
```

### `GET /v1/sessions/{session_id}`

Purpose: inspect one session.

Authentication: required.

Release requirement: the returned session must belong to the authenticated key.

Response:

```json
{
  "session_id": "sess_abc123",
  "region": "jp",
  "fingerprint": "japan_ecommerce",
  "request_count": 7,
  "created_at": 1716892800.0,
  "last_used": 1716892900.0,
  "expires_at": 1716893100.0
}
```

### `DELETE /v1/sessions/{session_id}`

Purpose: close one session.

Authentication: required.

Release requirement: only the owning key may delete the session.

Response: `204 No Content`.

### `GET /v1/usage`

Purpose: show current key usage.

Authentication: required.

Release requirement: this endpoint must be registered before public launch.

Response:

```json
{
  "tier": "starter",
  "requests_used": 4821,
  "requests_limit": 25000,
  "reset_at": "2026-06-01T00:00:00Z"
}
```

### Billing Endpoints

Billing endpoints are allowed in v1 only if Stripe environment variables are configured and test-mode checkout/webhook flows pass end to end.

- `POST /v1/billing/checkout`
- `POST /v1/billing/license-checkout`
- `POST /v1/billing/webhook`
- `GET /v1/billing/portal`
- `GET /v1/billing/invoices`
- `POST /v1/billing/verify-license`

If billing is not launch-ready, Stripe-backed routes must be excluded from deployment by keeping `billing.enabled=false`. License verification may remain mounted because it does not depend on Stripe.

## 8. Error Codes

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | Request shape or option is invalid. |
| 402 | `OVERAGE_LIMIT_EXCEEDED` | Paid usage exceeded the configured overage threshold. |
| 403 | `MISSING_AUTH` | Authorization header missing. |
| 403 | `INVALID_API_KEY` | API key is invalid or revoked. |
| 404 | `SESSION_NOT_FOUND` | Session does not exist, expired, or is not owned by the key. |
| 429 | `RATE_LIMIT_EXCEEDED` | Sliding-window rate limit exhausted. |
| 500 | `INTERNAL_ERROR` | Unexpected service error. |
| 503 | `SERVICE_NOT_INITIALIZED` | Worker pool or backing service is unavailable. |

## 9. Session Model

Session fields:

- `session_id`
- `key_id`
- `region`
- `fingerprint`
- encrypted cookies
- `created_at`
- `last_used`
- `request_count`
- `expires_at`
- `deleted_at` in PostgreSQL mode

Session TTL defaults to `pool.session_ttl_seconds`.

Security requirement: every session read, scrape use, and delete must check ownership against the authenticated `key_id`.

## 10. Worker Pool Requirements

- Worker pools are region scoped.
- Each region has a bounded semaphore equal to configured worker count.
- `acquire(region)` normalizes missing or unknown regions to `default_region`.
- `release(worker)` releases the region that was actually acquired, not the caller-provided region.
- Worker busy state is cleared exactly once.
- Shutdown must stop all browser workers.

## 11. Proxy Provider Requirements

All providers implement:

```python
class ProxyProviderInterface:
    @property
    def name(self) -> str: ...
    async def get_proxy(self, region: str | None = None) -> ProxyConfig: ...
    async def release_proxy(self, proxy: ProxyConfig) -> None: ...
    async def health_check(self) -> bool: ...
```

Provider lifecycle rules:

- A borrowed proxy must be released in a `finally` block.
- Provider health status must not block `null` provider operation.
- Provider API failures must be converted into scrape error responses, not leaked task exceptions.
- Fallback provider routing is not release-ready until covered by integration tests.

## 12. Persistence Requirements

PostgreSQL schema owns:

- `api_keys`
- `sessions`
- `usage_records`
- `daily_usage`
- `audit_log`

Timestamp columns use timezone-aware `datetime` values, not raw float epochs.

Local development may run without `DATABASE_URL`; production cloud mode must not.

## 13. Rate Limits and Usage

Rate limits are tier based.

| Tier | Window | Requests |
| --- | --- | --- |
| free | 1 hour | 500 |
| starter | 1 day | 25,000 |
| pro | 1 day | 200,000 |
| enterprise | 1 day | unlimited or contract-defined |

Usage tracking rules:

- Every scrape attempt records exactly one usage row in PostgreSQL mode.
- Usage rows include key, request ID, domain, status, duration, block flag, region, and creation time.
- Daily rollups must be idempotent.
- Overage behavior must be tested before billing is enabled.

### Backends

| Backend | Module | Scale | Notes |
|---------|--------|-------|-------|
| In-memory sliding window | `asb_api.api.rate_limiter.SlidingWindowLimiter` | Single process | Dev mode. Restart loses state. Not safe across multiple API processes. |
| PostgreSQL advisory lock | `asb_api.db.rate_limiter.PostgresRateLimiter` | Single DB, 1-3 API processes | Production for v1. Counts `usage_records` rows via advisory-lock serialization. |
| Redis (post-v1) | (future) | Multi-process, multi-region | Evaluated in [docs/REDIS_RATE_LIMITING.md](docs/REDIS_RATE_LIMITING.md). Recommended when >1 API process or >100 req/s per key.

## 14. Security Requirements

- Use HTTPS in cloud deployment.
- Use `secrets.compare_digest` for key verification.
- Store only hashed API keys.
- Do not log raw API keys, authorization headers, cookies, or request bodies by default.
- Encrypt cookies at rest when encryption key is configured.
- Validate session ownership on all session endpoints and scrape session usage.
- Keep Stripe and license secrets in environment variables only.
- Add CORS only for explicit dashboard origins, not wildcard, before dashboard launch.

## 15. Deployment

### Local

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m asb_api
```

### Docker

```bash
docker build -t asb-cloud-api .
docker run --rm -p 8000:8000 --env-file .env asb-cloud-api
```

### Required Release Environment

```text
ASB_CONFIG_PATH=config.yaml
DATABASE_URL=postgres://...
COOKIE_ENCRYPTION_KEY=...
STRIPE_SECRET_KEY=...
STRIPE_WEBHOOK_SECRET=...
LICENSE_SECRET_KEY=...
ASB_SCREENSHOT_DIR=/tmp/screenshots
```

## 16. Verification Plan

### Automated Checks

- `python -m compileall -q asb_api tests`
- `python -m unittest discover -s tests -v`
- Clawpatch report has no high or medium open findings for release-owned files.

### Manual Smoke Tests

1. Start API in in-memory mode.
2. Capture the default startup API key from logs.
3. Call `GET /v1/health`.
4. Call `POST /v1/scrape` with GET against `https://example.com`.
5. Call `POST /v1/scrape` with POST against a local echo endpoint and verify method/body.
6. Create a session, scrape with it, verify cookies and request count update.
7. Request a screenshot and verify the returned path exists.
8. Start with PostgreSQL and run migrations.
9. Create, verify, list, and revoke API keys with admin CLI.
10. Verify usage rows are written after scrape attempts.

## 17. Known Release Blockers

These must be fixed before a public paid launch:

- Verify Stripe checkout and webhook behavior in test mode.
- Decide screenshot delivery model: local self-hosted path vs cloud object storage URL.
- Confirm legal terms for patent, self-hosted license, and customer data handling.

## 18. Release Decision

The API may be released privately to beta users when:

- All v1 functional MUSTs pass.
- All high/medium Clawpatch findings are fixed or explicitly marked false-positive.
- Manual smoke tests pass in both local and Docker mode.
- PostgreSQL mode passes key/session/usage flows.
- Billing is either fully tested or disabled.
- Documentation covers setup, auth, scrape, sessions, errors, and support contact.

The API may be released publicly only after all known release blockers are closed.
