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


class TestCouncilModeToggle(unittest.TestCase):
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

    def _create_character(self):
        self._login_as_discord_user()
        res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        return res.get_json()["character_id"]

    def test_toggle_defaults_off_and_can_be_flipped_on(self):
        character_id = self._create_character()
        # Freshly created state has no council_mode key at all - the turn route treats that as off
        # via state.get("council_mode") defaulting to None/falsy, not a stored default value.
        state_res = self.client.get(f"/api/character/{character_id}/state")
        self.assertFalse(state_res.get_json()["state"].get("council_mode"))

        res = self.client.post(f"/api/character/{character_id}/council_mode", json={"enabled": True})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["council_mode"])

        state_res = self.client.get(f"/api/character/{character_id}/state")
        self.assertTrue(state_res.get_json()["state"]["council_mode"])

        res_off = self.client.post(f"/api/character/{character_id}/council_mode", json={"enabled": False})
        self.assertFalse(res_off.get_json()["council_mode"])

    def test_toggle_requires_ownership(self):
        character_id = self._create_character()
        # A different Discord user shouldn't be able to toggle someone else's character.
        other = db.get_or_create_discord_user("2", "Someone Else", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = other["id"]
        res = self.client.post(f"/api/character/{character_id}/council_mode", json={"enabled": True})
        self.assertEqual(res.status_code, 404)

    def test_turn_route_passes_council_mode_through_to_engine(self):
        character_id = self._create_character()
        self.client.post(f"/api/character/{character_id}/council_mode", json={"enabled": True})

        captured = {}
        real_run_turn = engine.run_turn

        def spy_run_turn(*args, **kwargs):
            captured.update(kwargs)
            return real_run_turn(*args, **kwargs)

        with patch.object(providers, "get_provider", return_value=(FakeProvider(), "gpt-5.6-luna")), \
             patch.object(engine, "run_turn", side_effect=spy_run_turn):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(captured.get("council_mode"))
        self.assertEqual(captured.get("model_key"), "luna")


if __name__ == "__main__":
    unittest.main()
