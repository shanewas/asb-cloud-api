from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Literal
import os
import stripe as stripe_lib

from asb_api.billing import (
    TIER_TO_PRICE,
    SOLO_LICENSE_PRICE_ID,
    TEAM_LICENSE_PRICE_ID,
    ENTERPRISE_LICENSE_PRICE_ID,
)
from asb_api.api.auth import get_api_key


router = APIRouter()
stripe_lib.api_key = os.environ.get("STRIPE_SECRET_KEY")


class CheckoutRequest(BaseModel):
    tier: Literal["starter", "pro", "enterprise"]
    key_id: str
    email: str


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


@router.post("/v1/billing/checkout", response_model=CheckoutResponse)
async def create_checkout(
    request: CheckoutRequest,
    _: str = Depends(get_api_key),  # must be logged in
):
    price_id = TIER_TO_PRICE.get(request.tier)
    if not price_id:
        raise HTTPException(400, "Invalid tier")

    try:
        session = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=request.email,
            success_url="https://asbcloud.io/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://asbcloud.io/pricing",
            metadata={
                "key_id": request.key_id,
                "tier": request.tier,
            },
            allow_promotion_codes=True,
        )
        return CheckoutResponse(checkout_url=session.url, session_id=session.id)
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")


class LicenseCheckoutRequest(BaseModel):
    license_type: Literal["solo", "team", "enterprise"]
    email: str


@router.post("/v1/billing/license-checkout", response_model=CheckoutResponse)
async def create_license_checkout(request: LicenseCheckoutRequest):
    price_id = {
        "solo": SOLO_LICENSE_PRICE_ID,
        "team": TEAM_LICENSE_PRICE_ID,
        "enterprise": ENTERPRISE_LICENSE_PRICE_ID,
    }.get(request.license_type)
    if not price_id:
        raise HTTPException(400, "Invalid license type")

    try:
        session = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",  # one-time
            customer_email=request.email,
            success_url="https://asbcloud.io/success?license_type={LICENSE_TYPE}",
            cancel_url="https://asbcloud.io/self-hosted",
            metadata={"license_type": request.license_type},
        )
        return CheckoutResponse(checkout_url=session.url, session_id=session.id)
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")
