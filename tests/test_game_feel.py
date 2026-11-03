"""Tests for the 2026-11-02 pass: the GM's memory rebuilt from the saved transcript (so it survives
restarts), 30-day logins, suggested actions, and the narration scrub."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import geodata
import providers
import server


class RecordingProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.conversations = []
        self.specs = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.conversations.append([dict(m) if isinstance(m, dict) else m for m in conversation])
        self.specs.append(tool_specs)
        result = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return result

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append({"role": "assistant", "content": " ".join(turn_result.text_parts)})

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append({"role": "user", "content": json.dumps(results)})


def text_turn(text):
    return providers.TurnResult(text_parts=[text], tool_calls=[], usage=None)


class TestRebuildConversation(unittest.TestCase):
    def test_keeps_the_last_turns_with_outcomes(self):
        turns = [
            {"player_input": f"move {i}", "narration": f"reply {i}",
             "debug_events_json": json.dumps([{"type": "check", "attribute": "oration", "skill": None, "degree": "strong_success"}])}
            for i in range(12)
        ]
        conv = engine.rebuild_conversation(RecordingProvider([]), turns)
        self.assertEqual(len(conv), engine.MEMORY_TURNS * 2)
        self.assertEqual(conv[0]["content"], f"move {12 - engine.MEMORY_TURNS}")
        self.assertIn("oration check: strong success", conv[1]["content"])
        self.assertEqual([m["role"] for m in conv[:4]], ["user", "assistant", "user", "assistant"])

    def test_outcome_summary_covers_operations_and_time(self):
        events = [
            {"type": "operation", "operation": "call_vote", "result": {"motion": "No confidence", "passed": True, "yes": 551, "no": 401}, "changes": []},
            {"type": "state_update", "changes": [{"kind": "time", "days": 3, "after": "1917-09-07"},
                                                  {"kind": "resource", "key": "soviet_authority", "before": 44, "after": 52}]},
        ]
        summary = engine.summarize_outcomes(events)
        self.assertIn('vote "No confidence" passed 551-401', summary)
        self.assertIn("3 day(s) passed", summary)
        self.assertIn("soviet_authority 44->52", summary)

    def test_campaign_log_is_bounded_in_the_prompt(self):
        state = engine.load_premade_character_state("petrograd_1917")
        state["campaign_log_summary"] = [{"turn": i, "summary": "x" * 400} for i in range(200)]
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("EARLIER IN THE CAMPAIGN", prompt)
        section = prompt[prompt.index("EARLIER IN THE CAMPAIGN"):]
        self.assertEqual(section.count("\n- turn "), engine.EARLIER_SUMMARIES)
        self.assertNotIn("x" * 300, prompt)


class TestServerMemoryAndLogin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "t.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        user = db.get_or_create_discord_user("9", "P", None)
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        self.cid = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"}).get_json()["character_id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_gm_remembers_previous_turns_after_a_restart(self):
        provider = RecordingProvider([text_turn("The workers cheer.")])
        with patch.object(providers, "get_provider", return_value=(provider, "m")):
            self.client.post(f"/api/character/{self.cid}/turn", json={"input": "I thank the workers.", "model_key": "luna"})
        server._conversations.clear()  # what a deploy/restart used to do to the GM's memory
        provider2 = RecordingProvider([text_turn("Natalia hands you the coat.")])
        with patch.object(providers, "get_provider", return_value=(provider2, "m")):
            self.client.post(f"/api/character/{self.cid}/turn", json={"input": "I go to Natalia.", "model_key": "luna"})
        sent = provider2.conversations[0]
        contents = [m["content"] for m in sent]
        i = contents.index("I thank the workers.")  # after the opening scene (2026-11-03)
        self.assertIn("The workers cheer.", contents[i + 1])
        self.assertEqual(sent[-1]["content"], "I go to Natalia.")

    def test_suggestions_are_returned_and_close_the_turn(self):
        call = providers.TurnResult(text_parts=["Kamenev waits."], tool_calls=[
            {"id": "s", "name": "suggest_actions", "input": {"options": ["Tell Kamenev no", "Go to Smolny", "Go to Smolny."]}}], usage=None)
        provider = RecordingProvider([call, text_turn("should not be reached")])
        with patch.object(providers, "get_provider", return_value=(provider, "m")):
            data = self.client.post(f"/api/character/{self.cid}/turn", json={"input": "x", "model_key": "luna"}).get_json()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(data["narration"], "Kamenev waits.")
        sugg = [e for e in data["debug_events"] if e["type"] == "suggestions"]
        self.assertEqual(sugg[0]["options"], ["Tell Kamenev no", "Go to Smolny"])
        self.assertIn("suggest_actions", {s["name"] for s in provider.specs[0]})

    def test_map_endpoint_serves_only_verified_places(self):
        with patch.object(geodata, "cache_path", return_value=Path(self._tmpdir.name) / "geo.json"):
            data = self.client.get("/api/settings/petrograd_1917/map").get_json()
        self.assertTrue(data["viable"])  # the hand-pinned positions are enough
        self.assertTrue(all(lm.get("qid") or lm.get("osm") for lm in data["landmarks"]))
        self.assertEqual(self.client.get("/api/settings/rome_59_bce/map").status_code, 404)

    def test_state_route_includes_the_clock_for_a_new_game(self):
        data = self.client.get(f"/api/character/{self.cid}/state").get_json()
        self.assertEqual(data["state"]["calendar"]["upcoming"][0]["label"], "Democratic Conference opens")
        self.assertIn("garrison_loyalty", data["state"]["hud"])

    def test_logins_are_persistent(self):
        self.client.get("/api/me")
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.permanent)


class TestScrub(unittest.TestCase):
    def test_trailing_chatbot_questions_are_removed(self):
        self.assertEqual(engine.scrub_narration('He waits.\n\nWhat do you do?'), "He waits.")
        self.assertEqual(engine.scrub_narration("He waits. What will you do next?"), "He waits.")

    def test_dialogue_questions_are_kept(self):
        text = '"What do you want?" Kamenev asks.'
        self.assertEqual(engine.scrub_narration(text), text)

    def test_markdown_and_curly_quotes(self):
        self.assertEqual(engine.scrub_narration("## Smolny\n**Night.** “Go.”"), 'Smolny\nNight. "Go."')

    def test_multiplayer_turns_get_no_suggestions_tool(self):
        provider = RecordingProvider([text_turn("ok")])
        state = engine.load_premade_character_state("petrograd_1917")
        engine.run_turn(provider, "m", state, "petrograd_1917", "red_admiral", [], "x", allow_time_advance=False)
        self.assertNotIn("suggest_actions", {s["name"] for s in provider.specs[0]})


def tools_turn(*calls, text=""):
    return providers.TurnResult(text_parts=[text] if text else [], tool_calls=[
        {"id": f"t{i}", "name": n, "input": x} for i, (n, x) in enumerate(calls)], usage=None)


CHECK = ("request_check", {"attribute": "presence", "skill": "negotiation", "dc": 15, "description": "Seek the chair's recognition"})
SUGGEST = ("suggest_actions", {"options": ["Address Chkheidze", "Ask Kamenev to second you"]})


class TestNarrationGuard(unittest.TestCase):
    def run_script(self, script):
        provider = RecordingProvider(script)
        state = engine.load_premade_character_state("petrograd_1917")
        narration, events = engine.run_turn(provider, "m", state, "petrograd_1917", "red_admiral", [], "x", suggestions=True)
        return provider, narration, events

    def test_a_roll_with_no_scene_sends_the_gm_back_once(self):
        # the reported failure: roll, then state update + suggestions with no text, then nothing
        provider, narration, events = self.run_script([
            tools_turn(CHECK),
            tools_turn(("propose_state_update", {"reasoning": "xp", "xp_award": 20}), SUGGEST),
            text_turn(""),
            text_turn("Chkheidze looks at you over his spectacles and rings the bell. The floor is yours."),
        ])
        self.assertIn("The floor is yours", narration)
        self.assertEqual(provider.calls, 4)
        self.assertIn(engine.NARRATE_NUDGE, [m["content"] for m in provider.conversations[3] if isinstance(m["content"], str)])

    def test_suggestions_before_the_scene_are_bounced(self):
        provider, narration, events = self.run_script([
            tools_turn(CHECK),
            tools_turn(SUGGEST),
            text_turn("Dan half rises, then sits again. Chkheidze nods to you."),
        ])
        self.assertIn("Chkheidze nods", narration)
        self.assertIn(engine.NARRATE_FIRST, provider.conversations[2][-1]["content"])
        self.assertTrue(any(e["type"] == "suggestions" for e in events))

    def test_a_normal_turn_costs_no_extra_call(self):
        provider, narration, _ = self.run_script([
            tools_turn(CHECK, text="You push through the door."),
            tools_turn(SUGGEST, text="Chkheidze gives you the floor."),
        ])
        self.assertEqual(provider.calls, 2)
        self.assertIn("gives you the floor", narration)

    def test_text_before_the_roll_does_not_count_as_narrating_it(self):
        provider, narration, _ = self.run_script([
            tools_turn(CHECK, text="You catch Kamenev's sleeve."),
            text_turn(""),
            text_turn("The chair recognizes you."),
        ])
        self.assertEqual(provider.calls, 3)
        self.assertIn("recognizes you", narration)

    def test_the_gm_is_only_sent_back_once(self):
        provider, narration, _ = self.run_script([tools_turn(CHECK), text_turn(""), text_turn(""), text_turn("never")])
        self.assertEqual(provider.calls, 3)


if __name__ == "__main__":
    unittest.main()
