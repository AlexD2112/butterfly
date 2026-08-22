import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import providers
import server


class FakeProvider:
    """Always says no NPC/GM responds - simplest fake for exercising the route/budget/lock plumbing
    without needing to script a full judge+reply exchange (multiplayer_judging.py already covers
    that in detail)."""

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        if force_tool_name == "decide_response":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "j1", "name": "decide_response",
                                             "input": {"responds": False, "responder": None, "reasoning": "banter"}}],
                usage={"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
                       "cache_read_tokens": 0, "cache_creation_tokens": 0},
            )
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[],
                                     usage={"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 1, "output_tokens": 1,
                                            "cache_read_tokens": 0, "cache_creation_tokens": 0})


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ActionFakeProvider:
    """A fake shaped for engine.run_turn's tool-calling loop (a plain narration reply, no tool
    calls, so the loop exits after one call) plus a "no combat" answer for the combat-start
    classifier that always runs afterward - exercises the real action-resolution route end to end
    without a live API."""

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
        return providers.TurnResult(text_parts=["You see nothing unusual."], tool_calls=[], usage=_usage())

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestMultiplayerRoutes(unittest.TestCase):
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

    def _login_as_discord_user(self, discord_id, username, is_admin=False):
        user = db.get_or_create_discord_user(discord_id, username, None)
        if is_admin:
            db.set_is_admin(user["id"], True)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        return user

    def test_non_admin_cannot_create_a_multiplayer_server(self):
        self._login_as_discord_user("1", "Regular", is_admin=False)
        res = self.client.post("/api/admin/multiplayer_servers", json={"name": "X", "setting_key": "petrograd_1917"})
        self.assertEqual(res.status_code, 403)

    def test_admin_can_create_server_room_and_location(self):
        self._login_as_discord_user("1", "Admin", is_admin=True)
        server_res = self.client.post(
            "/api/admin/multiplayer_servers",
            json={"name": "Petrograd Test", "setting_key": "petrograd_1917", "progression_speed": "day", "progression_rate_hours": 24},
        )
        self.assertEqual(server_res.status_code, 200)
        server_id = server_res.get_json()["server_id"]

        room_res = self.client.post(f"/api/admin/multiplayer_servers/{server_id}/rooms", json={"name": "The Smolny Institute"})
        self.assertEqual(room_res.status_code, 200)
        room_id = room_res.get_json()["room_id"]

        loc_res = self.client.post(f"/api/admin/rooms/{room_id}/locations", json={"name": "The Corridor"})
        self.assertEqual(loc_res.status_code, 200)

        rooms = self.client.get(f"/api/multiplayer/servers/{server_id}/rooms").get_json()["rooms"]
        self.assertEqual(len(rooms), 1)
        locations = self.client.get(f"/api/multiplayer/rooms/{room_id}/locations").get_json()["locations"]
        self.assertEqual(len(locations), 2)  # default "Main" + "The Corridor"

    def _make_server_room_and_character(self, discord_id="2", username="Player"):
        admin = self._login_as_discord_user("1", "Admin", is_admin=True)
        server_res = self.client.post(
            "/api/admin/multiplayer_servers",
            json={"name": "Petrograd Test", "setting_key": "petrograd_1917"},
        )
        server_id = server_res.get_json()["server_id"]
        room_id = self.client.post(f"/api/admin/multiplayer_servers/{server_id}/rooms", json={"name": "Room A"}).get_json()["room_id"]

        self._login_as_discord_user(discord_id, username)
        char_res = self.client.post(
            "/api/characters",
            json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": server_id},
        )
        character_id = char_res.get_json()["character_id"]
        return server_id, room_id, character_id

    def test_character_created_with_multiplayer_server_id_attaches_to_the_shared_game(self):
        server_id, room_id, character_id = self._make_server_room_and_character()
        char = db.get_character(character_id, db.get_user_by_id(db.get_or_create_discord_user("2", "Player", None)["id"])["id"])
        server_game = db.get_game_for_server(server_id)
        self.assertEqual(char["game_id"], server_game["id"])

    def test_move_and_then_location_messages_reflects_the_move(self):
        server_id, room_id, character_id = self._make_server_room_and_character()
        move_res = self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})
        self.assertEqual(move_res.status_code, 200)

        msgs_res = self.client.get(f"/api/multiplayer/characters/{character_id}/location_messages")
        self.assertEqual(msgs_res.status_code, 200)
        self.assertEqual(msgs_res.get_json()["messages"], [])

    def test_local_user_cannot_send_a_multiplayer_message(self):
        server_id, room_id, character_id = self._make_server_room_and_character()
        self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})

        # Switch to a fresh local/anonymous session (not the Discord user who owns the character) -
        # should 404 as "not found" since ownership is checked before the is_local gate, matching the
        # single-player turn route's own pattern.
        with self.client.session_transaction() as sess:
            sess.pop("user_id", None)
        res = self.client.post(f"/api/multiplayer/characters/{character_id}/send", json={"kind": "message", "content": "hi"})
        self.assertIn(res.status_code, (403, 404))

    def test_action_kind_resolves_through_the_engine_and_is_recorded(self):
        server_id, room_id, character_id = self._make_server_room_and_character()
        self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})

        with patch.object(providers, "get_provider", return_value=(ActionFakeProvider(), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/multiplayer/characters/{character_id}/send",
                json={"kind": "action", "content": "I look around the room.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["kind"], "action")
        self.assertEqual(data["narration"], "You see nothing unusual.")
        self.assertIsNone(data["combat_encounter_id"])

        turns = db.get_turns(character_id)
        self.assertTrue(any(t["kind"] == "action" for t in turns))
        self.assertTrue(any(t["kind"] == "gm_reply" for t in turns))

    def test_a_message_send_records_it_and_returns_budget(self):
        server_id, room_id, character_id = self._make_server_room_and_character()
        self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})

        with patch.object(providers, "get_provider", return_value=(FakeProvider(), "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/multiplayer/characters/{character_id}/send",
                json={"kind": "message", "content": "Just chatting with another player.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsNotNone(data["message_turn_id"])
        self.assertIsNone(data["reply_turn_id"])  # FakeProvider always says no one responds
        self.assertIn("budget", data)

    def test_location_detail_reports_occupants_and_no_combat(self):
        server_id, room_id, character_id = self._make_server_room_and_character(discord_id="2", username="PlayerA")
        move_res = self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})
        location_id = move_res.get_json()["location_id"]

        res = self.client.get(f"/api/multiplayer/locations/{location_id}/detail")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["location"]["id"], location_id)
        self.assertEqual([o["character_id"] for o in data["occupants"]], [character_id])
        self.assertTrue(data["occupants"][0]["name"])  # premade's real in-state name, whatever it is
        self.assertIsNone(data["combat"])
        self.assertEqual(data["conclusion"], {"marked_character_ids": [], "total_present": 1})

    def test_location_detail_unknown_location_404s(self):
        res = self.client.get("/api/multiplayer/locations/999999/detail")
        self.assertEqual(res.status_code, 404)

    def test_my_characters_only_returns_the_current_users_characters_for_that_server(self):
        server_id, room_id, character_id = self._make_server_room_and_character(discord_id="2", username="PlayerA")
        self._login_as_discord_user("3", "PlayerB")
        self.client.post(
            "/api/characters", json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": server_id},
        )

        self._login_as_discord_user("2", "PlayerA")
        res = self.client.get(f"/api/multiplayer/servers/{server_id}/my_characters")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual([c["id"] for c in data["characters"]], [character_id])

    def test_location_messages_include_the_sender_display_name(self):
        server_id, room_id, character_id = self._make_server_room_and_character(discord_id="2", username="PlayerA")
        move_res = self.client.post(f"/api/multiplayer/characters/{character_id}/move", json={"room_id": room_id})
        location_id = move_res.get_json()["location_id"]
        expected_name = db.get_character_unchecked(character_id)["name"]
        with patch.object(providers, "get_provider", return_value=(FakeProvider(), "gpt-5.6-luna")):
            self.client.post(
                f"/api/multiplayer/characters/{character_id}/send",
                json={"kind": "message", "content": "Hello there.", "model_key": "luna"},
            )
        res = self.client.get(f"/api/multiplayer/characters/{character_id}/location_messages")
        messages = res.get_json()["messages"]
        self.assertTrue(any(m["character_name"] == expected_name for m in messages))


if __name__ == "__main__":
    unittest.main()
