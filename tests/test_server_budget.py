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

    def _login_as_discord_user(self, discord_id="12345", username="Someone"):
        # Bypasses the real OAuth network exchange - directly creates/attaches to a Discord user
        # row and sets the session the same way auth_discord_callback would after a real exchange.
        user = db.get_or_create_discord_user(discord_id, username, None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        return user

    def _create_premade_character(self, as_discord=True):
        # Budget is Discord-only (see server.py's api_character_turn) - tests that actually spend
        # budget need a Discord-linked session, not the shared local/anonymous one.
        if as_discord:
            self._login_as_discord_user()
        else:
            self.client.get("/api/me")  # establishes the shared local-profile session
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

    def test_api_me_exposes_budget_fields_for_discord_user(self):
        self._login_as_discord_user()
        res = self.client.get("/api/me")
        data = res.get_json()
        self.assertIn("budget", data)
        self.assertEqual(data["budget"]["budget_usd"], db.DEFAULT_BUDGET_USD)
        self.assertEqual(data["budget"]["remaining_usd"], db.DEFAULT_BUDGET_USD)

    def test_api_me_zeroes_budget_for_local_user(self):
        # Local/anonymous profiles share ONE row deployment-wide (see db.py's module docstring) -
        # api_me should never report that shared row's real budget numbers as if they belonged to
        # this particular visitor.
        res = self.client.get("/api/me")
        data = res.get_json()
        self.assertTrue(data["is_local"])
        self.assertEqual(data["budget"]["budget_usd"], 0.0)
        self.assertEqual(data["budget"]["remaining_usd"], 0.0)
        self.assertTrue(data["budget"]["discord_login_required"])

    def test_local_user_cannot_spend_budget_on_a_turn(self):
        character_id = self._create_premade_character(as_discord=False)

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
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.get_json()["discord_login_required"])
        self.assertEqual(call_count["n"], 0, "the provider should never be called for a local/anonymous profile")


if __name__ == "__main__":
    unittest.main()
