import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db


class TestRoomsAndLocations(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        self.admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server(
            "Test Server", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"],
        )

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name):
        game = db.get_game_for_server(self.server_id)
        user = db.get_or_create_discord_user(discord_id, name, None)
        return db.create_character(user["id"], "petrograd_1917", "red_admiral", name, {"turn_number": 0}, game_id=game["id"])

    def test_creating_a_server_creates_exactly_one_backing_multiplayer_game(self):
        game = db.get_game_for_server(self.server_id)
        self.assertIsNotNone(game)
        self.assertEqual(game["mode"], "multiplayer")
        self.assertEqual(game["multiplayer_server_id"], self.server_id)

    def test_creating_a_room_creates_a_default_location(self):
        room_id = db.create_room(self.server_id, "The Smolny Institute")
        locations = db.list_locations(room_id)
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["name"], "Main")
        self.assertEqual(db.get_default_location(room_id)["id"], locations[0]["id"])

    def test_a_room_can_have_multiple_locations(self):
        room_id = db.create_room(self.server_id, "The Smolny Institute")
        db.create_location(room_id, "The Corridor")
        db.create_location(room_id, "Trotsky's Office")
        self.assertEqual(len(db.list_locations(room_id)), 3)  # Main + the 2 new ones

    def test_move_character_places_them_in_exactly_one_room(self):
        room_a = db.create_room(self.server_id, "Room A")
        room_b = db.create_room(self.server_id, "Room B")
        char_id = self._make_character("2", "Player1")

        db.move_character(char_id, self.server_id, room_a, db.get_default_location(room_a)["id"])
        self.assertEqual(db.get_character_location(char_id)["room_id"], room_a)

        db.move_character(char_id, self.server_id, room_b, db.get_default_location(room_b)["id"])
        loc = db.get_character_location(char_id)
        self.assertEqual(loc["room_id"], room_b)
        # still exactly one row - not accumulating history
        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) AS n FROM room_occupancy WHERE character_id = ?", (char_id,)).fetchone()["n"]
        conn.close()
        self.assertEqual(count, 1)

    def test_moving_within_the_same_room_does_not_reset_joined_room_at(self):
        room = db.create_room(self.server_id, "Room A")
        loc_a = db.get_default_location(room)["id"]
        loc_b = db.create_location(room, "Side room")
        char_id = self._make_character("2", "Player1")

        db.move_character(char_id, self.server_id, room, loc_a)
        first = db.get_character_location(char_id)["joined_room_at"]
        db.move_character(char_id, self.server_id, room, loc_b)
        second = db.get_character_location(char_id)["joined_room_at"]
        self.assertEqual(first, second)

    def test_characters_at_location_reflects_current_occupancy(self):
        room = db.create_room(self.server_id, "Room A")
        loc = db.get_default_location(room)["id"]
        c1 = self._make_character("2", "Player1")
        c2 = self._make_character("3", "Player2")
        db.move_character(c1, self.server_id, room, loc)
        db.move_character(c2, self.server_id, room, loc)
        self.assertCountEqual(db.characters_at_location(loc), [c1, c2])

    def test_a_message_is_only_visible_to_characters_present_when_it_was_sent(self):
        game = db.get_game_for_server(self.server_id)
        room = db.create_room(self.server_id, "Room A")
        loc = db.get_default_location(room)["id"]
        early = self._make_character("2", "Early")
        late = self._make_character("3", "Late")

        db.move_character(early, self.server_id, room, loc)
        turn_id = db.record_room_message(game["id"], room, loc, early, "message", "Hello, is anyone here?")

        # Late arrives AFTER the message was sent.
        db.move_character(late, self.server_id, room, loc)

        early_view = db.get_room_messages(loc, early)
        late_view = db.get_room_messages(loc, late)
        self.assertEqual([m["id"] for m in early_view], [turn_id])
        self.assertEqual(late_view, [], "a late arrival must not see messages sent before they got there")

    def test_leaving_does_not_retroactively_remove_visibility_of_what_was_already_seen(self):
        game = db.get_game_for_server(self.server_id)
        room = db.create_room(self.server_id, "Room A")
        other_room = db.create_room(self.server_id, "Room B")
        loc = db.get_default_location(room)["id"]
        char = self._make_character("2", "Player1")

        db.move_character(char, self.server_id, room, loc)
        turn_id = db.record_room_message(game["id"], room, loc, char, "message", "Something happens.")
        db.move_character(char, self.server_id, other_room, db.get_default_location(other_room)["id"])

        # Query the OLD location directly (as if reviewing history) - still shows they saw it.
        view = db.get_room_messages(loc, char)
        self.assertEqual([m["id"] for m in view], [turn_id])

    def test_messages_after_a_new_arrival_are_visible_to_them(self):
        game = db.get_game_for_server(self.server_id)
        room = db.create_room(self.server_id, "Room A")
        loc = db.get_default_location(room)["id"]
        early = self._make_character("2", "Early")
        late = self._make_character("3", "Late")

        db.move_character(early, self.server_id, room, loc)
        db.record_room_message(game["id"], room, loc, early, "message", "Before Late arrives.")
        db.move_character(late, self.server_id, room, loc)
        after_id = db.record_room_message(game["id"], room, loc, early, "message", "After Late arrives.")

        late_view = db.get_room_messages(loc, late)
        self.assertEqual([m["id"] for m in late_view], [after_id])


if __name__ == "__main__":
    unittest.main()
