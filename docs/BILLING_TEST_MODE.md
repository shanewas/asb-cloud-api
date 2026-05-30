# Stripe Billing Test Mode

Use this runbook before enabling Stripe-backed billing routes in any shared environment.

## Required Configuration

Set these values from a Stripe test-mode account:

```bash
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_ENTERPRISE=price_...
STRIPE_LICENSE_SOLO=price_...
STRIPE_LICENSE_TEAM=price_...
STRIPE_LICENSE_ENTERPRISE=price_...
```

Then set `billing.enabled: true` in the config file used by the deployment.

Leave `billing.enabled: false` until checkout and webhook verification pass. With billing disabled, Stripe-backed routes are not mounted and the app can start without importing the Stripe SDK. The self-hosted license verification route remains mounted separately.

## Local Verification

Start the API with the test-mode environment loaded:

```bash
python -m asb_api
```

In another shell, forward Stripe events to the local webhook:

```bash
stripe listen --forward-to localhost:8000/v1/billing/webhook
```

Use the generated `whsec_...` value as `STRIPE_WEBHOOK_SECRET`, then restart the API.

Create a subscription checkout session:

```bash
curl -X POST http://localhost:8000/v1/billing/checkout ^
  -H "Authorization: Bearer sk_live_your_key" ^
  -H "Content-Type: application/json" ^
  -d "{\"tier\":\"starter\",\"key_id\":\"key_123\",\"email\":\"buyer@example.com\"}"
```

Create a self-hosted license checkout session:

```bash
curl -X POST http://localhost:8000/v1/billing/license-checkout ^
  -H "Content-Type: application/json" ^
  -d "{\"license_type\":\"solo\",\"email\":\"buyer@example.com\"}"
```

Complete each checkout with a Stripe test card, then confirm the forwarded webhook logs show `checkout.session.completed`.

## Webhook Events To Confirm

The webhook handler must accept signed Stripe events and apply these updates:

| Event | Expected effect |
| --- | --- |
| `checkout.session.completed` with `key_id` and `tier` metadata | Upgrade the API key tier and record the subscription ID. |
| `checkout.session.completed` with `license_type` metadata | Create a self-hosted license for the purchaser identity. |
| `customer.subscription.updated` | Update the stored subscription status. |
| `customer.subscription.deleted` | Downgrade the customer to the free tier. |
| `invoice.payment_failed` | Mark the subscription as `past_due`. |
| `invoice.paid` | Mark the subscription as `active`. |

Run the regression suite after changing billing code:

```bash
python -m compileall -q asb_api tests
python -m unittest discover -s tests -v
python -m pytest
```
