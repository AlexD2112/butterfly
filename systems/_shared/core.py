"""
Shared Butterfly engine core.

Every system (Red Admiral, Monarch, Apollo, ...) is mostly *configuration* over this module: it
defines attributes, skills, a curated feat catalog, a curated liability catalog, and a resource
category schema, then re-exports these functions with that config baked in. Actual dice math,
clamping, and state-update/level-up validation logic lives here exactly once, so a balance change
or bug fix applies to every system at once instead of needing to be repeated three times.

All Butterfly systems currently share the same six attributes (Oration, Intellect, Resolve,
Presence, Cunning, Physique) - they're abstract enough to mean something in any era (a Roman
senator, a Saxon thegn, and a Bolshevik agitator all have oration, resolve, cunning...). What
actually differentiates a system is its skill list, its feat/liability catalogs, and its resource
categories - see systems/red_admiral, systems/monarch, systems/apollo.
"""

import random

ATTRIBUTES = ["oration", "intellect", "resolve", "presence", "cunning", "physique"]
TIER_NAMES = ["untrained", "proficiency", "expertise"]

RESOURCE_LABELS = [
    (85, 100, "Dominant"),
    (70, 84, "High"),
    (55, 69, "Rising/New"),
    (35, 54, "Moderate"),
    (18, 34, "Low"),
    (0, 17, "Suspect/Negative"),
]

ENMITY_LABELS = {0: "neutral", 1: "latent", 2: "active hostility", 3: "dangerous enemy"}

XP_BASE = 100
XP_PER_LEVEL_INCREMENT = 50
XP_AWARD_CAP = 60
MAX_ATTRIBUTE = 20
MAX_LEVEL = 20
MAX_RESOURCE_DELTA_PER_CHECK = 10  # default; a setting's resource_schema.json may override via "per_check_max_delta"

# ---- character creation constants (custom characters) ----
# A fresh custom character assigns this standard array across the six attributes one-to-one - the
# same tradeoff-driven approach as 5e's standard array, much simpler to validate than a point-buy
# cost table. Premade legendary figures (e.g. Trotsky's Intellect 20) are allowed to exceed this,
# which is intentional: a hand-built historical figure should feel a cut above a fresh custom one.
STANDARD_ATTRIBUTE_ARRAY = [16, 14, 13, 12, 10, 8]
# 1 point per tier-step (untrained->proficiency=1, proficiency->expertise=+1 more). 10 base points
# was 14 through 2026-08-20 - trimmed during a balance pass: at 14, a level-1 character could afford
# 3 Expertise skills (6pts) plus 8 more at Proficiency, i.e. meaningful competence in roughly half a
# system's ~23-skill list before a single check has been rolled, undercutting the level-1-through-5
# growth arc. 10 base + LIABILITY_BONUS_SKILL_POINTS (below) restores the same 14-point ceiling for a
# character who takes on real narrative baggage to earn it, while a "clean" character starts leaner.
SKILL_POINT_BUDGET = 10
# Each starting liability (see validate_and_build_character) grants this many bonus skill points, on
# top of SKILL_POINT_BUDGET, up to the 2-liability cap. Added 2026-08-20: previously a liability was
# a pure narrative downside with zero character-creation upside, meaning the mechanically optimal
# choice was always to take none - not a real tradeoff, just an unrewarded roleplay flag. Taking both
# allowed liabilities now funds real extra competence (10 + 2*2 = 14, matching the old flat budget)
# in exchange for the narrative constraint, which is what makes it a tradeoff rather than a freebie.
LIABILITY_BONUS_SKILL_POINTS = 2
MAX_STARTING_EXPERTISE_SKILLS = 3


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def attribute_modifier(score):
    return (score - 10) // 2  # Python's // floors toward -inf, matching the standard d20 table


def proficiency_bonus_for_level(level):
    return 2 + (max(1, level) - 1) // 4


def tier_bonus(tier_index, proficiency_bonus):
    if tier_index <= 0:
        return 0
    if tier_index == 1:
        return proficiency_bonus
    return proficiency_bonus * 2


def xp_to_next_level(level, discount=0.0):
    base = XP_BASE + (level - 1) * XP_PER_LEVEL_INCREMENT
    return max(1, round(base * (1 - discount)))


def label_for_value(value, table=RESOURCE_LABELS):
    for lo, hi, label in table:
        if lo <= value <= hi:
            return label
    return table[-1][2]


def build_tool_specs(feat_catalog):
    """Generic three-tool spec (request_check / propose_state_update / propose_level_up), shared
    by every system. feat_catalog is only used to build a human-readable description hint - the
    actual validation of a proposed feat_id happens in apply_level_up, not here."""
    feat_hint = ", ".join(sorted(feat_catalog.keys())) if feat_catalog else "(none defined)"
    return [
        {
            "name": "request_check",
            "description": (
                "Request a resolution check when the player attempts something with real stakes and "
                "a genuine chance of failure. Do NOT use this for pure conversation/narration. The "
                "application rolls the die, computes the modifier, and returns the result - you do "
                "not generate the roll or the total yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attribute": {"type": "string", "enum": ATTRIBUTES},
                    "skill": {
                        "type": ["string", "null"],
                        "description": "A skill name from the character's sheet, if one plausibly applies. Null for a raw attribute check.",
                    },
                    "dc": {
                        "type": "integer",
                        "description": "8 routine, 12 meaningful challenge, 15 hard, 18 very hard, 22 extraordinary.",
                    },
                    "description": {"type": "string", "description": "A short label for the attempt, 3-10 words, e.g. 'Seek the chair's recognition'. This is NOT the narration: the player sees it as a caption on the dice. After the result comes back you still write the scene."},
                    "target_npc": {
                        "type": ["string", "null"],
                        "description": "Key of the NPC this check is directed at/against, if any (used for enmity tracking).",
                    },
                    "at_stake": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional: the one resource category this attempt actually risks or builds (e.g. "
                            "personal_safety for slipping a tail, party_standing for a Central Committee fight). "
                            "The application moves it automatically by the result: +4 critical success, +2 strong "
                            "success, 0 success, -3 success at a cost, -4 failure, -8 critical failure. Don't "
                            "propose a second delta for the same thing in propose_state_update."
                        ),
                    },
                },
                "required": ["attribute", "dc", "description"],
            },
        },
        {
            "name": "propose_state_update",
            "description": (
                "Propose the mechanical consequences of a check result or a significant narrative beat. "
                "The application validates and clamps this before applying it. Feats can only be granted "
                "through propose_level_up, never here - liabilities may be granted here but only by a "
                "known liability_id from the system's catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_deltas": {"type": "object", "description": 'e.g. {"mass_influence": 5} - keys must be real resource categories'},
                    "relationship_deltas": {"type": "object", "description": 'e.g. {"lenin": 1}'},
                    "enmity_deltas": {"type": "object", "description": 'e.g. {"kamenev": 1} (0-3 scale)'},
                    "flags_set": {"type": "object"},
                    "commitments_upserted": {
                        "type": "object",
                        "description": 'e.g. {"id": {"description": "...", "parties": ["..."], "status": "active"}}',
                    },
                    "liabilities_gained": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liability IDs from the system's liability catalog, if the fiction earns the character a new one.",
                    },
                    "liabilities_removed": {"type": "array", "items": {"type": "string"}, "description": "Liability IDs to remove."},
                    "xp_award": {
                        "type": "integer",
                        "description": (
                            f"0-{XP_AWARD_CAP}. This is the main lever controlling how fast the character "
                            "levels, so anchor it to actual stakes rather than picking a number that feels "
                            "generous in the moment - most single turns should land in the middle of this "
                            "range, not the top. Guideline: 0 for pure narration/conversation with no real "
                            "stakes; 5-10 for a routine check or a minor beat; 15-25 for a meaningful check "
                            "succeeding, a real complication overcome, or a notable failure handled well; "
                            "30-45 for a hard/very-hard check succeeding or a genuinely significant turning "
                            "point; 50-60 reserved for a rare campaign-defining moment (a major historical "
                            "beat, a canon event firing). A character who's only having routine scenes "
                            "should level slowly; don't inflate awards just to keep leveling feeling brisk."
                        ),
                    },
                    "canon_event_triggered": {"type": ["string", "null"]},
                    "divergence_note": {
                        "type": ["string", "null"],
                        "description": "Short description of a specific way the campaign just diverged from history, if it did.",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["reasoning"],
            },
        },
        {
            "name": "propose_level_up",
            "description": (
                "Spend a pending level-up (only valid when the current state shows level_up_pending: true). "
                f"Choose exactly one option. Available feats: {feat_hint}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "choice": {"type": "string", "enum": ["skill", "feat", "attribute"]},
                    "skill_upgrades": {
                        "type": "array",
                        "description": 'For choice="skill": up to 2 entries, max 1 per distinct skill. [{"skill": "...", "attribute": "..."}]',
                        "items": {
                            "type": "object",
                            "properties": {"skill": {"type": "string"}, "attribute": {"type": "string", "enum": ATTRIBUTES}},
                        },
                    },
                    "feat_id": {
                        "type": ["string", "null"],
                        "description": 'For choice="feat": a feat_id from the system\'s curated feat catalog.',
                    },
                    "attribute_points": {
                        "type": "object",
                        "description": 'For choice="attribute": e.g. {"intellect": 2, "resolve": 1} summing to 3.',
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["choice", "reasoning"],
            },
        },
    ]


# Degrees of success (2026-10-26). Checks used to be strictly binary past a natural 20/1, which made
# most rolls feel identical and gave the GM no code-backed reason to vary consequences. The margin
# (total - DC) now sets one of six degrees:
#   critical_success   natural 20, or beat the DC by 10+
#   strong_success     beat the DC by 5-9
#   success            met the DC, or beat it by up to 4
#   success_at_cost    missed by 1-3: the goal is reached, but something is paid for it
#   failure            missed by 4-9
#   critical_failure   natural 1, or missed by 10+
# success_at_cost counts as a success for anything asking "did it work" (see SUCCESS_DEGREES), but
# engine.run_turn also charges an at-stake resource for it automatically - see AT_STAKE_DELTAS.
STRONG_SUCCESS_MARGIN = 5
CRITICAL_MARGIN = 10
COST_MARGIN = 3

SUCCESS_DEGREES = ("critical_success", "strong_success", "success", "success_at_cost")
FAILURE_DEGREES = ("failure", "critical_failure")

DEGREE_GUIDANCE = {
    "critical_success": "Exceptional success: the goal is achieved and the GM should narrate a real extra benefit.",
    "strong_success": "Clean success: the goal is achieved with no complication, and slightly more than asked.",
    "success": "Success: the goal is achieved.",
    "success_at_cost": "Success at a cost: the goal is achieved, but narrate a real price (time, exposure, a promise, someone's goodwill).",
    "failure": "Failure: the goal is not achieved; the situation moves on without it.",
    "critical_failure": "Critical failure: the goal is not achieved and something actively goes wrong beyond that.",
}

# What an at-stake resource (request_check's optional at_stake field) moves by, per degree. Applied by
# engine.run_turn in code, so a failure costs something even if the GM never proposes a delta for it.
AT_STAKE_DELTAS = {
    "critical_success": 4,
    "strong_success": 2,
    "success": 0,
    "success_at_cost": -3,
    "failure": -4,
    "critical_failure": -8,
}


def degree_for(roll, total, dc):
    if roll == 20:
        return "critical_success"
    if roll == 1:
        return "critical_failure"
    margin = total - dc
    if margin >= CRITICAL_MARGIN:
        return "critical_success"
    if margin >= STRONG_SUCCESS_MARGIN:
        return "strong_success"
    if margin >= 0:
        return "success"
    if margin >= -COST_MARGIN:
        return "success_at_cost"
    if margin > -CRITICAL_MARGIN:
        return "failure"
    return "critical_failure"


def roll_check(character, attribute, skill, dc, target_npc=None, feat_catalog=None):
    score = character["attributes"][attribute]
    mod = attribute_modifier(score)
    proficiency_bonus = proficiency_bonus_for_level(character["level"])

    tier_index = 0
    tier_name = "untrained"
    if skill and skill in character.get("skills", {}):
        skill_entry = character["skills"][skill]
        tier_index = skill_entry["tier"]
        tier_name = TIER_NAMES[tier_index]

    # A feat's catalog entry may carry a "check_bonus": {"on_attributes": [...], "bonus": N} - a
    # small, code-enforced circumstance bonus applied whenever the check's attribute matches. Only
    # given to feats whose narrative already reads as "checks of this flavor go better" with a clean,
    # unambiguous trigger (see FEAT_CATALOG comments) - most feats are deliberately left as pure GM
    # narrative because their trigger isn't something roll_check's inputs can detect (a specific NPC's
    # social class, "unfamiliar terrain", once-per-arc favors). This mirrors liabilities'
    # auto_trigger mechanism (apply_auto_liability_effects) - feats needed the same kind of
    # code-enforced weight, since "the app owns mechanical outcomes, not the model" is the whole
    # premise of this engine, and a feat with zero verifiable effect doesn't hold up that premise.
    feat_bonus = 0
    feat_bonus_source = None
    if feat_catalog:
        for f in character.get("feats", []):
            entry = feat_catalog.get(f.get("id"))
            trigger = (entry or {}).get("check_bonus")
            if trigger and attribute in trigger.get("on_attributes", []):
                feat_bonus += trigger.get("bonus", 0)
                feat_bonus_source = f.get("name")  # last match wins if more than one applies; fine at this scale

    modifier = mod + tier_bonus(tier_index, proficiency_bonus) + feat_bonus
    roll = random.randint(1, 20)
    total = roll + modifier
    degree = degree_for(roll, total, dc)

    # Track attribute/skill usage since the last level-up, for the reinforcement-bonus check at the
    # next level-up - the application tracks this, not the model's self-report.
    usage = character.setdefault("recent_check_usage", {"attributes": [], "skills": []})
    if attribute not in usage["attributes"]:
        usage["attributes"].append(attribute)
    if skill and skill not in usage["skills"]:
        usage["skills"].append(skill)

    return {
        "attribute": attribute,
        "skill": skill,
        "tier": tier_name,
        "attribute_score": score,
        "attribute_modifier": mod,
        "proficiency_bonus": proficiency_bonus,
        "feat_bonus": feat_bonus,
        "feat_bonus_source": feat_bonus_source,
        "modifier": modifier,
        "roll": roll,
        "total": total,
        "dc": dc,
        "margin": total - dc,
        "degree": degree,
        "gm_instruction": DEGREE_GUIDANCE[degree],
        "target_npc": target_npc,
    }


def apply_auto_liability_effects(state, check_result, liability_catalog):
    """Code-enforced liability triggers. A liability's catalog entry may carry an "auto_trigger"
    spec: {"on_attributes": [...], "effect": "enmity_pip"}. This is checked generically here rather
    than hardcoding any specific liability's name in code, so any system's catalog can define its
    own auto-triggering liabilities without touching this function."""
    changes = []
    if check_result["degree"] not in FAILURE_DEGREES:
        return changes
    target = check_result.get("target_npc")
    if not target or target not in state.get("enmity", {}):
        return changes

    character_liabilities = state["character"].get("liabilities", [])
    for liability in character_liabilities:
        liability_id = liability.get("id")
        catalog_entry = (liability_catalog or {}).get(liability_id)
        trigger = (catalog_entry or {}).get("auto_trigger")
        if not trigger:
            continue
        if check_result["attribute"] not in trigger.get("on_attributes", []):
            continue
        if trigger.get("effect") == "enmity_pip":
            before = state["enmity"][target]["pips"]
            after = clamp(before + 1, 0, 3)
            state["enmity"][target]["pips"] = after
            changes.append(
                {"kind": "enmity_auto", "key": target, "before": before, "after": after, "cause": f"{liability.get('name', liability_id)} liability"}
            )
    return changes


BROKEN_COMMITMENT_TRUST_PENALTY = 2


def _party_key(name):
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def apply_broken_commitment(state, commitment_key, entry):
    changes = []
    for party in (entry or {}).get("parties") or []:
        key = _party_key(party)
        if key in state.get("relationships", {}):
            before = state["relationships"][key]["trust"]
            after = clamp(before - BROKEN_COMMITMENT_TRUST_PENALTY, -5, 5)
            state["relationships"][key]["trust"] = after
            changes.append({"kind": "relationship", "key": key, "before": before, "after": after,
                             "requested_delta": -BROKEN_COMMITMENT_TRUST_PENALTY, "clamped": False,
                             "cause": f"broke commitment '{commitment_key}'"})
        if key in state.get("enmity", {}):
            before = state["enmity"][key]["pips"]
            after = clamp(before + 1, 0, 3)
            state["enmity"][key]["pips"] = after
            changes.append({"kind": "enmity_auto", "key": key, "before": before, "after": after,
                             "cause": f"broke commitment '{commitment_key}'"})
    return changes


def check_level_up(state):
    """Call after any XP change. May trigger multiple level-ups if a huge award crosses more than
    one threshold at once (shouldn't normally happen given XP_AWARD_CAP, but handled correctly)."""
    changes = []
    character = state["character"]
    while True:
        threshold = xp_to_next_level(character["level"], character.get("next_level_xp_discount", 0.0))
        if character["xp"] < threshold or character["level"] >= MAX_LEVEL:
            break
        character["xp"] -= threshold
        character["level"] += 1
        character["proficiency_bonus"] = proficiency_bonus_for_level(character["level"])
        character["level_up_pending"] = True
        character["next_level_xp_discount"] = 0.0  # consumed; recomputed at the actual level-up choice
        changes.append({"kind": "level_up", "new_level": character["level"]})
    return changes


def apply_state_update(state, update, resource_schema, liability_catalog=None):
    changes = []
    character = state["character"]
    resource_defs = resource_schema["resources"]
    max_delta = resource_schema.get("per_check_max_delta", MAX_RESOURCE_DELTA_PER_CHECK)
    liability_catalog = liability_catalog or {}

    for key, delta in (update.get("resource_deltas") or {}).items():
        if key not in state["resources"]:
            changes.append({"kind": "ignored_unknown_category", "key": key})
            continue
        bounded = clamp(delta, -max_delta, max_delta)
        lo, hi = resource_defs.get(key, {}).get("range", [0, 100])
        before = state["resources"][key]
        after = clamp(before + bounded, lo, hi)
        state["resources"][key] = after
        changes.append(
            {
                "kind": "resource", "key": key, "before": before, "after": after,
                "before_label": label_for_value(before), "after_label": label_for_value(after),
                "requested_delta": delta, "clamped": bounded != delta,
            }
        )

    for key, delta in (update.get("relationship_deltas") or {}).items():
        if key not in state["relationships"]:
            state["relationships"][key] = {"trust": 0, "note": "Introduced mid-campaign."}
        bounded = clamp(delta, -5, 5)
        before = state["relationships"][key]["trust"]
        after = clamp(before + bounded, -5, 5)
        state["relationships"][key]["trust"] = after
        changes.append({"kind": "relationship", "key": key, "before": before, "after": after,
                         "requested_delta": delta, "clamped": bounded != delta})

    for key, delta in (update.get("enmity_deltas") or {}).items():
        if key not in state["enmity"]:
            state["enmity"][key] = {"pips": 0, "note": "New rival."}
        bounded = clamp(delta, -1, 1)  # at most 1 pip per proposal; auto-triggers bypass this via apply_auto_liability_effects
        before = state["enmity"][key]["pips"]
        after = clamp(before + bounded, 0, 3)
        state["enmity"][key]["pips"] = after
        changes.append({"kind": "enmity", "key": key, "before": before, "after": after,
                         "before_label": ENMITY_LABELS[before], "after_label": ENMITY_LABELS[after],
                         "requested_delta": delta, "clamped": bounded != delta})

    for key, value in (update.get("flags_set") or {}).items():
        character["flags"][key] = value
        changes.append({"kind": "flag", "key": key, "value": value})

    for key, entry in (update.get("commitments_upserted") or {}).items():
        previous_status = (state["commitments"].get(key) or {}).get("status")
        state["commitments"][key] = entry
        changes.append({"kind": "commitment", "key": key, "value": entry})
        # A commitment going from active to broken costs something in code, not just in narration
        # (2026-10-26): every bound party loses 2 trust and gains an enmity pip. Parties are matched
        # against existing relationship/enmity keys, so a party named only in prose ("the Left SRs")
        # is skipped rather than invented as a new NPC.
        if previous_status == "active" and (entry or {}).get("status") == "broken":
            changes.extend(apply_broken_commitment(state, key, entry))

    existing_liability_ids = {l.get("id") for l in character.get("liabilities", [])}
    for liability_id in (update.get("liabilities_gained") or []):
        catalog_entry = liability_catalog.get(liability_id)
        if not catalog_entry:
            changes.append({"kind": "liability_rejected", "id": liability_id, "reason": "unknown liability_id"})
            continue
        if liability_id in existing_liability_ids:
            changes.append({"kind": "liability_rejected", "id": liability_id, "reason": "already has this liability"})
            continue
        character.setdefault("liabilities", []).append(
            {"id": liability_id, "name": catalog_entry["name"], "description": catalog_entry["description"]}
        )
        existing_liability_ids.add(liability_id)
        changes.append({"kind": "liability_gained", "id": liability_id, "name": catalog_entry["name"]})

    for liability_id in (update.get("liabilities_removed") or []):
        before_count = len(character.get("liabilities", []))
        character["liabilities"] = [l for l in character.get("liabilities", []) if l.get("id") != liability_id]
        if len(character["liabilities"]) != before_count:
            changes.append({"kind": "liability_removed", "id": liability_id})

    xp_award = update.get("xp_award")
    if xp_award:
        bounded_xp = clamp(int(xp_award), 0, XP_AWARD_CAP)
        character["xp"] += bounded_xp
        changes.append({"kind": "xp", "requested": xp_award, "applied": bounded_xp})
        changes.extend(check_level_up(state))

    event = update.get("canon_event_triggered")
    if event and event in state["canon_events"]:
        if state["canon_events"][event]["status"] == "pending":
            state["canon_events"][event]["status"] = "fired"
            changes.append({"kind": "canon_event", "key": event, "status": "fired"})

    note = update.get("divergence_note")
    if note:
        state["divergence_log"].append({"turn": state["turn_number"], "note": note})
        state["divergence_score"] = state.get("divergence_score", 0) + 1
        changes.append({"kind": "divergence", "note": note})

    return changes


def validate_and_build_character(
    name, role, attribute_assignment, skill_tiers, feat_id, liability_ids,
    system_skills, feat_catalog, liability_catalog,
):
    """Generic custom-character builder shared by every system. system_skills is a
    {attribute: [skill_name, ...]} map (a system's canonical skill list); feat_catalog/
    liability_catalog are that system's curated dicts. Returns (character_dict, errors) - character
    is None if errors is non-empty."""
    errors = []

    if not name or not name.strip():
        errors.append("A character name is required.")

    if set(attribute_assignment.keys()) != set(ATTRIBUTES) or sorted(attribute_assignment.values()) != sorted(STANDARD_ATTRIBUTE_ARRAY):
        errors.append(f"Attributes must be the standard array {STANDARD_ATTRIBUTE_ARRAY}, assigned one-to-one across all six attributes.")

    if liability_ids and len(liability_ids) > 2:
        errors.append("At most 2 starting liabilities may be chosen.")
    liability_entries = []
    for lid in (liability_ids or []):
        entry = liability_catalog.get(lid)
        if not entry:
            errors.append(f"Unknown liability: {lid}")
        else:
            liability_entries.append({"id": lid, "name": entry["name"], "description": entry["description"]})

    # Each valid starting liability funds extra skill points - see LIABILITY_BONUS_SKILL_POINTS'
    # docstring for why (a liability with no creation-time upside is a freebie downside, not a
    # tradeoff). Only counts liabilities that actually validated, so an unknown liability_id can't be
    # used to inflate the budget.
    effective_skill_budget = SKILL_POINT_BUDGET + LIABILITY_BONUS_SKILL_POINTS * len(liability_entries)

    all_valid_skills = {s for skills in system_skills.values() for s in skills}
    spent = 0
    expertise_count = 0
    for skill, tier in (skill_tiers or {}).items():
        if skill not in all_valid_skills:
            errors.append(f"Unknown skill: {skill}")
            continue
        if tier not in (0, 1, 2):
            errors.append(f"Invalid tier for {skill}: {tier}")
            continue
        spent += tier
        if tier == 2:
            expertise_count += 1
    if spent > effective_skill_budget:
        errors.append(f"Skill points over budget: {spent}/{effective_skill_budget}")
    if expertise_count > MAX_STARTING_EXPERTISE_SKILLS:
        errors.append(f"At most {MAX_STARTING_EXPERTISE_SKILLS} skills may start at Expertise tier.")

    feat_entry = feat_catalog.get(feat_id)
    if not feat_entry:
        errors.append(f"Unknown starting feat: {feat_id}")
    elif feat_entry.get("min_level", 1) > 1:
        errors.append(f"Feat '{feat_entry['name']}' is not available at level 1.")

    if errors:
        return None, errors

    skills_out = {}
    for attr, skills in system_skills.items():
        for s in skills:
            skills_out[s] = {"attribute": attr, "tier": (skill_tiers or {}).get(s, 0)}

    character = {
        "name": name.strip(),
        "role": (role or "").strip(),
        "level": 1,
        "xp": 0,
        "level_up_pending": False,
        "next_level_xp_discount": 0.0,
        "proficiency_bonus": proficiency_bonus_for_level(1),
        "attributes": dict(attribute_assignment),
        "skills": skills_out,
        "feats": [{"id": feat_id, "name": feat_entry["name"], "description": feat_entry["description"]}],
        "liabilities": liability_entries,
        "flags": {},
        "recent_check_usage": {"attributes": [], "skills": []},
    }
    return character, []


# "Fairly leveled" ceiling for an AI-generated historical figure (2026-08-23, see NOTES.md) - a
# deliberate simplification, not a full level-up simulation: rather than replaying N-1 real level-up
# choices through apply_level_up (which would also let attributes drift past the standard array via
# an attribute-type choice), the skill/feat budget below scales linearly with level as a bounded
# stand-in, while attributes stay locked to the same standard array a fresh custom character uses.
MAX_HISTORICAL_FIGURE_LEVEL = 6


def validate_and_build_leveled_character(
    name, role, level, attribute_assignment, skill_tiers, feat_ids, liability_ids,
    system_skills, feat_catalog, liability_catalog,
):
    """Builds a historical figure starting ABOVE level 1 - same standard-array attribute rule as
    validate_and_build_character (never stat-inflated beyond the array, per direction), with a skill
    budget and feat allowance that scale with level instead. Returns (character_dict, errors)."""
    errors = []

    if not name or not name.strip():
        errors.append("A character name is required.")

    if not isinstance(level, int) or not (1 <= level <= MAX_HISTORICAL_FIGURE_LEVEL):
        errors.append(f"level must be an integer from 1 to {MAX_HISTORICAL_FIGURE_LEVEL}.")
        level = clamp(level, 1, MAX_HISTORICAL_FIGURE_LEVEL) if isinstance(level, int) else 1

    if set(attribute_assignment.keys()) != set(ATTRIBUTES) or sorted(attribute_assignment.values()) != sorted(STANDARD_ATTRIBUTE_ARRAY):
        errors.append(f"Attributes must be the standard array {STANDARD_ATTRIBUTE_ARRAY}, assigned one-to-one across all six attributes.")

    if liability_ids and len(liability_ids) > 2:
        errors.append("At most 2 starting liabilities may be chosen.")
    liability_entries = []
    for lid in (liability_ids or []):
        entry = liability_catalog.get(lid)
        if not entry:
            errors.append(f"Unknown liability: {lid}")
        else:
            liability_entries.append({"id": lid, "name": entry["name"], "description": entry["description"]})

    # Same base budget as a fresh level-1 custom character, plus 2 skill points per level above 1 -
    # an upper bound matching what a single "skill" level-up choice grants (2 tier-steps), standing
    # in for levels this figure has plausibly already spent without simulating each one individually.
    effective_skill_budget = SKILL_POINT_BUDGET + LIABILITY_BONUS_SKILL_POINTS * len(liability_entries) + 2 * (level - 1)
    max_expertise = MAX_STARTING_EXPERTISE_SKILLS + (level - 1)

    all_valid_skills = {s for skills in system_skills.values() for s in skills}
    spent = 0
    expertise_count = 0
    for skill, tier in (skill_tiers or {}).items():
        if skill not in all_valid_skills:
            errors.append(f"Unknown skill: {skill}")
            continue
        if tier not in (0, 1, 2):
            errors.append(f"Invalid tier for {skill}: {tier}")
            continue
        spent += tier
        if tier == 2:
            expertise_count += 1
    if spent > effective_skill_budget:
        errors.append(f"Skill points over budget: {spent}/{effective_skill_budget}")
    if expertise_count > max_expertise:
        errors.append(f"At most {max_expertise} skills may start at Expertise tier at level {level}.")

    # Eligibility mismatches (wrong min_level, unmet prerequisite) are dropped, not hard errors - an
    # AI generating this sheet in one shot isn't reliably self-consistent between the level it picked
    # and the feats it reached for (observed for real: a real, otherwise-eligible figure got refused
    # over a single overreaching feat pick - see NOTES.md). Only a genuinely UNKNOWN feat_id (a
    # hallucinated id not in the catalog at all) or an exact duplicate is treated as a real error -
    # that's a sign of a malformed response, not just an honest reach that should simply be pruned.
    feat_entries = []
    seen_feat_ids = set()
    for feat_id in (feat_ids or []):
        entry = feat_catalog.get(feat_id)
        if not entry:
            errors.append(f"Unknown feat: {feat_id}")
            continue
        if feat_id in seen_feat_ids:
            errors.append(f"Duplicate feat: {feat_id}")
            continue
        if entry.get("min_level", 1) > level:
            continue
        prereq = entry.get("prerequisite_skill")
        if prereq and (skill_tiers or {}).get(prereq["skill"], 0) < prereq["min_tier"]:
            continue
        seen_feat_ids.add(feat_id)
        feat_entries.append({"id": feat_id, "name": entry["name"], "description": entry["description"]})

    if errors:
        return None, errors

    skills_out = {}
    for attr, skills in system_skills.items():
        for s in skills:
            skills_out[s] = {"attribute": attr, "tier": (skill_tiers or {}).get(s, 0)}

    character = {
        "name": name.strip(),
        "role": (role or "").strip(),
        "level": level,
        "xp": 0,
        "level_up_pending": False,
        "next_level_xp_discount": 0.0,
        "proficiency_bonus": proficiency_bonus_for_level(level),
        "attributes": dict(attribute_assignment),
        "skills": skills_out,
        "feats": feat_entries,
        "liabilities": liability_entries,
        "flags": {},
        "recent_check_usage": {"attributes": [], "skills": []},
    }
    return character, []


def apply_level_up(state, update, feat_catalog):
    character = state["character"]
    if not character.get("level_up_pending"):
        return [{"kind": "level_up_rejected", "reason": "No level-up is pending."}]

    choice = update.get("choice")
    changes = []
    reinforced = False
    usage = character.get("recent_check_usage", {"attributes": [], "skills": []})

    if choice == "skill":
        upgrades = (update.get("skill_upgrades") or [])[:2]
        seen = set()
        for u in upgrades:
            skill_name = u.get("skill")
            attr = u.get("attribute")
            if not skill_name or attr not in ATTRIBUTES or skill_name in seen:
                continue
            seen.add(skill_name)
            entry = character["skills"].setdefault(skill_name, {"attribute": attr, "tier": 0})
            if entry["tier"] < 2:
                before = entry["tier"]
                entry["tier"] += 1
                changes.append({"kind": "skill_upgrade", "skill": skill_name, "before": TIER_NAMES[before], "after": TIER_NAMES[entry["tier"]]})
                if skill_name in usage["skills"]:
                    reinforced = True
            else:
                changes.append({"kind": "skill_upgrade_rejected", "skill": skill_name, "reason": "already at Expertise (max tier) - this step was wasted, propose a different skill"})

    elif choice == "feat":
        feat_id = update.get("feat_id")
        catalog_entry = (feat_catalog or {}).get(feat_id)
        existing_ids = {f.get("id") for f in character.get("feats", [])}
        if not catalog_entry:
            changes.append({"kind": "feat_rejected", "id": feat_id, "reason": "unknown feat_id - choose one from the system's feat catalog"})
        elif feat_id in existing_ids:
            changes.append({"kind": "feat_rejected", "id": feat_id, "reason": "character already has this feat"})
        elif catalog_entry.get("min_level", 1) > character["level"]:
            changes.append({"kind": "feat_rejected", "id": feat_id, "reason": f"requires level {catalog_entry.get('min_level')}"})
        else:
            prereq = catalog_entry.get("prerequisite_skill")
            if prereq:
                entry = character["skills"].get(prereq["skill"])
                have_tier = entry["tier"] if entry else 0
                if have_tier < prereq["min_tier"]:
                    changes.append({
                        "kind": "feat_rejected", "id": feat_id,
                        "reason": f"requires {prereq['skill']} at {TIER_NAMES[prereq['min_tier']]} or higher",
                    })
                    catalog_entry = None
            if catalog_entry:
                character.setdefault("feats", []).append(
                    {"id": feat_id, "name": catalog_entry["name"], "description": catalog_entry["description"]}
                )
                changes.append({"kind": "feat_gained", "id": feat_id, "name": catalog_entry["name"]})
                reinforced = True  # feats are granted for demonstrated behavior by definition

    elif choice == "attribute":
        points = update.get("attribute_points") or {}
        total = sum(max(0, int(v)) for v in points.values())
        if total > 3:
            scale = 3 / total
            points = {k: int(v * scale) for k, v in points.items()}
        for attr, amount in points.items():
            if attr not in ATTRIBUTES or amount <= 0:
                continue
            before = character["attributes"][attr]
            after = clamp(before + amount, 1, MAX_ATTRIBUTE)
            character["attributes"][attr] = after
            changes.append({"kind": "attribute_improvement", "attribute": attr, "before": before, "after": after})
            if attr in usage["attributes"]:
                reinforced = True

    else:
        return [{"kind": "level_up_rejected", "reason": f"Unknown choice: {choice}"}]

    real_change_kinds = {"skill_upgrade", "feat_gained", "attribute_improvement"}
    if any(c["kind"] in real_change_kinds for c in changes):
        character["level_up_pending"] = False
        character["recent_check_usage"] = {"attributes": [], "skills": []}
        if reinforced:
            character["next_level_xp_discount"] = 0.2
            changes.append({"kind": "reinforcement_bonus", "discount": 0.2})
    else:
        changes.append({"kind": "level_up_rejected", "reason": "No valid change was applied - level-up is still pending, try a different choice."})

    return changes
