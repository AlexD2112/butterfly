import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import providers


def _fake_anthropic_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="submit_judgment", input={"scores": {}})],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def _fake_openai_response():
    return SimpleNamespace(
        output_text="",
        output=[SimpleNamespace(type="function_call", call_id="t1", name="submit_judgment", arguments="{}")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, input_tokens_details=SimpleNamespace(cached_tokens=0)),
    )


class TestForceToolName(unittest.TestCase):
    def test_anthropic_force_tool_name_sets_tool_choice(self):
        provider = providers.AnthropicProvider.__new__(providers.AnthropicProvider)
        provider._client = MagicMock()
        provider._client.messages.create.return_value = _fake_anthropic_response()

        provider.call("claude-haiku-4-5-20251001", "sys", [{"name": "submit_judgment", "description": "d", "parameters": {}}], [], force_tool_name="submit_judgment")

        kwargs = provider._client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["tool_choice"], {"type": "tool", "name": "submit_judgment"})

    def test_anthropic_no_force_tool_name_omits_tool_choice(self):
        provider = providers.AnthropicProvider.__new__(providers.AnthropicProvider)
        provider._client = MagicMock()
        provider._client.messages.create.return_value = _fake_anthropic_response()

        provider.call("claude-haiku-4-5-20251001", "sys", [], [])

        kwargs = provider._client.messages.create.call_args.kwargs
        self.assertNotIn("tool_choice", kwargs)

    def test_openai_force_tool_name_sets_tool_choice(self):
        provider = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
        provider._client = MagicMock()
        provider._client.responses.create.return_value = _fake_openai_response()

        provider.call("gpt-5.6-luna", "sys", [{"name": "submit_judgment", "description": "d", "parameters": {}}], [], force_tool_name="submit_judgment")

        kwargs = provider._client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["tool_choice"], {"type": "function", "name": "submit_judgment"})

    def test_openai_no_force_tool_name_omits_tool_choice(self):
        provider = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
        provider._client = MagicMock()
        provider._client.responses.create.return_value = _fake_openai_response()

        provider.call("gpt-5.6-luna", "sys", [], [])

        kwargs = provider._client.responses.create.call_args.kwargs
        self.assertNotIn("tool_choice", kwargs)


if __name__ == "__main__":
    unittest.main()
