"""
Council Mode: an opt-in, stakes-gated narration-quality pass layered on top of the normal turn
loop. This module never touches mechanics - dice, state updates, and canon events are always
decided exactly once, by the normal engine.run_turn tool-calling loop, before anything here runs.
Council Mode only ever rewrites the PROSE describing an already-decided outcome; it cannot change
what happened, only how it's told.

Three independent pieces, usable separately:

1. VOICE_GUIDANCE (always on, regardless of Council Mode) - a banned-word list and anti-purple-prose
   instruction folded into every turn's base system prompt (see engine.build_system_prompt). Free,
   no extra LLM calls, applies to every turn whether or not Council Mode is enabled.

2. should_convene_council(debug_events) - a free, rule-based gate (no LLM call) that inspects a
   turn's already-resolved debug_events and decides whether this turn's stakes justify the extra
   cost of the full council pipeline below. Gating on already-resolved outcomes (not on the request
   up front) is deliberate - you don't know a turn crossed a resource threshold or fired a canon
   event until the normal tool-calling loop has actually applied those changes.

3. run_council(...) - the actual pipeline, only invoked when should_convene_council() said yes and
   the caller has Council Mode enabled and budget available:
     independent candidate generation (baseline narration + one fresh alternative)
       -> 6 judges, each scoring every candidate against one specific rubric (structured tool output,
          cheap model - narrow checklist evaluation doesn't need a strong model)
       -> a chairman that SYNTHESIZES a final narration from all candidates + all judge output
          (not just picks the top score), and is asked to surface disagreement rather than hide it
       -> a cheap-model voice-editor pass: word choice/rhythm only, explicitly forbidden from
          touching content.
   Every LLM call in this pipeline goes through the same provider.call() + usage_sink/budget_ok
   accounting as the normal turn loop (see engine.run_turn), so council spend is captured by the
   existing per-account budget ledger automatically, and a mid-pipeline budget exhaustion just
   aborts back to the baseline narration rather than failing the whole turn - the player always
   gets a narration, worst case it's the ungated single-pass one.

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
"""

import json

import providers

BANNED_WORDS = [
    "delve", "delving", "tapestry", "boundless", "testament", "realm", "meticulous",
    "meticulously", "underscore", "underscores", "intricate", "intricately", "elevate",
    "elevates", "vibrant", "bustling", "whispered secrets", "unwavering", "steadfast",
    "in the annals of", "a testament to",
]

VOICE_GUIDANCE = f"""
# VOICE
Write grounded, concrete, period-specific prose - not AI-generic narration. Specifically avoid:
- These words/phrases, which read as AI-generic regardless of context: {", ".join(BANNED_WORDS)}.
- The "it's not just X, it's Y" construction, in any variation.
- Rhythmic triads ("the cold, the hunger, the fear") used as a crutch for emphasis.
- Declaring emotion instead of showing it ("she felt a surge of pride") - show what she does, says,
  or notices instead, and let the emotion be inferable.
- Unjustified narrative leaps or hand-waving a consequence into existence without grounding it in
  something concrete the character did, said, or noticed.
Prefer plain, specific, sensory detail over abstraction. A character should sound like a specific
person in a specific place and moment, not like narration describing a generic historical drama.
"""


def should_convene_council(debug_events):
    """Free, rule-based gate - no LLM call. Returns (should_convene: bool, reasons: list[str]).

    Deliberately conservative/cheap to evaluate: only looks at signals engine.py already produces
    in debug_events from the turn that just resolved (resource label-band crossings, canon events,
    liabilities gained, critical rolls) - never a new LLM call just to decide whether to spend more
    on LLM calls.
    """
    reasons = []
    for ev in debug_events:
        if ev.get("type") == "check" and ev.get("degree") in ("critical_success", "critical_failure"):
            reasons.append(f"critical {ev['degree'].replace('_', ' ')} on {ev.get('attribute')}")
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
    return (len(reasons) > 0, reasons)


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


def _cheap_model_key_for(model_key):
    """Judges/voice-editor use a cheaper model than the current turn's candidates/chairman - narrow
    checklist evaluation doesn't need a strong model. Prefers haiku when the current model is on
    Anthropic; falls back to reusing the same model when it's Luna, since MODEL_REGISTRY doesn't
    currently register a cheaper OpenAI tier than Luna (Luna is already the cheap default)."""
    entry = providers.MODEL_REGISTRY.get(model_key, {})
    if entry.get("provider") == "anthropic":
        return "haiku"
    return model_key


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


def run_council(provider, model, model_key, system_prompt, user_input, debug_events, baseline_narration, usage_sink=None, budget_ok=None):
    """Only call this once should_convene_council() has already said yes - this function doesn't
    re-check that gate itself, to keep "should we bother" and "actually do it" as separate,
    independently testable concerns.

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
        "candidate_count": 1,
        "judge_results": [],
        "chairman_reasoning": None,
        "disagreement_notes": None,
        "aborted_reason": None,
    }

    mechanical_facts = _format_mechanical_facts(debug_events)
    candidates = [baseline_narration]

    judge_model_key = _cheap_model_key_for(model_key)
    try:
        judge_provider, judge_model = providers.get_provider(judge_model_key)
    except providers.ProviderError:
        # judge model's provider isn't configured (missing API key) - fall back to the same
        # provider/model already in use for this turn rather than failing the whole council.
        judge_provider, judge_model = provider, model

    alt = _generate_alternative_candidate(provider, model, system_prompt, user_input, mechanical_facts, usage_sink, budget_ok)
    if alt:
        candidates.append(alt)
    log["candidate_count"] = len(candidates)

    if not _ok(budget_ok):
        log["aborted_reason"] = "budget exhausted before judging - used baseline narration"
        return baseline_narration, log

    judge_results = []
    for role in JUDGE_ROLES:
        if not _ok(budget_ok):
            log["aborted_reason"] = f"budget exhausted mid-judging (after {len(judge_results)}/{len(JUDGE_ROLES)} judges) - used baseline narration"
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
