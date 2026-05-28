# ASB Cloud API — Phase 1: MVP

**Goal:** First real customers can sign up. No billing yet.
**Working directory:** `/root/asb-cloud-api`
**Start from:** Phase 0 is done (imports clean, server starts). Read existing code before modifying.

## Context

Read these existing files before starting:
- `SPEC.md` (full spec)
- `asb_api/__main__.py` (current app entry)
- `asb_api/config.py` (current config loader)
- `asb_api/providers/__init__.py` (current registry)
- `asb_api/providers/base.py` (current interface)
- `asb_api/providers/null.py` (reference implementation)
- `asb_api/providers/custom.py` (reference implementation)
- `asb_api/workers/pool.py` (current pool)
- `asb_api/workers/worker.py` (current worker)
- `asb_api/api/routes/scrape.py` (current scrape route)
- `asb_api/session/models.py` (current models)
- `config.yaml` (current config)

Phase 0 already has: null provider, custom provider, basic worker pool, basic config.
Phase 1 adds: decodo/brightdata providers, region routing, health checks + circuit breaker, stateful sessions, API key auth, rate limiting, usage tracking, Docker image.

---

## What to Build

### 1. Decodo Provider (`asb_api/providers/decodo.py`)

```python
# Decodo API docs: https://api.decodo.com/docs
# API key format: ${DECODO_API_KEY}
# Proxy endpoint: https://api.decodo.com/v2/proxy
# Zones: residential, isp, mobile
# Regions: jp, us, eu, kr, etc.

# GET https://api.decodo.com/v2/proxy?zone=residential&country=jp&pool_size=10
# Returns: { "proxy": [{ "host": "...", "port": 12345, "username": "...", "password": "..." }] }

# The Decodo provider:
# - On init: call API to fetch available proxy pool
# - get_proxy(region): filter proxies by region, return one from pool, mark as "in use"
# - release_proxy(proxy): mark as "available" again
# - health_check(): call Decodo API status endpoint, return True if up
# - Pool is pre-fetched on startup and refreshed every 5 minutes
```

The `get_proxy` call from Decodo should include `pool_size` matching the config. Use `httpx` for async HTTP calls.

### 2. BrightData Provider (`asb_api/providers/brightdata.py`)

```python
# Bright Data API: https://api.brightdata.com
# Zone format: zone name + customer ID
# API: https://api.brightdata.com/v1/zones/{zone}/get_creds
# Returns: { "host": "...", "port": 22225, "username": "...", "password": "..." }

# For region targeting, pass country code: ?country=jp
# Sticky sessions: add &session=static_xxx
```

### 3. Health Check + Circuit Breaker (`asb_api/providers/health.py`)

```python
# Per-provider health state:
# - UP: healthy, use normally
# - DEGRADED: too many failures, use less
# - DOWN: provider unreachable, skip entirely
# - RECOVERING: test periodically, promote to UP when healthy

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure: float | None = None
        self.state: Literal["UP", "DEGRADED", "DOWN", "RECOVERING"] = "UP"

    async def record_success(self):
        self.failures = 0
        if self.state == "RECOVERING":
            self.state = "UP"

    async def record_failure(self):
        self.failures += 1
        self.last_failure = asyncio.get_event_loop().time()
        if self.failures >= self.failure_threshold:
            self.state = "DOWN"

    async def get_proxy(self, region):
        if self.state == "DOWN":
            # Check if recovery timeout passed
            if asyncio.get_event_loop().time() - self.last_failure > self.recovery_timeout:
                self.state = "RECOVERING"
            else:
                raise ProviderExhaustedError(f"Provider DOWN, try fallback")
        return await self.provider.get_proxy(region)
```

The fallback chain in config is: `provider_priority.primary` → `provider_priority.fallback`.
On `PoolExhaustedError` or `ProviderError`, try fallback provider.

### 4. Region Routing (`asb_api/workers/pool.py`)

Current pool is flat. Modify to support region-tagged workers:

```python
# config.yaml pool section:
# pool:
#   workers_per_region:
#     jp: 5
#     us: 3
#     eu: 2
#   default_region: jp

class WorkerPool:
    def __init__(self, workers_per_region: dict[str, int], ...):
        self.pools: dict[str, asyncio.Semaphore] = {}
        self.workers: dict[str, list[ASBWorker]] = {}
        for region, size in workers_per_region.items():
            self.pools[region] = asyncio.Semaphore(size)
            self.workers[region] = [
                ASBWorker(f"worker-{region}-{i}", ...) 
                for i in range(size)
            ]

    async def acquire(self, region: str | None) -> ASBWorker:
        region = region or self.default_region
        await self.pools[region].acquire()
        for w in self.workers[region]:
            if not getattr(w, "_busy", False):
                w._busy = True
                return w
        return self.workers[region][0]

    def release(self, worker: ASBWorker, region: str):
        worker._busy = False
        self.pools[region].release()
```

Update `__main__.py` to read `workers_per_region` from config and build region-tagged pools.

### 5. Stateful Sessions with Encrypted Cookie Jar

**`asb_api/session/store.py`** — in-memory session store for MVP (PostgreSQL comes in Phase 2):

```python
import uuid
import json
import time
import asyncio
from cryptography.fernet import Fernet
from .models import SessionInfo

class SessionStore:
    def __init__(self, encryption_key: str | None, ttl_seconds: int = 300):
        self.encryption_key = encryption_key
        self.fernet = Fernet(encryption_key.encode()) if encryption_key else None
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _encrypt(self, data: bytes) -> bytes:
        if self.fernet:
            return self.fernet.encrypt(data)
        return data

    def _decrypt(self, data: bytes) -> bytes:
        if self.fernet:
            return self.fernet.decrypt(data)
        return data

    async def create(self, key_id: str, region: str, fingerprint: str) -> SessionInfo:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        now = time.time()
        session = {
            "session_id": session_id,
            "key_id": key_id,
            "region": region,
            "fingerprint": fingerprint,
            "cookies": {},
            "created_at": now,
            "last_used": now,
            "request_count": 0,
            "expires_at": now + self.ttl_seconds,
        }
        async with self._lock:
            self._sessions[session_id] = session
        return SessionInfo(**session)

    async def get(self, session_id: str) -> SessionInfo | None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            if time.time() > s["expires_at"]:
                del self._sessions[session_id]
                return None
            s["last_used"] = time.time()
            return SessionInfo(**s)

    async def update_cookies(self, session_id: str, cookies: dict):
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["cookies"] = cookies
                self._sessions[session_id]["last_used"] = time.time()

    async def increment_count(self, session_id: str):
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["request_count"] += 1
                self._sessions[session_id]["last_used"] = time.time()

    async def delete(self, session_id: str):
        async with self._lock:
            self._sessions.pop(session_id, None)
```

Update `SessionInfo` in `models.py` to have all fields needed.

**`POST /v1/sessions`** route in `asb_api/api/routes/sessions.py`:
- Create a stateful session, return session_id + expiry
- Sessions are isolated per API key

**Worker update** — when `session_type` is `stateful` and `session_id` is provided:
- Load cookies from session store
- Apply cookies to browser context before navigating
- After navigation, save updated cookies back to session store
- For `stateful_reset`: clear cookies from context before each request, but keep session record

### 6. API Key Auth (`asb_api/api/auth.py`)

In-memory store for MVP:

```python
import hashlib
import secrets
import argon2
from dataclasses import dataclass

@dataclass
class APIKey:
    key_id: str
    key_hash: str
    tier: Literal["free", "starter", "pro", "enterprise"]
    owner_email: str | None
    created_at: float
    revoked: bool = False

class InMemoryKeyStore:
    def __init__(self):
        self._keys: dict[str, APIKey] = {}

    def create(self, tier: str = "free", owner_email: str | None = None) -> tuple[str, APIKey]:
        """Create a new API key. Returns (raw_key, key_object). Raw key is only shown once."""
        key_id = f"key_{secrets.token_hex(8)}"
        raw_key = f"sk_live_{secrets.token_hex(24)}"
        key_hash = argon2.PasswordHasher().hash(raw_key)
        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            tier=tier,
            owner_email=owner_email,
            created_at=time.time(),
        )
        self._keys[key_id] = api_key
        self._raw_keys[raw_key] = key_id  # temp lookup, remove after verify
        return raw_key, api_key

    def verify(self, raw_key: str) -> APIKey | None:
        # raw_key format: sk_live_xxx or sk_test_xxx
        key_id = self._raw_keys.get(raw_key)
        if not key_id:
            return None
        api_key = self._keys.get(key_id)
        if not api_key or api_key.revoked:
            return None
        return api_key

    def get(self, key_id: str) -> APIKey | None:
        return self._keys.get(key_id)

    def revoke(self, key_id: str):
        if key_id in self._keys:
            self._keys[key_id].revoked = True
```

Actually, don't use Argon2 for verification of raw keys since the raw key itself is the secret. Use SHA256 for speed, or use `secrets.compare_digest` for timing-safe comparison.

Simplified approach for MVP:
```python
# Store: key_id → (key_prefix_hash, tier, revoked)
# key_prefix_hash = hash of first 8 chars of raw key (for lookup) + full key hash
# Raw key never stored. On verify: hash provided key, compare to stored hash.

class InMemoryKeyStore:
    def __init__(self):
        self._keys: dict[str, dict] = {}  # key_id → {hash, tier, email, revoked}

    def create(self, tier="free", owner_email=None):
        raw = f"sk_live_{secrets.token_hex(24)}"
        key_id = f"key_{secrets.token_hex(8)}"
        # Store hash of full key
        h = hashlib.sha256(raw.encode()).hexdigest()
        self._keys[key_id] = {"hash": h, "tier": tier, "email": owner_email, "revoked": False}
        return raw, key_id

    def verify(self, raw: str) -> str | None:
        """Verify raw key. Returns key_id if valid, else None."""
        h = hashlib.sha256(raw.encode()).hexdigest()
        for kid, info in self._keys.items():
            if not info["revoked"] and secrets.compare_digest(h, info["hash"]):
                return kid
        return None
```

Add to `auth.py` a `get_api_key` dependency for FastAPI:
```python
from fastapi import Header, HTTPException

async def get_api_key(x_authorization: str = Header(None)) -> str:
    if not x_authorization:
        raise HTTPException(403, "Missing Authorization header")
    # Strip "Bearer " prefix
    raw = x_authorization.removeprefix("Bearer ").strip()
    key_id = key_store.verify(raw)
    if not key_id:
        raise HTTPException(403, "Invalid API key")
    return key_id
```

### 7. Rate Limiter (`asb_api/api/rate_limiter.py`)

Sliding window rate limiter, in-memory for MVP:

```python
import time
import asyncio
from collections import deque

class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key_id: str) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, reset_at_unix). Raises RateLimitExceeded."""
        now = time.time()
        async with self._lock:
            if key_id not in self._windows:
                self._windows[key_id] = deque()
            window = self._windows[key_id]
            # Remove expired entries
            cutoff = now - self.window_seconds
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self.max_requests:
                reset_at = int(window[0] + self.window_seconds)
                raise RateLimitExceeded(
                    limit=self.max_requests,
                    remaining=0,
                    reset_at=reset_at,
                )
            window.append(now)
            return True, self.max_requests - len(window), int(now + self.window_seconds)

class RateLimitExceeded(HTTPException):
    def __init__(self, limit: int, remaining: int, reset_at: int):
        super().__init__(
            status_code=429,
            detail={
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": "Rate limit reached",
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at,
            },
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_at),
            }
        )
```

Load limits from `config.rate_limits` by tier.

### 8. Usage Tracking (`asb_api/api/usage.py`)

Simple in-memory counter (Phase 2 moves to PostgreSQL):

```python
class UsageTracker:
    def __init__(self):
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._daily: dict[str, str] = {}  # key_id → "YYYY-MM-DD"

    async def increment(self, key_id: str):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        async with self._lock:
            self._counts[key_id] = self._counts.get(key_id, 0) + 1
            self._daily[key_id] = today

    async def get(self, key_id: str) -> int:
        return self._counts.get(key_id, 0)
```

### 9. Sessions Route (`asb_api/api/routes/sessions.py`)

```python
from fastapi import APIRouter, Depends

router = APIRouter()

@router.post("/v1/sessions")
async def create_session(
    request: CreateSessionRequest,
    key_id: str = Depends(get_api_key),
):
    session = await session_store.create(
        key_id=key_id,
        region=request.region,
        fingerprint=request.fingerprint,
    )
    return session

@router.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, key_id: str = Depends(get_api_key)):
    session = await session_store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session

@router.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, key_id: str = Depends(get_api_key)):
    await session_store.delete(session_id)
    return Response(status_code=204)
```

### 10. Updated Scrape Route with Auth + Rate Limit + Sessions

Update `scrape.py`:
- Add `key_id = Depends(get_api_key)` to the scrape endpoint
- Add `await rate_limiter.check(key_id)` at the start
- Add `await usage_tracker.increment(key_id)` on success
- For stateful sessions: load cookies from session_store before calling worker, save after

### 11. Health Route (`asb_api/api/routes/health.py`)

```python
@router.get("/v1/health")
async def health():
    return {
        "status": "healthy",
        "providers": {
            name: {"status": "up", "latency_ms": await p.health_check()}
            for name, p in registry.get_all_providers()
        },
        "workers": {
            region: {"active": sum(1 for w in workers if w._busy), "idle": len(workers) - sum(1 for w in workers if w._busy)}
            for region, workers in pool.workers.items()
        }
    }
```

### 12. Updated App Entry (`asb_api/__main__.py`)

Update `startup()` to:
1. Load config
2. Initialize ProviderRegistry + all providers (null, custom, decodo, brightdata)
3. Resolve active provider (primary → fallback on error)
4. Initialize CircuitBreaker per provider
5. Initialize SessionStore (with COOKIE_ENCRYPTION_KEY from env)
6. Initialize InMemoryKeyStore (with a default free key for testing)
7. Initialize SlidingWindowLimiter
8. Initialize UsageTracker
9. Initialize region-tagged WorkerPool
10. Pre-warm all workers
11. Register all routes

Update shutdown to gracefully close all workers.

### 13. Admin Command (`asb_api/admin.py`)

A simple CLI tool to create/revoke API keys without touching the API:

```python
# python -m asb_api.admin create-key --tier starter --email user@example.com
# python -m asb_api.admin revoke-key key_abc123
# python -m asb_api.admin list-keys
```

### 14. Docker Image (`Dockerfile`)

Multi-stage build:

```dockerfile
# Stage 1: builder
FROM python:3.11-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
RUN playwright install chromium --with-deps

# Stage 2: runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY asb_api/ ./asb_api/
COPY config.yaml .
EXPOSE 8000
ENV PYTHONPATH=/app
CMD ["python", "-m", "asb_api"]
```

Also create `.dockerignore` to exclude `__pycache__`, `.git`, tests.

### 15. config.yaml update

Add these new config sections (some already exist, add missing ones):

```yaml
pool:
  workers_per_region:
    jp: 5
    us: 3
    eu: 2
  default_region: jp
  # keep other existing fields

security:
  cookie_encryption_key: "${COOKIE_ENCRYPTION_KEY}"

billing:
  enabled: false
```

Add `httpx` to `requirements.txt` for Decodo/BrightData API calls.

## Dont
- Do NOT use PostgreSQL or asyncpg (comes in Phase 2)
- Do NOT use Redis (comes in Phase 5)
- Do NOT implement Stripe billing (comes in Phase 3)
- Do NOT create the Next.js dashboard (comes in Phase 4)
- Do NOT commit or push to git
- Do NOT run the server

## Verification

After building, run:
```bash
cd /root/asb-cloud-api
python -c "
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.api.auth import InMemoryKeyStore
from asb_api.api.rate_limiter import SlidingWindowLimiter
from asb_api.session.store import SessionStore
from asb_api.api.usage import UsageTracker
print('All new imports OK')

cfg = load_config()
registry = ProviderRegistry()
registry.initialize_from_config(cfg.get('providers', {}))
print(f'Providers: {registry.list_providers()}')

# Test key creation
store = InMemoryKeyStore()
raw, key = store.create(tier='starter', owner_email='test@example.com')
print(f'Created key: {key.key_id}, prefix: {raw[:12]}...')
verified = store.verify(raw)
print(f'Verified: {verified}')

# Test rate limiter
import asyncio
limiter = SlidingWindowLimiter(10, 3600)
async def test():
    await limiter.check('test_key')
    print('Rate limiter OK')
asyncio.run(test())

print('Phase 1 verification complete')
"
```
