"""Other characters roll for themselves, groups roll together, and the player can reroll
(2026-11-03)."""

import random
import unittest
from unittest.mock import patch

import db
import engine
import npcs
import server
from tests.test_hard_mechanics import fresh_petrograd, run
from tests.test_game_feel import TestWriteTheScene, RecordingProvider, tools_turn, text_turn

SYSTEM = engine.get_system("red_admiral")


class TestNpcRolls(unittest.TestCase):
    def test_kamenev_rolls_with_his_own_sheet_and_bears_it(self):
        state = fresh_petrograd()
        with patch("random.randint", return_value=1):
            _, events, r = run(state, "request_check", {"attribute": "oration", "skill": "debate_and_public_argument",
                                                         "dc": 12, "actor": "kamenev", "description": "Kamenev presents the motion",
                                                         "at_stake": "soviet_authority"})
        self.assertEqual(r["actor_name"], "Lev Kamenev")
        self.assertEqual(r["modifier"], 2 + 2)  # Oration 14 (+2), proficient in debate
        self.assertEqual(r["degree"], "critical_failure")
        self.assertEqual(state["_npc_standing"]["kamenev"], -2)
        self.assertIn("land on them", r["note"])

    def test_rolling_together(self):
        state = fresh_petrograd()
        _, events, r = run(state, "request_check", {"attribute": "presence", "skill": "personal_persuasion", "dc": 12,
                                                     "actors": ["player", "kollontai"], "combine": "best", "description": "x"})
        self.assertEqual(len(r["rolls"]), 2)
        checks = [e for e in events if e["type"] == "check"]
        self.assertEqual(sum(1 for c in checks if c["counts"]), 1)
        _, events, r = run(state, "request_check", {"attribute": "physique", "skill": "stealth_and_evasion", "dc": 12,
                                                     "actors": ["player", "natalia_sedova"], "combine": "each", "description": "slip past"})
        self.assertTrue(all(c["counts"] for c in events if c["type"] == "check"))

    def test_someone_else_gives_the_speech_before_a_vote(self):
        state = fresh_petrograd()
        state["pace"] = "quick"
        _, events, r = run(state, "call_vote", {"instance_id": "petrograd_soviet", "motion": "x", "player_position": "for",
                                                "speech_attribute": "oration", "speech_skill": "debate_and_public_argument",
                                                "speech_by": "kamenev"})
        self.assertEqual(r["speech"]["actor"], "kamenev")

    def test_unknown_actor(self):
        _, _, r = run(fresh_petrograd(), "request_check", {"attribute": "oration", "dc": 10, "actor": "a random sailor", "description": "x"})
        self.assertIn("no sheet", r["error"])


class TestReroll(TestWriteTheScene):
    def test_reroll_undoes_and_mocks_when_overused(self):
        import providers
        mocks = []
        for i in range(4):
            provider = RecordingProvider([tools_turn(("request_check", {"attribute": "oration", "dc": 12, "description": "x"}), text="You speak."),
                                          text_turn("They listen.")])
            with patch.object(providers, "get_provider", return_value=(provider, "m")):
                self.client.post(f"/api/character/{self.cid}/turn", json={"input": "I speak.", "model_key": "luna"})
            res = self.client.post(f"/api/character/{self.cid}/reroll_last").get_json()
            self.assertEqual(res["input"], "I speak.")
            self.assertEqual(db.get_turns(self.cid), [])
            mocks.append(res["mock"])
        self.assertIsNone(mocks[0])
        self.assertIsNotNone(mocks[-1])
        state = self.client.get(f"/api/character/{self.cid}/state").get_json()["state"]
        self.assertEqual(state["turn_number"], 0)


if __name__ == "__main__":
    unittest.main()
