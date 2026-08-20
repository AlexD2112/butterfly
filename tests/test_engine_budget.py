import unittest

import engine
import providers


class FakeProvider:
    """Minimal stand-in for AnthropicProvider/OpenAIProvider - returns a scripted sequence of
    TurnResults instead of calling a real API, so engine.run_turn's loop/budget logic can be tested
    without live provider calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation):
        result = self.script[self.calls]
        self.calls += 1
        return result

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


def make_usage():
    return {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def minimal_state():
    return {"campaign_log_summary": [], "turn_number": 1}


class TestRunTurnBudget(unittest.TestCase):
    def test_no_budget_guard_behaves_as_before(self):
        provider = FakeProvider(
            [providers.TurnResult(text_parts=["Hello."], tool_calls=[], usage=make_usage())]
        )
        narration, debug_events = engine.run_turn(
            provider, "gpt-5.6-luna", minimal_state(), "petrograd_1917", "red_admiral", [], "do something"
        )
        self.assertEqual(narration, "Hello.")
        self.assertEqual(provider.calls, 1)

    def test_usage_sink_accumulates_across_calls(self):
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["Rolling..."],
                    tool_calls=[{"id": "1", "name": "unknown_tool", "input": {}}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=make_usage()),
            ]
        )
        usage_sink = []
        narration, debug_events = engine.run_turn(
            provider, "gpt-5.6-luna", minimal_state(), "petrograd_1917", "red_admiral", [], "do something",
            usage_sink=usage_sink,
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(usage_sink), 2)
        self.assertEqual(narration, "Rolling...\nDone.")

    def test_budget_guard_stops_a_multi_call_turn_early(self):
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["Rolling..."],
                    tool_calls=[{"id": "1", "name": "unknown_tool", "input": {}}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Never reached."], tool_calls=[], usage=make_usage()),
            ]
        )
        usage_sink = []
        narration, debug_events = engine.run_turn(
            provider, "gpt-5.6-luna", minimal_state(), "petrograd_1917", "red_admiral", [], "do something",
            usage_sink=usage_sink, budget_ok=lambda: False,
        )
        self.assertEqual(provider.calls, 1, "second API call should never have been made")
        self.assertEqual(narration, "Rolling...")
        self.assertTrue(any(ev["type"] == "budget_exhausted" for ev in debug_events))

    def test_budget_guard_allows_normal_completion_when_never_exhausted(self):
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["Rolling..."],
                    tool_calls=[{"id": "1", "name": "unknown_tool", "input": {}}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=make_usage()),
            ]
        )
        narration, debug_events = engine.run_turn(
            provider, "gpt-5.6-luna", minimal_state(), "petrograd_1917", "red_admiral", [], "do something",
            budget_ok=lambda: True,
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(narration, "Rolling...\nDone.")
        self.assertFalse(any(ev["type"] == "budget_exhausted" for ev in debug_events))


if __name__ == "__main__":
    unittest.main()
