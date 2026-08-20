import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import providers
import server


class FakeProvider:
    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        return providers.TurnResult(text_parts=["You act."], tool_calls=[], usage={
            "provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
        })

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestModelLock(unittest.TestCase):
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
        user = db.get_or_create_discord_user("1", "Someone", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]

    def test_api_models_reports_the_lock(self):
        res = self.client.get("/api/models")
        data = res.get_json()
        self.assertEqual(data["locked_model_key"], "luna")

    def test_requesting_a_different_model_key_is_overridden_server_side(self):
        self._login_as_discord_user()
        char_res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        character_id = char_res.get_json()["character_id"]

        captured = {}

        def fake_get_provider(model_key):
            captured["model_key"] = model_key
            return FakeProvider(), "gpt-5.6-luna"

        with patch.object(providers, "get_provider", side_effect=fake_get_provider):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "sonnet"},  # attempt to request a locked-out model
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured["model_key"], "luna", "the lock must override an explicitly requested model_key")

    def test_lock_can_be_lifted_by_clearing_the_constant(self):
        # Confirms the revert path actually works, not just that the lock is currently on.
        with patch.object(providers, "LOCKED_MODEL_KEY", None):
            res = self.client.get("/api/models")
            self.assertIsNone(res.get_json()["locked_model_key"])


if __name__ == "__main__":
    unittest.main()
