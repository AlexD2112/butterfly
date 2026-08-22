import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db


class TestGamesSchema(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_user(self):
        return db.get_or_create_discord_user("1", "Someone", None)

    def test_create_character_creates_a_matching_single_player_game(self):
        user = self._make_user()
        character_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Trotsky", {"turn_number": 0})
        char = db.get_character(character_id, user["id"])
        self.assertIsNotNone(char["game_id"])

        game = db.get_game(char["game_id"])
        self.assertEqual(game["mode"], "single")
        self.assertEqual(game["setting_key"], "petrograd_1917")
        self.assertEqual(game["system_key"], "red_admiral")
        self.assertIsNone(game["multiplayer_server_id"])

    def test_get_game_characters_returns_the_roster(self):
        user = self._make_user()
        character_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Trotsky", {"turn_number": 0})
        char = db.get_character(character_id, user["id"])
        roster = db.get_game_characters(char["game_id"])
        self.assertEqual([r["id"] for r in roster], [character_id])

    def test_create_game_directly_for_multiplayer_use(self):
        game_id = db.create_game("multiplayer", "petrograd_1917", "red_admiral", multiplayer_server_id=42)
        game = db.get_game(game_id)
        self.assertEqual(game["mode"], "multiplayer")
        self.assertEqual(game["multiplayer_server_id"], 42)

    def test_record_turn_tags_the_turn_with_its_characters_game_id(self):
        user = self._make_user()
        character_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Trotsky", {"turn_number": 0})
        char = db.get_character(character_id, user["id"])
        db.record_turn(character_id, 0, "I act.", "Something happens.", "luna", [])

        conn = db.get_db()
        row = conn.execute("SELECT game_id FROM turns WHERE character_id = ?", (character_id,)).fetchone()
        conn.close()
        self.assertEqual(row["game_id"], char["game_id"])

    def test_backfill_migrates_pre_existing_characters_without_losing_them(self):
        user = self._make_user()

        # Simulate a pre-migration row: insert directly, bypassing create_character (which now
        # always sets game_id), to reproduce what a real old database looks like.
        conn = db.get_db()
        import json, time
        cur = conn.execute(
            "INSERT INTO characters (user_id, setting_key, system_key, name, is_premade, state_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (user["id"], "rome_59_bce", "apollo", "Caesar", json.dumps({"turn_number": 2}), time.time(), time.time()),
        )
        character_id = cur.lastrowid
        conn.execute(
            "INSERT INTO turns (character_id, turn_number, player_input, narration, model_key, debug_events_json, created_at) "
            "VALUES (?, 0, 'in', 'out', 'luna', '[]', ?)",
            (character_id, time.time()),
        )
        conn.commit()
        conn.close()

        # Confirm it really is unmigrated before we touch it.
        conn = db.get_db()
        before = conn.execute("SELECT game_id FROM characters WHERE id = ?", (character_id,)).fetchone()
        conn.close()
        self.assertIsNone(before["game_id"])

        # Re-running init_db() is what a real process restart does - this is the actual migration path.
        db.init_db()

        char = db.get_character(character_id, user["id"])
        self.assertIsNotNone(char["game_id"])
        game = db.get_game(char["game_id"])
        self.assertEqual(game["mode"], "single")
        self.assertEqual(game["setting_key"], "rome_59_bce")

        conn = db.get_db()
        turn_row = conn.execute("SELECT game_id FROM turns WHERE character_id = ?", (character_id,)).fetchone()
        conn.close()
        self.assertEqual(turn_row["game_id"], char["game_id"])

    def test_backfill_is_idempotent_across_repeated_init_db_calls(self):
        user = self._make_user()
        character_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", "Trotsky", {"turn_number": 0})
        char_before = db.get_character(character_id, user["id"])

        db.init_db()
        db.init_db()

        char_after = db.get_character(character_id, user["id"])
        self.assertEqual(char_before["game_id"], char_after["game_id"])

        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
        conn.close()
        self.assertEqual(count, 1, "re-running init_db() must not create duplicate games for an already-migrated character")


if __name__ == "__main__":
    unittest.main()
