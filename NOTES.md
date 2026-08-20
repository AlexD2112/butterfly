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
