import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db


class TestDiscordLinking(unittest.TestCase):
    """Pure data accessors for the Discord bot's Category/Channel/Thread mapping (2026-09-03) - no
    Discord API involved, just persistence. See discord_bot.py for how these get used."""

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

    def test_server_starts_unlinked(self):
        server = db.get_multiplayer_server(self.server_id)
        self.assertIsNone(server["discord_guild_id"])
        self.assertIsNone(server["discord_category_id"])

    def test_link_and_look_up_a_server_by_category(self):
        db.link_multiplayer_server_to_discord(self.server_id, "guild-1", "cat-1")
        server = db.get_multiplayer_server(self.server_id)
        self.assertEqual(server["discord_guild_id"], "guild-1")
        self.assertEqual(server["discord_category_id"], "cat-1")
        found = db.get_multiplayer_server_by_discord_category("cat-1")
        self.assertEqual(found["id"], self.server_id)
        self.assertIsNone(db.get_multiplayer_server_by_discord_category("unknown-cat"))

    def test_link_and_look_up_a_location_by_channel(self):
        db.link_location_to_discord(self.location_id, "chan-1", "wh-1", "wh-token-1")
        location = db.get_location(self.location_id)
        self.assertEqual(location["discord_channel_id"], "chan-1")
        self.assertEqual(location["discord_webhook_id"], "wh-1")
        self.assertEqual(location["discord_webhook_token"], "wh-token-1")
        found = db.get_location_by_discord_channel("chan-1")
        self.assertEqual(found["id"], self.location_id)
        self.assertIsNone(db.get_location_by_discord_channel("unknown-chan"))

    def test_link_and_look_up_a_subspace_by_thread(self):
        extra_subspace_id = db.create_subspace(self.location_id, "Extra")
        db.link_subspace_to_discord_thread(extra_subspace_id, "thread-1")
        subspace = db.get_subspace(extra_subspace_id)
        self.assertEqual(subspace["discord_thread_id"], "thread-1")
        found = db.get_subspace_by_discord_thread("thread-1")
        self.assertEqual(found["id"], extra_subspace_id)
        # The default subspace is never a thread - stays unlinked (it IS the location's own channel).
        self.assertIsNone(db.get_subspace(self.subspace_id)["discord_thread_id"])
        self.assertIsNone(db.get_subspace_by_discord_thread("unknown-thread"))


if __name__ == "__main__":
    unittest.main()
