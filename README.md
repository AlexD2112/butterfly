# Butterfly — Change History

A historical RPG engine, separate from the Dendrynexus game in the rest of this repo. Design
philosophy: a game master narrates every scene, roleplays every NPC, and makes the fictional
judgment calls; the application rolls the dice and validates every mechanical state change -
nothing that compounds over a campaign (XP, leveling, resource clamping, feat/liability
eligibility) is left to improvisation.

Three layers, each swappable independently:
- A **system** is a ruleset - attributes, skills, dice, feats, leveling. Lives under
  `systems/<name>/`, built on shared math in `systems/_shared/core.py` so a balance fix in one
  place applies everywhere. Three exist: **Red Admiral** (revolutionary-era), **Monarch**
  (feudal/medieval), **Apollo** (Roman Republic). In-house systems are named after butterfly
  species.
- A **setting** is content built on a system - a premade signature character, NPCs, factions,
  canon timeline, resource categories. Four exist: `settings/petrograd_1917` (Trotsky, on Red
  Admiral), `settings/britannia_876` (Alfred the Great, on Monarch), `settings/rome_59_bce`
  (Julius Caesar, on Apollo), `settings/crusades_1097` (Bohemond of Taranto, on Monarch).
- A **character** is a specific play-through - either a setting's premade signature character, or
  a custom character built through the in-app creator (standard-array attributes, a skill-point
  budget, one starting feat, up to two starting liabilities). Every account can have many
  characters across many settings; state persists in `butterfly.db` (SQLite).

`core_rules.md` is the original, much simpler v1 rulebook this replaced - kept on disk for
reference, no longer active.

Playable two ways, sharing all the same underlying logic:
- **`server.py`** — the webapp (browser UI): a landing page ("Butterfly — Change history") → start
  menu (Preset → setting → gameplay mode → character select/creation) → game. Every visitor gets an
  automatic local profile (no login needed) that can browse settings and create characters, with
  optional "Login with Discord" shown on the landing page. Sending an actual AI turn requires being
  logged in with Discord, since that's what the $0.10 LLM usage budget is tied to - see "Usage
  budget" below. This is the primary way to play.
- **`loop.py`** — a terminal CLI for scripting/quick testing, always playing a chosen setting's
  premade character to a single legacy save file (no accounts/multiple characters/budget tracking).

## Layout

```
core_rules.md                    - original v1 rulebook (superseded, kept for reference)
engine.py                        - SETTING_REGISTRY, SYSTEM_REGISTRY, prompt building, the
                                    provider-agnostic turn loop - shared by loop.py and server.py
providers.py                     - Anthropic + OpenAI adapters, MODEL_REGISTRY
pricing.py                       - $/1M-token rates per model + cost estimation for the usage budget
db.py                            - SQLite persistence: users, characters, usage_events (spend ledger),
                                    turns (full per-turn transcripts, see "Viewing transcripts" below)
discord_oauth.py                 - minimal Discord OAuth2 (login only, no token storage)
server.py                        - Flask webapp (routes, sessions, gunicorn entry point)
proxy_app.py                     - thin reverse proxy Render runs instead of server.py, forwarding
                                    to wherever the real app is locally hosted (see "Hosting" below)
loop.py                          - terminal CLI
view_transcript.py               - local script to read back a character's full saved turn history
static/index.html                - the webapp frontend (single file, no build step)
requirements.txt, Procfile       - deployment (see "Deploying" below)
tests/                           - unittest suite: pricing math, budget DB helpers, engine budget
                                    guard, and Flask route tests with a mocked provider
systems/
  _shared/core.py                - shared dice/leveling/validation logic, used by every system
  red_admiral/, monarch/, apollo/  - each: rules.md + mechanics.py (skills/feats/liabilities)
settings/
  petrograd_1917/, britannia_876/, rome_59_bce/, crusades_1097/  - each: character_sheet.json
  (docs), new_campaign_state.json (real starting state), npcs.md, factions.md,
  canon_timeline.md, opening_scene.md, resource_schema.json
```

## Usage budget

Usage budget is tied to a real Discord account, not the local/anonymous profile - every visitor
without Discord shares a single local profile deployment-wide (see `db.py`'s module docstring), so
a per-account budget only means something once there's an identity that's actually unique per
person. Local/anonymous play still works for browsing settings and creating characters; sending an
AI turn requires logging in with Discord first (HTTP 403, `discord_login_required: true`, if not).

Once logged in with Discord, each account starts with a $0.10 LLM usage budget. Each turn's actual
token usage is read from the real Anthropic/OpenAI response and costed against `pricing.py`'s rate
table, recorded in the `usage_events` table, and enforced by `server.py`'s turn route: a turn is
refused (HTTP 402) once an account's remaining budget hits zero, and a single turn that makes
several API calls (the request_check/propose_state_update tool loop) gets cut off mid-turn rather
than allowed to run arbitrarily past budget. See `NOTES.md` for caveats and things worth a
deliberate look before relying on this for real cost control.

## Viewing transcripts

Every successful turn is saved in full (player input, complete narration, which model was used, and
the mechanical facts - dice rolls, state changes, canon events, Council Mode's judge panel) to a
`turns` table in `butterfly.db`. This is only recorded going forward from 2026-08-21 - earlier turns
predate the feature and have nothing saved.

There's no in-app admin page for this (the app has no admin concept at all - every route only ever
shows a user their own data) - instead, run the local script directly, on whichever machine is
actually hosting the database:
```
python view_transcript.py --list              # every character across every account, with turn counts
python view_transcript.py <character_id>       # that character's full turn-by-turn transcript
```

## Council Mode (always on)

Every turn's narration gets reviewed by a panel of specialist judges before the player sees it -
not optional, no toggle. Mechanics are never affected - dice/state updates always happen exactly
once, on the normal trusted path; the council only ever rewrites prose describing an already-decided
outcome. A cheap router call picks the panel for each turn (at least 3 judges, up to all 6, sized to
how consequential the turn looks), then: an independent second narration candidate is drafted, the
routed judges score both candidates against their own rubric (rules/mechanics, canon/continuity,
characterization, groundedness, and a "what should go right" / "what should go wrong" pair), a
chairman synthesizes a final version from all of it, and a cheap-model voice-editor pass polishes
word choice. See `council.py`'s module docstring for the full design.

**Cost**: this materially increases per-turn cost on top of whatever the base narration already
costs - see `NOTES.md` for the full math. On the default path (GPT-5.6 Luna), a turn costs roughly
**$0.009/turn, ≈11 turns per $0.10 account** - essentially unchanged from before Council Mode's
judges got their own cost-tiering fix. A player who deliberately switches to Sonnet or Opus takes on
a materially pricier turn (**~$0.07-0.08/turn, ≈1-1.5 turns per $0.10 account** on Sonnet) - an
informed tradeoff for that player, not something imposed on new accounts by default.

**Model selection**: every registered model is selectable (`providers.ALLOWED_MODEL_KEYS = None`),
GPT-5.6 Luna stays the default (`providers.DEFAULT_MODEL_KEY`) - Sonnet/Haiku/Opus are real,
deliberate choices a player can make, not the account-opening default. Council Mode's router,
judges, and voice-editor pass always run on a fixed cheap model (Luna - `council.JUDGE_MODEL_KEY`)
regardless of what the player picked for their own turn - only the baseline narration, the extra
candidate, and the chairman's synthesis use the player's actual model choice. This was a deliberate
2026-08-21 change from council's earlier behavior (following the player's choice for everything) -
see `NOTES.md` for why that stopped being safe once Sonnet/Opus became selectable, and why the fixed
judge model is Luna specifically (the cheapest registered model, not just "the cheap Anthropic
one"). Every provider's own turn generation has NOT yet been run against live APIs, only mocked -
see `NOTES.md` for what's verified versus what still needs a real playtest.

Regardless of Council Mode's panel, every turn's base narration prompt already includes a
banned-word list and anti-purple-prose/anti-hand-waving guidance (`council.VOICE_GUIDANCE`) - that
part is free and unconditional.

## Free local model (optional)

A player-selectable model option that costs $0 regardless of real token counts, backed by a home
GPU running Ollama and exposed through a Cloudflare tunnel - see `local-ollama-proxy/README.md` for
the local-machine setup and `providers.OllamaTunnelProvider` for the Butterfly-side integration.
Requires `OLLAMA_TUNNEL_URL` and `OLLAMA_TUNNEL_SECRET` to be set (both blank by default - the
option just shows as unavailable if they're not configured, nothing else breaks). Council Mode is
skipped entirely for this option (see `NOTES.md`'s "URGENT FIX" entry - real testing found it's
far too slow for a multi-call judge panel), so choosing it keeps the whole turn genuinely free and
fast, not just the narration call. The option only shows as selectable when a live health check
against the tunnel actually
succeeds - if the host machine or tunnel is down, it's hidden/disabled in the picker rather than
failing on click, and a turn that was already in flight when the tunnel drops gets a clear "local
model is currently offline" state instead of a raw error.

This model is noticeably slower than the cloud providers (it's a reasoning model on consumer
hardware, not a datacenter) - see `NOTES.md` for real measured latency and why gunicorn's timeout/
threading config needed to change to accommodate it.

## Setup

```
pip install -r requirements.txt
```

You only strictly need the key for whichever model(s) you'll actually use - the model dropdown
(webapp) or CLI startup will tell you if a key's missing rather than crashing silently.

```
cp .env.example .env
```
then open `.env` and fill in real values - see the comments in `.env.example` for what each
one does (LLM provider keys, `FLASK_SECRET_KEY`, optional Discord OAuth settings). `.env` is
gitignored and will never be committed. **Never put real secrets anywhere else in this codebase** -
not in `providers.py`, not in any `.py` file. If you ever see a real key sitting in source, treat
it as compromised and rotate it immediately, whether or not it got committed.

## Running the webapp locally

```
python server.py
```
Open `http://127.0.0.1:5000`. You land on a landing page with your account status and usage budget;
click Begin to reach the start menu. Every visitor gets a local profile automatically (no login
needed); "Login with Discord" appears there once Discord OAuth is configured (see below). Pick
Preset → a setting → Full System → a character (the setting's premade figure, one of your existing
characters, or "Create Custom Character"), then play. Each turn shows a collapsible "Mechanics"
panel with the actual dice rolls and state deltas. Progress autosaves to `butterfly.db` after every
turn.

## Running the CLI

```
python loop.py                              # Petrograd 1917, default model (GPT-5.6 Luna)
python loop.py haiku                        # force a model - keys: luna, sonnet, haiku, opus
AI_RPG_MODEL=sonnet python loop.py           # same, via env var
AI_RPG_SETTING=rome_59_bce python loop.py    # play a different setting's premade character
```
Uses a single legacy save file (`save.json`), always the chosen setting's premade character - no
accounts, no custom characters, no usage budget tracking. For those, use the webapp.

## Hosting (local machine + a thin Render pass-through)

As of 2026-08-21, the real app runs on the player's own machine, not Render - Render's free tier has
no persistent disk, so `butterfly.db` (accounts and characters) kept getting silently wiped on every
redeploy. The real app now runs locally (SQLite on a normal local disk, genuinely persistent) and is
exposed to the public internet via a Cloudflare tunnel, the same pattern already proven out for the
free local model below. Render keeps serving the existing public URL
(`https://butterfly-haa9.onrender.com`) as a minimal reverse-proxy (`proxy_app.py`) that forwards
every request to wherever the real app currently is - so anyone who already has that link keeps
working, without a new URL to distribute.

**See `local-site-tunnel/README.md` for the full setup and the every-time-you-play steps.** Short
version: run `local-site-tunnel/start_local_site.ps1` (starts the real app via `waitress` + a
Cloudflare quick tunnel), copy the tunnel's URL into Render's `LOCAL_TUNNEL_URL` environment
variable on the service now running `proxy_app.py`. The tunnel URL changes every time that script
restarts and needs updating each time - same operational reality as the free local model's
`OLLAMA_TUNNEL_URL` below. The site is only fully live while this machine is on and awake - an
accepted tradeoff, same as the local model.

### Alternative: hosting the real app directly on Render

Still fully supported if you'd rather not run this machine continuously - trades away persistent
storage (back to the wipe-on-redeploy limitation above) for not needing a machine to stay on.

1. **Push this repo to GitHub** if it isn't already (this repo already has a GitHub remote -
   `git push` your current branch).
2. **Create a Render account** at render.com (free, GitHub login works) and click **New → Web
   Service**, connect this GitHub repo.
3. **Start Command**: `gunicorn server:app --timeout 900 --worker-class gthread --threads 4`
   (`render.yaml`'s current `startCommand` points at `proxy_app:app` instead - see above - so set
   this manually rather than relying on the blueprint if you want this alternative).
4. Under **Environment**, add each variable from `.env.example` as a real environment variable
   (not a `.env` file - Render doesn't read that): `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`,
   `FLASK_SECRET_KEY` (generate one: `python -c "import secrets; print(secrets.token_hex(32))"`),
   and the three `DISCORD_*` variables once you've done the Discord setup below (optional - skip
   them and the app still runs fine with local-only profiles).
5. Deploy. Render gives you a URL like `https://butterfly-xyz.onrender.com`.

The free tier also spins down after 15 minutes idle and takes 30-50 seconds to wake back up on the
next request - expected behavior, not a bug, if the first request after a while feels slow.

### Setting up Discord login (optional)

1. Go to https://discord.com/developers/applications, click **New Application**, name it whatever
   you want.
2. Under **OAuth2 → General**, copy the **Client ID** and **Client Secret**.
3. Under **OAuth2 → Redirects**, add a redirect URL that exactly matches where your app is
   running: `http://127.0.0.1:5000/auth/discord/callback` for local dev, or
   `https://<your-render-url>/auth/discord/callback` once deployed. You can add both at once.
4. Set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and `DISCORD_REDIRECT_URI` (matching whichever
   redirect you're actually running against right now) via `.env` locally or Render's environment
   variables when deployed. Update `DISCORD_REDIRECT_URI` (and re-deploy) when you switch between
   local dev and the hosted URL, since it must match exactly what's registered in step 3.

Local accounts and Discord accounts are **not** merged - a person's local-profile characters stay
under that browser's local profile even after they log in with Discord. Fine for now; a real
account-linking flow is a `ROADMAP.md` item.

## Model switching

`providers.MODEL_REGISTRY` currently has five options, GPT-5.6 Luna (OpenAI) as default - was briefly
locked to Luna+local-ollama only, then briefly switched the default to Sonnet on 2026-08-21 before
being reverted the same day (see `NOTES.md`); every model has been selectable again since:

| key            | provider  | model                        |
|----------------|-----------|------------------------------|
| `luna`         | OpenAI    | `gpt-5.6-luna` (default)     |
| `sonnet`       | Anthropic | `claude-sonnet-5`            |
| `haiku`        | Anthropic | `claude-haiku-4-5-20251001`  |
| `opus`         | Anthropic | `claude-opus-5`              |
| `local-ollama` | Ollama (home GPU) | `qwen3:30b` - free, see "Free local model" above |

You can switch models mid-campaign (webapp dropdown, or CLI's argv/env var on a fresh launch).
Each provider tracks its own native conversation thread internally - switching models doesn't
translate raw conversation history between providers (their tool-calling formats are genuinely
incompatible), but the actual game state (character sheet, resources, relationships, canon
progress) is unaffected, since that's the authoritative source of continuity, not the raw chat
transcript.

## A note on the OpenAI integration specifically

GPT-5.6 tool calling is implemented against OpenAI's **Responses API** (`client.responses.create`),
not the older Chat Completions API - Chat Completions currently requires disabling reasoning
(`reasoning_effort="none"`) to use function tools with GPT-5.6-family models. `providers.py` was
built against OpenAI's documented Responses API shape and verified structurally against realistic
mocked responses - but **it has not yet been run against the live API**. If you hit an error on
your first Luna turn, check the exact exception message against OpenAI's current Responses API
docs first. The Anthropic path is more battle-tested territory.

## The systems

All three share the same six attributes (Oration, Intellect, Resolve, Presence, Cunning,
Physique, scored 1-20, modifier `floor((score-10)/2)`), the same proficiency/tier/XP math, and the
same tool-call protocol (`request_check` / `propose_state_update` / `propose_level_up`) - see
`systems/_shared/core.py`. What differs per system is skills, a **curated feat catalog** (feats are
chosen by `feat_id` from a fixed list, gated by level and sometimes a prerequisite skill - never
freeform invention), a curated liability catalog (some liabilities carry a code-enforced
**auto_trigger**, e.g. Arrogance/Pride of Lineage/Overweening Ambition all add an automatic enmity
pip on a failed Cunning/Presence check against a named rival), and resource categories.

- **Red Admiral** (`systems/red_admiral/`) - revolutionary-era politics. 11 resource categories
  (mass influence, party standing, Lenin's trust, ...), 14 feats, 6 liabilities.
- **Monarch** (`systems/monarch/`) - feudal/medieval court, faith, and war. 8-9 resource categories
  per setting, 14 feats, 7 liabilities.
- **Apollo** (`systems/apollo/`) - Roman Republic politics. 9 resource categories, 14 feats, 6
  liabilities.

See each system's `rules.md` for the complete rulebook and its `mechanics.py` for the actual
catalogs.

## Character creation

Custom characters (built via the webapp's "Create Custom Character") use a fixed, balanced budget
so they're comparable to (if a notch below) a hand-authored premade signature character: the
standard array `[16, 14, 13, 12, 10, 8]` assigned across the six attributes, a 14-point skill
budget (Proficiency = 1 point, Expertise = 2 points total, max 3 starting Expertise skills), one
level-1-eligible starting feat, and up to 2 starting liabilities. See
`systems/_shared/core.validate_and_build_character`.

## Status

- [x] Three systems (Red Admiral, Monarch, Apollo) sharing one validated engine core, each with a
      curated feat/liability catalog (no more freeform "invent a perk")
- [x] Four settings (Petrograd 1917, Britannia 876, Rome 59 BCE, Crusades 1097), each with a
      premade signature character, full NPC stat blocks, factions, canon timeline
- [x] Generic character creation (standard array, skill budget, feat/liability selection),
      validated server-side, usable on any system
- [x] SQLite persistence (`db.py`): local auto-profiles + optional Discord login, many characters
      per account across settings
- [x] Flask webapp: setting → gameplay mode → character select/create → game, live sidebar,
      per-turn mechanics panel
- [x] Deployment-ready: `requirements.txt` + `Procfile`, tested booting under gunicorn exactly as
      Render will run it
- [x] Anthropic adapter - structurally verified plus full mocked integration tests (check → state
      update incl. XP/leveling → check targeting an NPC → auto-liability trigger → narration) for
      every system, run against the real engine code path
- [x] OpenAI adapter - same structural + mocked-integration verification; **not yet run against
      the live API**
- [x] Flask routes (`/api/settings`, `/api/character_creation_schema`, `/api/characters`,
      `/api/character/<id>/state|turn|opening_scene`, `/api/me`, Discord auth routes) tested,
      including a live-route turn test through the real DB with a mocked provider
- [ ] **Real playtesting against live APIs** - the actual next step
- [x] Persistent storage - hosted on the user's own machine as of 2026-08-21 (see "Hosting" above),
      SQLite persisting on a normal local disk instead of Render's ephemeral one
- [ ] Real prerequisite enforcement for contingent canon events (currently only checks the event
      is still `pending`, not the conditions in each setting's `canon_timeline.md`)
- [ ] Conversation history summarization once a session runs long (currently unbounded per
      provider conversation)
- [ ] Local-profile <-> Discord-account linking (currently separate, unmerged identities)
- [ ] "Custom" preset (build-your-own setting) and gameplay modes 1 (Freeform AI) and 2
      (Attributes & Feats) - still greyed out in the start menu, only "Full System" is wired up

See `ROADMAP.md` for the longer-term plan.
