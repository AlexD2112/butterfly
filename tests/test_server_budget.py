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

    def call(self, model, system_prompt, tool_specs, conversation):
        return providers.TurnResult(text_parts=["You act."], tool_calls=[], usage=self._usage)

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestTurnRouteBudget(unittest.TestCase):
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

    def _create_premade_character(self):
        self.client.get("/api/me")  # establishes the local-profile session cookie
        res = self.client.post(
            "/api/characters",
            json={"type": "premade", "setting_key": "petrograd_1917"},
        )
        return res.get_json()["character_id"]

    def test_turn_deducts_real_cost_and_returns_remaining_budget(self):
        character_id = self._create_premade_character()
        usage = {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        with patch.object(providers, "get_provider", return_value=(FakeProvider(usage), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        expected_cost = (1000 / 1_000_000) * 0.20 + (500 / 1_000_000) * 1.20
        self.assertAlmostEqual(data["budget"]["spent_usd"], expected_cost)
        self.assertAlmostEqual(data["budget"]["remaining_usd"], db.DEFAULT_BUDGET_USD - expected_cost)

    def test_turn_blocked_with_402_when_budget_already_exhausted(self):
        character_id = self._create_premade_character()
        res = self.client.get("/api/me")
        user_id = res.get_json()["id"]
        db.record_usage(user_id, character_id, "openai", "gpt-5.6-luna", 0, 0, 0, 0, db.DEFAULT_BUDGET_USD)

        call_count = {"n": 0}

        class CountingFakeProvider(FakeProvider):
            def call(self, *a, **kw):
                call_count["n"] += 1
                return super().call(*a, **kw)

        usage = {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 10,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0}
        with patch.object(providers, "get_provider", return_value=(CountingFakeProvider(usage), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 402)
        self.assertEqual(call_count["n"], 0, "the provider should never be called once budget is exhausted")

    def test_api_me_exposes_budget_fields(self):
        res = self.client.get("/api/me")
        data = res.get_json()
        self.assertIn("budget", data)
        self.assertEqual(data["budget"]["budget_usd"], db.DEFAULT_BUDGET_USD)
        self.assertEqual(data["budget"]["remaining_usd"], db.DEFAULT_BUDGET_USD)


if __name__ == "__main__":
    unittest.main()
