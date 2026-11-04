"""Debate pace (2026-11-03): assemblies argue over several turns before they vote; quick pace makes
the case and votes in one exchange; parties shown as one group with wings."""

import random
import unittest
from unittest.mock import patch

import council
import debate
import engine
import mechanics
import operations
from tests.test_contested_votes import BLOCS, SYSTEM
from tests.test_hard_mechanics import fresh_petrograd, run


def put(state, motion="Peace now", **kw):
    inp = {"instance_id": "petrograd_soviet", "motion": motion, "player_position": "for",
           "bloc_stances": {b: 1 for b in BLOCS}, **kw}
    return run(state, "call_vote", inp)[2]


def speak(state, speaker="player", side="for", motion="Peace now", **kw):
    return run(state, "debate_speech", {"instance_id": "petrograd_soviet", "motion": motion, "speaker": speaker,
                                        "side": side, "point": "The war is lost.", **kw})


class TestDebatePace(unittest.TestCase):
    def test_debate_is_the_default_and_a_question_must_be_argued_before_it_is_put(self):
        state = fresh_petrograd()
        self.assertEqual(debate.pace(state), "debate")
        r = put(state)
        self.assertEqual(r["reason"], "debate_first")
        self.assertEqual(r["speeches_needed"], 2)
        speak(state)
        speak(state, "dan", "against")
        self.assertIn("passed", put(state))

    def test_a_major_question_needs_four_speeches_and_procedure_none(self):
        state = fresh_petrograd()
        self.assertIn("passed", put(state, "Adjourn", weight="procedural"))
        argued = debate.vote_ready(state, state["mechanics"]["petrograd_soviet"], {"motion": "nc", "weight": "major"})
        self.assertEqual(argued["speeches_needed"], 4)

    def test_quick_pace_votes_at_once_and_skips_sittings(self):
        state = fresh_petrograd()
        state["pace"] = "quick"
        results = [put(state, f"resolution {i}") for i in range(6)]
        self.assertTrue(all("passed" in r for r in results))
        self.assertIn("passed", put(state, "No confidence", weight="major"))

    def test_speeches_move_the_room_and_the_vote(self):
        state = fresh_petrograd()
        chart = state["mechanics"]["petrograd_soviet"]
        with patch.object(random, "randint", return_value=20):
            r = debate.speech(state, "petrograd_1917", SYSTEM, chart, {"motion": "q", "speaker": "player", "side": "for", "point": "x"})
        self.assertEqual(r["roll"]["degree"], "critical_success")
        self.assertGreater(chart["data"]["debates"]["q"]["swing"], 0)

        def yes(debate_arg):
            s = fresh_petrograd()
            out, _ = operations.call_vote(s, SYSTEM, {"instance_id": "petrograd_soviet", "motion": "q", "player_position": "for",
                                                      "bloc_stances": {b: 0 for b in BLOCS}}, rng=random.Random(3), debate=debate_arg)
            return out["yes"]
        self.assertGreater(yes((0.25, 0.6)), yes(None) + 30)

    def test_the_same_speaker_matters_less_each_time(self):
        state = fresh_petrograd()
        chart = state["mechanics"]["petrograd_soviet"]
        swings = []
        with patch.object(random, "randint", return_value=20):
            for _ in range(3):
                before = chart["data"].get("debates", {}).get("q", {}).get("swing", 0)
                debate.speech(state, "petrograd_1917", SYSTEM, chart, {"motion": "q", "speaker": "player", "side": "for", "point": "x"})
                swings.append(chart["data"]["debates"]["q"]["swing"] - before)
        self.assertGreater(swings[0], swings[1])
        self.assertGreater(swings[1], swings[2])

    def test_npcs_speak_with_their_own_sheets_and_strangers_cannot(self):
        state = fresh_petrograd()
        _, events, r = speak(state, "kamenev")
        self.assertEqual(r["speaker"], "Lev Kamenev")
        self.assertTrue(any(e["type"] == "debate" for e in events))
        self.assertIn("error", speak(state, "a man in the gallery")[2])

    def test_election_ballots_need_debate_between_them_and_it_moves_votes(self):
        state = fresh_petrograd()
        inp = {"instance_id": "petrograd_soviet", "office": "Chairman", "candidates": ["Trotsky", "Chkheidze"],
               "bloc_support": {}}
        self.assertIn("results", run(state, "call_election", inp)[2])
        again = run(state, "call_election", inp)[2]
        self.assertEqual(again["reason"], "debate_first")
        for who in ("player", "kamenev"):
            run(state, "debate_speech", {"instance_id": "petrograd_soviet", "office": "Chairman", "candidate": "Trotsky",
                                         "speaker": who, "side": "for", "point": "He led the Soviet in 1905."})
        self.assertIn("results", run(state, "call_election", inp)[2])

        def trotsky(swing):
            out, _ = operations.call_election(fresh_petrograd(), SYSTEM, inp, rng=random.Random(5), candidate_swing=swing)
            return next(x["votes"] for x in out["results"] if x["candidate"] == "Trotsky")
        self.assertGreater(trotsky({"trotsky": 0.25}), trotsky(None) + 50)

    def test_the_prompt_tells_the_gm_to_wait_for_the_players_words(self):
        state = fresh_petrograd()
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("PACE: debate", prompt)
        self.assertIn("Never write his speech for him", prompt)
        self.assertIn("The player speaks for himself", council.VOICE_GUIDANCE)
        state["pace"] = "quick"
        self.assertIn("PACE: quick", engine.build_system_prompt(state, "petrograd_1917", "red_admiral"))


class TestParties(unittest.TestCase):
    def test_the_bolshevik_wings_belong_to_one_party(self):
        data = fresh_petrograd()["mechanics"]["petrograd_soviet"]["data"]
        wings = [f for f in data["factions"] if f.get("party") == "Bolsheviks"]
        self.assertEqual(len(wings), 2)
        self.assertEqual(sum(f["seats"] for f in wings), 400)
        self.assertIn("Bolsheviks", data["parties"])

    def test_a_stance_for_the_party_covers_its_wings(self):
        r, _ = operations.call_vote(fresh_petrograd(), SYSTEM, {"instance_id": "petrograd_soviet", "motion": "x", "player_position": "for",
                                                                "bloc_stances": {"Bolsheviks": -2}}, rng=random.Random(1))
        wings = [b for b in r["blocs"] if b.get("party") == "Bolsheviks"]
        self.assertTrue(all(b["stance"] == -2 for b in wings))
        self.assertTrue(all(w["name"] not in r.get("assumed_neutral", []) for w in wings))

    def test_a_gm_update_keeps_parties_and_the_debate_record(self):
        state = fresh_petrograd()
        chart = state["mechanics"]["petrograd_soviet"]
        chart["data"]["debates"] = {"q": {"speeches": [1, 2]}}
        incoming = {**chart["data"], "debates": {}, "factions": [{k: v for k, v in f.items()} for f in chart["data"]["factions"]]}
        clean, errors, _ = mechanics.validate_seat_chart(incoming, chart["data"])
        self.assertEqual(clean["debates"], {"q": {"speeches": [1, 2]}})
        self.assertIn("Bolsheviks", clean["parties"])
        self.assertTrue(any(f.get("party") == "Bolsheviks" for f in clean["factions"]))

    def test_an_unknown_pace_falls_back_to_debate(self):
        self.assertEqual(debate.pace({"pace": "nonsense"}), "debate")
        self.assertEqual(debate.pace({"pace": "quick"}), "quick")


if __name__ == "__main__":
    unittest.main()
