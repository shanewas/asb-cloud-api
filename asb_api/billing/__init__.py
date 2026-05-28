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
