import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import multiplayer
import providers


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedRumorProvider:
    def __init__(self, importance=3, publicity=3, summary="Something happened."):
        self.importance = importance
        self.publicity = publicity
        self.summary = summary
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        if force_tool_name == "submit_rumor":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "r1", "name": "submit_rumor", "input": {
                    "importance": self.importance, "publicity": self.publicity, "summary": self.summary,
                }}],
                usage=_usage(),
            )
        if not tool_specs:
            return providers.TurnResult(text_parts=[self.summary], tool_calls=[], usage=_usage())
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=_usage())


class TestRumorStorage(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_create_and_list_unpublished_rumors(self):
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "A rumor.", 3, 2)
        unpublished = db.get_unpublished_rumors(self.server_id)
        self.assertEqual(len(unpublished), 1)
        self.assertEqual(unpublished[0]["summary"], "A rumor.")
        self.assertIsNone(unpublished[0]["published_at"])

    def test_publishing_marks_rumors_published_and_creates_a_broadcast(self):
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "Rumor one.", 3, 2)
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "Rumor two.", 4, 5)
        broadcast_id = db.publish_rumor_broadcast(self.server_id, "Compiled broadcast text.", 2)

        self.assertEqual(db.get_unpublished_rumors(self.server_id), [])
        broadcasts = db.list_rumor_broadcasts(self.server_id)
        self.assertEqual(len(broadcasts), 1)
        self.assertEqual(broadcasts[0]["id"], broadcast_id)
        self.assertEqual(broadcasts[0]["rumor_count"], 2)

    def test_a_later_rumor_is_not_retroactively_published_by_an_earlier_broadcast(self):
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "Before.", 3, 2)
        db.publish_rumor_broadcast(self.server_id, "First broadcast.", 1)
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "After.", 3, 2)
        unpublished = db.get_unpublished_rumors(self.server_id)
        self.assertEqual([r["summary"] for r in unpublished], ["After."])


class TestGenerateRumor(unittest.TestCase):
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

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_no_prior_activity_generates_nothing(self):
        fake = ScriptedRumorProvider()
        result = multiplayer.generate_rumor(fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id)
        self.assertIsNone(result)
        self.assertEqual(fake.calls, [])  # not even worth calling the AI

    def test_real_activity_generates_a_stored_rumor(self):
        user = db.get_or_create_discord_user("2", "Player", None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Player", {"turn_number": 0}, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.location_id, self.subspace_id)
        db.record_subspace_message(self.game["id"], self.location_id, self.subspace_id, char_id, "message", "A dramatic confrontation.")

        fake = ScriptedRumorProvider(importance=5, publicity=4, summary="A dramatic confrontation shook the room.")
        rumor_id = multiplayer.generate_rumor(fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id)
        self.assertIsNotNone(rumor_id)
        unpublished = db.get_unpublished_rumors(self.server_id)
        self.assertEqual(len(unpublished), 1)
        self.assertEqual(unpublished[0]["importance"], 5)
        self.assertEqual(unpublished[0]["publicity"], 4)

    def test_empty_summary_stores_nothing(self):
        user = db.get_or_create_discord_user("2", "Player", None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Player", {"turn_number": 0}, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.location_id, self.subspace_id)
        db.record_subspace_message(self.game["id"], self.location_id, self.subspace_id, char_id, "message", "Hello.")

        fake = ScriptedRumorProvider(summary="")
        result = multiplayer.generate_rumor(fake, "gpt-5.6-luna", self.game["id"], self.location_id, self.subspace_id)
        self.assertIsNone(result)
        self.assertEqual(db.get_unpublished_rumors(self.server_id), [])


class TestCompileAndPublishRumors(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_no_unpublished_rumors_means_no_call_and_no_broadcast(self):
        fake = ScriptedRumorProvider()
        result = multiplayer.compile_and_publish_rumors(fake, "gpt-5.6-luna", self.server_id)
        self.assertIsNone(result)
        self.assertEqual(fake.calls, [])
        self.assertEqual(db.list_rumor_broadcasts(self.server_id), [])

    def test_unpublished_rumors_get_compiled_and_published(self):
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "Something happened at the Smolny.", 4, 3)
        db.create_rumor(self.server_id, self.location_id, self.subspace_id, "A quiet whisper about Lenin.", 2, 1)

        fake = ScriptedRumorProvider(summary="The compiled broadcast text.")
        usage_sink = []
        broadcast_id = multiplayer.compile_and_publish_rumors(fake, "gpt-5.6-luna", self.server_id, usage_sink=usage_sink)
        self.assertIsNotNone(broadcast_id)
        self.assertEqual(len(usage_sink), 1)

        broadcasts = db.list_rumor_broadcasts(self.server_id)
        self.assertEqual(len(broadcasts), 1)
        self.assertEqual(broadcasts[0]["rumor_count"], 2)
        self.assertEqual(db.get_unpublished_rumors(self.server_id), [])


if __name__ == "__main__":
    unittest.main()
