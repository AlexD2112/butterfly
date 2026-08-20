# Butterfly — Long-Term Roadmap

Where this is now, and a phased plan for turning it into a full product if that's the direction you
want to take it. Nothing here is committed - it's a menu, not a schedule.

## Where things stand tonight

Three systems (Red Admiral, Monarch, Apollo) sharing one validated engine core; four playable
settings (Petrograd 1917, Britannia 876, Rome 59 BCE, Crusades 1097); custom character creation;
local SQLite accounts with optional Discord login; deployed to a free public URL. See `README.md`
for the honest status checklist - a few real gaps are called out there (OpenAI's Responses API path
hasn't been exercised against the live API yet; the free hosting tier's storage isn't durable).

## Phase 1 — Make tonight's deploy actually solid (days, not weeks)

- **Live-API playtesting.** Everything's been verified structurally against mocked provider
  responses, but a real campaign turn against the live Anthropic and OpenAI APIs hasn't happened
  yet. Do this first - it's the highest-value, lowest-effort next step, and most likely to surface
  small integration bugs (a parameter name, a response shape) rather than design problems.
- **Durable storage.** Swap SQLite for a small hosted Postgres (Render's own free-for-90-days
  option, or Supabase's free tier) so accounts/characters survive redeploys. `db.py` is a thin
  wrapper specifically so this is a contained change.
- **Conversation length management.** Long campaigns will eventually hit context limits or get
  expensive. Add a summarization pass (compress older turns into `campaign_log_summary` entries,
  which already exist, and stop sending the full raw transcript past some turn count).
- **Basic cost/rate controls.** Once this is a public URL, nothing currently stops someone from
  running up your API bill. At minimum: a simple per-session or per-IP rate limit; longer-term, see
  Phase 4's "usage controls."

## Phase 2 — Content and mode breadth (weeks)

- **Freeform AI and Attributes & Feats modes.** Currently placeholder-disabled in the start menu.
  Freeform AI (no dice, pure narration) is nearly free to build - it's `run_turn` without the
  `request_check`/`propose_state_update` tool loop, just a system prompt and narration. Attributes
  & Feats (checks without the full canon/commitment/enmity apparatus) is a genuine middle design,
  worth scoping properly rather than rushing.
- **More settings on existing systems.** Each new setting is content, not engine work, once a
  system exists for its era - a natural place to expand without new code risk. Candidates: a second
  Red Admiral setting (to actually prove the system/setting split works for someone other than
  Trotsky), a second Monarch setting outside Western Europe.
- **A fourth+ system**, if a genre the existing three don't cover comes up (e.g. an early-modern or
  ancient-Chinese-court system) - same shared-core pattern, new skills/feats/resources.
- **"Custom" setting builder.** The top-level menu already reserves a slot for this. The realistic
  version isn't a full UI world-builder - it's probably an AI-assisted setting generator (describe
  an era/character, get a draft `npcs.md`/`canon_timeline.md`/starting state you can review and
  edit) rather than hand-authoring every field through forms.

## Phase 3 — Social and multiplayer (weeks to months)

- **Shareable character sheets.** A read-only public link to a character's current sheet/log -
  cheap to build (an unauthenticated GET route keyed by a share token), useful for showing off a
  campaign.
- **Discord bot mode.** You already have Discord OAuth wired up for login - a natural next step is
  playing turns directly in a Discord channel via a bot, not just logging into the web app with a
  Discord identity. This is a meaningfully bigger integration (bot hosting, message-based turn
  input) and belongs in its own scoped effort.
- **Co-op/shared campaigns.** Multiple accounts contributing to one shared world state - a real
  design problem (whose action resolves first, how NPCs react to multiple PCs at once), not just an
  engineering one. Don't start this before Phase 1/2 are solid.

## Phase 4 — Platform maturity (as it grows)

- **Account linking.** Local-profile and Discord-linked characters aren't merged today. Fix once
  people are actually hitting this friction, not before.
- **Usage controls.** If this gets real traffic: per-account turn limits, or a "bring your own API
  key" option so you're not the one paying for strangers' campaigns, or both.
- **Content moderation.** These are settings involving war, political violence, and real historical
  atrocity - worth a deliberate pass on what the GM should and shouldn't narrate in graphic detail,
  independent of Anthropic's/OpenAI's own model-level guardrails, once this is more than a handful
  of trusted players.
- **Mobile-friendly layout.** The current single-file frontend is desktop-oriented (fixed sidebar
  grid); a real mobile pass is a CSS/layout project, not an architecture one.
- **Backups/export.** A "download my campaign as JSON" button is cheap and worth having before this
  holds anything anyone would be upset to lose.

## Phase 5 — Sustainability (only if this grows beyond friends-and-you)

Not urgent, but worth deciding early enough that it shapes Phase 4's usage-control design:
free-and-rate-limited, donation-supported, "bring your own key," or a paid tier. Each implies
different engineering (usage tracking, billing integration, key management UI) - better to pick a
direction before Phase 4's usage controls are built than to retrofit it later.

## Deliberately not on this roadmap

- A general-purpose "any era, any character" freeform mode with no system underneath - that's a
  different, simpler product (closer to a plain chatbot GM) and would compete with, not extend,
  the actual design bet here: that code-owned mechanics make a long campaign more trustworthy than
  pure LLM narration alone.
- Native mobile apps - a responsive web layout covers this need at far lower cost until there's a
  concrete reason (push notifications, offline play) that a browser genuinely can't deliver.
