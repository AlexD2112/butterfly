import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db


class TestNpcSheets(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        self.game_id = db.create_game("multiplayer", "petrograd_1917", "red_admiral")

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_create_and_retrieve_an_npc(self):
        npc_id = db.create_npc(
            self.game_id, "lenin", "Vladimir Lenin",
            personality="Impatient, ideologically rigid, contemptuous of half-measures.",
            traits=["decisive", "ruthless", "persuasive"],
        )
        npc = db.get_npc(npc_id)
        self.assertEqual(npc["key"], "lenin")
        self.assertEqual(npc["name"], "Vladimir Lenin")
        import json
        self.assertEqual(json.loads(npc["traits_json"]), ["decisive", "ruthless", "persuasive"])
        self.assertEqual(json.loads(npc["memory_json"]), [])

    def _make_server_with_two_rooms(self):
        admin = db.get_or_create_discord_user("999", "Admin", None)
        server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        room_a = db.create_room(server_id, "Room A")
        room_b = db.create_room(server_id, "Room B")
        return db.get_default_location(room_a)["id"], db.get_default_location(room_b)["id"], room_a, room_b

    def test_npcs_at_location_reflects_placement(self):
        loc_a, loc_b, room_a, _ = self._make_server_with_two_rooms()
        npc_id = db.create_npc(self.game_id, "lenin", "Vladimir Lenin", room_id=room_a, location_id=loc_a)
        self.assertEqual([n["id"] for n in db.npcs_at_location(loc_a)], [npc_id])
        self.assertEqual(db.npcs_at_location(loc_b), [])

    def test_move_npc_updates_location(self):
        loc_a, loc_b, room_a, room_b = self._make_server_with_two_rooms()
        npc_id = db.create_npc(self.game_id, "lenin", "Vladimir Lenin", room_id=room_a, location_id=loc_a)
        db.move_npc(npc_id, room_id=room_b, location_id=loc_b)
        self.assertEqual(db.npcs_at_location(loc_a), [])
        self.assertEqual([n["id"] for n in db.npcs_at_location(loc_b)], [npc_id])

    def test_add_npc_memory_appends_and_caps(self):
        npc_id = db.create_npc(self.game_id, "lenin", "Vladimir Lenin")
        for i in range(25):
            db.add_npc_memory(npc_id, f"memory {i}", cap=20)
        npc = db.get_npc(npc_id)
        import json
        memory = json.loads(npc["memory_json"])
        self.assertEqual(len(memory), 20)
        self.assertEqual(memory[-1], "memory 24")
        self.assertEqual(memory[0], "memory 5")  # oldest 5 dropped

    def test_build_npc_sheet_text_includes_personality_traits_and_recent_memory(self):
        npc_id = db.create_npc(
            self.game_id, "lenin", "Vladimir Lenin", personality="Impatient.", traits=["decisive"],
        )
        db.add_npc_memory(npc_id, "Argued with Trotsky about timing.")
        npc = db.get_npc(npc_id)
        sheet = db.build_npc_sheet_text(npc)
        self.assertIn("Vladimir Lenin", sheet)
        self.assertIn("Impatient.", sheet)
        self.assertIn("decisive", sheet)
        self.assertIn("Argued with Trotsky", sheet)

    def test_build_npc_sheet_text_handles_a_bare_npc_with_no_personality_or_memory(self):
        npc_id = db.create_npc(self.game_id, "extra", "Background Figure")
        npc = db.get_npc(npc_id)
        sheet = db.build_npc_sheet_text(npc)
        self.assertIn("Background Figure", sheet)


if __name__ == "__main__":
    unittest.main()
