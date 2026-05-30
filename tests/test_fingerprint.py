import unittest

from asb_api.fingerprint.generator import FingerprintGenerator


class FingerprintGeneratorTests(unittest.TestCase):
    def test_rejects_invalid_viewport(self):
        generator = FingerprintGenerator({"general": {"viewport": [1920]}})

        with self.assertRaises(ValueError):
            generator.get("general")

    def test_rejects_invalid_canvas_mode(self):
        generator = FingerprintGenerator({"general": {"viewport": [1920, 1080], "canvas": "sparkles"}})

        with self.assertRaises(ValueError):
            generator.get("general")


if __name__ == "__main__":
    unittest.main()
