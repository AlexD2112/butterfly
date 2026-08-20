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
