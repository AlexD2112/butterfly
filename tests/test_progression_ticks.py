import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import multiplayer
import providers
import scheduler


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedSweepProvider:
    def __init__(self, should_conclude=False):
        self.should_conclude = should_conclude
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        if force_tool_name == "decide_sweep":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "s1", "name": "decide_sweep",
                                             "input": {"should_conclude": self.should_conclude, "reasoning": "scripted"}}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=_usage())


class TestAdvanceDateString(unittest.TestCase):
    def test_day_advances_by_one(self):
        self.assertEqual(multiplayer.advance_date_string("1917-09-25", "day"), "1917-09-26")

    def test_week_advances_by_seven(self):
        self.assertEqual(multiplayer.advance_date_string("1917-09-25", "week"), "1917-10-02")

    def test_month_advances_by_thirty_days(self):
        self.assertEqual(multiplayer.advance_date_string("1917-09-25", "month"), "1917-10-25")

    def test_crosses_year_boundary_correctly(self):
        self.assertEqual(multiplayer.advance_date_string("1917-12-31", "day"), "1918-01-01")

    def test_unparseable_date_is_returned_unchanged_rather_than_crashing(self):
        self.assertEqual(multiplayer.advance_date_string(None, "day"), None)
        self.assertEqual(multiplayer.advance_date_string("not-a-date", "day"), "not-a-date")


class TestServerTickState(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        initial_date = engine.load_premade_character_state("petrograd_1917")["date"]
        self.server_id = db.create_multiplayer_server(
            "Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"], initial_date=initial_date,
        )

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_server_is_seeded_with_the_settings_real_starting_date(self):
        server = db.get_multiplayer_server(self.server_id)
        self.assertEqual(server["current_date"], engine.load_premade_character_state("petrograd_1917")["date"])

    def test_last_tick_at_is_seeded_at_creation_not_left_null(self):
        server = db.get_multiplayer_server(self.server_id)
        self.assertIsNotNone(server["last_tick_at"])

    def test_set_server_tick_state_updates_both_fields(self):
        db.set_server_tick_state(self.server_id, "1917-09-26", 12345.0)
        server = db.get_multiplayer_server(self.server_id)
        self.assertEqual(server["current_date"], "1917-09-26")
        self.assertEqual(server["last_tick_at"], 12345.0)


class TestSweepServer(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name, room_id, location_id):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user(discord_id, name, None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", name, state, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, room_id, location_id)
        return char_id

    def test_empty_locations_are_skipped_entirely(self):
        db.create_room(self.server_id, "Empty Room")
        fake = ScriptedSweepProvider()
        results = multiplayer.sweep_server(fake, "gpt-5.6-luna", self.server_id)
        self.assertEqual(results, [])
        self.assertEqual(fake.calls, [])

    def test_occupied_location_gets_checked_and_concluded_if_the_classifier_says_so(self):
        room_id = db.create_room(self.server_id, "Room A")
        location_id = db.get_default_location(room_id)["id"]
        char_id = self._make_character("2", "Player", room_id, location_id)

        fake = ScriptedSweepProvider(should_conclude=True)
        results = multiplayer.sweep_server(fake, "gpt-5.6-luna", self.server_id)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["concluded"])
        messages = db.get_room_messages(location_id, char_id)
        self.assertTrue(any(m["kind"] == "thread_concluded" for m in messages))

    def test_occupied_location_left_open_if_classifier_says_still_ongoing(self):
        room_id = db.create_room(self.server_id, "Room A")
        location_id = db.get_default_location(room_id)["id"]
        self._make_character("2", "Player", room_id, location_id)

        fake = ScriptedSweepProvider(should_conclude=False)
        results = multiplayer.sweep_server(fake, "gpt-5.6-luna", self.server_id)
        self.assertFalse(results[0]["concluded"])

    def test_sweeps_multiple_occupied_locations_across_multiple_rooms(self):
        room_a = db.create_room(self.server_id, "Room A")
        room_b = db.create_room(self.server_id, "Room B")
        self._make_character("2", "PlayerA", room_a, db.get_default_location(room_a)["id"])
        self._make_character("3", "PlayerB", room_b, db.get_default_location(room_b)["id"])

        fake = ScriptedSweepProvider(should_conclude=False)
        results = multiplayer.sweep_server(fake, "gpt-5.6-luna", self.server_id)
        self.assertEqual(len(results), 2)


class TestTickHeartbeat(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        self.admin = db.get_or_create_discord_user("1", "Admin", None)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_a_freshly_created_server_is_not_due_yet(self):
        server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"])
        with patch.object(scheduler, "fire_tick") as mock_fire:
            scheduler.run_tick_heartbeat()
        mock_fire.assert_not_called()

    def test_a_server_past_its_progression_rate_is_due(self):
        server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 0.0, self.admin["id"])
        # progression_rate_hours=0.0 means "due immediately" - simplest way to test the due-check
        # without manipulating time.time() itself.
        with patch.object(scheduler, "fire_tick") as mock_fire:
            scheduler.run_tick_heartbeat()
        mock_fire.assert_called_once_with(server_id)

    def test_fire_tick_advances_the_date_even_if_no_judge_provider_is_configured(self):
        initial_date = engine.load_premade_character_state("petrograd_1917")["date"]
        server_id = db.create_multiplayer_server(
            "Test", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"], initial_date=initial_date,
        )
        with patch.object(providers, "get_provider", side_effect=providers.ProviderError("no key")):
            scheduler.fire_tick(server_id)
        server = db.get_multiplayer_server(server_id)
        self.assertEqual(server["current_date"], multiplayer.advance_date_string(initial_date, "day"))

    def test_fire_tick_runs_the_sweep_and_bills_present_players(self):
        initial_date = engine.load_premade_character_state("petrograd_1917")["date"]
        server_id = db.create_multiplayer_server(
            "Test", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"], initial_date=initial_date,
        )
        game = db.get_game_for_server(server_id)
        room_id = db.create_room(server_id, "Room A")
        location_id = db.get_default_location(room_id)["id"]

        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user("2", "Player", None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Player", state, game_id=game["id"])
        db.move_character(char_id, server_id, room_id, location_id)

        budget_before = db.get_budget_status(user["id"])
        fake = ScriptedSweepProvider(should_conclude=False)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            scheduler.fire_tick(server_id)
        budget_after = db.get_budget_status(user["id"])
        self.assertLess(budget_after["remaining_usd"], budget_before["remaining_usd"])


if __name__ == "__main__":
    unittest.main()
