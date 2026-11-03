"""Saved openings (shown again when a game is reopened, and given to the GM) and deleting games."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import providers
import server

SETTINGS = ["petrograd_1917", "britannia_876", "rome_59_bce", "crusades_1097"]


class TestOpeningFiles(unittest.TestCase):
    def test_every_setting_has_both_openings_without_gm_text_leaking(self):
        for key in SETTINGS:
            for premade in (True, False):
                with self.subTest(setting=key, premade=premade):
                    text, note = engine.opening_parts(key, premade=premade)
                    self.assertGreater(len(text.split()), 80)
                    self.assertTrue(note)
                    self.assertNotIn("GM", text)
                    self.assertNotIn("<!--", text)
                    self.assertNotIn("What do you do", text)
                    self.assertNotIn("\n", text.replace("\n\n", ""))  # unwrapped paragraphs

    def test_legacy_opening_picked_by_creation_date(self):
        state = {}
        old = engine.opening_for_existing("petrograd_1917", state, created_at=time.mktime((2026, 10, 1, 0, 0, 0, 0, 0, 0)))
        mid = engine.opening_for_existing("petrograd_1917", state, created_at=time.mktime((2026, 10, 30, 0, 0, 0, 0, 0, 0)))
        self.assertIn("Smolny Institute", old)       # the 25 September opening
        self.assertIn("Kresty prison swings open", mid)  # the first 4 September opening
        self.assertNotIn("GM note", old + mid)
        self.assertEqual(engine.opening_for_existing("petrograd_1917", {"opening_text": "saved"}, 0), "saved")


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "t.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.user = db.get_or_create_discord_user("7", "P", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def premade(self, key="petrograd_1917"):
        return self.client.post("/api/characters", json={"type": "premade", "setting_key": key}).get_json()["character_id"]


class TestOpeningsServer(ServerCase):
    def test_premade_opening_is_saved_and_survives_file_changes(self):
        cid = self.premade()
        first = self.client.get(f"/api/character/{cid}/opening_scene").get_json()["text"]
        self.assertIn("Kamenev", first)
        with patch.object(engine, "opening_parts", return_value=("a rewritten opening", "")):
            again = self.client.get(f"/api/character/{cid}/opening_scene").get_json()["text"]
        self.assertEqual(first, again)

    def test_gm_sees_the_opening_on_the_first_turn(self):
        cid = self.premade()

        class P:
            def __init__(s): s.sent = None
            def new_conversation(s): return []
            def append_user_message(s, c, t): c.append({"role": "user", "content": t})
            def call(s, model, sp, specs, conv, force_tool_name=None):
                s.sent = [dict(m) for m in conv]
                return providers.TurnResult(text_parts=["Natalia nods."], tool_calls=[], usage=None)
            def append_assistant_turn(s, c, t): pass
            def append_tool_results(s, c, t, r): pass
        p = P()
        with patch.object(providers, "get_provider", return_value=(p, "m")):
            self.client.post(f"/api/character/{cid}/turn", json={"input": "I take the coat.", "model_key": "luna"})
        self.assertIn("Kamenev", p.sent[1]["content"])
        self.assertIn("GM note", p.sent[1]["content"])
        self.assertEqual(p.sent[-1]["content"], "I take the coat.")


class TestDelete(ServerCase):
    def test_delete_removes_game_and_history_but_keeps_spend(self):
        cid = self.premade()
        db.record_turn(cid, 0, "x", "y", "luna", [])
        db.record_usage(self.user["id"], cid, "openai", "m", 10, 10, 0, 0, 0.01)
        spent_before = db.get_budget_status(self.user["id"])["remaining_usd"]
        res = self.client.delete(f"/api/character/{cid}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/api/characters").get_json()["characters"], [])
        self.assertEqual(db.get_turns(cid), [])
        self.assertEqual(db.get_budget_status(self.user["id"])["remaining_usd"], spent_before)

    def test_cannot_delete_someone_elses_game(self):
        cid = self.premade()
        other = db.get_or_create_discord_user("8", "Q", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = other["id"]
        self.assertEqual(self.client.delete(f"/api/character/{cid}").status_code, 404)
        self.assertIsNotNone(db.get_character(cid, self.user["id"]))


if __name__ == "__main__":
    unittest.main()
