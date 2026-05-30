import uuid
import time
import asyncio
import json

from cryptography.fernet import Fernet

from asb_api.session.models import SessionInfo


class SessionStore:
    def __init__(self, encryption_key: str | None = None, ttl_seconds: int = 300):
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

    def _store_cookies(self, cookies: dict) -> dict | str:
        if not self.fernet:
            return dict(cookies or {})
        encoded = json.dumps(cookies or {}, separators=(",", ":")).encode()
        return self._encrypt(encoded).decode()

    def _load_cookies(self, stored: dict | str | None) -> dict:
        if stored is None:
            return {}
        if not self.fernet:
            return dict(stored) if isinstance(stored, dict) else {}
        if isinstance(stored, str):
            return json.loads(self._decrypt(stored.encode()).decode())
        return dict(stored)

    def _session_info_from_record(self, record: dict) -> SessionInfo:
        data = dict(record)
        data["cookies"] = self._load_cookies(data.get("cookies"))
        return SessionInfo(**data)

    async def create(self, key_id: str, region: str, fingerprint: str | None = None) -> SessionInfo:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        now = time.time()
        session = {
            "session_id": session_id,
            "key_id": key_id,
            "region": region,
            "fingerprint": fingerprint,
            "cookies": self._store_cookies({}),
            "created_at": now,
            "last_used": now,
            "request_count": 0,
            "expires_at": now + self.ttl_seconds,
        }
        async with self._lock:
            self._sessions[session_id] = session
        return self._session_info_from_record(session)

    async def get(self, session_id: str) -> SessionInfo | None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            if time.time() > s["expires_at"]:
                del self._sessions[session_id]
                return None
            s["last_used"] = time.time()
            return self._session_info_from_record(s)

    async def update_cookies(self, session_id: str, cookies: dict):
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["cookies"] = self._store_cookies(cookies)
                self._sessions[session_id]["last_used"] = time.time()

    async def increment_count(self, session_id: str):
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["request_count"] += 1
                self._sessions[session_id]["last_used"] = time.time()

    async def delete(self, session_id: str):
        async with self._lock:
            self._sessions.pop(session_id, None)
