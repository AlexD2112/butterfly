"""
Tests for the /create_character Discord flow (2026-09-14, run_discord_bot.py). This module's own
docstring is explicit that the discord.py Client/Interaction-dependent parts "can't be meaningfully
unit tested" without a real gateway - true here too (Modal.on_submit, Select.callback all expect a
real discord.Interaction). What IS genuinely testable, and covered below:

  - The module imports cleanly (constructing discord.ui.View/Modal/Select subclasses and the
    discord.Client itself needs no network access, but a bad class body - a bad decorator, a typo in
    a class-level discord.ui.TextInput - would raise at import time, before any gateway connects).
  - The plain-value helper functions (_claim_name_or_none, _build_and_save_character,
    CharacterCreationState) - ordinary Python, no discord objects involved.
  - That a complete, realistic wizard run (the exact shape AttributeValueSelect/SkillGroupView/
    FeatSelectCustom/LiabilitySelectCustom would produce) actually validates against the real
    system.create_character - the same function server.py's own /api/characters route calls - proving
    the wizard's OUTPUT CONTRACT is correct even without driving Discord's actual UI dispatch.
  - A structural regression check that every system's SKILLS/FEAT_CATALOG/LIABILITY_CATALOG still fit
    Discord's real component limits (max 25 options per Select, at most 5 action rows per message) -
    this would have caught it immediately if a future system addition broke either assumption.
"""

import unittest

import db
import engine
import run_discord_bot as bot
from systems._shared import core as shared_core


class TestModuleImportsCleanly(unittest.TestCase):
    def test_new_classes_exist(self):
        for name in [
            "CharacterCreationState", "CharacterCreationEntryView", "HistoricalFigureModal",
            "NameRoleModal", "AttributeValueSelect", "SkillGroupView", "FeatSelectCustom",
            "LiabilitySelectCustom", "ReviewView",
        ]:
            self.assertTrue(hasattr(bot, name), f"run_discord_bot.{name} should exist")

    def test_create_character_command_registered(self):
        command_names = {c.name for c in bot.client.tree.get_commands()}
        self.assertIn("create_character", command_names)
        self.assertIn("join", command_names)  # pre-existing command, still registered alongside the new one


class TestCharacterCreationState(unittest.TestCase):
    def test_derives_system_key_from_setting(self):
        state = bot.CharacterCreationState({"id": 1}, {"id": 2, "name": "Test Server"}, {"id": 3}, "petrograd_1917")
        self.assertEqual(state.system_key, "red_admiral")
        self.assertEqual(state.attribute_assignment, {})
        self.assertEqual(state.skill_tiers, {})
        self.assertEqual(state.liability_ids, [])


class TestClaimNameOrNone(unittest.TestCase):
    def setUp(self):
        self.user = db.get_or_create_discord_user("999888777", "claimtest", None)
        self.server_id = db.create_multiplayer_server("Claim Test Server", "petrograd_1917", "red_admiral", "day", 24, self.user["id"])
        self.game = db.get_game_for_server(self.server_id)

    def test_none_when_name_free(self):
        self.assertIsNone(bot._claim_name_or_none(self.game["id"], "Nobody Yet"))

    def test_error_when_name_taken(self):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        db.create_character(self.user["id"], "petrograd_1917", "red_admiral", "Leon Trotsky", state, is_premade=True, game_id=self.game["id"])
        error = bot._claim_name_or_none(self.game["id"], "Leon Trotsky")
        self.assertIsNotNone(error)
        self.assertIn("already claimed", error)

    def test_case_insensitive(self):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        db.create_character(self.user["id"], "petrograd_1917", "red_admiral", "Leon Trotsky", state, is_premade=True, game_id=self.game["id"])
        self.assertIsNotNone(bot._claim_name_or_none(self.game["id"], "leon trotsky"))


class TestBuildAndSaveCharacter(unittest.TestCase):
    def test_builds_full_state_and_persists(self):
        user = db.get_or_create_discord_user("111222333", "buildtest", None)
        server_id = db.create_multiplayer_server("Build Test Server", "rome_59_bce", "apollo", "day", 24, user["id"])
        game = db.get_game_for_server(server_id)
        system = engine.get_system("apollo")
        character, errors = system.create_character(
            "Gnaeus Testicus", "A minor senator",
            dict(zip(shared_core.ATTRIBUTES, shared_core.STANDARD_ATTRIBUTE_ARRAY)),
            {}, next(iter(system.FEAT_CATALOG)), [],
        )
        self.assertEqual(errors, [])
        bot._build_and_save_character(user["id"], "rome_59_bce", "apollo", game["id"], character)
        rows = [c for c in db.get_game_characters(game["id"]) if c["name"] == "Gnaeus Testicus"]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_premade"])


class TestWizardOutputValidatesForEverySetting(unittest.TestCase):
    """Simulates exactly what a full run through AttributeValueSelect -> SkillGroupView ->
    FeatSelectCustom -> LiabilitySelectCustom would produce, for every real setting, and feeds it
    straight into the actual validation function server.py's own route uses - proving the wizard's
    data contract is right without needing to drive discord.py's real dispatch machinery."""

    def test_a_full_wizard_run_validates_for_every_setting(self):
        for setting_key, info in engine.SETTING_REGISTRY.items():
            with self.subTest(setting=setting_key):
                system = engine.get_system(info["system"])

                # AttributeValueSelect's guarantee: every attribute gets exactly one standard-array
                # value, one-to-one - simulate the simplest possible run (assign in list order).
                attribute_assignment = dict(zip(system.ATTRIBUTES, shared_core.STANDARD_ATTRIBUTE_ARRAY))

                liability_ids = list(system.LIABILITY_CATALOG)[:2]
                budget = shared_core.SKILL_POINT_BUDGET + shared_core.LIABILITY_BONUS_SKILL_POINTS * len(liability_ids)

                # SkillGroupView: a realistic, careful spend within budget - up to
                # MAX_STARTING_EXPERTISE_SKILLS at Expertise (2pts each), remaining budget at
                # Proficiency (1pt each) - same shape SkillGroupView's two-select-per-group UI
                # produces, just picked here without a live running-total display to steer it.
                all_skills = [s for skills in system.SKILLS.values() for s in skills]
                skill_tiers = {}
                spent = 0
                expertise_count = 0
                for skill in all_skills:
                    if expertise_count < shared_core.MAX_STARTING_EXPERTISE_SKILLS and spent + 2 <= budget:
                        skill_tiers[skill] = 2
                        spent += 2
                        expertise_count += 1
                    elif spent + 1 <= budget:
                        skill_tiers[skill] = 1
                        spent += 1
                    else:
                        break

                starting_feats = {k: v for k, v in system.FEAT_CATALOG.items() if v.get("min_level", 1) <= 1}
                self.assertTrue(starting_feats, f"{setting_key}'s system should offer at least one level-1 feat")
                feat_id = next(iter(starting_feats))

                character, errors = system.create_character(
                    "Test Character", "A test role", attribute_assignment, skill_tiers, feat_id, liability_ids,
                )
                self.assertEqual(errors, [], f"{setting_key}: wizard-shaped input should validate cleanly")
                self.assertEqual(character["name"], "Test Character")
                self.assertEqual(len(character["liabilities"]), len(liability_ids))


class TestCatalogsFitDiscordComponentLimits(unittest.TestCase):
    """Discord hard limits: a Select offers at most 25 options, and a message has at most 5 action
    rows. SkillGroupView pages through attribute groups one at a time (so the number of GROUPS is
    unbounded - each page only ever uses 2 selects + 1 button row, 3 rows total, regardless of how
    many groups exist), but each individual group's Select still can't exceed 25 skills, and
    FeatSelectCustom/LiabilitySelectCustom each put the WHOLE level-1 feat/liability catalog in one
    Select (so neither may exceed 25 entries). A future system/setting growing past these would
    silently truncate options rather than error - this test exists so that shows up as a clear
    failure instead."""

    def test_every_system_fits_select_limits(self):
        for system_key in engine.SYSTEM_REGISTRY:
            with self.subTest(system=system_key):
                system = engine.get_system(system_key)
                for attr, skills in system.SKILLS.items():
                    self.assertLessEqual(len(skills), 25, f"{system_key}.{attr} has more than 25 skills - exceeds one Select's option limit")
                starting_feats = [f for f in system.FEAT_CATALOG.values() if f.get("min_level", 1) <= 1]
                self.assertLessEqual(len(starting_feats), 25, f"{system_key} has more than 25 level-1 feats - exceeds FeatSelectCustom's option limit")
                self.assertLessEqual(len(system.LIABILITY_CATALOG), 25, f"{system_key} has more than 25 liabilities - exceeds LiabilitySelectCustom's option limit")


if __name__ == "__main__":
    unittest.main()
