"""
Dynamics: the clock, the world, and the rules that make resources cause things (2026-10-26).

Before this, a campaign's date never moved (state["date"] was set once and never touched), the
"world" resources factions.md talked about (the government's stability, war exhaustion) didn't exist,
canon_timeline.md's "prerequisites the code checks" were never actually checked, and resources were
independent mood meters - nothing happened at any threshold unless the GM remembered to make it
happen. A setting opts into all of this with a settings/<name>/dynamics.json file; a setting without
one is untouched.

dynamics.json sections (all optional):
  calendar     {"max_days_per_update": 7, "style": "Julian (Old Style)"}
  milestones   [{"label", "date"}]                        countdown shown to GM and player
  world        {key: {"label", "start", "range", "description"}}  state["world"]
  world_max_delta_per_update                               clamp for the GM's world_deltas
  drift        [{"target": "world.x" | "resources.x", "per_day": n, "when": [cond...]}]
  thresholds   [{"id", "when": [cond...], "once": bool, "cooldown_days": n, "message",
                 "effects": {...}, "roll": {"attribute", "skill", "dc", "description",
                 "on_success": {...}, "on_failure": {...}}}]
  canon        {event_key: {"type": "locked"|"contingent", "date"?, "not_before"?,
                 "requires"?: [cond...], "effects"?: {...}, "message"?, "historical_date"?}}
  derived      {resource_key: {"from_forces": type_key, "kinds": [...]}}
  influence    {"vote": resource_key, "sway": resource_key}
  sway_risk_resource, confrontation_effects [{"outcome", "player_attacking"?, "when"?, "effects", "message"?}]

A condition is {"path": "resources.party_standing", "op": ">=", "value": 60}. Paths: resources.<k>,
world.<k>, relationships.<k>.trust, enmity.<k>.pips, flags.<k> (the character's), canon.<k> (that
event's status), date. Dates compare as ISO strings.

Effects: {"resources": {k: delta}, "world": {k: delta}, "flags": {k: v}, "relationships": {k: delta},
"enmity": {k: delta}, "unit_loyalty": {"type_key": t, "kinds": [...], "max_loyalty"?: n, "min_loyalty"?: n, "delta": n}}. Effects are
code-applied consequences, so they bypass the per-check delta cap but still respect each range.

Time is Julian-dated in Petrograd, but day arithmetic over a few months doesn't care which calendar
the strings are in, so plain datetime.date math is used.
"""

import datetime
import json
from pathlib import Path

import geography
import knowledge
import random
import operations
from systems._shared import core

ROOT = Path(__file__).parent
SETTINGS_ROOT = ROOT / "settings"
NOTICE_TURNS_VISIBLE = 2
DEFAULT_MAX_DAYS = 7

_cache = {}


def load(setting_key):
    if setting_key not in _cache:
        path = SETTINGS_ROOT / setting_key / "dynamics.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("_comment", None)
            _cache[setting_key] = raw
        else:
            _cache[setting_key] = None
    return _cache[setting_key]


# ---------------------------------------------------------------- helpers

def _parse_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def add_days(date_str, days):
    d = _parse_date(date_str)
    if d is None:
        return date_str
    return (d + datetime.timedelta(days=days)).isoformat()


def days_between(a, b):
    da, db = _parse_date(a), _parse_date(b)
    if da is None or db is None:
        return None
    return (db - da).days


def resolve_path(state, path):
    parts = path.split(".")
    head = parts[0]
    if head == "date":
        return state.get("date")
    if head == "flags":
        return (state.get("character", {}).get("flags") or {}).get(parts[1])
    if head == "canon":
        return ((state.get("canon_events") or {}).get(parts[1]) or {}).get("status")
    node = state.get(head)
    for p in parts[1:]:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def condition_met(state, cond):
    value = resolve_path(state, cond["path"])
    if value is None and cond.get("op") not in ("==", "!="):
        return False
    try:
        return OPS[cond.get("op", ">=")](value, cond["value"])
    except TypeError:
        return False


def all_met(state, conds):
    return all(condition_met(state, c) for c in (conds or []))


def describe_condition(cond):
    return f"{cond['path']} {cond.get('op', '>=')} {cond['value']}"


def _range_for(dyn, resource_schema, target):
    head, key = target.split(".", 1)
    if head == "world":
        return tuple((dyn.get("world", {}).get(key) or {}).get("range", [0, 100]))
    return tuple(((resource_schema or {}).get("resources", {}).get(key) or {}).get("range", [0, 100]))


def derived_keys(dyn):
    return set((dyn or {}).get("derived", {}).keys())


# ---------------------------------------------------------------- initialization

def ensure_initialized(state, dyn):
    """Idempotent. Adds world tracks and canon entries a save is missing - including saves made
    before this module existed - without overwriting anything already there."""
    if not dyn:
        return
    world = state.setdefault("world", {})
    for key, spec in (dyn.get("world") or {}).items():
        world.setdefault(key, spec.get("start", 50))
    canon_state = state.setdefault("canon_events", {})
    for key, spec in (dyn.get("canon") or {}).items():
        canon_state.setdefault(key, {"status": "pending", "type": spec.get("type", "contingent")})
    state.setdefault("world_notices", [])
    state.setdefault("dynamics_log", {})


# ---------------------------------------------------------------- effects

def apply_effects(state, dyn, resource_schema, effects, cause):
    changes = []
    if not effects:
        return changes
    derived = derived_keys(dyn)
    for key, delta in (effects.get("resources") or {}).items():
        if key not in state.get("resources", {}) or key in derived:
            continue
        lo, hi = _range_for(dyn, resource_schema, f"resources.{key}")
        before = state["resources"][key]
        after = core.clamp(before + delta, lo, hi)
        state["resources"][key] = after
        changes.append({"kind": "resource", "key": key, "before": before, "after": after,
                        "before_label": core.label_for_value(before), "after_label": core.label_for_value(after),
                        "requested_delta": delta, "clamped": False, "cause": cause})
    for key, delta in (effects.get("world") or {}).items():
        if key not in state.get("world", {}):
            continue
        lo, hi = _range_for(dyn, resource_schema, f"world.{key}")
        before = state["world"][key]
        after = core.clamp(round(before + delta), lo, hi)
        state["world"][key] = after
        changes.append({"kind": "world", "key": key, "before": before, "after": after, "cause": cause})
    for key, value in (effects.get("flags") or {}).items():
        state["character"].setdefault("flags", {})[key] = value
        changes.append({"kind": "flag", "key": key, "value": value, "cause": cause})
    for key, delta in (effects.get("relationships") or {}).items():
        rel = state.setdefault("relationships", {}).setdefault(key, {"trust": 0, "note": "Introduced mid-campaign."})
        before = rel["trust"]
        rel["trust"] = core.clamp(before + delta, -5, 5)
        changes.append({"kind": "relationship", "key": key, "before": before, "after": rel["trust"],
                        "requested_delta": delta, "clamped": False, "cause": cause})
    for key, delta in (effects.get("enmity") or {}).items():
        en = state.setdefault("enmity", {}).setdefault(key, {"pips": 0, "note": "New rival."})
        before = en["pips"]
        en["pips"] = core.clamp(before + delta, 0, 3)
        changes.append({"kind": "enmity_auto", "key": key, "before": before, "after": en["pips"], "cause": cause})
    ul = effects.get("unit_loyalty")
    if ul:
        for iid, m in (state.get("mechanics") or {}).items():
            if m.get("render_primitive") != "forces" or (ul.get("type_key") and m.get("type_key") != ul["type_key"]):
                continue
            for u in m["data"]["units"]:
                if ul.get("kinds") and u.get("kind") not in ul["kinds"]:
                    continue
                if "max_loyalty" in ul and u["loyalty"] > ul["max_loyalty"]:
                    continue
                if "min_loyalty" in ul and u["loyalty"] < ul["min_loyalty"]:
                    continue
                before = u["loyalty"]
                u["loyalty"] = core.clamp(before + ul.get("delta", 0), -3, 3)
                if u["loyalty"] != before:
                    changes.append({"kind": "unit_loyalty", "instance_id": iid, "unit": u["name"],
                                    "before": before, "after": u["loyalty"], "cause": cause})
    return changes


def add_notice(state, message, cause):
    state.setdefault("world_notices", []).append({
        "turn": state.get("turn_number", 0), "date": state.get("date"), "message": message, "cause": cause,
    })


# ---------------------------------------------------------------- canon events

def canon_requirements(state, dyn, key):
    """Returns a list of unmet requirement descriptions (empty means it may fire)."""
    spec = (dyn.get("canon") or {}).get(key) or {}
    unmet = [describe_condition(c) for c in spec.get("requires") or [] if not condition_met(state, c)]
    not_before = spec.get("not_before")
    if not_before and (state.get("date") or "") < not_before:
        unmet.append(f"date >= {not_before}")
    return unmet


def try_fire_event(state, dyn, resource_schema, key, cause):
    """The gate canon_timeline.md always described but nothing enforced. Returns changes; a rejection
    is itself a change, so the GM sees in the turn's tool result exactly what's still missing."""
    events = state.setdefault("canon_events", {})
    if key not in events:
        return [{"kind": "canon_event_rejected", "key": key, "reason": "unknown canon event"}]
    if events[key].get("status") != "pending":
        return [{"kind": "canon_event_rejected", "key": key, "reason": f"already {events[key].get('status')}"}]
    if dyn and key in (dyn.get("canon") or {}):
        spec = dyn["canon"][key]
        if spec.get("type") == "locked" and spec.get("date"):
            return [{"kind": "canon_event_rejected", "key": key,
                     "reason": f"locked to {spec['date']} - it fires on its own when the date arrives"}]
        unmet = canon_requirements(state, dyn, key)
        if unmet:
            return [{"kind": "canon_event_rejected", "key": key, "reason": "requirements not met: " + "; ".join(unmet)}]
    return fire_event(state, dyn, resource_schema, key, cause)


def fire_event(state, dyn, resource_schema, key, cause):
    state["canon_events"][key]["status"] = "fired"
    state["canon_events"][key]["fired_date"] = state.get("date")
    spec = ((dyn or {}).get("canon") or {}).get(key) or {}
    changes = [{"kind": "canon_event", "key": key, "status": "fired", "message": spec.get("message")}]
    changes += apply_effects(state, dyn, resource_schema, spec.get("effects"), f"canon: {key}")
    if spec.get("message"):
        add_notice(state, spec["message"], f"canon: {key}")
    return changes


# ---------------------------------------------------------------- time

def advance_time(state, dyn, resource_schema, system, days, rng=None):
    """Moves the date forward day by day, applying drift each day, firing date-locked canon events as
    their date arrives, and checking thresholds at the end of each day."""
    if not dyn or not days or days <= 0:
        return []
    max_days = (dyn.get("calendar") or {}).get("max_days_per_update", DEFAULT_MAX_DAYS)
    days = min(int(days), max_days)
    before_date = state.get("date")
    changes = []
    accum = state.setdefault("_drift_accum", {})
    for _ in range(days):
        state["date"] = add_days(state["date"], 1)
        for rule in dyn.get("drift") or []:
            if rule.get("when") and not all_met(state, rule["when"]):
                continue
            target = rule["target"]
            head, key = target.split(".", 1)
            bucket = state.get(head) or {}
            if key not in bucket or (head == "resources" and key in derived_keys(dyn)):
                continue
            accum[target] = accum.get(target, 0.0) + rule["per_day"]
            whole = int(accum[target])  # truncates toward zero for negatives too
            if whole:
                accum[target] -= whole
                lo, hi = _range_for(dyn, resource_schema, target)
                bucket[key] = core.clamp(bucket[key] + whole, lo, hi)
        for key, spec in (dyn.get("canon") or {}).items():
            if spec.get("type") == "locked" and spec.get("date") and spec["date"] <= state["date"]:
                if state["canon_events"].get(key, {}).get("status") == "pending":
                    changes += fire_event(state, dyn, resource_schema, key, "date reached")
        geo = geography.load(state.get("setting_pack"))
        if geo:
            for c in geography.tick_day(geo, state):
                changes.append(c)
                add_notice(state, c["message"], "movement")
        for c in knowledge.spread_one_day(state, state.get("setting_pack"), rng=rng or random):
            changes.append(c)
            if not c.get("gm_only"):
                add_notice(state, c["message"], "rumour")
        changes += check_thresholds(state, dyn, resource_schema, system, rng=rng)
    changes.insert(0, {"kind": "time", "before": before_date, "after": state["date"], "days": days})
    return changes


def check_thresholds(state, dyn, resource_schema, system, rng=None):
    if not dyn:
        return []
    log = state.setdefault("dynamics_log", {})
    changes = []
    for t in dyn.get("thresholds") or []:
        tid = t["id"]
        entry = log.get(tid)
        if entry and t.get("once", True):
            continue
        if entry and t.get("cooldown_days"):
            gap = days_between(entry.get("last_date"), state.get("date"))
            if gap is not None and gap < t["cooldown_days"]:
                continue
        if not all_met(state, t.get("when")):
            continue
        log[tid] = {"last_date": state.get("date"), "count": (entry or {}).get("count", 0) + 1}
        cause = f"threshold: {tid}"
        fired = {"kind": "threshold", "id": tid, "message": t.get("message", "")}
        changes.append(fired)
        changes += apply_effects(state, dyn, resource_schema, t.get("effects"), cause)
        roll = t.get("roll")
        notice = t.get("message", "")
        if roll and system is not None:
            result = system.roll_check(state["character"], roll["attribute"], roll.get("skill"), roll["dc"])
            ok = result["degree"] in core.SUCCESS_DEGREES
            fired["check"] = {k: result[k] for k in ("attribute", "skill", "roll", "modifier", "total", "dc", "degree")}
            fired["check"]["description"] = roll.get("description", "")
            changes += apply_effects(state, dyn, resource_schema, roll.get("on_success" if ok else "on_failure"), cause)
            outcome_msg = roll.get("success_message" if ok else "failure_message")
            if outcome_msg:
                notice = f"{notice} {outcome_msg}".strip()
        if notice:
            add_notice(state, notice, cause)
    return changes


# ---------------------------------------------------------------- derived resources

def recompute_derived(state, dyn):
    changes = []
    for key, spec in ((dyn or {}).get("derived") or {}).items():
        if key not in state.get("resources", {}):
            continue
        source = spec.get("from_forces")
        units = []
        for m in (state.get("mechanics") or {}).values():
            if m.get("render_primitive") == "forces" and m.get("type_key") == source:
                units += [u for u in m["data"]["units"] if not spec.get("kinds") or u.get("kind") in spec["kinds"]]
        if not units:
            continue  # no tracker yet: the resource keeps its stored value
        value = operations.forces_alignment(units)
        before = state["resources"][key]
        if value != before:
            state["resources"][key] = value
            changes.append({"kind": "resource", "key": key, "before": before, "after": value,
                            "before_label": core.label_for_value(before), "after_label": core.label_for_value(value),
                            "requested_delta": value - before, "clamped": False, "cause": f"derived from {source}"})
    return changes


# ---------------------------------------------------------------- per-turn bookkeeping

def update_calendar(state, dyn):
    upcoming = []
    for ms in (dyn or {}).get("milestones") or []:
        gap = days_between(state.get("date"), ms["date"])
        if gap is not None and gap >= 0:
            upcoming.append({"label": ms["label"], "date": ms["date"], "days_until": gap})
    upcoming.sort(key=lambda x: x["days_until"])
    state["calendar"] = {"upcoming": upcoming[:3], "style": (dyn.get("calendar") or {}).get("style")}
    if dyn.get("hud"):
        state["hud"] = dyn["hud"]  # which numbers the game screen's top bar shows


def end_of_turn(state, dyn, resource_schema, system):
    if not dyn:
        return []
    changes = check_thresholds(state, dyn, resource_schema, system)
    changes += recompute_derived(state, dyn)
    update_calendar(state, dyn)
    keep_from = state.get("turn_number", 0) - 10
    state["world_notices"] = [n for n in state.get("world_notices", []) if n["turn"] >= keep_from]
    return changes


def apply_confrontation_effects(state, dyn, resource_schema, result):
    """confrontation_effects is a list of rules: {"outcome": "win"|"loss"|"any", "player_attacking"?:
    bool, "when"?: [cond...], "effects": {...}, "message"?}. Every matching rule applies - so a
    setting can attach political consequences to WHEN a fight happens, not just who won it."""
    rules = (dyn or {}).get("confrontation_effects") or []
    outcome = result["player_outcome"]
    won = outcome in operations.PLAYER_WIN_OUTCOMES
    lost = outcome in operations.PLAYER_LOSS_OUTCOMES
    if outcome == "no_fight":
        return []
    changes = []
    for rule in rules:
        want = rule.get("outcome", "any")
        if (want == "win" and not won) or (want == "loss" and not lost):
            continue
        if "player_attacking" in rule and bool(rule["player_attacking"]) != bool(result.get("player_side_attacking")):
            continue
        if not all_met(state, rule.get("when")):
            continue
        cause = f"confrontation ({want})"
        changes += apply_effects(state, dyn, resource_schema, rule.get("effects"), cause)
        if rule.get("message"):
            add_notice(state, rule["message"], cause)
            changes.append({"kind": "threshold", "id": "confrontation_consequence", "message": rule["message"]})
    return changes


def apply_world_deltas(state, dyn, resource_schema, deltas):
    changes = []
    max_delta = (dyn or {}).get("world_max_delta_per_update", 5)
    for key, delta in (deltas or {}).items():
        if key not in state.get("world", {}):
            changes.append({"kind": "ignored_unknown_category", "key": f"world.{key}"})
            continue
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            continue
        bounded = core.clamp(delta, -max_delta, max_delta)
        lo, hi = _range_for(dyn, resource_schema, f"world.{key}")
        before = state["world"][key]
        after = core.clamp(before + bounded, lo, hi)
        state["world"][key] = after
        changes.append({"kind": "world", "key": key, "before": before, "after": after,
                        "requested_delta": delta, "clamped": bounded != delta})
    return changes


# ---------------------------------------------------------------- prompt

def prompt_section(state, dyn, allow_time_advance=True):
    if not dyn:
        return ""
    lines = []
    style = (dyn.get("calendar") or {}).get("style")
    lines.append(f"Today is {state.get('date')}{f' ({style})' if style else ''}.")
    for ms in (state.get("calendar") or {}).get("upcoming", []):
        lines.append(f"- {ms['label']}: {ms['date']} ({ms['days_until']} days away)")

    world_lines = []
    for key, spec in (dyn.get("world") or {}).items():
        v = (state.get("world") or {}).get(key)
        world_lines.append(f"- {key} = {v} ({core.label_for_value(v) if v is not None else '?'}): {spec.get('description', '')}")

    pending, later = [], []
    for key, spec in (dyn.get("canon") or {}).items():
        status = (state.get("canon_events") or {}).get(key, {}).get("status")
        if status != "pending":
            continue
        # Beats two or more steps down a chain (their prerequisite beat is itself still blocked) are
        # only named, not explained - keeps the prompt about what's actually in reach.
        chained = [c for c in spec.get("requires") or [] if c["path"].startswith("canon.") and c.get("value") == "fired"]
        blocked_parent = any(
            resolve_path(state, c["path"]) != "fired" and canon_requirements(state, dyn, c["path"].split(".", 1)[1])
            for c in chained
        )
        if blocked_parent:
            later.append(key)
            continue
        if spec.get("type") == "locked" and spec.get("date"):
            pending.append(f"- {key}: LOCKED, happens on {spec['date']} regardless. {spec.get('summary', '')}")
        else:
            unmet = canon_requirements(state, dyn, key)
            hist = f" (historically {spec['historical_date']})" if spec.get("historical_date") else ""
            how = spec.get("how", "")
            gate = "requirements met - may fire when the fiction earns it" if not unmet else "not yet: needs " + "; ".join(unmet)
            pending.append(f"- {key}{hist}: {gate}. {how}".rstrip())

    recent_from = state.get("turn_number", 0) - NOTICE_TURNS_VISIBLE
    notices = [n for n in state.get("world_notices", []) if n["turn"] >= recent_from]

    out = ["# THE CLOCK", *lines]
    if allow_time_advance:
        out.append(
            "Time only moves when you move it: set days_elapsed in propose_state_update when a scene "
            "closes and time passes (overnight, a few days of organizing). Most turns are 0 or 1 day; at "
            f"most {(dyn.get('calendar') or {}).get('max_days_per_update', DEFAULT_MAX_DAYS)} at once. "
            "Every day that passes, the world below drifts on its own and locked dates arrive whether "
            "the player is ready or not - that pressure is the point, so don't freeze time to be kind, "
            "and don't skip days the player wanted to use."
        )
    else:
        out.append("This shared world's clock is advanced by the server, not by you - don't set days_elapsed.")
    if world_lines:
        out += ["", "# THE WORLD (moves on its own each day; nudge with world_deltas only for the player's direct impact, max ±"
                + str(dyn.get("world_max_delta_per_update", 5)) + ")", *world_lines]
    if notices:
        out += ["", "# JUST HAPPENED (application-fired events - work these into the narration now)"]
        out += [f"- [{n['date']}] {n['message']}" for n in notices]
    if pending:
        out += ["", "# CANON BEATS (the application enforces these gates - a canon_event_triggered that fails them is rejected)", *pending]
        if later:
            out.append("- Further down the line, once the beats above happen: " + ", ".join(later) + ".")
    derived = derived_keys(dyn)
    if derived:
        out += ["", "Derived resources (computed by the application from trackers - your deltas to them are ignored): "
                + ", ".join(sorted(derived)) + "."]
    return "\n".join(out)
