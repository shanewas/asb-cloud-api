import time
import unittest

from asb_api.billing.license import SelfHostedLicense


class SelfHostedLicenseTests(unittest.TestCase):
    def test_generated_license_verifies_and_rejects_token_tampering(self):
        license_service = SelfHostedLicense("test-secret")
        expiry = int(time.time()) + 3600
        key = license_service.generate("solo", "example.com", expiry)

        valid, error = license_service.verify_full(key, "solo", "example.com")
        self.assertTrue(valid, error)
        self.assertTrue(license_service.verify(key, "solo", "example.com", expiry))

        parts = key.split("_")
        parts[2] = "0" * 16
        tampered_key = "_".join(parts)

        valid, error = license_service.verify_full(tampered_key, "solo", "example.com")
        self.assertFalse(valid)
        self.assertEqual(error, "Invalid license signature")
        self.assertFalse(license_service.verify(tampered_key, "solo", "example.com", expiry))

    def test_license_rejects_extra_segments(self):
        license_service = SelfHostedLicense("test-secret")
        expiry = int(time.time()) + 3600
        key = license_service.generate("solo", "example.com", expiry)

        valid, error = license_service.verify_full(f"{key}_extra", "solo", "example.com")

        self.assertFalse(valid)
        self.assertEqual(error, "Malformed license key")


if __name__ == "__main__":
    unittest.main()
