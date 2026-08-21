import unittest

import council
import engine
import providers


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls += 1
        return providers.TurnResult(text_parts=["You act."], tool_calls=[], usage={
            "provider": "ollama_tunnel", "model": "qwen3:30b", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
        })

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


def minimal_state():
    return {"campaign_log_summary": [], "turn_number": 1}


class TestCouncilSkippedForLocalModel(unittest.TestCase):
    """Real unmocked testing found a full council turn on local-ollama realistically takes 6-24
    minutes (Qwen3's reasoning overhead applies to every call type, not just narration) - confirmed
    against an actual turn hanging on the live site, not just estimated. Council Mode must be
    skipped entirely for this model key; mechanics are unaffected either way since council never
    touches them."""

    def test_local_ollama_makes_exactly_one_provider_call(self):
        fake = FakeProvider()
        narration, debug_events = engine.run_turn(
            fake, "qwen3:30b", minimal_state(), "petrograd_1917", "red_admiral", [], "I act.",
            council_mode=True, model_key="local-ollama",
        )
        self.assertEqual(fake.calls, 1, "council must not run any extra calls for local-ollama")
        self.assertEqual(narration, "You act.")
        self.assertFalse(any(ev.get("type") == "council" for ev in debug_events))

    def test_other_models_still_get_council(self):
        fake = FakeProvider()
        narration, debug_events = engine.run_turn(
            fake, "gpt-5.6-luna", minimal_state(), "petrograd_1917", "red_admiral", [], "I act.",
            council_mode=True, model_key="luna",
        )
        self.assertGreater(fake.calls, 1, "council should still run its full pipeline for luna")
        self.assertTrue(any(ev.get("type") == "council" for ev in debug_events))

    def test_skip_set_contains_local_ollama(self):
        self.assertIn("local-ollama", council.SKIP_COUNCIL_FOR_MODEL_KEYS)


if __name__ == "__main__":
    unittest.main()
