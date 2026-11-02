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


class TestCouncilAlwaysOn(unittest.TestCase):
    """Council Mode was a per-campaign opt-in toggle through 2026-08-20 early that night, then
    became mandatory on every turn per a direction change later the same night (see NOTES.md) -
    the old POST /api/character/<id>/council_mode route and its state["council_mode"] flag are gone.
    These confirm the new unconditional behavior.

    2026-11-02: council became opt-in again, via the COUNCIL_MODE env var (server.COUNCIL_MODE) -
    these tests now run with it switched on, plus one test for the new default."""

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

    def _create_character(self):
        user = db.get_or_create_discord_user("1", "Someone", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        return res.get_json()["character_id"]

    def test_toggle_route_no_longer_exists(self):
        character_id = self._create_character()
        res = self.client.post(f"/api/character/{character_id}/council_mode", json={"enabled": True})
        self.assertEqual(res.status_code, 404)

    def test_turn_route_always_passes_council_mode_true_to_engine(self):
        character_id = self._create_character()

        captured = {}
        real_run_turn = engine.run_turn

        def spy_run_turn(*args, **kwargs):
            captured.update(kwargs)
            return real_run_turn(*args, **kwargs)

        with patch.object(providers, "get_provider", return_value=(FakeProvider(), "gpt-5.6-luna")), \
             patch.object(engine, "run_turn", side_effect=spy_run_turn), \
             patch.object(server, "COUNCIL_MODE", True):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(captured.get("council_mode"))
        self.assertEqual(captured.get("model_key"), "luna")

    def test_council_is_off_by_default(self):
        self.assertFalse(server.COUNCIL_MODE)

    def test_a_council_debug_event_is_always_appended(self):
        character_id = self._create_character()
        with patch.object(providers, "get_provider", return_value=(FakeProvider(), "gpt-5.6-luna")), \
             patch.object(server, "COUNCIL_MODE", True):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        debug_events = res.get_json()["debug_events"]
        council_events = [e for e in debug_events if e.get("type") == "council"]
        self.assertEqual(len(council_events), 1)


if __name__ == "__main__":
    unittest.main()
