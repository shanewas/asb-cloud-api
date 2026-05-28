from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os

from asb_api.billing.license import SelfHostedLicense


router = APIRouter()


class VerifyLicenseRequest(BaseModel):
    license_key: str
    license_type: str
    domain: str


@router.post("/v1/billing/verify-license")
async def verify_license(request: VerifyLicenseRequest):
    secret = os.environ.get("LICENSE_SECRET_KEY", "")
    if not secret:
        raise HTTPException(500, "LICENSE_SECRET_KEY not configured")
    lic = SelfHostedLicense(secret)
    valid, error = lic.verify_full(request.license_key, request.license_type, request.domain)
    if not valid:
        raise HTTPException(400, error)
    return {"valid": True}
