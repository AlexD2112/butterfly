"""Assemblies meet in sittings and take days (2026-11-03)."""

import unittest

from tests.test_hard_mechanics import argued, fresh_petrograd, run
from tests.test_contested_votes import BLOCS


def vote(state, motion, **kw):
    inp = {"instance_id": "petrograd_soviet", "motion": motion, "player_position": "for",
           "bloc_stances": {b: 1 for b in BLOCS}, **kw}
    argued(state, kw.get("motion_key") or motion)
    return run(state, "call_vote", inp)[2]


class TestSittings(unittest.TestCase):
    def test_a_sitting_gets_through_only_a_few_votes(self):
        state = fresh_petrograd()
        results = [vote(state, f"resolution {i}") for i in range(4)]
        self.assertTrue(all("passed" in r for r in results[:3]))
        self.assertEqual(results[3]["reason"], "sitting_over")
        self.assertEqual(vote(state, "order of business", weight="procedural").get("reason"), "sitting_over")

    def test_the_next_sitting_is_days_away(self):
        state = fresh_petrograd()
        vote(state, "a")
        run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 1})
        self.assertEqual(vote(state, "b")["reason"], "not_sitting")
        run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 2})
        self.assertIn("passed", vote(state, "b"))

    def test_a_major_question_is_tabled_unless_urgency_carries(self):
        state = fresh_petrograd()
        r = vote(state, "No confidence in the presidium", weight="major", motion_key="no_confidence")
        self.assertEqual(r["reason"], "tabled")
        r2 = vote(state, "No confidence in the presidium", weight="major", motion_key="no_confidence", urgency=True)
        self.assertEqual(r2["threshold"], "two_thirds")
        state2 = fresh_petrograd()
        vote(state2, "No confidence", weight="major", motion_key="nc")
        run(state2, "propose_state_update", {"reasoning": "x", "days_elapsed": 3})
        self.assertIn("passed", vote(state2, "No confidence", weight="major", motion_key="nc"))

    def test_election_ballots_count_for_half(self):
        state = fresh_petrograd()
        inp = {"instance_id": "petrograd_soviet", "office": "Chairman", "candidates": ["A", "B"], "bloc_support": {}}
        outs = []
        for _ in range(7):
            argued(state, "Chairman", 2)
            outs.append(run(state, "call_election", inp)[2])
        self.assertTrue(all("results" in o for o in outs[:6]))
        self.assertEqual(outs[6]["reason"], "sitting_over")


if __name__ == "__main__":
    unittest.main()
