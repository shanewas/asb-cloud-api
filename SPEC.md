# ASB Cloud API — Specification

> **Status:** Draft v1  
> **Author:** Nova (for Shanewas Ahmed Nabil)  
> **Date:** 2026-05-28  
> **License:** Proprietary — Patent JP 2025-169170 A  

---

## 1. Product Overview

### What
ASB Cloud API is a paid REST API that gives developers programmatic access to stealth browser sessions. Customers send a URL + config via `POST`, receive page content, screenshots, cookies, and metadata.

Two delivery models:

| Model | Who Runs It | Price |
|---|---|---|
| **Cloud API** | We run it | Subscription + usage |
| **Self-Hosted** | Customer runs it | One-time license |

### Why
- Commercialize the ASB stealth fingerprinting patent
- Create a recurring revenue SaaS with defensible IP
- Serve both startups (Cloud API) and enterprises (Self-Hosted)

### Core Principle: Proxy Is a Plugin
Proxy providers are swappable implementation details. Zero proxy-specific code exists in core. The system works identically whether the active provider is Decodo, Bright Data, a custom HTTP proxy, or no proxy at all.

---

## 2. Architecture

### Layers
```
┌──────────────────────────────────────┐
│           API Gateway                │
│  (auth, rate-limit, routing)         │
├──────────────────────────────────────┤
│        Session Orchestrator           │
│  (pool manager, fingerprint rotate) │
├──────────────────────────────────────┤
│          Worker Pool                  │
│  (ASB instances, async)              │
├──────────────────────────────────────┤
│     Proxy Provider Interface         │
│  (Decodo / Bright Data / Custom /    │
│   None — all identical contract)     │
└──────────────────────────────────────┘
```

### Async-First
All I/O is async. Every worker is an `asyncio` task. A single thread handles hundreds of concurrent sessions. Synchronous blocking anywhere in the request path is a bug.

### Self-Hosted = Same Codebase
Cloud and Self-Hosted run identical code. The only difference is deployment topology:

- **Cloud:** Workers run on our VPS, behind a load balancer
- **Self-Hosted:** Workers run in customer's Docker container, no gateway

---

## 3. Proxy Provider Interface

### Contract
Every provider implements this interface exactly:

```python
class ProxyProviderInterface(ABC):
    @abstractmethod
    def get_proxy(self, region: str | None) -> ProxyConfig:
        """Borrow one proxy from pool. Raises PoolExhaustedError."""

    @abstractmethod
    def release_proxy(self, proxy: ProxyConfig) -> None:
        """Return proxy after use."""

    @abstractmethod
    def health_check(self) -> bool:
        """Returns True if provider API is reachable."""

    @property
    def name(self) -> str:
        """Provider name for logs/metadata only."""
```

### ProxyConfig Dataclass
```python
@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    protocol: Literal["http", "socks5", "socks4"]
    region: str | None          # "jp", "us", "eu", etc.
    sticky: bool               # maintain same IP for session
    session_token: str | None  # for sticky affinity
```

### Provider Types

#### `null` (built-in)
Direct connection, no proxy. Always available.

#### `custom` (built-in)
```yaml
custom:
  enabled: true
  proxies:
    - host: "203.0.113.1"
      port: 8080
      username: "user"
      password: "pass"
    - host: "203.0.113.2"
      port: 8080
```

#### `decodo` (optional)
```yaml
decodo:
  enabled: true
  api_key: "${DECODO_API_KEY}"
  pool_size: 10
  regions: [jp, us, eu]
  default_region: jp
```

#### `brightdata` (optional)
```yaml
brightdata:
  enabled: false
  api_key: "${BRIGHTDATA_API_KEY}"
  zones: [isp_mobile, residential]
```

#### Provider Priority
```yaml
providers:
  primary: decodo
  fallback: null   # if primary is down, use direct connection
```

---

## 4. Configuration Schema

### `config.yaml`
```yaml
# ============================================
# APPLICATION
# ============================================
app:
  host: "0.0.0.0"
  port: 8000
  log_level: "info"
  debug: false

# ============================================
# SECURITY
# ============================================
security:
  api_key_hash_algorithm: "argon2"
  cookie_encryption_key: "${COOKIE_ENCRYPTION_KEY}"
  log_url_domains_only: true       # never log full URLs
  redact_authorization_headers: true

# ============================================
# PROXY PROVIDERS
# ============================================
providers:
  null:
    enabled: true

  custom:
    enabled: false
    proxies: []

  decodo:
    enabled: false
    api_key: ""
    pool_size: 10
    regions: [jp, us, eu]
    default_region: jp

  brightdata:
    enabled: false
    api_key: ""
    zones: [residential]

provider_priority:
  primary: null
  fallback: null

# ============================================
# SESSION POOL
# ============================================
pool:
  max_workers: 20
  session_ttl_seconds: 300
  idle_timeout_seconds: 60
  prewarm_on_startup: true
  max_retries_per_request: 2

# ============================================
# FINGERPRINT
# ============================================
fingerprint:
  rotation_strategy: "per_request"   # per_request | per_session | sticky
  default_preset: "general"
  presets:
    general:
      viewport: [1920, 1080]
      user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      webgl_vendor: "Google Inc. (Intel)"
      canvas: "noise"
    japan_ecommerce:
      viewport: [1920, 1080]
      user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
      webgl_vendor: "Apple Inc."
      canvas: "noise"
      accept_language: "ja-JP,ja;q=0.9"
    mobile_jp:
      viewport: [390, 844]
      user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
      platform: "iPhone"

# ============================================
# RATE LIMITS (in-memory for MVP, Redis later)
# ============================================
rate_limits:
  free:
    requests: 500
    window_seconds: 3600
    concurrent_sessions: 2
  starter:
    requests: 25000
    window_seconds: 86400
    concurrent_sessions: 10
  pro:
    requests: 200000
    window_seconds: 86400
    concurrent_sessions: 50
  enterprise:
    requests: -1          # unlimited
    window_seconds: 86400
    concurrent_sessions: 200

# ============================================
# BILLING (Stripe — paid phase only)
# ============================================
billing:
  enabled: false          # flip to true after Stripe integration
  stripe_api_key: "${STRIPE_API_KEY}"
  webhook_secret: "${STRIPE_WEBHOOK_SECRET}"
  free_tier_hard_cap: true

# ============================================
# SELF-HOSTED MODE
# ============================================
self_hosted:
  enabled: true
  license_key_required: false   # true = require license key to run
  telemetry_enabled: false      # opt-in usage telemetry to cloud
```

---

## 5. API Design

### Base URL
```
Cloud:  https://api.asb.io/v1
Self-hosted: http://localhost:8000/v1
```

### Authentication
```
Authorization: Bearer sk_live_xxxx
```
- Keys: `sk_live_xxxx` (live) / `sk_test_xxxx` (test)
- Keys stored as Argon2 hash in DB
- Never logged; masked in all responses

---

### Endpoints

#### `POST /v1/scrape` — Execute a scrape
```
Request
{
  "url": "https://example.com",
  "method": "GET",                          # GET | POST
  "headers": {},                            # optional extra headers
  "data": {},                              # optional POST body
  "proxy_provider": "decodo",              # optional override
  "region": "jp",                          # optional; uses provider default
  "fingerprint": "japan_ecommerce",       # optional; uses default preset
  "timeout": 30,                           # default 30s
  "screenshot": false,
  "session_id": null,                     # null = stateless, uuid = sticky
  "session_type": "stateless"              # stateless | stateful | stateful_reset
}

Response (200 OK)
{
  "request_id": "req_abc123",
  "status": "success",
  "html": "<!DOCTYPE html>...",
  "screenshot_url": "https://cdn.asb.io/screenshots/req_abc123.png",
  "cookies": {"session_id": "xyz"},
  "headers": {"content-type": "text/html"},
  "metadata": {
    "provider": "decodo",
    "region": "jp",
    "fingerprint_id": "jpecom_042",
    "worker_id": "worker-jp-3",
    "duration_ms": 1847,
    "block_detected": false,
    "retries": 0
  }
}

Error (429 Too Many Requests)
{
  "error_code": "RATE_LIMIT_EXCEEDED",
  "message": "Rate limit reached",
  "limit": 500,
  "remaining": 0,
  "reset_at": 1716892800
}
```

#### `POST /v1/sessions` — Create a stateful session
```
Request
{
  "region": "jp",
  "fingerprint": "japan_ecommerce"
}

Response
{
  "session_id": "sess_abc123",
  "created_at": "2026-05-28T12:00:00Z",
  "expires_at": "2026-05-28T12:05:00Z"
}
```

#### `GET /v1/sessions/{id}` — Get session info
```
Response
{
  "session_id": "sess_abc123",
  "region": "jp",
  "fingerprint": "japan_ecommerce",
  "request_count": 7,
  "created_at": "...",
  "last_used": "...",
  "expires_at": "..."
}
```

#### `DELETE /v1/sessions/{id}` — Close session
```
Response 204 No Content
```

#### `GET /v1/usage` — Current usage
```
Response
{
  "tier": "starter",
  "requests_used": 4821,
  "requests_limit": 25000,
  "reset_at": "2026-06-01T00:00:00Z"
}
```

#### `GET /v1/health` — Health check
```
Response
{
  "status": "healthy",
  "providers": {
    "decodo": { "status": "up", "latency_ms": 142 },
    "null":   { "status": "up", "latency_ms": 0 }
  },
  "workers": { "active": 12, "idle": 8 }
}
```

---

## 6. Session Model

### Types

| Type | Cookies | Proxy | Use Case |
|---|---|---|---|
| `stateless` | New per request | Fresh per request | Public pages |
| `stateful` | Persists across requests | Sticky | Auth-gated sites |
| `stateful_reset` | Persists, cleared before each request | Sticky | Cart/checkout flows |

### Lifecycle
```
Session created
  → Worker assigned
  → Worker holds session until:
      - idle_timeout reached (return to pool)
      - TTL reached (hard expire)
      - explicit DELETE /v1/sessions/{id}
  → Cookie jar encrypted and stored in DB
```

### State Storage
- Sessions stored in PostgreSQL
- Cookie jar encrypted with Fernet (AES) before storage
- Session record: `id, key_id, region, fingerprint_id, cookies_blob, created_at, last_used, request_count, expires_at`

---

## 7. Worker Pool

### Worker Process
```python
class ASBWorker:
    worker_id: str
    provider: ProxyProviderInterface
    browser: Browser | None  # Playwright/ASB
    fingerprint: Fingerprint
    current_session: ScrapeSession | None

    async def scrape(self, request: ScrapeRequest) -> ScrapeResponse:
        proxy = self.provider.get_proxy(request.region)
        try:
            fp = self.fingerprint_generator.get(request.fingerprint)
            await self.browser.set_fingerprint(fp)
            await self.browser.set_proxy(proxy)
            result = await self.browser.navigate(request.url)
            return result
        finally:
            self.provider.release_proxy(proxy)
```

### Pool Manager
```python
class WorkerPool:
    def __init__(self, size: int, provider: ProxyProviderInterface):
        self.semaphore = asyncio.Semaphore(size)
        self.workers: list[ASBWorker] = []

    async def acquire(self) -> ASBWorker:
        """Block until a worker is available."""
        await self.semaphore.acquire()
        return self._get_idle_worker()

    def release(self, worker: ASBWorker):
        self.semaphore.release()
```

### Block Handling (Anti-Block Orchestrator)
Built on existing ASB `AntiBlockOrchestrator`. Triggered automatically:

| Signal | Action |
|---|---|
| Block page detected | Rotate fingerprint + proxy, retry once |
| Timeout | Retry with same fingerprint, new session |
| Browser crash | Restart browser, retry once |
| Provider exhausted | Circuit breaker → fallback provider |
| Provider down | Health check fails → switch to fallback provider |

---

## 8. Security

| Threat | Mitigation |
|---|---|
| API key leakage | Hash with Argon2; never log; rotate on demand |
| Scraped content interception | HTTPS only; content never written to disk on server |
| Session cookie theft | Encrypted blob in DB; Fernet/AES; key from env var |
| Customer scraping each other | Isolated browser profiles per request |
| URL logging PII | Log domain only; never log query params or paths |
| Authorization header leaking | Strip before all logging |
| Japanese data compliance | Default region = JP; no data crosses region without explicit config |
| Self-hosted license bypass | HMAC-signed license keys; clock-tied expiry |

---

## 9. Build Phases

### Phase 0 — Prototype (3-4 days)
**Goal:** Prove the core loop works. Single VPS, curl-accessible.

- [ ] Modular `ProxyProviderInterface` with `null` and `custom` providers
- [ ] Single `ASBWorker` process (no pool yet)
- [ ] `POST /v1/scrape` endpoint — hardcoded API key
- [ ] Async scrape flow: request → ASB → response
- [ ] No auth, no rate-limit, no persistence
- [ ] Test with curl from local machine

**Deliverable:** `curl -X POST http://vps:8000/v1/scrape -d '{"url":"https://example.com"}'` returns HTML.

---

### Phase 1 — MVP (7-10 days)
**Goal:** First real customers can sign up and pay. No billing yet.

- [ ] Multi-worker pool (`max_workers` configurable)
- [ ] Region routing (workers tagged by region; request hints route to right pool)
- [ ] Full proxy provider suite (null, custom, decodo, brightdata)
- [ ] Provider health check + circuit breaker → fallback chain
- [ ] `POST /v1/sessions` — stateful session with encrypted cookie jar
- [ ] API key auth (in-memory store; PostgreSQL in Phase 2)
- [ ] In-memory sliding window rate limiter per API key
- [ ] Usage tracking (increment on each request)
- [ ] Self-hosted Docker image (`Dockerfile`)
- [ ] Static landing page (Pitchground, Gumroad, or Vercel)
- [ ] Manual key creation via admin command

**Deliverable:** Customer gets an API key via email, can make requests from their code, billed manually.

---

### Phase 2 — Persistence (3-4 days)
**Goal:** Everything persists. Real database, not in-memory.

- [ ] PostgreSQL schema (api_keys, usage_records, sessions, requests)
- [ ] Migrate API key store from in-memory → PostgreSQL
- [ ] Migrate rate limiting from in-memory → PostgreSQL advisory locks
- [ ] Session store (cookie jar serialization + encryption)
- [ ] Usage rollup job (daily aggregation)
- [ ] Audit log table (request_id, key_id, domain, status, duration_ms, timestamp)

**Deliverable:** Data survives restart. Usage stats accurate within 1 minute.

---

### Phase 3 — Billing (5-7 days)
**Goal:** Get money. Stripe integration, first revenue.

- [ ] Stripe SDK integration
- [ ] Product/price setup in Stripe dashboard
- [ ] Checkout session for upgrades
- [ ] Webhook handler: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`
- [ ] Tier enforcement (upgrade key tier on subscription.active; downgrade on deleted)
- [ ] Metered billing for overage on Starter
- [ ] Invoice history (fetch from Stripe API, display link)
- [ ] Test mode fully functional before going live

**Pricing:**
| Tier | Monthly | Includes | Overage |
|---|---|---|---|
| Free | $0 | 500 req/mo | Hard cap |
| Starter | $49 | 25K req/mo | $2/1K |
| Pro | $149 | 200K req/mo | $1/1K |
| Enterprise | $499+ | Unlimited + SLA | Per contract |

**Self-hosted license:**
| License | Price |
|---|---|
| Solo | $199 one-time |
| Team | $499 one-time |
| Enterprise | $999+ one-time |

**Deliverable:** Real credit card payments. Self-hosted license purchase via Stripe.

---

### Phase 4 — Dashboard & SDKs (7-10 days)
**Goal:** Self-serve. No manual key creation.

- [ ] Next.js dashboard (email auth via magic link)
- [ ] API key CRUD (create, name, revoke, view usage)
- [ ] Usage chart (7-day / 30-day)
- [ ] Billing page (current plan, upgrade/downgrade, invoices)
- [ ] Embedded API docs (or link to docs page)
- [ ] Playground (try-it console with their own key)
- [ ] Python SDK (`pip install asb-api`)
- [ ] Node SDK (`npm install asb-api`)
- [ ] CLI tool (`pip install asb-api-cli`)

**Deliverable:** Customer signs up, pays, gets key, and integrates in < 10 minutes.

---

### Phase 5 — Scale (ongoing)
**Goal:** Production hardening.

- [ ] Redis rate limiting (replace PostgreSQL advisory locks)
- [ ] Load testing (k6, target: 100 concurrent workers)
- [ ] Auto-scaling (multiple VPS or Railway/Render)
- [ ] Multi-region workers (JP + US + EU)
- [ ] Usage analytics dashboard (ClickHouse or Postgres + Grafana)
- [ ] Webhook subscriptions (customer-provided endpoint for scrape events)
- [ ] Bulk scrape endpoint (`POST /v1/scrape/batch`)
- [ ] International patent filing (PCT for JP 2025-169170 A)

---

## 10. Directory Structure

```
asb-cloud-api/
├── config.yaml                    # Main configuration
├── Dockerfile                     # Self-hosted Docker image
├── docker-compose.yml             # Local dev stack
├── requirements.txt
├── asb_api/
│   ├── __main__.py               # uvicorn app entry
│   ├── config.py                 # YAML config loader
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── scrape.py         # POST /v1/scrape
│   │   │   ├── sessions.py        # POST/GET/DELETE /v1/sessions
│   │   │   ├── keys.py            # API key management
│   │   │   ├── usage.py           # GET /v1/usage
│   │   │   └── health.py          # GET /v1/health
│   │   ├── auth.py               # Bearer token validation
│   │   ├── rate_limiter.py       # Sliding window rate limiter
│   │   └── errors.py             # Error code definitions
│   ├── providers/
│   │   ├── __init__.py           # ProviderRegistry
│   │   ├── base.py               # ProxyProviderInterface
│   │   ├── null.py               # Direct connection
│   │   ├── custom.py             # User-defined HTTP/SOCKS
│   │   ├── decodo.py             # Decodo implementation
│   │   └── brightdata.py         # Bright Data implementation
│   ├── fingerprint/
│   │   ├── generator.py
│   │   ├── rotator.py
│   │   └── presets/
│   │       ├── general.py
│   │       ├── japan_ecommerce.py
│   │       └── mobile_jp.py
│   ├── session/
│   │   ├── manager.py
│   │   ├── models.py
│   │   └── store.py              # PostgreSQL session store
│   ├── workers/
│   │   ├── pool.py               # WorkerPool + semaphore
│   │   ├── worker.py             # ASBWorker
│   │   └── asb_runner.py         # ASB browser integration
│   ├── billing/
│   │   ├── stripe_client.py
│   │   ├── webhooks.py
│   │   ├── tier_enforcer.py
│   │   └── license.py            # Self-hosted HMAC license
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py          # asyncpg pool
│   │   └── schema.sql
│   └── utils/
│       ├── crypto.py              # Fernet cookie encryption
│       └── logger.py
├── sdk/
│   ├── python/
│   │   ├── asb_api/
│   │   │   ├── __init__.py
│   │   │   └── client.py
│   │   └── pyproject.toml
│   ├── node/
│   │   ├── package.json
│   │   └── src/
│   └── cli/
│       └── asb_cli/
├── dashboard/                    # Next.js app (Phase 4)
│   ├── app/
│   │   ├── page.tsx              # landing/dashboard
│   │   ├── login/
│   │   ├── keys/
│   │   ├── billing/
│   │   └── docs/
│   └── package.json
├── tests/
│   ├── unit/
│   │   ├── test_providers.py
│   │   ├── test_fingerprint.py
│   │   └── test_rate_limiter.py
│   ├── integration/
│   │   └── test_scrape_flow.py
│   └── load/
│       └── k6_scrape.js
├── SPEC.md
└── README.md
```

---

## 11. Technology Choices

| Component | Choice | Reason |
|---|---|---|
| API framework | FastAPI | Async-first, Pydantic validation, auto OpenAPI docs |
| ASB integration | Existing codebase | Reuse proven stealth logic |
| Browser | Playwright | ASB wraps Playwright; swap at driver layer |
| Database | PostgreSQL | ACID, JSON support for metadata, self-hosted-friendly |
| Cache/rate-limit | PostgreSQL advisory locks (MVP) → Redis (Scale) | Simpler ops; Redis only when needed |
| Auth | Argon2 for key hashing | Best-in-class for password/key hashing |
| Encryption | Fernet (AES-128-CBC + HMAC) | Built into `cryptography` library, easy |
| Docker | Multi-stage build, Alpine base | Small image, fast pull |
| Hosting (Cloud) | Railway or Hetzner | Simple deploy, good Japan latency |
| Billing | Stripe | Industry standard, great webhook support |
| Dashboard | Next.js + Tailwind | Fast to build, Vercel deploy |

---

## 12. Competitive Positioning

| Competitor | Weakness | ASB Cloud API Answer |
|---|---|---|
| ScraperAPI | Overused IPs, easy to detect | Fresh residential proxies + ASB fingerprinting |
| Bright Data | Expensive, rigid zones | Pay-per-use, modular proxy, regions |
| Zyte (Crawler API) | Complex setup, slow | Drop-in REST, async from day 1 |
| Oxylabs | No stealth focus | Built by ASB authors; proven patent |
| Apify | Expensive, JS-centric | Cheaper, async, better for static + stealth |

**Headline differentiators:**
1. **Patent-protected stealth** — JP 2025-169170 A is a defensible moat
2. **Modular proxy** — customers bring their own or choose; not locked in
3. **Japan residential IPs** — niche but high-value for Japanese e-commerce
4. **Self-hosted option** — enterprise can run on their own infra, pays once
5. **Async-first** — no thread blocking, high concurrency

---

## 13. Patent Considerations

JP 2025-169170 A covers the fingerprinting technology used in ASB.

**Before launch:**
- [ ] Confirm with a Japanese IP lawyer that operating the Cloud API doesn't require licensing the patent to customers (they're not receiving the technology, just a service)
- [ ] File PCT application within 12 months of JP filing to preserve international rights
- [ ] Consider if the self-hosted license needs explicit patent grant language in terms of service

---

## 14. What Is NOT In MVP

These are intentionally deferred:

- [ ] Redis (use PostgreSQL for everything until it hurts)
- [ ] Multi-region workers (Japan-only for now)
- [ ] Python/Node SDK (curl is fine)
- [ ] Dashboard (static page + manual key email)
- [ ] Bulk scrape endpoint
- [ ] Webhook subscriptions
- [ ] Analytics/ClickHouse
- [ ] Auto-scaling
- [ ] International patent filing

---

## 15. Success Metrics

### Prototype (Phase 0)
- `curl` returns HTML from `https://example.com` via stealth browser

### MVP (Phase 1)
- 3+ external beta users making real requests
- Self-hosted Docker image runs on a fresh VPS in < 10 minutes
- Zero data loss on worker restart

### Billing (Phase 3)
- First Stripe payment received
- Self-hosted license sold

### Dashboard (Phase 4)
- 10+ paying customers
- < 10 min signup-to-first-request time
- < $500/month hosting cost at 50 customers

### Scale (Phase 5)
- 99.5% API uptime
- 100 concurrent workers
- < 5s p95 latency on scrape requests
