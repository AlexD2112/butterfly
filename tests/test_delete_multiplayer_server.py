import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import engine
import server


class TestDeleteMultiplayerServerCascade(unittest.TestCase):
    """db.delete_multiplayer_server directly - builds one fully-populated server (locations,
    subspaces, NPCs, characters, combat, conclusions, rumors, usage) and confirms every piece of it
    is actually gone afterward, that single-player data is untouched, and that billing history
    survives with its character_id nulled rather than being deleted outright."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, user_id, game_id, name):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        return db.create_character(user_id, "petrograd_1917", "red_admiral", name, state, game_id=game_id)

    def test_delete_cascades_through_everything_and_preserves_billing_history(self):
        admin = db.get_or_create_discord_user("1", "Admin", None)
        server_id = db.create_multiplayer_server(
            "Doomed Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"], initial_date="1917-09-25",
        )
        game = db.get_game_for_server(server_id)
        location_id = db.create_location(server_id, "Location A")
        main_subspace_id = db.get_default_subspace(location_id)["id"]
        second_subspace_id = db.create_subspace(location_id, "Second Spot")
        npc_id = db.create_npc(game["id"], "old_sailor", "Old Sailor Ivan", location_id=location_id, subspace_id=main_subspace_id)

        char_a = self._make_character(admin["id"], game["id"], "PlayerA")
        char_b = self._make_character(admin["id"], game["id"], "PlayerB")
        db.move_character(char_a, server_id, location_id, main_subspace_id)
        db.move_character(char_b, server_id, location_id, main_subspace_id)

        # A message + a reply, so turns.reply_to_turn_id (self-referential FK) is exercised by the
        # bulk delete, not just a simple case.
        msg_turn_id = db.record_subspace_message(game["id"], location_id, main_subspace_id, char_a, "message", "Hello.")
        db.record_subspace_message(
            game["id"], location_id, main_subspace_id, char_a, "npc_reply", "Well met.",
            reply_to_turn_id=msg_turn_id, responder="old_sailor",
        )

        encounter_id = db.create_combat_encounter(
            location_id, main_subspace_id,
            [{"character_id": char_a, "initiative_roll": 15}, {"npc_key": "old_sailor", "initiative_roll": 10}],
        )
        db.mark_subspace_concluded_by(main_subspace_id, char_a)

        rumor_id = db.create_rumor(server_id, location_id, main_subspace_id, "Something happened.", 3, 3)
        db.publish_rumor_broadcast(server_id, "Word is spreading.", 1)

        db.record_usage(admin["id"], char_a, "openai", "gpt-5.6-luna", 10, 5, 0, 0, 0.001)

        # A completely unrelated single-player character/game, to confirm it survives untouched.
        sp_state = engine.load_premade_character_state("petrograd_1917")
        sp_state.pop("_is_new", None)
        sp_char_id = db.create_character(admin["id"], "petrograd_1917", "red_admiral", "Solo Player", sp_state)
        db.record_turn(sp_char_id, 0, "solo input", "solo narration", "luna", [])

        db.delete_multiplayer_server(server_id)

        conn = db.get_db()
        self.assertIsNone(conn.execute("SELECT id FROM multiplayer_servers WHERE id = ?", (server_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM games WHERE id = ?", (game["id"],)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM locations WHERE server_id = ?", (server_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM subspaces WHERE id = ?", (main_subspace_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM subspaces WHERE id = ?", (second_subspace_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM npcs WHERE id = ?", (npc_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT * FROM characters WHERE id IN (?, ?)", (char_a, char_b)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM turns WHERE game_id = ?", (game["id"],)).fetchone()["c"], 0)
        self.assertIsNone(conn.execute("SELECT character_id FROM location_occupancy WHERE character_id IN (?, ?)", (char_a, char_b)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM combat_encounters WHERE id = ?", (encounter_id,)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM combat_participants WHERE encounter_id = ?", (encounter_id,)).fetchone()["c"], 0)
        self.assertIsNone(conn.execute("SELECT * FROM subspace_conclusions WHERE subspace_id = ?", (main_subspace_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM rumors WHERE id = ?", (rumor_id,)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM rumor_broadcasts WHERE server_id = ?", (server_id,)).fetchone()["c"], 0)

        # Billing history survives - not deleted, just detached from the now-gone character.
        usage_row = conn.execute("SELECT character_id, cost_usd FROM usage_events WHERE user_id = ?", (admin["id"],)).fetchone()
        self.assertIsNotNone(usage_row)
        self.assertIsNone(usage_row["character_id"])
        self.assertEqual(usage_row["cost_usd"], 0.001)

        # Single-player data is completely untouched.
        self.assertIsNotNone(conn.execute("SELECT id FROM characters WHERE id = ?", (sp_char_id,)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM turns WHERE character_id = ?", (sp_char_id,)).fetchone()["c"], 1)
        conn.close()

    def test_delete_unknown_server_is_a_harmless_noop(self):
        # No exception, no error - db-level delete doesn't need to distinguish "already gone" from
        # "never existed" (server.py's route is what returns 404 for a client-facing unknown id).
        db.delete_multiplayer_server(999999)

    def test_delete_succeeds_when_a_character_dynamically_created_a_subspace(self):
        """Real bug, found live (2026-09-03) while testing the Discord bot's action pipeline against a
        real server: subspaces.created_by_character_id (set on a dynamically-created subspace - see
        create_subspace/multiplayer.detect_new_space) also REFERENCES characters(id), but characters
        were deleted before that reference was cleared, same as usage_events would have been had it
        not already been nulled - any server that ever had even one dynamically-created subspace threw
        a real sqlite3.IntegrityError on delete. This fixture is exactly what
        test_delete_cascades_through_everything_and_preserves_billing_history's didn't happen to
        cover: a subspace created via the 'dynamic' path, attributed to a character being deleted."""
        admin = db.get_or_create_discord_user("2", "Admin2", None)
        server_id = db.create_multiplayer_server(
            "Doomed Server 2", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"], initial_date="1917-09-25",
        )
        game = db.get_game_for_server(server_id)
        location_id = db.create_location(server_id, "Location A")
        char_id = self._make_character(admin["id"], game["id"], "Explorer")
        dynamic_subspace_id = db.create_subspace(location_id, "A Hidden Alcove", origin="dynamic", created_by_character_id=char_id)

        db.delete_multiplayer_server(server_id)  # must not raise

        conn = db.get_db()
        self.assertIsNone(conn.execute("SELECT id FROM multiplayer_servers WHERE id = ?", (server_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM subspaces WHERE id = ?", (dynamic_subspace_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT id FROM characters WHERE id = ?", (char_id,)).fetchone())
        conn.close()


class TestDeleteMultiplayerServerRoute(unittest.TestCase):
    """Route-level: admin gating, 404 on unknown server, and that a deleted server actually
    disappears from the public server list afterward."""

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

    def _login(self, discord_id, username, is_admin=False):
        user = db.get_or_create_discord_user(discord_id, username, None)
        if is_admin:
            db.set_is_admin(user["id"], True)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        return user

    def test_non_admin_cannot_delete_a_server(self):
        self._login("1", "Admin", is_admin=True)
        server_id = self.client.post(
            "/api/admin/multiplayer_servers", json={"name": "X", "setting_key": "petrograd_1917"},
        ).get_json()["server_id"]

        self._login("2", "Regular", is_admin=False)
        res = self.client.delete(f"/api/admin/multiplayer_servers/{server_id}")
        self.assertEqual(res.status_code, 403)
        # Still there, unaffected by the rejected attempt.
        self.assertIsNotNone(db.get_multiplayer_server(server_id))

    def test_unknown_server_404s(self):
        self._login("1", "Admin", is_admin=True)
        res = self.client.delete("/api/admin/multiplayer_servers/999999")
        self.assertEqual(res.status_code, 404)

    def test_admin_delete_removes_it_from_the_server_list(self):
        self._login("1", "Admin", is_admin=True)
        server_id = self.client.post(
            "/api/admin/multiplayer_servers", json={"name": "Doomed", "setting_key": "petrograd_1917"},
        ).get_json()["server_id"]

        list_before = self.client.get("/api/multiplayer/servers").get_json()["servers"]
        self.assertTrue(any(s["id"] == server_id for s in list_before))

        res = self.client.delete(f"/api/admin/multiplayer_servers/{server_id}")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])

        list_after = self.client.get("/api/multiplayer/servers").get_json()["servers"]
        self.assertFalse(any(s["id"] == server_id for s in list_after))


if __name__ == "__main__":
    unittest.main()
