import hmac
import hashlib
import secrets
import time


class SelfHostedLicense:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()

    def generate(self, license_type: str, domain: str, expiry_ts: int) -> str:
        """Generate a license key for a self-hosted deployment.
        Embeds expiry_ts so verify_full can parse and enforce it.
        """
        payload = f"{license_type}:{domain}:{expiry_ts}"
        sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"sk_license_{secrets.token_hex(8)}_{expiry_ts}_{sig}"

    def verify(self, license_key: str, license_type: str, domain: str, expiry_ts: int) -> bool:
        """Verify a license key (caller provides expected expiry)."""
        if not license_key.startswith("sk_license_"):
            return False
        payload = f"{license_type}:{domain}:{expiry_ts}"
        expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        provided_sig = license_key.split("_")[-1]
        return hmac.compare_digest(expected_sig, provided_sig)

    def verify_full(self, license_key: str, license_type: str, domain: str) -> tuple[bool, str]:
        """Verify and return (valid, error_message)."""
        if not license_key.startswith("sk_license_"):
            return False, "Invalid license key format"
        parts = license_key.split("_")
        if len(parts) < 5:
            return False, "Malformed license key"
        try:
            expiry_ts = int(parts[3])
            if time.time() > expiry_ts:
                return False, "License key expired"
            payload = f"{license_type}:{domain}:{expiry_ts}"
            expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
            provided_sig = parts[-1]
            if not hmac.compare_digest(expected_sig, provided_sig):
                return False, "Invalid license signature"
            return True, ""
        except (ValueError, IndexError):
            return False, "Invalid expiry in license key"
