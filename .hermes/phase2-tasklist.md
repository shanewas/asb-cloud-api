# ASB Cloud API — Phase 2: Persistence

**Goal:** Everything persists. Real database, not in-memory.
**Working directory:** `/root/asb-cloud-api`
**Start from:** Phase 1 is done. Read existing code before modifying.

## Context

Read these existing files before starting:
- `SPEC.md` (full spec, Section 10.2 for DB schema)
- `asb_api/__main__.py` (current app entry — startup/shutdown)
- `asb_api/api/auth.py` (current InMemoryKeyStore)
- `asb_api/api/rate_limiter.py` (current SlidingWindowLimiter)
- `asb_api/session/store.py` (current SessionStore in-memory)
- `asb_api/api/usage.py` (current UsageTracker in-memory)
- `config.yaml` (current config — add db section)
- `requirements.txt`

Phase 1 already has: null/custom/decodo/brightdata providers, circuit breaker, in-memory API keys, in-memory rate limiter, in-memory sessions, in-memory usage tracking.
Phase 2 replaces: all in-memory stores → PostgreSQL, adds audit log + daily rollup.

---

## What to Build

### 1. PostgreSQL driver + connection pool (`asb_api/db.py`)

Use `asyncpg` for async PostgreSQL access.

```python
import asyncpg
import asyncio
from typing import Optional

class Database:
    def __init__(self, dsn: str, min_pool: int = 5, max_pool: int = 20):
        self.dsn = dsn
        self.min_pool = min_pool
        self.max_pool = max_pool
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_pool,
            max_size=self.max_pool,
            command_timeout=60,
        )

    async def disconnect(self):
        if self._pool:
            await self._pool.close()

    async def acquire(self):
        return await self._pool.acquire()

    async def release(self, conn):
        await self._pool.release(conn)

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected")
        return self._pool

# Singleton
db = Database(os.environ["DATABASE_URL"])

# Helper
async def run_migrations():
    """Create all tables if they don't exist."""
    conn = await db.pool.acquire()
    try:
        await conn.execute("""
            -- API Keys table
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id         VARCHAR(32) PRIMARY KEY,
                key_hash       TEXT NOT NULL,
                tier           VARCHAR(16) NOT NULL DEFAULT 'free',
                owner_email    TEXT,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked        BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE(key_hash)
            );

            -- Sessions table
            CREATE TABLE IF NOT EXISTS sessions (
                session_id     VARCHAR(64) PRIMARY KEY,
                key_id         VARCHAR(32) NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
                region         VARCHAR(8),
                fingerprint    TEXT,
                cookies_enc    BYTEA,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_used      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                request_count  INTEGER NOT NULL DEFAULT 0,
                expires_at     TIMESTAMPTZ NOT NULL,
                deleted_at     TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_key_id ON sessions(key_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at) WHERE deleted_at IS NULL;

            -- Usage records (raw, one per request)
            CREATE TABLE IF NOT EXISTS usage_records (
                id             BIGSERIAL PRIMARY KEY,
                key_id         VARCHAR(32) NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
                request_id     VARCHAR(32) NOT NULL,
                domain         VARCHAR(255),
                status         VARCHAR(16) NOT NULL,
                duration_ms    INTEGER NOT NULL,
                block_detected BOOLEAN NOT NULL DEFAULT FALSE,
                region         VARCHAR(8),
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_usage_key_date ON usage_records(key_id, DATE(created_at));

            -- Daily usage rollup (aggregated)
            CREATE TABLE IF NOT EXISTS daily_usage (
                key_id         VARCHAR(32) NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
                date           DATE NOT NULL,
                total_requests INTEGER NOT NULL DEFAULT 0,
                total_duration_ms BIGINT NOT NULL DEFAULT 0,
                block_count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (key_id, date)
            );

            -- Audit log
            CREATE TABLE IF NOT EXISTS audit_log (
                id             BIGSERIAL PRIMARY KEY,
                key_id         VARCHAR(32) REFERENCES api_keys(key_id) ON DELETE SET NULL,
                action         VARCHAR(32) NOT NULL,
                domain         VARCHAR(255),
                request_id     VARCHAR(32),
                status         VARCHAR(16),
                duration_ms    INTEGER,
                metadata       JSONB,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_key ON audit_log(key_id);
            CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_log(created_at);
        """)
    finally:
        await db.pool.release(conn)
```

### 2. PostgreSQL-backed API Key Store (`asb_api/db/auth_store.py`)

```python
import hashlib
import secrets
import time
from . import db

class PostgresKeyStore:
    async def create(self, tier: str = "free", owner_email: str | None = None) -> tuple[str, dict]:
        """Create a new API key. Returns (raw_key, key_info_dict)."""
        raw = f"sk_live_{secrets.token_hex(24)}"
        key_id = f"key_{secrets.token_hex(8)}"
        h = hashlib.sha256(raw.encode()).hexdigest()
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO api_keys (key_id, key_hash, tier, owner_email)
                   VALUES ($1, $2, $3, $4)""",
                key_id, h, tier, owner_email
            )
        finally:
            await db.db.pool.release(conn)
        return raw, {"key_id": key_id, "tier": tier, "owner_email": owner_email}

    async def verify(self, raw: str) -> str | None:
        """Verify raw key. Returns key_id if valid, else None."""
        h = hashlib.sha256(raw.encode()).hexdigest()
        conn = await db.db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT key_id FROM api_keys
                   WHERE key_hash = $1 AND revoked = FALSE""",
                h
            )
            return row["key_id"] if row else None
        finally:
            await db.db.pool.release(conn)

    async def get(self, key_id: str) -> dict | None:
        conn = await db.db.pool.acquire()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM api_keys WHERE key_id = $1", key_id
            )
            return dict(row) if row else None
        finally:
            await db.db.pool.release(conn)

    async def revoke(self, key_id: str):
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                "UPDATE api_keys SET revoked = TRUE WHERE key_id = $1",
                key_id
            )
        finally:
            await db.db.pool.release(conn)

    async def list_keys(self) -> list[dict]:
        conn = await db.db.pool.acquire()
        try:
            rows = await conn.fetch("SELECT * FROM api_keys ORDER BY created_at DESC")
            return [dict(r) for r in rows]
        finally:
            await db.db.pool.release(conn)
```

### 3. PostgreSQL-backed Rate Limiter (`asb_api/db/rate_limiter.py`)

Use PostgreSQL advisory locks for distributed-safe rate limiting.

```python
import hashlib
import time
import asyncio
from . import db

class PostgresRateLimiter:
    """Sliding window rate limiter using PostgreSQL advisory locks."""

    def __init__(self, limits_by_tier: dict[str, dict]):
        self.limits_by_tier = limits_by_tier

    def _lock_id(self, key_id: str) -> int:
        """Deterministic lock ID from key_id."""
        return int(hashlib.sha256(key_id.encode()).hexdigest()[:12], 16) % (1 << 31)

    async def check(self, key_id: str, tier: str) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, reset_at_unix). Raises RateLimitExceeded."""
        limits = self.limits_by_tier.get(tier, self.limits_by_tier["free"])
        max_requests = limits["requests"]
        window_seconds = limits["window_seconds"]

        lock_id = self._lock_id(key_id)
        now = time.time()
        reset_at = int(now + window_seconds)

        conn = await db.db.pool.acquire()
        try:
            # Use advisory lock to serialize requests per key
            async with conn.transaction():
                # Try to acquire advisory lock
                acquired = await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", lock_id
                )
                if not acquired:
                    # Another request is checking — wait briefly
                    await asyncio.sleep(0.05)
                    acquired = await conn.fetchval(
                        "SELECT pg_try_advisory_lock($1)", lock_id
                    )
                    if not acquired:
                        raise RateLimitExceeded(max_requests, 0, reset_at)

                try:
                    # Count requests in window
                    cutoff = now - window_seconds
                    count_row = await conn.fetchrow(
                        """SELECT COUNT(*) as cnt FROM usage_records
                           WHERE key_id = $1 AND created_at > $2""",
                        key_id, cutoff
                    )
                    count = count_row["cnt"]

                    if count >= max_requests:
                        # Find when the window resets
                        oldest_row = await conn.fetchrow(
                            """SELECT created_at FROM usage_records
                               WHERE key_id = $1 AND created_at > $2
                               ORDER BY created_at ASC LIMIT 1""",
                            key_id, cutoff
                        )
                        if oldest_row:
                            reset_at = int(oldest_row["created_at"].timestamp() + window_seconds)
                        raise RateLimitExceeded(max_requests, 0, reset_at)

                    return True, max_requests - count - 1, reset_at
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
        finally:
            await db.db.pool.release(conn)

class RateLimitExceeded(Exception):
    def __init__(self, limit: int, remaining: int, reset_at: int):
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        super().__init__(f"Rate limit exceeded: {limit} requests")
```

### 4. PostgreSQL-backed Session Store (`asb_api/db/session_store.py`)

```python
import uuid
import time
import asyncio
from cryptography.fernet import Fernet
from . import db

class PostgresSessionStore:
    def __init__(self, encryption_key: str | None, ttl_seconds: int = 300):
        self.encryption_key = encryption_key
        self.fernet = Fernet(encryption_key.encode()) if encryption_key else None
        self.ttl_seconds = ttl_seconds

    def _encrypt(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data) if self.fernet else data

    def _decrypt(self, data: bytes) -> bytes:
        return self.fernet.decrypt(data) if self.fernet else data

    async def create(self, key_id: str, region: str, fingerprint: str) -> dict:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        now = time.time()
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO sessions (session_id, key_id, region, fingerprint, expires_at)
                   VALUES ($1, $2, $3, $4, $5)""",
                session_id, key_id, region, fingerprint,
                time.time() + self.ttl_seconds
            )
        finally:
            await db.db.pool.release(conn)
        return {
            "session_id": session_id, "key_id": key_id, "region": region,
            "fingerprint": fingerprint, "cookies": {},
            "created_at": now, "last_used": now, "request_count": 0,
            "expires_at": now + self.ttl_seconds
        }

    async def get(self, session_id: str) -> dict | None:
        conn = await db.db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT * FROM sessions WHERE session_id = $1 AND deleted_at IS NULL
                   AND expires_at > $2""",
                session_id, time.time()
            )
            if not row:
                return None
            cookies = {}
            if row["cookies_enc"]:
                try:
                    cookies = json.loads(self._decrypt(row["cookies_enc"]).decode())
                except Exception:
                    cookies = {}
            return {
                **dict(row), "cookies": cookies,
                "last_used": row["last_used"].timestamp() if row["last_used"] else row["created_at"].timestamp(),
                "created_at": row["created_at"].timestamp(),
                "expires_at": row["expires_at"].timestamp(),
            }
        finally:
            await db.db.pool.release(conn)

    async def update_cookies(self, session_id: str, cookies: dict):
        enc = self._encrypt(json.dumps(cookies).encode())
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE sessions SET cookies_enc = $1, last_used = NOW()
                   WHERE session_id = $2""",
                enc, session_id
            )
        finally:
            await db.db.pool.release(conn)

    async def increment_count(self, session_id: str):
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE sessions SET request_count = request_count + 1, last_used = NOW()
                   WHERE session_id = $1""",
                session_id
            )
        finally:
            await db.db.pool.release(conn)

    async def delete(self, session_id: str):
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                "UPDATE sessions SET deleted_at = NOW() WHERE session_id = $1",
                session_id
            )
        finally:
            await db.db.pool.release(conn)
```

### 5. Usage Tracker with PostgreSQL (`asb_api/db/usage.py`)

```python
from datetime import datetime
from . import db

class PostgresUsageTracker:
    """Track every request in PostgreSQL. For rollup, see the rollup job."""

    async def record(
        self,
        key_id: str,
        request_id: str,
        domain: str | None,
        status: str,
        duration_ms: int,
        block_detected: bool,
        region: str | None,
    ):
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO usage_records
                   (key_id, request_id, domain, status, duration_ms, block_detected, region)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, request_id, domain, status, duration_ms, block_detected, region
            )
        finally:
            await db.db.pool.release(conn)

    async def get_daily_usage(self, key_id: str, date: str) -> dict:
        """date format: YYYY-MM-DD"""
        conn = await db.db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT total_requests, total_duration_ms, block_count
                   FROM daily_usage WHERE key_id = $1 AND date = $2""",
                key_id, date
            )
            if row:
                return dict(row)
            # Fallback: compute from raw
            raw = await conn.fetchrow(
                """SELECT COUNT(*) as cnt, COALESCE(SUM(duration_ms), 0) as dur,
                          COALESCE(SUM(CASE WHEN block_detected THEN 1 ELSE 0 END), 0) as blocks
                   FROM usage_records
                   WHERE key_id = $1 AND DATE(created_at) = $2""",
                key_id, date
            )
            return {
                "total_requests": raw["cnt"],
                "total_duration_ms": raw["dur"],
                "block_count": raw["blocks"],
            }
        finally:
            await db.db.pool.release(conn)

    async def rollup_daily(self, date: str):
        """Aggregate raw usage_records into daily_usage for a given date."""
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO daily_usage (key_id, date, total_requests, total_duration_ms, block_count)
                   SELECT key_id, DATE(created_at) as date,
                          COUNT(*) as total_requests,
                          COALESCE(SUM(duration_ms), 0) as total_duration_ms,
                          COALESCE(SUM(CASE WHEN block_detected THEN 1 ELSE 0 END), 0) as block_count
                   FROM usage_records
                   WHERE DATE(created_at) = $1
                   GROUP BY key_id, DATE(created_at)
                   ON CONFLICT (key_id, date) DO UPDATE SET
                       total_requests = EXCLUDED.total_requests,
                       total_duration_ms = EXCLUDED.total_duration_ms,
                       block_count = EXCLUDED.block_count""",
                date
            )
        finally:
            await db.db.pool.release(conn)
```

### 6. Audit Logger (`asb_api/db/audit.py`)

```python
from . import db
import json

class AuditLogger:
    async def log(
        self,
        key_id: str | None,
        action: str,
        domain: str | None = None,
        request_id: str | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        metadata: dict | None = None,
    ):
        conn = await db.db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO audit_log (key_id, action, domain, request_id, status, duration_ms, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, action, domain, request_id, status, duration_ms,
                json.dumps(metadata) if metadata else None
            )
        finally:
            await db.db.pool.release(conn)
```

### 7. Daily Rollup Cron Job (`asb_api/jobs/daily_rollup.py`)

```python
"""Daily rollup job — run at midnight UTC via cron."""
from datetime import datetime, timezone
from asb_api.db.usage import PostgresUsageTracker

async def run():
    tracker = PostgresUsageTracker()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    await tracker.rollup_daily(yesterday)

if __name__ == "__main__":
    import asyncio
    from datetime import timedelta
    asyncio.run(run())
```

### 8. DB module (`asb_api/db/__init__.py`)

```python
"""Database module — all PostgreSQL-backed stores."""
from . import db  # the Database singleton
from .auth_store import PostgresKeyStore
from .rate_limiter import PostgresRateLimiter, RateLimitExceeded
from .session_store import PostgresSessionStore
from .usage import PostgresUsageTracker
from .audit import AuditLogger
```

### 9. Updated `config.yaml` — add DB section

```yaml
database:
  dsn: "${DATABASE_URL}"
  min_pool: 5
  max_pool: 20

pool:
  # keep existing
  workers_per_region:
    jp: 5
    us: 3
    eu: 2
  default_region: jp
```

### 10. Updated `requirements.txt`

Add:
```
asyncpg>=0.30.0
psycopg2-binary>=2.9.10
```

### 11. Updated `__main__.py` — wire PostgreSQL

Update `startup()`:
```python
import os
from asb_api.db import db
from asb_api.db.auth_store import PostgresKeyStore
from asb_api.db.rate_limiter import PostgresRateLimiter
from asb_api.db.session_store import PostgresSessionStore
from asb_api.db.usage import PostgresUsageTracker
from asb_api.db.audit import AuditLogger

@app.on_event("startup")
async def startup():
    config = load_config()

    # Connect to PostgreSQL
    await db.connect()
    await run_migrations()  # from db.py

    # Wire up stores
    global key_store, rate_limiter, session_store, usage_tracker, audit_logger
    key_store = PostgresKeyStore()
    rate_limiter = PostgresRateLimiter(config["rate_limits"])
    session_store = PostgresSessionStore(
        encryption_key=os.environ.get("COOKIE_ENCRYPTION_KEY"),
        ttl_seconds=config.get("pool", {}).get("session_ttl_seconds", 300),
    )
    usage_tracker = PostgresUsageTracker()
    audit_logger = AuditLogger()

    # ... rest of startup (providers, pool, etc.)
```

Update `shutdown()`:
```python
@app.on_event("shutdown")
async def shutdown():
    await db.disconnect()
```

Also update `get_api_key` dependency in `auth.py` to use the global `key_store`:
```python
async def get_api_key(x_authorization: str = Header(None)) -> str:
    ...
    key_id = await key_store.verify(raw)  # now async
    ...
```

And update `rate_limiter.check` calls in routes to `await rate_limiter.check(key_id, tier)`.

Update `usage_tracker.increment(key_id)` calls to `await usage_tracker.record(...)` with full fields.

### 12. Admin command update (`asb_api/admin.py`)

Update to use `PostgresKeyStore` instead of `InMemoryKeyStore`:
```python
import asyncio
from asb_api.db import db
from asb_api.db.auth_store import PostgresKeyStore

async def main():
    await db.connect()
    store = PostgresKeyStore()
    # ... rest of CLI logic, all using await
    await db.disconnect()
```

---

## Dont
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
import asyncio
from asb_api.db import db
from asb_api.db.auth_store import PostgresKeyStore
from asb_api.db.rate_limiter import PostgresRateLimiter
from asb_api.db.session_store import PostgresSessionStore
from asb_api.db.usage import PostgresUsageTracker

async def test():
    print('All new imports OK')
    # Note: will fail at runtime without DATABASE_URL, but import must succeed
    try:
        await db.connect()
        print('DB connected OK')
        store = PostgresKeyStore()
        raw, info = await store.create(tier='starter', owner_email='test@example.com')
        print(f'Key created: {info[\"key_id\"]}')
        verified = await store.verify(raw)
        print(f'Key verified: {verified}')
        await db.disconnect()
    except Exception as e:
        print(f'DB not available (expected in dev): {e}')
    print('Phase 2 verification complete')

asyncio.run(test())
"
```
If all imports are OK and the DB error is a clean RuntimeError/connection error (not an import error), Phase 2 is complete.
