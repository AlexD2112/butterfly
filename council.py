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
