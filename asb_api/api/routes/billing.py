from fastapi import APIRouter, HTTPException, Depends
import os
import stripe as stripe_lib

from asb_api.api.auth import get_api_key, get_key_store


router = APIRouter()
stripe_lib.api_key = os.environ.get("STRIPE_SECRET_KEY")


@router.get("/v1/billing/portal")
async def customer_portal(key_id: str = Depends(get_api_key)):
    key_store = get_key_store()
    key = await key_store.get(key_id) if hasattr(key_store, "get") and callable(getattr(key_store, "get")) else None
    # Support sync get for InMemory fallback (though billing requires Stripe)
    if key is None:
        # try sync
        try:
            key = key_store.get(key_id)
        except Exception:
            key = None
    if not key or not key.get("stripe_customer_id"):
        raise HTTPException(400, "No Stripe customer found")

    try:
        session = stripe_lib.billing_portal.Session.create(
            customer=key["stripe_customer_id"],
            return_url="https://asbcloud.io/dashboard"
        )
        return {"portal_url": session.url}
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")


@router.get("/v1/billing/invoices")
async def list_invoices(key_id: str = Depends(get_api_key)):
    key_store = get_key_store()
    key = await key_store.get(key_id) if hasattr(key_store, "get") and callable(getattr(key_store, "get")) else None
    if key is None:
        try:
            key = key_store.get(key_id)
        except Exception:
            key = None
    if not key or not key.get("stripe_customer_id"):
        raise HTTPException(400, "No Stripe customer found")

    try:
        invoices = stripe_lib.Invoice.list(customer=key["stripe_customer_id"], limit=10)
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
