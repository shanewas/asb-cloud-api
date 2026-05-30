import uuid
import time
import json
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from .connection import db
from ..session.store import SessionInfo  # reuse the dataclass for return compatibility


class PostgresSessionStore:
    def __init__(self, encryption_key: str | None, ttl_seconds: int = 300):
        self.encryption_key = encryption_key
        self.fernet = Fernet(encryption_key.encode()) if encryption_key else None
        self.ttl_seconds = ttl_seconds

    def _encrypt(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data) if self.fernet else data

    def _decrypt(self, data: bytes) -> bytes:
        return self.fernet.decrypt(data) if self.fernet else data

    async def create(self, key_id: str, region: str, fingerprint: str | None = None) -> SessionInfo:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.timestamp()
        expires_at_dt = now_dt + timedelta(seconds=self.ttl_seconds)
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO sessions (session_id, key_id, region, fingerprint, expires_at)
                   VALUES ($1, $2, $3, $4, $5)""",
                session_id, key_id, region, fingerprint, expires_at_dt
            )
        finally:
            await db.pool.release(conn)
        return SessionInfo(
            session_id=session_id,
            key_id=key_id,
            region=region,
            fingerprint=fingerprint,
            cookies={},
            created_at=now,
            last_used=now,
            request_count=0,
            expires_at=expires_at_dt.timestamp(),
        )

    async def get(self, session_id: str) -> SessionInfo | None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.timestamp()
        conn = await db.pool.acquire()
        try:
            row = await conn.fetchrow(
                """SELECT * FROM sessions WHERE session_id = $1 AND deleted_at IS NULL
                   AND expires_at > $2""",
                session_id, now_dt
            )
            if not row:
                return None
            cookies = {}
            if row["cookies_enc"]:
                try:
                    cookies = json.loads(self._decrypt(row["cookies_enc"]).decode())
                except Exception:
                    cookies = {}
            # Update last_used on get (best effort, non-blocking for response)
            try:
                await conn.execute(
                    "UPDATE sessions SET last_used = NOW() WHERE session_id = $1",
                    session_id
                )
            except Exception:
                pass
            return SessionInfo(
                session_id=row["session_id"],
                key_id=row["key_id"],
                region=row["region"] or "jp",
                fingerprint=row["fingerprint"],
                cookies=cookies,
                created_at=row["created_at"].timestamp() if row["created_at"] else now,
                last_used=row["last_used"].timestamp() if row["last_used"] else now,
                request_count=row["request_count"] or 0,
                expires_at=row["expires_at"].timestamp() if row["expires_at"] else now + self.ttl_seconds,
            )
        finally:
            await db.pool.release(conn)

    async def update_cookies(self, session_id: str, cookies: dict):
        enc = self._encrypt(json.dumps(cookies or {}).encode())
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE sessions SET cookies_enc = $1, last_used = NOW()
                   WHERE session_id = $2""",
                enc, session_id
            )
        finally:
            await db.pool.release(conn)

    async def increment_count(self, session_id: str):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """UPDATE sessions SET request_count = request_count + 1, last_used = NOW()
                   WHERE session_id = $1""",
                session_id
            )
        finally:
            await db.pool.release(conn)

    async def delete(self, session_id: str):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                "UPDATE sessions SET deleted_at = NOW() WHERE session_id = $1",
                session_id
            )
        finally:
            await db.pool.release(conn)
