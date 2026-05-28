from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from typing import Optional, Any
from asb_api.api.auth import get_api_key
from asb_api.session.store import SessionStore

router = APIRouter()
session_store: Any = None


def set_session_store(store: Any):
    global session_store
    session_store = store


class CreateSessionRequest(BaseModel):
    region: str = "jp"
    fingerprint: Optional[str] = None


@router.post("/v1/sessions")
async def create_session(
    request: CreateSessionRequest,
    key_id: str = Depends(get_api_key),
):
    if not session_store:
        raise HTTPException(503, "Session store not initialized")
    session = await session_store.create(
        key_id=key_id,
        region=request.region,
        fingerprint=request.fingerprint,
    )
    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


@router.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, key_id: str = Depends(get_api_key)):
    if not session_store:
        raise HTTPException(503, "Session store not initialized")
    session = await session_store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": session.session_id,
        "region": session.region,
        "fingerprint": session.fingerprint,
        "request_count": session.request_count,
        "created_at": session.created_at,
        "last_used": session.last_used,
        "expires_at": session.expires_at,
    }


@router.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, key_id: str = Depends(get_api_key)):
    if not session_store:
        raise HTTPException(503, "Session store not initialized")
    await session_store.delete(session_id)
    return Response(status_code=204)
