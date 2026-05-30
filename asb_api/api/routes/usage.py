from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from asb_api.api.auth import get_api_key, get_key_store


router = APIRouter()
usage_tracker: Any = None
limits_cfg: dict = {}


def set_usage_context(tracker: Any, rate_limits: dict):
    global usage_tracker, limits_cfg
    usage_tracker = tracker
    limits_cfg = rate_limits or {}


async def _get_tier(key_id: str) -> str:
    key_store = get_key_store()
    key = key_store.get(key_id)
    if hasattr(key, "__await__"):
        key = await key
    if isinstance(key, dict):
        return key.get("tier", "free")
    return getattr(key, "tier", "free")


@router.get("/v1/usage")
async def get_usage(key_id: str = Depends(get_api_key)):
    if usage_tracker is None:
        raise HTTPException(503, "Usage tracker not initialized")
    tier = await _get_tier(key_id)
    return await usage_tracker.get_usage_info(key_id, tier, limits_cfg)
