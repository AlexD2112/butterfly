"""
Curated 'mechanics' library - structured, persistent trackers (a parliament seat chart, a military
command-structure tree) an AI game master can create/update/expire mid-turn, on top of a setting's
own curated catalog (settings/<name>/mechanics_catalog.json - never an AI-invented type_key, same
convention engine.py already enforces for feats/liabilities).

Storage (2026-09-09, single-player pilot): mechanics live directly on the character's own state dict
(state["mechanics"], keyed by instance id) rather than a separate DB table. For single-player, state
IS the character's whole persisted blob already (see engine.py's own module docstring - it works on
"whatever state dict it's handed"), so this needs no new storage plumbing at all, and the frontend
gets active mechanics for free through the exact same build_state_digest() path every other state
field already goes through - no new API endpoint. Multiplayer's eventual equivalent needs real
server-scoped storage (shared across every character present, like rumor_broadcasts) - deliberately
out of scope for this pass; see NOTES.md.

Cadence: single-player has no background scheduler (scheduler.py's own docstring: multiplayer-tick-
only), so "how often should this update" is checked opportunistically once per player turn, against
state["turn_number"] (already incremented every turn) rather than wall-clock time.
due_for_review()/active_mechanics_prompt_section() below are pure, free, no-AI-call checks - only a
mechanic they flag as due costs anything, and only because it rides the SAME AI call the turn was
already making (via propose_mechanic_update), never a separate one. This is deliberate given the
$0.10 lifetime budget (db.py's DEFAULT_BUDGET_USD) - see NOTES.md for the fuller cost reasoning.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
SETTINGS_ROOT = ROOT / "settings"

_catalog_cache = {}


def load_catalog(setting_key):
    """Returns the setting's mechanics catalog dict, or None if this setting doesn't have one yet
    (every setting except petrograd_1917, until the catalog is extended past this pilot). Cached per
    process - catalog files are static content, same assumption engine.py's own system-module cache
    makes."""
    if setting_key not in _catalog_cache:
        path = SETTINGS_ROOT / setting_key / "mechanics_catalog.json"
        if not path.exists():
            _catalog_cache[setting_key] = None
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("_comment", None)
            _catalog_cache[setting_key] = raw
    return _catalog_cache[setting_key]


def tool_spec():
    """The one new tool this feature adds - see engine.run_turn, which appends this to a system's own
    TOOL_SPECS only when the active setting actually has a catalog (load_catalog returned non-None).
    Kept setting-agnostic on purpose (no catalog contents baked in here) - the catalog itself is
    injected into the system prompt separately, via catalog_prompt_section below."""
    return {
        "name": "propose_mechanic_update",
        "description": (
            "Create, update, or expire a 'mechanic' - a persistent structured tracker (e.g. a "
            "parliament seat chart, a military command-structure chart) shown to the player outside "
            "the narration, not just described in prose. Use 'create' when a mechanic from the "
            "AVAILABLE MECHANIC TYPES catalog below becomes contextually relevant for the first time - "
            "a listed canon trigger firing is a strong signal but not the only one; plain narrative "
            "relevance is enough even without one. "
            "Use 'update' when its underlying facts have materially changed on-screen - don't let it "
            "go stale once it exists. Use 'expire' when it's no longer narratively relevant (the body "
            "dissolved, was superseded, etc.) - expiry deletes it outright, it does not just fade out. "
            "Only ever use a type_key that appears in the catalog - never invent one. Don't create a "
            "second instance of a type_key that already has an active instance unless that specific "
            "catalog entry's own guidance calls for it (e.g. a genuine successor structure) - reuse "
            "the existing instance_id via 'update' instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "expire"]},
                "type_key": {"type": "string", "description": "Must exactly match a key from the setting's mechanics catalog."},
                "instance_id": {
                    "type": "string",
                    "description": "A short, stable, lowercase_with_underscores slug identifying this specific instance (e.g. 'petrograd_soviet_ispolkom'). Required for update/expire. For create, invent a new one - do not reuse an id already in CURRENTLY ACTIVE MECHANICS.",
                },
                "title": {"type": "string", "description": "Human-readable title. Optional on create (defaults to the catalog label)."},
                "data": {
                    "type": "object",
                    "description": "The mechanic's structured data, matching its type's data_shape in the catalog. Optional on create: omit it to start from the catalog's researched seed data, which is almost always right; pass it only if the campaign has already diverged from history. On update, send the full object. The application validates it: seat totals must add up, and a bloc's lean or a unit's loyalty can move at most 1 step per update (use the operations tools to resolve votes and confrontations - those move the numbers by dice).",
                },
                "review_after_turns": {
                    "type": "integer",
                    "description": "How many player turns from now this should next be reconsidered for an update or expiry check. Optional (defaults to the catalog's cadence on create; keeps the previous cadence on update).",
                },
                "reasoning": {"type": "string", "description": "Brief in-fiction reasoning for this specific change."},
            },
            "required": ["action", "type_key", "reasoning"],
        },
    }


def catalog_prompt_section(catalog):
    """Renders the AVAILABLE MECHANIC TYPES block for the system prompt - just what the AI needs to
    decide (label, when_relevant, canon triggers, render primitive, data shape), not the sourcing_note
    (that's for devs reading the catalog file, not turn-time context) and not seed_data (only relevant
    at the moment of creation - see seed_data_hint - dumping it into every single turn's prompt
    regardless would be pure waste)."""
    entries = []
    for type_key, entry in catalog.items():
        triggers = entry.get("canon_triggers") or []
        trigger_line = f"\n  Canon triggers to watch for: {'; '.join(triggers)}" if triggers else ""
        entries.append(
            f'- type_key: "{type_key}" — {entry["label"]}\n'
            f"  When relevant: {entry['when_relevant']}{trigger_line}\n"
            f"  Render as: {entry['render_primitive']}. Data shape: {json.dumps(entry['data_shape'])}"
        )
    return "\n".join(entries)


def seed_data_hint(catalog, type_key):
    """The catalog's researched starting data for one type, for the AI to use as its 'create' payload
    unless the campaign has already diverged. Looked up on demand rather than dumped into every turn's
    prompt - see catalog_prompt_section."""
    entry = catalog.get(type_key)
    return entry.get("seed_data") if entry else None


def render_compact(m):
    """A prompt-sized text rendering of one mechanic's data (2026-10-26) - the full JSON of a
    seeded order of battle alone was ~8k characters per turn."""
    data = m.get("data") or {}
    prim = m.get("render_primitive")
    if prim == "seat_chart":
        axis = data.get("lean_toward") or "the player's side"
        lines = [f"    {data.get('body_size')} seats; lean -3..+3 toward {axis}"
                 + (" (the player also votes)" if data.get("includes_player") else "")]
        for f in data.get("factions", []):
            lines.append(f"    · {f['name']}: {f['seats']} seats, lean {f.get('lean', 0):+d} — {f.get('note', '')}")
        lv = data.get("last_vote")
        if lv:
            lines.append(f"    last vote ({lv.get('date')}): \"{lv.get('motion')}\" {'PASSED' if lv.get('passed') else 'FAILED'} {lv.get('yes')}-{lv.get('no')}")
        return "\n".join(lines)
    if prim == "forces":
        lines = ["    loyalty -3..+3 toward the player's side; only |loyalty| >= 2 takes the field"]
        for u in data.get("units", []):
            lines.append(f"    · {u['id']}: {u['name']} [{u.get('kind', '')}] {u['strength']} men, readiness {u['readiness']}, "
                         f"loyalty {u['loyalty']:+d} — {u.get('note', '')}")
        le = data.get("last_engagement")
        if le:
            lines.append(f"    last engagement ({le.get('date')}): {le.get('objective')} - {le.get('player_outcome')}")
        return "\n".join(lines)
    return "    " + json.dumps(data, ensure_ascii=False)


def active_mechanics_prompt_section(state):
    """Renders the CURRENTLY ACTIVE MECHANICS block - what's already instantiated for this character,
    flagging any whose next_review_turn has already passed state["turn_number"]. Returns "" when there
    are none yet, so build_system_prompt can omit the section entirely rather than showing an empty
    list every turn."""
    mechanics = state.get("mechanics") or {}
    if not mechanics:
        return ""
    turn_number = state.get("turn_number", 0)
    lines = []
    for instance_id, m in mechanics.items():
        due = m.get("next_review_turn", 0) <= turn_number
        due_note = " — DUE FOR REVIEW this turn: consider whether it needs an update, or has expired." if due else ""
        lines.append(f'- instance_id: "{instance_id}" (type: {m["type_key"]}, {m.get("render_primitive")}) — {m["title"]}{due_note}')
        lines.append(render_compact(m))
    return "\n".join(lines)


# ==================== data validation (2026-10-26) ====================
# Until now the AI could write any `data` it liked into a mechanic, so a seat chart's totals or a
# regiment's loyalty were whatever the model last said. Each render primitive now has a validator,
# and updates to the numbers that decide outcomes (a bloc's lean, a unit's loyalty and strength) are
# rate-limited against the previous version, so the only way to move them far is over several turns
# of fiction - or through operations.py, where the app moves them itself from dice.

RENDER_PRIMITIVES = ("seat_chart", "org_tree", "text", "forces")
LEAN_RANGE = (-3, 3)
LOYALTY_RANGE = (-3, 3)
READINESS_RANGE = (1, 5)
MAX_LEAN_STEP_PER_UPDATE = 1
MAX_LOYALTY_STEP_PER_UPDATE = 1
MAX_SEAT_SHIFT_FRACTION = 0.10      # of body_size, summed across all blocs, per update
MAX_STRENGTH_GROWTH_FRACTION = 0.25  # per unit, per update (losses are unlimited)
MAX_NEW_UNIT_STRENGTH = 3000  # a unit the AI adds by update; bigger arrivals come from code (geography.py)
MAX_NEW_UNIT_READINESS = 3


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _as_int(v, default=None):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _slug(name):
    return "".join(ch if ch.isalnum() else "_" for ch in str(name).lower()).strip("_")


def validate_seat_chart(data, previous=None):
    """Returns (clean, errors, notes). errors reject the update; notes record clamping."""
    errors, notes = [], []
    factions = data.get("factions")
    if not isinstance(factions, list) or not factions:
        return None, ["seat_chart needs a non-empty factions list"], notes
    prev_by_name = {f["name"]: f for f in (previous or {}).get("factions", [])}
    clean_factions = []
    for f in factions:
        if not isinstance(f, dict) or not f.get("name"):
            errors.append("every faction needs a name")
            continue
        seats = _as_int(f.get("seats"))
        if seats is None or seats < 0:
            errors.append(f"faction '{f.get('name')}' needs a non-negative integer seat count")
            continue
        lean = _as_int(f.get("lean"), 0)
        lean = _clamp(lean, *LEAN_RANGE)
        prev = prev_by_name.get(f["name"])
        if prev is not None:
            prev_lean = prev.get("lean", 0)
            if abs(lean - prev_lean) > MAX_LEAN_STEP_PER_UPDATE:
                stepped = prev_lean + (MAX_LEAN_STEP_PER_UPDATE if lean > prev_lean else -MAX_LEAN_STEP_PER_UPDATE)
                notes.append(f"{f['name']} lean {prev_lean}->{lean} limited to {stepped}")
                lean = stepped
        clean = {"name": f["name"], "seats": seats, "lean": lean, "note": str(f.get("note", ""))}
        clean_factions.append(clean)
    if errors:
        return None, errors, notes
    total = sum(f["seats"] for f in clean_factions)
    body_size = _as_int(data.get("body_size"), total)
    if body_size != total:
        notes.append(f"body_size {body_size} set to the seat total {total}")
        body_size = total
    if previous:
        prev_seats = {f["name"]: f.get("seats", 0) for f in previous.get("factions", [])}
        shifted = sum(abs(f["seats"] - prev_seats.get(f["name"], 0)) for f in clean_factions)
        shifted += sum(v for k, v in prev_seats.items() if k not in {f["name"] for f in clean_factions})
        limit = max(2, round(previous.get("body_size", total) * MAX_SEAT_SHIFT_FRACTION * 2))  # moved seats count twice (out + in)
        if shifted > limit:
            return None, [f"seat changes move {shifted // 2} seats at once; at most {limit // 2} may change hands per update - shift them over several turns of fiction"], notes
    clean = {"body_size": body_size, "factions": clean_factions}
    if "last_vote" in data or (previous and "last_vote" in previous):
        clean["last_vote"] = (previous or {}).get("last_vote")  # only operations.call_vote writes this
    # Fixed properties of the body itself - kept from the previous version so an update can't quietly
    # redefine what the chart measures.
    for key in ("influence_resource", "lean_toward", "includes_player"):
        if previous is not None and key in previous:
            clean[key] = previous[key]
        elif previous is None and key in data:
            clean[key] = data[key]
    return clean, errors, notes


def validate_forces(data, previous=None):
    errors, notes = [], []
    units = data.get("units")
    if not isinstance(units, list) or not units:
        return None, ["forces needs a non-empty units list"], notes
    prev_by_id = {u["id"]: u for u in (previous or {}).get("units", [])}
    clean_units, seen = [], set()
    for u in units:
        if not isinstance(u, dict) or not u.get("name"):
            errors.append("every unit needs a name")
            continue
        uid = u.get("id") or _slug(u["name"])
        if uid in seen:
            errors.append(f"duplicate unit id '{uid}'")
            continue
        seen.add(uid)
        strength = _as_int(u.get("strength"))
        if strength is None or strength < 0:
            errors.append(f"unit '{u['name']}' needs a non-negative integer strength")
            continue
        loyalty = _clamp(_as_int(u.get("loyalty"), 0), *LOYALTY_RANGE)
        readiness = _clamp(_as_int(u.get("readiness"), 3), *READINESS_RANGE)
        prev = prev_by_id.get(uid)
        if prev is None and previous is not None:
            # A unit appearing mid-campaign through an update: capped, so the AI can't conjure an
            # army in one line. Merges, splits and larger raises go through reorganize_units.
            if strength > MAX_NEW_UNIT_STRENGTH:
                notes.append(f"new unit {u['name']} strength {strength} limited to {MAX_NEW_UNIT_STRENGTH} (use reorganize_units)")
                strength = MAX_NEW_UNIT_STRENGTH
            if readiness > MAX_NEW_UNIT_READINESS:
                notes.append(f"new unit {u['name']} readiness limited to {MAX_NEW_UNIT_READINESS}")
                readiness = MAX_NEW_UNIT_READINESS
        if prev is not None:
            if abs(loyalty - prev["loyalty"]) > MAX_LOYALTY_STEP_PER_UPDATE:
                stepped = prev["loyalty"] + (1 if loyalty > prev["loyalty"] else -1)
                notes.append(f"{u['name']} loyalty {prev['loyalty']}->{loyalty} limited to {stepped}")
                loyalty = stepped
            cap = int(prev["strength"] * (1 + MAX_STRENGTH_GROWTH_FRACTION)) + 50
            if strength > cap:
                notes.append(f"{u['name']} strength {strength} limited to {cap}")
                strength = cap
            if abs(readiness - prev["readiness"]) > 1:
                stepped = prev["readiness"] + (1 if readiness > prev["readiness"] else -1)
                notes.append(f"{u['name']} readiness limited to {stepped}")
                readiness = stepped
        clean_units.append({
            "id": uid, "name": u["name"], "strength": strength, "readiness": readiness,
            "loyalty": loyalty, "kind": str(u.get("kind", "")), "note": str(u.get("note", "")),
        })
    if errors:
        return None, errors, notes
    clean = {"units": clean_units}
    for key in ("loyalty_scale", "last_engagement"):
        if key in (previous or {}):
            clean[key] = previous[key]  # last_engagement is written only by operations.py
        elif key == "loyalty_scale" and data.get(key):
            clean[key] = data[key]
    return clean, errors, notes


def validate_org_tree(data, previous=None):
    def check(node, depth=0):
        if depth > 8:
            return "org_tree is nested too deeply (max 8 levels)"
        if not isinstance(node, dict) or not node.get("name"):
            return "every org_tree node needs a name"
        for child in node.get("children") or []:
            err = check(child, depth + 1)
            if err:
                return err
        return None
    err = check(data.get("root"))
    return (None, [err], []) if err else ({"root": data["root"]}, [], [])


def validate_text(data, previous=None):
    entries = data.get("entries")
    if not isinstance(entries, list) or not all(isinstance(e, dict) and e.get("body") for e in entries):
        return None, ["text needs an entries list of {heading, tag, body}"], []
    notes = []
    prev_entries = (previous or {}).get("entries") or []
    if len(entries) < len(prev_entries):
        notes.append("earlier ledger entries were dropped; the ledger should be appended to, not rewritten")
    return {"entries": entries}, [], notes


VALIDATORS = {
    "seat_chart": validate_seat_chart,
    "forces": validate_forces,
    "org_tree": validate_org_tree,
    "text": validate_text,
}


def validate_data(primitive, data, previous=None):
    if not isinstance(data, dict):
        return None, ["data must be an object"], []
    validator = VALIDATORS.get(primitive)
    if validator is None:
        return None, [f"unknown render primitive '{primitive}'"], []
    return validator(data, previous)


def split_old_blocs(state, catalog):
    """Saves from before a bloc was split in the catalog (split_blocs: {old name: [[new name, share,
    lean offset, note]...]}) get the same split, seats kept. 2026-11-03: the Petrograd Soviet's single
    400-seat Bolshevik bloc became two, so the fraction stops voting as one man."""
    changes = []
    for iid, m in (state.get("mechanics") or {}).items():
        rule = ((catalog or {}).get(m.get("type_key")) or {}).get("split_blocs") if isinstance((catalog or {}).get(m.get("type_key")), dict) else None
        if not rule or m.get("render_primitive") != "seat_chart":
            continue
        factions = m["data"]["factions"]
        for old, parts in rule.items():
            i = next((i for i, f in enumerate(factions) if f["name"] == old), None)
            if i is None:
                continue
            f = factions[i]
            seats = [int(round(f["seats"] * share)) for _, share, _, _ in parts]
            seats[-1] = f["seats"] - sum(seats[:-1])
            factions[i:i + 1] = [{"name": name, "seats": n, "lean": max(-3, min(3, f.get("lean", 0) + off)), "note": note}
                                 for (name, _, off, note), n in zip(parts, seats)]
            changes.append({"kind": "mechanic_updated", "instance_id": iid, "note": f"split '{old}'"})
    return changes


def ensure_always_present(state, catalog):
    """Creates any catalog type marked always_present that a save doesn't have yet (2026-11-03: the
    country-wide order of battle, added after some campaigns had started)."""
    changes = []
    for type_key, entry in (catalog or {}).items():
        if isinstance(entry, dict) and entry.get("always_present") and not any(
                m.get("type_key") == type_key for m in (state.get("mechanics") or {}).values()):
            changes += apply_mechanic_update(state, catalog, {"action": "create", "type_key": type_key, "instance_id": type_key})
    return changes


def apply_mechanic_update(state, catalog, update):
    """Mirrors systems/_shared/core.py's apply_state_update: validates/applies in place against
    state["mechanics"], returns a list of change dicts for debug_events. catalog is this setting's
    loaded mechanics_catalog.json - never None here, since engine.run_turn only offers this tool at
    all when a catalog exists for the active setting.

    2026-10-26: data is now validated per render primitive (see VALIDATORS), the numbers that decide
    votes and confrontations are rate-limited between updates, and a create with no data falls back
    to the catalog's researched seed_data instead of whatever the model improvises."""
    mechanics = state.setdefault("mechanics", {})
    action = update.get("action")
    type_key = update.get("type_key")
    instance_id = update.get("instance_id")

    if type_key not in catalog:
        return [{"kind": "mechanic_rejected", "reason": f"Unknown type_key '{type_key}' - not in this setting's catalog."}]

    if action == "create":
        if not instance_id:
            return [{"kind": "mechanic_rejected", "reason": "instance_id is required to create a mechanic."}]
        if instance_id in mechanics:
            return [{"kind": "mechanic_rejected", "reason": f"instance_id '{instance_id}' already exists - use action 'update' instead."}]
        title = update.get("title") or catalog[type_key].get("label")
        review_after = update.get("review_after_turns") or catalog[type_key].get("default_review_after_turns", 5)
        primitive = catalog[type_key]["render_primitive"]
        raw = update.get("data") or catalog[type_key].get("seed_data")
        if not raw:
            return [{"kind": "mechanic_rejected", "reason": "data is required (this type has no seed data to fall back on)."}]
        clean, errors, notes = validate_data(primitive, raw)
        if errors:
            return [{"kind": "mechanic_rejected", "reason": "; ".join(errors)}]
        turn_number = state.get("turn_number", 0)
        mechanics[instance_id] = {
            "type_key": type_key,
            "title": title,
            "render_primitive": primitive,
            "data": clean,
            "status": "active",
            "created_turn": turn_number,
            "updated_turn": turn_number,
            "review_after_turns": review_after,
            "next_review_turn": turn_number + review_after,
        }
        change = {"kind": "mechanic_created", "instance_id": instance_id, "type_key": type_key, "title": title,
                  "used_seed": not update.get("data")}
        if notes:
            change["adjusted"] = notes
        return [change]

    if not instance_id or instance_id not in mechanics:
        return [{"kind": "mechanic_rejected", "reason": f"No active mechanic with instance_id '{instance_id}'."}]

    if action == "update":
        turn_number = state.get("turn_number", 0)
        entry = mechanics[instance_id]
        notes = []
        if update.get("data"):
            # Validate against the instance's own stored primitive, not the catalog's current one,
            # so instances created before a catalog change keep working.
            clean, errors, notes = validate_data(entry["render_primitive"], update["data"], entry.get("data"))
            if errors:
                return [{"kind": "mechanic_rejected", "reason": "; ".join(errors)}]
            entry["data"] = clean
        if update.get("title"):
            entry["title"] = update["title"]
        entry["updated_turn"] = turn_number
        review_after = update.get("review_after_turns") or entry["review_after_turns"]
        entry["review_after_turns"] = review_after
        entry["next_review_turn"] = turn_number + review_after
        change = {"kind": "mechanic_updated", "instance_id": instance_id, "type_key": type_key}
        if notes:
            change["adjusted"] = notes
        return [change]

    if action == "expire":
        del mechanics[instance_id]
        return [{"kind": "mechanic_expired", "instance_id": instance_id, "type_key": type_key}]

    return [{"kind": "mechanic_rejected", "reason": f"Unknown action '{action}'."}]


def find_instances(state, primitive=None, type_key=None):
    return [
        (iid, m) for iid, m in (state.get("mechanics") or {}).items()
        if (primitive is None or m.get("render_primitive") == primitive)
        and (type_key is None or m.get("type_key") == type_key)
    ]
