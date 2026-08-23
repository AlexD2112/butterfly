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
