import os
from collections.abc import Iterator, Mapping


TIER_PRICE_ENV = {
    "starter": "STRIPE_PRICE_STARTER",
    "pro": "STRIPE_PRICE_PRO",
    "enterprise": "STRIPE_PRICE_ENTERPRISE",
}

LICENSE_PRICE_ENV = {
    "solo": "STRIPE_LICENSE_SOLO",
    "team": "STRIPE_LICENSE_TEAM",
    "enterprise": "STRIPE_LICENSE_ENTERPRISE",
}


class EnvPriceMap(Mapping[str, str]):
    def __init__(self, env_by_key: dict[str, str]):
        self._env_by_key = env_by_key

    def __getitem__(self, key: str) -> str:
        env_key = self._env_by_key[key]
        return os.environ.get(env_key, "")

    def __iter__(self) -> Iterator[str]:
        return iter(self._env_by_key)

    def __len__(self) -> int:
        return len(self._env_by_key)


TIER_TO_PRICE: Mapping[str, str] = EnvPriceMap(TIER_PRICE_ENV)
LICENSE_TO_PRICE: Mapping[str, str] = EnvPriceMap(LICENSE_PRICE_ENV)


def get_tier_price_id(tier: str) -> str:
    return TIER_TO_PRICE.get(tier, "")


def get_license_price_id(license_type: str) -> str:
    return LICENSE_TO_PRICE.get(license_type, "")


def __getattr__(name: str) -> str:
    legacy_prices = {
        "FREE_PRICE_ID": ("tier", "starter"),
        "PRO_PRICE_ID": ("tier", "pro"),
        "ENTERPRISE_PRICE_ID": ("tier", "enterprise"),
        "SOLO_LICENSE_PRICE_ID": ("license", "solo"),
        "TEAM_LICENSE_PRICE_ID": ("license", "team"),
        "ENTERPRISE_LICENSE_PRICE_ID": ("license", "enterprise"),
    }
    if name not in legacy_prices:
        raise AttributeError(name)
    kind, key = legacy_prices[name]
    if kind == "tier":
        return get_tier_price_id(key)
    return get_license_price_id(key)


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
