import os
import asyncpg
from typing import Optional


class Database:
    def __init__(self, dsn: str | None = None, min_pool: int = 5, max_pool: int = 20):
        self.dsn = dsn or os.environ.get("DATABASE_URL", "")
        self.min_pool = min_pool
        self.max_pool = max_pool
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if not self.dsn:
            raise RuntimeError("DATABASE_URL not set")
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_pool,
            max_size=self.max_pool,
            command_timeout=60,
        )

    async def disconnect(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def acquire(self):
        if not self._pool:
            raise RuntimeError("Database not connected")
        return await self._pool.acquire()

    async def release(self, conn):
        if self._pool:
            await self._pool.release(conn)

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected")
        return self._pool


# Singleton instance (call connect() during app startup)
db = Database()


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
