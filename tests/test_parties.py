"""Parties with wings, and the strict rules for changing them (2026-11-06)."""

import copy
import random
import unittest

import engine
import mechanics
import operations
import parties
from tests.test_contested_votes import SYSTEM
from tests.test_hard_mechanics import fresh_petrograd, run

SR, MEN, BOL = "Socialist-Revolutionaries", "Mensheviks", "Bolsheviks"


def chart(state):
    return state["mechanics"]["petrograd_soviet"]


def reorg(state, **inp):
    return parties.reorganize(state, chart(state), {"instance_id": "petrograd_soviet",
                                                    "cause": "After the fight over the Democratic Conference", **inp})


class TestSeed(unittest.TestCase):
    def test_three_parties_with_two_wings_each(self):
        data = chart(fresh_petrograd())["data"]
        for party, seats in ((BOL, 400), (SR, 250), (MEN, 210)):
            ws = parties.wings(data["factions"], party)
            self.assertEqual(len(ws), 2, party)
            self.assertEqual(sum(w["seats"] for w in ws), seats, party)
            self.assertIn(party, data["parties"])
        groups = parties.groups(data["factions"])
        self.assertEqual([p for p, _ in groups], [BOL, None, SR, MEN])  # wings sit together
        self.assertEqual(sum(f["seats"] for f in data["factions"]), 1000)

    def test_an_older_save_gets_its_parties_back(self):
        state = fresh_petrograd()
        data = chart(state)["data"]
        for f in data["factions"]:
            f.pop("party", None)
        data.pop("parties")
        random.Random(1).shuffle(data["factions"])
        mechanics.split_old_blocs(state, mechanics.load_catalog("petrograd_1917"))
        self.assertEqual(len(parties.wings(data["factions"], MEN)), 2)
        self.assertIn(SR, data["parties"])
        order = [p for p, _ in parties.groups(data["factions"])]
        self.assertEqual(len(order), len(set(order)))  # no party appears twice

    def test_the_migration_never_undoes_a_break_away(self):
        state = fresh_petrograd()
        data = chart(state)["data"]
        left = parties._find(data["factions"], "Left Socialist-Revolutionaries")
        left["party"] = "Left Socialist-Revolutionaries"
        data["reorg"] = {"last": {"break_away": "1917-11-01"}, "log": [{"action": "break_away"}]}
        mechanics.split_old_blocs(state, mechanics.load_catalog("petrograd_1917"))
        self.assertEqual(left["party"], "Left Socialist-Revolutionaries")


class TestStrain(unittest.TestCase):
    def test_wings_voting_against_their_party_are_counted(self):
        data = {"factions": []}
        parties.record_vote(data, [
            {"name": "a", "party": "P", "yes": 200, "no": 10, "abstain": 0},
            {"name": "b", "party": "P", "yes": 10, "no": 100, "abstain": 0},
            {"name": "c", "yes": 50, "no": 50, "abstain": 0}])
        self.assertEqual(data["strain"]["wings"], {"b": 1})
        self.assertEqual(data["strain"]["blocs"], {"c": 1})

    def test_ballots_count_too(self):
        data = {}
        parties.record_election(data, [{"name": "a", "party": "P", "votes": {"X": 100, "Y": 10}},
                                       {"name": "b", "party": "P", "votes": {"X": 5, "Y": 60}}])
        self.assertEqual(data["strain"]["wings"], {"b": 1})

    def test_a_real_vote_records_strain_and_reports_parties(self):
        state = fresh_petrograd()
        r, _ = operations.call_vote(state, SYSTEM, {"instance_id": "petrograd_soviet", "motion": "x", "player_position": "for",
                                                    "bloc_stances": {"Left Socialist-Revolutionaries": 2, "Socialist-Revolutionaries (leadership)": -2,
                                                                     BOL: 1, "Mensheviks (defensist)": -2}}, rng=random.Random(2))
        self.assertEqual({p["party"] for p in r["parties"]}, {BOL, SR, MEN})
        self.assertGreaterEqual(sum(chart(state)["data"]["strain"]["wings"].values()), 1)
        self.assertIn("party by party", r["gm_instruction"])


class TestReorganize(unittest.TestCase):
    def test_a_cause_is_required(self):
        state = fresh_petrograd()
        self.assertTrue(parties.reorganize(state, chart(state), {"action": "move_delegates", "from": "x", "to": "y"})["refused"])

    def test_delegates_drift_a_few_at_a_time(self):
        state = fresh_petrograd()
        r = reorg(state, action="move_delegates", **{"from": "Mensheviks (defensist)", "to": "Menshevik-Internationalists"}, seats=60)
        self.assertEqual(r["seats"], 15)  # 10% of 155
        self.assertIn("note", r)
        reorg(state, action="move_delegates", **{"from": "Non-party soldiers' delegates", "to": "Bolsheviks (district militants)"}, seats=5)
        third = reorg(state, action="move_delegates", **{"from": "Non-party soldiers' delegates", "to": "Bolsheviks (district militants)"}, seats=5)
        self.assertTrue(third["refused"])
        self.assertEqual(sum(f["seats"] for f in chart(state)["data"]["factions"]), 1000)

    def test_a_new_wing_needs_the_bloc_to_have_divided_on_the_floor(self):
        state = fresh_petrograd()
        src = "Bolsheviks (Kamenev's moderates)"
        args = dict(action="new_wing", name="Bolsheviks (Zinoviev's followers)", seats=40, **{"from": src})
        r = reorg(state, **args)
        self.assertTrue(r["refused"])
        self.assertEqual((r["strain"], r["needed"]), (0, parties.NEW_WING_STRAIN))
        chart(state)["data"]["strain"]["blocs"][src] = parties.NEW_WING_STRAIN
        r = reorg(state, **args)
        self.assertTrue(r["done"])
        self.assertEqual(len(parties.wings(chart(state)["data"]["factions"], BOL)), 3)
        self.assertEqual(chart(state)["data"]["strain"]["blocs"][src], 0)
        # a third Bolshevik wing is the limit, and the body can't regroup again for two weeks
        chart(state)["data"]["strain"]["blocs"]["Bolsheviks (district militants)"] = 5
        self.assertTrue(reorg(state, action="new_wing", name="Bolsheviks (Vyborg)", **{"from": "Bolsheviks (district militants)"})["refused"])
        chart(state)["data"]["strain"]["blocs"]["Mensheviks (defensist)"] = 5
        r = reorg(state, action="new_wing", name="Mensheviks (Potresov)", **{"from": "Mensheviks (defensist)"})
        self.assertIn("at the earliest", r["reason"])
        state["date"] = "1917-09-20"
        self.assertTrue(reorg(state, action="new_wing", name="Mensheviks (Potresov)", **{"from": "Mensheviks (defensist)"})["done"])

    def test_a_wing_breaks_away_only_after_repeatedly_voting_against_its_party(self):
        state = fresh_petrograd()
        left = "Left Socialist-Revolutionaries"
        r = reorg(state, action="break_away", wing=left)
        self.assertTrue(r["refused"])
        chart(state)["data"].setdefault("strain", {"blocs": {}, "wings": {}})["wings"][left] = parties.BREAK_STRAIN
        r = reorg(state, action="break_away", wing=left, new_party="Party of Left SRs", note="Spiridonova's party.")
        self.assertTrue(r["done"])
        data = chart(state)["data"]
        self.assertEqual(parties._find(data["factions"], left)["party"], "Party of Left SRs")
        self.assertEqual(len(parties.wings(data["factions"], SR)), 1)  # what's left of the SRs is one bloc
        self.assertEqual(data["parties"]["Party of Left SRs"], "Spiridonova's party.")
        data["strain"]["wings"]["Menshevik-Internationalists"] = 9
        self.assertIn("at the earliest", reorg(state, action="break_away", wing="Menshevik-Internationalists")["reason"])

    def test_found_party_and_merge(self):
        state = fresh_petrograd()
        self.assertTrue(reorg(state, action="found_party", bloc="Non-party soldiers' delegates", new_party="Soldiers' party")["done"])
        self.assertTrue(reorg(state, action="found_party", bloc="Bolsheviks (district militants)")["refused"])
        state["date"] = "1917-09-30"
        r = reorg(state, action="merge_wings", **{"from": "Bolsheviks (Kamenev's moderates)", "into": "Bolsheviks (district militants)"})
        self.assertTrue(r["done"])
        self.assertEqual(parties._find(chart(state)["data"]["factions"], "Bolsheviks (district militants)")["seats"], 400)


class TestTheGmCannotGoAroundIt(unittest.TestCase):
    def update(self, state, data):
        return mechanics.apply_mechanic_update(state, mechanics.load_catalog("petrograd_1917"),
                                               {"action": "update", "type_key": "petrograd_soviet_composition", "instance_id": "petrograd_soviet", "data": data, "reasoning": "x"})

    def test_party_membership_is_fixed_after_creation(self):
        state = fresh_petrograd()
        data = copy.deepcopy(chart(state)["data"])
        data["strain"] = {"blocs": {"x": 1}, "wings": {}}
        chart(state)["data"]["strain"] = {"blocs": {"x": 1}, "wings": {}}
        new = copy.deepcopy(data)
        parties._find(new["factions"], "Left Socialist-Revolutionaries")["party"] = "Left SR party"
        new["factions"][2]["party"] = BOL  # the soldiers can't be annexed by an update
        new["parties"] = {"Left SR party": "invented"}
        new["strain"] = {}
        self.update(state, new)
        after = chart(state)["data"]
        self.assertEqual(parties._find(after["factions"], "Left Socialist-Revolutionaries")["party"], SR)
        self.assertIsNone(after["factions"][2].get("party"))
        self.assertNotIn("Left SR party", after["parties"])
        self.assertEqual(after["strain"], {"blocs": {"x": 1}, "wings": {}})

    def test_an_update_cannot_bring_back_a_party_as_one_bloc(self):
        state = fresh_petrograd()
        new = copy.deepcopy(chart(state)["data"])
        bol = [f for f in new["factions"] if f.get("party") == BOL]
        new["factions"] = [{"name": BOL, "seats": 400, "lean": 3, "note": ""}] + [f for f in new["factions"] if f not in bol]
        changes = self.update(state, new)
        self.assertEqual(len(parties.wings(chart(state)["data"]["factions"], BOL)), 2)
        self.assertTrue(any("party with wings" in str(c) for c in changes))


class TestEngine(unittest.TestCase):
    def test_the_tool_and_the_prompt(self):
        state = fresh_petrograd()
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("PARTIES AND WINGS", prompt)
        self.assertIn("Socialist-Revolutionaries 250 (Left Socialist-Revolutionaries 70", prompt)
        _, events, r = run(state, "reorganize_blocs", {"instance_id": "petrograd_soviet", "action": "break_away",
                                                        "wing": "Left Socialist-Revolutionaries", "cause": "They walked out of the SR caucus."})
        self.assertTrue(r["refused"])
        self.assertTrue(any(e["type"] == "blocs" and e["refused"] for e in events))


if __name__ == "__main__":
    unittest.main()
