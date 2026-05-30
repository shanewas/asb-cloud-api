from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os

from asb_api.billing.license import SelfHostedLicense
from asb_api.api.errors import APIError


router = APIRouter()


class VerifyLicenseRequest(BaseModel):
    license_key: str
    license_type: str
    domain: str


@router.post("/v1/billing/verify-license")
async def verify_license(request: VerifyLicenseRequest):
    secret = os.environ.get("LICENSE_SECRET_KEY", "")
    if not secret:
        raise APIError(500, "INTERNAL_ERROR", "LICENSE_SECRET_KEY not configured")
    lic = SelfHostedLicense(secret)
    valid, error = lic.verify_full(request.license_key, request.license_type, request.domain)
    if not valid:
        raise APIError(400, "BAD_REQUEST", error or "License verification failed")
    return {"valid": True}
