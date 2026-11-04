"""Negotiations (2026-11-03): a position track, demands that offers are judged against, failures that
stall instead of losing ground, and an NPC who stays until the talk is done."""

import json
import unittest
from unittest.mock import patch

import engine
import negotiation
from tests.test_hard_mechanics import fresh_petrograd, run, fixed_check

SYSTEM = engine.get_system("red_admiral")
OPEN = {"npc": "Spiridonova", "topic": "Left SR votes tonight", "stakes": "significant",
        "demands": ["Land to the peasant committees at once", "An immediate peace"]}


def offer(state, degree, **kw):
    inp = {"npc": "spiridonova", "offer": "x", "attribute": "presence", "skill": "personal_persuasion", **kw}
    with patch.object(SYSTEM, "roll_check", side_effect=fixed_check(degree)):
        return run(state, "negotiation_offer", inp)


class TestNegotiation(unittest.TestCase):
    def setUp(self):
        self.state = fresh_petrograd()
        _, _, self.opened = run(self.state, "open_negotiation", OPEN)

    def n(self):
        return self.state["negotiations"]["spiridonova"]

    def test_opens_from_trust_with_demands_written_down(self):
        self.assertEqual(self.n()["position"], 0)
        self.assertEqual(self.n()["goal"], 2)
        self.assertEqual([d["id"] for d in self.n()["demands"]], ["land_to_the_peasant_committees_at_once", "an_immediate_peace"])
        self.assertIn("stays", self.opened["note"])

    def test_meeting_demands_lowers_the_dc_and_dodging_raises_it(self):
        _, events, r = offer(self.state, "failure", meets=["land_to_the_peasant_committees_at_once", "an_immediate_peace"])
        check = next(e for e in events if e["type"] == "check")
        self.assertEqual(check["dc"], 14 - 4)  # her base 14 (Resolve), two demands met
        state2 = fresh_petrograd()
        run(state2, "open_negotiation", OPEN)
        _, events, _ = offer(state2, "failure", dodges=["land"])
        self.assertEqual(next(e for e in events if e["type"] == "check")["dc"], 16)

    def test_failure_stalls_and_only_a_critical_failure_loses_ground(self):
        _, _, r = offer(self.state, "failure", at_stake="left_sr_alliance")
        self.assertEqual(r["moved"], 0)
        before = self.state["resources"]["left_sr_alliance"]
        _, _, r = offer(self.state, "critical_failure", at_stake="left_sr_alliance")
        self.assertEqual(r["moved"], -1)
        self.assertLess(self.state["resources"]["left_sr_alliance"], before)

    def test_no_deal_while_a_demand_is_unanswered(self):
        offer(self.state, "strong_success", meets=["land"])
        _, _, r = offer(self.state, "strong_success")
        self.assertFalse(r["agreed"])
        self.assertEqual(r["position"], 1)
        self.assertIn("unmet demand", r["gm_instruction"])
        _, _, r = offer(self.state, "success", meets=["peace"])
        self.assertTrue(r["agreed"])
        self.assertEqual(self.n()["status"], "agreed")

    def test_she_cannot_be_settled_on_the_side_or_walk_off(self):
        _, _, r = run(self.state, "request_check", {"attribute": "presence", "skill": "personal_persuasion", "dc": 10,
                                                    "target_npc": "spiridonova", "description": "x"})
        self.assertIn("negotiation_offer", r["error"])
        _, _, r = run(self.state, "close_negotiation", {"npc": "spiridonova", "reason": "the bell rang"})
        self.assertIn("error", r)
        prompt = engine.build_system_prompt(self.state, "petrograd_1917", "red_admiral")
        self.assertIn("Maria Spiridonova STAYS in this conversation", prompt)
        _, _, r = run(self.state, "close_negotiation", {"npc": "spiridonova", "reason": "player_walked_away"})
        self.assertEqual(r["reason"], "player_walked_away")

    def test_days_passing_closes_open_talks(self):
        run(self.state, "propose_state_update", {"reasoning": "x", "days_elapsed": 1})
        self.assertEqual(self.n()["status"], "closed")

    def test_a_raised_price_adds_a_demand(self):
        run(self.state, "negotiation_update", {"npc": "spiridonova", "add_demands": ["Two Left SR seats on the presidium"]})
        self.assertEqual(len(self.n()["demands"]), 3)

    def test_works_without_a_sheet(self):
        state = fresh_petrograd()
        _, _, r = run(state, "open_negotiation", {"npc": "A railway union delegate", "topic": "x", "stakes": "minor",
                                                  "demands": ["Wages paid in September"]})
        self.assertEqual(r["opened"]["goal"], 1)
        with patch.object(SYSTEM, "roll_check", side_effect=fixed_check("success")):
            _, events, r = run(state, "negotiation_offer", {"npc": "A railway union delegate", "offer": "pay",
                                                            "attribute": "presence", "meets": ["wages"]})
        self.assertTrue(r["agreed"])
        self.assertEqual(next(e for e in events if e["type"] == "check")["dc"], 10)


if __name__ == "__main__":
    unittest.main()
