import importlib
import os


def get_stripe():
    stripe = importlib.import_module("stripe")
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    return stripe
