"""
Letters, couriers and telegrams (2026-11-03): messages take real time to travel.

Reported: the player sent Lenin (in Finland) an account of the coalition, and in the same scene a
courier arrived with Lenin's angry reply to it. Nothing knew how far away Lenin was or how long a
letter takes.

Now:
  - every NPC has a location (npc_sheets.json: location, or the setting's default_location;
    moved in play with npc_location), so code knows Lenin is in Helsingfors and Kornilov in Bykhov;
  - send_message: the player's letter travels by courier along the geography graph (or, where the
    setting has one, by telegram - fast, but read by whoever runs the wires); it arrives at a
    date code works out, and only then does the recipient know what it says (knowledge.py);
  - npc_message: a far-off NPC's letter to the player can only set out once that NPC could know
    what they're writing about, and arrives after the same travel time. The GM narrates it when
    code says it has arrived - never sooner;
  - the prompt lists who is far away and how long a courier takes, and every letter on the road.

state["_mail"] = [{id, sender, recipient, text, means, sent, arrives, hours, status, fact}]
"""

import math

import geography
import knowledge
import npcs

COURIER_EXTRA_HOURS = 2       # finding a reliable man, handing over, finding the recipient
FAR_HOURS = 4.0               # further than this (travel alone), someone is listed as far away
PROMPT_LIMIT = 10


def _mail(state):
    return state.setdefault("_mail", [])


def _add_days(iso, days):
    return geography._add_days(iso, days) if iso else iso


def player_place(state, geo):
    return (state.get("geo") or {}).get("player") or (geo or {}).get("player_start")


def travel_hours(state, setting_key, geo, a, b):
    """Courier time between two places: the fastest route for someone travelling light, plus handling."""
    if not geo or not a or not b:
        return None
    if a == b:
        return 0.5
    r = geography.route(geo, state, a, b, geography.PLAYER)
    return None if r is None else round(r["hours"] + COURIER_EXTRA_HOURS, 1)


def _eta(state, hours):
    days = 0 if hours <= geography_same_scene(state) else math.ceil(hours / 24)
    return days, _add_days(state.get("date"), days)


def geography_same_scene(state):
    return 12


def send(state, setting_key, inp):
    """The player sends someone a letter (or a telegram)."""
    geo = geography.load(setting_key)
    to = npcs.resolve(state, setting_key, inp.get("to")) or knowledge._slug(inp.get("to"))
    if not to:
        return {"error": "Who is it to?"}
    sheets = npcs.all_sheets(state, setting_key)
    name = (sheets.get(to) or {}).get("name") or str(inp.get("to"))
    where = npcs.location(state, setting_key, to)
    here = player_place(state, geo)
    means = inp.get("means") or "courier"
    tele = (geo or {}).get("telegraph")
    if means == "telegram":
        if not tele:
            return {"error": "There's no telegraph here; send a courier."}
        hours = tele["hours"]
    else:
        means = "courier"
        hours = travel_hours(state, setting_key, geo, here, where)
        if hours is None:
            return {"error": f"No courier can get through to {name} right now (no open way to {geography.place_name(geo, where)})."}
    text = str(inp.get("text") or "").strip()[:300]
    if not text:
        return {"error": "What does it say? (text)"}
    who = state.get("character", {}).get("name", "The player").split(" (")[0]
    fact = knowledge.record(state, setting_key, f"{who}'s {means} to {name}: {text}", [], inp.get("secrecy") or "private")
    fact_id = fact.get("recorded")
    if means == "telegram" and fact_id:
        f = next(x for x in knowledge._facts(state) if x["id"] == fact_id)
        f["groups"] = sorted(set(f["groups"]) | set(tele.get("read_by", [])))  # the wires are theirs
    days, arrives = _eta(state, hours)
    msg = {"id": f"m{len(_mail(state)) + 1}", "sender": "player", "recipient": to, "recipient_name": name, "text": text,
           "means": means, "sent": state.get("date"), "arrives": arrives, "hours": hours, "status": "in_transit", "fact": fact_id,
           "to_place": where}
    _mail(state).append(msg)
    out = {"sent": msg["id"], "to": name, "where": geography.place_name(geo, where) if geo else where, "means": means,
           "travel": geography.fmt_hours(hours)}
    if days == 0:
        deliver(state, setting_key, msg)
        out["delivered"] = True
        out["note"] = f"{name} is close by: it reaches them within the scene."
    else:
        out["arrives"] = arrives
        out["note"] = (f"{name} won't read it until {arrives}. Until then they know nothing of it, and no reply can come "
                       f"before {_add_days(arrives, days)} at the earliest.")
    if means == "telegram" and tele.get("read_by"):
        out["warning"] = f"Telegrams pass through hands that read them: {', '.join(tele['read_by'])} will know what it says."
    return out


def deliver(state, setting_key, msg):
    msg["status"] = "delivered"
    if msg["sender"] == "player" and msg.get("fact"):
        knowledge.record(state, setting_key, None, [msg["recipient"]], fact_id=msg["fact"])
    return {"kind": "mail_delivered", "gm_only": msg["sender"] == "player", "message": (
        f"{msg['recipient_name']} has received the {msg['means']}: {msg['text']}" if msg["sender"] == "player"
        else f"A {msg['means']} from {msg['sender_name']} has arrived for the player: {msg['text']}")}


def npc_send(state, setting_key, inp):
    """A distant NPC writes to the player. It leaves now and arrives after real travel time."""
    geo = geography.load(setting_key)
    sender = npcs.resolve(state, setting_key, inp.get("from")) or knowledge._slug(inp.get("from"))
    sheets = npcs.all_sheets(state, setting_key)
    name = (sheets.get(sender) or {}).get("name") or str(inp.get("from"))
    where = npcs.location(state, setting_key, sender)
    here = player_place(state, geo)
    hours = travel_hours(state, setting_key, geo, where, here)
    if hours is None:
        return {"error": f"No courier from {name} can get through right now."}
    about = inp.get("about_fact")
    if about:
        fact = next((f for f in knowledge._facts(state) if f["id"] == about), None)
        if fact and not knowledge.knows(state, setting_key, sender, fact):
            return {"error": f"{name} doesn't know about [{about}] yet - they can't be answering it. Check WHO KNOWS WHAT and MAIL."}
    text = str(inp.get("text") or "").strip()[:300]
    days, arrives = _eta(state, hours)
    msg = {"id": f"m{len(_mail(state)) + 1}", "sender": sender, "sender_name": name, "recipient": "player", "text": text,
           "means": "courier", "sent": state.get("date"), "arrives": arrives, "hours": hours, "status": "in_transit"}
    _mail(state).append(msg)
    if days == 0:
        deliver(state, setting_key, msg)
        return {"sent": msg["id"], "delivered": True, "note": f"{name} is near enough: it arrives within the scene."}
    return {"sent": msg["id"], "arrives": arrives, "note": f"It's on the road. Don't narrate it arriving: it reaches the player on {arrives}."}


def deliver_due(state, setting_key):
    """Called as days pass: everything whose date has come is delivered."""
    out = []
    today = state.get("date") or ""
    for msg in _mail(state):
        if msg["status"] == "in_transit" and msg.get("arrives") and msg["arrives"] <= today:
            out.append(deliver(state, setting_key, msg))
    return out


def set_location(state, setting_key, inp):
    geo = geography.load(setting_key)
    key = npcs.resolve(state, setting_key, inp.get("npc"))
    if not key:
        return {"error": "Only someone with a sheet has a tracked location."}
    node, err = geography.resolve_place(geo, state, inp.get("at")) if geo else (None, "no geography")
    if err:
        return {"error": err}
    before = npcs.location(state, setting_key, key)
    state.setdefault("_npc_locations", {})[key] = node
    hours = travel_hours(state, setting_key, geo, before, node)
    return {"npc": key, "now_at": geography.place_name(geo, node),
            "note": f"That journey takes about {geography.fmt_hours(hours)}; only move them once they could have made it." if hours else ""}


def prompt_section(state, setting_key):
    geo = geography.load(setting_key)
    if not geo or not npcs.load(setting_key):
        return ""
    here = player_place(state, geo)
    sheets = npcs.all_sheets(state, setting_key)
    far = []
    for key, sheet in sheets.items():
        where = npcs.location(state, setting_key, key)
        hours = travel_hours(state, setting_key, geo, here, where)
        if hours and hours - COURIER_EXTRA_HOURS > FAR_HOURS:
            far.append(f"{sheet['name']} [{key}] at {geography.place_name(geo, where)} (a courier takes {geography.fmt_hours(hours)})")
    lines = ["MAIL AND DISTANCE (code-owned. Anyone not listed as far away is within reach today. Messages to or from far-off",
             "people go through send_message / npc_message and take the time shown - nobody far away hears, answers or",
             "reacts to something before it could have reached them, and no courier arrives before code delivers it.)"]
    if far:
        lines.append("Far away: " + "; ".join(far) + ".")
    tele = geo.get("telegraph")
    if tele:
        lines.append(f"Telegrams take about {tele['hours']} hours but are read by: {', '.join(tele.get('read_by', []))}.")
    moving = [m for m in _mail(state) if m["status"] == "in_transit"][-PROMPT_LIMIT:]
    for m in moving:
        if m["sender"] == "player":
            lines.append(f"On the road: [{m['id']}] {m['means']} to {m['recipient_name']} - arrives {m['arrives']}: {m['text']}")
        else:
            lines.append(f"On the road: [{m['id']}] courier from {m['sender_name']} to the player - arrives {m['arrives']}.")
    recent = [m for m in _mail(state) if m["status"] == "delivered" and m.get("arrives") == state.get("date")][-5:]
    for m in recent:
        lines.append(f"Arrived today: [{m['id']}] " + (f"{m['recipient_name']} has read the player's {m['means']}."
                                                     if m["sender"] == "player" else f"letter from {m['sender_name']}: {m['text']}"))
    return "\n".join(lines)


TOOL_SPECS = [
    {"name": "send_message",
     "description": "The player sends a letter, note or courier (or a telegram, where there is a telegraph) to someone. Code works out when it arrives; the recipient knows nothing of it until then.",
     "parameters": {"type": "object", "properties": {
         "to": {"type": "string", "description": "The recipient's id or name."},
         "text": {"type": "string", "description": "What it says, one or two lines."},
         "means": {"type": "string", "enum": ["courier", "telegram"]},
         "secrecy": {"type": "string", "enum": ["secret", "private", "public"]},
     }, "required": ["to", "text"]}},
    {"name": "npc_message",
     "description": "Someone far away sends the player a letter or courier. It leaves now and arrives when code says - narrate it only when MAIL says it has arrived. If it answers something, pass about_fact: code refuses if they can't know about it yet.",
     "parameters": {"type": "object", "properties": {
         "from": {"type": "string"}, "text": {"type": "string"},
         "about_fact": {"type": "string", "description": "A WHO KNOWS WHAT fact id this letter responds to."},
     }, "required": ["from", "text"]}},
    {"name": "npc_location",
     "description": "Record that someone with a sheet is now somewhere else (they travelled, fled, were arrested and moved). Only once they could have made the journey.",
     "parameters": {"type": "object", "properties": {
         "npc": {"type": "string"}, "at": {"type": "string", "description": "A place from GEOGRAPHY."},
     }, "required": ["npc", "at"]}},
]
