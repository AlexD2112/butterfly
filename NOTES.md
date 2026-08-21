# Claude restored as default + Council Mode judge-cost fix (2026-08-21) - real math, real problem found

Direction from you: bring Claude back as the standard/default model (un-hide Sonnet/Haiku/Opus),
keep the free local model available too, but first fix a real cost risk - Council Mode's router +
minimum 3 judges + chairman + voice-editor pass, previously following whatever model the player
picked, would silently bill judges against Sonnet/Opus the moment they became selectable again.

**What shipped:**
- `providers.ALLOWED_MODEL_KEYS = None` (was `{"luna", "local-ollama"}`) - every registered model is
  selectable again; `providers.DEFAULT_MODEL_KEY = "sonnet"` (was `"luna"`). Luna stays in the
  registry and stays selectable - nothing about it was removed, it's just no longer the default.
- `council._cheap_model_key_for()` now always returns a fixed `JUDGE_MODEL_KEY = "haiku"`, regardless
  of the player's own `model_key` - previously it followed the player's choice (a no-op while only
  Luna/local-ollama were selectable, but a real problem the instant Sonnet/Opus became choosable
  again). Only the router, the judges, and the voice-editor pass move to Haiku; the baseline
  narration, the extra candidate, and the chairman's synthesis still run on whatever model the
  player picked - narration quality still scales with the player's choice, only the narrow
  rubric-scoring work doesn't. Local-ollama is unaffected either way (Council Mode is skipped for it
  entirely - see the URGENT FIX entry below - so this function is never reached on that path).
- Added a test (`tests/test_council_orchestration.py::TestCheapModelKeyFor`) asserting this directly -
  previously nothing actually checked which model key the judge/router calls requested, since every
  existing test mocked `get_provider` with a blanket `return_value` that ignored its argument.
- 92/92 tests passing (90 existing + 2 new), including updates to `tests/test_allowed_model_keys.py`
  (its old assertions - "only luna/local-ollama are listed," "sonnet is rejected with 400" - directly
  encoded the now-reverted lock; restructured to verify the restriction *mechanism* still works via
  `patch.object` rather than asserting on the now-stale default).

## The real cost math - and a bigger problem than the judge-model fix alone solves

You asked me to recompute this for real rather than assume the fix would be enough, and it wasn't -
the judge-model fix is correct and worth having, but it is **not sufficient on its own** to make the
current $0.10 default budget usable with Claude as the default and Council Mode always on. Read this
whole section before treating "Claude is back" as a finished, ship-ready state.

Same estimation methodology as the 2026-08-20 entry below (real measured ~9,500-token base system
prompt + ~1,140 tokens of tool defs from `wc -c` on the actual setting files; conversation
history/output length still estimated, not measured, since that varies turn to turn):

| Call | Input (est.) | Output (est.) | Model | Cost |
|---|---|---|---|---|
| Baseline turn generation | ~11,250 | ~250 | Sonnet ($2.00/$10.00 per 1M) | ~$0.0250 |
| Router | ~1,080 | ~80 | Haiku ($1.00/$5.00 per 1M) | ~$0.0015 |
| Extra candidate | ~9,950 | ~300 | Sonnet | ~$0.0229 |
| Each judge | ~1,000 | ~90 | Haiku | ~$0.0015 |
| Chairman | ~1,500 | ~380 | Sonnet | ~$0.0068 |
| Voice editor | ~580 | ~300 | Haiku | ~$0.0021 |

Baseline generation still runs ~1.5 calls/turn on average (same assumption as before): **~$0.0375**
just for the pre-existing baseline narration, before any council overhead - already more than
1/3 of the entire $0.10 budget, from Sonnet's per-token rate alone (~10x Luna's).

**Council's incremental cost per turn** (router + candidate + judges + chairman + voice editor):
- At the MIN_JUDGES=3 floor: 0.0015 + 0.0229 + (3 × 0.0015) + 0.0068 + 0.0021 ≈ **$0.0378**
- At the max of 6 judges: 0.0015 + 0.0229 + (6 × 0.0015) + 0.0068 + 0.0021 ≈ **$0.0423**

**Total cost per turn with Council Mode on Sonnet**: roughly **$0.075-0.080**, call it **~$0.077** as
a representative middle estimate (most turns land at the 3-judge floor per the router's own design -
"most turns should just get the MIN_JUDGES most relevant judges").

**Turns per $0.10 account: 0.10 / 0.077 ≈ ~1.3 turns.** Not a typo. The $0.10 default now affords
barely more than a single turn.

**What the judge-model fix actually bought, quantified**: I computed the counterfactual (judges/
router/voice-editor all on Sonnet, i.e. what "follow the player's choice" would have done the moment
Sonnet became the default) - that comes out to **~$0.083-0.092/turn** (0.10/0.083 ≈ 1.2 turns,
0.10/0.092 ≈ 1.09 turns). The fix saves roughly $0.006-0.012/turn (about 8-14% of the total), real
and worth having, but **it is not the dominant cost driver**. The dominant driver is that the
baseline narration, the extra candidate, and the chairman synthesis - all three genuinely need a
strong model, so pinning them to Haiku isn't the right fix - together resend something close to the
full ~10,650-token system prompt at Sonnet's per-token rate on 2-3 separate calls, every single turn.
That's the actual problem, and the judge-model fix was never going to touch it, because it was never
about the judges.

## What this means - options, not a decision I made for you

I did **not** change `DEFAULT_BUDGET_USD` or implement caching - consistent with how this exact
question was handled in the 2026-08-20 entry below (you asked me to surface it, not silently pick a
number), and this is the same kind of call. But I want to be direct about what "Claude is back as
default" currently means in practice: a new account gets essentially one turn before hitting the
budget wall, which reads as broken to a player, not just "a bit pricier than before." I'd genuinely
recommend NOT treating this as done-and-shippable in its current state - one of the options below
(most likely the budget raise, since it's a one-line change versus caching's real implementation
work) probably needs to happen before this goes back in front of real players. Options, not
mutually exclusive:

- **Raise `DEFAULT_BUDGET_USD` substantially.** To match the old Luna-era experience of roughly
  10-11 turns per account, the math says ~10 × $0.077 ≈ **$0.75-$1.00**. This is the fastest fix (a
  one-line constant change) but a real, larger dollar commitment per account than before - worth
  deciding deliberately, not defaulting into.
- **Prompt caching** (flagged as the most promising lever in the 2026-08-20 entry below, still not
  implemented - `cache_control` is set on zero calls anywhere in `providers.py` today). The
  ~9,500-10,650-token stable prefix (rules/NPCs/factions/canon/resources/tools - everything except
  the small per-turn state digest) is close to IDENTICAL between the baseline call and the extra
  candidate every single turn. Rough math on just those two calls: uncached, they cost
  (11,250+9,950) tokens × $2.00/1M ≈ $0.0424 in input alone; with the shared prefix cached (one
  cache-write call at a 1.25x premium, one cache-read call at a 0.1x discount), that drops to
  roughly $0.030 - about a **28% reduction on just those two calls**, before counting a turn's
  second baseline-retry call (common per the "~1.5 calls/turn" figure) also hitting a warm cache for
  even more savings. This is a genuinely separate, real piece of work (restructuring
  `engine.build_system_prompt` to put the stable content first/last consistently, adding
  `cache_control` breakpoints, verifying real cache hits against the live API) - not done tonight,
  flagging as the highest-leverage lever that doesn't require raising anyone's dollar cost.
- **Narrower judge floor or roster** (`MIN_JUDGES`, currently 3) would reduce council's own
  incremental cost roughly linearly, but per the math above council's incremental cost isn't the
  main problem - narrowing it further would save maybe $0.003-0.006/turn, a much smaller lever than
  either option above. Noting it exists, not recommending it as the primary fix.

## What's estimated vs. real

Same caveat as every prior cost-math entry in this file: the base system prompt size is a real
measurement (`wc -c` on the actual setting files), everything else (conversation history length,
narration length, judge verbosity) is a reasonable planning estimate, not a guarantee - the only way
to get real numbers is running actual turns against the live Anthropic API, which I still can't do
from here (no API keys configured in this repo, deliberately, per earlier NOTES.md entries). If
you've since run any real turns and have actual token counts from Anthropic's own usage response,
those would supersede everything estimated here.

---

# Local hosting + Render pass-through (2026-08-21, continuation session)

Picking this up in a fresh session after the previous one started hitting persistent safety-
classifier errors on every message - nothing about the repo state was lost, this is purely a
conversation-continuity handoff. First real finding of this session: the previous session had
**already started this exact change** before it went down - `proxy_app.py`, and diffs to
`Procfile`/`render.yaml`/`server.py`/`requirements.txt`, were already sitting uncommitted in the
working tree. I nearly clobbered `proxy_app.py` by writing my own competing version
(`passthrough_app.py`) before noticing via `git status` - caught it before committing anything,
deleted mine, and continued from the existing (well-reasoned, already-correct) implementation
instead. Same thing happened with the tunnel starter script: the previous session had already
written an untracked `start_local_site.ps1` at the repo root (auto-creates `.env` with a generated
`FLASK_SECRET_KEY` on first run - a nice touch mine didn't have), while I'd independently written my
own inside a `local-site-tunnel/` subfolder (matching `local-ollama-proxy/`'s existing folder
pattern). Merged the better parts of both into the subfolder version and deleted the root-level
duplicate, rather than keeping two competing scripts. Lesson for future sessions picking up a
handoff: check `git status` for uncommitted work before creating anything, not just before
destructive operations.

**What was already done (by the previous session, verified and kept as-is):**
- `proxy_app.py` - a transparent reverse-proxy Flask app: forwards every request (method, headers,
  body, query string, cookies) to `LOCAL_TUNNEL_URL`, relays the response byte-for-byte, strips only
  genuinely hop-by-hop headers. No auth layer of its own (unlike the Ollama proxy) - reasoned
  correctly in its own docstring: it isn't protecting a raw unmetered resource, it's relaying to an
  app that already has its own auth (Discord OAuth) and cost control (the budget ledger).
- `Procfile`/`render.yaml` startCommand switched to `gunicorn proxy_app:app --timeout 900
  --worker-class gthread --threads 4` - same flags as before, now on the proxy instead of the real
  app, so a slow forwarded request (a Council Mode turn, or worst case a local-model turn) doesn't
  block other players either.
- `render.yaml`'s old envVars (API keys, Discord, Ollama tunnel settings) commented out rather than
  deleted, replaced with a single `LOCAL_TUNNEL_URL` - a clean, reversible record of what Render used
  to need, not a destructive edit.
- `server.py`: `SESSION_COOKIE_SECURE` now defaults to `True` (`os.environ.get("SESSION_COOKIE_SECURE",
  "1") == "1"`) - correctly closes the gap flagged in this file's own 2026-08-20 entry ("no reliable
  production signal to gate this on"). The insight that unblocked it: every real deployment path now
  terminates in HTTPS somewhere in front of the Flask process regardless of how server.py itself is
  reached (Render's own HTTPS in front of the old direct-hosted setup, or a Cloudflare tunnel's HTTPS
  edge in front of this machine now) - and browsers treat `127.0.0.1`/`localhost` as a secure context,
  so `python server.py` local dev over plain `http://127.0.0.1` still works unmodified. Verified this
  directly this session (see below) - a curl-based smoke test confirmed a Secure session cookie
  survives across two requests to `127.0.0.1` with no HTTPS involved.

**What I finished this session:**
- `local-site-tunnel/start_local_site.ps1` + `README.md` - the local-machine half, mirroring
  `local-ollama-proxy/`'s already-proven pattern exactly. Runs the real app via **waitress**, not
  `python server.py`'s own dev server or gunicorn - gunicorn depends on `fcntl` and simply doesn't
  run on Windows at all (this machine's actual OS), and Flask's own dev server defaults
  `FLASK_DEBUG` on, which would expose Werkzeug's interactive debugger (arbitrary code execution on
  any unhandled exception) to the public internet the moment the tunnel comes up - a real, serious
  risk once this process is genuinely publicly reachable, not just a local dev nicety. `waitress` (a
  pure-Python production WSGI server) was already added to `requirements.txt` by the previous
  session before it went down; I added the explanatory comment.
- Ran the full test suite (90/90 passing, no regressions from any of this).
- **Real smoke test, not just structural**: started `server.py` via `waitress-serve` on one local
  port and `proxy_app.py` via Flask's dev server on another, pointed `LOCAL_TUNNEL_URL` at the first.
  Confirmed via curl: a GET through the proxy returns real data from the real app
  (`/api/settings`); a session cookie set by the real app survives being relayed through the proxy
  and is correctly sent back on a follow-up request through the proxy (same `user_id` both times);
  the `Secure` flag is actually present on that cookie and curl still honored it over plain HTTP to
  `127.0.0.1` (confirming the browser-secure-context reasoning above, not just assuming it); a POST
  relays correctly too (real 404 from real character-lookup logic, not a proxy-level error); and with
  `LOCAL_TUNNEL_URL` unset entirely, the proxy answers with a clean 503 rather than crashing or
  hanging. **What's still NOT verified**: an actual Cloudflare quick tunnel round-trip (this
  session's smoke test used two local ports directly, no real tunnel involved - `local-ollama-proxy`'s
  tunnel pattern is already proven to work end-to-end, but that doesn't guarantee this specific
  proxy behaves identically over a real tunnel hop), and the real Render deployment itself (I have no
  access to the Render dashboard from here - the Start Command switch and `LOCAL_TUNNEL_URL` env var
  are both manual steps only the user can do, documented in `local-site-tunnel/README.md`).
- This write-up, plus `README.md`/`ROADMAP.md` updates.

**Judgment call, not explicitly specified: old Render-hosted `butterfly.db` is now simply
abandoned, not migrated.** Once Render runs `proxy_app.py` instead of `server.py`, nothing on Render
reads that old database file anymore - any accounts/characters that happened to survive there (per
the known wipe-on-redeploy bug, unlikely to be much) become permanently unreachable through the live
URL. I did not attempt to fetch/migrate it (no access to Render's filesystem from here, and given
the wipe bug, there's a real chance there's nothing worth migrating anyway) - flagging rather than
silently accepting data loss.

**Left alone, flagged as a small future win, not done:** `OllamaTunnelProvider` still calls Ollama
via the public `OLLAMA_TUNNEL_URL`, even though the real app and Ollama now run on the same
machine - a same-machine call now takes an unnecessary round trip out through Cloudflare and back.
Still fully correct as-is, just not maximally efficient; pointing it at the local proxy's own
`127.0.0.1:11435` directly once `server.py` itself is local is a small, easy change for later. See
`local-site-tunnel/README.md`.

---

# URGENT FIX: Council Mode skipped for local-ollama (2026-08-21, live-site incident)

A real player turn against the local model on the live site appeared to hang past a minute. Checked
immediately: not actually hung (GPU at 36% utilization, `ollama ps` showed fresh activity, proxy/
tunnel both healthy and answering `/health`) - genuinely just this slow. Did the math from real
measured data rather than the cloud-model-shaped estimate in the entry below: Qwen3 burns 1000+
reasoning tokens even on short structured judge output (measured directly, unmocked), not just on
narration. At ~9-29 tok/s, a full council turn (~8-11 calls: router, extra candidate, 3-6 judges,
chairman, voice editor, on top of the baseline generation) realistically runs **6-24 minutes** -
confirmed against the actual incident, not just projected. The worse end exceeds gunicorn's 900s
timeout too, so the turn could fail anyway after all that wait.

**Fixed, not just documented**: `council.SKIP_COUNCIL_FOR_MODEL_KEYS = {"local-ollama"}`, checked in
`engine.run_turn` before ever calling `council.run_council`. This directly reverses the "council
follows the player's model choice, including local-ollama" decision from the entry below - flagged
there as something to revisit if the tradeoff felt wrong in practice, and it did, immediately, on
first real use. Mechanics are unaffected either way (Council Mode never touches dice/state) - a
local-model turn now makes exactly one provider call, same as if council weren't enabled at all.
Considered capping output tokens instead of skipping outright; rejected because a reasoning model
needs room to finish its `<think>` block AND produce its real answer - a cap tight enough to bound
latency risks cutting generation off before it ever reaches the actual answer or tool call, which is
worse than the panel simply not running that turn.

**One thing worth knowing**: this fix only helps turns started AFTER Render redeploys with it. Any
turn already in flight when this lands is running the old code and will still take however long it
takes (or hit the timeout) - there's no way to retroactively speed up or cancel a request already
running on Ollama from here.

---

# Free local model via home GPU + Cloudflare tunnel (2026-08-20/21, overnight into early morning)

Implemented the full feature: a local Ollama instance (qwen3:30b, set up earlier tonight) exposed
to the live Render deployment as a free, player-selectable model, via a Cloudflare quick tunnel and
a small local auth/rate-limit proxy. Both halves are verified against REAL infrastructure, not just
mocks - the local proxy, a real Cloudflare tunnel, and OllamaTunnelProvider were all exercised
end-to-end against each other during this session. Judgment calls and findings below, roughly in
order of how much they change the design versus just documenting a choice.

## Endpoint switch: OpenAI-compat -> native Ollama API (a real finding, not a preference)

The original plan was to use Ollama's OpenAI-compatible `/v1/chat/completions` surface, reusing
patterns close to OpenAIProvider. Testing directly against the running Ollama instance found that
endpoint **silently ignores** a context-length override in the request body - `ollama ps` kept
reporting whatever the Ollama desktop app had configured (confirmed ~4K per the user's screenshot)
regardless of what was sent, tried both as a nested `options.num_ctx` and a top-level `num_ctx`
field. Ollama's NATIVE `/api/chat` endpoint honors `options.num_ctx` correctly (confirmed) and
supports forced `tool_choice` the same way (confirmed) - so both the proxy and
`OllamaTunnelProvider` target that endpoint instead. This is why `OllamaTunnelProvider` hand-rolls
requests via `requests` rather than reusing the `openai` SDK the way `OpenAIProvider` does - the SDK
is built around OpenAI's own endpoints, not really a natural fit for a different JSON API.

## num_ctx=24576 - reasoned from real measurements, not picked arbitrarily

Full reasoning lives in `local-ollama-proxy/proxy.py`'s comment next to `NUM_CTX`, summarized: the
real system prompt (rules/NPCs/factions/canon/resources/state) measures ~9,500 tokens, ~10,650 with
tool definitions - the floor requirement for a single call with zero conversation history. Tested
context sizes directly on this machine (RTX 4080 Laptop, 12GB VRAM): GPU-resident model layers
declined gently as context grew (~54% GPU at the ~4K default, ~51% at 16K, ~49% at 24K, ~47% at
32K) rather than falling off a cliff, so there was real room to raise it. 24576 covers the
~10,650-token floor with ~14K tokens of headroom for conversation growth (which isn't summarized/
trimmed anywhere in this app yet - an already-documented gap, so it grows unboundedly turn over
turn regardless of this number) while keeping just under half the model GPU-resident.

## Real latency is worse than initially estimated - found via unmocked testing, not assumed

Earlier NOTES.md cost-math (for the always-on Council Mode redesign) assumed judge/router calls
would produce short output (~80-100 tokens) based on how cloud models typically behave for
structured scoring tasks. Running `OllamaTunnelProvider` directly against the live tunnel (not
mocked) found qwen3 is a reasoning model that thinks extensively regardless of task size - a single
judge call scoring one sentence produced **1,304 output tokens** of chain-of-thought before its
15-word structured answer; a "say hello in three words" narration call produced 1,618. At the
~10-25 tok/s measured on this hardware, that's routinely 1-2+ minutes for a SINGLE call. Council
Mode chains ~9 such calls per turn (router, extra candidate, 3-6 judges, chairman, voice editor) -
a fully-reviewed turn on the local model could reasonably take many minutes end to end, not the
"maybe a bit slow" I'd loosely assumed going in.

This drove a real, necessary infrastructure change: gunicorn's Procfile/render.yaml startCommand now
sets `--timeout 900` (without it, Render would kill the worker mid-response on any slow local-model
turn) and `--worker-class gthread --threads 4` (without THIS, gunicorn's default single sync worker
would let one slow local-model turn block every other player on the site - not just the person who
picked it - for the full duration). Confirmed this is safe with the existing per-account turn lock,
which already used `threading.Lock` specifically (correctly serializes across threads in one
process; the multi-*process* case was already called out as its one known gap, not multi-thread).

**What I still can't verify from here**: whether Render's own platform-level proxy imposes an
independent timeout ceiling below 900s regardless of what gunicorn allows - same caveat as the
gunicorn-timeout note from the budget feature earlier tonight, still unresolved, would need Render's
docs/support to confirm. If local-model turns still time out after this change, that's the likely
next thing to check.

## Council follows the player's own model choice, including the free local one (confirmed per your instruction to write this up, not silently decide)

You asked me to confirm this reasoning here rather than just deciding quietly. Two options existed:
(A) council's internal machinery (router/judges/chairman/candidates) follows whatever model the
player picked for their turn, including local-ollama; (B) council's internal machinery always uses a
fixed model (Luna) regardless of the player's choice, treating it as "infrastructure" separate from
"content." I picked (A) - `_cheap_model_key_for` now just returns the player's own model_key
(previously it forced everything onto `LOCKED_MODEL_KEY`). Reasoning: if a player picks the free
local model specifically to avoid cost, having council's ~9 internal calls silently keep billing a
paid model would work against the entire point of offering the free option - "free" should mean the
WHOLE turn, not just the visible narration call. The tradeoff this accepts: choosing local-ollama
now means Council Mode's full latency cost (the many-minutes concern above) applies to literally
every turn for that player, with no way to get council's quality benefit on a fast paid model while
still avoiding cost on the narration itself. If that tradeoff turns out to feel wrong in practice,
option (B) is a small, isolated change (pin `_cheap_model_key_for` and the candidate/chairman model
choice back to a fixed key) - flagging so it's easy to revisit, not treating this as permanently
settled.

## ALLOWED_MODEL_KEYS replaces the singular LOCKED_MODEL_KEY (confirmed per your instruction)

You asked me to confirm how much of the earlier "lock everything to Luna" direction to keep versus
relax, rather than deciding silently. Widened `providers.LOCKED_MODEL_KEY` (a single forced model)
to `providers.ALLOWED_MODEL_KEYS` (a set, currently `{"luna", "local-ollama"}`) - Sonnet/Haiku/Opus
stay excluded from both `/api/models` and the turn route exactly as before (still hidden, not
shown-disabled, per that earlier direction), but the picker now offers a genuine choice between the
two currently-sanctioned options rather than showing one disabled entry. This is the smallest change
that satisfies "show Luna (locked/default) plus the new free local option, still hiding the rest" -
reverting to full openness is still one constant (`ALLOWED_MODEL_KEYS = None`), same revert story as
before.

## Local model still requires Discord login, despite being free - a call I made, not explicitly asked

Your instructions said this provider should be free ($0 against budget) and go through the existing
per-account turn-lock "like any other provider" - they didn't explicitly say whether it should also
be exempt from the Discord-login requirement that gates every OTHER provider (since that
requirement's original reasoning was specifically about a *budget* being meaningless against the
shared local-profile identity - see the earlier budget-redesign NOTES.md entry). I chose NOT to
exempt it: the turn route's `if user["is_local"]: return 403` check still applies unconditionally,
before model_key is even read. Reasoning: even though there's no dollar budget to protect, there's
still a real resource being protected (this specific user's home GPU/electricity), and the local
profile's shared/trivially-resettable identity (documented in the earlier budget NOTES.md entry)
would make it a much easier target for someone hammering the free option anonymously than a real
Discord-gated identity. This felt like the safer default, but it's a genuine judgment call, not
something explicitly specified - happy to relax it if the friction turns out to not be worth it.

## Health check design: lazy + cached, not a background thread

`ollama_tunnel_is_healthy()` checks the proxy's `/health` endpoint on demand (not proactively polled)
with a 3-second timeout and a 20-second result cache. Chosen over a background polling thread for
simplicity - no new always-running infrastructure inside the Flask process, consistent with this
app's existing "no extra moving parts" style (no Redis, no APScheduler, no Celery anywhere in it). A
background thread would avoid ANY per-request latency risk (a cache miss still costs up to 3
seconds), which is the real tradeoff being made here - acceptable at this app's scale, worth
revisiting if `/api/models` latency ever becomes a real complaint.

## What's verified vs. not

Verified for real, not mocked: the full local-machine stack (Ollama -> proxy -> Cloudflare tunnel)
end to end, including auth rejection and a real forwarded/answered request through the public tunnel
URL; `OllamaTunnelProvider` run directly (unmocked) against the live tunnel for both plain narration
(confirmed thinking-stripping works on a real response) and forced tool-calling (confirmed structured
judge-style output parses correctly); `/api/models` and the rendered model `<select>` against the
live tunnel's real health-check result, both correctly showing Luna unavailable (no local API key)
and local-ollama available. NOT verified: a complete authenticated turn through the actual browser
UI with a real Discord login (no real Discord OAuth credentials available locally, same limitation
as every other Discord-gated feature tonight) - the request/response shapes are proven correct at
the provider level, but the full click-path through a real login has not been exercised. Also not
verified: real behavior once this is actually live on Render (the timeout/threading fix is reasoned
from measured local latency, not confirmed against Render's actual platform behavior).

---

# Council Mode redesign: always-on + cost math (2026-08-20, later that night)

Direction change from you: Council Mode is no longer opt-in - it runs on every turn, with a router
deciding panel size (>= 3 judges floor, up to all 6). You explicitly asked me to do the real cost
math rather than silently absorb it, since "every message, minimum 3 judges" is a materially bigger
default cost than the stakes-gated version. Here it is, worked from actual file sizes in this repo,
not guessed - and it's a bigger number than I expected going in, which is exactly why you were right
to ask for it before this became the default.

## The math

I measured the actual system prompt content for Petrograd 1917/Red Admiral (rules.md + npcs.md +
factions.md + canon_timeline.md + resource_schema.json + the character state digest) at **36,887
characters** - at the standard ~4 chars/token estimate for English text, that's **~9,500 tokens**
just for the base prompt every GM call sends, before tools or conversation history. `TOOL_SPECS`
(request_check/propose_state_update/propose_level_up) adds another ~1,140 tokens on top for calls
that offer tools. These are real measurements (`wc -c` on the actual files), not estimates - the
per-call token counts below (conversation history, output length) are estimates built on top of
that real base number.

Per-call cost estimates at Luna pricing ($0.20/$1.20 per 1M input/output tokens):

| Call | Input (est. tokens) | Output (est.) | Cost |
|---|---|---|---|
| Baseline turn generation (existing, not new) | ~11,000-11,500 (full prompt + tools + history) | ~200-300 | ~$0.0025-0.0027 |
| Router (judge selection) | ~1,080 (roster + context summary) | ~80 | ~$0.0003 |
| Extra candidate | ~9,950 (full prompt, no tools) | ~300 | ~$0.0024 |
| Each judge | ~1,000 (rubric + facts + 2 candidates) | ~90 | ~$0.0003 |
| Chairman | ~1,500 (candidates + judge notes) | ~380 | ~$0.0008 |
| Voice editor | ~580 | ~300 | ~$0.0005 |

Turns typically make ~1.5 baseline generation calls (some resolve in one call, some need a second
after a tool result) - call it **~$0.004/turn for the baseline generation that already existed**
before tonight.

**Council's incremental cost per turn**: router + candidate + judges + chairman + voice editor.
- At the MIN_JUDGES=3 floor: 0.0003 + 0.0024 + (3 × 0.0003) + 0.0008 + 0.0005 ≈ **$0.0048/turn**
- At the max of 6 judges: 0.0003 + 0.0024 + (6 × 0.0003) + 0.0008 + 0.0005 ≈ **$0.0057/turn**

**Total cost per turn with Council Mode**: roughly **$0.009-0.010**, call it ~$0.0092 as a
representative middle estimate (baseline ~$0.004 + council ~$0.0048-0.0057).

**Turns per $0.10 account**: 0.10 / 0.0092 ≈ **~11 turns**. Without Council Mode's incremental cost,
the same budget would last roughly 0.10 / 0.004 ≈ **~25 turns**. Council Mode roughly **cuts the
number of turns a $0.10 budget affords by more than half**.

## What this means for the $0.10 default

~11 turns is genuinely limiting for anything resembling a real campaign - even a fairly short
narrative arc (the game's canon timelines span multiple in-fiction months with several tracked
canon events each) will run out of budget well before reaching a meaningful stopping point. I did
NOT change `DEFAULT_BUDGET_USD` myself - you asked me to surface this so you can decide, not to
silently pick a new number. Options as I see them, not mutually exclusive:
- **Raise the default budget.** Something like $0.50-$1.00 would buy roughly 55-110 turns at this
  rate - enough to actually experience a campaign arc. Simplest lever, direct dollar cost.
- **Prompt caching** (not currently implemented anywhere in `providers.py` - no `cache_control` is
  set on any call today) would be the highest-leverage fix rather than a budget increase: the
  ~9,500-token base system prompt is IDENTICAL across the baseline call, the extra candidate, and
  turn-to-turn except for the small "current state" digest at the end. Anthropic/OpenAI both discount
  cached-prefix tokens roughly 90% (see `pricing.py`'s cache multipliers from an earlier commit
  tonight). If the stable prefix (rules/NPCs/factions/canon/resources - everything except the state
  digest) were cached, the baseline turn and the extra candidate could both drop from ~9,500-11,500
  priced input tokens to a small uncached tail plus a ~10%-priced cached prefix - a large fraction of
  the real cost here. This is a genuinely separate, real piece of work (restructuring
  `build_system_prompt` to put volatile content last, adding `cache_control` breakpoints, verifying
  cache hits) - flagging as the most promising next lever, not implementing it tonight.
- **Fewer judges as the floor**, or a narrower judge roster, would reduce cost roughly linearly but
  goes directly against what you asked for (a 3-judge floor, 6-judge roster) - noting it as a lever
  that exists, not recommending it.

## Other things worth knowing about this redesign

**Judge/router/candidate token counts above are estimates, not measurements** - I could measure the
real system prompt size exactly (I did), but conversation history length, actual narration length,
and judge output verbosity all vary turn to turn and campaign to campaign. Treat the per-turn total
as a reasonable planning estimate, not a guarantee - the only way to get real numbers is running
actual turns against the live APIs, which (as before) I still can't do from here.

**Model lock**: separately, you also directed every call (generation, router, judges, chairman -
everything) locked to GPT-5.6 Luna only, disabling the "escalate to Sonnet for high-stakes turns"
idea and the README's mid-game model-switching feature, until you say otherwise. This is
`providers.LOCKED_MODEL_KEY = "luna"` - explicitly a temporary standing default (not a removal;
`MODEL_REGISTRY` still has all four entries), reverted by setting that one constant back to `None`.
Enforced server-side (a direct API request can't bypass it even if the frontend selector is hidden)
and the router has no model-tier-escalation logic at all right now - its only job is judge-panel
size, never which model runs anything. The cost math above already assumes Luna-only throughout,
since that's the actual locked behavior.

# Council Mode (2026-08-20, overnight)

Implemented the full design you'd already approved (LLM council pattern - independent generation,
blind cross-ranking by specialist judges, chairman synthesis - plus the advocate/premortem judge
pair and a voice-editor pass). Mapped it onto the actual codebase across 5 commits: always-on voice
guidance + rule-based gate, `force_tool_name` support in `providers.py` (needed for structured judge/
chairman output), the full `run_council` orchestration, server-side wiring, then the frontend
toggle. Since the high-level design was already decided, this section covers the judgment calls in
mapping it onto this specific codebase, not a re-litigation of the design itself.

**Per-campaign, not per-account.** You left this open ("per-campaign or per-account setting"). Went
per-campaign: stored in the character's existing `state` JSON (`state["council_mode"]`), no schema
migration, no new DB column. Reasoning: Council Mode changes a cost/quality tradeoff for one
specific campaign's narration, not a property of the account as a whole - a player might want it on
for a high-stakes Trotsky campaign and off for a casual one on the same account. If you'd rather it
default per-account (e.g. so a new campaign inherits whatever the player last chose), that's a
different, slightly bigger change - flagging rather than assuming.

**2 total candidates, not 3+.** The normal turn's narration is free candidate #1; only one more gets
generated when the council actually convenes. This is a deliberate cost-conservative starting point,
not a finding from testing (I couldn't playtest real costs - see below) - easy to bump up once you
have real numbers on what a convened turn actually costs.

**Judge model selection**: prefers Haiku when the turn's on Anthropic, falls back to reusing the
same model when it's Luna (OpenAI), since `MODEL_REGISTRY` doesn't currently register anything
cheaper than Luna for OpenAI. This means on the Luna default, judges/candidates/chairman are ALL
the same model tier - the cost savings from "judges use a cheap model" only actually materializes
today when a player has picked Sonnet/Opus for their turn. Worth knowing if judge cost looks higher
than expected on the default path.

**Canon-event gating is broader than literally "locked" events.** The design brief said gate on "a
canon-locked event"; I gate on ANY canon event firing, locked or contingent (see `should_convene_
council`). Reasoning: a contingent event firing represents genuine player-driven divergence, which
if anything is MORE narratively significant than a locked event playing out as scripted - gating
only on locked events felt like it would miss the more interesting case. Flagging the interpretation
since it's a deviation from the literal brief, not obviously "the smallest correct reading."

**What I could NOT verify - the most important gap.** I did not run a real council-convened turn
against live Anthropic/OpenAI APIs. No API keys are configured in this repo (deliberately excluded
when it was split out, see earlier NOTES), and Council Mode additionally now requires Discord login
(per tonight's earlier budget change), which I have no way to complete locally either. Everything
is verified against mocked providers (56+ tests) and real UI click-throughs of the toggle/display
plumbing - but the actual thing the opt-in design was FOR (seeing real cost-per-convened-turn and
real narration quality before ever defaulting this on) hasn't happened yet. This is the literal
next step before this feature is actually validated, not just "implemented." Given a ~$0.10 total
budget and roughly 9 extra LLM calls on a convened turn (1 candidate + 6 judges + chairman + voice
editor, on top of the normal turn's own call), I'd genuinely recommend checking real cost on one
convened turn before recommending anyone turn this on with the current $0.10 default - it's
plausible a single convened turn could eat a double-digit percentage of an entire account's budget,
though I don't have real numbers to confirm or rule that out.

**Minor code smell noticed while testing, not fixed**: `run_council`'s judging loop checks
`budget_ok()` both in the outer loop and again inside `_run_judge` - redundant in production (a real
`budget_ok` is monotonic - once exhausted it stays exhausted, so the double-check never actually
matters), but it made a call-counting mock in testing behave less intuitively than expected until I
switched the test to count real API calls instead. Not worth a change on its own, just documenting
in case it's confusing to a future reader.

---

# Game rules balance pass (2026-08-20, late night)

You asked for a real balance pass on the game rules themselves - dice math, XP/leveling, and all
three systems' feats/liabilities/DCs, grounded in the actual numbers rather than vibes, taking
inspiration from D&D 5e/comparable tabletop design (bounded accuracy, a real level-1-through-5
curve, feats that are situationally strong not strictly-better, liabilities as real tradeoffs).
Read `systems/_shared/core.py` (the shared dice/XP/leveling/creation math) and all three systems'
`mechanics.py`/`rules.md` in full before touching anything. Four changes shipped, in four commits;
here's the reasoning behind each, plus what I found but deliberately left alone.

**1. XP awards had no rubric - the single most underspecified number in the system.** The
`xp_award` tool parameter was documented as just "0-60," with the actual amount entirely up to the
GM model's in-the-moment judgment. Since XP is the only thing driving leveling pace, this meant
pacing was effectively random across sessions/models rather than designed - exactly the "dead
levels or runaway snowballing" risk you named, just from the award side rather than the DC side.
Fixed with a concrete guideline table in the tool description (0 pure narration, 5-10 routine,
15-25 meaningful, 30-45 hard/pivotal, 50-60 rare campaign-defining moments), with an explicit steer
against inflating awards just to keep leveling feeling brisk. Lowest-risk change of the four - pure
prompt/tool-description text, no code path changed.

**2. Liabilities had zero creation-time upside - not a tradeoff, an unrewarded roleplay flag.**
Taking a starting liability cost something narratively but gave nothing back mechanically, so the
optimal-play choice was always to take none. This is close to the opposite of what you asked for
("real tradeoffs not free stat sticks") - it was actually backwards, a free downside with no stick
to trade. Fixed: each starting liability now grants +2 bonus skill points
(`LIABILITY_BONUS_SKILL_POINTS`), up to +4 for the max of 2.

**3. Level-1 characters could already be broadly competent across roughly half the skill list.**
At the old flat 14-point budget, 3 Expertise skills (6pts, the max allowed) plus 8 more at
Proficiency was affordable - out of each system's ~20-24 total skills, that's real competence in
close to half of them before a single check has been rolled. That leaves little room for a
level-1-through-5 *growth* arc, since a fresh character already reads as broadly accomplished
rather than someone who grows into competence. Trimmed the base `SKILL_POINT_BUDGET` from 14 to 10;
combined with #2 above, a character built "clean" now starts leaner, while one willing to take on
both starting liabilities can still reach the old 14-point ceiling - same absolute maximum, now
earned rather than default. **Judgment call, not a bug fix**: 10 and +2-per-liability are numbers I
picked to feel meaningfully tighter without gutting character concepts that need some starting
breadth (e.g. Rome's Cicero-type "man of many talents" builds) - reasonable people could land on
9-12 instead. I deliberately did NOT also cut `MAX_STARTING_EXPERTISE_SKILLS` (still 3) - the budget
cut alone does the tightening; stacking two simultaneous nerfs felt like overcorrecting without
seeing the first one in play.

**4. Feats had literally zero code-enforced effect anywhere - the biggest finding.** Every feat in
every system was pure GM narrative ("the GM should let a clever turn of phrase win over an
audience..."), with no numeric backing at all. This is a real inconsistency against the engine's
own stated premise (the app owns mechanical outcomes, not the model) - feats were the one entire
progression category that broke that rule completely, and it's structurally the same failure mode
you were worried about elsewhere: a feat's actual strength would vary entirely with how generous a
given session's GM model felt like being, not with deliberate design. Gave `roll_check` an optional
`feat_catalog` param and a small code-enforced `check_bonus` mechanism (mirrors liabilities'
existing `auto_trigger` pattern) - mechanized exactly one clean Oration-flavored feat per system
(+2) plus a second clean match per system (Physique for Red Admiral's Iron Constitution, since its
framing is genuinely physical not willpower; Resolve for Monarch/Apollo's Unyielding
Faith/Stoic Composure, since "steadies under pressure" is clean willpower).

**Judgment call on where I drew the mechanization line**: I deliberately left every *other* feat
as pure narrative - most (a once-per-arc favor, "sense a plot before it breaks," NPC-social-class-
dependent warmth, "unfamiliar terrain" ease) don't have a trigger that `roll_check`'s actual inputs
(attribute, skill, DC, target NPC) can honestly detect without tracking more state than the engine
does today (NPC social class, "is this terrain unfamiliar to this specific character," rolling
counters for once-per-arc effects). I could have forced a numeric mapping onto those anyway, but it
would have been a dishonest approximation of what the feat's fiction actually describes, not a
clarifying one - and half-mechanizing everything risked a much bigger, riskier change than tonight's
scope. If you want more feats to carry real numeric weight, the `check_bonus`
mechanism is now there and it's a small addition per feat - but the ones I left narrative genuinely
don't fit that shape without new state-tracking (e.g. a per-arc-favor counter) that's a bigger
design decision than "which feats get +2."

**+2 as the bonus magnitude**: proportionate to the existing DC spacing (DCs run 8/12/15/18/22,
~3-4 apart), so it's a real, felt bonus - roughly half a difficulty tier - without trivializing the
checks it applies to. This is also a taste call; I didn't find a principled reason it couldn't be
+1 or +3 instead.

**What I found but did NOT fix - flagging for you rather than expanding scope further:** at the top
of the level range (17-20), an optimized specialist's modifier gets large enough that even DC 22
("extraordinary") starts trending toward a comfortable success rate (back-of-envelope: max attribute
mod +5, Expertise at proficiency+6 = +12, plus a matching feat's +2 = +19 modifier, needing only a
roll of 3+ against DC 22). This is a real bounded-accuracy erosion at the high end, structurally
similar to what 5e itself experiences with heavily-optimized high-level specialists - but you
specifically asked about the level-1-through-5 curve, and re-architecting the top end (e.g. scaling
DCs with level, capping Expertise's multiplier, adding a higher "legendary" DC tier) is a
materially bigger change than tonight's four fixes. Noted here rather than acted on. On the
positive side: I checked the level-1 DC/modifier spread specifically (the thing you most explicitly
asked about) against a fresh custom character's best- and worst-case builds, and it already looks
well-tuned as-is - a specialist gets real, uncertain odds on "hard" checks (~65%) and "extraordinary"
stays genuinely hard (~30%), while an unpracticed attempt at the same DCs correctly struggles
(10-25%). I didn't touch the DC table itself; it didn't need it.

Verified throughout: ran the full test suite after each of the four changes (37/37 passing by the
end - added 16 new tests across three new files: `test_character_creation_balance.py`,
`test_feat_check_bonus.py`, plus the XP-rubric change needed no new test since it's pure text) and
did a live click-through in the running dev server for the two changes with visible UI surface
(confirmed the creation form's budget line updates 10 -> 12 -> 14 as liability checkboxes toggle;
confirmed a freshly-created premade character with a mechanized feat shows the updated description
in the actual sidebar). Did not touch resource mechanics (`MAX_RESOURCE_DELTA_PER_CHECK`,
`RESOURCE_LABELS`) - they weren't part of what you asked about (dice/XP/leveling/feats/liabilities/
DCs) and looked reasonably scoped as-is on inspection.

---

# Budget tied to Discord account (2026-08-20, direction change from you)

You reopened decision A: budget should be fully tied to the Discord account, not the local
session-cookie profile, and mentioned local hosting is an acceptable persistence option alongside
Render's Postgres.

**Correction to my own earlier notes first:** investigating this, I found my prior claim in this
file ("local-profile budgets are resettable via cookie-clearing") was wrong, and the real situation
is more significant than that. `db.get_or_create_local_user()` returns **one single shared row for
every anonymous visitor across the whole deployment** - `discord_id` is `UNIQUE` and the local
sentinel value never varies, so there's no per-browser local identity at all, just one global one
everybody without a Discord login shares. Clearing cookies doesn't get you a fresh budget; every
anonymous visitor was always sharing the same one. This was already true in the original code
before last night's work (its own docstring says "a single stable 'local' account," which I read
too quickly and mis-described as per-browser). This makes the direction you gave the actually-
correct fix, not just a preference - a per-account budget genuinely cannot mean anything against an
identity that everyone shares.

**What I built - the smallest change that's actually correct, not a full identity merge:**
Local/anonymous profiles keep working exactly as before for browsing settings and creating
characters (harmless, no cost). Only `POST /api/character/<id>/turn` - the one action that spends
real money - now requires a Discord-linked session; a local user hitting Send gets a clear 403
(`discord_login_required: true`) instead of ever reaching the provider API. `/api/me` reports a
zeroed, non-actionable budget for local users rather than the shared row's now-meaningless numbers.
The frontend shows "Log in with Discord to unlock AI turns" in place of a dollar figure on both the
landing page and in-game header, and locks the input bar the same way an exhausted budget already
did. No schema change was needed - `users.budget_usd` / `usage_events` already work correctly
per-user-row; Discord accounts already get their own row via `get_or_create_discord_user()`, so
"budget per Discord account" was already what the ledger did, once local accounts are excluded from
ever writing to it.

**Your question on migration - did I need to move accumulated local spend onto a Discord account
once someone links?** No, and here's the actual reasoning rather than a guess: since local profiles
can now never spend anything (the turn route rejects them before any provider call happens), there
is no *ongoing* local spend that could ever need migrating - a local session's `usage_events` history
will always be empty from this point forward. A full identity-merge system (transferring existing
characters/spend from a local session onto a Discord account at login time) was NOT needed to
achieve "budget tied to Discord" correctly - it would have been solving a broader problem (character
ownership continuity) than what you asked for, so I didn't build it.

**What I genuinely can't verify and am flagging rather than guessing:** whether the shared local
account already has *some* historical spend recorded in `usage_events` from earlier tonight's
testing or from your own click-through on the live Render deployment. I don't have access to the
live deployment's actual database to check. If it does, that spend is now orphaned/irrelevant (the
shared local account can't spend going forward, so its historical `budget_usd`/`spent_usd` numbers
don't gate anything anymore) - harmless dead data, not something I'd recommend spending effort
cleaning up, but flagging so you're not surprised if you go looking at that row directly.

**On Postgres vs. local hosting:** noted your direction in `ROADMAP.md` (both are now listed as
acceptable answers to the "durable storage" problem) but did not act on either tonight - that's a
real infrastructure change (a hosted Postgres migration, or documenting/supporting a local-hosting
deployment path) that felt like new scope beyond "the smallest correct change for the budget
direction," not something to fold into this same commit. Happy to pick it up as its own piece of
work if you want it prioritized.

Verified: updated the existing tests that assumed a local session could send turns (they now log in
as a Discord user first, matching the new requirement) and added 3 new ones covering the local-user
403 and the local/Discord split in `/api/me` - 26/26 passing. Also drove a full click-through
against the running dev server confirming the landing page and in-game header both show the login
prompt for a local session, and that an actual turn attempt from a local session gets rejected with
the clear 403 before any provider call.

---

# Security/correctness self-review (2026-08-20, after live deploy confirmed working)

You asked for a review of budget-bypass vectors, concurrent-turn races, cookie forgery, and Discord
OAuth CSRF on everything from the overnight session. Findings and fixes, most important first:

**Real bug, fixed: concurrent requests could jointly overspend an account's budget.** The turn
route read `remaining_usd` once, then spent real money across a multi-second LLM call before
writing the result back. Two requests for the same account arriving close together (a double-click,
two open tabs, two devices on one Discord login) could both read "budget OK" before either commits
its spend, and jointly overshoot it - a real TOCTOU race, trivially triggerable by just clicking
Send twice fast. Fixed with a per-user `threading.Lock` in `server.py`
(`_turn_lock_for`/`_turn_locks`) that makes a second concurrent request for the same account fail
fast with 429 instead of racing the first. **Caveat I'm not certain about and didn't want to
guess on:** this lock only serializes within one Python process. Nothing in this repo
(`Procfile`, `render.yaml`, `requirements.txt`) sets an explicit gunicorn worker count, and
gunicorn's own documented default with no `--workers`/`-w` flag is 1 worker - so as configured in
this repo, everything should already run single-process, and the lock is the full fix. What I can't
see or verify from here is whether Render's dashboard has any override on top of that (a manually
set worker/instance count, autoscaling, etc.) - if it does, the race reopens across processes and
would need a database-level fix instead (e.g. an atomic reserve-then-true-up against SQLite, which
is a real design change, not tonight's scope). Worth a five-minute check of the Render service
settings to confirm nothing overrides the single-worker default.

**Real gap, fixed: session cookie had no explicit SameSite, and the turn route's `force=True`
JSON parsing removes an implicit CSRF speed bump.** `request.get_json(force=True)` in
`api_character_turn` parses the body as JSON regardless of `Content-Type`, which means a plain
cross-site HTML form (no CORS preflight needed) could have posted a working request using a
victim's cookies - forcing an arbitrary game turn (spending their budget, corrupting their game
state) with no consent, if they had an active session and visited a malicious page. Fixed by
setting `SESSION_COOKIE_SAMESITE = "Lax"` explicitly (also set `HTTPONLY` explicitly, though that
was already Flask's default) - this blocks the cookie from being attached to cross-site POSTs.
Verified locally: the local profile session still persists correctly across page reloads and fresh
navigations with this set (SameSite=Lax explicitly still allows normal top-level navigation, which
is what actually mattered to check).

**Deliberately not changed - flagging instead of guessing: `SESSION_COOKIE_SECURE` is still
unset.** This should be `True` in production (stops the cookie ever being sent over a non-HTTPS
connection) but `True` locally would silently break `python server.py` for anyone following the
README's plain `pip install && python server.py` instructions over `http://127.0.0.1:5000` - the
browser simply wouldn't send the cookie back, so every request would look like a brand-new visitor.
Flipping this correctly needs a reliable "are we in production" signal, and I don't have one I trust
without guessing: `FLASK_DEBUG` isn't currently documented as something to set on Render either way,
and I can't see the live service's actual environment variables from here to confirm what it's
currently set to. Given the site is already confirmed live and working, I didn't want to gamble on
guessing that wrong and breaking login on the deployed instance. If you want this closed: either
confirm `FLASK_DEBUG` is unset/`"0"` on Render and I can safely gate `SESSION_COOKIE_SECURE` on it,
or just set it manually - `app.config["SESSION_COOKIE_SECURE"] = True` in `server.py`, since Render
serves the public URL over HTTPS only anyway.

**Discord OAuth CSRF state validation: checked, was already correct, hardened anyway.** Re-traced
the full flow: `/auth/discord/login` generates a fresh random `state`, stores it server-side in the
session, and only `/auth/discord/callback` requests where the returned `state` matches that stored
value proceed to exchange the code and log someone in - this is the standard, correct defense
against an attacker forcing their own Discord login onto a victim's session. No bypass found. Two
small hardening changes made anyway: swapped the plain `!=` state comparison for
`secrets.compare_digest` (constant-time; the state token isn't a secret being checked against
itself so this wasn't exploitable, but it's the correct default for any token comparison and costs
nothing), and URL-encoded the `auth_error` values before putting them in a redirect Location header
(previously an error message containing `&` or similar could have malformed the redirect query
string - low severity today since the frontend only `console.warn`s this value rather than
rendering it as HTML, but cheap to close properly).

**Budget-bypass via manipulating cost calculation: checked, found nothing.** Traced every value
that reaches `pricing.estimate_cost_usd` and `db.record_usage` back to its source: token counts and
the `model` string come from `TurnResult.usage`, which is populated inside `providers.py` directly
from the real Anthropic/OpenAI SDK response objects - never from anything in the request body.
`model_key` is client-supplied but is checked against `providers.MODEL_REGISTRY` (a fixed dict, not
client-extensible) before it's used for anything, so a client can pick which registered model to
use but can't smuggle in a fake model id to hit the "unknown model, cost defaults to $0" fail-open
path (already documented below) - that path is only reachable if `pricing.py` and
`providers.MODEL_REGISTRY` fall out of sync with each other, not from client input. `user_id` for
both the budget check and the `usage_events` insert always comes from the signed session, never
from the request body. No manipulation vector found here.

Verified via the existing test suite plus 6 new tests (`tests/test_security_hardening.py`: the lock
rejects a genuinely-concurrent second request, different accounts aren't cross-serialized, the
Discord callback rejects a missing/wrong/empty state, and the `auth_error` redirect is properly
encoded) - 24/24 passing - and a real click-through against the running dev server confirming the
session cookie still survives page reloads/fresh navigations under the new SameSite setting, and
that a forged Discord callback state is correctly rejected in an actual browser.

---

# Notes from the overnight session (2026-08-19 → 2026-08-20)

Implemented items 1-3 from the landing-page/login/usage-budget feature request while you were
asleep, using the two decisions you relayed (A: keep local/Discord accounts separate; B: current
SQLite+Discord persistence is sufficient - item 4 needed no new work). Everything below is a
judgment call I made to keep moving without blocking on you - none of it is a bug, but each one is
worth a deliberate look rather than silent acceptance.

## Time-sensitive: Claude Sonnet 5 pricing changes in ~10 days

`pricing.py` has Sonnet 5 at its introductory rate ($2.00/$10.00 per 1M input/output tokens), which
Anthropic's pricing page says expires **2026-08-31**. After that it reverts to $3.00/$15.00. If
nobody updates `PRICING_TABLE["claude-sonnet-5"]` after that date, Sonnet turns will be
under-costed by 33-50% against the account's budget - not a crash, just a quietly-wrong number.
There's no live pricing API this pulls from; it's a hand-maintained snapshot by design (see the
module docstring), so this needs a human to actually go change it after the date passes.

## The usage budget doesn't stop someone from just clearing cookies

Per decision A, local profiles and Discord accounts are fully independent, and a local profile's
identity *is* its session cookie. That means an anonymous visitor whose $0.10 budget runs out can
reset it by clearing cookies (or opening a private window) and getting a fresh local profile with a
fresh budget. This isn't a bug in what got built - it does exactly what a "per-account budget"
feature should do - but it means the feature mainly protects against *accidental* runaway spend
(a long campaign, a runaway tool-calling loop) rather than someone deliberately working around it.
If the deployed app gets real public traffic and that matters, the honest next step is a per-IP or
per-session rate limit as a backstop (already on ROADMAP.md's radar), not a bigger per-account
budget.

## Unknown-model pricing fails open to $0, not blocked

If `providers.MODEL_REGISTRY` ever gets a new model added without a matching row in
`pricing.py`'s `PRICING_TABLE`, that model's usage is tracked (input/output token counts still land
in `usage_events`) but costs $0 against the budget rather than raising an error or blocking the
turn. I chose this deliberately - failing closed would mean a pricing-table typo silently breaks
gameplay entirely, which felt like the worse failure mode for a ~$0.10 hobby budget - but it does
mean adding a fifth model later requires remembering to update `pricing.py` in the same change, or
that model's spend goes untracked. Nothing enforces the two staying in sync.

## Mid-turn budget cutoff is best-effort, not a hard ceiling

A single user turn can trigger several provider API calls (each `request_check`/
`propose_state_update` round-trip in `engine.py`'s tool loop is its own billed call). The budget
guard checks *after* each call completes, not before, so a turn already in progress can push an
account's actual spend slightly past its nominal $0.10 - by at most the cost of one extra API call.
The alternative (checking before every call) can't work here anyway, since you don't know a call's
real cost until the response comes back with its usage numbers. This is a soft, not hard, ceiling;
fine for a hobby budget, worth knowing if this number ever needs to be a real financial guarantee.

## Landing page copy is a first draft

The hero copy ("Butterfly — Change history.", the pitch paragraph) is mine, not yours - purely a
placeholder-quality first pass so the page isn't blank, not something I'd consider final marketing
copy. Worth a look/rewrite whenever you want to actually put this in front of people.

## What I verified vs. what I didn't

Ran the full local test suite (18 tests: pricing math, DB budget helpers, engine budget-guard
logic, and Flask route tests against a mocked provider) - all passing. Also started the actual dev
server and clicked through the full flow in a real browser (landing → login status → begin → preset
→ setting → full system → premade character → live game screen with opening scene and sidebar),
confirming the budget chip renders correctly at each step with no console errors. I did **not** send
a real turn against a live Anthropic/OpenAI API (no `.env` exists in this repo on purpose - it was
deliberately excluded when the repo was split out, see the earlier commit history) - that's still
the actual first real integration test of this feature, and it's already the #1 item on
`ROADMAP.md`'s Phase 1 list for a different reason (verifying the provider integration itself, pre-
dating this session's work). Once real API keys are configured somewhere, running one real campaign
turn on each provider would be worth doing specifically to confirm the usage/cost numbers this
session added look sane against actual token counts, not just against the mocked test fixtures.
