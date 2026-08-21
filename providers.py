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
import re
import time
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


def _strip_thinking(text):
    """Qwen3 (via Ollama) sometimes leaks its chain-of-thought directly into response content even
    when think:false is requested (observed directly during testing 2026-08-20/21) - and not always
    in a well-formed <think>...</think> pair; several observed responses had a bare closing </think>
    with no matching opening tag, apparently because Ollama's own content capture already starts
    mid-thought. Taking everything after the LAST </think> handles both the well-formed and the
    opening-tag-missing cases the same way, and is a no-op (returns the text unchanged, just
    stripped) when no </think> appears at all."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


class OllamaTunnelProvider:
    """Talks to a local Ollama instance exposed via a Cloudflare quick tunnel + a small auth/rate-
    limit proxy that machine runs (see local-ollama-proxy/ at the repo root) - never Ollama's raw
    port directly, which has no authentication of its own. This is what makes the local model a
    free, player-selectable option: $0 cost regardless of real token counts (see pricing.py - this
    provider's model id is deliberately absent from PRICING_TABLE, so estimate_cost_usd() returns
    0.0 for it via that module's existing fail-open behavior; real token counts still get recorded
    in usage_events for observability, just billed at zero).

    Uses Ollama's NATIVE /api/chat endpoint (via the proxy), not the OpenAI-compatible /v1/ surface
    OpenAIProvider uses - confirmed by direct testing that Ollama's OpenAI-compat endpoint silently
    ignores a context-length override in the request body, while the native endpoint honors it. The
    proxy (not this class) is what actually sets the context length on every request - see
    local-ollama-proxy/proxy.py's NUM_CTX for the reasoning behind that number. The native
    endpoint's tool-calling shape is close to OpenAI's (tools/tool_calls, arguments as a JSON
    object already - not a string needing parsing, unlike the other two providers here), so this
    class's conversation handling looks similar to OpenAIProvider's despite using plain `requests`
    rather than an SDK (there's no official Ollama Python SDK this app already depends on, and the
    `openai` SDK is built around OpenAI's own endpoints - a natural fit for Anthropic/OpenAI's real
    APIs, not for hand-rolling requests to a self-hosted JSON API like this one).

    Configured via env vars OLLAMA_TUNNEL_URL (the cloudflared quick tunnel's current public HTTPS
    URL - changes every time the tunnel restarts, so this needs to be updated as a Render env var,
    not hardcoded - see local-ollama-proxy/README.md) and OLLAMA_TUNNEL_SECRET (the shared secret
    the local proxy requires, sent as a Bearer token - must match that proxy's PROXY_SHARED_SECRET).
    """

    env_var = ("OLLAMA_TUNNEL_URL", "OLLAMA_TUNNEL_SECRET")

    def __init__(self):
        self.base_url = (os.environ.get("OLLAMA_TUNNEL_URL") or "").rstrip("/")
        self.secret = os.environ.get("OLLAMA_TUNNEL_SECRET")
        if not self.base_url or not self.secret:
            raise ProviderError("OLLAMA_TUNNEL_URL / OLLAMA_TUNNEL_SECRET are not set.")
        import requests  # imported lazily, matching the other providers' lazy-import convention

        self._requests = requests

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append({"role": "user", "content": text})

    def _tools_payload(self, tool_specs):
        return [
            {"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}}
            for s in tool_specs
        ]

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        messages = [{"role": "system", "content": system_prompt}] + conversation
        body = {"model": model, "messages": messages, "stream": False}
        if tool_specs:
            body["tools"] = self._tools_payload(tool_specs)
        if force_tool_name:
            body["tool_choice"] = {"type": "function", "function": {"name": force_tool_name}}

        try:
            response = self._requests.post(
                f"{self.base_url}/api/chat",
                json=body,
                headers={"Authorization": f"Bearer {self.secret}"},
                # Matches the local proxy's own timeout (local-ollama-proxy/proxy.py) - generation
                # speed on that machine varies a fair amount (observed roughly 9-29 tok/s across
                # testing) plus a one-time 30-50s+ reload whenever the model/context config changes.
                timeout=280,
            )
        except self._requests.exceptions.RequestException as e:
            raise ProviderError(
                f"Local model is currently offline - couldn't reach the tunnel ({type(e).__name__})."
            ) from e

        if response.status_code == 401:
            raise ProviderError(
                "Local model tunnel rejected the request - OLLAMA_TUNNEL_SECRET doesn't match the "
                "local proxy's PROXY_SHARED_SECRET."
            )
        if response.status_code == 429:
            raise ProviderError("Local model is temporarily rate-limited on its own end - try again shortly.")
        if response.status_code != 200:
            raise ProviderError(f"Local model tunnel returned an error (HTTP {response.status_code}).")

        data = response.json()
        message = data.get("message", {})
        text = _strip_thinking((message.get("content") or ""))
        text_parts = [text] if text else []

        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments")
            if isinstance(args, str):
                args = json.loads(args) if args else {}
            tool_calls.append({"id": tc.get("id") or fn.get("name"), "name": fn.get("name"), "input": args or {}})

        usage = {
            "provider": "ollama_tunnel",
            "model": model,
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        return TurnResult(text_parts=text_parts, tool_calls=tool_calls, raw=data, usage=usage)

    def append_assistant_turn(self, conversation, turn_result):
        raw_message = turn_result.raw.get("message", {})
        message = {"role": "assistant", "content": raw_message.get("content", "")}
        if raw_message.get("tool_calls"):
            message["tool_calls"] = raw_message["tool_calls"]
        conversation.append(message)

    def append_tool_results(self, conversation, turn_result, results):
        for r in results:
            conversation.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})


MODEL_REGISTRY = {
    "luna": {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna (OpenAI - fast/cheap)",
    },
    "sonnet": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "label": "Claude Sonnet 5 (Anthropic, default)",
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
    "local-ollama": {
        "provider": "ollama_tunnel",
        "model": "qwen3:30b",
        "label": "Qwen3 30B (local, via a home GPU - free, but slower and depends on that machine being on)",
    },
}
DEFAULT_MODEL_KEY = "sonnet"  # switched back from "luna" 2026-08-21 - see NOTES.md

# Was a temporary lock (2026-08-20 - {"luna", "local-ollama"}, before that a single LOCKED_MODEL_KEY
# forcing everything onto Luna) while cost data on the cloud providers was still unknown. Reopened
# 2026-08-21 now that Council Mode's judge/router/voice-editor calls are pinned to a fixed cheap
# model regardless of the player's own choice (see council._cheap_model_key_for) - the thing this
# lock was originally protecting against (every one of council's ~7-9 internal calls silently
# running on an expensive model just because the player picked one for narration) is now handled at
# the source, not by hiding the picker. None means "every registered model is selectable" - server.py
# and the /api/models route both key off this single constant, so re-restricting later (e.g. to
# {"luna", "local-ollama"} again) is a one-line revert, not a bigger change.
ALLOWED_MODEL_KEYS = None

_PROVIDER_CLASSES = {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "ollama_tunnel": OllamaTunnelProvider}
_provider_instances = {}


def get_provider(model_key):
    entry = MODEL_REGISTRY.get(model_key)
    if not entry:
        raise ProviderError(f"Unknown model key: {model_key}")
    provider_key = entry["provider"]
    if provider_key not in _provider_instances:
        _provider_instances[provider_key] = _PROVIDER_CLASSES[provider_key]()
    return _provider_instances[provider_key], entry["model"]


def _required_env_vars(provider_key):
    env_var = _PROVIDER_CLASSES[provider_key].env_var
    return (env_var,) if isinstance(env_var, str) else tuple(env_var)


def provider_available(provider_key):
    """Whether a provider is CONFIGURED (its required env vars are set) - not whether it's actually
    reachable right now. For ollama_tunnel specifically, being "configured" doesn't mean the tunnel
    is currently up (the laptop could be asleep) - see ollama_tunnel_is_healthy() for that liveness
    check, used separately by /api/models to decide whether to show the option as selectable."""
    return all(v in os.environ for v in _required_env_vars(provider_key))


# Cached liveness check for the local-ollama tunnel specifically - a real network round-trip to a
# home machine that might be asleep, so this is deliberately NOT done on every /api/models call
# (that route gets hit on essentially every page load; an uncached check could add multi-second
# latency to page loads for every player, not just ones considering the local model). Cached for a
# short TTL rather than checked once at startup, since the tunnel can go up/down at any time
# independent of a deploy. A lazy-checked-and-cached approach was chosen over a background polling
# thread for simplicity - see NOTES.md for that tradeoff.
_HEALTH_CACHE_TTL_SECONDS = 20
_HEALTH_CHECK_TIMEOUT_SECONDS = 3
_health_cache = {"healthy": False, "checked_at": 0.0}


def ollama_tunnel_is_healthy(force=False):
    if not provider_available("ollama_tunnel"):
        return False
    now = time.monotonic()
    if not force and (now - _health_cache["checked_at"]) < _HEALTH_CACHE_TTL_SECONDS:
        return _health_cache["healthy"]
    import requests

    healthy = False
    try:
        base_url = os.environ.get("OLLAMA_TUNNEL_URL", "").rstrip("/")
        resp = requests.get(f"{base_url}/health", timeout=_HEALTH_CHECK_TIMEOUT_SECONDS)
        healthy = resp.status_code == 200
    except requests.exceptions.RequestException:
        healthy = False
    _health_cache["healthy"] = healthy
    _health_cache["checked_at"] = now
    return healthy
