"""
Negotiations (2026-11-03): talking someone round over several exchanges, tracked in code.

Before this, every line of a negotiation was its own pass/fail roll: one good roll could win an
alliance on the opening line, one bad roll on a generous offer lost ground, the NPC's terms were
forgotten from one turn to the next, and the GM could end the conversation whenever it liked
(a bell, a messenger, a coat).

Now a negotiation is a small object in state["negotiations"][npc_key]:
  - position on a track from hostile to agreed, starting from the relationship's trust;
  - the NPC's demands, written down when it opens; an offer is judged against them (each demand met
    makes the roll easier, each one dodged harder) - what the player offers matters, not only the dice;
  - a deal closes when the position reaches the goal set by how much is at stake;
  - a failed roll stalls, only a critical failure loses ground;
  - while it's open the NPC stays: the GM is told so every turn, and request_check can't be used
    to settle a persuasion of them on the side.

Positions: -2 hostile, -1 wary, 0 listening, 1 interested, 2 close, 3 agreed (goals 1 / 2 / 3).
"""

import re

POSITION_WORDS = {-2: "hostile", -1: "wary", 0: "listening", 1: "interested", 2: "close to a deal", 3: "agreed"}
GOALS = {"minor": 1, "significant": 2, "major": 3}
MOVE = {"critical_success": 2, "strong_success": 1, "success": 1, "success_at_cost": 1, "failure": 0, "critical_failure": -1}
DEMAND_MET_DC = -2
DEMAND_DODGED_DC = 2
MAX_DEMANDS = 4

INSTRUCTIONS = {
    "critical_success": "A real breakthrough: they move two steps and warm to the player personally.",
    "strong_success": "They move a step and say so plainly - the offer landed.",
    "success": "They move a step, guardedly. Show what convinced them and what still bothers them.",
    "success_at_cost": "They move a step but raise their price: add a demand with negotiation_update (something concrete they now want).",
    "failure": "They don't move. Have them say exactly why - this is information the player can use. They stay in the conversation.",
    "critical_failure": "The offer offends or alarms them; they move back a step. They're still here, but colder.",
}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "demand"


def open_for(state, key):
    n = (state.get("negotiations") or {}).get(key)
    return n if n and n.get("status") == "open" else None


def _start_position(state, key):
    trust = ((state.get("relationships") or {}).get(key) or {}).get("trust", 0)
    enmity = ((state.get("enmity") or {}).get(key) or {}).get("pips", 0)
    pos = 1 if trust >= 3 else -1 if trust <= -2 else 0
    if enmity >= 2:
        pos -= 1
    return max(-2, min(1, pos))


def _summary(n):
    return {"npc": n["npc"], "name": n["name"], "topic": n["topic"], "stakes": n["stakes"],
            "position": n["position"], "position_word": POSITION_WORDS[n["position"]], "goal": n["goal"],
            "demands": n["demands"], "status": n["status"]}


def open_negotiation(state, npc_key, npc_name, inp):
    if open_for(state, npc_key):
        return {"error": f"A negotiation with {npc_name} is already open - use negotiation_offer."}
    stakes = inp.get("stakes") if inp.get("stakes") in GOALS else "significant"
    demands = []
    for d in (inp.get("demands") or [])[:MAX_DEMANDS]:
        text = str(d).strip()
        if text:
            demands.append({"id": _slug(text), "text": text, "met": False})
    if not demands:
        return {"error": "Write down what they want (1-4 short demands) - offers are judged against them."}
    n = {"npc": npc_key, "name": npc_name, "topic": str(inp.get("topic") or "").strip() or "terms",
         "stakes": stakes, "goal": GOALS[stakes], "position": _start_position(state, npc_key),
         "demands": demands, "status": "open", "opened_turn": state.get("turn_number", 0),
         "opened_date": state.get("date"), "history": []}
    state.setdefault("negotiations", {})[npc_key] = n
    return {"opened": _summary(n), "note": f"{npc_name} stays in this conversation until it's agreed, the player walks away, or days pass."}


def _match(n, ids):
    out = []
    for raw in ids or []:
        key = _slug(raw)
        hit = next((d for d in n["demands"] if d["id"] == key or key in d["id"] or d["id"] in key), None)
        if hit and hit not in out:
            out.append(hit)
    return out


def offer(state, npc_key, inp, system, base_dc):
    """Rolls the player's offer against base_dc adjusted by the demands it meets or dodges, and moves
    the position. Returns (result, roll, changes) - changes are relationship records for the log."""
    n = open_for(state, npc_key)
    if not n:
        return {"error": "No open negotiation with them - open_negotiation first."}, None, []
    met, dodged = _match(n, inp.get("meets")), _match(n, inp.get("dodges"))
    dodged = [d for d in dodged if d not in met]
    dc = max(5, min(30, base_dc + DEMAND_MET_DC * len(met) + DEMAND_DODGED_DC * len(dodged)))
    roll = system.roll_check(state["character"], inp.get("attribute") or "presence", inp.get("skill"), dc, npc_key)
    for d in met:
        d["met"] = True
    before = n["position"]
    step = MOVE.get(roll["degree"], 0)
    unmet = [d for d in n["demands"] if not d["met"]]
    after = max(-2, min(n["goal"], before + step))
    if after >= n["goal"] and unmet and roll["degree"] != "critical_success":
        after = n["goal"] - 1  # no deal while something they asked for is still unanswered
    n["position"] = after
    changes = []
    if after >= n["goal"]:
        n["status"] = "agreed"
        changes += _trust(state, npc_key, 1, "negotiation agreed")
    elif roll["degree"] == "critical_failure":
        changes += _trust(state, npc_key, -1, "offer gave offence")
    n["history"] = (n["history"] + [{"turn": state.get("turn_number", 0), "offer": str(inp.get("offer") or "")[:200],
                                      "degree": roll["degree"], "from": before, "to": after}])[-6:]
    held = after == n["goal"] - 1 and before + step >= n["goal"]
    result = {
        "npc": n["name"], "position_before": POSITION_WORDS[before], "position_after": POSITION_WORDS[after],
        "position": after, "moved": after - before, "goal": n["goal"], "agreed": n["status"] == "agreed",
        "met_now": [d["text"] for d in met], "dodged": [d["text"] for d in dodged],
        "still_wanted": [d["text"] for d in n["demands"] if not d["met"]],
        "dc_note": f"base {base_dc}, {len(met)} demand(s) met ({DEMAND_MET_DC * len(met):+d}), {len(dodged)} dodged ({DEMAND_DODGED_DC * len(dodged):+d})",
        "gm_instruction": INSTRUCTIONS.get(roll["degree"], "") + (
            " They'd agree, but won't until the unmet demand is answered - have them name it." if held else "") + (
            " The deal is made: narrate them agreeing to these terms." if n["status"] == "agreed" else
            f" {n['name']} stays in the conversation - end on them waiting for the player's next word."),
    }
    return result, roll, changes


def _trust(state, key, delta, cause):
    rel = (state.get("relationships") or {}).get(key)
    if not rel:
        return []
    before = rel["trust"]
    rel["trust"] = max(-5, min(5, before + delta))
    return [{"kind": "relationship", "key": key, "before": before, "after": rel["trust"], "requested_delta": delta,
             "clamped": False, "cause": cause}] if rel["trust"] != before else []


def update(state, npc_key, inp):
    n = open_for(state, npc_key)
    if not n:
        return {"error": "No open negotiation with them."}
    for d in inp.get("add_demands") or []:
        text = str(d).strip()
        if text and len(n["demands"]) < MAX_DEMANDS + 2:
            n["demands"].append({"id": _slug(text), "text": text, "met": False})
    drop = {x["id"] for x in _match(n, inp.get("drop_demands"))}
    n["demands"] = [d for d in n["demands"] if d["id"] not in drop] or n["demands"]
    return {"updated": _summary(n)}


def close(state, npc_key, inp):
    n = open_for(state, npc_key)
    if not n:
        return {"error": "No open negotiation with them."}
    why = inp.get("reason")
    if why not in ("player_walked_away", "time_passed", "events_intervened"):
        return {"error": "A negotiation closes only if the player walks away, days pass, or events really intervene (reason: player_walked_away | time_passed | events_intervened). Otherwise they stay and talk."}
    n["status"] = "closed"
    n["closed_reason"] = why
    return {"closed": n["name"], "position": POSITION_WORDS[n["position"]], "reason": why}


def on_days_passed(state):
    """Talks don't survive days of silence: open negotiations close, keeping where they got to."""
    out = []
    for n in (state.get("negotiations") or {}).values():
        if n.get("status") == "open":
            n["status"] = "closed"
            n["closed_reason"] = "time_passed"
            out.append({"kind": "negotiation_closed", "key": n["npc"], "message": f"Talks with {n['name']} lapse ({POSITION_WORDS[n['position']]})."})
    return out


def prompt_section(state):
    open_ = [n for n in (state.get("negotiations") or {}).values() if n.get("status") == "open"]
    lines = ["NEGOTIATIONS (code-owned. When the player tries to win someone over to a real commitment, open_negotiation",
             "with what that person wants; each concrete offer is negotiation_offer, listing which demands it meets and",
             "which it dodges. Ordinary talk between offers needs no tool. A failed offer stalls; the deal closes when the",
             "position reaches the goal and their demands are answered.)"]
    if not open_:
        lines.append("None open.")
    for n in open_:
        demands = "; ".join(f"[{d['id']}] {d['text']}{' (met)' if d['met'] else ''}" for d in n["demands"])
        lines.append(f"OPEN with {n['name']} [{n['npc']}] on {n['topic']}: {POSITION_WORDS[n['position']]} "
                     f"({n['position']}/{n['goal']}). Demands: {demands}.")
        lines.append(f"  {n['name']} STAYS in this conversation: no bell, messenger, exit or interruption ends it. It ends only "
                     "if the player walks away, days pass, or the deal is made. Close every reply on them waiting for the player.")
    return "\n".join(lines)


def tool_specs():
    return [
        {"name": "open_negotiation",
         "description": "Start a negotiation when the player tries to win a person over to a real commitment (their votes, an alliance, a deal, a betrayal). Write down what THEY want - offers are judged against it. Not for small favours or information (request_check is fine for those).",
         "parameters": {"type": "object", "properties": {
             "npc": {"type": "string", "description": "The NPC's id or name."},
             "topic": {"type": "string", "description": "What is being negotiated, a few words."},
             "stakes": {"type": "string", "enum": ["minor", "significant", "major"], "description": "significant: a vote, a real commitment. major: an alliance, changing sides."},
             "demands": {"type": "array", "items": {"type": "string"}, "description": "1-4 concrete things they want, in their terms, e.g. 'land to the peasant committees at once'."},
         }, "required": ["npc", "topic", "stakes", "demands"]}},
        {"name": "negotiation_offer",
         "description": "The player makes a concrete offer or argument in an open negotiation. Judge it honestly against their demands: list the ids it meets, and the ids it dodges or waters down. The application rolls and moves their position.",
         "parameters": {"type": "object", "properties": {
             "npc": {"type": "string"},
             "offer": {"type": "string", "description": "What the player offered, one line."},
             "meets": {"type": "array", "items": {"type": "string"}, "description": "Demand ids this offer genuinely meets."},
             "dodges": {"type": "array", "items": {"type": "string"}, "description": "Demand ids it sidesteps, hedges or waters down."},
             "attribute": {"type": "string"}, "skill": {"type": "string"},
             "at_stake": {"type": "string", "description": "Optional resource that rides on this negotiation."},
         }, "required": ["npc", "offer", "attribute"]}},
        {"name": "negotiation_update",
         "description": "Add a demand (they raise their price, or reveal what they really want) or drop one they've given up.",
         "parameters": {"type": "object", "properties": {
             "npc": {"type": "string"},
             "add_demands": {"type": "array", "items": {"type": "string"}},
             "drop_demands": {"type": "array", "items": {"type": "string"}},
         }, "required": ["npc"]}},
        {"name": "close_negotiation",
         "description": "Close a negotiation that isn't agreed: only when the player walks away, days pass, or events genuinely intervene.",
         "parameters": {"type": "object", "properties": {
             "npc": {"type": "string"},
             "reason": {"type": "string", "enum": ["player_walked_away", "time_passed", "events_intervened"]},
         }, "required": ["npc", "reason"]}},
    ]
