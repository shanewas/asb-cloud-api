import hashlib
import secrets
import time
from .connection import db


class PostgresKeyStore:
    async def create(self, tier: str = "free", owner_email: str | None = None) -> tuple[str, dict]:
        """Create a new API key. Returns (raw_key, key_info_dict) as per Phase 2 spec."""
        raw = f"sk_live_{secrets.token_hex(24)}"
        key_id = f"key_{secrets.token_hex(8)}"
        h = hashlib.sha256(raw.encode()).hexdigest()
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO api_keys (key_id, key_hash, tier, owner_email)
                   VALUES ($1, $2, $3, $4)""",
                key_id, h, tier, owner_email
            )
        finally:
            await db.pool.release(conn)
        return raw, {"key_id": key_id, "tier": tier, "owner_email": owner_email, "revoked": False}

    async def verify(self, raw: str) -> str | None:
        """Verify raw key. Returns key_id if valid, else None."""
        h = hashlib.sha256(raw.encode()).hexdigest()
        conn = await db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT key_id FROM api_keys
                   WHERE key_hash = $1 AND revoked = FALSE""",
                h
            )
            return row["key_id"] if row else None
        finally:
            await db.pool.release(conn)

    async def get(self, key_id: str) -> dict | None:
        conn = await db.pool.acquire()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM api_keys WHERE key_id = $1", key_id
            )
            if not row:
                return None
            return {
                "key_id": row["key_id"],
                "key_hash": row["key_hash"],
                "tier": row["tier"],
                "owner_email": row["owner_email"],
                "email": row["owner_email"],  # alias for old code
                "created_at": row["created_at"].timestamp() if row["created_at"] else time.time(),
                "revoked": row["revoked"],
            }
        finally:
            await db.pool.release(conn)

    async def revoke(self, key_id: str):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                "UPDATE api_keys SET revoked = TRUE WHERE key_id = $1",
                key_id
            )
        finally:
            await db.pool.release(conn)

    async def list_keys(self) -> list[dict]:
        conn = await db.pool.acquire()
        try:
            rows = await conn.fetch("SELECT * FROM api_keys ORDER BY created_at DESC")
            return [
                {
                    "key_id": r["key_id"],
                    "tier": r["tier"],
                    "email": r["owner_email"],
                    "revoked": r["revoked"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]
        finally:
            await db.pool.release(conn)
