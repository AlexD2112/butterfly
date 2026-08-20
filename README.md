# Butterfly — AI-Narrated Historical RPGs

An LLM-driven RPG engine, separate from the Dendrynexus game in the rest of this repo. Design
philosophy: the model narrates, roleplays NPCs, and makes judgment calls; the application rolls
dice and validates every mechanical state change - nothing that compounds over a campaign (XP,
leveling, resource clamping, feat/liability eligibility) is left to the model's discretion.

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
- **`server.py`** — the webapp (browser UI): start menu (Preset → setting → gameplay mode →
  character select/creation) → game. Accounts are automatic (a local profile per browser via a
  session cookie) with optional "Login with Discord". This is the primary way to play.
- **`loop.py`** — a terminal CLI for scripting/quick testing, always playing a chosen setting's
  premade character to a single legacy save file (no accounts/multiple characters).

## Layout

```
ai_rpg/
  core_rules.md                    - original v1 rulebook (superseded, kept for reference)
  engine.py                        - SETTING_REGISTRY, SYSTEM_REGISTRY, prompt building, the
                                      provider-agnostic turn loop - shared by loop.py and server.py
  providers.py                     - Anthropic + OpenAI adapters, MODEL_REGISTRY
  db.py                            - SQLite persistence: users, characters, saves
  discord_oauth.py                 - minimal Discord OAuth2 (login only, no token storage)
  server.py                        - Flask webapp (routes, sessions, gunicorn entry point)
  loop.py                          - terminal CLI
  static/index.html                - the webapp frontend (single file, no build step)
  requirements.txt, Procfile       - deployment (see "Deploying" below)
  systems/
    _shared/core.py                - shared dice/leveling/validation logic, used by every system
    red_admiral/, monarch/, apollo/  - each: rules.md + mechanics.py (skills/feats/liabilities)
  settings/
    petrograd_1917/, britannia_876/, rome_59_bce/, crusades_1097/  - each: character_sheet.json
    (docs), new_campaign_state.json (real starting state), npcs.md, factions.md,
    canon_timeline.md, opening_scene.md, resource_schema.json
```

## Setup

```
pip install -r requirements.txt
```

You only strictly need the key for whichever model(s) you'll actually use - the model dropdown
(webapp) or CLI startup will tell you if a key's missing rather than crashing silently.

```
cp .env.example .env
```
then open `ai_rpg/.env` and fill in real values - see the comments in `.env.example` for what each
one does (LLM provider keys, `FLASK_SECRET_KEY`, optional Discord OAuth settings). `ai_rpg/.env` is
gitignored and will never be committed. **Never put real secrets anywhere else in this codebase** -
not in `providers.py`, not in any `.py` file. If you ever see a real key sitting in source, treat
it as compromised and rotate it immediately, whether or not it got committed.

## Running the webapp locally

```
cd ai_rpg
python server.py
```
Open `http://127.0.0.1:5000`. Every visitor gets a local profile automatically (no login needed);
"Login with Discord" appears once Discord OAuth is configured (see below). Pick Preset → a setting
→ Full System → a character (the setting's premade figure, one of your existing characters, or
"Create Custom Character"), then play. Each turn shows a collapsible "Mechanics" panel with the
actual dice rolls and state deltas. Progress autosaves to `ai_rpg/butterfly.db` after every turn.

## Running the CLI

```
cd ai_rpg
python loop.py                              # Petrograd 1917, default model (GPT-5.6 Luna)
python loop.py haiku                        # force a model - keys: luna, sonnet, haiku, opus
AI_RPG_MODEL=sonnet python loop.py           # same, via env var
AI_RPG_SETTING=rome_59_bce python loop.py    # play a different setting's premade character
```
Uses a single legacy save file (`ai_rpg/save.json`), always the chosen setting's premade character
- no accounts, no custom characters. For those, use the webapp.

## Deploying (Render, free tier, no domain needed)

This gets you a real `https://<something>.onrender.com` URL anyone can open, without buying a
domain or paying anything.

1. **Push this repo to GitHub** if it isn't already (this repo already has a GitHub remote -
   `git push` your current branch).
2. **Create a Render account** at render.com (free, GitHub login works) and click **New → Web
   Service**, connect this GitHub repo.
3. Set the **Root Directory** to `ai_rpg` (Render supports deploying a subfolder of a larger repo).
4. Render should auto-detect Python. Confirm: **Build Command** `pip install -r requirements.txt`,
   **Start Command** `gunicorn server:app` (this matches the included `Procfile`, so Render may
   fill it in automatically).
5. Under **Environment**, add each variable from `.env.example` as a real environment variable
   (not a `.env` file - Render doesn't read that): `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`,
   `FLASK_SECRET_KEY` (generate one: `python -c "import secrets; print(secrets.token_hex(32))"`),
   and the three `DISCORD_*` variables once you've done the Discord setup below (optional - skip
   them and the app still runs fine with local-only profiles).
6. Deploy. Render gives you a URL like `https://butterfly-xyz.onrender.com`.

**Known limitation to know about going in:** Render's free web services don't get a persistent
disk, so `butterfly.db` (accounts and characters) resets on every redeploy or restart - fine for a
demo tonight, not fine for anything you want to keep long-term. When you're ready to fix that,
swap SQLite for a small hosted Postgres (Render's own, or Supabase's free tier) - `db.py` is a thin
enough wrapper that this is a contained change, not a rewrite. See `ROADMAP.md`.

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

`providers.MODEL_REGISTRY` currently has four options, GPT-5.6 Luna (OpenAI) as default:

| key      | provider  | model                        |
|----------|-----------|------------------------------|
| `luna`   | OpenAI    | `gpt-5.6-luna` (default)     |
| `sonnet` | Anthropic | `claude-sonnet-5`            |
| `haiku`  | Anthropic | `claude-haiku-4-5-20251001`  |
| `opus`   | Anthropic | `claude-opus-5`              |

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
- [ ] Persistent storage once actually hosted long-term (swap SQLite → Postgres - see "Deploying")
- [ ] Real prerequisite enforcement for contingent canon events (currently only checks the event
      is still `pending`, not the conditions in each setting's `canon_timeline.md`)
- [ ] Conversation history summarization once a session runs long (currently unbounded per
      provider conversation)
- [ ] Local-profile <-> Discord-account linking (currently separate, unmerged identities)
- [ ] "Custom" preset (build-your-own setting) and gameplay modes 1 (Freeform AI) and 2
      (Attributes & Feats) - still greyed out in the start menu, only "Full System" is wired up

See `ROADMAP.md` for the longer-term plan.
