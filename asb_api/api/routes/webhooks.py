from fastapi import APIRouter, Request, HTTPException
import stripe
import os
import secrets

from asb_api.billing import TIER_TO_PRICE
from asb_api.db.auth_store import PostgresKeyStore

router = APIRouter()
webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
key_store: PostgresKeyStore | None = None


def set_store(store: PostgresKeyStore):
    global key_store
    key_store = store


@router.post("/v1/billing/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not webhook_secret:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")
    except Exception:
        raise HTTPException(400, "Invalid payload")

    if key_store is None:
        # In test or misconfig, accept but do nothing
        return {"received": True}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        key_id = session["metadata"].get("key_id")
        tier = session["metadata"].get("tier")
        license_type = session["metadata"].get("license_type")

        if key_id and tier:
            # Subscription upgrade
            await key_store.upgrade_tier(key_id, tier, session.get("subscription"))
        elif license_type:
            # Self-hosted license purchase
            raw_license = f"sk_license_{secrets.token_hex(24)}"
            ident = key_id or session.get("customer_email") or session.get("customer")
            if ident:
                await key_store.add_license(ident, license_type, raw_license)

    elif event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        await key_store.update_subscription_status(customer_id, status)

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        await key_store.downgrade_to_free(customer_id)

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]
        await key_store.update_subscription_status(customer_id, "past_due")

    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]
        await key_store.update_subscription_status(customer_id, "active")

    return {"received": True}
