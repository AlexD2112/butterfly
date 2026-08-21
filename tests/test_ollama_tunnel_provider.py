import os
import unittest
from unittest.mock import MagicMock, patch

import providers


class TestOllamaTunnelProviderInit(unittest.TestCase):
    def test_missing_env_vars_raises_provider_error(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLLAMA_TUNNEL_URL", None)
            os.environ.pop("OLLAMA_TUNNEL_SECRET", None)
            with self.assertRaises(providers.ProviderError):
                providers.OllamaTunnelProvider()

    def test_configured_env_vars_construct_cleanly(self):
        with patch.dict(os.environ, {"OLLAMA_TUNNEL_URL": "https://example.trycloudflare.com/", "OLLAMA_TUNNEL_SECRET": "s3cret"}):
            provider = providers.OllamaTunnelProvider()
            self.assertEqual(provider.base_url, "https://example.trycloudflare.com")  # trailing slash stripped
            self.assertEqual(provider.secret, "s3cret")


def _make_provider():
    with patch.dict(os.environ, {"OLLAMA_TUNNEL_URL": "https://example.trycloudflare.com", "OLLAMA_TUNNEL_SECRET": "s3cret"}):
        return providers.OllamaTunnelProvider()


class TestOllamaTunnelProviderCall(unittest.TestCase):
    def setUp(self):
        self.provider = _make_provider()

    def _mock_response(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        return resp

    def test_successful_text_response_strips_thinking(self):
        resp = self._mock_response(json_data={
            "message": {"role": "assistant", "content": "Okay let me think about this.\n</think>\n\nParis is the capital."},
            "prompt_eval_count": 50,
            "eval_count": 20,
        })
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            result = self.provider.call("qwen3:30b", "system prompt", [], [])
        self.assertEqual(result.text_parts, ["Paris is the capital."])
        self.assertEqual(result.usage["input_tokens"], 50)
        self.assertEqual(result.usage["output_tokens"], 20)
        self.assertEqual(result.usage["provider"], "ollama_tunnel")

    def test_tool_call_response_parses_object_arguments(self):
        # Ollama's native API returns arguments as an already-parsed object, not a JSON string
        # (unlike OpenAI's APIs) - confirmed by direct testing 2026-08-20/21.
        resp = self._mock_response(json_data={
            "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "submit_judgment", "arguments": {"scores": {"candidate_1": 7}}}}],
            },
            "prompt_eval_count": 10, "eval_count": 5,
        })
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            result = self.provider.call("qwen3:30b", "sys", [{"name": "submit_judgment", "description": "d", "parameters": {}}], [], force_tool_name="submit_judgment")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]["name"], "submit_judgment")
        self.assertEqual(result.tool_calls[0]["input"], {"scores": {"candidate_1": 7}})

    def test_tool_call_response_parses_string_arguments_defensively(self):
        resp = self._mock_response(json_data={
            "message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "submit_judgment", "arguments": '{"scores": {}}'}},
            ]},
            "prompt_eval_count": 10, "eval_count": 5,
        })
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            result = self.provider.call("qwen3:30b", "sys", [], [], force_tool_name="submit_judgment")
        self.assertEqual(result.tool_calls[0]["input"], {"scores": {}})

    def test_request_sends_bearer_secret(self):
        resp = self._mock_response(json_data={"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 1})
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            self.provider.call("qwen3:30b", "sys", [], [])
        kwargs = mock_requests.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer s3cret")
        self.assertEqual(mock_requests.post.call_args.args[0], "https://example.trycloudflare.com/api/chat")

    def test_401_raises_provider_error(self):
        resp = self._mock_response(status_code=401)
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            with self.assertRaises(providers.ProviderError):
                self.provider.call("qwen3:30b", "sys", [], [])

    def test_429_raises_provider_error(self):
        resp = self._mock_response(status_code=429)
        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.post.return_value = resp
            mock_requests.exceptions.RequestException = Exception
            with self.assertRaises(providers.ProviderError):
                self.provider.call("qwen3:30b", "sys", [], [])

    def test_connection_failure_raises_provider_error_not_a_raw_exception(self):
        class FakeRequestException(Exception):
            pass

        with patch.object(self.provider, "_requests") as mock_requests:
            mock_requests.exceptions.RequestException = FakeRequestException
            mock_requests.post.side_effect = FakeRequestException("connection refused")
            with self.assertRaises(providers.ProviderError) as ctx:
                self.provider.call("qwen3:30b", "sys", [], [])
        self.assertIn("offline", str(ctx.exception))


class TestStripThinking(unittest.TestCase):
    def test_well_formed_think_block_removed(self):
        self.assertEqual(providers._strip_thinking("<think>reasoning</think>Final answer"), "Final answer")

    def test_bare_closing_tag_without_opening_removed(self):
        # Observed directly during testing - Ollama sometimes emits only the closing tag.
        self.assertEqual(providers._strip_thinking("some reasoning trace\n</think>\n\nFinal answer"), "Final answer")

    def test_no_think_tag_is_unchanged(self):
        self.assertEqual(providers._strip_thinking("Just a normal answer."), "Just a normal answer.")

    def test_empty_string(self):
        self.assertEqual(providers._strip_thinking(""), "")


class TestAppendConversation(unittest.TestCase):
    def setUp(self):
        self.provider = _make_provider()

    def test_append_assistant_turn_with_tool_calls(self):
        conversation = []
        turn_result = providers.TurnResult(
            text_parts=[], tool_calls=[],
            raw={"message": {"content": "text", "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": {}}}]}},
        )
        self.provider.append_assistant_turn(conversation, turn_result)
        self.assertEqual(conversation[0]["role"], "assistant")
        self.assertEqual(conversation[0]["content"], "text")
        self.assertIn("tool_calls", conversation[0])

    def test_append_tool_results(self):
        conversation = []
        self.provider.append_tool_results(conversation, None, [{"id": "1", "content": "result text"}])
        self.assertEqual(conversation[0], {"role": "tool", "tool_call_id": "1", "content": "result text"})


if __name__ == "__main__":
    unittest.main()
