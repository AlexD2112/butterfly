"""Votes are contested (2026-11-03): stances on the motion, split blocs, undecided delegates on a
first vote, and elections with several candidates and rounds."""

import random
import unittest

import engine
import operations
from tests.test_hard_mechanics import fresh_petrograd, run

SYSTEM = engine.get_system("red_admiral")
BLOCS = ["Bolsheviks (district militants)", "Bolsheviks (Kamenev's moderates)", "Non-party soldiers' delegates", "Left Socialist-Revolutionaries",
         "Menshevik-Internationalists", "Socialist-Revolutionaries (leadership)", "Mensheviks (defensist)"]


def vote(state, stances, seed, key="q"):
    r, _ = operations.call_vote(state, SYSTEM, {"instance_id": "petrograd_soviet", "motion": "x", "motion_key": key,
                                                "player_position": "for", "bloc_stances": dict(zip(BLOCS, stances))},
                                rng=random.Random(seed))
    return r


class TestContestedVotes(unittest.TestCase):
    def test_allies_split_on_a_motion_that_doesnt_serve_them(self):
        r = vote(fresh_petrograd(), [-1, -1, 0, 2, 2, -1, -1], 1)
        bolsh = r["blocs"][0]
        self.assertTrue(bolsh.get("split"))
        self.assertGreater(bolsh["no"], 40)

    def test_a_motion_put_cold_does_worse_than_after_debate(self):
        firsts, thirds = [], []
        for seed in range(30):
            state = fresh_petrograd()
            firsts.append(vote(state, [1, 1, 1, 0, 0, -1, -2], seed)["yes"])
            vote(state, [1, 1, 1, 0, 0, -1, -2], seed + 100)
            thirds.append(vote(state, [1, 1, 1, 0, 0, -1, -2], seed + 200)["yes"])
        self.assertGreater(sum(thirds) / 30, sum(firsts) / 30 + 15)

    def test_what_the_bloc_wants_matters_more_than_loyalty(self):
        state = fresh_petrograd()
        liked = vote(state, [2] * 7, 3, key="a")["yes"]
        disliked = vote(state, [-2] * 7, 3, key="b")["yes"]
        self.assertGreater(liked - disliked, 400)

    def test_missing_stances_are_reported(self):
        r, _ = operations.call_vote(fresh_petrograd(), SYSTEM, {"instance_id": "petrograd_soviet", "motion": "x", "player_position": "for"})
        self.assertEqual(len(r["assumed_neutral"]), 7)


class TestOldSaves(unittest.TestCase):
    def test_an_old_single_bolshevik_bloc_is_split(self):
        import mechanics
        state = fresh_petrograd()
        fac = state["mechanics"]["petrograd_soviet"]["data"]["factions"]
        fac[0:2] = [{"name": "Bolsheviks", "seats": 400, "lean": 3, "note": ""}]
        mechanics.split_old_blocs(state, mechanics.load_catalog("petrograd_1917"))
        names = [f["name"] for f in fac]
        self.assertIn("Bolsheviks (Kamenev's moderates)", names)
        self.assertEqual(sum(f["seats"] for f in fac), 1000)
        self.assertEqual(fac[1]["lean"], 2)


class TestElections(unittest.TestCase):
    def support(self):
        return {"Bolsheviks (district militants)": {"Trotsky": 2, "Kamenev": 0}, "Bolsheviks (Kamenev's moderates)": {"Trotsky": 0, "Kamenev": 2}, "Non-party soldiers' delegates": {"Trotsky": 1, "Kamenev": 0},
                "Left Socialist-Revolutionaries": {"Trotsky": 0, "Martov": 0, "Kamenev": 0},
                "Menshevik-Internationalists": {"Martov": 2}, "Socialist-Revolutionaries (leadership)": {"Chkheidze": 2},
                "Mensheviks (defensist)": {"Chkheidze": 2}}

    def test_a_split_field_often_has_no_majority_the_first_time(self):
        no_majority = 0
        for seed in range(20):
            r, _ = operations.call_election(fresh_petrograd(), SYSTEM, {
                "instance_id": "petrograd_soviet", "office": "Chairman", "ballot": "secret",
                "candidates": ["Trotsky", "Kamenev", "Martov", "Chkheidze"], "bloc_support": self.support()}, rng=random.Random(seed))
            no_majority += r["elected"] is None
            self.assertEqual(sum(x["votes"] for x in r["results"]), r["cast"])
        self.assertGreater(no_majority, 10)

    def test_rounds_are_counted_and_the_tool_runs_through_the_engine(self):
        state = fresh_petrograd()
        inp = {"instance_id": "petrograd_soviet", "office": "Chairman", "candidates": ["Trotsky", "Chkheidze"],
               "bloc_support": self.support()}
        _, _, r1 = run(state, "call_election", inp)
        _, _, r2 = run(state, "call_election", inp)
        self.assertEqual((r1["round"], r2["round"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
