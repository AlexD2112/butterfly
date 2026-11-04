"""
Council Mode: an always-on narration-quality pass layered on top of the normal turn loop. This
module never touches mechanics - dice, state updates, and canon events are always decided exactly
once, by the normal engine.run_turn tool-calling loop, before anything here runs. Council Mode only
ever rewrites the PROSE describing an already-decided outcome; it cannot change what happened, only
how it's told.

Runs on EVERY turn (as of 2026-08-20 - previously an opt-in, stakes-gated feature; see NOTES.md for
that history and the cost tradeoff this change implies). The only thing that varies per turn is the
SIZE of the judge panel:

1. VOICE_GUIDANCE (always on) - a banned-word list and anti-purple-prose instruction folded into
   every turn's base system prompt (see engine.build_system_prompt). Free, no extra LLM calls.

2. build_context_summary(state, debug_events) - a free, rule-based (no LLM call) one-line summary
   of where the campaign is and what just happened, cheap input for the router below. "Dynamically
   generated" doesn't have to mean another paid call - this is built directly from state already in
   memory, at zero extra cost.

3. run_council(...) - always invoked, every turn:
     a cheap router call picks which judges review THIS turn (MIN_JUDGES=3 floor, up to all 6 for
     more consequential turns - see _route_judges/_select_judge_roles)
       -> independent candidate generation (baseline narration + one fresh alternative)
       -> the routed judges, each scoring every candidate against one specific rubric (structured
          tool output, cheap model - narrow checklist evaluation doesn't need a strong model)
       -> a chairman that SYNTHESIZES a final narration from all candidates + all judge output
          (not just picks the top score), and is asked to surface disagreement rather than hide it
       -> a cheap-model voice-editor pass: word choice/rhythm only, explicitly forbidden from
          touching content.
   Every LLM call in this pipeline goes through the same provider.call() + usage_sink/budget_ok
   accounting as the normal turn loop (see engine.run_turn), so council spend is captured by the
   existing per-account budget ledger automatically, and a mid-pipeline budget exhaustion just
   aborts back to the baseline narration rather than failing the whole turn - the player always
   gets a narration, worst case it's the single-pass one with no council review.

   As of 2026-08-21, council's judges/router/voice-editor always run on a fixed cheap model
   (JUDGE_MODEL_KEY = "luna" - see _cheap_model_key_for), regardless of what model the player picked
   for their own turn. This reverses the 2026-08-20 design (judges followed the player's own model
   choice), which made sense ONLY while Luna and the free local-ollama option were the sole
   selectable models - neither had a cheaper same-provider fallback to reach for, so "follow the
   player" and "pin to a fixed cheap model" were the same thing in practice. That stopped being safe
   once Sonnet/Haiku/Opus became selectable again the same day: with a real spread of model tiers now
   selectable, "follow the player" would mean an always-on, minimum-3-judge council silently running
   ~7-9 internal calls on whatever the player picked for narration - including Opus - which is
   exactly the runaway-cost failure mode a fixed cheap judge model exists to prevent. Luna remains the
   DEFAULT model a new account opens with (briefly changed to Sonnet the same day, then reverted per
   direction - see NOTES.md), so pinning judges to Luna specifically (not Haiku, which briefly held
   this role and is actually pricier than Luna) means the common default-path turn's cost is
   unchanged from before Sonnet/Opus became selectable at all - the fix only matters for a player who
   deliberately opts into a pricier model. The candidate/chairman calls are unaffected - they still
   use the player's own model directly, so narration quality still scales with the player's choice,
   only the narrow rubric-scoring work doesn't. Local-ollama is unaffected too (Council Mode is
   skipped for it entirely - see SKIP_COUNCIL_FOR_MODEL_KEYS below - so a player choosing the free
   local model still gets a genuinely free turn). There is no "escalate to a stronger model for
   high-stakes turns" path right now; the router's only job is choosing HOW MANY/WHICH judges review
   a turn, not which model tier runs them. See NOTES.md for the real cost math behind this change.

Design rationale (why this maps to real technique, not just "have some models argue"):
  - The overall independent-generation -> blind cross-ranking -> chairman-synthesis shape follows
    the "LLM council" pattern (multiple independent attempts, judged, then synthesized rather than
    voted on) rather than a single model self-critiquing its own output.
  - The "what should go right" / "what should go wrong" judge pair combines two separate,
    established techniques: the "premortem" (imagine the decision has already failed, work
    backward to find the risk) from decision science, and the "anticipatory reflection" pattern
    (an agent asks itself what could go wrong / what's the alternative before committing to an
    action) explored in recent LLM-agent research. They're deliberately not redundant with the
    groundedness judge - groundedness checks prose quality (purple prose, hand-waving); this pair
    argues for/against the specific consequences the narration proposes.
  - Judges each get a genuinely different rubric/lens (not just "disagree with the other judge"),
    since debate/multi-agent literature suggests the benefit comes from genuinely different framing
    per agent, not from mere disagreement between agents given the same framing.
  - The router itself is the standard cheap-triage pattern: one small call decides scope before any
    expensive work happens, rather than always paying for the maximum possible panel.
"""

import json

import providers

# Rewritten 2026-11-02. The old list caught a handful of words; the narration still read like a
# chatbot because the tells are mostly structural. This version folds in two outside sources:
#   - the "humanizer" Claude skill (github.com/blader/humanizer), itself built on Wikipedia's
#     "Signs of AI writing" guide - its content/language/style pattern list, adapted to fiction;
#   - common roleplay/game-master prompt practice (e.g. the widely shared "TGIF" base prompt on
#     rentry.co/TGIFbaseprompt): never write the player's lines, every reply changes something, end
#     on a pull forward, NPCs with their own goals, no poetic summaries or omniscient foreshadowing.
BANNED_WORDS = [
    "delve", "delving", "tapestry", "boundless", "testament", "realm", "meticulous",
    "meticulously", "underscore", "underscores", "intricate", "intricately", "elevate",
    "elevates", "vibrant", "bustling", "whispered secrets", "unwavering", "steadfast",
    "in the annals of", "a testament to", "palpable", "electric", "the weight of history",
    "hangs in the air", "thick with tension", "a mix of", "something shifts", "a flicker of",
    "you can't help but", "little do you know", "pivotal", "crucial", "echoes through",
    "a reminder that", "symbolizing", "showcasing", "navigate", "landscape", "resonate",
    "a beat", "the stakes", "indelible", "unspoken", "the gravity of",
]

VOICE_GUIDANCE = f"""
# HOW TO SOUND (read this twice - it matters more than anything above about prose)
You are a game master running a table for one player, not an author writing a novel and not an
assistant answering a request. Sound like a sharp, well-read human GM who knows this period cold.

Shape of a reply:
- Usually 80-180 words. A big set piece (a vote, a battle, a ruler's return) may run to ~300.
  Never pad. If nothing much happens, say so in two lines and move on.
- Put the weight in people. Most replies should have someone talking: a named person with an
  agenda who interrupts, deflects, jokes, lies, gets facts wrong, or wants something from the
  player right now. Give recurring people a verbal fingerprint (one clipped, sarcastic, giving orders;
  another reasonable and hedging; another precise and procedural; soldiers blunt, crude, local).
  Ordinary people (a ferryman, a sentry, a scribe) talk like ordinary people.
- Dialogue in straight double quotes. A new paragraph for each speaker. No stage-play format.
- Concrete period texture: prices, the news and who is spreading it, the roads, the weather, what people are
  eating or not eating. One sharp detail beats three general ones.
- Every reply changes something - information, a relationship, the clock, a threat. No reply
  should leave the situation exactly where it was.
- End on something the player has to answer: a person waiting for a reply, a door opening, a
  messenger, a choice that won't wait. Never end with "What do you do?" or any question to the
  player out of character, and never with a reflective summary line.

Conversations are played, not resolved (2026-11-03):
- Talk is free. Don't roll on every line of dialogue - most lines just get an answer in character.
  Roll when the player makes a real attempt to move someone (an ask, an offer, a threat, a lie)
  and the outcome is uncertain, and roll on the player's actual offer, not their opening line.
- People don't fold. A single good roll gets a person to listen, name their terms or soften - it
  never settles an alliance, a bloc's votes or a change of sides on its own. Make them want
  something concrete, push back, and leave room for the player to make the full offer. When the
  player is after a real commitment, open_negotiation with what the person wants and run each
  offer through negotiation_offer - the application keeps the score.
- Don't end a conversation the player is still in. No convenient bell, messenger or exit to wrap a
  scene up unless the clock really forces it (a vote being called that the player chose to attend
  counts). End with the other person waiting on the player's next word.

The player's character belongs to the player:
- Never write what the player character says, decides, thinks or feels beyond what the player
  wrote. You narrate the world and everyone else in it. If the player wrote "I give a speech",
  you may describe how the speech lands, quote a line only if the player supplied it, and show the
  room's response.

Mechanics stay out of the prose:
- Never mention dice, rolls, checks, DCs, degrees, resource names or numbers in the narration -
  the screen shows those. The dice speak through what happens: a "success at a cost" is a real
  cost shown in the fiction, never the phrase itself.

Patterns that make text read as machine-written - do not use them:
- These words/phrases: {", ".join(BANNED_WORDS)}.
- Significance inflation: telling the player a moment is historic, momentous or a turning point.
- Trailing "-ing" commentary clauses ("..., a sign of how far things had come").
- "Not just X, but Y" / "It's not X, it's Y", in any form.
- Lists of three for rhythm ("the cold, the hunger, the fear"). Use the natural number of things.
- Synonym cycling (a name / 'the old man' / 'the leader' / 'the theorist' in one paragraph). Repeat the name.
- More than one em dash per reply. Use commas and full stops.
- Declaring emotions ("a surge of pride", "dread settles"). Show what someone does or says.
- Poetic closing lines, aphorisms, or summaries of what the scene "means".
- Omniscient foreshadowing ("history will remember...", "though you can't know it yet...").
- Markdown of any kind: no headers, bold, bullets or lists in the narration.
- Assistant habits: no "Certainly", no recaps of the player's request, no offers of options in the
  prose (use suggest_actions for that).
"""


# Model keys Council Mode is skipped for entirely (engine.run_turn checks this before ever calling
# run_council - see there). Added 2026-08-21 for local-ollama specifically: real unmocked testing
# found Qwen3 burns 1000+ reasoning tokens even on short structured judge output, and at this
# hardware's measured ~9-29 tok/s, a full ~8-11-call council turn (router + candidate + 3-6 judges +
# chairman + voice editor, on top of the baseline generation) realistically runs 6-24 MINUTES end to
# end - confirmed against a real hung-looking turn on the live site, not just estimated. That's both
# a broken player experience and, in the worse cases, longer than gunicorn's 900s timeout, so the
# turn could fail anyway after all that wait. Mechanics are completely unaffected either way (Council
# Mode never touches dice/state - see this module's docstring), so skipping it here only means this
# provider's narration doesn't get the extra judge-panel pass, not that anything about the game
# itself is degraded. See NOTES.md for the full reasoning and the alternative considered (capping
# output tokens instead - rejected: a reasoning model needs room to finish its <think> block AND
# answer, so a tight-enough cap to bound latency risks cutting generation off mid-thought, before it
# ever reaches its actual answer or tool call, which is worse than not running the panel at all).
SKIP_COUNCIL_FOR_MODEL_KEYS = {"local-ollama"}


def detect_stakes_signals(debug_events):
    """Free, rule-based (no LLM call) - inspects a turn's already-resolved debug_events for concrete
    stakes signals (resource label-band crossings, canon events, liabilities gained, critical rolls).

    Previously this was should_convene_council(), a yes/no gate deciding whether the council ran at
    all - as of 2026-08-20 the council runs on every turn regardless, so there's no gate left to be.
    Renamed and repurposed as a signal DETECTOR: its output now feeds build_context_summary() below,
    giving the judge-count router genuinely useful "why might this turn matter" context instead of
    deciding anything itself.
    """
    reasons = []
    for ev in debug_events:
        if ev.get("type") == "check" and ev.get("degree") in ("critical_success", "critical_failure"):
            reasons.append(f"critical {ev['degree'].replace('_', ' ')} on {ev.get('attribute')}")
        # Kept for defensiveness/forward-compat, but engine.run_turn never actually produces this
        # shape - real canon events arrive nested inside a state_update's `changes` (kind ==
        # "canon_event"), handled in the loop below. Found and fixed 2026-08-21: this top-level
        # check was dead code since the day it was written, meaning canon events were NEVER
        # detected as stakes signals for Council Mode's judge-router the entire time it's existed -
        # see NOTES.md.
        if ev.get("type") == "canon_event":
            reasons.append(f"canon event fired: {ev.get('key')}")
        if ev.get("type") in ("state_update", "level_up"):
            for c in ev.get("changes", []):
                if c.get("kind") == "resource":
                    before_label, after_label = c.get("before_label"), c.get("after_label")
                    if before_label != after_label:
                        # Any label-band crossing is notable; crossing into either extreme band is
                        # always high-stakes even if it's not the first crossing this campaign.
                        if after_label in ("Suspect/Negative", "Dominant") or before_label in ("Suspect/Negative", "Dominant"):
                            reasons.append(f"{c['key']} crossed into {after_label} (from {before_label})")
                        else:
                            reasons.append(f"{c['key']} shifted from {before_label} to {after_label}")
                elif c.get("kind") == "liability_gained":
                    reasons.append(f"liability gained: {c.get('name')}")
                elif c.get("kind") == "canon_event":
                    reasons.append(f"canon event fired: {c.get('key')}")
    return reasons


def build_context_summary(state, debug_events):
    """Free, rule-based (no LLM call) - a short line of context for the judge-count router, cheaper
    than handing it the full system prompt/state digest. Built entirely from state and this turn's
    own debug_events, already in memory - "dynamically generated" here means "assembled from what we
    already know," not "worth paying for another LLM call to produce.\""""
    parts = [f"Turn {state.get('turn_number')}, {state.get('date', '')}"]
    log = state.get("campaign_log_summary") or []
    if log:
        parts.append(f"Last turn: {log[-1].get('summary', '')[:200]}")
    signals = detect_stakes_signals(debug_events)
    if signals:
        parts.append("This turn's mechanical signals: " + "; ".join(signals))
    else:
        parts.append("This turn's mechanical signals: none notable (routine turn)")
    return " | ".join(parts)


MIN_JUDGES = 3  # every turn's floor, regardless of how routine it looks - see run_council


# Each judge gets ONE specific rubric and nothing else - the point of a panel is genuinely different
# lenses, not several copies of the same generic "is this narration good?" question asked in
# different words. Advocate/premortem are deliberately not redundant with groundedness: groundedness
# judges prose quality (purple prose, hand-waving), advocate/premortem argue for/against the
# SPECIFIC consequences the narration proposes - two different questions.
JUDGE_ROLES = [
    {
        "key": "rules_mechanics",
        "rubric": (
            "RULES/MECHANICS: Does the narration accurately reflect the check result and state changes "
            "in the mechanical facts below? Flag anything that contradicts, ignores, or invents an "
            "outcome beyond what's listed. A narration that's mechanically accurate but dull should "
            "still score reasonably here - this rubric is ONLY about consistency with the facts, not "
            "prose quality."
        ),
    },
    {
        "key": "canon_continuity",
        "rubric": (
            "CANON/CONTINUITY: Does the narration respect the setting's locked historical timeline and "
            "the campaign's divergence log so far? Flag anachronisms, contradictions with already-fired "
            "canon events, or claims that quietly ignore established campaign history."
        ),
    },
    {
        "key": "characterization",
        "rubric": (
            "CHARACTERIZATION: Do NPCs speak and act consistent with their established personality (see "
            "the setting context in the mechanical facts / system prompt), or do they read as generic, "
            "interchangeable AI-voiced characters regardless of who's actually speaking?"
        ),
    },
    {
        "key": "groundedness",
        "rubric": (
            "GROUNDEDNESS: Penalize purple prose, unjustified narrative leaps, and hand-waving. Reward "
            "concrete, period-specific, sensory, plausible detail. This is about prose quality "
            "specifically, independent of plot or mechanics."
        ),
    },
    {
        "key": "advocate",
        "rubric": (
            "WHAT SHOULD GO RIGHT (advocate): Argue for what this narration's proposed consequences get "
            "right for the player, or what real opportunity it opens that a flatter, safer narration "
            "would have missed. Score higher when the narration takes a genuine, earned upside rather "
            "than playing it safe."
        ),
    },
    {
        "key": "premortem",
        "rubric": (
            "WHAT SHOULD GO WRONG (premortem): Imagine this narration's consequences have already led to "
            "real trouble for the player, days later in-fiction. Working backward from that failure, "
            "what risk, complication, or cost is this narration failing to surface or quietly glossing "
            "over? Score higher when the narration is honest about real risk rather than only showing "
            "upside."
        ),
    },
]


def _ok(budget_ok):
    """budget_ok=None means unlimited (matches engine.run_turn's own top-level convention, used by
    loop.py's CLI path which has no budget concept at all) - everywhere else, defer to the callable."""
    return budget_ok is None or budget_ok()


# Luna, not Haiku - deliberately reconsidered same-day (2026-08-21): DEFAULT_MODEL_KEY briefly moved
# to "sonnet" and JUDGE_MODEL_KEY was set to "haiku" to match, then the default was reverted back to
# Luna. Haiku ($1.00/$5.00 per 1M) is NOT actually cheap relative to Luna ($0.20/$1.20 per 1M) - it
# was only "the cheap option" relative to Sonnet/Opus. With Luna back as the default, pinning judges
# to Haiku would have been a real cost REGRESSION for the common case (every default-path turn paying
# Haiku rates for ~7-9 calls that used to run on Luna, the cheapest model in the whole registry) for
# no upside. Luna is both the default model AND the cheapest one available, so it's the correct fixed
# target - see NOTES.md for the full math this reasoning is based on.
JUDGE_MODEL_KEY = "luna"


def _cheap_model_key_for():
    """Judges/router/voice-editor always run on JUDGE_MODEL_KEY (Luna) - as of 2026-08-21, no longer
    derived from the player's own model choice. NOTES.md's 2026-08-20 entry deliberately picked
    "follow the player's choice" specifically because only Luna and the free local-ollama option were
    selectable then - neither had a cheaper same-provider fallback to reach for (Luna was already the
    cheap default; local-ollama was already free), so following the player's choice and pinning to a
    fixed model amounted to the same thing in practice. That assumption breaks the moment a player
    manually picks Sonnet/Haiku/Opus (all selectable again as of 2026-08-21, though Luna stays the
    default a new account opens with) - "follow the player's choice" would mean council's ~7-9
    internal router/judge/voice-editor calls silently run on whatever the player picked for
    narration, including Opus, which is exactly the runaway-cost problem an always-on, minimum-3-
    judge council creates if left unfixed. Pinning to Luna specifically (not Haiku - see the comment
    above JUDGE_MODEL_KEY) means the common default-path turn sees no cost change at all versus
    before Sonnet/Opus became selectable, while a player who opts into a pricier model for narration
    still gets cost-contained judging rather than paying that model's rate ~7-9 extra times per turn.
    Local-ollama is unaffected either way - Council Mode is skipped entirely for it (see
    SKIP_COUNCIL_FOR_MODEL_KEYS above), so this function is never reached on that path. The
    candidate/chairman calls still use the player's own model_key directly in run_council below, not
    this function - only the narrow rubric-style judging work moves to a fixed cheap model, not
    narration itself. See NOTES.md for the real cost math this is based on."""
    return JUDGE_MODEL_KEY


def _route_judges(user_input, context_summary, provider, model, usage_sink, budget_ok):
    """One cheap call: given the judge roster and a short context summary, decide which judges
    review THIS turn. Returns the raw {"judge_keys": [...], "reasoning": ...} dict, or None if the
    call couldn't be made/didn't return structured output - _select_judge_roles handles turning
    that into a valid, floor-respecting judge list either way."""
    if not _ok(budget_ok):
        return None
    roster_text = "\n".join(f"- {r['key']}: {r['rubric']}" for r in JUDGE_ROLES)
    system_prompt = f"""You are a fast triage router for a narration review council on a historical
RPG. Every turn gets reviewed by at least {MIN_JUDGES} judges - your job is to pick WHICH ones are
most relevant to this specific turn, and to pick MORE than {MIN_JUDGES} (up to all {len(JUDGE_ROLES)})
when this turn is unusually consequential (a critical roll, a major state shift, a canon event, a
pivotal NPC interaction) rather than routine. Most turns should just get the {MIN_JUDGES} most
relevant judges - don't pad with irrelevant ones for their own sake.

# AVAILABLE JUDGES
{roster_text}

# PLAYER'S MESSAGE THIS TURN
{user_input}

# CONTEXT
{context_summary}

Submit your selection via select_judges.
"""
    tool_specs = [
        {
            "name": "select_judges",
            "description": "Select which judges review this turn's narration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "judge_keys": {
                        "type": "array",
                        "items": {"type": "string", "enum": [r["key"] for r in JUDGE_ROLES]},
                        "description": f"At least {MIN_JUDGES} judge keys; more for higher-stakes turns.",
                    },
                    "reasoning": {"type": "string", "description": "1 sentence on why this set."},
                },
                "required": ["judge_keys", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Select the judge panel for this turn.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="select_judges")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return None
    return result.tool_calls[0]["input"]


def _select_judge_roles(user_input, context_summary, provider, model, usage_sink, budget_ok):
    """Turns the router's (possibly missing/malformed) output into a valid judge role list: at
    least MIN_JUDGES, no duplicates, no unknown keys, order preserved from the router's own
    preference where given. Deterministically pads from JUDGE_ROLES' fixed order when the router
    under-delivers or fails outright - the floor is enforced here in code, not trusted to the model.
    Returns (roles: list[dict], router_reasoning: str|None).
    """
    routed = _route_judges(user_input, context_summary, provider, model, usage_sink, budget_ok)
    valid_keys_in_order = [r["key"] for r in JUDGE_ROLES]
    keys = []
    reasoning = None
    if routed:
        seen = set()
        for k in routed.get("judge_keys") or []:
            if k in valid_keys_in_order and k not in seen:
                keys.append(k)
                seen.add(k)
        reasoning = routed.get("reasoning")
    if len(keys) < MIN_JUDGES:
        for k in valid_keys_in_order:
            if len(keys) >= MIN_JUDGES:
                break
            if k not in keys:
                keys.append(k)
    roles_by_key = {r["key"]: r for r in JUDGE_ROLES}
    return [roles_by_key[k] for k in keys], reasoning


def _format_change(c):
    kind = c.get("kind")
    if kind == "resource":
        return f"Resource {c['key']}: {c['before']} ({c['before_label']}) -> {c['after']} ({c['after_label']})"
    if kind == "relationship":
        return f"Relationship {c['key']} trust: {c['before']} -> {c['after']}"
    if kind in ("enmity", "enmity_auto"):
        return f"Enmity vs {c['key']}: {c['before']} -> {c['after']} pips"
    if kind == "liability_gained":
        return f"Liability gained: {c.get('name')}"
    if kind == "liability_removed":
        return f"Liability removed: {c.get('id')}"
    if kind == "xp":
        return f"XP awarded: {c.get('applied')}"
    if kind == "level_up":
        return f"LEVEL UP to {c.get('new_level')}"
    if kind == "feat_gained":
        return f"Feat gained: {c.get('name')}"
    if kind == "attribute_improvement":
        return f"Attribute {c.get('attribute')}: {c['before']} -> {c['after']}"
    if kind == "skill_upgrade":
        return f"Skill {c.get('skill')}: {c['before']} -> {c['after']}"
    if kind == "divergence":
        return f"Divergence logged: {c.get('note')}"
    if kind == "flag":
        return f"Flag {c.get('key')} = {c.get('value')}"
    if kind == "commitment":
        return f"Commitment '{c.get('key')}' set"
    return f"{kind}: {json.dumps(c)}"


def _format_mechanical_facts(debug_events):
    """Plain-text rendering of the turn's already-decided mechanical outcome, for candidate/judge/
    chairman prompts - the server-side equivalent of the frontend's Mechanics panel, but meant for
    an LLM to read as ground truth rather than a person to read as a debug log."""
    lines = []
    for ev in debug_events:
        if ev.get("type") == "check":
            skill_part = f" ({ev['skill']}, {ev['tier']})" if ev.get("skill") else ""
            target_part = f", targeting {ev['target_npc']}" if ev.get("target_npc") else ""
            lines.append(
                f"- Check: {ev['attribute']}{skill_part} vs DC {ev['dc']} -> rolled {ev['roll']} "
                f"(modifier {ev['modifier']:+d}) = total {ev['total']}, result: {ev['degree'].upper()}"
                f"{target_part}. Attempted: {ev.get('description', '')}"
            )
        elif ev.get("type") in ("state_update", "level_up"):
            for c in ev.get("changes", []):
                lines.append(f"- {_format_change(c)}")
        elif ev.get("type") == "canon_event":
            lines.append(f"- Canon event fired: {ev.get('key')}")
    return "\n".join(lines) if lines else "(no mechanical changes this turn)"


def _generate_alternative_candidate(provider, model, system_prompt, user_input, mechanical_facts, usage_sink, budget_ok):
    """An independent second narration of the SAME already-decided outcome - genuinely independent
    (no tools offered, explicitly told the mechanical facts are final) rather than "rewrite the
    first draft," so the judges/chairman have real variety to work with, not one draft twice."""
    if not _ok(budget_ok):
        return None
    narrow_prompt = f"""{system_prompt}

# COUNCIL MODE - INDEPENDENT ALTERNATE NARRATION DRAFT
The mechanical outcome of this turn has ALREADY been decided and is final - do not invent, change,
alter, or add any mechanical fact beyond what's listed below. Do not call any tools. Write ONLY
narration prose describing this outcome, as an independent draft - do not assume any other draft
exists or try to match/differ from one.

# PLAYER'S ACTION THIS TURN
{user_input}

# MECHANICAL FACTS (final, not open to reinterpretation)
{mechanical_facts}
"""
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Write the narration for this turn's already-decided outcome.")
    result = provider.call(model, narrow_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    text = "\n".join(result.text_parts).strip()
    return text or None


def _run_judge(role, candidates, mechanical_facts, judge_provider, judge_model, usage_sink, budget_ok):
    if not _ok(budget_ok):
        return None
    candidates_text = "\n\n".join(f"CANDIDATE {i + 1}:\n{text}" for i, text in enumerate(candidates))
    system_prompt = f"""You are one judge on a narration review council for a historical RPG. Your ONLY
job is the rubric below - do not evaluate anything outside it.

{role['rubric']}

# MECHANICAL FACTS THIS TURN (ground truth)
{mechanical_facts}

# CANDIDATE NARRATIONS TO SCORE
{candidates_text}

Score each candidate 1-10 on your rubric only, then submit via submit_judgment.
"""
    tool_specs = [
        {
            "name": "submit_judgment",
            "description": "Submit your rubric scores for every candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "object",
                        "description": 'One entry per candidate, e.g. {"candidate_1": 7, "candidate_2": 9} - integers 1-10.',
                    },
                    "notes": {
                        "type": "string",
                        "description": "1-3 sentences explaining the scores - specific enough to be useful to the chairman synthesizing the final narration.",
                    },
                },
                "required": ["scores", "notes"],
            },
        }
    ]
    conversation = judge_provider.new_conversation()
    judge_provider.append_user_message(conversation, "Evaluate the candidates now.")
    result = judge_provider.call(judge_model, system_prompt, tool_specs, conversation, force_tool_name="submit_judgment")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return {"role": role["key"], "scores": {}, "notes": "(judge did not return structured output)"}
    judgment = result.tool_calls[0]["input"]
    return {"role": role["key"], "scores": judgment.get("scores", {}), "notes": judgment.get("notes", "")}


def _run_chairman(candidates, judge_results, mechanical_facts, user_input, provider, model, usage_sink, budget_ok):
    if not _ok(budget_ok):
        return None
    candidates_text = "\n\n".join(f"CANDIDATE {i + 1}:\n{text}" for i, text in enumerate(candidates))
    judges_text = "\n".join(f"- {jr['role']}: scores={jr['scores']} - {jr['notes']}" for jr in judge_results)
    system_prompt = f"""You are the chairman of a narration review council for a historical RPG. Several
candidate narrations of the SAME already-decided turn outcome were independently drafted, then
scored by specialist judges (rules/mechanics, canon/continuity, characterization, groundedness, and
an advocate/premortem pair arguing what the narration gets right versus what risk it glosses over).

Your job is to SYNTHESIZE the best final narration - not just copy the highest-scored candidate
verbatim. Draw the strongest elements from each candidate as warranted by the judges' specific
notes. If the judges meaningfully disagreed about what was strong or weak, say so explicitly in
disagreement_notes - do not paper over real disagreement.

# MECHANICAL FACTS THIS TURN (final, cannot be changed)
{mechanical_facts}

# PLAYER'S ACTION
{user_input}

# CANDIDATES
{candidates_text}

# JUDGE SCORES AND NOTES
{judges_text}

Submit your synthesized final narration via submit_synthesis.
"""
    tool_specs = [
        {
            "name": "submit_synthesis",
            "description": "Submit the final synthesized narration for this turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "final_narration": {"type": "string"},
                    "reasoning": {
                        "type": "string",
                        "description": "Brief note on how you synthesized (e.g. which candidate's opening you kept, what you changed based on which judge's note).",
                    },
                    "disagreement_notes": {
                        "type": ["string", "null"],
                        "description": "If judges meaningfully disagreed about what was strong/weak, summarize the disagreement in 1-2 sentences. Null if judges were broadly aligned.",
                    },
                },
                "required": ["final_narration", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Synthesize the final narration now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="submit_synthesis")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return None
    return result.tool_calls[0]["input"]


def _run_voice_editor(narration, judge_provider, judge_model, usage_sink, budget_ok):
    if not _ok(budget_ok):
        return narration
    system_prompt = f"""You are a line editor. Rewrite ONLY word choice and rhythm in the text below -
do not add, remove, or change any plot content, fact, dialogue meaning, or mechanical implication.
The goal is purely to make it sound less AI-generic.
{VOICE_GUIDANCE}

# TEXT TO EDIT
{narration}
"""
    conversation = judge_provider.new_conversation()
    judge_provider.append_user_message(conversation, "Return only the edited text, nothing else - no preamble or commentary.")
    result = judge_provider.call(judge_model, system_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    edited = "\n".join(result.text_parts).strip()
    return edited or narration


def run_council(provider, model, model_key, system_prompt, user_input, state, debug_events, baseline_narration, usage_sink=None, budget_ok=None):
    """Called on EVERY turn as of 2026-08-20 (see module docstring) - there is no gate to check
    before calling this; it always attempts the full pipeline, sized dynamically by the router.

    model_key is no longer used inside this function as of 2026-08-21 (judge/router/voice-editor
    now always target JUDGE_MODEL_KEY, not something derived from it - see _cheap_model_key_for)
    - kept in the signature since engine.py already has it at the call site and it costs nothing to
    keep passing, not because anything here still reads it.

    Returns (final_narration, council_log). council_log is always a dict (never None) suitable for
    appending directly to debug_events, so the frontend's existing Mechanics panel can show what the
    council did (or why it aborted) - this is also the "at minimum logging" fallback for surfacing
    judge disagreement to the player, per the design brief.

    Every stage checks budget before spending on it and gracefully falls back to the best narration
    available so far (never raises) - a mid-pipeline budget exhaustion degrades to the baseline
    narration (or as far as the pipeline got), it never fails the turn outright.
    """
    log = {
        "type": "council",
        "router_reasoning": None,
        "judges_selected": [],
        "candidate_count": 1,
        "judge_results": [],
        "chairman_reasoning": None,
        "disagreement_notes": None,
        "aborted_reason": None,
    }

    if not _ok(budget_ok):
        log["aborted_reason"] = "budget exhausted before the council could start - used baseline narration"
        return baseline_narration, log

    mechanical_facts = _format_mechanical_facts(debug_events)
    context_summary = build_context_summary(state, debug_events)
    candidates = [baseline_narration]

    judge_model_key = _cheap_model_key_for()
    try:
        judge_provider, judge_model = providers.get_provider(judge_model_key)
    except providers.ProviderError:
        # judge/router model's provider isn't configured (missing API key) - fall back to the same
        # provider/model already in use for this turn rather than failing the whole council.
        judge_provider, judge_model = provider, model

    selected_roles, router_reasoning = _select_judge_roles(user_input, context_summary, judge_provider, judge_model, usage_sink, budget_ok)
    log["router_reasoning"] = router_reasoning
    log["judges_selected"] = [r["key"] for r in selected_roles]

    alt = _generate_alternative_candidate(provider, model, system_prompt, user_input, mechanical_facts, usage_sink, budget_ok)
    if alt:
        candidates.append(alt)
    log["candidate_count"] = len(candidates)

    if not _ok(budget_ok):
        log["aborted_reason"] = "budget exhausted before judging - used baseline narration"
        return baseline_narration, log

    judge_results = []
    for role in selected_roles:
        if not _ok(budget_ok):
            log["aborted_reason"] = f"budget exhausted mid-judging (after {len(judge_results)}/{len(selected_roles)} judges) - used baseline narration"
            log["judge_results"] = judge_results
            return baseline_narration, log
        jr = _run_judge(role, candidates, mechanical_facts, judge_provider, judge_model, usage_sink, budget_ok)
        if jr:
            judge_results.append(jr)
    log["judge_results"] = judge_results

    if not judge_results or not _ok(budget_ok):
        log["aborted_reason"] = "no usable judge output or budget exhausted before the chairman - used baseline narration"
        return baseline_narration, log

    synthesis = _run_chairman(candidates, judge_results, mechanical_facts, user_input, provider, model, usage_sink, budget_ok)
    if not synthesis or not synthesis.get("final_narration"):
        log["aborted_reason"] = "chairman failed to return a synthesis - used baseline narration"
        return baseline_narration, log

    final_narration = synthesis["final_narration"]
    log["chairman_reasoning"] = synthesis.get("reasoning")
    log["disagreement_notes"] = synthesis.get("disagreement_notes")

    if _ok(budget_ok):
        final_narration = _run_voice_editor(final_narration, judge_provider, judge_model, usage_sink, budget_ok)
    else:
        log["aborted_reason"] = "voice-editor pass skipped - budget exhausted after the chairman"

    return final_narration, log
