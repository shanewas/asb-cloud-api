import hashlib
import secrets
import time
import inspect
from dataclasses import dataclass, field
from typing import Literal, Any
from fastapi import Header, HTTPException
from asb_api.api.errors import APIError


@dataclass
class APIKey:
    key_id: str
    key_hash: str
    tier: Literal["free", "starter", "pro", "enterprise"] = "free"
    owner_email: str | None = None
    created_at: float = field(default_factory=time.time)
    revoked: bool = False


class InMemoryKeyStore:
    def __init__(self):
        self._keys: dict[str, dict] = {}

    def create(self, tier: str = "free", owner_email: str | None = None) -> tuple[str, APIKey]:
        raw = f"sk_live_{secrets.token_hex(24)}"
        key_id = f"key_{secrets.token_hex(8)}"
        h = hashlib.sha256(raw.encode()).hexdigest()
        self._keys[key_id] = {
            "hash": h,
            "tier": tier,
            "email": owner_email,
            "revoked": False,
            "created_at": time.time(),
        }
        api_key = APIKey(
            key_id=key_id,
            key_hash=h,
            tier=tier,
            owner_email=owner_email,
            created_at=self._keys[key_id]["created_at"],
        )
        return raw, api_key

    def verify(self, raw: str) -> str | None:
        h = hashlib.sha256(raw.encode()).hexdigest()
        for kid, info in self._keys.items():
            if not info["revoked"] and secrets.compare_digest(h, info["hash"]):
                return kid
        return None

    def get(self, key_id: str) -> APIKey | None:
        info = self._keys.get(key_id)
        if not info:
            return None
        return APIKey(
            key_id=key_id,
            key_hash=info["hash"],
            tier=info["tier"],
            owner_email=info.get("email"),
            created_at=info["created_at"],
            revoked=info["revoked"],
        )

    def revoke(self, key_id: str):
        if key_id in self._keys:
            self._keys[key_id]["revoked"] = True

    def list_keys(self) -> list[dict]:
        return [
            {"key_id": kid, "tier": info["tier"], "email": info.get("email"), "revoked": info["revoked"]}
            for kid, info in self._keys.items()
        ]


_key_store: Any = None


def set_key_store(store: Any):
    global _key_store
    _key_store = store


def get_key_store() -> Any:
    global _key_store
    if _key_store is None:
        _key_store = InMemoryKeyStore()
    return _key_store


async def get_api_key(authorization: str = Header(None)) -> str:
    if not authorization:
        raise APIError(403, "MISSING_AUTH", "Authorization header missing")
    raw = authorization.removeprefix("Bearer ").strip()
    store = get_key_store()
    # Support both sync (InMemoryKeyStore) and async (PostgresKeyStore)
    verify = getattr(store, "verify", None)
    if verify and inspect.iscoroutinefunction(verify):
        key_id = await verify(raw)
    else:
        key_id = verify(raw) if verify else None
    if not key_id:
        raise APIError(403, "INVALID_API_KEY", "API key is invalid or revoked")
    return key_id
