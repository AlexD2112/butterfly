"""
Per-model USD pricing for the usage-budget feature (db.py's usage_events ledger).

Rates are USD per 1,000,000 tokens, hand-maintained from each provider's public pricing page -
there's no live pricing API to pull from, so this is a snapshot that needs occasional updating.

Cached-token discounts (confirmed against provider docs, not guessed):
  - Anthropic: cache_read billed at ~0.1x the input rate, cache_creation at ~1.25x the input rate.
    `input_tokens` on an Anthropic response already EXCLUDES cached tokens (separate additive
    counters), so the three input-side counters never overlap.
  - OpenAI (GPT-5.6 family): cached tokens billed at ~0.1x the input rate. Unlike Anthropic,
    `usage.input_tokens_details.cached_tokens` is a SUBSET of `usage.input_tokens`, not a separate
    additive counter - the non-cached portion is `input_tokens - cached_tokens`. OpenAI's Responses
    API usage object doesn't expose a separate cache-write counter, so cache-write cost isn't
    modeled here for OpenAI (in practice this app doesn't opt into anything that would generate one).

NOTE (as of 2026-08-20): Claude Sonnet 5 is on introductory pricing ($2.00 / $10.00 per 1M
input/output) through 2026-08-31. After that it reverts to $3.00 / $15.00 - update the "sonnet" row
below once that date passes, or costs will be silently under-counted.
"""

PRICING_TABLE = {
    # keyed by the raw model id string passed to the provider API (providers.MODEL_REGISTRY[...]["model"])
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},  # intro pricing through 2026-08-31; becomes 3.00/15.00 after
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
}

_CACHE_READ_MULTIPLIER = 0.1
_CACHE_CREATION_MULTIPLIER = 1.25


def estimate_cost_usd(usage):
    """Returns the USD cost of one API call given a usage dict as populated by providers.py's
    TurnResult.usage: {"provider", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens"}.

    Fails open (returns 0.0) for a model missing from PRICING_TABLE rather than raising, so an
    unrecognized/future model can't hard-block gameplay - but it means PRICING_TABLE must be kept in
    sync with providers.MODEL_REGISTRY or spend on a new model goes untracked. There's no live
    traffic risk here (this is a ~$0.10 hobby budget, not real billing), so failing open is the
    safer default over crashing a turn on a pricing lookup miss.

    "qwen3:30b" (providers.OllamaTunnelProvider, the local-ollama option) is DELIBERATELY absent
    from PRICING_TABLE - it's a free provider by design (a home GPU, not a paid API), so it should
    always cost $0.0 via this same fail-open path, not because it's unrecognized. Token counts still
    get recorded in usage_events for observability; they're just never converted to a dollar cost.
    """
    rates = PRICING_TABLE.get(usage["model"])
    if rates is None:
        return 0.0

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_tokens", 0) or 0
    cache_creation_tokens = usage.get("cache_creation_tokens", 0) or 0

    if usage.get("provider") == "openai":
        # cache_read_tokens is a SUBSET of input_tokens for OpenAI - don't double-charge it.
        uncached_input_tokens = max(0, input_tokens - cache_read_tokens)
    else:
        # Anthropic's input_tokens already excludes cached tokens - it's the full uncached count.
        uncached_input_tokens = input_tokens

    cost = uncached_input_tokens / 1_000_000 * rates["input"]
    cost += output_tokens / 1_000_000 * rates["output"]
    cost += cache_read_tokens / 1_000_000 * (rates["input"] * _CACHE_READ_MULTIPLIER)
    cost += cache_creation_tokens / 1_000_000 * (rates["input"] * _CACHE_CREATION_MULTIPLIER)
    return cost


# Confirmed cost model (2026-08-21, NOTES.md) for multiplayer AI calls that aren't tied to any one
# player's own action - a storm-out headcount check, the eventual daily conclusion sweep: split the
# real cost evenly across every present player, then only charge each their AMBIENT_COST_PLAYER_SHARE
# of that split. The rest isn't charged to anyone - the developer is personally covering it, which in
# practice just means not deducting it from any account's budget (there's no real mechanism for a
# human to manually settle a per-call subsidy).
AMBIENT_COST_PLAYER_SHARE = 0.5


def split_ambient_cost_per_player(total_cost_usd, num_players):
    """Returns each present player's charge for one ambient (non-player-triggered) AI call. 0.0 if
    there's nobody to split across - callers should skip billing entirely in that case rather than
    calling this with num_players=0."""
    if num_players <= 0:
        return 0.0
    return (total_cost_usd / num_players) * AMBIENT_COST_PLAYER_SHARE
