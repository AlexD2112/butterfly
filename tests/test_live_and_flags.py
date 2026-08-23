import queue
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import live
import multiplayer
import providers


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class TestLivePubSub(unittest.TestCase):
    """live.py directly - no Flask, no DB, pure pub/sub."""

    def test_subscribing_and_publishing_a_subspace_event_delivers_it(self):
        q = live.subscribe(subspace_id=1, server_id=1)
        live.publish_subspace_event(1, {"hello": "world"})
        event = q.get_nowait()
        self.assertEqual(event, {"hello": "world"})

    def test_publishing_a_server_event_delivers_to_a_subscriber_registered_under_that_server(self):
        q = live.subscribe(subspace_id=1, server_id=42)
        live.publish_server_event(42, {"type": "rumor_broadcast"})
        event = q.get_nowait()
        self.assertEqual(event["type"], "rumor_broadcast")

    def test_unsubscribed_queue_receives_nothing(self):
        q = live.subscribe(subspace_id=2, server_id=2)
        live.unsubscribe(2, 2, q)
        live.publish_subspace_event(2, {"x": 1})
        with self.assertRaises(queue.Empty):
            q.get_nowait()

    def test_events_are_scoped_to_the_right_subspace_not_leaked_to_others(self):
        q_a = live.subscribe(subspace_id=10, server_id=99)
        q_b = live.subscribe(subspace_id=11, server_id=99)
        live.publish_subspace_event(10, {"only": "for-a"})
        self.assertEqual(q_a.get_nowait(), {"only": "for-a"})
        with self.assertRaises(queue.Empty):
            q_b.get_nowait()

    def test_multiple_subscribers_to_the_same_subspace_all_receive_the_event(self):
        q1 = live.subscribe(subspace_id=20, server_id=20)
        q2 = live.subscribe(subspace_id=20, server_id=20)
        live.publish_subspace_event(20, {"broadcast": True})
        self.assertEqual(q1.get_nowait(), {"broadcast": True})
        self.assertEqual(q2.get_nowait(), {"broadcast": True})


class FakeMessageProvider:
    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        if force_tool_name == "decide_response":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "j1", "name": "decide_response",
                                             "input": {"responds": False, "responder": None, "reasoning": "banter"}}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=_usage())


class TestMultiplayerLivePublishing(unittest.TestCase):
    """Confirms multiplayer.py's functions actually publish events for real subscribers, not just
    that live.py itself works in isolation."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]
        user = db.get_or_create_discord_user("2", "Player", None)
        self.char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Player", {"turn_number": 0}, game_id=self.game["id"])
        db.move_character(self.char_id, self.server_id, self.location_id, self.subspace_id)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_sending_a_message_publishes_a_live_event_for_the_subspace(self):
        q = live.subscribe(self.subspace_id, self.server_id)
        fake = FakeMessageProvider()
        multiplayer.handle_message(fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id, self.char_id, "Hello room.")
        event = q.get_nowait()
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["player_input"], "Hello room.")

    def test_mark_concluded_publishes_the_conclusion_event(self):
        q = live.subscribe(self.subspace_id, self.server_id)
        multiplayer.mark_concluded(db.get_character(self.char_id, db.get_character_owner(self.char_id)["id"]), self.location_id, self.subspace_id)
        event = q.get_nowait()
        self.assertEqual(event["kind"], "thread_concluded")


class ScriptedFlagProvider:
    """Shaped for engine.run_turn's tool loop: first call proposes a state update firing a canon
    event (or, for the routine case, nothing notable), second call (after tool results are appended)
    returns plain narration with no further tool calls, ending the loop. Also answers the
    combat-start classifier that always runs after a free action resolves."""

    def __init__(self, fire_canon_event=False):
        self.fire_canon_event = fire_canon_event
        self.calls = 0

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        if force_tool_name == "decide_combat":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c1", "name": "decide_combat",
                                             "input": {"started": False, "participants": [], "reasoning": "no threat"}}],
                usage=_usage(),
            )
        self.calls += 1
        if self.calls == 1:
            update = {"reasoning": "a significant turn of events"}
            if self.fire_canon_event:
                update["canon_event_triggered"] = "lenin_returns_to_petrograd"
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "t1", "name": "propose_state_update", "input": update}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=["Word spreads through the city."], tool_calls=[], usage=_usage())

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestActionFlags(unittest.TestCase):
    """resolve_action's significance flag - free (council.detect_stakes_signals), no new AI call."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]
        user = db.get_or_create_discord_user("2", "Player", None)
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        self.char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Player", state, game_id=self.game["id"])
        db.move_character(self.char_id, self.server_id, self.location_id, self.subspace_id)

        # resolve_action also runs multiplayer.detect_new_subspace (2026-08-22) after a free action,
        # which resolves its own judge provider via providers.get_provider - patched here to raise
        # ProviderError so it gracefully skips (same fallback resolve_action already has), keeping
        # these flag-focused tests from depending on real environment configuration or making a real
        # API call.
        self._provider_patcher = patch.object(providers, "get_provider", side_effect=providers.ProviderError("not configured"))
        self._provider_patcher.start()

    def tearDown(self):
        self._provider_patcher.stop()
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _char_row(self):
        return db.get_character(self.char_id, db.get_character_owner(self.char_id)["id"])

    def test_an_action_that_fires_a_canon_event_is_flagged(self):
        fake = ScriptedFlagProvider(fire_canon_event=True)
        result = multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(), self.location_id, self.subspace_id,
            "I make a bold declaration.", fake.new_conversation(),
        )
        action_row = db.get_turn_by_id(result["turn_id"])
        self.assertTrue(action_row["flagged"])
        self.assertIn("canon event", action_row["flag_reason"])

    def test_a_routine_action_with_no_signals_is_not_flagged(self):
        fake = ScriptedFlagProvider(fire_canon_event=False)
        result = multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(), self.location_id, self.subspace_id,
            "I look around.", fake.new_conversation(),
        )
        action_row = db.get_turn_by_id(result["turn_id"])
        self.assertFalse(action_row["flagged"])
        self.assertIsNone(action_row["flag_reason"])


if __name__ == "__main__":
    unittest.main()
