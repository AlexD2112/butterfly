"""
NPC sheets (2026-11-03): the people the player argues with, bargains with and tries to outwit.

Each sheet has the same six attributes and skill tiers as the player character. They used to be
notes in npcs.md that only the GM read, so the difficulty of beating Dan in a debate was whatever
the model picked that turn. Now:

  - the application sets the DC of any check aimed at an NPC with a sheet: 10 + the NPC's modifier
    in the skill (or attribute) that resists it - by default the same skill the player is using;
  - the player can open a sheet from the Relationships list, seen through fog of war: how much is
    visible depends on how well the character knows the person (the sheet's starting familiarity,
    plus every check made against them);
  - the GM can give a new important figure a sheet with create_npc_sheet, validated here.

Setting sheets: settings/<key>/npc_sheets.json. Sheets created in play: state["_npc_sheets"].
What the player has learned: state["npc_knowledge"][key] = {"encounters": n}.
"""

import hashlib
import json
import re
from pathlib import Path

SETTINGS_DIR = Path(__file__).resolve().parent / "settings"
ATTRS = ("oration", "intellect", "resolve", "presence", "cunning", "physique")
ENCOUNTERS_PER_TIER = 3       # three checks against someone teach you one tier more about them
MAX_NEW_ATTRIBUTE_TOTAL = 90  # a new figure's six attributes; Lenin's are 95, Kamenev's 69
MAX_NEW_HIGH_ATTRIBUTES = 2   # at most two scores of 18+
MAX_NEW_EXPERTISE = 2
MAX_NEW_PROFICIENT = 4
MAX_NEW_SHEETS_PER_TURN = 2

TIER_WORDS = ["Never met - reputation at most", "Known by reputation", "Worked with them", "Knows them well"]
BANDS = [(8, "weak"), (11, "ordinary"), (14, "capable"), (17, "formidable"), (99, "exceptional")]

_CACHE = {}


def _raw(setting_key):
    if setting_key not in _CACHE:
        path = SETTINGS_DIR / str(setting_key) / "npc_sheets.json"
        _CACHE[setting_key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return _CACHE[setting_key]


def load(setting_key):
    raw = _raw(setting_key)
    return raw["sheets"] if raw else None


def location(state, setting_key, key):
    """Where an NPC is now (a geography node): moved in play, else their sheet, else the setting's default."""
    moved = (state.get("_npc_locations") or {}).get(key)
    if moved:
        return moved
    sheet = all_sheets(state, setting_key).get(key) or {}
    return sheet.get("location") or (_raw(setting_key) or {}).get("default_location")


def all_sheets(state, setting_key):
    out = dict(load(setting_key) or {})
    out.update(state.get("_npc_sheets") or {})
    return out


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def resolve(state, setting_key, who):
    """A sheet key from an id or a name ('dan', 'Fyodor Dan', 'Dan'), or None."""
    sheets = all_sheets(state, setting_key)
    key = _norm(who)
    if key in sheets:
        return key
    for k, s in sheets.items():
        name = _norm(s["name"])
        if key == name or name.endswith("_" + key) or key.endswith("_" + k):
            return k
    return None


def modifier(sheet, skill_or_attr, system):
    """The NPC's bonus in a skill (attribute modifier + proficiency x tier) or a bare attribute."""
    score_mod = lambda a: (sheet["attributes"][a] - 10) // 2
    if skill_or_attr in ATTRS:
        return score_mod(skill_or_attr)
    attr = next((a for a, skills in system.SKILLS.items() if skill_or_attr in skills), None)
    if attr is None:
        return None
    tier = 2 if skill_or_attr in sheet.get("expertise", []) else 1 if skill_or_attr in sheet.get("proficient", []) else 0
    return score_mod(attr) + tier * sheet.get("proficiency", 2)


# How much is being asked of them (2026-11-03). A favour costs nothing extra; changing sides costs a lot.
ASK_DC = {"minor": 0, "significant": 3, "major": 6}
SOCIAL_ATTRS = ("presence", "oration")
ASK_STEPS = {
    # what each degree means when the ask is major - one roll moves someone, it doesn't buy them
    "critical_success": "They agree in principle - but they will want the terms in writing, and something in return.",
    "strong_success": "They move a real step: they say what they'd need to agree, and mean it. They have not agreed yet.",
    "success": "They'll hear the offer: the door is open, nothing is promised. They ask what exactly is on the table.",
    "success_at_cost": "They'll keep talking, but they've noticed what you want and raise their price.",
    "failure": "They don't move, and say why. The conversation can go on, but this line of argument is spent.",
    "critical_failure": "They take offence or suspect a trap; they're further away than when you started.",
}


def opposed_dc(state, setting_key, system, target, player_attribute, player_skill, opposed_by=None, ask=None):
    """(dc, explanation) for a check aimed at an NPC with a sheet, else (None, None).
    opposed_by: the skill/attribute that resists ('none' = not opposed: the GM's DC stands).
    Talking someone round is resisted by the better of the same skill or their Resolve - a stubborn
    person is hard to persuade however charming you are. ask raises the DC with what's asked of them."""
    if not target or opposed_by == "none":
        return None, None
    key = resolve(state, setting_key, target)
    if not key:
        return None, None
    sheet = all_sheets(state, setting_key)[key]
    resist = opposed_by or player_skill or player_attribute
    mod = modifier(sheet, resist, system)
    if mod is None:
        resist = player_attribute
        mod = modifier(sheet, resist, system)
    if not opposed_by:
        skill_attr = player_attribute if player_attribute in ATTRS else None
        if player_skill:
            skill_attr = next((a for a, sk in system.SKILLS.items() if player_skill in sk), skill_attr)
        # their will: Resolve, or a trained Resolve skill (Cato's Unbending Will) if that's better
        will = max([modifier(sheet, "resolve", system)] + [modifier(sheet, sk, system) for sk in system.SKILLS.get("resolve", [])])
        if skill_attr in SOCIAL_ATTRS and will > mod:
            resist, mod = "resolve", will
    extra = ASK_DC.get(ask or "minor", 0)
    dc = max(5, min(30, 10 + mod + extra))
    why = f"{sheet['name']} resists with {resist.replace('_', ' ')} ({mod:+d})"
    if extra:
        why += f", {ask} ask (+{extra})"
    return dc, f"{why}: DC {dc}"


# ---------------------------------------------------------------- NPCs rolling for themselves (2026-11-03)

STANDING_SHIFT = {"critical_success": 2, "strong_success": 1, "success": 0, "success_at_cost": 0,
                  "failure": -1, "critical_failure": -2}
STANDING_WORDS = {-3: "badly damaged", -2: "damaged", -1: "slipping", 0: "steady", 1: "rising", 2: "high", 3: "commanding"}


def roll_for(state, setting_key, system, who, attribute, skill, dc, target_npc=None, rng=None):
    """An NPC with a sheet makes a check themselves (Kamenev speaks; Natalia carries the message).
    Same dice and degrees as the player's checks; the consequences land on them - their standing -
    not on the player's character."""
    import random
    from systems._shared import core
    key = resolve(state, setting_key, who)
    if not key:
        return None
    sheet = all_sheets(state, setting_key)[key]
    if attribute not in ATTRS:
        attribute = next((a for a, sk in system.SKILLS.items() if skill in sk), "presence")
    mod = modifier(sheet, skill, system) if skill and modifier(sheet, skill, system) is not None else modifier(sheet, attribute, system)
    roll = (rng or random).randint(1, 20)
    total = roll + mod
    degree = core.degree_for(roll, total, dc)
    standing = state.setdefault("_npc_standing", {})
    before = standing.get(key, 0)
    standing[key] = max(-3, min(3, before + STANDING_SHIFT[degree]))
    tier = "expertise" if skill in sheet.get("expertise", []) else "proficiency" if skill in sheet.get("proficient", []) else "untrained"
    return {"actor": key, "actor_name": sheet["name"], "attribute": attribute, "skill": skill, "tier": tier,
            "modifier": mod, "roll": roll, "total": total, "dc": dc, "margin": total - dc, "degree": degree,
            "gm_instruction": core.DEGREE_GUIDANCE[degree].replace("the player", sheet["name"]).replace("The player", sheet["name"]),
            "target_npc": target_npc, "standing_before": STANDING_WORDS[before], "standing_after": STANDING_WORDS[standing[key]]}


# ---------------------------------------------------------------- what the player can see

def knowledge_tier(state, setting_key, key):
    sheet = all_sheets(state, setting_key).get(key) or {}
    met = ((state.get("npc_knowledge") or {}).get(key) or {}).get("encounters", 0)
    return max(0, min(3, sheet.get("familiarity", 0) + met // ENCOUNTERS_PER_TIER))


def record_encounter(state, setting_key, target):
    key = resolve(state, setting_key, target)
    if key:
        entry = state.setdefault("npc_knowledge", {}).setdefault(key, {"encounters": 0})
        entry["encounters"] += 1
    return key


def _band(score):
    return next(word for cap, word in BANDS if score <= cap)


def _wobble(key, attr):
    """A fixed, per-person offset so a range doesn't always sit centred on the true value."""
    return int(hashlib.sha1(f"{key}:{attr}".encode()).hexdigest(), 16) % 3


def player_view(state, setting_key, key):
    """The sheet as the character knows it. Tier 0: only what they're known for. Tier 1: a word
    for each attribute and their best skills. Tier 2: each score to within a few points, and
    everything they're trained in. Tier 3: the exact sheet. Never sends what the tier hides."""
    sheets = all_sheets(state, setting_key)
    sheet = sheets.get(key)
    if not sheet:
        return None
    tier = knowledge_tier(state, setting_key, key)
    met = ((state.get("npc_knowledge") or {}).get(key) or {}).get("encounters", 0)
    attrs = []
    top = sorted(ATTRS, key=lambda a: -sheet["attributes"][a])[:2]
    for a in ATTRS:
        v = sheet["attributes"][a]
        if tier >= 3:
            shown = {"value": v, "modifier": (v - 10) // 2}
        elif tier == 2:
            lo = v - _wobble(key, a)
            shown = {"range": [lo, lo + 2]}
        elif tier == 1:
            shown = {"word": _band(v)}
        else:
            shown = {"word": _band(v)} if a in top and v >= 15 else {"unknown": True}
        attrs.append({"attribute": a, **shown})
    skills = {}
    if tier >= 1:
        skills["expertise"] = sheet.get("expertise", [])
    if tier >= 2:
        skills["proficient"] = sheet.get("proficient", [])
    if tier >= 3:
        skills["proficiency"] = sheet.get("proficiency", 2)
    rel = (state.get("relationships") or {}).get(key) or {}
    en = (state.get("enmity") or {}).get(key) or {}
    nxt = None if tier >= 3 else ENCOUNTERS_PER_TIER - (met % ENCOUNTERS_PER_TIER)
    st = (state.get("_npc_standing") or {}).get(key, 0)
    return {"key": key, "name": sheet["name"], "role": sheet.get("role", ""), "tier": tier,
            "standing": STANDING_WORDS[st] if st else None,
            "tier_label": TIER_WORDS[tier], "attributes": attrs, "skills": skills,
            "trust": rel.get("trust"), "relationship_note": rel.get("note"), "enmity": en.get("pips"),
            "encounters": met, "next_tier_in": nxt}


# ---------------------------------------------------------------- new sheets, from the GM

CREATE_SPEC = {
    "name": "create_npc_sheet",
    "description": ("Give a newly important figure a sheet: someone who will recur, or whom the player will "
                    "argue with, bargain with or try to outwit. Not for walk-ons. Same attributes and skills as "
                    "the player. Historically ordinary people sit around 10-14; only the historically "
                    "exceptional go to 17+. The application validates it, starts a relationship at trust 0, "
                    "and from then on sets the DC of checks aimed at them."),
    "parameters": {"type": "object", "properties": {
        "key": {"type": "string", "description": "Short id, e.g. 'bukharin' or 'colonel_polkovnikov'."},
        "name": {"type": "string"},
        "role": {"type": "string", "description": "One line: who they are now."},
        "attributes": {"type": "object", "description": "All six: oration, intellect, resolve, presence, cunning, physique (3-20)."},
        "proficiency": {"type": "integer", "description": "2, or 3 for a senior figure."},
        "expertise": {"type": "array", "items": {"type": "string"}, "description": "At most 2 skill ids."},
        "proficient": {"type": "array", "items": {"type": "string"}, "description": "At most 4 skill ids."},
        "familiarity": {"type": "integer", "description": "How well the player's character already knows them: 0 never met, 1 by reputation, 2 worked with, 3 well."},
        "reasoning": {"type": "string"},
    }, "required": ["key", "name", "role", "attributes"]},
}


def create(state, setting_key, system, inp):
    key = _norm(inp.get("key") or inp.get("name"))
    if not key:
        return {"error": "A sheet needs a key."}
    if key in all_sheets(state, setting_key) or resolve(state, setting_key, inp.get("name")):
        return {"error": f"'{inp.get('name') or key}' already has a sheet."}
    turn = state.get("turn_number", 0)
    made = [s for s in (state.get("_npc_sheets") or {}).values() if s.get("created_turn") == turn]
    if len(made) >= MAX_NEW_SHEETS_PER_TURN:
        return {"error": "Two new sheets this turn already - give one to the next important figure later."}
    raw = inp.get("attributes") or {}
    try:
        attrs = {a: int(raw[a]) for a in ATTRS}
    except (KeyError, TypeError, ValueError):
        return {"error": f"attributes needs all six of {', '.join(ATTRS)}, as whole numbers."}
    errors = []
    if any(not 3 <= v <= 20 for v in attrs.values()):
        errors.append("each attribute must be 3-20")
    if sum(attrs.values()) > MAX_NEW_ATTRIBUTE_TOTAL:
        errors.append(f"attributes total {sum(attrs.values())}, over the {MAX_NEW_ATTRIBUTE_TOTAL} cap for a new figure")
    if sum(v >= 18 for v in attrs.values()) > MAX_NEW_HIGH_ATTRIBUTES:
        errors.append("at most two attributes of 18+")
    valid = {s for skills in system.SKILLS.values() for s in skills}
    expertise = list(dict.fromkeys(inp.get("expertise") or []))
    proficient = [s for s in dict.fromkeys(inp.get("proficient") or []) if s not in expertise]
    unknown = [s for s in expertise + proficient if s not in valid]
    if unknown:
        errors.append(f"unknown skills {unknown}")
    if len(expertise) > MAX_NEW_EXPERTISE:
        errors.append(f"at most {MAX_NEW_EXPERTISE} expertise skills")
    if len(proficient) > MAX_NEW_PROFICIENT:
        errors.append(f"at most {MAX_NEW_PROFICIENT} proficient skills")
    if errors:
        return {"error": "; ".join(errors)}
    sheet = {"name": str(inp["name"]).strip(), "role": str(inp.get("role") or "").strip(), "attributes": attrs,
             "proficiency": 3 if int(inp.get("proficiency") or 2) >= 3 else 2,
             "expertise": expertise, "proficient": proficient,
             "familiarity": max(0, min(3, int(inp.get("familiarity") or 0))), "created_turn": turn}
    state.setdefault("_npc_sheets", {})[key] = sheet
    state.setdefault("relationships", {}).setdefault(key, {"trust": 0, "note": sheet["role"]})
    return {"created": key, "name": sheet["name"], "note": "Checks aimed at them now use this sheet for their DC."}


# ---------------------------------------------------------------- prompt

def compact(key, s):
    a = s["attributes"]
    stats = " ".join(f"{x[:2].capitalize()}{a[x]}" for x in ATTRS)
    bits = [f"{key}: {stats} prof+{s.get('proficiency', 2)}"]
    if s.get("_standing"):
        bits.append(f"standing {STANDING_WORDS[s['_standing']]}")
    if s.get("expertise"):
        bits.append("exp " + ", ".join(s["expertise"]))
    if s.get("proficient"):
        bits.append("prof " + ", ".join(s["proficient"]))
    return "; ".join(bits)


def prompt_section(state, setting_key):
    sheets = all_sheets(state, setting_key)
    if not sheets:
        return ""
    lines = ["NPC SHEETS (the application sets the DC of any request_check whose target_npc has a sheet: 10 + their",
             "modifier in the resisting skill - by default the skill the player uses, or their Resolve if that's higher",
             "when being talked round; pass opposed_by to name another, e.g. deception against reading_hidden_agendas,",
             "or 'none' when they aren't actively resisting. Pass ask: minor (a favour, information), significant (a",
             "real commitment, a vote), or major (changing sides, an alliance, betraying their own people). New",
             "important figures: create_npc_sheet.)"]
    standing = state.get("_npc_standing") or {}
    lines += [compact(k, dict(s, _standing=standing.get(k, 0))) for k, s in sheets.items()]
    return "\n".join(lines)
