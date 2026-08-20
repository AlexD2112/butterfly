import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import server


class TestConcurrentTurnLock(unittest.TestCase):
    """Covers the per-account lock added to api_character_turn: two requests for the same account
    racing past the budget check before either records its spend was a real bug (see NOTES.md) -
    this confirms a second in-flight request for the same account is rejected rather than allowed
    to race the first one."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_second_concurrent_request_for_same_account_is_rejected(self):
        # Must be a Discord-linked session - budget (and therefore this lock) is gated to Discord
        # identities only, see api_character_turn. A local session would short-circuit with 403
        # before ever reaching the lock, which isn't what this test is checking.
        user = db.get_or_create_discord_user("999", "Someone", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        user_id = user["id"]
        res = self.client.post(
            "/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"}
        )
        character_id = res.get_json()["character_id"]

        lock = server._turn_lock_for(user_id)
        lock.acquire()  # simulate a turn already in flight
        try:
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
            self.assertEqual(res.status_code, 429)
        finally:
            lock.release()

    def test_different_accounts_are_not_serialized_against_each_other(self):
        # Two distinct users' locks must be independent objects, not one shared lock keyed wrong.
        lock_a = server._turn_lock_for(1)
        lock_b = server._turn_lock_for(2)
        self.assertIsNot(lock_a, lock_b)
        lock_a.acquire()
        try:
            self.assertTrue(lock_b.acquire(blocking=False))
            lock_b.release()
        finally:
            lock_a.release()


class TestDiscordCallbackStateValidation(unittest.TestCase):
    """CSRF protection on the Discord OAuth callback relies on the `state` query param matching
    what /auth/discord/login stashed in the session. These confirm the callback actually rejects a
    missing/forged/stale state rather than silently proceeding."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_callback_with_no_prior_login_attempt_is_rejected(self):
        # No /auth/discord/login was ever called, so there's no oauth_state in the session at all -
        # an attacker driving a victim straight to the callback with a guessed/forged state.
        res = self.client.get("/auth/discord/callback?state=anything&code=somecode")
        self.assertEqual(res.status_code, 302)
        self.assertIn("auth_error=state_mismatch", res.headers["Location"])

    def test_callback_with_wrong_state_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "the-real-state"
        res = self.client.get("/auth/discord/callback?state=an-attacker-guessed-state&code=somecode")
        self.assertEqual(res.status_code, 302)
        self.assertIn("auth_error=state_mismatch", res.headers["Location"])

    def test_callback_with_empty_state_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "the-real-state"
        res = self.client.get("/auth/discord/callback?state=&code=somecode")
        self.assertEqual(res.status_code, 302)
        self.assertIn("auth_error=state_mismatch", res.headers["Location"])

    def test_error_param_is_url_encoded_in_redirect(self):
        res = self.client.get("/auth/discord/callback?error=access_denied%20%26%20more")
        self.assertEqual(res.status_code, 302)
        location = res.headers["Location"]
        # the raw "&" must not appear unencoded (it would otherwise inject a second query param)
        self.assertNotIn("& more", location)


if __name__ == "__main__":
    unittest.main()
