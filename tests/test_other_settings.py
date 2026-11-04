"""Today's systems in the settings beyond Petrograd (2026-11-03): NPC sheets, travel graphs,
negotiations, and a style guide that no longer assumes 1917."""

import unittest
from unittest.mock import patch

import council
import engine
import geography as G
import npcs
from tests.test_hard_mechanics import fixed_check
from tests.test_game_feel import RecordingProvider, tools_turn, text_turn

OTHERS = ["rome_59_bce", "britannia_876", "crusades_1097"]


def fresh(setting):
    state = engine.load_premade_character_state(setting)
    state.pop("_is_new", None)
    return state


class TestOtherSettings(unittest.TestCase):
    def test_every_relationship_and_rival_has_a_sheet_with_real_skills(self):
        for setting in OTHERS:
            state = fresh(setting)
            system = engine.get_system(engine.SETTING_REGISTRY[setting]["system"])
            valid = {s for v in system.SKILLS.values() for s in v}
            sheets = npcs.all_sheets(state, setting)
            for key in list(state.get("relationships", {})) + list(state.get("enmity", {})):
                self.assertIn(key, sheets, (setting, key))
            for key, s in sheets.items():
                self.assertTrue(set(s["expertise"] + s["proficient"]) <= valid, (setting, key))
            self.assertNotIn("**Stats:**", (engine.SETTINGS_ROOT / setting / "npcs.md").read_text())

    def test_travel_graphs_connect_and_times_are_sane(self):
        for setting in OTHERS:
            geo = G.load(setting)
            state = fresh(setting)
            G.ensure_initialized(state, geo)
            self.assertEqual([n for n in geo["nodes"] if G.route(geo, state, geo["player_start"], n) is None], [], setting)
        geo = G.load("britannia_876")
        state = fresh("britannia_876")
        G.ensure_initialized(state, geo)
        days = G.route(geo, state, "wilton", "exeter")["hours"] / 24
        self.assertTrue(1 < days < 5, days)

    def test_prompts_carry_sheets_geography_and_negotiations_and_no_1917(self):
        for setting in OTHERS:
            prompt = engine.build_system_prompt(fresh(setting), setting, engine.SETTING_REGISTRY[setting]["system"])
            for section in ("# NPC SHEETS", "# GEOGRAPHY", "# NEGOTIATIONS"):
                self.assertIn(section, prompt, (setting, section))
        self.assertNotIn("1917", council.VOICE_GUIDANCE)
        self.assertNotIn("Lenin", council.VOICE_GUIDANCE)

    def test_a_negotiation_with_cato_uses_his_sheet(self):
        state = fresh("rome_59_bce")
        system = engine.get_system("apollo")
        provider = RecordingProvider([
            tools_turn(("open_negotiation", {"npc": "Cato", "topic": "the land bill", "stakes": "major",
                                             "demands": ["The bill goes before the Senate first"]})),
            tools_turn(("negotiation_offer", {"npc": "cato", "offer": "x", "attribute": "presence",
                                              "skill": "patron_client_charm", "meets": ["senate"]})),
            text_turn("Cato does not move.")])
        with patch.object(system, "roll_check", side_effect=fixed_check("failure")):
            _, events = engine.run_turn(provider, "m", state, "rome_59_bce", "apollo", [], "x")
        check = next(e for e in events if e["type"] == "check")
        self.assertEqual(check["dc"], 10 + 4 + 2 * 2 - 2)  # Resolve 19 (+4), expertise Unbending Will, one demand met
        self.assertEqual(state["negotiations"]["cato"]["position"], -1)  # a rival starts wary; failure doesn't move him

    def test_the_player_travels_in_britannia(self):
        state = fresh("britannia_876")
        geo = G.load("britannia_876")
        res, _ = G.handle(geo, state, "move_unit", {"unit": "player", "to": "Exeter"})
        self.assertEqual(state["geo"]["player"], "exeter")
        self.assertIn("days_elapsed", res["note"])


if __name__ == "__main__":
    unittest.main()
