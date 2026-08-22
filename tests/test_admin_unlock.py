import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import server


class TestAdminUnlock(unittest.TestCase):
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

    def _login_as_discord_user(self):
        user = db.get_or_create_discord_user("777", "Someone", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        return user

    def test_api_me_reports_is_admin_false_by_default(self):
        self._login_as_discord_user()
        res = self.client.get("/api/me")
        self.assertFalse(res.get_json()["is_admin"])

    def test_correct_code_grants_admin_and_persists(self):
        user = self._login_as_discord_user()
        res = self.client.post("/api/admin/unlock", json={"code": "ElfbertWasRight"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        self.assertTrue(res.get_json()["is_admin"])

        # Persisted on the account, not just this session - a fresh lookup of the same user_id
        # shows it too.
        fresh = db.get_user_by_id(user["id"])
        self.assertTrue(fresh["is_admin"])

        me = self.client.get("/api/me")
        self.assertTrue(me.get_json()["is_admin"])

    def test_wrong_code_is_rejected_and_does_not_grant_admin(self):
        user = self._login_as_discord_user()
        res = self.client.post("/api/admin/unlock", json={"code": "wrong-code"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json()["ok"])
        self.assertFalse(db.get_user_by_id(user["id"])["is_admin"])

    def test_empty_code_is_rejected(self):
        self._login_as_discord_user()
        res = self.client.post("/api/admin/unlock", json={})
        self.assertEqual(res.status_code, 400)

    def test_local_user_can_also_unlock_admin(self):
        # Not blocked for local/anonymous profiles - see db.set_is_admin's docstring for the real
        # implication of that (it's the single shared local row) - just confirming the route itself
        # doesn't special-case is_local the way the turn route does.
        self.client.get("/api/me")
        res = self.client.post("/api/admin/unlock", json={"code": "ElfbertWasRight"})
        self.assertTrue(res.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
