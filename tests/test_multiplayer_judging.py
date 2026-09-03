import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import multiplayer
import providers


def usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedProvider:
    """Stands in for both the judge provider and the sender's own provider - routes call() by
    force_tool_name (the judge call) vs plain text (a generated reply)."""

    def __init__(self, responds=True, responder="lenin", reply_text="A scripted in-character reply."):
        self.responds = responds
        self.responder = responder
        self.reply_text = reply_text
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        if force_tool_name == "decide_response":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "j1", "name": "decide_response", "input": {
                    "responds": self.responds, "responder": self.responder if self.responds else None,
                    "reasoning": "scripted",
                }}],
                usage=usage(),
            )
        if not tool_specs:
            return providers.TurnResult(text_parts=[self.reply_text], tool_calls=[], usage=usage())
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=usage())


class TestMultiplayerJudging(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        self.admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]
        user = db.get_or_create_discord_user("2", "Sender", None)
        self.character_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Sender", {"turn_number": 0}, game_id=self.game["id"])
        db.move_character(self.character_id, self.server_id, self.location_id, self.subspace_id)
        self.npc_id = db.create_npc(
            self.game["id"], "lenin", "Vladimir Lenin", personality="Impatient.",
            location_id=self.location_id, subspace_id=self.subspace_id,
        )

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_fresh_message_the_judge_picks_an_npc_and_a_reply_is_recorded(self):
        fake = ScriptedProvider(responds=True, responder="lenin", reply_text="Lenin's reply.")
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            message_id, reply_id, responder = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "Comrade Lenin, what should we do?",
            )
        self.assertIsNotNone(message_id)
        self.assertIsNotNone(reply_id)
        self.assertEqual(responder, "lenin")
        reply_row = db.get_turn_by_id(reply_id)
        self.assertEqual(reply_row["kind"], "npc_reply")
        self.assertEqual(reply_row["narration"], "Lenin's reply.")
        self.assertEqual(reply_row["reply_to_turn_id"], message_id)

        npc = db.get_npc(self.npc_id)
        import json
        self.assertEqual(len(json.loads(npc["memory_json"])), 1)

    def test_fresh_message_the_judge_says_nobody_responds_no_reply_row_created(self):
        fake = ScriptedProvider(responds=False)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            message_id, reply_id, responder = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "lol anyway back to you",
            )
        self.assertIsNotNone(message_id)
        self.assertIsNone(reply_id)
        self.assertIsNone(responder)
        # confirm no npc_reply/gm_reply rows exist
        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) AS n FROM turns WHERE kind IN ('npc_reply','gm_reply')").fetchone()["n"]
        conn.close()
        self.assertEqual(count, 0)

    def test_judge_naming_an_absent_npc_fails_safe_to_no_response(self):
        fake = ScriptedProvider(responds=True, responder="someone-not-present")
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            message_id, reply_id, responder = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "Hey you over there",
            )
        self.assertIsNone(reply_id)
        self.assertIsNone(responder)

    def test_reply_to_a_players_message_skips_judging_gm_responds(self):
        # First, a plain player message with no reply target (judge says no response).
        fake = ScriptedProvider(responds=False)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            first_id, _, _ = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "I mutter to myself.",
            )

        # Now another player's reply, targeting that message - should skip the judge entirely and go
        # straight to a GM reply (no decide_response call should appear in fake.calls).
        fake2 = ScriptedProvider(reply_text="A small ambient beat.")
        with patch.object(providers, "get_provider", return_value=(fake2, "gpt-5.6-luna")):
            _, reply_id, responder = multiplayer.handle_message(
                fake2, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "Did you say something?", reply_to_turn_id=first_id,
            )
        self.assertEqual(responder, "gm")
        self.assertNotIn("decide_response", fake2.calls)
        reply_row = db.get_turn_by_id(reply_id)
        self.assertEqual(reply_row["kind"], "gm_reply")

    def test_reply_to_an_npc_message_skips_judging_and_continues_the_same_voice(self):
        fake = ScriptedProvider(responds=True, responder="lenin", reply_text="Lenin's first reply.")
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            _, first_reply_id, _ = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "Comrade Lenin?",
            )

        fake2 = ScriptedProvider(reply_text="Lenin's second reply.")
        with patch.object(providers, "get_provider", return_value=(fake2, "gpt-5.6-luna")):
            _, second_reply_id, responder = multiplayer.handle_message(
                fake2, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "And what about the garrison?", reply_to_turn_id=first_reply_id,
            )
        self.assertEqual(responder, "lenin")
        self.assertNotIn("decide_response", fake2.calls)
        reply_row = db.get_turn_by_id(second_reply_id)
        self.assertEqual(reply_row["kind"], "npc_reply")
        self.assertEqual(reply_row["responder"], "lenin")

    def test_judge_provider_unavailable_falls_back_to_senders_provider(self):
        fake = ScriptedProvider(responds=True, responder="lenin", reply_text="Fallback reply.")
        with patch.object(providers, "get_provider", side_effect=providers.ProviderError("no key")):
            message_id, reply_id, responder = multiplayer.handle_message(
                fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id,
                self.character_id, "Comrade Lenin?",
            )
        # Should still complete using `fake` directly, not crash.
        self.assertEqual(responder, "lenin")
        self.assertIsNotNone(reply_id)


class ScriptedClassifyProvider:
    """Stands in for the classifier call multiplayer.classify_message_or_action makes - always
    responds to the forced 'classify' tool with a scripted kind, or (kind=None) a malformed/empty
    response, to exercise the fail-safe-to-"message" path."""

    def __init__(self, kind="action"):
        self.kind = kind
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name)
        if self.kind is None:
            return providers.TurnResult(text_parts=[], tool_calls=[], usage=usage())
        return providers.TurnResult(
            text_parts=[], tool_calls=[{"id": "c1", "name": "classify", "input": {"kind": self.kind, "reasoning": "scripted"}}],
            usage=usage(),
        )


class TestClassifyMessageOrAction(unittest.TestCase):
    """Pure function of a scripted provider - no DB needed. Real bug this exists to close (2026-09-03,
    per direction): Discord has no Message/Action toggle like the website, so a plain-typed message
    that reads as an attempted move/action was silently always handled as talk-only, with no
    mechanical resolution and no dynamic-space detection - see NOTES.md."""

    def test_scripted_action_response_returns_action(self):
        fake = ScriptedClassifyProvider(kind="action")
        kind = multiplayer.classify_message_or_action(fake, "gpt-5.6-luna", "I charge at the guard!")
        self.assertEqual(kind, "action")
        self.assertEqual(fake.calls, ["classify"])

    def test_scripted_message_response_returns_message(self):
        fake = ScriptedClassifyProvider(kind="message")
        kind = multiplayer.classify_message_or_action(fake, "gpt-5.6-luna", "Nice weather today, isn't it?")
        self.assertEqual(kind, "message")

    def test_malformed_response_fails_safe_to_message(self):
        fake = ScriptedClassifyProvider(kind=None)
        kind = multiplayer.classify_message_or_action(fake, "gpt-5.6-luna", "Whatever this is.")
        self.assertEqual(kind, "message")

    def test_usage_sink_records_the_call(self):
        fake = ScriptedClassifyProvider(kind="action")
        sink = []
        multiplayer.classify_message_or_action(fake, "gpt-5.6-luna", "I attack.", usage_sink=sink)
        self.assertEqual(len(sink), 1)


if __name__ == "__main__":
    unittest.main()
