"""((double parentheses)) mark the player's private intent (2026-11-03)."""

import unittest

import engine
from tests.test_game_feel import RecordingProvider, text_turn


class TestPrivateIntent(unittest.TestCase):
    def test_intent_is_labelled_for_the_gm_only(self):
        out = engine.mark_private_intent("I go to Natalia ((I want the chair, but she mustn't know)) and ask her to carry a note.")
        self.assertTrue(out.startswith("I go to Natalia and ask her to carry a note."))
        self.assertIn("[PRIVATE INTENT - for the GM only; no character sees or hears this: I want the chair, but she mustn't know]", out)
        self.assertEqual(engine.mark_private_intent("plain move"), "plain move")

    def test_it_reaches_the_model_marked_and_stays_marked_in_memory(self):
        provider = RecordingProvider([text_turn("ok")])
        state = engine.load_premade_character_state("petrograd_1917")
        engine.run_turn(provider, "m", state, "petrograd_1917", "red_admiral", [], 'I say: "Good evening." ((size him up))')
        self.assertIn("[PRIVATE INTENT", provider.conversations[0][-1]["content"])
        conv = engine.rebuild_conversation(RecordingProvider([]), [{"player_input": "Hello ((secret))", "narration": "Hi.", "debug_events_json": "[]"}])
        self.assertIn("[PRIVATE INTENT", conv[0]["content"])

    def test_the_style_guide_forbids_mind_reading(self):
        import council
        self.assertIn("People can't read minds", council.VOICE_GUIDANCE)
        self.assertIn("Don't open with", council.VOICE_GUIDANCE)


if __name__ == "__main__":
    unittest.main()
