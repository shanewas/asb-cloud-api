import uuid
import time
import asyncio
from dataclasses import dataclass, field

from cryptography.fernet import Fernet


@dataclass
class SessionInfo:
    session_id: str
    key_id: str
    region: str
    fingerprint: str | None = None
    cookies: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    request_count: int = 0
    expires_at: float = 0


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

    async def create(self, key_id: str, region: str, fingerprint: str | None = None) -> SessionInfo:
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
