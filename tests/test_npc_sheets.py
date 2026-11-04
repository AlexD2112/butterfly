"""NPC sheets (2026-11-03): DCs from the sheet, fog of war for the player, and new sheets from the GM."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
import engine
import npcs
import server
from tests.test_hard_mechanics import fresh_petrograd, run

SYSTEM = engine.get_system("red_admiral")


class TestSheets(unittest.TestCase):
    def test_every_relationship_and_rival_has_a_sheet(self):
        state = fresh_petrograd()
        sheets = npcs.all_sheets(state, "petrograd_1917")
        for key in list(state["relationships"]) + list(state["enmity"]):
            self.assertIn(key, sheets, key)

    def test_skills_are_real_and_npcs_md_has_no_stale_stats(self):
        valid = {s for v in SYSTEM.SKILLS.values() for s in v}
        for key, s in npcs.load("petrograd_1917").items():
            self.assertEqual(set(s["attributes"]), set(npcs.ATTRS), key)
            self.assertTrue(set(s["expertise"] + s["proficient"]) <= valid, key)
        self.assertNotIn("**Stats:**", (engine.SETTINGS_ROOT / "petrograd_1917" / "npcs.md").read_text())

    def test_names_resolve(self):
        state = fresh_petrograd()
        self.assertEqual(npcs.resolve(state, "petrograd_1917", "Fyodor Dan"), "dan")
        self.assertEqual(npcs.resolve(state, "petrograd_1917", "Antonov-Ovseenko"), "antonov_ovseenko")
        self.assertIsNone(npcs.resolve(state, "petrograd_1917", "a random sentry"))


class TestOpposedDC(unittest.TestCase):
    def test_dan_in_debate_is_sixteen(self):
        dc, why = npcs.opposed_dc(fresh_petrograd(), "petrograd_1917", SYSTEM, "dan", "oration", "debate_and_public_argument")
        self.assertEqual(dc, 16)  # debate is an oration skill: Oration 14 (+2) + expertise 2 x 2
        self.assertIn("Fyodor Dan", why)

    def test_the_gm_number_is_overridden_and_explained(self):
        state = fresh_petrograd()
        _, events, result = run(state, "request_check", {"attribute": "oration", "skill": "debate_and_public_argument",
                                                         "dc": 12, "target_npc": "dan", "description": "Out-argue Dan"})
        self.assertEqual(result["dc"], 16)
        self.assertIn("resists with", result["dc_source"])
        self.assertEqual(state["npc_knowledge"]["dan"]["encounters"], 1)

    def test_opposed_by_another_skill_or_none(self):
        state = fresh_petrograd()
        dc, _ = npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "stalin", "cunning", "reading_hidden_agendas", "deception")
        self.assertEqual(dc, 10 + 4 + 2)  # Cunning 18 (+4), proficient in deception
        self.assertEqual(npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "stalin", "cunning", None, "none"), (None, None))
        _, _, result = run(state, "request_check", {"attribute": "presence", "dc": 9, "target_npc": "a sentry", "description": "x"})
        self.assertEqual(result["dc"], 9)  # no sheet: the GM's DC stands


class TestFogOfWar(unittest.TestCase):
    def view(self, state, key):
        return npcs.player_view(state, "petrograd_1917", key)

    def test_tiers_hide_what_they_should(self):
        state = fresh_petrograd()
        krasnov = self.view(state, "krasnov")  # never met
        self.assertEqual(krasnov["tier"], 0)
        self.assertNotIn("value", json.dumps(krasnov))
        self.assertTrue(any(a.get("unknown") for a in krasnov["attributes"]))
        self.assertNotIn("expertise", krasnov["skills"])
        dan = self.view(state, "dan")  # worked with
        self.assertEqual(dan["tier"], 2)
        for a in dan["attributes"]:
            true = npcs.load("petrograd_1917")["dan"]["attributes"][a["attribute"]]
            self.assertTrue(a["range"][0] <= true <= a["range"][1])
        self.assertNotIn("proficiency", dan["skills"])
        kamenev = self.view(state, "kamenev")  # knows well
        self.assertEqual(kamenev["attributes"][0]["value"], 14)

    def test_dealing_with_someone_teaches_you_about_them(self):
        state = fresh_petrograd()
        for _ in range(npcs.ENCOUNTERS_PER_TIER):
            npcs.record_encounter(state, "petrograd_1917", "Krasnov")
        self.assertEqual(self.view(state, "krasnov")["tier"], 1)


class TestNewSheets(unittest.TestCase):
    BASE = {"key": "kozmin", "name": "Colonel Kozmin", "role": "Deputy commander of the military district",
            "attributes": {"oration": 10, "intellect": 13, "resolve": 13, "presence": 12, "cunning": 12, "physique": 13},
            "proficient": ["military_strategic_planning"]}

    def test_create_validates_and_starts_a_relationship(self):
        state = fresh_petrograd()
        _, _, result = run(state, "create_npc_sheet", self.BASE)
        self.assertEqual(result["created"], "kozmin")
        self.assertEqual(state["relationships"]["kozmin"]["trust"], 0)
        self.assertNotIn("_npc_sheets", engine.build_state_digest(state))  # exact numbers stay server-side
        dc, _ = npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "kozmin", "intellect", "military_strategic_planning")
        self.assertEqual(dc, 13)

    def test_no_super_people_no_duplicates(self):
        state = fresh_petrograd()
        over = dict(self.BASE, attributes={a: 18 for a in npcs.ATTRS})
        self.assertIn("error", npcs.create(state, "petrograd_1917", SYSTEM, over))
        self.assertIn("error", npcs.create(state, "petrograd_1917", SYSTEM, dict(self.BASE, key="dan2", name="Fyodor Dan")))
        bad = dict(self.BASE, expertise=["flying"])
        self.assertIn("error", npcs.create(state, "petrograd_1917", SYSTEM, bad))

    def test_prompt_carries_the_sheets(self):
        prompt = engine.build_system_prompt(fresh_petrograd(), "petrograd_1917", "red_admiral")
        self.assertIn("# NPC SHEETS", prompt)
        self.assertIn("dan: Or14 In16", prompt)


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "t.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.cid = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"}).get_json()["character_id"]

    def tearDown(self):
        db.DB_FILE = self._orig
        self._tmpdir.cleanup()

    def test_the_player_gets_the_fogged_view(self):
        data = self.client.get(f"/api/character/{self.cid}/npc/krasnov").get_json()
        self.assertEqual(data["tier"], 0)
        self.assertNotIn('"value"', json.dumps(data))
        data = self.client.get(f"/api/character/{self.cid}/npc/kamenev").get_json()
        self.assertEqual(data["tier"], 3)
        self.assertEqual(data["trust"], 1)
        self.assertTrue(self.client.get(f"/api/character/{self.cid}/npc/nobody").get_json()["no_sheet"])


class TestAsks(unittest.TestCase):
    def test_being_talked_round_meets_their_resolve_and_the_size_of_the_ask(self):
        state = fresh_petrograd()
        dc, why = npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "spiridonova", "presence", "personal_persuasion")
        self.assertEqual(dc, 16)  # her will (Resolve +4, proficient willpower +2) beats her Presence-based persuasion (+2)
        self.assertIn("resolve", why)
        dc, why = npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "spiridonova", "presence", "personal_persuasion", ask="major")
        self.assertEqual(dc, 22)
        self.assertIn("major ask", why)
        dc, _ = npcs.opposed_dc(state, "petrograd_1917", SYSTEM, "dan", "oration", "debate_and_public_argument")
        self.assertEqual(dc, 16)  # his debate (+6) is better than his Resolve (+1)

    def test_a_major_ask_is_never_won_in_one_roll(self):
        state = fresh_petrograd()
        _, _, result = run(state, "request_check", {"attribute": "presence", "skill": "personal_persuasion", "dc": 10,
                                                    "target_npc": "spiridonova", "ask": "major", "description": "Win the Left SRs"})
        self.assertIn("several exchanges", result["gm_instruction"])


if __name__ == "__main__":
    unittest.main()
