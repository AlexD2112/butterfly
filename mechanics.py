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
                "title": {"type": "string", "description": "Human-readable title. Required for create."},
                "data": {
                    "type": "object",
                    "description": "The mechanic's structured data, matching its type's data_shape in the catalog. Required for create/update. On create, base this on the catalog's researched seed_data unless the campaign's actual state already diverges from history by this point.",
                },
                "review_after_turns": {
                    "type": "integer",
                    "description": "How many player turns from now this should next be reconsidered for an update or expiry check. Required for create; optional for update (keeps the previous cadence if omitted).",
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
        lines.append(f'- instance_id: "{instance_id}" (type: {m["type_key"]}) — {m["title"]}{due_note}')
    return "\n".join(lines)


def apply_mechanic_update(state, catalog, update):
    """Mirrors systems/_shared/core.py's apply_state_update: validates/applies in place against
    state["mechanics"], returns a list of change dicts for debug_events. catalog is this setting's
    loaded mechanics_catalog.json - never None here, since engine.run_turn only offers this tool at
    all when a catalog exists for the active setting."""
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
        if not update.get("title") or not update.get("data") or not update.get("review_after_turns"):
            return [{"kind": "mechanic_rejected", "reason": "title, data, and review_after_turns are all required to create a mechanic."}]
        turn_number = state.get("turn_number", 0)
        mechanics[instance_id] = {
            "type_key": type_key,
            "title": update["title"],
            "render_primitive": catalog[type_key]["render_primitive"],
            "data": update["data"],
            "status": "active",
            "created_turn": turn_number,
            "updated_turn": turn_number,
            "review_after_turns": update["review_after_turns"],
            "next_review_turn": turn_number + update["review_after_turns"],
        }
        return [{"kind": "mechanic_created", "instance_id": instance_id, "type_key": type_key, "title": update["title"]}]

    if not instance_id or instance_id not in mechanics:
        return [{"kind": "mechanic_rejected", "reason": f"No active mechanic with instance_id '{instance_id}'."}]

    if action == "update":
        turn_number = state.get("turn_number", 0)
        entry = mechanics[instance_id]
        if update.get("title"):
            entry["title"] = update["title"]
        if update.get("data"):
            entry["data"] = update["data"]
        entry["updated_turn"] = turn_number
        review_after = update.get("review_after_turns") or entry["review_after_turns"]
        entry["review_after_turns"] = review_after
        entry["next_review_turn"] = turn_number + review_after
        return [{"kind": "mechanic_updated", "instance_id": instance_id, "type_key": type_key}]

    if action == "expire":
        del mechanics[instance_id]
        return [{"kind": "mechanic_expired", "instance_id": instance_id, "type_key": type_key}]

    return [{"kind": "mechanic_rejected", "reason": f"Unknown action '{action}'."}]
