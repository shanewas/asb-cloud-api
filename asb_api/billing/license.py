import hmac
import hashlib
import secrets
import time
import string


class SelfHostedLicense:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()

    def generate(self, license_type: str, domain: str, expiry_ts: int) -> str:
        """Generate a license key for a self-hosted deployment.
        Embeds expiry_ts so verify_full can parse and enforce it.
        """
        token = secrets.token_hex(8)
        payload = self._payload(license_type, domain, token, expiry_ts)
        sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"sk_license_{token}_{expiry_ts}_{sig}"

    def verify(self, license_key: str, license_type: str, domain: str, expiry_ts: int) -> bool:
        """Verify a license key (caller provides expected expiry)."""
        parsed = self._parse_key(license_key)
        if not parsed:
            return False
        token, parsed_expiry, provided_sig = parsed
        if parsed_expiry != expiry_ts:
            return False
        payload = self._payload(license_type, domain, token, expiry_ts)
        expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(expected_sig, provided_sig)

    def verify_full(self, license_key: str, license_type: str, domain: str) -> tuple[bool, str]:
        """Verify and return (valid, error_message)."""
        parsed = self._parse_key(license_key)
        if not parsed:
            return False, "Malformed license key"
        try:
            token, expiry_ts, provided_sig = parsed
            if time.time() > expiry_ts:
                return False, "License key expired"
            payload = self._payload(license_type, domain, token, expiry_ts)
            expected_sig = hmac.new(self.secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
            if not hmac.compare_digest(expected_sig, provided_sig):
                return False, "Invalid license signature"
            return True, ""
        except ValueError:
            return False, "Invalid expiry in license key"

    def _payload(self, license_type: str, domain: str, token: str, expiry_ts: int) -> str:
        return f"{license_type}:{domain}:{token}:{expiry_ts}"

    def _parse_key(self, license_key: str) -> tuple[str, int, str] | None:
        parts = license_key.split("_")
        if len(parts) != 5 or parts[0] != "sk" or parts[1] != "license":
            return None
        token, expiry_raw, sig = parts[2], parts[3], parts[4]
        hexdigits = set(string.hexdigits)
        if len(token) != 16 or len(sig) != 16:
            return None
        if any(char not in hexdigits for char in token + sig):
            return None
        try:
            expiry_ts = int(expiry_raw)
        except ValueError:
            return None
        return token, expiry_ts, sig
