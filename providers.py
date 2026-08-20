"""
Multi-provider adapter layer.

Anthropic and OpenAI have genuinely different tool-calling APIs, and for GPT-5.6 specifically,
tool calling on the Chat Completions endpoint requires reasoning_effort="none" (which disables
reasoning) - the documented, supported path for tool calling with reasoning models is the
Responses API instead, so that's what OpenAIProvider uses.

Each provider exposes the same four-method interface so engine.py's run_turn loop never needs to
know which one it's talking to:
  - new_conversation()                                    -> provider-native empty conversation
  - append_user_message(conversation, text)                -> mutates conversation in place
  - call(model, system_prompt, tool_specs, conversation,
          force_tool_name=None)                             -> TurnResult
  - append_assistant_turn(conversation, turn_result)        -> mutates conversation in place
  - append_tool_results(conversation, turn_result, results) -> mutates conversation in place,
                                                                 results = [{"id":..., "content":...}]

force_tool_name (optional): when set, the model MUST call that specific tool rather than being free
to just respond with text (the default "auto" behavior every normal game turn uses). council.py's
judges/chairman rely on this - a judge that responds with prose instead of its scoring tool would be
unusable structured data, so their calls force the tool rather than hoping for compliance.

Conversations are NOT translated between providers - each provider tracks its own native thread.
If you switch models mid-session, the new provider starts its own fresh native conversation; the
game state file (not the raw tool-call transcript) is what carries continuity across that switch,
per core_rules.md section 5. In practice this just means a short-term loss of verbatim back-and-
forth phrasing on the turn you switch, not any loss of actual game state.
"""

import json
import os
from dataclasses import dataclass, field


@dataclass
class TurnResult:
    text_parts: list
    tool_calls: list  # [{"id": ..., "name": ..., "input": {...}}]
    raw: object = None
    usage: dict = None  # {"provider", "model", "input_tokens", "output_tokens",
    #  "cache_read_tokens", "cache_creation_tokens"} - see pricing.py for how this is costed


class ProviderError(RuntimeError):
    pass


class AnthropicProvider:
    env_var = "ANTHROPIC_API_KEY"

    def __init__(self):
        if self.env_var not in os.environ:
            raise ProviderError(f"{self.env_var} is not set.")
        import anthropic  # imported lazily so a missing package only breaks this provider

        self._client = anthropic.Anthropic()

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def _tools_payload(self, tool_specs):
        return [
            {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]}
            for s in tool_specs
        ]

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        kwargs = {}
        if force_tool_name:
            kwargs["tool_choice"] = {"type": "tool", "name": force_tool_name}
        response = self._client.messages.create(
            model=model,
            max_tokens=1500,
            system=system_prompt,
            tools=self._tools_payload(tool_specs),
            messages=conversation,
            **kwargs,
        )
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        usage = {
            "provider": "anthropic",
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        }
        return TurnResult(text_parts=text_parts, tool_calls=tool_calls, raw=response, usage=usage)

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append({"role": "assistant", "content": turn_result.raw.content})

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                    for r in results
                ],
            }
        )


class OpenAIProvider:
    """
    Uses the Responses API (client.responses.create), not Chat Completions - required for tool
    calling with GPT-5.6-family reasoning models. Per OpenAI's documented pattern, the model's own
    previous `response.output` (which may include opaque reasoning items alongside function_call
    items) is appended back into the input list verbatim rather than reconstructed - this is the
    officially documented way to preserve multi-turn tool-call continuity for reasoning models, and
    sidesteps needing to model reasoning-item internals ourselves.

    NOTE: no output-token-limit parameter is set here - OpenAI's Responses API function-calling
    guide (as of this writing) doesn't document one for this endpoint, and guessing a wrong
    parameter name risks a hard API error on every single turn. If you want a token cap, test
    `max_output_tokens=` against the live API first and add it back in once confirmed.
    """

    env_var = "OPENAI_API_KEY"

    def __init__(self):
        if self.env_var not in os.environ:
            raise ProviderError(f"{self.env_var} is not set.")
        import openai  # imported lazily so a missing package only breaks this provider

        self._client = openai.OpenAI()

    def new_conversation(self):
        return []  # a Responses-API `input` list

    def append_user_message(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def _tools_payload(self, tool_specs):
        return [
            {
                "type": "function",
                "name": s["name"],
                "description": s["description"],
                "parameters": s["parameters"],
            }
            for s in tool_specs
        ]

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        kwargs = {}
        if force_tool_name:
            kwargs["tool_choice"] = {"type": "function", "name": force_tool_name}
        response = self._client.responses.create(
            model=model,
            instructions=system_prompt,
            tools=self._tools_payload(tool_specs),
            input=conversation,
            **kwargs,
        )
        text_parts = [response.output_text] if getattr(response, "output_text", "") else []
        tool_calls = []
        for item in response.output:
            if getattr(item, "type", None) == "function_call":
                tool_calls.append(
                    {
                        "id": item.call_id,
                        "name": item.name,
                        "input": json.loads(item.arguments) if item.arguments else {},
                    }
                )
        input_details = getattr(response.usage, "input_tokens_details", None)
        usage = {
            "provider": "openai",
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            # cached_tokens is a SUBSET of input_tokens for OpenAI, not additive - see pricing.py
            "cache_read_tokens": getattr(input_details, "cached_tokens", 0) or 0 if input_details else 0,
            "cache_creation_tokens": 0,  # not exposed by the Responses API usage object
        }
        return TurnResult(text_parts=text_parts, tool_calls=tool_calls, raw=response, usage=usage)

    def append_assistant_turn(self, conversation, turn_result):
        conversation.extend(turn_result.raw.output)

    def append_tool_results(self, conversation, turn_result, results):
        for r in results:
            conversation.append(
                {"type": "function_call_output", "call_id": r["id"], "output": r["content"]}
            )


MODEL_REGISTRY = {
    "luna": {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna (OpenAI - fast/cheap, default)",
    },
    "sonnet": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "label": "Claude Sonnet 5 (Anthropic)",
    },
    "haiku": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5 (Anthropic - cheap/fast, good for mechanical smoke tests)",
    },
    "opus": {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "label": "Claude Opus 5 (Anthropic - top quality, slower/pricier)",
    },
}
DEFAULT_MODEL_KEY = "luna"

# TEMPORARY LOCK (2026-08-20, standing until the user says otherwise - see NOTES.md). Every LLM
# call in the app - main turn generation, and every part of Council Mode (router/judges/chairman/
# extra candidates) - is forced onto this single model regardless of what's requested, both because
# Council Mode now runs on every single turn (so cost discipline matters a lot more than before) and
# because the "escalate to a stronger model on high-stakes turns" idea is explicitly on hold until
# there's real cost data. This is deliberately NOT a removal of the other registry entries below
# (sonnet/haiku/opus stay fully defined and functional) - set this back to None to revert instantly,
# no other code change needed; server.py and council.py both key off this single constant.
LOCKED_MODEL_KEY = "luna"

_PROVIDER_CLASSES = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}
_provider_instances = {}


def get_provider(model_key):
    entry = MODEL_REGISTRY.get(model_key)
    if not entry:
        raise ProviderError(f"Unknown model key: {model_key}")
    provider_key = entry["provider"]
    if provider_key not in _provider_instances:
        _provider_instances[provider_key] = _PROVIDER_CLASSES[provider_key]()
    return _provider_instances[provider_key], entry["model"]


def provider_available(provider_key):
    env_var = _PROVIDER_CLASSES[provider_key].env_var
    return env_var in os.environ
