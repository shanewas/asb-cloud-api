import os
import tempfile
import textwrap
import unittest

from asb_api.config import load_config


class ConfigLoadingTests(unittest.TestCase):
    def test_env_defaults_and_yaml_null_values(self):
        os.environ.pop("ASB_SCREENSHOT_DIR", None)
        os.environ.pop("MISSING_ASB_VALUE", None)

        config_text = textwrap.dedent(
            """
            providers:
              null:
                enabled: true
            database:
              dsn:
            screenshots:
              dir: "${ASB_SCREENSHOT_DIR:/tmp/screenshots}"
            plain_missing: "${MISSING_ASB_VALUE}"
            default_missing: "${MISSING_ASB_VALUE:-fallback}"
            """
        )

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(config_text)
            config_path = handle.name

        try:
            cfg = load_config(config_path)
        finally:
            os.unlink(config_path)

        self.assertIn("null", cfg["providers"])
        self.assertIsNone(cfg["database"]["dsn"])
        self.assertEqual(cfg["screenshots"]["dir"], "/tmp/screenshots")
        self.assertEqual(cfg["plain_missing"], "")
        self.assertEqual(cfg["default_missing"], "fallback")


if __name__ == "__main__":
    unittest.main()
