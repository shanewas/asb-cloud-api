import importlib
import os
import sys
import types
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from asb_api import billing
from asb_api.api.auth import set_key_store
from asb_api.api.routes import webhooks as webhook_routes
from asb_api.api.routes.billing import customer_portal, list_invoices
from asb_api.api.routes.checkout import (
    CheckoutRequest,
    LicenseCheckoutRequest,
    create_checkout,
    create_license_checkout,
)
from asb_api.api.routes.webhooks import set_store, stripe_webhook


_MISSING = object()
_ENV_KEYS = [
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_STARTER",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_ENTERPRISE",
    "STRIPE_LICENSE_SOLO",
    "STRIPE_LICENSE_TEAM",
    "STRIPE_LICENSE_ENTERPRISE",
]


class FakeRequest:
    def __init__(self, payload=b"{}", signature="sig_test"):
        self._payload = payload
        self.headers = {"stripe-signature": signature}

    async def body(self):
        return self._payload


class FakeKeyStore:
    def __init__(self):
        self.calls = []

    async def upgrade_tier(self, key_id, tier, subscription_id, customer_id=None):
        self.calls.append(("upgrade_tier", key_id, tier, subscription_id, customer_id))

    async def add_license(self, ident, license_type, raw_license):
        self.calls.append(("add_license", ident, license_type, raw_license))

    async def update_subscription_status(self, customer_id, status):
        self.calls.append(("update_subscription_status", customer_id, status))

    async def downgrade_to_free(self, customer_id):
        self.calls.append(("downgrade_to_free", customer_id))


class SyncBillingStore:
    def get(self, key_id):
        return {"key_id": key_id, "stripe_customer_id": "cus_123"}


def install_fake_stripe():
    fake = types.ModuleType("stripe")
    fake.api_key = None
    fake.checkout_calls = []
    fake.portal_calls = []
    fake.invoice_calls = []
    fake.event = {"type": "noop", "data": {"object": {}}}
    fake.construct_error = None
    fake.webhook_calls = []

    class SignatureVerificationError(Exception):
        pass

    class FakeCheckoutSession:
        @staticmethod
        def create(**kwargs):
            fake.checkout_calls.append(kwargs)
            return SimpleNamespace(url="https://checkout.test/session", id="cs_test_123")

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, signature, secret):
            fake.webhook_calls.append((payload, signature, secret))
            if fake.construct_error:
                raise fake.construct_error
            return fake.event

    class FakeBillingPortalSession:
        @staticmethod
        def create(**kwargs):
            fake.portal_calls.append(kwargs)
            return SimpleNamespace(url="https://billing.test/portal")

    class FakeInvoice:
        @staticmethod
        def list(**kwargs):
            fake.invoice_calls.append(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        id="in_123",
                        amount_paid=4900,
                        currency="usd",
                        status="paid",
                        created=1234567890,
                        invoice_pdf="https://billing.test/in_123.pdf",
                    )
                ]
            )

    fake.checkout = SimpleNamespace(Session=FakeCheckoutSession)
    fake.billing_portal = SimpleNamespace(Session=FakeBillingPortalSession)
    fake.Invoice = FakeInvoice
    fake.Webhook = FakeWebhook
    fake.error = SimpleNamespace(SignatureVerificationError=SignatureVerificationError)
    sys.modules["stripe"] = fake
    return fake


class BillingRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        self._old_stripe = sys.modules.get("stripe", _MISSING)
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        webhook_routes._processed_events.clear()
        set_key_store(None)
        set_store(None)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        if self._old_stripe is _MISSING:
            sys.modules.pop("stripe", None)
        else:
            sys.modules["stripe"] = self._old_stripe
        webhook_routes._processed_events.clear()
        set_key_store(None)
        set_store(None)

    async def test_subscription_checkout_uses_test_mode_price_from_env(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_issue1"
        os.environ["STRIPE_PRICE_STARTER"] = "price_starter_test"

        response = await create_checkout(
            CheckoutRequest(tier="starter", key_id="key_123", email="buyer@example.com"),
            authenticated_key_id="key_123",
        )

        self.assertEqual(fake_stripe.api_key, "sk_test_issue1")
        self.assertEqual(response.checkout_url, "https://checkout.test/session")
        self.assertEqual(response.session_id, "cs_test_123")
        self.assertEqual(fake_stripe.checkout_calls[0]["mode"], "subscription")
        self.assertEqual(fake_stripe.checkout_calls[0]["line_items"][0]["price"], "price_starter_test")
        self.assertEqual(fake_stripe.checkout_calls[0]["metadata"], {"key_id": "key_123", "tier": "starter"})

    async def test_subscription_checkout_rejects_other_key_id(self):
        install_fake_stripe()
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_issue1"
        os.environ["STRIPE_PRICE_STARTER"] = "price_starter_test"

        with self.assertRaises(HTTPException) as ctx:
            await create_checkout(
                CheckoutRequest(tier="starter", key_id="key_other", email="buyer@example.com"),
                authenticated_key_id="key_123",
            )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_license_checkout_uses_test_mode_price_from_env(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_issue1"
        os.environ["STRIPE_LICENSE_SOLO"] = "price_license_solo_test"

        response = await create_license_checkout(
            LicenseCheckoutRequest(license_type="solo", email="buyer@example.com")
        )

        self.assertEqual(fake_stripe.api_key, "sk_test_issue1")
        self.assertEqual(response.session_id, "cs_test_123")
        self.assertEqual(fake_stripe.checkout_calls[0]["mode"], "payment")
        self.assertEqual(fake_stripe.checkout_calls[0]["line_items"][0]["price"], "price_license_solo_test")
        self.assertEqual(fake_stripe.checkout_calls[0]["metadata"], {"license_type": "solo"})
        self.assertNotIn("{LICENSE_TYPE}", fake_stripe.checkout_calls[0]["success_url"])
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", fake_stripe.checkout_calls[0]["success_url"])
        self.assertIn("license_type=solo", fake_stripe.checkout_calls[0]["success_url"])

    async def test_price_lookup_uses_current_environment(self):
        os.environ["STRIPE_PRICE_PRO"] = "price_pro_first"
        self.assertEqual(billing.get_tier_price_id("pro"), "price_pro_first")
        self.assertEqual(billing.TIER_TO_PRICE["pro"], "price_pro_first")

        os.environ["STRIPE_PRICE_PRO"] = "price_pro_second"
        self.assertEqual(billing.get_tier_price_id("pro"), "price_pro_second")
        self.assertEqual(billing.TIER_TO_PRICE["pro"], "price_pro_second")

    async def test_billing_portal_and_invoices_use_sync_store_customer_id(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_issue1"
        set_key_store(SyncBillingStore())

        portal = await customer_portal(key_id="key_123")
        invoices = await list_invoices(key_id="key_123")

        self.assertEqual(portal, {"portal_url": "https://billing.test/portal"})
        self.assertEqual(fake_stripe.portal_calls[0]["customer"], "cus_123")
        self.assertEqual(fake_stripe.invoice_calls[0], {"customer": "cus_123", "limit": 10})
        self.assertEqual(invoices["invoices"][0]["id"], "in_123")

    async def test_webhook_rejects_invalid_signature(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        fake_stripe.construct_error = fake_stripe.error.SignatureVerificationError("bad sig")

        with self.assertRaises(HTTPException) as ctx:
            await stripe_webhook(FakeRequest())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "Invalid signature")

    async def test_webhook_events_update_key_store(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        store = FakeKeyStore()
        set_store(store)

        events = [
            {
                "id": "evt_checkout_sub",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "metadata": {"key_id": "key_123", "tier": "pro"},
                        "subscription": "sub_123",
                        "customer": "cus_123",
                    }
                },
            },
            {
                "id": "evt_checkout_license",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "metadata": {"license_type": "team"},
                        "customer_email": "buyer@example.com",
                    }
                },
            },
            {
                "id": "evt_sub_updated",
                "type": "customer.subscription.updated",
                "data": {"object": {"customer": "cus_123", "status": "active"}},
            },
            {
                "id": "evt_sub_deleted",
                "type": "customer.subscription.deleted",
                "data": {"object": {"customer": "cus_123"}},
            },
            {
                "id": "evt_invoice_failed",
                "type": "invoice.payment_failed",
                "data": {"object": {"customer": "cus_123"}},
            },
            {
                "id": "evt_invoice_paid",
                "type": "invoice.paid",
                "data": {"object": {"customer": "cus_123"}},
            },
        ]

        for event in events:
            fake_stripe.event = event
            self.assertEqual(await stripe_webhook(FakeRequest()), {"received": True})

        self.assertEqual(store.calls[0], ("upgrade_tier", "key_123", "pro", "sub_123", "cus_123"))
        self.assertEqual(store.calls[1][:3], ("add_license", "buyer@example.com", "team"))
        self.assertTrue(store.calls[1][3].startswith("sk_license_"))
        self.assertEqual(store.calls[2], ("update_subscription_status", "cus_123", "active"))
        self.assertEqual(store.calls[3], ("downgrade_to_free", "cus_123"))
        self.assertEqual(store.calls[4], ("update_subscription_status", "cus_123", "past_due"))
        self.assertEqual(store.calls[5], ("update_subscription_status", "cus_123", "active"))

    async def test_webhook_duplicate_event_does_not_repeat_side_effects(self):
        fake_stripe = install_fake_stripe()
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        store = FakeKeyStore()
        set_store(store)
        fake_stripe.event = {
            "id": "evt_duplicate",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"license_type": "team"},
                    "customer_email": "buyer@example.com",
                }
            },
        }

        first = await stripe_webhook(FakeRequest())
        second = await stripe_webhook(FakeRequest())

        self.assertEqual(first, {"received": True})
        self.assertEqual(second, {"received": True, "duplicate": True})
        self.assertEqual(len(store.calls), 1)

    async def test_missing_webhook_secret_fails_before_stripe_import(self):
        sys.modules.pop("stripe", None)

        with self.assertRaises(HTTPException) as ctx:
            await stripe_webhook(FakeRequest())

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "STRIPE_WEBHOOK_SECRET not configured")
        self.assertNotIn("stripe", sys.modules)

    def test_billing_disabled_app_excludes_stripe_backed_routes(self):
        sys.modules.pop("stripe", None)
        main = importlib.import_module("asb_api.__main__")
        paths = {route.path for route in main.app.routes}

        self.assertNotIn("/v1/billing/checkout", paths)
        self.assertNotIn("/v1/billing/license-checkout", paths)
        self.assertNotIn("/v1/billing/webhook", paths)
        self.assertNotIn("/v1/billing/portal", paths)
        self.assertNotIn("/v1/billing/invoices", paths)
        self.assertIn("/v1/billing/verify-license", paths)
        self.assertNotIn("stripe", sys.modules)


if __name__ == "__main__":
    unittest.main()
