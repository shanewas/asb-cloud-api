import unittest

from cryptography.fernet import Fernet

from asb_api.session.models import SessionInfo
from asb_api.session.store import SessionStore


class SessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_canonical_session_model(self):
        store = SessionStore()

        session = await store.create("key_1", "jp")

        self.assertIsInstance(session, SessionInfo)

    async def test_encrypts_cookie_payload_when_key_is_configured(self):
        key = Fernet.generate_key().decode()
        store = SessionStore(encryption_key=key)
        session = await store.create("key_1", "jp")

        await store.update_cookies(session.session_id, {"sid": "secret"})
        raw = store._sessions[session.session_id]["cookies"]
        loaded = await store.get(session.session_id)

        self.assertIsInstance(raw, str)
        self.assertNotIn("secret", raw)
        self.assertEqual(loaded.cookies, {"sid": "secret"})


if __name__ == "__main__":
    unittest.main()
