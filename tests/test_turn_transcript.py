import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import providers
import server


class FakeProvider:
    def __init__(self, usage):
        self._usage = usage

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        return providers.TurnResult(text_parts=["You act boldly."], tool_calls=[], usage=self._usage)

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestDbTurnStorage(unittest.TestCase):
    """Unit-level: db.record_turn/get_turns/list_characters_with_turn_counts directly."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self):
        user = db.get_or_create_discord_user("999", "Someone", None)
        return db.create_character(user["id"], "petrograd_1917", "red_admiral", "Test Char", {"turn_number": 1})

    def test_record_and_retrieve_a_turn_round_trips_exactly(self):
        character_id = self._make_character()
        debug_events = [{"type": "check", "attribute": "oration", "dc": 12, "roll": 15, "degree": "success"}]
        db.record_turn(character_id, 1, "I give a speech.", "The crowd roars.", "sonnet", debug_events)

        turns = db.get_turns(character_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["turn_number"], 1)
        self.assertEqual(turns[0]["player_input"], "I give a speech.")
        self.assertEqual(turns[0]["narration"], "The crowd roars.")
        self.assertEqual(turns[0]["model_key"], "sonnet")
        import json
        self.assertEqual(json.loads(turns[0]["debug_events_json"]), debug_events)

    def test_turns_are_returned_in_turn_order(self):
        character_id = self._make_character()
        db.record_turn(character_id, 2, "second", "narration 2", "luna", [])
        db.record_turn(character_id, 1, "first", "narration 1", "luna", [])
        turns = db.get_turns(character_id)
        self.assertEqual([t["turn_number"] for t in turns], [1, 2])

    def test_no_turns_recorded_returns_empty_list(self):
        character_id = self._make_character()
        self.assertEqual(db.get_turns(character_id), [])

    def test_list_characters_with_turn_counts_reflects_real_counts(self):
        character_id = self._make_character()
        db.record_turn(character_id, 1, "in", "out", "luna", [])
        db.record_turn(character_id, 2, "in2", "out2", "luna", [])
        rows = db.list_characters_with_turn_counts()
        row = next(r for r in rows if r["id"] == character_id)
        self.assertEqual(row["turn_count"], 2)


class TestServerRecordsTurnsOnSuccess(unittest.TestCase):
    """Integration-level: a real turn through the Flask route actually lands in the `turns` table."""

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

    def _login_and_create_character(self):
        user = db.get_or_create_discord_user("12345", "Someone", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        return res.get_json()["character_id"]

    def test_a_successful_turn_is_recorded_with_full_narration_and_input(self):
        character_id = self._login_and_create_character()
        usage = {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 10,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0}
        with patch.object(providers, "get_provider", return_value=(FakeProvider(usage), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I address the crowd.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)

        turns = db.get_turns(character_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["player_input"], "I address the crowd.")
        self.assertEqual(turns[0]["narration"], res.get_json()["narration"])
        self.assertEqual(turns[0]["model_key"], "luna")
        # Fresh campaigns start at turn_number 0 (matches campaign_log_summary's own convention -
        # both capture the pre-increment value before engine.run_turn bumps it for the next turn).
        self.assertEqual(turns[0]["turn_number"], 0)

    def test_a_turn_blocked_by_exhausted_budget_is_not_recorded(self):
        character_id = self._login_and_create_character()
        res = self.client.get("/api/me")
        user_id = res.get_json()["id"]
        db.record_usage(user_id, character_id, "openai", "gpt-5.6-luna", 0, 0, 0, 0, db.DEFAULT_BUDGET_USD)

        usage = {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 10,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0}
        with patch.object(providers, "get_provider", return_value=(FakeProvider(usage), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I address the crowd.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 402)
        self.assertEqual(db.get_turns(character_id), [])


if __name__ == "__main__":
    unittest.main()
