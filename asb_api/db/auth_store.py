import hashlib
import secrets
import time
from .connection import db


class PostgresKeyStore:
    async def create(self, tier: str = "free", owner_email: str | None = None,
                 stripe_customer_id: str | None = None) -> tuple[str, dict]:
        """Create a new API key. Returns (raw_key, key_info_dict) as per Phase 2 spec."""
        raw = f"sk_live_{secrets.token_hex(24)}"
        key_id = f"key_{secrets.token_hex(8)}"
        h = hashlib.sha256(raw.encode()).hexdigest()
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO api_keys (key_id, key_hash, tier, owner_email, stripe_customer_id)
                   VALUES ($1, $2, $3, $4, $5)""",
                key_id, h, tier, owner_email, stripe_customer_id
            )
        finally:
            await db.pool.release(conn)
        return raw, {
            "key_id": key_id,
            "tier": tier,
            "owner_email": owner_email,
            "revoked": False,
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": None,
            "subscription_status": None,
            "license_type": None,
            "license_key": None,
        }

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
                "stripe_customer_id": row.get("stripe_customer_id"),
                "stripe_subscription_id": row.get("stripe_subscription_id"),
                "subscription_status": row.get("subscription_status"),
                "license_type": row.get("license_type"),
                "license_key": row.get("license_key"),
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
                    "stripe_customer_id": r.get("stripe_customer_id"),
                    "subscription_status": r.get("subscription_status"),
                    "license_type": r.get("license_type"),
                }
                for r in rows
            ]
        finally:
            await db.pool.release(conn)

    async def upgrade_tier(self, key_id: str, tier: str, stripe_subscription_id: str | None = None):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE api_keys SET tier = $1, stripe_subscription_id = $2, subscription_status = 'active'
                   WHERE key_id = $3""",
                tier, stripe_subscription_id, key_id
            )
        finally:
            await db.pool.release(conn)

    async def get_tier(self, key_id: str) -> str:
        conn = await db.pool.acquire()
        try:
            row = await conn.fetchrow("SELECT tier FROM api_keys WHERE key_id = $1", key_id)
            return row["tier"] if row else "free"
        finally:
            await db.pool.release(conn)

    async def add_license(self, key_id_or_email: str, license_type: str, raw_license: str):
        """Associate a self-hosted license. key_id_or_email can be key_id or owner_email."""
        conn = await db.pool.acquire()
        try:
            if key_id_or_email.startswith("key_"):
                await conn.execute(
                    """UPDATE api_keys SET license_type = $1, license_key = $2
                       WHERE key_id = $3""",
                    license_type, raw_license, key_id_or_email
                )
            else:
                # by email (may affect one or more; take first non-revoked for simplicity)
                await conn.execute(
                    """UPDATE api_keys SET license_type = $1, license_key = $2
                       WHERE key_id = (
                           SELECT key_id FROM api_keys
                           WHERE owner_email = $3 AND revoked = FALSE
                           ORDER BY created_at DESC LIMIT 1
                       )""",
                    license_type, raw_license, key_id_or_email
                )
        finally:
            await db.pool.release(conn)

    async def update_subscription_status(self, customer_id: str, status: str):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE api_keys SET subscription_status = $1
                   WHERE stripe_customer_id = $2""",
                status, customer_id
            )
        finally:
            await db.pool.release(conn)

    async def downgrade_to_free(self, customer_id: str):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE api_keys SET tier = 'free', subscription_status = 'canceled', stripe_subscription_id = NULL
                   WHERE stripe_customer_id = $1""",
                customer_id
            )
        finally:
            await db.pool.release(conn)
