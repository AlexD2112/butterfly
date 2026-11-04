"""Who knows what (2026-11-03): private matters stay with the people who were there, until word
spreads in code."""

import json
import random
import unittest
from unittest.mock import patch

import engine
import knowledge
from tests.test_hard_mechanics import fresh_petrograd, run, fixed_check
from tests.test_negotiation import OPEN, SYSTEM


class TestKnowledge(unittest.TestCase):
    def test_a_private_talk_is_known_only_to_who_was_there(self):
        state = fresh_petrograd()
        run(state, "record_knowledge", {"text": "Trotsky promised Spiridonova land at once", "witnesses": ["Spiridonova"]})
        fact = state["_knowledge"][0]
        self.assertTrue(knowledge.knows(state, "petrograd_1917", "spiridonova", fact))
        self.assertFalse(knowledge.knows(state, "petrograd_1917", "martov", fact))
        self.assertFalse(knowledge.knows(state, "petrograd_1917", "lazimir", fact))  # same faction, hasn't heard yet
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("known to: Maria Spiridonova", prompt)
        self.assertIn("Nobody else knows", prompt)

    def test_word_reaches_the_faction_then_maybe_everyone(self):
        state = fresh_petrograd()
        knowledge.record(state, "petrograd_1917", "x", ["spiridonova"], "private")
        rng = random.Random(0)
        days = 0
        while "left_srs" not in state["_knowledge"][0]["groups"] and days < 60:
            changes = knowledge.spread_one_day(state, "petrograd_1917", rng)
            days += 1
        self.assertTrue(knowledge.knows(state, "petrograd_1917", "lazimir", state["_knowledge"][0]))
        self.assertFalse(knowledge.knows(state, "petrograd_1917", "martov", state["_knowledge"][0]) and not state["_knowledge"][0]["public"])
        self.assertTrue(any(c.get("gm_only") for c in changes) or days > 1)

    def test_secrets_leak_much_more_slowly(self):
        leaks = {"private": 0, "secret": 0}
        for secrecy in leaks:
            for seed in range(200):
                state = fresh_petrograd()
                knowledge.record(state, "petrograd_1917", "x", ["spiridonova"], secrecy)
                for _ in range(5):
                    knowledge.spread_one_day(state, "petrograd_1917", random.Random(seed * 7 + _))
                leaks[secrecy] += bool(state["_knowledge"][0]["groups"])
        self.assertGreater(leaks["private"], 3 * leaks["secret"])

    def test_negotiation_offers_are_recorded_privately(self):
        state = fresh_petrograd()
        run(state, "open_negotiation", OPEN)
        with patch.object(SYSTEM, "roll_check", side_effect=fixed_check("success")):
            run(state, "negotiation_offer", {"npc": "spiridonova", "offer": "Seats on the convocation committee", "attribute": "presence"})
        fact = state["_knowledge"][-1]
        self.assertIn("Seats on the convocation committee", fact["text"])
        self.assertEqual(fact["witnesses"], ["spiridonova"])

    def test_telling_someone_adds_them(self):
        state = fresh_petrograd()
        knowledge.record(state, "petrograd_1917", "x", ["spiridonova"])
        run(state, "record_knowledge", {"fact_id": "f1", "witnesses": ["Kamenev"]})
        self.assertIn("kamenev", state["_knowledge"][0]["witnesses"])

    def test_days_passing_spread_word_and_the_page_never_sees_faction_leaks(self):
        state = fresh_petrograd()
        knowledge.record(state, "petrograd_1917", "x", ["spiridonova"])
        with patch.object(knowledge.random, "random", return_value=0.0):
            run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 1})
        self.assertIn("left_srs", state["_knowledge"][0]["groups"])
        digest = engine.build_state_digest(state)
        self.assertNotIn("_knowledge", digest)
        view = knowledge.player_view(state, "petrograd_1917")
        self.assertNotIn("groups", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
