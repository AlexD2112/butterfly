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

import council
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
        "blurb": "Leon Trotsky, Chairman of the Petrograd Soviet. It's September 1917, and Kerensky's government is barely holding on.",
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
    """Returns a fresh copy of a setting's premade signature character's starting state."""
    info = SETTING_REGISTRY[setting_key]
    fresh = load_json(setting_key, info["premade_character_file"])
    fresh["_is_new"] = True
    return fresh


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


def build_system_prompt(state, setting_key, system_key):
    system_info = SYSTEM_REGISTRY[system_key]
    system_rules = (ROOT / system_info["rules_file"]).read_text(encoding="utf-8")

    npcs = load_text(setting_key, "npcs.md")
    factions = load_text(setting_key, "factions.md")
    canon = load_text(setting_key, "canon_timeline.md")
    resource_schema = load_json(setting_key, "resource_schema.json")
    digest = build_state_digest(state)

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

Narrate in second person, present tense, evocative but not overwrought. Keep responses to a few
paragraphs. Never resolve an uncertain action without calling request_check first. If the current
state shows level_up_pending: true, remind the player at a natural pause and offer them the three
level-up options from the system rules before proceeding.
{council.VOICE_GUIDANCE}"""


def run_turn(provider, model, state, setting_key, system_key, conversation, user_input, usage_sink=None, budget_ok=None, council_mode=False, model_key=None):
    """Returns (narration, debug_events).

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
    """
    system = get_system(system_key)
    resource_schema = load_json(setting_key, "resource_schema.json")
    provider.append_user_message(conversation, user_input)
    system_prompt = build_system_prompt(state, setting_key, system_key)

    text_parts = []
    debug_events = []
    while True:
        turn_result = provider.call(model, system_prompt, system.TOOL_SPECS, conversation)
        provider.append_assistant_turn(conversation, turn_result)
        text_parts.extend(turn_result.text_parts)

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
            if tc["name"] == "request_check":
                inp = tc["input"]
                result = system.roll_check(
                    state["character"], inp["attribute"], inp.get("skill"), inp["dc"], inp.get("target_npc")
                )
                auto_changes = system.apply_auto_liability_effects(state, result)
                debug_events.append({"type": "check", "description": inp.get("description", ""), **result})
                if auto_changes:
                    debug_events.append({"type": "state_update", "reasoning": "Automatic liability trigger.", "changes": auto_changes})
                results.append({"id": tc["id"], "content": json.dumps(result)})
            elif tc["name"] == "propose_state_update":
                changes = system.apply_state_update(state, tc["input"], resource_schema)
                debug_events.append(
                    {"type": "state_update", "reasoning": tc["input"].get("reasoning", ""), "changes": changes}
                )
                results.append(
                    {"id": tc["id"], "content": "Applied (values may have been clamped or rejected by the application)."}
                )
            elif tc["name"] == "propose_level_up":
                changes = system.apply_level_up(state, tc["input"])
                debug_events.append(
                    {"type": "level_up", "reasoning": tc["input"].get("reasoning", ""), "changes": changes}
                )
                results.append({"id": tc["id"], "content": json.dumps({"changes": changes})})
            else:
                results.append({"id": tc["id"], "content": f"Unknown tool: {tc['name']}"})

        provider.append_tool_results(conversation, turn_result, results)

    narration = "\n".join(text_parts).strip()

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
