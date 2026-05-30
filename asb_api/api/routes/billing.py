import inspect
from typing import Any

from fastapi import APIRouter, HTTPException, Depends

from asb_api.api.auth import get_api_key, get_key_store
from asb_api.billing.stripe_client import get_stripe


router = APIRouter()


async def _get_key_record(key_store: Any, key_id: str) -> Any:
    get = getattr(key_store, "get", None)
    if not callable(get):
        return None
    result = get(key_id)
    if inspect.isawaitable(result):
        result = await result
    return result


def _stripe_customer_id(key: Any) -> str | None:
    if isinstance(key, dict):
        return key.get("stripe_customer_id")
    return getattr(key, "stripe_customer_id", None)


@router.get("/v1/billing/portal")
async def customer_portal(key_id: str = Depends(get_api_key)):
    key_store = get_key_store()
    key = await _get_key_record(key_store, key_id)
    customer_id = _stripe_customer_id(key)
    if not customer_id:
        raise HTTPException(400, "No Stripe customer found")

    try:
        stripe_lib = get_stripe()
        session = stripe_lib.billing_portal.Session.create(
            customer=customer_id,
            return_url="https://asbcloud.io/dashboard"
        )
        return {"portal_url": session.url}
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")


@router.get("/v1/billing/invoices")
async def list_invoices(key_id: str = Depends(get_api_key)):
    key_store = get_key_store()
    key = await _get_key_record(key_store, key_id)
    customer_id = _stripe_customer_id(key)
    if not customer_id:
        raise HTTPException(400, "No Stripe customer found")

    try:
        stripe_lib = get_stripe()
        invoices = stripe_lib.Invoice.list(customer=customer_id, limit=10)
        return {
            "invoices": [
                {
                    "id": inv.id,
                    "amount_paid": inv.amount_paid,
                    "currency": inv.currency,
                    "status": inv.status,
                    "created": inv.created,
                    "invoice_pdf": inv.invoice_pdf,
                }
                for inv in invoices.data
            ]
        }
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")
