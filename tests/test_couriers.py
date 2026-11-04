"""Letters and couriers take real time (2026-11-03): Lenin in Finland can't answer a letter in the
same scene it was sent."""

import unittest

import couriers
import engine
import knowledge
from tests.test_hard_mechanics import fresh_petrograd, run


class TestCouriers(unittest.TestCase):
    def setUp(self):
        self.state = fresh_petrograd()
        self.state["geo"] = {"player": "smolny_institute"}

    def test_a_letter_to_lenin_takes_days_and_he_knows_nothing_until_then(self):
        _, _, r = run(self.state, "send_message", {"to": "Lenin", "text": "We have the coalition; no premature rising."})
        self.assertNotIn("delivered", r)
        self.assertGreater(r["arrives"], self.state["date"])
        fact = self.state["_knowledge"][-1]
        self.assertFalse(knowledge.knows(self.state, "petrograd_1917", "lenin", fact))
        # Lenin can't reply to it yet
        _, _, r2 = run(self.state, "npc_message", {"from": "Lenin", "text": "Is this a road to power?", "about_fact": fact["id"]})
        self.assertIn("doesn't know", r2["error"])
        # days pass: the letter arrives, then he knows
        run(self.state, "propose_state_update", {"reasoning": "x", "days_elapsed": 3})
        self.assertTrue(knowledge.knows(self.state, "petrograd_1917", "lenin", fact))

    def test_his_reply_is_on_the_road_before_it_arrives(self):
        _, _, r = run(self.state, "npc_message", {"from": "Lenin", "text": "Take power now."})
        self.assertIn("Don't narrate it arriving", r["note"])
        prompt = engine.build_system_prompt(self.state, "petrograd_1917", "red_admiral")
        self.assertIn("courier from Vladimir Lenin to the player - arrives", prompt)
        self.assertIn("Vladimir Lenin [lenin] at Helsingfors", prompt)

    def test_someone_in_the_city_gets_a_note_within_the_scene(self):
        _, _, r = run(self.state, "send_message", {"to": "Kamenev", "text": "Meet me at nine."})
        self.assertTrue(r["delivered"])
        self.assertTrue(knowledge.knows(self.state, "petrograd_1917", "kamenev", self.state["_knowledge"][-1]))

    def test_telegrams_are_fast_but_the_government_reads_them(self):
        _, _, r = run(self.state, "send_message", {"to": "Lenin", "text": "x", "means": "telegram"})
        self.assertTrue(r["delivered"])
        self.assertIn("government", r["warning"])
        self.assertTrue(knowledge.knows(self.state, "petrograd_1917", "kerensky", self.state["_knowledge"][-1]))

    def test_moving_someone(self):
        run(self.state, "npc_location", {"npc": "lenin", "at": "Vyborg"})
        _, _, r = run(self.state, "send_message", {"to": "Lenin", "text": "x"})
        self.assertEqual(r["where"], "Vyborg")

    def test_other_settings_have_distance_too(self):
        state = engine.load_premade_character_state("britannia_876")
        prompt = engine.build_system_prompt(state, "britannia_876", "monarch")
        self.assertIn("Guthrum [guthrum] at Exeter", prompt)


if __name__ == "__main__":
    unittest.main()
