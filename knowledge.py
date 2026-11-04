"""
Who knows what (2026-11-03): fog of war for information.

Reported: Martov referred to a private conversation the player had with Spiridonova. The GM sees
everything that has happened, and nothing stopped it from letting every character know it too.

Now private matters are recorded as facts with the people who know them:
  state["_knowledge"] = [{id, text, secrecy: secret|private|public, turn, date,
                         witnesses: [npc keys], groups: [faction ids], public: bool}]
  - the GM records anything said or done out of public view (record_knowledge), naming who was
    there; negotiation offers and deals are recorded automatically;
  - a character knows a fact if they were there, were told, their faction has heard, or it's public;
  - word spreads on its own as days pass, rolled in code: a private matter tends to reach the
    witnesses' own faction within a few days and may become common talk; a secret rarely leaks;
  - the prompt lists every private fact with exactly who knows it, and the rule that no one else
    may act on it.
The player can't see leaks between other factions (that's the point); facts that become public
show as a rumour.
"""

import random
import re

LEAK = {  # chance per day: a witness's faction hears it; a faction-known fact becomes common talk
    "private": {"faction": 0.25, "public": 0.06},
    "secret": {"faction": 0.04, "public": 0.01},
}
PROMPT_LIMIT = 15
SECRECY = ("secret", "private", "public")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def _facts(state):
    return state.setdefault("_knowledge", [])


def faction_of(state, setting_key, key):
    import npcs
    sheet = npcs.all_sheets(state, setting_key).get(key) if setting_key else None
    return (sheet or {}).get("faction")


def resolve_people(state, setting_key, names):
    import npcs
    out = []
    for n in names or []:
        key = npcs.resolve(state, setting_key, n) if setting_key else None
        key = key or _slug(n)
        if key and key not in out and key not in ("player", "me"):
            out.append(key)
    return out


def knows(state, setting_key, key, fact):
    if fact.get("public") or key in fact.get("witnesses", []):
        return True
    fac = faction_of(state, setting_key, key)
    return bool(fac and fac in fact.get("groups", []))


def record(state, setting_key, text, witnesses, secrecy="private", fact_id=None):
    """A new fact, or (fact_id) more people told about an existing one."""
    facts = _facts(state)
    people = resolve_people(state, setting_key, witnesses)
    if fact_id:
        fact = next((f for f in facts if f["id"] == fact_id), None)
        if not fact:
            return {"error": f"No fact {fact_id}."}
        added = [p for p in people if p not in fact["witnesses"]]
        fact["witnesses"] += added
        return {"fact": fact_id, "now_known_to": fact["witnesses"], "added": added}
    if not str(text or "").strip():
        return {"error": "What happened? (text)"}
    secrecy = secrecy if secrecy in SECRECY else "private"
    fact = {"id": f"f{len(facts) + 1}", "text": str(text).strip()[:300], "secrecy": secrecy,
            "turn": state.get("turn_number", 0), "date": state.get("date"), "witnesses": people,
            "groups": [], "public": secrecy == "public"}
    facts.append(fact)
    return {"recorded": fact["id"], "known_to": people or ["the player only"], "secrecy": secrecy}


def spread_one_day(state, setting_key, rng=random):
    """Word travels. Returns change records: faction leaks are for the GM only; facts that become
    public are marked as rumours the player can see."""
    changes = []
    for fact in _facts(state):
        if fact.get("public"):
            continue
        odds = LEAK.get(fact.get("secrecy"), LEAK["private"])
        for w in list(fact["witnesses"]):
            fac = faction_of(state, setting_key, w)
            if fac and fac not in fact["groups"] and rng.random() < odds["faction"]:
                fact["groups"].append(fac)
                changes.append({"kind": "knowledge_spread", "fact": fact["id"], "to": fac, "gm_only": True,
                                "message": f"Word of [{fact['id']}] has reached the {fac.replace('_', ' ')}."})
        if fact["groups"] and rng.random() < odds["public"] * len(fact["groups"]):
            fact["public"] = True
            changes.append({"kind": "knowledge_public", "fact": fact["id"], "message": f"It's common talk now: {fact['text']}"})
    return changes


def player_view(state, setting_key):
    """What the player's character can know about who knows: whom they told themselves, and what
    is now common talk. Never which other factions have quietly heard."""
    import npcs
    sheets = npcs.all_sheets(state, setting_key) if setting_key else {}
    out = []
    for f in reversed(_facts(state)):
        names = [sheets.get(w, {}).get("name", w.replace("_", " ").title()) for w in f["witnesses"]]
        out.append({"id": f["id"], "text": f["text"], "secrecy": f["secrecy"], "date": f.get("date"),
                    "you_told": names, "common_talk": bool(f.get("public"))})
    return out[:40]


def prompt_section(state, setting_key):
    import npcs
    sheets = npcs.all_sheets(state, setting_key) if setting_key else {}
    lines = ["WHO KNOWS WHAT (code-owned fog of war. A character knows only what they saw or heard themselves, what is",
             "public, and the private matters below that list them, their faction, or 'everyone'. Nobody else knows - they",
             "may not mention, hint at or act on it, however convenient. When the player says or does something out of",
             "public view (a closed-door talk, a letter, a secret order), record_knowledge with who was there; when the",
             "player tells someone, record_knowledge with fact_id to add them. Negotiation offers are recorded for you.)"]
    private = [f for f in _facts(state) if not f.get("public")][-PROMPT_LIMIT:]
    if not private:
        lines.append("No private matters yet.")
    for f in private:
        who = [sheets.get(w, {}).get("name", w) for w in f["witnesses"]]
        groups = [g.replace("_", " ") for g in f.get("groups", [])]
        known = "; ".join(filter(None, [", ".join(who), ("word has reached: " + ", ".join(groups)) if groups else ""]))
        lines.append(f"[{f['id']}] ({f['secrecy']}, {f.get('date') or 'turn ' + str(f['turn'])}) {f['text']} - known to: {known or 'the player only'}.")
    recent_public = [f for f in _facts(state) if f.get("public") and f["secrecy"] != "public"][-3:]
    for f in recent_public:
        lines.append(f"[{f['id']}] now common talk: {f['text']}")
    return "\n".join(lines)


TOOL_SPEC = {
    "name": "record_knowledge",
    "description": ("Record something said or done out of public view, and who knows it: a closed-door talk, a letter, a "
                    "secret order, a bribe. Or, with fact_id, record that the player told more people. Code then decides "
                    "who else hears of it as days pass. Public events (a speech, a vote, a street fight) don't need this."),
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "What happened, one line, e.g. 'Trotsky offered Spiridonova Left SR seats on the convocation committee'."},
        "witnesses": {"type": "array", "items": {"type": "string"}, "description": "Everyone besides the player who was there or was told (ids or names)."},
        "secrecy": {"type": "string", "enum": list(SECRECY), "description": "secret: sworn to silence; private: not for others' ears but people talk; public: said openly."},
        "fact_id": {"type": "string", "description": "To add people to an existing fact instead of recording a new one."},
    }, "required": ["witnesses"]},
}
