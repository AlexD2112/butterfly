import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import engine
import server


class TestLevelUpOptionsAndApply(unittest.TestCase):
    """The clickable level-up UI (2026-08-23) - apply_level_up called directly, no AI involved, so
    these are plain route/db tests, no fake provider needed."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.user = db.get_or_create_discord_user("1", "Player", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user["id"]

        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        self.character_id = db.create_character(self.user["id"], "petrograd_1917", "red_admiral", "Test Trotsky", state)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _set_pending(self, level=None, skill_tier=None):
        row = db.get_character(self.character_id, self.user["id"])
        state = row["state"]
        state["character"]["level_up_pending"] = True
        if level is not None:
            state["character"]["level"] = level
        if skill_tier is not None:
            skill, tier = skill_tier
            state["character"]["skills"].setdefault(skill, {"attribute": "intellect", "tier": 0})
            state["character"]["skills"][skill]["tier"] = tier
        db.save_character_state(self.character_id, self.user["id"], state)

    def test_options_when_nothing_pending(self):
        res = self.client.get(f"/api/character/{self.character_id}/level_up_options")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"pending": False})

    def test_options_when_pending_lists_skills_and_attributes(self):
        self._set_pending()
        data = self.client.get(f"/api/character/{self.character_id}/level_up_options").get_json()
        self.assertTrue(data["pending"])
        self.assertEqual(data["level"], 1)
        self.assertTrue(any(s["skill"] == "revolutionary_theory" for s in data["skills"]))
        self.assertIn("intellect", data["attributes"])

    def test_options_excludes_feats_above_current_level(self):
        self._set_pending(level=1)
        data = self.client.get(f"/api/character/{self.character_id}/level_up_options").get_json()
        feat_ids = {f["id"] for f in data["feats"]}
        self.assertNotIn("network_of_agitators", feat_ids)  # min_level 5, character is level 1
        self.assertIn("silver_tongue", feat_ids)  # min_level 1

    def test_options_excludes_a_feat_the_character_already_has(self):
        row = db.get_character(self.character_id, self.user["id"])
        state = row["state"]
        state["character"]["level_up_pending"] = True
        state["character"]["feats"].append({"id": "silver_tongue", "name": "Silver Tongue", "description": "..."})
        db.save_character_state(self.character_id, self.user["id"], state)
        data = self.client.get(f"/api/character/{self.character_id}/level_up_options").get_json()
        feat_ids = {f["id"] for f in data["feats"]}
        self.assertNotIn("silver_tongue", feat_ids)

    def test_options_excludes_a_feat_whose_skill_prerequisite_is_unmet(self):
        # network_of_agitators needs level 5 + crowd_command_and_agitation >= proficiency - Trotsky's
        # premade sheet already has that skill at proficiency, so force it down to untrained first to
        # actually exercise the "unmet" branch, not just the trivially-already-met default.
        self._set_pending(level=5, skill_tier=("crowd_command_and_agitation", 0))
        data = self.client.get(f"/api/character/{self.character_id}/level_up_options").get_json()
        feat_ids = {f["id"] for f in data["feats"]}
        self.assertNotIn("network_of_agitators", feat_ids)

        self._set_pending(level=5, skill_tier=("crowd_command_and_agitation", 1))
        data = self.client.get(f"/api/character/{self.character_id}/level_up_options").get_json()
        feat_ids = {f["id"] for f in data["feats"]}
        self.assertIn("network_of_agitators", feat_ids)

    def test_apply_skill_choice_already_at_expertise_is_rejected(self):
        # Trotsky's premade sheet already has revolutionary_theory at Expertise (tier 2, the max) -
        # picking it again for a level-up should be rejected, not silently no-op or overflow.
        self._set_pending()
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "skill", "skill_upgrades": [{"skill": "revolutionary_theory", "attribute": "intellect"}], "reasoning": "x"},
        )
        data = res.get_json()
        self.assertFalse(data["applied"])
        self.assertTrue(data["state"]["character"]["level_up_pending"])  # still pending - nothing was spent

    def test_apply_skill_choice_on_an_untrained_skill(self):
        self._set_pending()
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "skill", "skill_upgrades": [{"skill": "military_strategic_planning", "attribute": "intellect"}], "reasoning": "x"},
        )
        data = res.get_json()
        self.assertTrue(data["applied"])
        self.assertEqual(data["state"]["character"]["skills"]["military_strategic_planning"]["tier"], 1)

    def test_apply_feat_choice(self):
        self._set_pending()
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "feat", "feat_id": "silver_tongue", "reasoning": "x"},
        )
        data = res.get_json()
        self.assertTrue(data["applied"])
        feat_ids = {f["id"] for f in data["state"]["character"]["feats"]}
        self.assertIn("silver_tongue", feat_ids)

    def test_apply_attribute_choice(self):
        # Trotsky's premade sheet already has intellect at MAX_ATTRIBUTE (20) - use physique/resolve
        # instead so the assertion isn't silently checking clamped-to-the-same-value behavior.
        self._set_pending()
        before = db.get_character(self.character_id, self.user["id"])["state"]["character"]["attributes"]["physique"]
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "attribute", "attribute_points": {"physique": 2, "resolve": 1}, "reasoning": "x"},
        )
        data = res.get_json()
        self.assertTrue(data["applied"])
        self.assertEqual(data["state"]["character"]["attributes"]["physique"], before + 2)

    def test_apply_with_nothing_pending_is_rejected(self):
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "attribute", "attribute_points": {"intellect": 1}, "reasoning": "x"},
        )
        self.assertEqual(res.status_code, 400)

    def test_apply_an_invalid_choice_leaves_it_still_pending(self):
        self._set_pending()
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "feat", "feat_id": "not_a_real_feat", "reasoning": "x"},
        )
        data = res.get_json()
        self.assertFalse(data["applied"])
        self.assertTrue(data["state"]["character"]["level_up_pending"])

    def test_apply_persists_to_the_database(self):
        self._set_pending()
        self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "attribute", "attribute_points": {"physique": 3}, "reasoning": "x"},
        )
        row = db.get_character(self.character_id, self.user["id"])
        self.assertFalse(row["state"]["character"]["level_up_pending"])

    def test_wrong_owner_cannot_see_or_apply(self):
        self._set_pending()
        other = db.get_or_create_discord_user("2", "OtherPlayer", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = other["id"]
        self.assertEqual(self.client.get(f"/api/character/{self.character_id}/level_up_options").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/character/{self.character_id}/apply_level_up", json={"choice": "skill"}).status_code, 404
        )


class TestLevelUpAnnouncementInMultiplayer(unittest.TestCase):
    """Applying a level-up for a multiplayer character drops a "reached level N" note in the shared
    subspace log - level only, never which skill/feat/attribute was picked (per direction)."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.user = db.get_or_create_discord_user("1", "Player", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user["id"]

        admin = db.get_or_create_discord_user("99", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        state["character"]["level"] = 4
        state["character"]["level_up_pending"] = True
        self.character_id = db.create_character(self.user["id"], "petrograd_1917", "red_admiral", "Test Trotsky", state, game_id=game["id"])
        db.move_character(self.character_id, self.server_id, self.location_id, self.subspace_id)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_applying_posts_a_level_only_announcement(self):
        res = self.client.post(
            f"/api/character/{self.character_id}/apply_level_up",
            json={"choice": "attribute", "attribute_points": {"intellect": 1}, "reasoning": "x"},
        )
        self.assertTrue(res.get_json()["applied"])
        messages = db.get_subspace_messages(self.subspace_id, self.character_id)
        level_ups = [m for m in messages if m["kind"] == "level_up"]
        self.assertEqual(len(level_ups), 1)
        self.assertIn("reached level 4", level_ups[0]["narration"])
        self.assertNotIn("intellect", level_ups[0]["narration"].lower())

    def test_single_player_apply_does_not_error_on_missing_presence(self):
        sp_state = engine.load_premade_character_state("petrograd_1917")
        sp_state.pop("_is_new", None)
        sp_state["character"]["level_up_pending"] = True
        sp_char_id = db.create_character(self.user["id"], "petrograd_1917", "red_admiral", "Solo", sp_state)
        res = self.client.post(
            f"/api/character/{sp_char_id}/apply_level_up",
            json={"choice": "attribute", "attribute_points": {"intellect": 1}, "reasoning": "x"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["applied"])


if __name__ == "__main__":
    unittest.main()
