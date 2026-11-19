"""Style check (2026-11-18): the habits reported from play are caught, scrubbed where safe, and fed back."""

import unittest

import council
import engine
import negotiation
import style
from tests.test_hard_mechanics import fresh_petrograd

# Real endings and lines from the reported session.
WAITING = ('Kamenev lowers his voice.\n\n"I will not denounce you tonight. But tell me plainly: are these your personal '
           'assurances?"\n\nThe bell sounds again. Kamenev waits for your answer before entering.')
VERDICT = ("Dan rises at once.\n\nYou have made him explain himself before the whole committee, and that changes the room. "
           "Dan is no longer the man blocking the door. He is the man defending the door while everyone watches the hinges.")
EPIGRAM = 'She folds her arms.\n\n"Beside you, yes. Beneath you, no. We will defend the committees."'
QUESTIONS = ('He leans nearer.\n\n"Name the safeguards. Who sits in the new presidium? How are decisions made? What prevents '
             'the Bolsheviks from discarding it?"')
GOOD = ('The doors open and the corridor fills with delegates shaking rain off their coats. Spiridonova is already at the '
        'Left SR benches, saying something to Kamkov that makes him laugh.\n\n"You are late," Kamenev says, and hands you '
        'the agenda. Somebody has written DAN in the margin and underlined it twice.')


class TestLint(unittest.TestCase):
    def test_the_reported_habits_are_caught(self):
        self.assertIn("it ended on someone waiting for the player", style.lint(WAITING))
        self.assertIn("it used a bell as punctuation", style.lint(WAITING))
        self.assertTrue(any("explained the moment" in n for n in style.lint(VERDICT)))
        self.assertTrue(any("epigram" in n for n in style.lint(EPIGRAM)))
        self.assertTrue(any("string of questions" in n for n in style.lint(QUESTIONS)))
        self.assertTrue(any("game numbers" in n for n in style.lint("The hall cheers.\n\nProgress: +20 XP!")))

    def test_good_prose_passes(self):
        self.assertEqual(style.lint(GOOD), [])


class TestScrub(unittest.TestCase):
    def test_a_closing_waiting_sentence_goes(self):
        self.assertEqual(style.scrub('"Fine," she says. "Two seats."\n\nShe waits, paper in hand.'), '"Fine," she says. "Two seats."')
        self.assertTrue(style.scrub(WAITING).endswith("The bell sounds again."))

    def test_dialogue_and_untouched_text_stay(self):
        self.assertEqual(style.scrub('"I am waiting for your answer," Dan says.'), '"I am waiting for your answer," Dan says.')
        self.assertEqual(style.scrub("Rolling...\nDone."), "Rolling...\nDone.")
        self.assertNotIn("XP", style.scrub("The hall cheers.\n📈 Progress: +20 XP!\nDan sits."))


class TestFeedback(unittest.TestCase):
    def test_notes_and_endings_reach_the_next_prompt(self):
        state = fresh_petrograd()
        style.record(state, WAITING)
        style.record(state, VERDICT)
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("STYLE CHECK ON YOUR RECENT REPLIES", prompt)
        self.assertIn("explained the moment", prompt)
        self.assertIn("End this one differently", prompt)
        self.assertTrue(prompt.rstrip().endswith("End this one differently."))  # last thing the model reads
        self.assertNotIn("_style", engine.build_state_digest(state))

    def test_the_voice_rules_quote_the_bad_lines_and_drop_the_waiting_ending(self):
        self.assertIn("Beside you, yes. Beneath you, no.", council.VOICE_GUIDANCE)
        self.assertNotIn("End with the other person waiting", council.VOICE_GUIDANCE)
        self.assertNotIn("a person waiting for a reply", council.VOICE_GUIDANCE)


class TestDemandText(unittest.TestCase):
    def test_slug_demands_read_as_words_and_are_not_duplicated(self):
        state = fresh_petrograd()
        negotiation.open_negotiation(state, "spiridonova", "Maria Spiridonova",
                                     {"topic": "votes", "stakes": "significant", "demands": ["Land to the committees"]})
        negotiation.update(state, "spiridonova", {"add_demands": ["a_public_statement_before_tonights_vote", "land_to_the_committees"]})
        texts = [d["text"] for d in state["negotiations"]["spiridonova"]["demands"]]
        self.assertEqual(texts, ["Land to the committees", "A public statement before tonights vote"])


if __name__ == "__main__":
    unittest.main()
