import unittest
from unittest.mock import patch

import council
import providers


def usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedProvider:
    """Routes call() based on force_tool_name/tool_specs so one fake can stand in for the main
    provider AND the judge/router provider across routing, candidate generation, judging, chairman,
    and voice-edit."""

    def __init__(self, judge_scores=None, chairman_result="default", voice_edit_text="EDITED",
                 router_judge_keys=None):
        self.judge_scores = judge_scores if judge_scores is not None else {"candidate_1": 6, "candidate_2": 8}
        self.chairman_result = chairman_result
        self.voice_edit_text = voice_edit_text
        self.router_judge_keys = router_judge_keys if router_judge_keys is not None else [
            "rules_mechanics", "canon_continuity", "groundedness", "premortem",
        ]
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or (tool_specs[0]["name"] if tool_specs else "plain"))
        if force_tool_name == "select_judges":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "r1", "name": "select_judges", "input": {
                    "judge_keys": self.router_judge_keys,
                    "reasoning": "critical success warrants a bigger panel",
                }}],
                usage=usage(),
            )
        if force_tool_name == "submit_judgment":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "j1", "name": "submit_judgment",
                                             "input": {"scores": self.judge_scores, "notes": "ok"}}],
                usage=usage(),
            )
        if force_tool_name == "submit_synthesis":
            if self.chairman_result is None:
                return providers.TurnResult(text_parts=["no tool call"], tool_calls=[], usage=usage())
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c1", "name": "submit_synthesis", "input": {
                    "final_narration": "SYNTHESIZED NARRATION",
                    "reasoning": "took the best bits",
                    "disagreement_notes": "judges disagreed on tone",
                }}],
                usage=usage(),
            )
        if not tool_specs:
            # either the alternate-candidate generator or the voice editor - distinguish by prompt
            if "line editor" in system_prompt:
                return providers.TurnResult(text_parts=[self.voice_edit_text], tool_calls=[], usage=usage())
            return providers.TurnResult(text_parts=["ALTERNATE CANDIDATE TEXT"], tool_calls=[], usage=usage())
        return providers.TurnResult(text_parts=["unexpected"], tool_calls=[], usage=usage())


def make_debug_events():
    return [{"type": "check", "attribute": "oration", "degree": "critical_success", "skill": None,
             "dc": 12, "roll": 20, "modifier": 3, "total": 23, "description": "a speech"}]


def make_state():
    return {"turn_number": 3, "date": "1917-09-28", "campaign_log_summary": []}


class TestCheapModelKeyFor(unittest.TestCase):
    """As of 2026-08-21, judges/router/voice-editor always target a fixed cheap model - no longer
    derived from whatever the player picked for their own turn (see council.py's module docstring
    and NOTES.md for why: with Sonnet/Haiku/Opus selectable again, "follow the player" would let an
    always-on, minimum-3-judge council silently run on Opus if that's what the player chose)."""

    def test_always_returns_the_fixed_judge_model_key_regardless_of_input(self):
        for player_choice in ("luna", "sonnet", "haiku", "opus", "local-ollama", "unknown-key", None):
            self.assertEqual(council._cheap_model_key_for(), council.JUDGE_MODEL_KEY)

    def test_run_council_requests_the_fixed_judge_model_even_when_player_picked_opus(self):
        fake = ScriptedProvider()
        requested_keys = []

        def fake_get_provider(model_key):
            requested_keys.append(model_key)
            return fake, "claude-haiku-4-5-20251001"

        with patch.object(providers, "get_provider", side_effect=fake_get_provider):
            council.run_council(
                fake, "claude-opus-5", "opus", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        self.assertEqual(requested_keys, [council.JUDGE_MODEL_KEY])


class TestRunCouncil(unittest.TestCase):
    def test_full_successful_run_uses_voice_edited_synthesis(self):
        fake = ScriptedProvider()  # router selects 4 judges by default
        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        self.assertEqual(narration, "EDITED")
        self.assertEqual(log["candidate_count"], 2)
        self.assertEqual(log["judges_selected"], ["rules_mechanics", "canon_continuity", "groundedness", "premortem"])
        self.assertEqual(len(log["judge_results"]), 4)
        self.assertEqual(log["router_reasoning"], "critical success warrants a bigger panel")
        self.assertEqual(log["chairman_reasoning"], "took the best bits")
        self.assertEqual(log["disagreement_notes"], "judges disagreed on tone")
        self.assertIsNone(log["aborted_reason"])

    def test_router_selecting_too_few_judges_is_padded_to_the_floor(self):
        # Router only asked for 1 judge - _select_judge_roles must enforce MIN_JUDGES=3 in code,
        # not trust the model's count.
        fake = ScriptedProvider(router_judge_keys=["groundedness"])
        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        self.assertGreaterEqual(len(log["judges_selected"]), council.MIN_JUDGES)
        self.assertIn("groundedness", log["judges_selected"])

    def test_router_returning_no_structured_output_falls_back_to_default_floor(self):
        fake = ScriptedProvider()

        # Force the router call specifically to return nothing usable, while everything else works.
        real_call = fake.call

        def call_with_broken_router(model, system_prompt, tool_specs, conversation, force_tool_name=None):
            if force_tool_name == "select_judges":
                fake.calls.append("select_judges")
                return providers.TurnResult(text_parts=["no tool call"], tool_calls=[], usage=usage())
            return real_call(model, system_prompt, tool_specs, conversation, force_tool_name)

        fake.call = call_with_broken_router
        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        self.assertEqual(len(log["judges_selected"]), council.MIN_JUDGES)
        self.assertIsNone(log["router_reasoning"])

    def test_budget_exhausted_before_judging_falls_back_to_baseline(self):
        fake = ScriptedProvider()

        def budget_ok():
            return len(fake.calls) < 2  # router + alternate-candidate succeed, then cut off

        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION", budget_ok=budget_ok,
            )
        self.assertEqual(narration, "BASELINE NARRATION")
        self.assertIn("budget exhausted", log["aborted_reason"])
        self.assertEqual(log["judge_results"], [])

    def test_budget_exhausted_mid_judging_keeps_partial_results_but_falls_back(self):
        fake = ScriptedProvider()

        # Tied to actual API calls made (idempotent/monotonic), matching how a real budget_ok
        # behaves - once genuinely exhausted it stays exhausted, unlike a call-counter that could
        # flip closed and then (nonsensically) open again.
        def budget_ok():
            return len(fake.calls) < 4  # router + candidate + 2 judges succeed, then cut off

        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION", budget_ok=budget_ok,
            )
        self.assertEqual(narration, "BASELINE NARRATION")
        self.assertIn("mid-judging", log["aborted_reason"])
        self.assertEqual(len(log["judge_results"]), 2)

    def test_chairman_failure_falls_back_to_baseline(self):
        fake = ScriptedProvider(chairman_result=None)
        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        self.assertEqual(narration, "BASELINE NARRATION")
        self.assertIn("chairman", log["aborted_reason"])

    def test_judge_provider_unavailable_falls_back_to_main_provider_without_crashing(self):
        fake = ScriptedProvider()
        with patch.object(providers, "get_provider", side_effect=providers.ProviderError("no key")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION",
            )
        # Should still complete the full pipeline using `fake` for routing/judging too, not crash.
        self.assertEqual(narration, "EDITED")
        self.assertEqual(len(log["judge_results"]), 4)

    def test_no_budget_ok_means_unlimited(self):
        # budget_ok=None (engine.run_turn's own convention for "no limit") must not crash council.
        fake = ScriptedProvider()
        with patch.object(providers, "get_provider", return_value=(fake, "claude-haiku-4-5-20251001")):
            narration, log = council.run_council(
                fake, "gpt-5.6-luna", "luna", "SYSTEM PROMPT", "I give a speech.",
                make_state(), make_debug_events(), "BASELINE NARRATION", budget_ok=None,
            )
        self.assertEqual(narration, "EDITED")
        self.assertIsNone(log["aborted_reason"])


if __name__ == "__main__":
    unittest.main()
