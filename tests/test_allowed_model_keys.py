import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
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


class TestAllowedModelKeys(unittest.TestCase):
    """providers.ALLOWED_MODEL_KEYS replaced the earlier singular LOCKED_MODEL_KEY, and as of
    2026-08-21 defaults to None (every registered model selectable, Claude restored as the default -
    see NOTES.md) - but the restriction mechanism itself is still real and still enforced server-side
    if re-enabled later, which these tests verify by patching it back on."""

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

    def test_api_models_lists_everything_by_default(self):
        # ALLOWED_MODEL_KEYS defaults to None as of 2026-08-21 - Claude restored as the default
        # model, no models hidden from the picker (see NOTES.md).
        res = self.client.get("/api/models")
        keys = {m["key"] for m in res.get_json()["models"]}
        self.assertEqual(keys, set(providers.MODEL_REGISTRY.keys()))

    def test_api_models_only_lists_allowed_keys_when_restricted(self):
        # The restriction mechanism itself still works if re-enabled later - not exercised by
        # default anymore, but still real, still enforced server-side.
        with patch.object(providers, "ALLOWED_MODEL_KEYS", {"luna", "local-ollama"}):
            res = self.client.get("/api/models")
            keys = {m["key"] for m in res.get_json()["models"]}
            self.assertEqual(keys, {"luna", "local-ollama"})

    def test_requesting_a_disallowed_model_is_rejected_not_silently_substituted(self):
        self._login_as_discord_user()
        char_res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        character_id = char_res.get_json()["character_id"]

        call_count = {"n": 0}

        def fake_get_provider(model_key):
            call_count["n"] += 1
            return FakeProvider(), "gpt-5.6-luna"

        with patch.object(providers, "ALLOWED_MODEL_KEYS", {"luna", "local-ollama"}), \
             patch.object(providers, "get_provider", side_effect=fake_get_provider):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "sonnet"},
            )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(call_count["n"], 0, "should be rejected before ever reaching a provider")

    def test_local_ollama_shows_unavailable_when_not_configured(self):
        # No OLLAMA_TUNNEL_URL/SECRET set in the test environment - should be listed but disabled.
        res = self.client.get("/api/models")
        entry = next(m for m in res.get_json()["models"] if m["key"] == "local-ollama")
        self.assertFalse(entry["available"])
        self.assertEqual(entry["unavailable_reason"], "not configured")

    def test_local_ollama_shows_offline_when_configured_but_unhealthy(self):
        with patch.object(providers, "provider_available", return_value=True), \
             patch.object(providers, "ollama_tunnel_is_healthy", return_value=False):
            res = self.client.get("/api/models")
        entry = next(m for m in res.get_json()["models"] if m["key"] == "local-ollama")
        self.assertFalse(entry["available"])
        self.assertEqual(entry["unavailable_reason"], "currently offline")

    def test_local_ollama_shows_available_when_healthy(self):
        with patch.object(providers, "provider_available", return_value=True), \
             patch.object(providers, "ollama_tunnel_is_healthy", return_value=True):
            res = self.client.get("/api/models")
        entry = next(m for m in res.get_json()["models"] if m["key"] == "local-ollama")
        self.assertTrue(entry["available"])
        self.assertIsNone(entry["unavailable_reason"])

    def test_turn_rejected_up_front_with_clear_flag_when_local_model_unhealthy(self):
        self._login_as_discord_user()
        char_res = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        character_id = char_res.get_json()["character_id"]

        with patch.object(providers, "ollama_tunnel_is_healthy", return_value=False):
            res = self.client.post(
                f"/api/character/{character_id}/turn",
                json={"input": "I look around.", "model_key": "local-ollama"},
            )
        self.assertEqual(res.status_code, 503)
        self.assertTrue(res.get_json()["local_model_offline"])


if __name__ == "__main__":
    unittest.main()
