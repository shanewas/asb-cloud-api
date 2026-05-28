# ASB Cloud API — Phase 3: Billing

**Goal:** Get money. Stripe integration, first revenue.
**Working directory:** `/root/asb-cloud-api`
**Start from:** Phase 2 is done (PostgreSQL persistence). Read existing code before modifying.

## Context

Read these existing files before starting:
- `SPEC.md` (full spec, Section 10.3 for pricing details)
- `asb_api/__main__.py` (app entry — where to wire Stripe)
- `asb_api/db/auth_store.py` (PostgresKeyStore — where to add tier fields)
- `asb_api/config.py` (current config loader)
- `config.yaml` (current config)
- `requirements.txt`

Phase 2 already has: PostgreSQL persistence for all stores.
Phase 3 adds: Stripe SDK, checkout sessions, webhook handlers, tier enforcement, self-hosted license keys.

---

## What to Build

### 1. Stripe SDK + config (`asb_api/billing.py`)

Add `stripe` to `requirements.txt`.

```python
import stripe
import os
from typing import Literal

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Price IDs from Stripe dashboard (set via env for dev)
FREE_PRICE_ID = os.environ.get("STRIPE_PRICE_STARTER", "")  # $49/mo
PRO_PRICE_ID = os.environ.get("STRIPE_PRICE_PRO", "")       # $149/mo
ENTERPRISE_PRICE_ID = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")  # $499/mo

# Self-hosted license prices (one-time)
SOLO_LICENSE_PRICE_ID = os.environ.get("STRIPE_LICENSE_SOLO", "")   # $199
TEAM_LICENSE_PRICE_ID = os.environ.get("STRIPE_LICENSE_TEAM", "")   # $499
ENTERPRISE_LICENSE_PRICE_ID = os.environ.get("STRIPE_LICENSE_ENTERPRISE", "")  # $999

TIER_TO_PRICE: dict[str, str] = {
    "starter": FREE_PRICE_ID,
    "pro": PRO_PRICE_ID,
    "enterprise": ENTERPRISE_PRICE_ID,
}

TIER_MONTHLY_REQUESTS: dict[str, int] = {
    "free": 500,
    "starter": 25_000,
    "pro": 200_000,
    "enterprise": 9_999_999_999,
}

TIER_OVERAGE_RATE: dict[str, float] = {
    "free": 0,
    "starter": 0.002,   # $2 per 1K overage
    "pro": 0.001,        # $1 per 1K overage
    "enterprise": 0,
}
```

### 2. Stripe-backed Key Store update (`asb_api/db/auth_store.py`)

Update `PostgresKeyStore` to store Stripe customer info:

```python
# Add these columns to api_keys table migration (update run_migrations):
# stripe_customer_id TEXT
# stripe_subscription_id TEXT
# subscription_status TEXT  -- active, trialing, past_due, canceled, None
# license_type TEXT  -- null, solo, team, enterprise
# license_key TEXT  -- for self-hosted

# Add to create():
async def create(self, tier: str = "free", owner_email: str | None = None,
                 stripe_customer_id: str | None = None) -> tuple[str, dict]:
    ...
    await conn.execute("""INSERT INTO api_keys (key_id, key_hash, tier, owner_email, stripe_customer_id)
       VALUES ($1, $2, $3, $4, $5)""",
       key_id, h, tier, owner_email, stripe_customer_id)
    ...

# Add upgrade_tier() and get_tier():
async def upgrade_tier(self, key_id: str, tier: str, stripe_subscription_id: str | None = None):
    await conn.execute(
        """UPDATE api_keys SET tier = $1, stripe_subscription_id = $2, subscription_status = 'active'
           WHERE key_id = $3""",
        tier, stripe_subscription_id, key_id
    )

async def get_tier(self, key_id: str) -> str:
    row = await conn.fetchrow("SELECT tier FROM api_keys WHERE key_id = $1", key_id)
    return row["tier"] if row else "free"
```

### 3. Update migrations (`asb_api/db/connection.py`)

Add to `run_migrations()`:
```python
await conn.execute("""
    ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
    ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
    ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS subscription_status TEXT;
    ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS license_type TEXT;
    ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS license_key TEXT;
""")
```

### 4. Checkout Routes (`asb_api/api/routes/checkout.py`)

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import stripe as stripe_lib
from asb_api.billing import TIER_TO_PRICE, SOLO_LICENSE_PRICE_ID, TEAM_LICENSE_PRICE_ID, ENTERPRISE_LICENSE_PRICE_ID

router = APIRouter()

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
```

### 5. Webhook Handler (`asb_api/api/routes/webhooks.py`)

```python
from fastapi import APIRouter, Request, HTTPException
import stripe
import os
from asb_api.billing import TIER_TO_PRICE
from asb_api.db.auth_store import PostgresKeyStore

router = APIRouter()
webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
key_store = PostgresKeyStore()  # will be overridden by set_store() in __main__

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
            await key_store.add_license(key_id or session["customer_email"], license_type, raw_license)

    elif event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        # Find key by stripe_customer_id and update status
        await key_store.update_subscription_status(customer_id, status)

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        # Downgrade to free
        await key_store.downgrade_to_free(customer_id)

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]
        await key_store.update_subscription_status(customer_id, "past_due")

    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]
        # Overage payment received — activate overage
        await key_store.update_subscription_status(customer_id, "active")

    return {"received": True}
```

Add `add_license`, `update_subscription_status`, `downgrade_to_free` to `PostgresKeyStore`.

### 6. Customer Portal Route (`asb_api/api/routes/billing.py`)

```python
@router.get("/v1/billing/portal")
async def customer_portal(key_id: str = Depends(get_api_key)):
    key = await key_store.get(key_id)
    if not key.get("stripe_customer_id"):
        raise HTTPException(400, "No Stripe customer found")

    try:
        session = stripe_lib.billing_portal.Session.create(
            customer=key["stripe_customer_id"],
            return_url="https://asbcloud.io/dashboard"
        )
        return {"portal_url": session.url}
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")
```

### 7. Invoice History Route (`asb_api/api/routes/billing.py`)

```python
@router.get("/v1/billing/invoices")
async def list_invoices(key_id: str = Depends(get_api_key)):
    key = await key_store.get(key_id)
    if not key.get("stripe_customer_id"):
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
```

### 8. Overage billing (`asb_api/db/usage.py`)

Add to `PostgresUsageTracker`:
```python
async def check_overage(self, key_id: str, tier: str) -> tuple[bool, int, float]:
    """Returns (is_overage, overage_requests, overage_cost_usd)."""
    from asb_api.billing import TIER_MONTHLY_REQUESTS, TIER_OVERAGE_RATE
    limit = TIER_MONTHLY_REQUESTS.get(tier, 0)
    if tier == "free":
        return False, 0, 0.0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    usage = await self.get_daily_usage(key_id, today)
    total = usage.get("total_requests", 0)

    if total <= limit:
        return False, 0, 0.0

    overage = total - limit
    rate = TIER_OVERAGE_RATE.get(tier, 0)
    cost = overage * rate
    return True, overage, cost
```

### 9. Updated rate limiter to check overage (`asb_api/db/rate_limiter.py`)

The rate limiter already exists. Update `check()` to also charge overage if applicable:
- On `check()`, also call `usage_tracker.check_overage()` 
- If overage is `True`, return a different error `OverageLimitExceeded` (HTTP 402 Payment Required)

```python
class OverageLimitExceeded(HTTPException):
    def __init__(self, overage_cost_usd: float):
        super().__init__(
            status_code=402,
            detail={
                "error_code": "OVERAGE_LIMIT_EXCEEDED",
                "message": "Usage limit exceeded. Upgrade or pay overage.",
                "overage_cost_usd": overage_cost_usd,
            }
        )
```

### 10. Updated `__main__.py`

Wire in:
```python
from asb_api.api.routes import checkout, webhooks, billing
app.include_router(checkout.router)
app.include_router(webhooks.router)
app.include_router(billing.router)
# set the webhook store
from asb_api.api.routes.webhooks import set_store
set_store(key_store)
```

Add Stripe config to config.yaml:
```yaml
stripe:
  secret_key: "${STRIPE_SECRET_KEY}"
  webhook_secret: "${STRIPE_WEBHOOK_SECRET}"
  price_starter: "${STRIPE_PRICE_STARTER}"
  price_pro: "${STRIPE_PRICE_PRO}"
  price_enterprise: "${STRIPE_PRICE_ENTERPRISE}"
  license_solo: "${STRIPE_LICENSE_SOLO}"
  license_team: "${STRIPE_LICENSE_TEAM}"
  license_enterprise: "${STRIPE_LICENSE_ENTERPRISE}"
```

Add to `requirements.txt`:
```
stripe>=8.0.0
```

### 11. Self-hosted license key verification middleware

A lightweight HMAC-based license key system for self-hosted (no Stripe needed):

```python
# asb_api/billing/license.py
import hmac
import hashlib
import os
import secrets

class SelfHostedLicense:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()

    def generate(self, license_type: str, domain: str, expiry_ts: int) -> str:
        """Generate a license key for a self-hosted deployment."""
        payload = f"{license_type}:{domain}:{expiry_ts}"
        sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"sk_license_{secrets.token_hex(8)}_{sig}"

    def verify(self, license_key: str, license_type: str, domain: str, expiry_ts: int) -> bool:
        """Verify a license key."""
        if not license_key.startswith("sk_license_"):
            return False
        payload = f"{license_type}:{domain}:{expiry_ts}"
        expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        provided_sig = license_key.split("_")[-1]
        return hmac.compare_digest(expected_sig, provided_sig)

    def verify_full(self, license_key: str, license_type: str, domain: str) -> tuple[bool, str]:
        """Verify and return (valid, error_message)."""
        import time
        if not license_key.startswith("sk_license_"):
            return False, "Invalid license key format"
        parts = license_key.split("_")
        if len(parts) < 3:
            return False, "Malformed license key"
        # Expiry encoded in key: last segment before sig is expiry_ts
        try:
            expiry_ts = int(parts[2])
            if time.time() > expiry_ts:
                return False, "License key expired"
        except ValueError:
            return False, "Invalid expiry in license key"
        payload = f"{license_type}:{domain}:{expiry_ts}"
        expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        provided_sig = parts[-1]
        if not hmac.compare_digest(expected_sig, provided_sig):
            return False, "Invalid license signature"
        return True, ""
```

Add to config.yaml:
```yaml
self_hosted:
  enabled: true
  license_key_required: true
  license_secret_key: "${LICENSE_SECRET_KEY}"
```

### 12. License verification route

```python
# asb_api/api/routes/licenses.py
@router.post("/v1/billing/verify-license")
async def verify_license(request: VerifyLicenseRequest):
    lic = SelfHostedLicense(os.environ["LICENSE_SECRET_KEY"])
    valid, error = lic.verify_full(request.license_key, request.license_type, request.domain)
    if not valid:
        raise HTTPException(400, error)
    return {"valid": True}
```

## Dont
- Do NOT implement the Next.js dashboard (comes in Phase 4)
- Do NOT add Redis or caching (comes in Phase 5)
- Do NOT commit or push to git
- Do NOT run the server

## Verification

After building, run:
```bash
cd /root/asb-cloud-api
python -c "
from asb_api.billing import (
    TIER_OVERAGE_RATE, TIER_MONTHLY_REQUESTS,
    TIER_TO_PRICE, SOLO_LICENSE_PRICE_ID, TEAM_LICENSE_PRICE_ID
)
from asb_api.db.auth_store import PostgresKeyStore
from asb_api.billing.license import SelfHostedLicense
from asb_api.api.routes import checkout, webhooks, billing

print('All Phase 3 imports OK')
print(f'Tiers: {list(TIER_TO_PRICE.keys())}')
print(f'Overage rates: {TIER_OVERAGE_RATE}')
print(f'Monthly requests: {TIER_MONTHLY_REQUESTS}')

lic = SelfHostedLicense('test-secret-key')
key = lic.generate('solo', 'myapp.com', 9999999999)
valid, _ = lic.verify_full(key, 'solo', 'myapp.com')
print(f'License generation/verification: {\"PASS\" if valid else \"FAIL\"}')

print('Phase 3 verification complete')
"
```
