import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import providers
import server
from systems import _shared
from systems._shared import core
from systems.red_admiral import mechanics as red_admiral

ATTRS = {"oration": 16, "intellect": 14, "resolve": 13, "presence": 12, "cunning": 10, "physique": 8}


class TestValidateAndBuildLeveledCharacter(unittest.TestCase):
    """core-level validation for a historical figure starting above level 1 (2026-08-23) - same
    standard-array rule as a fresh custom character, but a skill/feat budget that scales with level."""

    def test_level_1_matches_the_plain_custom_character_budget(self):
        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", 1, ATTRS, {"platform_speechmaking": 1, "revolutionary_theory": 2}, [],
        )
        self.assertEqual(errors, [])
        self.assertEqual(character["level"], 1)
        self.assertEqual(character["proficiency_bonus"], core.proficiency_bonus_for_level(1))

    def test_higher_level_grants_a_larger_skill_budget(self):
        # Level 3 -> +2*(3-1) = +4 skill points over the level-1 base (10) = 14 total.
        tiers = {
            "platform_speechmaking": 2, "debate_and_public_argument": 2, "crowd_command_and_agitation": 1,
            "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1,
            "inspiring_loyalty": 1, "factional_maneuvering": 1, "deception": 1, "negotiation": 1,
        }  # 14 points total, expertise_count=4 (within max_expertise = 3 + (3-1) = 5)
        self.assertEqual(sum(tiers.values()), 14)
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 3, ATTRS, tiers, [])
        self.assertEqual(errors, [])
        self.assertEqual(character["level"], 3)

    def test_over_budget_for_the_chosen_level_fails(self):
        tiers = {"platform_speechmaking": 2, "debate_and_public_argument": 2, "crowd_command_and_agitation": 2,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 2, "inspiring_loyalty": 2,
                 "factional_maneuvering": 2}  # 16 points - over the level-2 budget of 12
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 2, ATTRS, tiers, [])
        self.assertIsNone(character)
        self.assertTrue(any("over budget" in e for e in errors))

    def test_level_out_of_range_is_rejected(self):
        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", core.MAX_HISTORICAL_FIGURE_LEVEL + 5, ATTRS, {}, [],
        )
        self.assertIsNone(character)
        self.assertTrue(any("level must be" in e for e in errors))

    def test_attributes_still_locked_to_the_standard_array_even_at_a_higher_level(self):
        inflated = dict(ATTRS)
        inflated["oration"] = 20  # not a standard-array value
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 4, inflated, {}, [])
        self.assertIsNone(character)
        self.assertTrue(any("standard array" in e for e in errors))

    def test_feat_requiring_a_higher_level_than_chosen_is_silently_dropped_not_rejected(self):
        # An ineligible feat pick is pruned, not a hard failure - an AI generating this in one shot
        # isn't reliably self-consistent between the level it picked and the feats it reached for
        # (a real miscalibration found via live-model testing - see NOTES.md). Punishing an otherwise
        # eligible figure's entire sheet over one overreaching feat pick would defeat the point.
        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", 2, ATTRS, {}, ["network_of_agitators"],  # min_level 5
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(character)
        self.assertEqual(character["feats"], [])  # dropped, but everything else still built

    def test_feat_with_unmet_skill_prerequisite_is_silently_dropped_even_at_a_qualifying_level(self):
        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", 5, ATTRS, {}, ["network_of_agitators"],  # needs crowd_command_and_agitation >= proficiency
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(character)
        self.assertEqual(character["feats"], [])

    def test_feat_with_prerequisite_met_succeeds(self):
        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", 5, ATTRS, {"crowd_command_and_agitation": 1}, ["network_of_agitators"],
        )
        self.assertEqual(errors, [])
        self.assertEqual({f["id"] for f in character["feats"]}, {"network_of_agitators"})

    def test_multiple_feats_and_zero_feats_both_work(self):
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 1, ATTRS, {}, [])
        self.assertEqual(errors, [])
        self.assertEqual(character["feats"], [])

        character, errors = red_admiral.create_leveled_character(
            "Test", "Tester", 1, ATTRS, {}, ["silver_tongue", "iron_constitution"],
        )
        self.assertEqual(errors, [])
        self.assertEqual({f["id"] for f in character["feats"]}, {"silver_tongue", "iron_constitution"})

    def test_unknown_feat_id_is_rejected(self):
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 1, ATTRS, {}, ["not_a_real_feat"])
        self.assertIsNone(character)
        self.assertTrue(any("Unknown feat" in e for e in errors))

    def test_level_up_pending_starts_false_and_xp_starts_zero(self):
        character, errors = red_admiral.create_leveled_character("Test", "Tester", 4, ATTRS, {}, [])
        self.assertEqual(errors, [])
        self.assertFalse(character["level_up_pending"])
        self.assertEqual(character["xp"], 0)


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedHistoricalProvider:
    def __init__(self, eligible=True, refusal_reason=None, level=2, feat_ids=None, liability_ids=None, skill_tiers=None):
        self.eligible = eligible
        self.refusal_reason = refusal_reason
        self.level = level
        self.feat_ids = feat_ids or []
        self.liability_ids = liability_ids or []
        self.skill_tiers = skill_tiers if skill_tiers is not None else {"platform_speechmaking": 1}

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        if force_tool_name == "decide_historical_character":
            input_ = {
                "eligible": self.eligible, "reasoning": "scripted",
                "refusal_reason": self.refusal_reason,
                "role": "A minor revolutionary figure", "level": self.level,
                "attribute_assignment": ATTRS, "skill_tiers": self.skill_tiers,
                "feat_ids": self.feat_ids, "liability_ids": self.liability_ids,
            }
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "h1", "name": "decide_historical_character", "input": input_}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=_usage())


class TestGenerateHistoricalCharacter(unittest.TestCase):
    """engine.generate_historical_character - the eligibility/refusal decision plus building a
    validated sheet from whatever the model proposed."""

    def test_eligible_returns_a_validated_character(self):
        fake = ScriptedHistoricalProvider(eligible=True, level=2)
        result = engine.generate_historical_character(fake, "gpt-5.6-luna", "petrograd_1917", "red_admiral", "A. Historical Figure")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["character"]["name"], "A. Historical Figure")
        self.assertEqual(result["character"]["level"], 2)

    def test_refusal_is_reported_as_ineligible_with_a_reason(self):
        fake = ScriptedHistoricalProvider(eligible=False, refusal_reason="Not enough real information about this person.")
        result = engine.generate_historical_character(fake, "gpt-5.6-luna", "petrograd_1917", "red_admiral", "Some Obscure Person")
        self.assertFalse(result["eligible"])
        self.assertIn("Not enough real information", result["reason"])

    def test_a_generated_sheet_that_fails_validation_is_treated_as_ineligible_not_an_error(self):
        fake = ScriptedHistoricalProvider(eligible=True, feat_ids=["not_a_real_feat"])
        result = engine.generate_historical_character(fake, "gpt-5.6-luna", "petrograd_1917", "red_admiral", "Some Figure")
        self.assertFalse(result["eligible"])
        self.assertIn("failed validation", result["reason"])

    def test_usage_is_recorded_regardless_of_eligibility(self):
        fake = ScriptedHistoricalProvider(eligible=False, refusal_reason="x")
        sink = []
        engine.generate_historical_character(fake, "gpt-5.6-luna", "petrograd_1917", "red_admiral", "X", usage_sink=sink)
        self.assertEqual(len(sink), 1)


class TestHistoricalCharacterRouteAndClaimLock(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.admin = db.get_or_create_discord_user("1", "Admin", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.admin["id"]
        self.server_id = db.create_multiplayer_server("Test", "petrograd_1917", "red_admiral", "day", 24.0, self.admin["id"])
        self.game = db.get_game_for_server(self.server_id)

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_local_user_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess.pop("user_id", None)  # forces current_user() to fall back to the shared local user
        res = self.client.post("/api/characters/historical", json={"setting_key": "petrograd_1917", "figure_name": "X"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.get_json()["discord_login_required"])

    def test_successful_generation_creates_a_character(self):
        fake = ScriptedHistoricalProvider(eligible=True, level=2)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            res = self.client.post(
                "/api/characters/historical",
                json={"setting_key": "petrograd_1917", "figure_name": "A. Historical Figure",
                      "multiplayer_server_id": self.server_id},
            )
        data = res.get_json()
        self.assertTrue(data["eligible"])
        self.assertIsNotNone(data["character_id"])
        row = db.get_character(data["character_id"], self.admin["id"])
        self.assertEqual(row["name"], "A. Historical Figure")
        self.assertEqual(row["game_id"], self.game["id"])

    def test_refusal_does_not_create_a_character(self):
        fake = ScriptedHistoricalProvider(eligible=False, refusal_reason="Doesn't fit this exact period.")
        before = len(db.get_game_characters(self.game["id"]))
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            res = self.client.post(
                "/api/characters/historical",
                json={"setting_key": "petrograd_1917", "figure_name": "Someone Ineligible",
                      "multiplayer_server_id": self.server_id},
            )
        data = res.get_json()
        self.assertFalse(data["eligible"])
        self.assertEqual(len(db.get_game_characters(self.game["id"])), before)

    def test_claim_lock_blocks_a_second_request_for_an_already_taken_name(self):
        db.create_character(self.admin["id"], "petrograd_1917", "red_admiral", "Already Taken", {"turn_number": 0}, game_id=self.game["id"])
        fake = ScriptedHistoricalProvider(eligible=True)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            res = self.client.post(
                "/api/characters/historical",
                json={"setting_key": "petrograd_1917", "figure_name": "already taken",  # case-insensitive match
                      "multiplayer_server_id": self.server_id},
            )
        self.assertEqual(res.status_code, 409)

    def test_claim_lock_does_not_apply_to_single_player(self):
        # No multiplayer_server_id - same name reused freely across unrelated single-player games.
        db.create_character(self.admin["id"], "petrograd_1917", "red_admiral", "Duplicate Name", {"turn_number": 0})
        fake = ScriptedHistoricalProvider(eligible=True)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            res = self.client.post(
                "/api/characters/historical",
                json={"setting_key": "petrograd_1917", "figure_name": "Duplicate Name"},
            )
        self.assertTrue(res.get_json()["eligible"])

    def test_premade_flagship_is_also_claim_locked_in_the_same_server(self):
        first = self.client.post(
            "/api/characters", json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": self.server_id},
        )
        self.assertIn("character_id", first.get_json())

        other = db.get_or_create_discord_user("2", "OtherPlayer", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = other["id"]
        second = self.client.post(
            "/api/characters", json={"type": "premade", "setting_key": "petrograd_1917", "multiplayer_server_id": self.server_id},
        )
        self.assertEqual(second.status_code, 409)

    def test_premade_flagship_claim_lock_does_not_apply_to_single_player(self):
        first = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        self.assertIn("character_id", first.get_json())
        second = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"})
        self.assertIn("character_id", second.get_json())  # unrelated single-player games, both succeed


if __name__ == "__main__":
    unittest.main()
