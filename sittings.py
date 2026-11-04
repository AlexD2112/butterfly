"""
Sittings and the agenda (2026-11-03): assemblies take days, not minutes.

Reported: in one evening the Soviet voted power to the Soviets, peace and land, land committees,
no confidence in the presidium, a new presidium, vice-chairs, the ballot procedure and a chairman.
Historically the no-confidence vote came on 9 September and the new presidium - Trotsky in the
chair - on 25 September; in between were caucuses, section meetings and days of argument.

So a body with a seat_chart now meets in sittings:
  - a sitting can only get through a few votes (votes_per_sitting); after that the hour is late,
    delegates drift off and the chair adjourns;
  - the body meets again only after some days (sits_every_days);
  - a major question (who leads the body, what it stands for) can't be voted the day it's raised:
    it goes on the agenda for the next sitting, unless an urgency motion carries it - which needs
    two-thirds;
  - procedural votes (order of business, adjournment) count for half; each election ballot for half.
Rules live in the mechanics catalog entry's "procedure"; defaults below. State: chart data
"sitting" {date, used} and "tabled" {motion_key: date it may be voted}.
"""

import geography

DEFAULT = {"sits_every_days": 2, "votes_per_sitting": 4, "major_needs_tabling": True}
WEIGHTS = {"procedural": 0.5, "substantive": 1.0, "major": 1.0, "ballot": 0.5}


def procedure(catalog, mechanic):
    entry = (catalog or {}).get(mechanic.get("type_key")) or {}
    return {**DEFAULT, **(entry.get("procedure") or {})} if isinstance(entry, dict) else dict(DEFAULT)


def _key(inp):
    return (inp.get("motion_key") or inp.get("motion") or inp.get("office") or "").strip().lower()[:80]


def check(state, catalog, mechanic, inp, kind="vote"):
    """None if the vote can be taken now, else a result dict explaining why not (and, for a new major
    question, tabling it). Call record() after a vote is actually taken."""
    proc = procedure(catalog, mechanic)
    data = mechanic["data"]
    today = state.get("date") or ""
    sitting = data.get("sitting") or {}
    weight = "ballot" if kind == "election" else (inp.get("weight") or "substantive")
    cost = WEIGHTS.get(weight, 1.0)
    title = mechanic.get("title", "the body")
    if sitting.get("date") == today:
        if sitting.get("used", 0) + cost > proc["votes_per_sitting"] + 1e-9 and not inp.get("extraordinary"):
            nxt = geography._add_days(today, proc["sits_every_days"])
            return {"not_voted": True, "reason": "sitting_over",
                    "gm_instruction": (f"{title} has been sitting for hours and has got through all it can tonight: "
                                       f"delegates are leaving, the chair adjourns. The next sitting is on {nxt}. Narrate the "
                                       "adjournment, the corridors and the caucuses; pass days to reach it.")}
    elif sitting.get("date") and today < geography._add_days(sitting["date"], proc["sits_every_days"]) and not inp.get("extraordinary"):
        nxt = geography._add_days(sitting["date"], proc["sits_every_days"])
        return {"not_voted": True, "reason": "not_sitting",
                "gm_instruction": (f"{title} isn't sitting today; it meets next on {nxt}. Until then the work is in "
                                   "faction meetings, sections, committees and private talks - play those, or pass days.")}
    if kind == "vote" and weight == "major" and proc["major_needs_tabling"] and not inp.get("urgency"):
        tabled = data.setdefault("tabled", {})
        k = _key(inp)
        if k not in tabled:
            when = geography._add_days(today, proc["sits_every_days"])
            tabled[k] = when
            return {"not_voted": True, "reason": "tabled", "tabled_until": when,
                    "gm_instruction": (f"A question this big isn't voted the night it's raised. It goes on the agenda for the "
                                       f"sitting of {when}: factions will caucus, waverers will be lobbied. Narrate the motion "
                                       "being put down and the room's first reaction. (Or the player can move urgency: a vote "
                                       "on it now needing two-thirds - call again with urgency: true.)")}
        if today < tabled[k]:
            return {"not_voted": True, "reason": "tabled", "tabled_until": tabled[k],
                    "gm_instruction": f"This is on the agenda for {tabled[k]}; it can't be voted before then (or move urgency)."}
    return None


def record(state, mechanic, inp, kind="vote"):
    data = mechanic["data"]
    today = state.get("date") or ""
    weight = "ballot" if kind == "election" else (inp.get("weight") or "substantive")
    sitting = data.get("sitting") or {}
    if sitting.get("date") != today:
        sitting = {"date": today, "used": 0}
    sitting["used"] = round(sitting.get("used", 0) + WEIGHTS.get(weight, 1.0), 2)
    data["sitting"] = sitting
    (data.get("tabled") or {}).pop(_key(inp), None)
    return sitting


def spec_properties():
    return {
        "weight": {"type": "string", "enum": ["procedural", "substantive", "major"], "description": (
            "procedural: order of business, adjournment, how to vote. substantive: an ordinary resolution. major: who "
            "leads the body or what it stands for (power, no-confidence, a new presidium) - put on the agenda for the next "
            "sitting unless urgency carries.")},
        "urgency": {"type": "boolean", "description": "Vote a major question the day it's raised: needs two-thirds."},
        "extraordinary": {"type": "boolean", "description": "An emergency session outside the normal sittings (a coup, an invasion) - rare."},
    }
