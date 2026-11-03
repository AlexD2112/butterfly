"""Memory: ask what your character would know, without spending a turn (2026-11-03)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import providers
import server


class P:
    def __init__(self, answer="You know Kamenev from exile."):
        self.answer, self.prompts, self.convs, self.specs = answer, [], [], []
    def new_conversation(self): return []
    def append_user_message(self, c, t): c.append({"role": "user", "content": t})
    def call(self, model, sp, specs, conv, force_tool_name=None):
        self.prompts.append(sp); self.convs.append(list(conv)); self.specs.append(specs)
        return providers.TurnResult(text_parts=[self.answer], tool_calls=[], usage={
            "provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 0, "cache_creation_tokens": 0})
    def append_assistant_turn(self, c, t): pass
    def append_tool_results(self, c, t, r): pass


class TestMemory(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "t.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.user = db.get_or_create_discord_user("5", "P", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user["id"]
        self.cid = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"}).get_json()["character_id"]

    def tearDown(self):
        db.DB_FILE = self._orig
        self._tmpdir.cleanup()

    def ask(self, q, provider):
        with patch.object(providers, "get_provider", return_value=(provider, "m")):
            return self.client.post(f"/api/character/{self.cid}/ask", json={"question": q, "model_key": "luna"})

    def test_answers_without_changing_the_game_and_bills_it(self):
        before = db.get_character(self.cid, self.user["id"])["state"]
        budget_before = db.get_budget_status(self.user["id"])["remaining_usd"]
        p = P()
        res = self.ask("Who is Kamenev to me?", p)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["answer"], "You know Kamenev from exile.")
        after = db.get_character(self.cid, self.user["id"])["state"]
        for k in ("date", "turn_number", "resources", "relationships", "world"):
            self.assertEqual(before.get(k), after.get(k))
        self.assertEqual(after["memory_log"][-1]["q"], "Who is Kamenev to me?")
        self.assertLess(db.get_budget_status(self.user["id"])["remaining_usd"], budget_before)
        self.assertEqual(p.specs[0], [])  # no tools: it can't touch the game
        self.assertEqual(db.get_turns(self.cid), [])  # not a turn

    def test_prompt_pins_the_date_and_hides_future_canon(self):
        p = P()
        self.ask("What happens in October?", p)
        prompt = p.prompts[0]
        self.assertIn("Nothing after 1917-09-04", prompt)
        known = prompt[prompt.index("Events that have already happened"):prompt.index("Recent play")]
        self.assertIn("trotsky released from kresty", known)
        self.assertNotIn("october revolution", known.lower())

    def test_follow_ups_see_the_last_answers_and_stay_out_of_the_gm_prompt(self):
        self.ask("Who is Kamenev?", P())
        p = P("He is cautious.")
        self.ask("And what does he want?", p)
        self.assertEqual(p.convs[0][0]["content"], "Who is Kamenev?")
        state = db.get_character(self.cid, self.user["id"])["state"]
        self.assertNotIn("Who is Kamenev?", engine.build_system_prompt(state, "petrograd_1917", "red_admiral"))

    def test_guest_profile_cannot_ask(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertIn(self.client.post(f"/api/character/{self.cid}/ask", json={"question": "x"}).status_code, (403, 404))


if __name__ == "__main__":
    unittest.main()
