import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import multiplayer
import pricing
import providers
import server


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedContinuationProvider:
    def __init__(self, continues=True):
        self.continues = continues
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        if force_tool_name == "decide_continuation":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c1", "name": "decide_continuation",
                                             "input": {"continues": self.continues, "reasoning": "scripted"}}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=_usage())


class TestMarkConcludedAndStormOutOrchestration(unittest.TestCase):
    """multiplayer.py-level: the actual conclusion/storm-out logic, DB-backed but no HTTP layer."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user(discord_id, name, None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", name, state, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.location_id, self.subspace_id)
        return char_id

    def _char_row(self, char_id):
        conn = db.get_db()
        user_id = conn.execute("SELECT user_id FROM characters WHERE id = ?", (char_id,)).fetchone()["user_id"]
        conn.close()
        return db.get_character(char_id, user_id)

    def test_a_solo_character_marking_concluded_is_immediately_unanimous(self):
        char_id = self._make_character("2", "Solo")
        concluded, marks, total = multiplayer.mark_concluded(self._char_row(char_id), self.location_id, self.subspace_id)
        self.assertTrue(concluded)
        self.assertEqual(marks, 1)
        self.assertEqual(total, 1)

    def test_two_present_needs_both_marks_to_conclude(self):
        char_a = self._make_character("2", "A")
        char_b = self._make_character("3", "B")
        concluded1, marks1, total1 = multiplayer.mark_concluded(self._char_row(char_a), self.location_id, self.subspace_id)
        self.assertFalse(concluded1)
        self.assertEqual((marks1, total1), (1, 2))

        concluded2, marks2, total2 = multiplayer.mark_concluded(self._char_row(char_b), self.location_id, self.subspace_id)
        self.assertTrue(concluded2)
        self.assertEqual((marks2, total2), (2, 2))

    def test_conclusion_logs_a_visible_marker_and_resets_tracking(self):
        char_a = self._make_character("2", "A")
        multiplayer.mark_concluded(self._char_row(char_a), self.location_id, self.subspace_id)
        # solo -> immediately concluded
        messages = db.get_subspace_messages(self.subspace_id, char_a)
        concluded_rows = [m for m in messages if m["kind"] == "thread_concluded"]
        self.assertTrue(concluded_rows)
        # Real bug, found via a live SSE smoke test, not a code review: the note text used to be
        # silently discarded for this kind (record_subspace_message's content routing only covered
        # message/action and npc_reply/gm_reply, missing thread_concluded and storm_out entirely).
        self.assertEqual(concluded_rows[0]["narration"], "The scene concludes.")
        self.assertEqual(db.get_subspace_conclusion_marks(self.subspace_id), set())

    def test_a_third_arrival_does_not_retroactively_count_as_having_marked(self):
        char_a = self._make_character("2", "A")
        char_b = self._make_character("3", "B")
        multiplayer.mark_concluded(self._char_row(char_a), self.location_id, self.subspace_id)
        char_c = self._make_character("4", "C")  # arrives after A marked, before B does
        concluded, marks, total = multiplayer.mark_concluded(self._char_row(char_b), self.location_id, self.subspace_id)
        # Still not unanimous - C hasn't marked, and now counts toward "present"
        self.assertFalse(concluded)
        self.assertEqual(total, 3)

    def test_storm_out_with_final_message_records_it_and_moves_the_character(self):
        char_id = self._make_character("2", "Leaver")
        fake = ScriptedContinuationProvider(continues=True)
        new_subspace_id, auto_concluded = multiplayer.storm_out(
            fake, "gpt-5.6-luna", self._char_row(char_id), self.location_id, self.subspace_id,
            "I storm off in a rage!",
        )
        self.assertEqual(new_subspace_id, self.subspace_id)  # only one subspace - the default itself
        presence = db.get_character_presence(char_id)
        self.assertEqual(presence["subspace_id"], new_subspace_id)

        # No one else was present, so the deterministic zero-remaining path fires - auto-concluded,
        # regardless of the scripted provider's answer (never even called).
        self.assertTrue(auto_concluded)
        self.assertNotIn("decide_continuation", fake.calls)

        # The final flavor message's actual text must land somewhere retrievable - see the
        # content-routing bug fix in db.record_subspace_message (found via a live SSE smoke test).
        messages = db.get_subspace_messages(self.subspace_id, char_id)
        storm_rows = [m for m in messages if m["kind"] == "storm_out"]
        self.assertEqual(len(storm_rows), 1)
        self.assertEqual(storm_rows[0]["player_input"], "I storm off in a rage!")

    def test_storm_out_with_others_remaining_asks_whether_to_continue(self):
        leaver_id = self._make_character("2", "Leaver")
        stayer_id = self._make_character("3", "Stayer")
        fake = ScriptedContinuationProvider(continues=True)
        _, auto_concluded = multiplayer.storm_out(
            fake, "gpt-5.6-luna", self._char_row(leaver_id), self.location_id, self.subspace_id, None,
        )
        self.assertIn("decide_continuation", fake.calls)
        self.assertFalse(auto_concluded)

    def test_storm_out_auto_concludes_when_classifier_says_not_enough_remains(self):
        leaver_id = self._make_character("2", "Leaver")
        stayer_id = self._make_character("3", "Stayer")
        fake = ScriptedContinuationProvider(continues=False)
        _, auto_concluded = multiplayer.storm_out(
            fake, "gpt-5.6-luna", self._char_row(leaver_id), self.location_id, self.subspace_id, None,
        )
        self.assertTrue(auto_concluded)
        self.assertIsNone(db.get_subspace_conclusion_marks(self.subspace_id) or None)  # cleared, i.e. empty set (falsy)

    def test_storm_out_with_no_provider_available_defaults_to_still_ongoing(self):
        leaver_id = self._make_character("2", "Leaver")
        stayer_id = self._make_character("3", "Stayer")
        _, auto_concluded = multiplayer.storm_out(
            None, None, self._char_row(leaver_id), self.location_id, self.subspace_id, None,
        )
        self.assertFalse(auto_concluded)

    def test_storming_out_clears_the_leavers_own_prior_mark(self):
        char_a = self._make_character("2", "A")
        char_b = self._make_character("3", "B")
        multiplayer.mark_concluded(self._char_row(char_a), self.location_id, self.subspace_id)
        self.assertEqual(db.get_subspace_conclusion_marks(self.subspace_id), {char_a})

        fake = ScriptedContinuationProvider(continues=True)
        multiplayer.storm_out(fake, "gpt-5.6-luna", self._char_row(char_a), self.location_id, self.subspace_id, None)
        self.assertEqual(db.get_subspace_conclusion_marks(self.subspace_id), set())


class TestMarkConcludedRoute(unittest.TestCase):
    """Route + billing-model level."""

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

    def _login(self, discord_id, username):
        user = db.get_or_create_discord_user(discord_id, username, None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        return user

    def _setup_server_location_and_two_characters(self):
        admin = self._login("1", "Admin")
        db.set_is_admin(admin["id"], True)
        server_res = self.client.post("/api/admin/multiplayer_servers", json={"name": "Test", "setting_key": "petrograd_1917"})
        server_id = server_res.get_json()["server_id"]
        location_id = self.client.post(f"/api/admin/multiplayer_servers/{server_id}/locations", json={"name": "Location A"}).get_json()["location_id"]

        self._login("2", "PlayerA")
        char_a = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": server_id}).get_json()["character_id"]
        self.client.post(f"/api/multiplayer/characters/{char_a}/move", json={"location_id": location_id})

        # PlayerB's character is built directly via db.create_character, not the /api/characters
        # route - the premade flagship is already claimed by char_a above (claim-locked, 2026-08-23),
        # and this test isn't exercising character creation, just needs a second distinct character.
        player_b = self._login("3", "PlayerB")
        game = db.get_game_for_server(server_id)
        char_b = db.create_character(
            player_b["id"], "petrograd_1917", "red_admiral", "PlayerB's Character", {"turn_number": 0}, game_id=game["id"],
        )
        self.client.post(f"/api/multiplayer/characters/{char_b}/move", json={"location_id": location_id})

        return server_id, location_id, char_a, char_b

    def test_mark_concluded_route_reports_progress_toward_unanimity(self):
        server_id, location_id, char_a, char_b = self._setup_server_location_and_two_characters()
        self._login("2", "PlayerA")
        res = self.client.post(f"/api/multiplayer/characters/{char_a}/mark_concluded", json={})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["concluded"])
        self.assertEqual(data["total_present"], 2)

    def test_storm_out_ambient_billing_splits_cost_at_50_percent_across_remaining_players(self):
        server_id, location_id, char_a, char_b = self._setup_server_location_and_two_characters()
        b_user = db.get_or_create_discord_user("3", "PlayerB", None)
        budget_before = db.get_budget_status(b_user["id"])

        fake = ScriptedContinuationProvider(continues=True)
        self._login("2", "PlayerA")
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            res = self.client.post(
                f"/api/multiplayer/characters/{char_a}/mark_concluded",
                json={"storm_out": True, "final_message": "I'm out.", "model_key": "luna"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.get_json()["thread_auto_concluded"])

        # PlayerB (the only remaining player) was billed - and A (who left) was NOT billed anything
        # for this ambient call, since the check is about the scene B is left in, not A's own action.
        budget_after = db.get_budget_status(b_user["id"])
        self.assertLess(budget_after["remaining_usd"], budget_before["remaining_usd"])

        a_user = db.get_or_create_discord_user("2", "PlayerA", None)
        a_budget = db.get_budget_status(a_user["id"])
        self.assertEqual(a_budget["spent_usd"], 0.0)

        # And it's specifically the 50%-subsidized rate (split across the 1 remaining player), not
        # the full cost of the single decide_continuation call that was made.
        full_cost = pricing.estimate_cost_usd(_usage())
        expected_charge = pricing.split_ambient_cost_per_player(full_cost, 1)
        self.assertAlmostEqual(budget_before["remaining_usd"] - budget_after["remaining_usd"], expected_charge, places=6)

    def test_storm_out_with_nobody_remaining_costs_nothing(self):
        admin = self._login("1", "Admin")
        db.set_is_admin(admin["id"], True)
        server_res = self.client.post("/api/admin/multiplayer_servers", json={"name": "Test", "setting_key": "petrograd_1917"})
        server_id = server_res.get_json()["server_id"]
        location_id = self.client.post(f"/api/admin/multiplayer_servers/{server_id}/locations", json={"name": "Location A"}).get_json()["location_id"]

        self._login("2", "Solo")
        char_id = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": server_id}).get_json()["character_id"]
        self.client.post(f"/api/multiplayer/characters/{char_id}/move", json={"location_id": location_id})

        res = self.client.post(f"/api/multiplayer/characters/{char_id}/mark_concluded", json={"storm_out": True, "final_message": "Bye."})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["thread_auto_concluded"])


if __name__ == "__main__":
    unittest.main()
