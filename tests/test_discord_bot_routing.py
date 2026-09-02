import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import discord_bot


class TestDiscordSafeName(unittest.TestCase):
    def test_simple_name(self):
        self.assertEqual(discord_bot.discord_safe_name("The Smolny Institute"), "the-smolny-institute")

    def test_apostrophe_and_punctuation(self):
        self.assertEqual(discord_bot.discord_safe_name("Trotsky's Office!"), "trotsky-s-office")

    def test_leading_trailing_punctuation_stripped(self):
        self.assertEqual(discord_bot.discord_safe_name("-- Main --"), "main")

    def test_empty_after_sanitizing_falls_back(self):
        self.assertEqual(discord_bot.discord_safe_name("!!!"), "space")

    def test_long_name_truncated_to_100_chars(self):
        long_name = "a" * 150
        result = discord_bot.discord_safe_name(long_name)
        self.assertEqual(len(result), 100)


class TestDiscordBotRouting(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        self.admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.default_subspace_id = db.get_default_subspace(self.location_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_resolve_or_create_user_matches_existing_web_login(self):
        # Same discord_id, same row - a Discord-first and a web-first account are never two things.
        web_user = db.get_or_create_discord_user("42", "WebFirst", None)
        discord_user = discord_bot.resolve_or_create_user("42", "WebFirst (via Discord)", None)
        self.assertEqual(web_user["id"], discord_user["id"])

    def test_resolve_or_create_user_grants_the_same_starting_budget_as_web_signup(self):
        user = discord_bot.resolve_or_create_user("999", "DiscordFirst", None)
        budget = db.get_budget_status(user["id"])
        self.assertEqual(budget["budget_usd"], db.DEFAULT_BUDGET_USD)

    def test_location_for_channel_unlinked_returns_none(self):
        self.assertIsNone(discord_bot.location_for_channel("unknown-channel"))

    def test_location_for_channel_linked(self):
        db.link_location_to_discord(self.location_id, "chan-1", "wh-1", "wh-token-1")
        found = discord_bot.location_for_channel("chan-1")
        self.assertEqual(found["id"], self.location_id)

    def test_route_channel_message_targets_the_default_subspace(self):
        db.link_location_to_discord(self.location_id, "chan-1", "wh-1", "wh-token-1")
        result = discord_bot.route_channel_message("chan-1")
        self.assertIsNotNone(result)
        location, subspace = result
        self.assertEqual(location["id"], self.location_id)
        self.assertEqual(subspace["id"], self.default_subspace_id)

    def test_route_channel_message_unlinked_returns_none(self):
        self.assertIsNone(discord_bot.route_channel_message("unknown-channel"))

    def test_route_thread_message_targets_the_linked_subspace(self):
        extra_id = db.create_subspace(self.location_id, "Extra")
        db.link_location_to_discord(self.location_id, "chan-1", "wh-1", "wh-token-1")
        db.link_subspace_to_discord_thread(extra_id, "thread-1")
        location, subspace = discord_bot.route_thread_message("thread-1", "chan-1")
        self.assertEqual(location["id"], self.location_id)
        self.assertEqual(subspace["id"], extra_id)

    def test_route_thread_message_falls_back_to_parent_channel_when_thread_unlinked(self):
        db.link_location_to_discord(self.location_id, "chan-1", "wh-1", "wh-token-1")
        location, subspace = discord_bot.route_thread_message("some-manual-thread", "chan-1")
        self.assertEqual(location["id"], self.location_id)
        self.assertEqual(subspace["id"], self.default_subspace_id)

    def test_route_thread_message_returns_none_when_nothing_linked(self):
        self.assertIsNone(discord_bot.route_thread_message("unknown-thread", "unknown-channel"))

    def test_resolve_character_for_user_with_none_returns_none(self):
        self.assertIsNone(discord_bot.resolve_character_for_user_in_game(self.admin["id"], self.game["id"]))

    def test_resolve_character_for_user_with_one_returns_it(self):
        char_id = db.create_character(self.admin["id"], "petrograd_1917", "red_admiral", "Test", {"turn_number": 0}, game_id=self.game["id"])
        found = discord_bot.resolve_character_for_user_in_game(self.admin["id"], self.game["id"])
        self.assertEqual(found["id"], char_id)

    def test_resolve_character_for_user_with_multiple_returns_most_recently_updated(self):
        import time
        older_id = db.create_character(self.admin["id"], "petrograd_1917", "red_admiral", "Older", {"turn_number": 0}, game_id=self.game["id"])
        time.sleep(0.01)
        newer_id = db.create_character(self.admin["id"], "petrograd_1917", "red_admiral", "Newer", {"turn_number": 0}, game_id=self.game["id"])
        found = discord_bot.resolve_character_for_user_in_game(self.admin["id"], self.game["id"])
        self.assertEqual(found["id"], newer_id)

    def test_subspace_needs_a_thread_true_for_a_new_non_default_subspace(self):
        extra_id = db.create_subspace(self.location_id, "Extra")
        extra = db.get_subspace(extra_id)
        location = db.get_location(self.location_id)
        self.assertTrue(discord_bot.subspace_needs_a_thread(extra, location))

    def test_subspace_needs_a_thread_false_for_the_default_subspace(self):
        default = db.get_subspace(self.default_subspace_id)
        location = db.get_location(self.location_id)
        self.assertFalse(discord_bot.subspace_needs_a_thread(default, location))

    def test_subspace_needs_a_thread_false_once_already_linked(self):
        extra_id = db.create_subspace(self.location_id, "Extra")
        db.link_subspace_to_discord_thread(extra_id, "thread-1")
        extra = db.get_subspace(extra_id)
        location = db.get_location(self.location_id)
        self.assertFalse(discord_bot.subspace_needs_a_thread(extra, location))


if __name__ == "__main__":
    unittest.main()
