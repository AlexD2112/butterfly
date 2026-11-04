"""
Butterfly game engine: setting/system-agnostic state helpers, prompt construction, and the
provider-agnostic turn loop.

Three layers, each swappable independently:
  - A **system** (systems/<name>/) is a ruleset: dice, skills, feats, leveling - see
    systems/_shared/core.py for the shared math and SYSTEM_REGISTRY below for the roster.
  - A **setting** (settings/<name>/) is content built on a system: a premade character, NPCs,
    factions, canon timeline, resource categories - see SETTING_REGISTRY below.
  - A **character** (a specific play-through, premade or custom) is state data. Persisting that
    state across multiple characters/users is db.py's job, not this module's - engine.py works on
    whatever state dict it's handed, it doesn't own storage. load_state()/save_state() below are
    kept only as a single-save legacy path for loop.py's simple CLI mode.
"""

import importlib
import json
from pathlib import Path

import copy
import re
import time

import council
import dynamics
import geography
import couriers
import sittings
import debate
import knowledge
import mechanics
import negotiation
import npcs as npc_sheets_mod
import operations
from systems._shared import core as shared_core

ROOT = Path(__file__).parent
SETTINGS_ROOT = ROOT / "settings"
SAVE_FILE = ROOT / "save.json"  # legacy single-save path, used only by loop.py's simple CLI mode

SYSTEM_REGISTRY = {
    "red_admiral": {
        "module": "systems.red_admiral.mechanics",
        "rules_file": "systems/red_admiral/rules.md",
        "label": "Red Admiral — d20 Skills & Progression (revolutionary-era)",
    },
    "monarch": {
        "module": "systems.monarch.mechanics",
        "rules_file": "systems/monarch/rules.md",
        "label": "Monarch — d20 Skills & Progression (feudal/medieval)",
    },
    "apollo": {
        "module": "systems.apollo.mechanics",
        "rules_file": "systems/apollo/rules.md",
        "label": "Apollo — d20 Skills & Progression (Roman Republic)",
    },
}

SETTING_REGISTRY = {
    "petrograd_1917": {
        "system": "red_admiral",
        "label": "Petrograd, 1917",
        # sort_year (2026-09-03): a real chronological value (negative = BCE) for the frontend to
        # sort games/servers by period - dict insertion order below isn't chronological (Petrograd
        # comes first for historical/narrative reasons, not date order), and the label string alone
        # ("Rome, 59 BCE" vs "Petrograd, 1917") doesn't sort correctly as text either.
        "sort_year": 1917,
        "blurb": "Leon Trotsky, just out of prison on bail. It's September 4, 1917: Kornilov has failed, the Soviet is turning Bolshevik, and the Congress of Soviets is seven weeks away.",
        "premade_character_file": "new_campaign_state.json",
        "premade_character_name": "Leon Trotsky",
        "location_scale_hint": (
            "This entire campaign takes place within one city (Petrograd). A Location should be "
            "building/institution-scale - a specific building, factory floor, barracks, or street "
            "(e.g. 'The Smolny Institute', 'The Putilov Works', 'Nevsky Prospekt') - not a whole city "
            "district, and never the city itself."
        ),
    },
    "britannia_876": {
        "system": "monarch",
        "label": "Britannia, 876",
        "sort_year": 876,
        "blurb": "Alfred, King of the West Saxons. Guthrum's Vikings have a camp in the west and aren't going anywhere.",
        "premade_character_file": "new_campaign_state.json",
        "premade_character_name": "Alfred, King of the West Saxons",
        "location_scale_hint": (
            "This campaign follows a king moving across a kingdom under threat. A Location should be "
            "settlement/stronghold-scale - a royal hall, a fortified burh, a marsh hideout, an enemy "
            "camp (e.g. 'Athelney', 'Chippenham', 'The Viking Camp at Wareham') - broader than a "
            "single room, narrower than the whole of Wessex."
        ),
    },
    "rome_59_bce": {
        "system": "apollo",
        "label": "Rome, 59 BCE",
        "sort_year": -59,
        "blurb": "Julius Caesar, at the height of his power as Consul, with Pompey and Crassus watching his every move.",
        "premade_character_file": "new_campaign_state.json",
        "premade_character_name": "Julius Caesar",
        "location_scale_hint": (
            "This campaign's politics play out within one city (Rome) during Caesar's consulship. A "
            "Location should be building/district-scale - a specific place within the city (e.g. "
            "'The Forum', 'The Senate House', 'Caesar's Domus', 'The Campus Martius') - not the city "
            "itself, and not a province."
        ),
    },
    "crusades_1097": {
        "system": "monarch",
        "label": "The First Crusade, 1097",
        "sort_year": 1097,
        "blurb": "Bohemond of Taranto, marching on Antioch, having sworn an oath he's already thinking about breaking.",
        "premade_character_file": "new_campaign_state.json",
        "premade_character_name": "Bohemond of Taranto",
        "location_scale_hint": (
            "This campaign follows an army marching across a huge region (Anatolia and the Levant). "
            "A Location should be city/region-scale - a besieged city, a march through a named "
            "territory, a major camp (e.g. 'Nicaea', 'The March Through Anatolia', 'The Siege Camp at "
            "Antioch') - individual tents or rooms belong at the Subspace level, not the Location "
            "level."
        ),
    },
}
DEFAULT_SETTING_KEY = "petrograd_1917"

_system_module_cache = {}


def get_system(system_key):
    if system_key not in _system_module_cache:
        module_path = SYSTEM_REGISTRY[system_key]["module"]
        _system_module_cache[system_key] = importlib.import_module(module_path)
    return _system_module_cache[system_key]


def setting_dir(setting_key):
    return SETTINGS_ROOT / setting_key


def load_text(setting_key, path):
    return (setting_dir(setting_key) / path).read_text(encoding="utf-8")


def load_json(setting_key, path):
    return json.loads((setting_dir(setting_key) / path).read_text(encoding="utf-8"))


def load_premade_character_state(setting_key):
    """Returns a fresh copy of a setting's premade signature character's starting state.

    2026-11-03: the opening scene is now saved into the state itself (opening_text / opening_gm_note)
    at creation. Before, it lived only in opening_scene.md, was never a recorded turn, and so it
    vanished when a game was reopened - and the GM never saw it either. Custom and historical
    characters swap in the setting's opening_scene_other.md (see use_other_opening)."""
    info = SETTING_REGISTRY[setting_key]
    fresh = load_json(setting_key, info["premade_character_file"])
    fresh["opening_text"], fresh["opening_gm_note"] = opening_parts(setting_key, premade=True)
    fresh["_is_new"] = True
    return fresh


def _unwrap(text):
    """Opening files are hard-wrapped at ~100 columns; the game renders single newlines as line
    breaks, so join lines within a paragraph."""
    paras = [" ".join(line.strip() for line in p.splitlines() if line.strip()) for p in text.strip().split("\n\n")]
    return "\n\n".join(p for p in paras if p)


def opening_parts(setting_key, premade=True):
    """(player-facing text, GM-only note) from opening_scene.md / opening_scene_other.md. The GM
    note follows a '<!-- gm -->' line and is never shown to the player."""
    name = "opening_scene.md" if premade else "opening_scene_other.md"
    path = setting_dir(setting_key) / name
    if not path.exists():
        path = setting_dir(setting_key) / "opening_scene.md"
    raw = path.read_text(encoding="utf-8")
    text, _, note = raw.partition("<!-- gm -->")
    return _unwrap(text), " ".join(note.split())


def use_other_opening(state, setting_key):
    state["opening_text"], state["opening_gm_note"] = opening_parts(setting_key, premade=False)
    return state


def opening_for_existing(setting_key, state, created_at):
    """The opening a game was shown: saved in its state, or - for games created before openings were
    saved - the version that was live when it was created (settings/<key>/legacy_openings.json)."""
    if state.get("opening_text"):
        return state["opening_text"]
    path = setting_dir(setting_key) / "legacy_openings.json"
    if path.exists():
        for entry in json.loads(path.read_text(encoding="utf-8")).get("openings", []):
            if entry.get("until") is None or (created_at or 0) < entry["until"]:
                return _unwrap(entry["text"])
    return opening_parts(setting_key)[0]


def load_state():
    """Legacy single-save path for loop.py - always Petrograd 1917. server.py uses db.py instead,
    which supports many characters across many settings/systems concurrently."""
    if SAVE_FILE.exists():
        return json.loads(SAVE_FILE.read_text(encoding="utf-8"))
    return load_premade_character_state(DEFAULT_SETTING_KEY)


def save_state(state):
    state = dict(state)
    state.pop("_is_new", None)
    SAVE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def build_state_digest(state):
    """Generic by design - whatever shape the active system's state has, this just strips internal
    bookkeeping fields (leading underscore) rather than hand-picking keys, so it doesn't need to
    change when a system's state schema changes."""
    return {k: v for k, v in state.items() if not k.startswith("_")}


def build_system_prompt(state, setting_key, system_key, extra_context=None, allow_time_advance=True):
    """extra_context (2026-09-03): optional shared-scene text - see multiplayer.resolve_action, which
    is the only caller that passes it. This function/run_turn are otherwise the same single-player-
    shaped path multiplayer's mechanical action resolution deliberately reuses unchanged (see
    multiplayer.py's own docstring) - state, on its own, only ever describes ONE character. Without
    this, a multiplayer action was being resolved with zero awareness that anyone ELSE was even in the
    scene, let alone what they'd just done - a real bug (found live, via real Discord play): a
    character's action would narrate as if they were alone, even seconds after another character in
    the very same subspace had just done something the narration should plausibly have reacted to.
    None (single-player's own routes, and loop.py) leaves the prompt exactly as it always was."""
    system_info = SYSTEM_REGISTRY[system_key]
    system_rules = (ROOT / system_info["rules_file"]).read_text(encoding="utf-8")

    npcs = load_text(setting_key, "npcs.md")
    factions = load_text(setting_key, "factions.md")
    canon = load_text(setting_key, "canon_timeline.md")
    resource_schema = load_json(setting_key, "resource_schema.json")
    digest = build_state_digest(state)
    # Sections rendered elsewhere in this prompt are dropped from the raw state dump (2026-10-26) so
    # the same facts aren't paid for twice every turn: mechanics (compact rendering below), and - for
    # settings with dynamics.json - the world, the calendar and the canon beats (THE CLOCK section).
    # The campaign log is rendered as its own bounded section below instead of dumped whole - it grew
    # by ~400 characters every turn forever, inside every single call's prompt.
    digest.pop("campaign_log_summary", None)
    digest.pop("opening_text", None)
    digest.pop("opening_gm_note", None)
    digest.pop("memory_log", None)
    earlier_section = _earlier_campaign_section(state)
    digest.pop("negotiations", None)
    negotiation_section = "\n\n# " + negotiation.prompt_section(state) + "\n\n# " + knowledge.prompt_section(state, setting_key)
    mail_text = couriers.prompt_section(state, setting_key)
    if mail_text:
        negotiation_section += "\n\n# " + mail_text
    if any((m or {}).get("render_primitive") == "seat_chart" for m in (state.get("mechanics") or {}).values()):
        negotiation_section += "\n\n# " + debate.prompt_section(state)
    npc_section = ""
    if npc_sheets_mod.load(setting_key):
        digest.pop("npc_knowledge", None)
        npc_section = "\n\n# " + npc_sheets_mod.prompt_section(state, setting_key)
    geo = geography.load(setting_key)
    geography_section = ""
    if geo:
        digest.pop("geo", None)
        geography_section = "\n\n# " + geography.prompt_section(geo, state)
    if mechanics.load_catalog(setting_key):
        digest.pop("mechanics", None)
    if dynamics.load(setting_key):
        for key in ("world", "calendar", "world_notices", "dynamics_log"):
            digest.pop(key, None)
        canon = digest.pop("canon_events", {}) or {}
        digest["canon_events_fired"] = [k for k, v in canon.items() if v.get("status") == "fired"]

    # Mechanics (2026-09-09, see mechanics.py) - only sections at all if this setting has a curated
    # catalog; most settings won't yet (this shipped Petrograd-first - see NOTES.md). Kept as two
    # separate blocks (catalog vs currently-active) rather than one, since the catalog is static per
    # setting but the active list changes every turn - separating them makes it obvious to the model
    # which mechanics already exist (touch via 'update'/'expire') vs which are just available types
    # (touch via 'create').
    mechanics_catalog = mechanics.load_catalog(setting_key)
    mechanics_section = ""
    if mechanics_catalog:
        active = mechanics.active_mechanics_prompt_section(state)
        mechanics_section = f"""

# AVAILABLE MECHANIC TYPES (curated - never use a type_key not listed here)
{mechanics.catalog_prompt_section(mechanics_catalog)}

# CURRENTLY ACTIVE MECHANICS FOR THIS CHARACTER
{active if active else "(none yet)"}

Mechanics are structured trackers shown to the player outside the narration (e.g. a parliament seat
chart, a military command tree) - use propose_mechanic_update to create one from the catalog above
when it becomes contextually relevant, update one when its underlying facts have changed on-screen,
or expire one that's no longer relevant. Don't create one just because it's available - only when the
fiction actually calls for it. Address anything flagged DUE FOR REVIEW above before ignoring it again."""

    dyn = dynamics.load(setting_key)
    dynamics_section = ""
    if dyn:
        dynamics_section = "\n\n" + dynamics.prompt_section(state, dyn, allow_time_advance=allow_time_advance)

    operations_section = ""
    if mechanics_catalog:
        primitives = {e.get("render_primitive") for e in mechanics_catalog.values()}
        ops = []
        if "seat_chart" in primitives:
            ops.append("- Formal votes in a body with a seat_chart: call_vote. The application decides the result; you narrate it. If the player speaks first, pass speech_attribute/skill and the application rolls the speech too. Give every bloc a stance on the motion itself (bloc_stances): allies dislike plenty of things their leaders propose. A question put cold finds many undecided; real bodies debate, amend and vote again (reuse motion_key). Choosing people (a chairman, a presidium) is call_election, not call_vote: several candidates, split blocs, often no majority the first time. Bodies meet in sittings: only a few votes a night, then days until the next sitting; a major question (weight: major) goes on the agenda for the next sitting unless urgency (two-thirds) carries. Hours of speeches and the caucusing between sittings are the game - don't rush to the count. Stances and election support can be given for a whole party (e.g. 'Bolsheviks') or for each wing separately when they differ. Each speech in a debate is debate_speech (see PACE).")
        if "forces" in primitives:
            ops.append("- Winning over a unit in person (or talking a hostile one into staying in barracks): sway_unit. Never move a unit's loyalty more than a step by propose_mechanic_update when sway_unit is the honest description of what happened.")
            ops.append("- Any armed clash: resolve_confrontation. Units only fight at loyalty +2/+3 (for the player) or -2/-3 (against); waverers stay home. Narrate who refused - it's usually the story.")
            if geography.load(setting_key):
                ops.append("- Movement: before anyone goes anywhere that matters, move_unit (it times the trip and keeps positions honest). Fights happen at a place, among the units actually near it - move them in first, and narrate the march.")
        if ops:
            operations_section = "\n\n# OPERATIONS (resolved by the application - never narrate these outcomes on your own)\n" + "\n".join(ops)

    scene_section = f"""

# OTHER CHARACTERS PRESENT - RECENT ACTIVITY IN THIS SHARED SCENE (most recent last)
{extra_context}

This is a multiplayer scene - other real characters may be right there with you, and may have just
said or done something relevant. Stay aware of who else is present and what they just did; reference
them by name where it's actually natural (someone reacting to them, addressing them, being addressed
by them), rather than narrating this character as if they're alone when they demonstrably aren't.
Don't force a reference where the action genuinely has nothing to do with anyone else present.""" if extra_context else ""

    return f"""You are the game master for a historical RPG, running the {system_info['label']} system.
Follow the rules below exactly - especially: you never generate your own dice rolls (use
request_check), and all mechanical state changes go through propose_state_update or
propose_level_up, never implied by narration alone. Feats and liabilities may only ever come from
the system's curated catalogs (by id) - never invent one.

# SYSTEM RULES
{system_rules}

# SETTING: KEY NPCS
{npcs}

# SETTING: FACTIONS
{factions}

# SETTING: CANON TIMELINE
{canon}

# RESOURCE DEFINITIONS
{json.dumps(resource_schema, indent=2)}

# CURRENT STATE (authoritative - always reflects the real game state, not your memory of past turns)
{json.dumps(digest, indent=2)}
{mechanics_section}{operations_section}{dynamics_section}{geography_section}{npc_section}{negotiation_section}{earlier_section}
{scene_section}

Narrate in second person, present tense. Never resolve an uncertain action without calling
request_check first. Checks come back
with one of six degrees (critical_success, strong_success, success, success_at_cost, failure,
critical_failure) and a gm_instruction - honor the degree: a success_at_cost really does cost
something, a strong success really does go better than asked. When a check plainly risks or builds
one resource, name it as at_stake and the application moves it for you. If the current
state shows level_up_pending: true, remind the player at a natural pause and offer them the three
level-up options from the system rules before proceeding.
{council.VOICE_GUIDANCE}"""


# ==================== private intent (2026-11-03) ====================
# The player can write what their character intends or thinks in ((double parentheses)): it reaches
# the GM labelled as private, so no character "hears" it. (Characters reading the player's mind -
# answering aims he never voiced - was reported as the most unnatural thing in play.)
_INTENT = re.compile(r"\(\((.+?)\)\)", re.S)


def mark_private_intent(text):
    parts = [p.strip() for p in _INTENT.findall(text or "") if p.strip()]
    if not parts:
        return text
    visible = _INTENT.sub("", text).strip()
    visible = re.sub(r"\s{2,}", " ", visible)
    return (visible or "(no visible action)") + "\n\n[PRIVATE INTENT - for the GM only; no character sees or hears this: " + " / ".join(parts) + "]"


# ==================== narration guard (2026-11-03) ====================
# Tools whose results the player can only learn through narration: dice, votes, appeals, fights.
MAX_TOOL_ROUNDS = 6  # model calls with tools per turn, before the forced write-up
OUTCOME_TOOLS = {"request_check", "call_vote", "sway_unit", "resolve_confrontation", "negotiation_offer"}
NARRATE_NUDGE = (
    "(From the application, not the player: the player has seen the dice and the numbers but no "
    "narration. Write the scene now, in prose: what happens as a result of the outcome(s) above, "
    "honoring their degree. Don't roll again for the same action.)"
)
NARRATE_FIRST = (
    "Not shown yet: the player hasn't seen any narration of the outcome. Write the scene first, "
    "then call suggest_actions again."
)


MISSING_NARRATION = "(The game master didn't write this scene. The result above stands - carry on from it, or ask what happened.)"


# ==================== undoing a turn from its own record (2026-11-03) ====================
# Turns played before the resend feature have no saved "state before". Most of what a turn changes is
# recorded with its before-value (resources, world, trust, rivalry, XP, the date), so those turns can
# still be undone exactly by walking the record backwards. Anything that can't be put back exactly
# (a vote's seat chart, a fight, a level-up, a canon event...) refuses instead of guessing.
UNDOABLE = {"resource", "world", "relationship", "enmity", "enmity_auto", "xp", "time", "commitment", "flag", "divergence"}
HARMLESS = {"derived_resource_ignored", "time_ignored", "canon_event_rejected", "mechanic_rejected", "feat_rejected",
            "liability_rejected", "level_up_rejected", "skill_upgrade_rejected", "ignored_unknown_category"}


def _earlier_value(earlier_events, kind, key):
    """The last value an earlier turn gave a commitment or flag (None if it didn't exist yet)."""
    value = None
    for ev in earlier_events or []:
        for c in ev.get("changes") or []:
            if isinstance(c, dict) and c.get("kind") == kind and c.get("key") == key:
                value = c.get("value")
    return value


def undo_turn_from_events(state, debug_events, turn_number_played, earlier_events=None):
    """Puts state back to before a recorded turn. Returns None on success, or the reason it can't
    be done exactly (state is then left untouched - work on a copy)."""
    changes = []
    for ev in debug_events or []:
        changes += [c for c in (ev.get("changes") or []) if isinstance(c, dict)]
        if ev.get("type") in ("operation", "geography", "npc_sheet", "mechanic_update", "level_up", "negotiation", "knowledge", "mail"):
            return f"the turn included a {ev.get('operation') or ev['type'].replace('_', ' ')}, which can't be undone exactly"
    blocked = sorted({c.get("kind") for c in changes} - UNDOABLE - HARMLESS)
    if blocked:
        return "the turn changed things that can't be put back exactly: " + ", ".join(k.replace("_", " ") for k in blocked)
    for c in reversed(changes):
        k = c["kind"]
        if k == "resource":
            state["resources"][c["key"]] = c["before"]
        elif k == "world":
            state["world"][c["key"]] = c["before"]
        elif k == "relationship":
            state["relationships"][c["key"]]["trust"] = c["before"]
        elif k in ("enmity", "enmity_auto"):
            state["enmity"][c["key"]]["pips"] = c["before"]
        elif k == "xp":
            state["character"]["xp"] = max(0, state["character"]["xp"] - int(c.get("applied", 0)))
        elif k == "time":
            state["date"] = c["before"]
        elif k in ("commitment", "flag"):
            bucket = state.setdefault("commitments", {}) if k == "commitment" else state["character"].setdefault("flags", {})
            prior = _earlier_value(earlier_events, k, c["key"])
            if prior is None:
                bucket.pop(c["key"], None)
            else:
                bucket[c["key"]] = prior
        elif k == "divergence":
            log = state.get("divergence_log") or []
            if log and log[-1].get("note") == c.get("note"):
                log.pop()
                state["divergence_score"] = max(0, state.get("divergence_score", 0) - 1)
    for ev in debug_events or []:
        if ev.get("type") == "check" and ev.get("target_npc"):
            entry = (state.get("npc_knowledge") or {}).get(npc_sheets_mod.resolve(state, state.get("setting_pack"), ev["target_npc"]) or "")
            if entry and entry.get("encounters"):
                entry["encounters"] -= 1
    log = state.get("campaign_log_summary") or []
    if log and log[-1].get("turn") == turn_number_played:
        log.pop()
    state["world_notices"] = [n for n in state.get("world_notices") or [] if n.get("turn") != turn_number_played]
    state["turn_number"] = turn_number_played
    return None


def narration_missing(narration):
    return not (narration or "").strip() or narration.strip() == MISSING_NARRATION


def _outcome_brief(debug_events):
    """What the application already decided in a turn, in full enough detail to narrate from."""
    lines = []
    for ev in debug_events or []:
        t = ev.get("type")
        if t == "check":
            skill = f" / {ev['skill']}" if ev.get("skill") else ""
            lines.append(f"- {ev.get('description') or 'Check'}: {ev.get('attribute')}{skill}, rolled {ev.get('roll')} "
                         f"total {ev.get('total')} vs DC {ev.get('dc')} = {str(ev.get('degree', '')).replace('_', ' ')}."
                         + (f" {ev['gm_instruction']}" if ev.get("gm_instruction") else ""))
        elif t == "operation":
            res = ev.get("result") or {}
            lines.append(f"- {ev.get('operation')}: {json.dumps({k: v for k, v in res.items() if k in ('motion', 'passed', 'yes', 'no', 'player_outcome', 'unit', 'after', 'gm_instruction', 'absent')}, ensure_ascii=False)}")
    summary = summarize_outcomes(debug_events)
    if summary:
        lines.append(f"- Changes: {summary}")
    return "\n".join(lines) or "- (no rolls)"


def narrate_resolved_turn(provider, model, state, setting_key, system_key, conversation, player_input, debug_events,
                          usage_sink=None):
    """Writes the scene for a turn whose rolls and changes already happened but whose narration never
    did. One call, tools off, nothing in state changes: the outcome is given, not re-rolled."""
    system_prompt = build_system_prompt(state, setting_key, system_key)
    provider.append_user_message(conversation, (
        f"{player_input}\n\n[From the application: this move was already resolved - do not roll again "
        f"or change anything. The player saw these results but no scene:\n{_outcome_brief(debug_events)}\n"
        "Write the scene now, in prose, honoring each result's degree.]"))
    tool_specs = build_tool_specs(get_system(system_key), mechanics.load_catalog(setting_key),
                                  dynamics.load(setting_key), True, geo=geography.load(setting_key))
    try:
        result = provider.call(model, system_prompt, tool_specs, conversation, no_tools=True)
    except TypeError:
        result = provider.call(model, system_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    return scrub_narration("\n".join(result.text_parts).strip())


def _needs_narration(outcome_unnarrated, text_parts):
    """True when the turn would end with a result nobody narrated, or with no text at all."""
    return outcome_unnarrated or not "".join(text_parts).strip()


# ==================== suggested actions + narration scrub (2026-11-02) ====================

SUGGEST_ACTIONS_SPEC = {
    "name": "suggest_actions",
    "description": (
        "Call once, at the end of every reply, alongside the narration: 2-4 short next moves the "
        "player might make, shown as clickable choices under your narration (the player can always "
        "type something else). Write them in the player's voice as plain actions, 3-9 words each, "
        "specific to this exact moment and to named people/places in it - e.g. 'Tell Kamenev the "
        "fraction votes tonight', 'Go to the Pavlovsky barracks', 'Ask Natalia what she heard'. Make "
        "them genuinely different (bold, careful, social, sideways). Never generic ('Look around', "
        "'Continue'), never numbered, never mention mechanics."
    ),
    "parameters": {
        "type": "object",
        "properties": {"options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4}},
        "required": ["options"],
    },
}

MAX_SUGGESTION_CHARS = 70


def clean_suggestions(options):
    out = []
    for o in options or []:
        if not isinstance(o, str):
            continue
        o = " ".join(o.split()).strip(" -•*\"'").rstrip(".")
        o = re.sub(r"^\d+[.)]\s*", "", o)
        if o and len(o) <= MAX_SUGGESTION_CHARS and o.lower() not in {x.lower() for x in out}:
            out.append(o)
    return out[:4]


_TRAILING_PROMPTS = re.compile(
    r"(\n+|^|(?<=[.!?\"”]) )(\*\*)?(what (do|will|would) you (do|say)( next| now)?\??|"
    r"what('s| is) your (next )?(move|response|plan)\??|how do you respond\??|"
    r"the choice is yours\.?|the decision is yours\.?)(\*\*)?\s*$",
    re.IGNORECASE,
)


def scrub_narration(text):
    """Cheap, deterministic cleanup of the few chatbot tells a prompt can't fully stop: a closing
    'What do you do?' (the choice chips do that job now), markdown headers/bold, and curly quotes."""
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = _TRAILING_PROMPTS.sub("", text).rstrip()
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return text.strip()


# ==================== GM memory (2026-11-02) ====================
# How many recent turns the GM sees verbatim. Older turns survive only as one-line summaries in the
# system prompt (see _earlier_campaign_section). Kept small on purpose: each verbatim turn is a
# player line plus a few paragraphs, and the window is resent on every call.
MEMORY_TURNS = 8
EARLIER_SUMMARIES = 24
EARLIER_SUMMARY_CHARS = 220


def summarize_outcomes(debug_events):
    """One compact line of what the application decided in a turn - so a GM rebuilt from the
    transcript remembers that the vote passed 551-401, not just the prose about it."""
    bits = []
    for ev in debug_events or []:
        t = ev.get("type")
        if t == "check":
            skill = f"/{ev['skill']}" if ev.get("skill") else ""
            bits.append(f"{ev.get('attribute')}{skill} check: {str(ev.get('degree', '')).replace('_', ' ')}")
        elif t == "operation":
            r = ev.get("result") or {}
            if r.get("error"):
                continue
            if ev.get("operation") == "call_vote":
                bits.append(f"vote \"{r.get('motion', '')}\" {'passed' if r.get('passed') else 'failed'} {r.get('yes')}-{r.get('no')}")
            elif ev.get("operation") == "sway_unit":
                bits.append(f"{r.get('unit')} loyalty {r.get('loyalty_before')}->{r.get('loyalty_after')}")
            elif ev.get("operation") == "resolve_confrontation":
                bits.append(f"clash at {r.get('objective')}: {str(r.get('player_outcome', '')).replace('_', ' ')}")
        for c in ev.get("changes") or []:
            k = c.get("kind")
            if k == "resource" and c.get("after") != c.get("before"):
                bits.append(f"{c['key']} {c['before']}->{c['after']}")
            elif k == "time":
                bits.append(f"{c.get('days')} day(s) passed, now {c.get('after')}")
            elif k == "canon_event":
                bits.append(f"canon: {c['key']} fired")
            elif k == "threshold" and c.get("message"):
                bits.append(f"event: {c['message'][:120]}")
    # de-duplicate while keeping order
    seen, out = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return "; ".join(out[:14])


def rebuild_conversation(provider, turns, memory_turns=MEMORY_TURNS, opening=None, opening_note=None):
    """A fresh provider conversation holding the last few turns of a transcript as plain
    user/assistant text pairs (no tool calls - every provider accepts that shape). While the game
    is young enough that the opening is still inside the memory window, it goes first, so the GM
    knows what scene the player's first moves are answering."""
    conversation = provider.new_conversation()
    if opening and len(turns or []) < memory_turns:
        provider.append_user_message(conversation, "(The game begins. Show me the opening scene.)")
        note = f"\n\n[GM note, not shown to the player: {opening_note}]" if opening_note else ""
        conversation.append({"role": "assistant", "content": opening + note})
    for t in (turns or [])[-memory_turns:]:
        player = (t.get("player_input") or "").strip()
        narration = (t.get("narration") or "").strip()
        if narration == MISSING_NARRATION:
            # don't show the model a turn where "the GM" wrote nothing - it copies the pattern
            narration = "[This turn's scene was never written; only the rules outcome below is known.]"
        if not player or not narration:
            continue
        try:
            events = json.loads(t.get("debug_events_json") or "[]")
        except (TypeError, ValueError):
            events = []
        outcome = summarize_outcomes(events)
        provider.append_user_message(conversation, mark_private_intent(player))
        conversation.append({"role": "assistant", "content": narration + (f"\n\n[Rules resolved: {outcome}]" if outcome else "")})
    return conversation


def _earlier_campaign_section(state):
    log = state.get("campaign_log_summary") or []
    older = log[:-MEMORY_TURNS] if len(log) > MEMORY_TURNS else []
    if not older:
        return ""
    lines = []
    for entry in older[-EARLIER_SUMMARIES:]:
        text = " ".join(str(entry.get("summary", "")).split())
        if len(text) > EARLIER_SUMMARY_CHARS:
            text = text[:EARLIER_SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."
        lines.append(f"- turn {entry.get('turn')}: {text}")
    skipped = len(older) - min(len(older), EARLIER_SUMMARIES)
    head = f"(and {skipped} earlier turns not shown)\n" if skipped else ""
    return "\n\n# EARLIER IN THE CAMPAIGN (the last few turns are in the conversation itself)\n" + head + "\n".join(lines)


def run_turn(provider, model, state, setting_key, system_key, conversation, user_input, usage_sink=None, budget_ok=None, council_mode=False, model_key=None, extra_context=None, allow_time_advance=True, suggestions=False):
    """Returns (narration, debug_events).

    extra_context: see build_system_prompt - only ever passed by multiplayer.resolve_action, for
    shared-scene awareness of other present characters' recent activity.

    usage_sink, if given, is a list that each API call's TurnResult.usage dict gets appended to -
    callers that care about cost (server.py) can read it after this returns, or inspect it live via
    budget_ok. This keeps engine.py agnostic of accounts/billing (per this module's docstring, it
    doesn't own storage) while still letting a caller meter spend.

    budget_ok, if given, is a zero-arg callable checked after each API call (i.e. after usage_sink
    has the latest entry) - returning False ends the turn early rather than letting the model's
    tool-calling loop keep making calls past budget. A single user turn can trigger several API
    calls (each request_check/propose_state_update round-trip is its own call), so checking only
    once before the turn starts wouldn't catch a runaway multi-call turn.

    council_mode + model_key: if council_mode is true and the turn's outcome trips
    council.should_convene_council's stakes gate, the baseline narration below gets replaced by
    council.run_council's synthesized version before this returns (see council.py). model_key (the
    MODEL_REGISTRY key, e.g. "sonnet" - distinct from `model`, the raw model id string) is needed to
    pick a cheaper same-provider model for the council's judges; without it (the loop.py CLI path
    never passes it) council_mode is silently a no-op regardless of its value.

    suggestions (2026-11-02): offer the suggest_actions tool - 2-4 short next moves the frontend
    shows as clickable choices. Single-player only; the multiplayer UI has no place for them.

    allow_time_advance (2026-10-26): single-player campaigns keep their own calendar, moved by the GM
    through propose_state_update's days_elapsed (see dynamics.py). Multiplayer passes False - a shared
    server's date belongs to scheduler.py's ticks, and one player's turn must not move it.
    """
    system = get_system(system_key)
    resource_schema = load_json(setting_key, "resource_schema.json")
    mechanics_catalog = mechanics.load_catalog(setting_key)
    dyn = dynamics.load(setting_key)
    dynamics.ensure_initialized(state, dyn)
    geo = geography.load(setting_key)
    if mechanics_catalog:
        mechanics.ensure_always_present(state, mechanics_catalog)
        mechanics.split_old_blocs(state, mechanics_catalog)
    geography.ensure_initialized(state, geo)
    if dyn:
        dynamics.recompute_derived(state, dyn)
        dynamics.update_calendar(state, dyn)
    tool_specs = build_tool_specs(system, mechanics_catalog, dyn, allow_time_advance, geo=geo,
                                  npc_sheets=bool(npc_sheets_mod.load(setting_key)))
    if suggestions:
        tool_specs.append(SUGGEST_ACTIONS_SPEC)
    provider.append_user_message(conversation, mark_private_intent(user_input))
    system_prompt = build_system_prompt(
        state, setting_key, system_key, extra_context=extra_context, allow_time_advance=allow_time_advance,
    )

    text_parts = []
    debug_events = []
    # Narration guard (2026-11-03): a turn could end with the dice rolled and the numbers moved but
    # no scene written - the model's last response was just tools, or empty. outcome_unnarrated is
    # set when a roll/vote/fight result goes back to the model and cleared by any text after it;
    # if the turn is about to end with it still set, the model is sent back once to write the scene.
    outcome_unnarrated = False
    nudged = False
    rounds = []
    started = time.monotonic()
    while True:
        if len(rounds) >= MAX_TOOL_ROUNDS:
            # A turn could loop on tool calls with no limit (2026-11-03: a resend took ~5 minutes).
            # Stop here; the no-tools call below still makes sure a scene gets written.
            debug_events.append({"type": "gm_retry", "reason": f"stopped after {MAX_TOOL_ROUNDS} rounds of tool calls", "wrote": False})
            outcome_unnarrated = True
            break
        t0 = time.monotonic()
        turn_result = provider.call(model, system_prompt, tool_specs, conversation)
        rounds.append({"seconds": round(time.monotonic() - t0, 1), "tools": [tc["name"] for tc in turn_result.tool_calls],
                       "text": bool("".join(turn_result.text_parts).strip())})
        provider.append_assistant_turn(conversation, turn_result)
        text_parts.extend(turn_result.text_parts)
        if any(t.strip() for t in turn_result.text_parts):
            outcome_unnarrated = False

        if usage_sink is not None and turn_result.usage is not None:
            usage_sink.append(turn_result.usage)

        if budget_ok is not None and not budget_ok():
            debug_events.append(
                {
                    "type": "budget_exhausted",
                    "description": "Turn ended early: usage budget ran out mid-turn.",
                }
            )
            break

        if not turn_result.tool_calls:
            break

        results = []
        for tc in turn_result.tool_calls:
            if tc["name"] == "suggest_actions":
                options = clean_suggestions((tc.get("input") or {}).get("options"))
                debug_events = [e for e in debug_events if e.get("type") != "suggestions"]
                if options:
                    debug_events.append({"type": "suggestions", "options": options})
                unnarrated = _needs_narration(outcome_unnarrated, text_parts)
                results.append({"id": tc["id"], "content": NARRATE_FIRST if unnarrated else "Shown to the player."})
                continue
            content, events = handle_tool_call(
                tc, state, system, resource_schema, mechanics_catalog, dyn, allow_time_advance, geo=geo,
                setting_key=setting_key,
            )
            debug_events.extend(events)
            results.append({"id": tc["id"], "content": content})
            if tc["name"] in OUTCOME_TOOLS:
                outcome_unnarrated = True

        provider.append_tool_results(conversation, turn_result, results)
        # suggest_actions is the closing move of a turn: when it's the only thing the model asked
        # for, there's nothing to feed back, so skip the extra round-trip (and its latency/cost) -
        # unless the scene itself still hasn't been written (its tool result above says so).
        if all(tc["name"] == "suggest_actions" for tc in turn_result.tool_calls):
            if _needs_narration(outcome_unnarrated, text_parts) and not nudged:
                nudged = True
                continue  # its tool result told the model to write the scene; give it the chance
            break

    debug_events.append({"type": "timing", "seconds": round(time.monotonic() - started, 1), "rounds": rounds})
    print(f"[turn] {setting_key} {len(rounds)} model call(s), {time.monotonic() - started:.1f}s: "
          + " | ".join(f"{r['seconds']}s {','.join(r['tools']) or ('text' if r['text'] else 'empty')}" for r in rounds), flush=True)

    # Still no scene: one last call with tools switched off, so the only thing the model CAN do is
    # write (2026-11-03 - a nudge the model could answer with more tool calls wasn't enough).
    if _needs_narration(outcome_unnarrated, text_parts) and (budget_ok is None or budget_ok()):
        provider.append_user_message(conversation, NARRATE_NUDGE)
        try:
            final = provider.call(model, system_prompt, tool_specs, conversation, no_tools=True)
        except TypeError:  # a provider without the no_tools switch
            final = provider.call(model, system_prompt, tool_specs, conversation)
        provider.append_assistant_turn(conversation, final)
        if usage_sink is not None and final.usage is not None:
            usage_sink.append(final.usage)
        text_parts.extend(final.text_parts)
        if final.tool_calls:  # shouldn't happen with tools off; answer them so the history stays valid
            provider.append_tool_results(conversation, final, [{"id": tc["id"], "content": "Ignored: write the scene."} for tc in final.tool_calls])
        debug_events.append({"type": "gm_retry", "reason": "no narration after the outcome; asked again with tools off",
                             "wrote": bool("".join(final.text_parts).strip())})

    if dyn:
        closing = dynamics.end_of_turn(state, dyn, resource_schema, system)
        if closing:
            debug_events.append({"type": "world", "reasoning": "End of turn: the world moves.", "changes": closing})

    narration = scrub_narration("\n".join(text_parts).strip())
    if not narration and any(e.get("type") in ("check", "operation") for e in debug_events):
        narration = MISSING_NARRATION

    if council_mode and model_key and model_key not in council.SKIP_COUNCIL_FOR_MODEL_KEYS:
        # Runs on every turn as of 2026-08-20 - no gate here anymore, the router inside run_council
        # decides panel SIZE (>= council.MIN_JUDGES), not whether to run at all. See council.py's
        # module docstring for the history and NOTES.md for the cost tradeoff this implies.
        # SKIP_COUNCIL_FOR_MODEL_KEYS (added 2026-08-21) is a real exception to "runs on every
        # turn" - see that constant's comment for why local-ollama specifically can't afford it.
        narration, council_log = council.run_council(
            provider, model, model_key, system_prompt, user_input, state, debug_events, narration,
            usage_sink=usage_sink, budget_ok=budget_ok,
        )
        debug_events.append(council_log)

    state["campaign_log_summary"].append({"turn": state["turn_number"], "summary": narration[:400]})
    state["turn_number"] += 1
    return narration, debug_events


# ==================== tool handling (2026-10-26) ====================
# Split out of run_turn when the tool set grew past the original four. Each handler returns
# (content_for_the_model, debug_events).

def build_tool_specs(system, mechanics_catalog, dyn, allow_time_advance, geo=None, npc_sheets=False):
    specs = copy.deepcopy(system.TOOL_SPECS)
    if npc_sheets:
        for spec in specs:
            if spec["name"] == "request_check":
                spec["parameters"]["properties"]["actor"] = {
                    "type": "string",
                    "description": "Who makes the attempt, if not the player: an NPC with a sheet (Kamenev presenting the motion, Natalia carrying a message). Their sheet rolls; the consequences land on them."}
                spec["parameters"]["properties"]["actors"] = {
                    "type": "array", "items": {"type": "string"},
                    "description": "Several people attempting it together ('player' plus NPC ids). Use with combine."}
                spec["parameters"]["properties"]["combine"] = {
                    "type": "string", "enum": ["best", "each"],
                    "description": "best: one effort, the best roll carries it (they help each other). each: everyone rolls and answers for their own result (all of them must get past the sentries)."}
                spec["parameters"]["properties"]["opposed_by"] = {
                    "type": "string",
                    "description": "When target_npc has a sheet: the skill or attribute they resist with (default: the same skill the player uses, or their Resolve when being talked round). 'none' if they aren't actively resisting. The application sets the DC from their sheet."}
                spec["parameters"]["properties"]["ask"] = {
                    "type": "string", "enum": ["minor", "significant", "major"],
                    "description": "When target_npc has a sheet: how much the player is asking of them. minor: a favour, information, a hearing. significant: a real commitment - a vote, a meeting, a risk. major: changing sides, an alliance, turning on their own people. Raises the DC; a major ask is never won by one roll."}
        specs.append(npc_sheets_mod.CREATE_SPEC)
    for spec in specs:
        if spec["name"] != "propose_state_update":
            continue
        props = spec["parameters"]["properties"]
        if dyn and allow_time_advance:
            max_days = (dyn.get("calendar") or {}).get("max_days_per_update", dynamics.DEFAULT_MAX_DAYS)
            props["days_elapsed"] = {
                "type": "integer",
                "description": f"Days that pass as this beat closes (0-{max_days}). The world drifts and dated events fire for each day.",
            }
        if dyn and dyn.get("world"):
            props["world_deltas"] = {
                "type": "object",
                "description": "The player's direct impact on the world tracks in THE WORLD, e.g. {\"government_stability\": -3}. Small (the application clamps it); the world already drifts on its own.",
            }
    if mechanics_catalog:
        specs.append(mechanics.tool_spec())
        primitives = {e.get("render_primitive") for e in mechanics_catalog.values()}
        if "seat_chart" in primitives:
            vote = operations.vote_tool_spec()
            vote["parameters"]["properties"].update(sittings.spec_properties())
            vote["parameters"]["properties"]["speech_by"] = {
                "type": "string", "description": "Who gives the speech, if not the player (e.g. 'kamenev' when the player lets him present it). Their sheet rolls it."}
            specs.append(vote)
            specs.append(operations.election_tool_spec())
            specs.append(debate.SPEC)
        if "forces" in primitives:
            specs.append(operations.sway_tool_spec())
            conf = operations.confrontation_tool_spec()
            if geo:
                conf["parameters"]["properties"]["place"] = {
                    "type": "string", "description": "Where it happens - a place from GEOGRAPHY. Units more than a short march away (or cut off by a raised bridge or a picket) are left out by the application."}
                conf["parameters"]["required"].append("place")
            specs.append(conf)
    if geo:
        specs += geography.tool_specs()
    specs += negotiation.tool_specs()
    specs.append(knowledge.TOOL_SPEC)
    if geo and npc_sheets:
        specs += couriers.TOOL_SPECS
    return specs


def _resource_value(state, key):
    if not key:
        return None
    return (state.get("resources") or {}).get(key)


def _apply_at_stake(state, resource_schema, dyn, key, degree, cause):
    """Moves one resource by the fixed per-degree amount in core.AT_STAKE_DELTAS, bypassing the GM."""
    if not key or key not in (state.get("resources") or {}) or key in dynamics.derived_keys(dyn):
        return []
    delta = shared_core.AT_STAKE_DELTAS.get(degree, 0)
    if not delta:
        return []
    lo, hi = (resource_schema["resources"].get(key) or {}).get("range", [0, 100])
    before = state["resources"][key]
    after = shared_core.clamp(before + delta, lo, hi)
    state["resources"][key] = after
    return [{"kind": "resource", "key": key, "before": before, "after": after,
             "before_label": shared_core.label_for_value(before), "after_label": shared_core.label_for_value(after),
             "requested_delta": delta, "clamped": False, "cause": cause}]


def _post_change_bookkeeping(state, dyn, resource_schema, system):
    """After anything that moves numbers: re-derive computed resources and check thresholds, so a
    threshold crossed mid-turn fires in time for the GM to narrate it this turn."""
    if not dyn:
        return []
    return dynamics.recompute_derived(state, dyn) + dynamics.check_thresholds(state, dyn, resource_schema, system)


def _notices_for(changes):
    lines = [c["message"] for c in changes if c.get("kind") == "threshold" and c.get("message")]
    lines += [f"canon event fired: {c['key']}" for c in changes if c.get("kind") == "canon_event"]
    lines += [c["message"] for c in changes if c.get("kind") == "movement" and c.get("message")]
    lines += [c["message"] for c in changes if c.get("kind") in ("knowledge_spread", "knowledge_public", "mail_delivered") and c.get("message")]
    return lines


def _npc_key(state, setting_key, who):
    """An NPC id for negotiations: a sheet's key if they have one, else a relationship key, else a slug."""
    key = npc_sheets_mod.resolve(state, setting_key, who) if setting_key else None
    if key:
        return key, npc_sheets_mod.all_sheets(state, setting_key)[key]["name"]
    slug = re.sub(r"[^a-z0-9]+", "_", str(who or "").lower()).strip("_")
    rels = state.get("relationships") or {}
    key = slug if slug in rels else next((k for k in rels if slug.endswith("_" + k) or k.endswith("_" + slug)), slug)
    return key, str(who).strip() or key


def _handle_negotiation(name, inp, state, system, resource_schema, dyn, setting_key):
    """Returns (content, events) for the four negotiation tools (2026-11-03, see negotiation.py)."""
    events = []
    if not inp.get("npc"):
        return json.dumps({"error": "npc is required."}), events
    key, npc_name = _npc_key(state, setting_key, inp["npc"])
    if name == "open_negotiation":
        result = negotiation.open_negotiation(state, key, npc_name, inp)
    elif name == "negotiation_update":
        result = negotiation.update(state, key, inp)
    elif name == "close_negotiation":
        result = negotiation.close(state, key, inp)
    else:
        base_dc, why = (None, None)
        if setting_key:
            base_dc, why = npc_sheets_mod.opposed_dc(state, setting_key, system, key, inp.get("attribute") or "presence",
                                                     inp.get("skill"))
            npc_sheets_mod.record_encounter(state, setting_key, key)
        if base_dc is None:
            base_dc = 12
        result, roll, changes = negotiation.offer(state, key, inp, system, base_dc)
        if roll:
            roll = dict(roll, dc_source=(why or "no sheet: base 12") + "; " + result["dc_note"])
            events.append({"type": "check", "description": inp.get("offer") or f"Offer to {npc_name}", "at_stake": inp.get("at_stake"), **roll})
            moved = result["moved"]
            stake = inp.get("at_stake")
            if stake and stake in (state.get("resources") or {}) and stake not in dynamics.derived_keys(dyn):
                delta = max(-4, min(4, 2 * moved)) + (4 if result["agreed"] else 0)
                if delta:
                    lo, hi = ((resource_schema or {}).get("resources", {}).get(stake) or {}).get("range", [0, 100])
                    before = state["resources"][stake]
                    state["resources"][stake] = shared_core.clamp(before + delta, lo, hi)
                    changes.append({"kind": "resource", "key": stake, "before": before, "after": state["resources"][stake],
                                    "before_label": shared_core.label_for_value(before),
                                    "after_label": shared_core.label_for_value(state["resources"][stake]),
                                    "requested_delta": delta, "clamped": False, "cause": "negotiation"})
            changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
            if changes:
                events.append({"type": "state_update", "reasoning": f"Negotiation with {npc_name}.", "changes": changes})
            result["roll"] = {k: roll[k] for k in ("roll", "total", "dc", "degree")}
            # what's offered behind a closed door is private: only this person knows it, for now
            who = state.get("character", {}).get("name", "The player").split(" (")[0]
            knowledge.record(state, setting_key, f"{who} offered {npc_name}: {inp.get('offer', '')}", [key], "private")
            if result["agreed"]:
                topic = ((state.get("negotiations") or {}).get(key) or {}).get("topic", "terms")
                knowledge.record(state, setting_key, f"{npc_name} agreed terms with {who} on {topic}", [key], "private")
    if "error" not in result:
        n = (state.get("negotiations") or {}).get(key) or {}
        events.append({"type": "negotiation", "operation": name, "npc": npc_name, "result": result,
                       "position": n.get("position"), "goal": n.get("goal"), "status": n.get("status"),
                       "demands": copy.deepcopy(n.get("demands"))})  # a snapshot: later offers mark them met
    return json.dumps(result), events


def handle_tool_call(tc, state, system, resource_schema, mechanics_catalog, dyn, allow_time_advance, geo=None,
                     setting_key=None):
    name, inp = tc["name"], dict(tc["input"] or {})
    events = []

    if name in ("send_message", "npc_message", "npc_location") and setting_key:
        fn = {"send_message": couriers.send, "npc_message": couriers.npc_send, "npc_location": couriers.set_location}[name]
        result = fn(state, setting_key, inp)
        if "error" not in result:
            events.append({"type": "mail", "operation": name, "result": result})
        return json.dumps(result), events

    if name == "record_knowledge":
        result = knowledge.record(state, setting_key, inp.get("text"), inp.get("witnesses"), inp.get("secrecy") or "private",
                                  inp.get("fact_id"))
        if "error" not in result:
            events.append({"type": "knowledge", "result": result})
        return json.dumps(result), events

    if name in ("open_negotiation", "negotiation_offer", "negotiation_update", "close_negotiation"):
        return _handle_negotiation(name, inp, state, system, resource_schema, dyn, setting_key)

    if name == "create_npc_sheet" and setting_key:
        result = npc_sheets_mod.create(state, setting_key, system, inp)
        if "error" not in result:
            events.append({"type": "npc_sheet", "reasoning": inp.get("reasoning") or inp.get("role") or "", "result": result})
        return json.dumps(result), events

    if geo and name in ("route_info", "move_unit", "set_route", "reorganize_units"):
        result, changes = geography.handle(geo, state, name, inp)
        if name != "route_info" or "error" in result:
            events.append({"type": "geography", "operation": name, "reasoning": inp.get("reasoning") or inp.get("reason") or "",
                           "result": result, "changes": changes})
        return json.dumps(result), events

    if name == "request_check":
        skill_attr = next((a for a, sk in system.SKILLS.items() if inp.get("skill") in sk), None)
        if inp.get("target_npc") and skill_attr in ("presence", "oration"):
            nkey, nname = _npc_key(state, setting_key, inp["target_npc"])
            if negotiation.open_for(state, nkey):
                return json.dumps({"error": f"A negotiation with {nname} is open: make this a negotiation_offer (list the demands it meets or dodges) instead of a separate check."}), events
        actors = [a for a in (inp.get("actors") or [inp.get("actor") or "player"]) if a] or ["player"]
        actors = list(dict.fromkeys("player" if str(a).lower() in ("player", "me", "you") else a for a in actors))
        if any(a != "player" for a in actors) and not setting_key:
            return json.dumps({"error": "Only the player can roll in this setting."}), events
        base_dc = inp["dc"]
        rolls, stake_changes, auto_changes = [], [], []
        for actor in actors:
            dc, dc_source = base_dc, None
            if setting_key and inp.get("target_npc"):
                # an NPC with a sheet sets the DC (2026-11-03) - the GM's number is only a fallback
                sheet_dc, dc_source = npc_sheets_mod.opposed_dc(state, setting_key, system, inp["target_npc"], inp["attribute"],
                                                                inp.get("skill"), inp.get("opposed_by"), inp.get("ask"))
                if sheet_dc is not None:
                    dc = sheet_dc
            if actor == "player":
                result = system.roll_check(state["character"], inp["attribute"], inp.get("skill"), dc, inp.get("target_npc"))
                auto_changes += system.apply_auto_liability_effects(state, result)
            else:
                # another character makes the attempt (2026-11-03): their sheet, their consequences
                result = npc_sheets_mod.roll_for(state, setting_key, system, actor, inp["attribute"], inp.get("skill"), dc,
                                                 inp.get("target_npc"))
                if result is None:
                    return json.dumps({"error": f"'{actor}' has no sheet - give them one with create_npc_sheet, or roll for the player."}), events
            if dc_source:
                result["dc_source"] = dc_source
                if inp.get("ask") == "major":
                    result["gm_instruction"] = (npc_sheets_mod.ASK_STEPS.get(result["degree"], "") +
                                                " A major ask is a negotiation: play it over several exchanges.")
            rolls.append(result)
        if setting_key and inp.get("target_npc"):
            npc_sheets_mod.record_encounter(state, setting_key, inp["target_npc"])
        combine = inp.get("combine") if inp.get("combine") in ("best", "each") else "best"
        order = ["critical_failure", "failure", "success_at_cost", "success", "strong_success", "critical_success"]
        best = max(rolls, key=lambda r: (order.index(r["degree"]), r["total"]))
        if combine == "best" or len(rolls) == 1:
            stake_degree = best["degree"]
        else:  # each answers for themselves: the side's stake rides on the player's own roll, if they rolled
            stake_degree = next((r["degree"] for r in rolls if not r.get("actor")), best["degree"])
        stake_changes = _apply_at_stake(state, resource_schema, dyn, inp.get("at_stake"), stake_degree, "at stake in check")
        follow = _post_change_bookkeeping(state, dyn, resource_schema, system) if stake_changes else []
        for r in rolls:
            ev = {"type": "check", "description": inp.get("description", ""), "at_stake": inp.get("at_stake"), **r}
            if len(rolls) > 1:
                ev["group"] = combine
                ev["counts"] = combine == "each" or r is best
            events.append(ev)
        if auto_changes or stake_changes or follow:
            events.append({"type": "state_update", "reasoning": "Automatic: liability trigger / at-stake resource.",
                           "changes": auto_changes + stake_changes + follow})
        payload = dict(rolls[0]) if len(rolls) == 1 else {
            "rolls": rolls, "combine": combine, "group_degree": best["degree"],
            "gm_instruction": ("They act together: the best roll carries the group - narrate who made it work. " if combine == "best"
                               else "Each answers for their own roll - narrate each outcome and who bears it. ") + best["gm_instruction"]}
        if any(r.get("actor") for r in rolls):
            payload["note"] = "Consequences of an NPC's roll land on them (their standing), not on the player's character."
        if stake_changes:
            payload["at_stake_applied"] = {c["key"]: c["after"] - c["before"] for c in stake_changes}
        notices = _notices_for(follow)
        if notices:
            payload["world_events"] = notices
        return json.dumps(payload), events

    if name == "propose_state_update":
        days = inp.pop("days_elapsed", None)
        world_deltas = inp.pop("world_deltas", None)
        canon_key = inp.pop("canon_event_triggered", None)
        derived = dynamics.derived_keys(dyn)
        ignored = []
        if derived and inp.get("resource_deltas"):
            ignored = [{"kind": "derived_resource_ignored", "key": k} for k in inp["resource_deltas"] if k in derived]
            inp["resource_deltas"] = {k: v for k, v in inp["resource_deltas"].items() if k not in derived}
        changes = ignored + system.apply_state_update(state, inp, resource_schema)
        if world_deltas and dyn:
            changes += dynamics.apply_world_deltas(state, dyn, resource_schema, world_deltas)
        if canon_key:
            if dyn:
                changes += dynamics.try_fire_event(state, dyn, resource_schema, canon_key, "GM")
            else:
                # Settings without dynamics.json keep the original ungated behavior.
                changes += system.apply_state_update(state, {"canon_event_triggered": canon_key, "reasoning": ""}, resource_schema)
        if days:
            if dyn and allow_time_advance:
                changes += dynamics.advance_time(state, dyn, resource_schema, system, days)
                changes += negotiation.on_days_passed(state)
            elif not allow_time_advance:
                changes.append({"kind": "time_ignored", "reason": "this shared world's clock is run by the server"})
        changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
        events.append({"type": "state_update", "reasoning": inp.get("reasoning", ""), "changes": changes})
        rejected = [c for c in changes if c.get("kind") in ("canon_event_rejected", "derived_resource_ignored", "time_ignored")]
        payload = {"applied": "Values may have been clamped or rejected by the application."}
        if rejected:
            payload["rejected"] = rejected
        time_change = next((c for c in changes if c.get("kind") == "time"), None)
        if time_change:
            payload["date_now"] = state.get("date")
        notices = _notices_for(changes)
        if notices:
            payload["world_events"] = notices
        return json.dumps(payload), events

    if name == "propose_level_up":
        changes = system.apply_level_up(state, inp)
        events.append({"type": "level_up", "reasoning": inp.get("reasoning", ""), "changes": changes})
        return json.dumps({"changes": changes}), events

    if name == "propose_mechanic_update" and mechanics_catalog:
        changes = mechanics.apply_mechanic_update(state, mechanics_catalog, inp)
        changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
        events.append({"type": "mechanic_update", "reasoning": inp.get("reasoning", ""), "changes": changes})
        return json.dumps({"changes": changes}), events

    if name in ("call_vote", "call_election") and mechanics_catalog:
        chart = (state.get("mechanics") or {}).get(inp.get("instance_id"))
        if chart and chart.get("render_primitive") == "seat_chart":
            kind = "election" if name == "call_election" else "vote"
            blocked = sittings.check(state, mechanics_catalog, chart, inp, kind) if debate.pace(state) == "debate" else None
            if not blocked:
                blocked = debate.vote_ready(state, chart, inp, kind)
            if blocked:
                events.append({"type": "sitting", "body": chart.get("title"), "motion": inp.get("motion") or inp.get("office"), **blocked})
                return json.dumps(blocked), events
            if inp.get("urgency"):
                inp["threshold"] = "two_thirds"
            sittings.record(state, chart, inp, kind)

    if name == "debate_speech" and mechanics_catalog:
        chart = (state.get("mechanics") or {}).get(inp.get("instance_id"))
        if not chart or chart.get("render_primitive") != "seat_chart":
            return json.dumps({"error": f"No seat_chart mechanic '{inp.get('instance_id')}'."}), events
        result = debate.speech(state, setting_key, system, chart, inp)
        if "error" not in result:
            roll = result["roll"]
            events.append({"type": "check", "description": f"{result['speaker']} speaks {result['side']}"
                           + (f" {result['candidate']}" if result.get("candidate") else "") + f": {inp.get('point', '')}",
                           **({k: v for k, v in roll.items() if k != "gm_instruction"}),
                           **({"actor": roll.get("actor_name")} if roll.get("actor_name") else {})})
            events.append({"type": "debate", "body": chart.get("title"), "motion": inp.get("motion") or inp.get("office") or "",
                           "speaker": result["speaker"], "side": result["side"], "candidate": result.get("candidate"),
                           "degree": roll["degree"], "room": result["room"], "speeches": result["speeches_so_far"],
                           "ready": result["ready_for_vote"]})
        return json.dumps(result), events

    if name == "call_election" and mechanics_catalog:
        chart = (state.get("mechanics") or {}).get(inp.get("instance_id")) or {}
        d = ((chart.get("data") or {}).get("debates") or {}).get(debate.key_for(inp)) or {}
        result, changes = operations.call_election(state, system, inp,
                                                   candidate_swing={str(k).lower(): v for k, v in (d.get("candidate_swing") or {}).items()})
        if "error" not in result and chart:
            debate.after_ballot(chart, inp)
        if "error" not in result:
            events.append({"type": "operation", "operation": "call_election", "reasoning": inp.get("office", ""),
                           "result": result, "changes": changes})
        return json.dumps(result), events

    if name == "call_vote" and mechanics_catalog:
        m = (state.get("mechanics") or {}).get(inp.get("instance_id")) or {}
        influence_key = (m.get("data") or {}).get("influence_resource") or ((dyn or {}).get("influence") or {}).get("vote")
        roller = None
        if inp.get("speech_by") and str(inp["speech_by"]).lower() not in ("player", "me") and setting_key:
            if not npc_sheets_mod.resolve(state, setting_key, inp["speech_by"]):
                return json.dumps({"error": f"'{inp['speech_by']}' has no sheet to speak with."}), events
            roller = lambda a, sk, dc: npc_sheets_mod.roll_for(state, setting_key, system, inp["speech_by"], a, sk, dc)
        result, changes = operations.call_vote(state, system, inp, influence_value=_resource_value(state, influence_key),
                                               speech_roller=roller, debate=debate.apply_to_vote(m, inp) if m else None)
        if "error" not in result and result["passed"] and inp.get("canon_event"):
            if dyn:
                fire = dynamics.try_fire_event(state, dyn, resource_schema, inp["canon_event"], "vote")
            else:
                fire = system.apply_state_update(state, {"canon_event_triggered": inp["canon_event"], "reasoning": ""}, resource_schema)
            changes += fire
            result["canon_event"] = fire
        changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
        if result.get("speech"):
            events.append({"type": "check", "description": f"Speech before the vote: {inp.get('motion', '')}", **result["speech"]})
        events.append({"type": "operation", "operation": "call_vote", "reasoning": inp.get("motion", ""), "result": result, "changes": changes})
        return json.dumps(result), events

    if name == "sway_unit" and mechanics_catalog:
        if geo:  # an appeal in person needs the player there (2026-11-03)
            _, here, away, _ = geography.presence(geo, state, "player", [inp.get("unit_id")])
            if inp.get("unit_id") in away:
                return json.dumps({"error": f"The player isn't with {inp.get('unit_id')} ({away[inp['unit_id']]}). Travel there with move_unit first, or narrate a letter or an emissary without a roll."}), events
        influence_key = ((dyn or {}).get("influence") or {}).get("sway")
        result, changes = operations.sway_unit(state, system, inp, influence_value=_resource_value(state, influence_key))
        if "error" not in result:
            risk = (dyn or {}).get("sway_risk_resource")
            degree = result["check"]["degree"]
            if risk and shared_core.AT_STAKE_DELTAS.get(degree, 0) < 0:
                changes += _apply_at_stake(state, resource_schema, dyn, risk, degree, "exposure from sway_unit")
            changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
            events.append({"type": "check", "description": inp.get("description", ""), **result["check"]})
        events.append({"type": "operation", "operation": "sway_unit", "reasoning": inp.get("description", ""), "result": result, "changes": changes})
        return json.dumps(result), events

    if name == "resolve_confrontation" and mechanics_catalog:
        absent, place_node = {}, None
        if geo:
            if not inp.get("place"):
                return json.dumps({"error": "Say where it happens: place (a name from GEOGRAPHY)."}), events
            place_node, p_in, p_out, err = geography.presence(geo, state, inp["place"], inp.get("player_units") or [])
            if err:
                return json.dumps({"error": err}), events
            _, o_in, o_out, _ = geography.presence(geo, state, inp["place"], inp.get("opposing_units") or [])
            absent = {**p_out, **o_out}
            inp["player_units"], inp["opposing_units"] = p_in, o_in
            if not p_in:
                return json.dumps({"error": "None of the player's units can reach that place now.", "absent": absent,
                                   "hint": "move_unit them there first (or let the fight not happen)."}), events
        result, changes = operations.resolve_confrontation(state, system, inp)
        if geo and "error" not in result:
            if absent:
                result["absent"] = absent
            changes += geography.after_confrontation(geo, state, place_node, inp, result)
        if "error" not in result and dyn:
            changes += dynamics.apply_confrontation_effects(state, dyn, resource_schema, result)
            changes += _post_change_bookkeeping(state, dyn, resource_schema, system)
            notices = _notices_for(changes)
            if notices:
                result["world_events"] = notices
        if result.get("command_check"):
            events.append({"type": "check", "description": f"Command: {inp.get('objective', '')}", **result["command_check"]})
        events.append({"type": "operation", "operation": "resolve_confrontation", "reasoning": inp.get("objective", ""), "result": result, "changes": changes})
        return json.dumps(result), events

    return f"Unknown tool: {name}", events


# ==================== Memory: ask what your character would know (2026-11-03) ====================
MEMORY_LOG_KEEP = 30


def _known_canon(state, setting_key):
    """Canon the character can know about: what has already happened in this campaign, plus anything
    dated on or before today. Never the setting's future beats."""
    dyn = dynamics.load(setting_key)
    lines = []
    for key, ev in (state.get("canon_events") or {}).items():
        if ev.get("status") != "fired":
            continue
        spec = ((dyn or {}).get("canon") or {}).get(key) or {}
        lines.append(f"- {key.replace('_', ' ')}{' (' + ev['fired_date'] + ')' if ev.get('fired_date') else ''}: {spec.get('summary') or spec.get('message') or ''}".rstrip(": "))
    return "\n".join(lines) or "(nothing recorded yet)"


def answer_memory_question(provider, model, state, setting_key, system_key, question, usage_sink=None):
    """One model call, no tools, no state changes beyond logging the exchange. The point is to catch
    the PLAYER up with their character - who these people are, what happened in July, what the
    Mensheviks want - without spending a game turn or advancing the clock."""
    c = state.get("character") or {}
    setting = SETTING_REGISTRY[setting_key]
    rels = "\n".join(f"- {k.replace('_', ' ')}: trust {v.get('trust', 0):+d}. {v.get('note', '')}" for k, v in (state.get("relationships") or {}).items())
    enmity = "\n".join(f"- {k.replace('_', ' ')}: {v.get('pips', 0)}/3. {v.get('note', '')}" for k, v in (state.get("enmity") or {}).items() if v.get("pips"))
    commitments = "\n".join(f"- {v.get('description', k)} ({v.get('status')})" for k, v in (state.get("commitments") or {}).items())
    recent = "\n".join(f"- {e.get('summary', '')[:300]}" for e in (state.get("campaign_log_summary") or [])[-6:])
    divergence = "\n".join(f"- {d.get('note')}" for d in (state.get("divergence_log") or [])[-10:])
    opening = state.get("opening_text") or ""
    system_prompt = f"""You are the memory of {c.get('name')} ({c.get('role', '')}), in the setting "{setting['label']}". Today, in the
story, is {state.get('date')}. The player controls this character but may know little about the period;
they are asking you what their character would know, so they can play the part well.

Answer as what the character knows, believes, has heard, or remembers - in second person ("You've known
Kamenev since exile; he..."). Plain prose, 60-180 words, no markdown, no lists unless the question asks
for one. Speak like a well-informed person of the period, not a textbook.

Hard rules:
- Nothing after {state.get('date')}. If asked about the future, say the character can't know it, and give
  what they'd expect, hope or fear, as opinion. Never hint at how history turned out.
- If the character plausibly wouldn't know something, say so, and say what they might have heard.
- The campaign's own events override history: if the state below says something changed, that is what
  happened. Don't contradict it.
- Don't invent game mechanics, numbers or rules; don't tell the player what to do. You can say what
  the character thinks of the options.
- If the question isn't something a memory can answer (e.g. "roll a check", "what do I do"), say so in a
  sentence and offer what the character does know that's relevant.

# WHO THE CHARACTER IS
{json.dumps({k: c.get(k) for k in ("name", "role", "flags", "liabilities", "feats")}, ensure_ascii=False, default=str)}

# PEOPLE (setting notes - use what the character would know of them as of today)
{load_text(setting_key, "npcs.md")}

# FACTIONS
{load_text(setting_key, "factions.md")}

# THE CHARACTER'S OWN RELATIONSHIPS
{rels or '(none recorded)'}
Rivalries: {enmity or 'none active'}
Commitments: {commitments or 'none'}

# WHAT HAS HAPPENED SO FAR
Opening of the campaign: {opening[:1200]}
Events that have already happened: 
{_known_canon(state, setting_key)}
Recent play:
{recent or '(nothing yet)'}
Where this campaign has departed from history:
{divergence or '(nowhere yet)'}"""
    conversation = provider.new_conversation()
    for qa in (state.get("memory_log") or [])[-4:]:
        provider.append_user_message(conversation, qa["q"])
        conversation.append({"role": "assistant", "content": qa["a"]})
    provider.append_user_message(conversation, question)
    result = provider.call(model, system_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    answer = scrub_narration("\n".join(result.text_parts).strip()) or "Nothing comes to mind."
    log = state.setdefault("memory_log", [])
    log.append({"q": question, "a": answer, "date": state.get("date"), "turn": state.get("turn_number", 0)})
    del log[:-MEMORY_LOG_KEEP]
    return answer


def generate_historical_character(provider, model, setting_key, system_key, figure_name, usage_sink=None):
    """AI-driven generation of a playable historical figure who existed within setting_key's exact
    time period (2026-08-23) - never the setting's own flagship premade character (players pick that
    through a separate path). The model can and does refuse: not enough real information to generate
    responsibly, doesn't actually fit this exact period, or a case better served by a custom
    character build. This is a real, meaningful refusal path per direction, not a formality - the
    prompt is written to make "no" the easy, expected answer whenever genuinely unsure.

    Returns {"eligible": True, "character": {...validated dict, ready to store...}} or
    {"eligible": False, "reason": str} - the latter covers both a real refusal AND a generated sheet
    that failed validation (a malformed response, not treated differently from a legitimate "no" by
    the caller - either way, nothing gets created)."""
    system = get_system(system_key)
    setting_info = SETTING_REGISTRY[setting_key]
    canon = load_text(setting_key, "canon_timeline.md")
    skill_list = [s for skills in system.SKILLS.values() for s in skills]
    feat_ids = sorted(system.FEAT_CATALOG.keys())
    liability_ids = sorted(system.LIABILITY_CATALOG.keys())

    system_prompt = f"""You are deciding whether to generate a playable character sheet for a REAL
historical figure a player has requested, for a historical RPG.

# SETTING
{setting_info['label']}
{setting_info['blurb']}

# CANON TIMELINE FOR THIS EXACT SETTING/PERIOD
{canon}

# REQUESTED FIGURE
{figure_name}

First decide whether this is eligible at all. Refuse (eligible=false) if ANY of these are true:
- You don't have enough real, reliable information about this specific person to generate a
  responsible, reasonably historically-grounded character sheet for them - don't guess or invent.
- They did not actually exist within THIS setting's exact time period (the specific window above,
  not just the same broad century or region) - a real person from the wrong decade or the wrong
  country still doesn't fit.
- They ARE this setting's own signature/flagship character ({setting_info['premade_character_name']})
  - that one is picked through a separate path already.
- They aren't a real historical person at all (fictional, legendary/mythical with no real basis, or a
  living person too recent/sensitive to depict responsibly).
- A custom character build would genuinely serve the player better than an attempt at this specific
  historical likeness (e.g. the request is really "someone like X," not X themselves).

When genuinely unsure, refuse - a missed valid figure is a much smaller problem than a fabricated or
irresponsible one. This is a real decision point, not a formality: most requests for obscure or
poorly-documented figures should be refused.

If eligible, build a character sheet:
- attribute_assignment: assign the standard array {shared_core.STANDARD_ATTRIBUTE_ARRAY} one-to-one
  across all six attributes ({', '.join(shared_core.ATTRIBUTES)}), based on this figure's known real
  strengths.
- level: an integer from 1 to {shared_core.MAX_HISTORICAL_FIGURE_LEVEL}. Calibrate to be FAIRLY
  LEVELED - balanced against other player characters, not wildly over- or under-powered. Most figures
  should land in the low-to-middle of this range; reserve the top only for a case you can genuinely
  justify from real historical accomplishment within this exact period.
- skill_tiers: a {{skill_name: tier}} map, tiers 0 (untrained) / 1 (proficiency) / 2 (expertise), from
  this system's skill list: {', '.join(skill_list)}. Reflects real documented competence, scaled
  sensibly with the level you chose - do not use every skill, only the ones this figure would
  plausibly be trained in.
- feat_ids: zero or more feat ids from: {', '.join(feat_ids)} - each one you can plausibly justify for
  this figure. Each has its own min_level and sometimes a prerequisite skill tier; the app validates
  and rejects anything that doesn't actually qualify, so don't pick one just to fill a slot.
- liability_ids: zero to two liability ids from: {', '.join(liability_ids)} - real narrative baggage
  this figure plausibly carries, not a formality.
- role: a short one-line real title/role for them in this setting.
- reasoning: your reasoning for the eligibility decision either way (required).

Submit via decide_historical_character."""

    tool_specs = [
        {
            "name": "decide_historical_character",
            "description": "Decide whether this figure is eligible, and if so, build their character sheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "eligible": {"type": "boolean"},
                    "refusal_reason": {"type": ["string", "null"], "description": "Required if eligible is false - a clear, specific reason."},
                    "role": {"type": "string"},
                    "level": {"type": "integer"},
                    "attribute_assignment": {"type": "object", "description": "e.g. {\"oration\": 16, \"intellect\": 14, ...}"},
                    "skill_tiers": {"type": "object", "description": "e.g. {\"debate_and_public_argument\": 2, ...}"},
                    "feat_ids": {"type": "array", "items": {"type": "string"}},
                    "liability_ids": {"type": "array", "items": {"type": "string"}},
                    "reasoning": {"type": "string"},
                },
                "required": ["eligible", "reasoning"],
            },
        }
    ]

    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_historical_character")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return {"eligible": False, "reason": "The model didn't return a usable decision - try again."}

    decision = result.tool_calls[0]["input"]
    if not decision.get("eligible"):
        reason = (decision.get("refusal_reason") or "").strip()
        return {"eligible": False, "reason": reason or "Not eligible - insufficient information or doesn't fit this setting's exact period."}

    character, errors = system.create_leveled_character(
        figure_name, decision.get("role", ""), decision.get("level"),
        decision.get("attribute_assignment") or {}, decision.get("skill_tiers") or {},
        decision.get("feat_ids") or [], decision.get("liability_ids") or [],
    )
    if errors:
        return {"eligible": False, "reason": f"Generated sheet failed validation: {'; '.join(errors)}"}
    return {"eligible": True, "character": character}
