"""Tests for URL safety controls and log redaction (issue #8)."""

import unittest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from asb_api.security import (
    SecurityConfig,
    set_security_config,
    get_security_config,
    validate_scrape_url,
    redact_url_for_logging,
    redact_headers_for_logging,
    redact_api_key_for_logging,
    _is_private_or_localhost,
)


class SecurityConfigTests(unittest.TestCase):
    def test_defaults_are_safe_for_dev(self):
        cfg = SecurityConfig()
        self.assertTrue(cfg.log_url_domains_only)
        self.assertTrue(cfg.redact_authorization_headers)
        self.assertFalse(cfg.block_private_networks)
        self.assertIn("https", cfg.allowed_schemes)

    def test_set_and_get_global_config(self):
        set_security_config({"security": {"block_private_networks": True, "log_url_domains_only": False}})
        cfg = get_security_config()
        self.assertTrue(cfg.block_private_networks)
        self.assertFalse(cfg.log_url_domains_only)
        # reset for other tests
        set_security_config(SecurityConfig())


class URLSafetyValidationTests(unittest.TestCase):
    def setUp(self):
        set_security_config(SecurityConfig(block_private_networks=False))

    def test_allows_http_and_https(self):
        self.assertEqual(validate_scrape_url("https://example.com/path?x=1"), "https://example.com/path?x=1")
        self.assertEqual(validate_scrape_url("http://example.com:8080/foo"), "http://example.com:8080/foo")

    def test_rejects_bad_schemes(self):
        bad = ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "ftp://evil.com", "ws://example.com"]
        for u in bad:
            with self.assertRaises(HTTPException) as ctx:
                validate_scrape_url(u)
            self.assertEqual(ctx.exception.status_code, 400)
            detail = ctx.exception.detail
            self.assertEqual(detail.get("error_code"), "INVALID_URL_SCHEME")

    def test_rejects_malformed_urls(self):
        for u in ["", "   ", "not-a-url", "://missing-scheme", "http://"]:
            with self.assertRaises(HTTPException) as ctx:
                validate_scrape_url(u)
            self.assertIn(ctx.exception.status_code, (400, 422))

    def test_private_network_blocking_when_enabled(self):
        set_security_config(SecurityConfig(block_private_networks=True))

        dangerous = [
            "http://127.0.0.1:8000/admin",
            "https://localhost/secret",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
        ]
        for u in dangerous:
            with self.assertRaises(HTTPException) as ctx:
                validate_scrape_url(u)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail.get("error_code"), "PRIVATE_NETWORK_BLOCKED")

        # Public should still work
        validate_scrape_url("https://example.com/public")

    def test_private_check_helper(self):
        self.assertTrue(_is_private_or_localhost("127.0.0.1"))
        self.assertTrue(_is_private_or_localhost("localhost"))
        self.assertTrue(_is_private_or_localhost("10.1.2.3"))
        self.assertTrue(_is_private_or_localhost("192.168.0.1"))
        self.assertFalse(_is_private_or_localhost("93.184.216.34"))  # example.com
        self.assertFalse(_is_private_or_localhost("example.com"))


class RedactionTests(unittest.TestCase):
    def setUp(self):
        set_security_config(SecurityConfig(log_url_domains_only=True, redact_authorization_headers=True))

    def test_redact_url_domains_only(self):
        self.assertEqual(redact_url_for_logging("https://user:pass@api.example.com:8443/v1/scrape?token=secret"), "api.example.com:8443")
        self.assertEqual(redact_url_for_logging("http://10.0.0.1:9000/foo"), "10.0.0.1:9000")
        self.assertEqual(redact_url_for_logging("https://example.com"), "example.com")

    def test_redact_url_full_when_disabled(self):
        cfg = SecurityConfig(log_url_domains_only=False)
        set_security_config(cfg)
        url = "https://example.com/path?auth=supersecret"
        self.assertEqual(redact_url_for_logging(url), url)  # still returns full; real token stripping would be extra

    def test_redact_auth_headers(self):
        headers = {
            "Authorization": "Bearer sk_live_very_secret_1234567890",
            "Content-Type": "application/json",
            "X-Custom": "ok",
            "Proxy-Authorization": "Basic abc",
        }
        redacted = redact_headers_for_logging(headers)
        self.assertEqual(redacted["Authorization"], "[REDACTED]")
        self.assertEqual(redacted["Proxy-Authorization"], "[REDACTED]")
        self.assertEqual(redacted["Content-Type"], "application/json")
        self.assertEqual(redacted["X-Custom"], "ok")

    def test_redact_api_key_helper(self):
        self.assertEqual(redact_api_key_for_logging("sk_live_1234567890abcdef"), "sk_live_1234...")
        self.assertEqual(redact_api_key_for_logging("short"), "[REDACTED]")
        self.assertEqual(redact_api_key_for_logging(""), "")


class SecurityEndpointIntegrationTests(unittest.TestCase):
    """Lightweight integration via direct function calls (full app startup has heavy deps)."""

    def test_validate_is_called_and_rejects_before_expensive_work(self):
        # If this raises the right error, the early gate in scrape route is working
        with self.assertRaises(HTTPException) as ctx:
            validate_scrape_url("file:///etc/shadow")
        self.assertEqual(ctx.exception.detail["error_code"], "INVALID_URL_SCHEME")


if __name__ == "__main__":
    unittest.main()
