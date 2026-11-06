"""
Parties and their wings in a seat_chart body (2026-11-06).

A faction ("bloc") may carry a "party": the Bolsheviks are one party with two wings, the Mensheviks
(defensists, Martov's internationalists) and the SRs (the leadership, the Left SRs) the same. Votes
and the tracker read party first, wings second.

The structure only changes through reorganize_blocs, and the application is strict about it:
  - move_delegates: a few delegates drift from one bloc to another (at most 10% of the source,
    two moves per body per day);
  - new_wing: a party's bloc divides. Only once that bloc has split in at least NEW_WING_STRAIN
    votes since its last change; at most MAX_WINGS wings per party;
  - merge_wings: two wings of a party close ranks;
  - break_away: a wing leaves its party and becomes a party of its own. Only once it has voted
    against the rest of its party at least BREAK_STRAIN times;
  - found_party: a non-party bloc organises as a party.
new_wing and merge_wings share a 14-day cooldown per body; break_away and found_party a 30-day one.
Strain is counted by the application from the actual counts (record_vote, record_election), so
the GM can't declare a split into being - it has to happen on the floor first.
State, kept in the chart data (and preserved by the validator): "parties" {name: note},
"strain" {"blocs": {name: n}, "wings": {name: n}}, "reorg" {"last": {kind: date}, "moves": {date: n},
"log": [...]}.
"""

import dynamics

MOVE_MAX_SHARE = 0.10
MOVES_PER_DAY = 2
NEW_WING_STRAIN = 3
BREAK_STRAIN = 4
MAX_WINGS = 3
MAX_FACTIONS = 10
WING_COOLDOWN_DAYS = 14
PARTY_COOLDOWN_DAYS = 30
FOUND_MIN_SHARE = 0.05
DECIDED = 0.10   # a wing "went one way" only if its yes-no margin is at least this share of its votes cast
SPLIT_MINORITY = 0.25


def party_of(f):
    return f.get("party") or None


def wings(factions, party):
    return [f for f in factions if f.get("party") == party]


def groups(factions):
    """[(party or None, [factions])] in chart order, each party at its first wing."""
    out, seen = [], {}
    for f in factions:
        p = party_of(f)
        if p is None:
            out.append((None, [f]))
        elif p in seen:
            seen[p].append(f)
        else:
            seen[p] = [f]
            out.append((p, seen[p]))
    return out


def _strain(data):
    s = data.setdefault("strain", {})
    s.setdefault("blocs", {})
    s.setdefault("wings", {})
    return s


def _direction(yes, no):
    cast = yes + no
    if cast <= 0 or abs(yes - no) < DECIDED * cast:
        return 0
    return 1 if yes > no else -1


def record_vote(data, bloc_results):
    """After a vote: a bloc that divided (minority >= 25%) gains bloc strain; a wing whose majority went
    the other way from its party's gains wing strain."""
    s = _strain(data)
    for b in bloc_results:
        cast = b["yes"] + b["no"]
        if cast >= 4 and min(b["yes"], b["no"]) >= SPLIT_MINORITY * cast:
            s["blocs"][b["name"]] = s["blocs"].get(b["name"], 0) + 1
    by_party = {}
    for b in bloc_results:
        if b.get("party"):
            by_party.setdefault(b["party"], []).append(b)
    for party, ws in by_party.items():
        if len(ws) < 2:
            continue
        line = _direction(sum(w["yes"] for w in ws), sum(w["no"] for w in ws))
        for w in ws:
            d = _direction(w["yes"], w["no"])
            if line and d and d != line:
                s["wings"][w["name"]] = s["wings"].get(w["name"], 0) + 1


def record_election(data, blocs):
    """After a ballot: a wing whose favourite isn't its party's favourite gains wing strain."""
    s = _strain(data)
    by_party = {}
    for b in blocs:
        if b.get("party"):
            by_party.setdefault(b["party"], []).append(b)
    for party, ws in by_party.items():
        if len(ws) < 2:
            continue
        total = {}
        for w in ws:
            for c, n in (w.get("votes") or {}).items():
                total[c] = total.get(c, 0) + n
        if not total:
            continue
        line = max(total, key=total.get)
        for w in ws:
            v = w.get("votes") or {}
            if v and sum(v.values()) and max(v, key=v.get) != line:
                s["wings"][w["name"]] = s["wings"].get(w["name"], 0) + 1


def _find(factions, name):
    if not name:
        return None
    n = str(name).strip().lower()
    return next((f for f in factions if f["name"].lower() == n), None)


def _cooldown(data, kinds, days, today):
    last = [d for k, d in ((data.get("reorg") or {}).get("last") or {}).items() if k in kinds and d]
    if not last:
        return None
    latest = max(last)
    gone = dynamics.days_between(latest, today)
    if gone is not None and gone < days:
        return dynamics.add_days(latest, days)
    return None


def _refuse(why, **extra):
    return {"refused": True, "reason": why, **extra,
            "gm_instruction": ("The application refused this change to the body's make-up. Don't narrate it as having "
                               "happened. Tension inside a party can still be shown - grumbling, a walk-out from a "
                               "caucus, a hostile speech - without the party dividing.")}


def reorganize(state, chart, inp):
    data = chart["data"]
    factions = data["factions"]
    action = inp.get("action")
    today = state.get("date") or ""
    cause = str(inp.get("cause") or "").strip()
    if len(cause) < 15:
        return _refuse("Give the cause: what happened on-screen that did this (a speech, a vote, a betrayal).")
    reorg = data.setdefault("reorg", {})
    reorg.setdefault("last", {})
    reorg.setdefault("log", [])
    s = _strain(data)
    total = sum(f["seats"] for f in factions) or 1

    if action == "move_delegates":
        src, dst = _find(factions, inp.get("from")), _find(factions, inp.get("to"))
        if not src or not dst or src is dst:
            return _refuse(f"Name two different blocs from the chart: {[f['name'] for f in factions]}.")
        moves = reorg.setdefault("moves", {})
        moves_today = moves.get(today, 0)
        if moves_today >= MOVES_PER_DAY:
            return _refuse("Enough delegates have changed sides today; more can drift on another day.")
        try:
            n = int(inp.get("seats") or 0)
        except (TypeError, ValueError):
            n = 0
        cap = max(1, int(src["seats"] * MOVE_MAX_SHARE))
        if n <= 0:
            return _refuse("How many delegates?")
        note = None
        if n > cap:
            note, n = f"limited to {cap} ({int(MOVE_MAX_SHARE * 100)}% of {src['name']})", cap
        src["seats"] -= n
        dst["seats"] += n
        reorg["moves"] = {today: moves_today + 1}
        entry = {"date": today, "action": action, "from": src["name"], "to": dst["name"], "seats": n, "cause": cause[:200]}
        reorg["log"] = (reorg["log"] + [entry])[-30:]
        return {"done": True, **entry, **({"note": note} if note else {}),
                "gm_instruction": f"{n} delegates have gone over from {src['name']} to {dst['name']}. Narrate it as it happened."}

    if action == "new_wing":
        src = _find(factions, inp.get("from"))
        name = str(inp.get("name") or "").strip()[:60]
        if not src or not src.get("party"):
            return _refuse("new_wing divides a bloc that belongs to a party (found_party first for a non-party bloc).")
        party = src["party"]
        if not name or _find(factions, name) or name.lower() == party.lower():
            return _refuse("The new wing needs a new name.")
        if len(wings(factions, party)) >= MAX_WINGS:
            return _refuse(f"{party} already has {MAX_WINGS} wings; merge two first, or a wing breaks away.")
        if len(factions) >= MAX_FACTIONS:
            return _refuse("The chart has as many blocs as it can sensibly show.")
        strain = s["blocs"].get(src["name"], 0)
        if strain < NEW_WING_STRAIN:
            return _refuse(f"{src['name']} hasn't divided on the floor enough to split: it has split in {strain} of the "
                           f"{NEW_WING_STRAIN} votes needed.", strain=strain, needed=NEW_WING_STRAIN)
        until = _cooldown(data, ("new_wing", "merge_wings", "break_away", "found_party"), WING_COOLDOWN_DAYS, today)
        if until:
            return _refuse(f"The body's make-up changed recently; the next regrouping can come on {until} at the earliest.")
        try:
            n = int(inp.get("seats") or 0)
        except (TypeError, ValueError):
            n = 0
        lo, hi = max(1, round(src["seats"] * 0.10)), round(src["seats"] * 0.45)
        n = max(lo, min(hi, n or lo))
        try:
            lean = int(inp.get("lean", src.get("lean", 0)))
        except (TypeError, ValueError):
            lean = src.get("lean", 0)
        lean = max(src.get("lean", 0) - 1, min(src.get("lean", 0) + 1, lean))
        src["seats"] -= n
        new = {"name": name, "seats": n, "lean": max(-3, min(3, lean)), "note": str(inp.get("note") or "")[:300], "party": party}
        factions.insert(factions.index(src) + 1, new)
        s["blocs"][src["name"]] = 0
        reorg["last"]["new_wing"] = today
        entry = {"date": today, "action": action, "party": party, "from": src["name"], "name": name, "seats": n, "cause": cause[:200]}
        reorg["log"] = (reorg["log"] + [entry])[-30:]
        return {"done": True, **entry, "gm_instruction": f"{party} now has a new wing, {name} ({n} delegates). Narrate how it formed."}

    if action == "merge_wings":
        a, b = _find(factions, inp.get("from")), _find(factions, inp.get("into"))
        if not a or not b or a is b or not a.get("party") or a.get("party") != b.get("party"):
            return _refuse("merge_wings joins two wings of the same party.")
        until = _cooldown(data, ("new_wing", "merge_wings", "break_away", "found_party"), WING_COOLDOWN_DAYS, today)
        if until:
            return _refuse(f"The body's make-up changed recently; the next regrouping can come on {until} at the earliest.")
        if s["wings"].get(a["name"], 0) >= 2 or s["wings"].get(b["name"], 0) >= 2:
            return _refuse("These wings have been voting against each other; they won't close ranks yet.")
        b["seats"] += a["seats"]
        factions.remove(a)
        s["wings"].pop(a["name"], None)
        s["blocs"].pop(a["name"], None)
        reorg["last"]["merge_wings"] = today
        entry = {"date": today, "action": action, "party": b["party"], "from": a["name"], "into": b["name"], "cause": cause[:200]}
        reorg["log"] = (reorg["log"] + [entry])[-30:]
        return {"done": True, **entry, "gm_instruction": f"{a['name']} has merged into {b['name']}."}

    if action in ("break_away", "found_party"):
        bloc = _find(factions, inp.get("wing") or inp.get("bloc"))
        if not bloc:
            return _refuse(f"Name a bloc from the chart: {[f['name'] for f in factions]}.")
        new_party = str(inp.get("new_party") or bloc["name"]).strip()[:60]
        if action == "break_away":
            if not bloc.get("party") or len(wings(factions, bloc["party"])) < 2:
                return _refuse("break_away is for a wing of a party with more than one wing.")
            strain = s["wings"].get(bloc["name"], 0)
            if strain < BREAK_STRAIN:
                return _refuse(f"{bloc['name']} hasn't broken with its party on the floor often enough to leave it: it has "
                               f"voted against the party line {strain} of the {BREAK_STRAIN} times needed.",
                               strain=strain, needed=BREAK_STRAIN)
        else:
            if bloc.get("party"):
                return _refuse("found_party is for a bloc that doesn't belong to a party.")
            if bloc["seats"] < FOUND_MIN_SHARE * total:
                return _refuse("Too few delegates to organise as a party in this body.")
            if new_party.lower() in {(f.get("party") or "").lower() for f in factions}:
                return _refuse("That party already exists; move delegates into it instead.")
        until = _cooldown(data, ("break_away", "found_party"), PARTY_COOLDOWN_DAYS, today)
        if until:
            return _refuse(f"A party split or formed here recently; the next can come on {until} at the earliest.")
        if any((f.get("party") or "").lower() == new_party.lower() for f in factions if f is not bloc):
            return _refuse("A party of that name already sits here.")
        old = bloc.get("party")
        bloc["party"] = new_party
        if inp.get("note"):
            data.setdefault("parties", {})[new_party] = str(inp["note"])[:300]
        s["wings"].pop(bloc["name"], None)
        reorg["last"][action] = today
        entry = {"date": today, "action": action, "bloc": bloc["name"], "old_party": old, "new_party": new_party, "cause": cause[:200]}
        reorg["log"] = (reorg["log"] + [entry])[-30:]
        what = f"{bloc['name']} has left {old} and sits as {new_party}." if old else f"{bloc['name']} has organised as {new_party}."
        return {"done": True, **entry, "gm_instruction": what + " A real rupture: narrate it, and who it hurts."}

    return _refuse("Unknown action.")


def migrate(state, catalog):
    """Saves from before a party had wings: fill in the party of blocs the catalog assigns one (party_of),
    unless the bloc already has a party, and the parties' notes. Never undoes a break-away."""
    changes = []
    for iid, m in (state.get("mechanics") or {}).items():
        entry = (catalog or {}).get(m.get("type_key"))
        if m.get("render_primitive") != "seat_chart" or not isinstance(entry, dict):
            continue
        rule = entry.get("party_of") or {}
        notes = ((entry.get("seed_data") or {}).get("parties")) or {}
        data = m["data"]
        if (data.get("reorg") or {}).get("log"):
            rule = {}  # the body has been reorganised in play; leave its parties as they are now
        for f in data.get("factions", []):
            if not f.get("party") and f["name"] in rule:
                f["party"] = rule[f["name"]]
                changes.append({"kind": "mechanic_updated", "instance_id": iid, "note": f"{f['name']} under {rule[f['name']]}"})
        present = {f.get("party") for f in data.get("factions", [])}
        for p, note in notes.items():
            if p in present and p not in (data.get("parties") or {}):
                data.setdefault("parties", {})[p] = note
        order = [f for _, ws in groups(data.get("factions", [])) for f in ws]
        if order != data.get("factions"):
            data["factions"] = order  # wings of a party sit together
    return changes


def prompt_lines(state):
    lines = []
    for iid, m in (state.get("mechanics") or {}).items():
        if m.get("render_primitive") != "seat_chart":
            continue
        data = m["data"]
        if not any(f.get("party") for f in data.get("factions", [])):
            continue
        s = data.get("strain") or {}
        parts = []
        for party, ws in groups(data["factions"]):
            if party and (len(ws) > 1 or ws[0]["name"] != party):
                inner = ", ".join(f"{w['name']} {w['seats']}" + (f" [strained {s.get('wings', {}).get(w['name'])}]"
                                                                  if (s.get("wings") or {}).get(w["name"]) else "") for w in ws)
                parts.append(f"{party} {sum(w['seats'] for w in ws)} ({inner})")
            else:
                parts.append(f"{ws[0]['name']} {ws[0]['seats']}" + (f" [{party}]" if party and party != ws[0]["name"] else ""))
        lines.append(f"- {m.get('title', iid)}: " + "; ".join(parts))
    if not lines:
        return ""
    return ("PARTIES AND WINGS. Speak of a party as one body first, then of its wings where they differ (\"the Bolsheviks "
            "voted for, though Kamenev's people were uneasy\"). Wings are moods inside a party, not separate parties.\n"
            + "\n".join(lines) + "\nThe make-up changes only through reorganize_blocs, and rarely: the application refuses a "
            "new wing until a bloc has divided on the floor several times, and a break-away until a wing has repeatedly "
            "voted against its own party. Delegates drifting a few at a time is move_delegates.")


SPEC = {
    "name": "reorganize_blocs",
    "description": ("Change who sits where in a body with a seat_chart. Rare. move_delegates: a few delegates go over "
                    "(at most 10% of a bloc). new_wing: a party's bloc divides (needs that bloc to have split in "
                    "several votes). merge_wings: two wings close ranks. break_away: a wing leaves its party (needs it to "
                    "have voted against the party several times). found_party: a non-party bloc organises. The "
                    "application refuses changes the floor hasn't earned - then show the tension without the split."),
    "parameters": {"type": "object", "properties": {
        "instance_id": {"type": "string"},
        "action": {"type": "string", "enum": ["move_delegates", "new_wing", "merge_wings", "break_away", "found_party"]},
        "from": {"type": "string", "description": "move_delegates/new_wing/merge_wings: the bloc delegates come from."},
        "to": {"type": "string", "description": "move_delegates: the bloc they go to."},
        "into": {"type": "string", "description": "merge_wings: the wing that absorbs the other."},
        "wing": {"type": "string", "description": "break_away: the wing that leaves."},
        "bloc": {"type": "string", "description": "found_party: the non-party bloc."},
        "name": {"type": "string", "description": "new_wing: the new wing's name, e.g. 'Bolsheviks (Zinoviev's followers)'."},
        "new_party": {"type": "string", "description": "break_away/found_party: the party's name."},
        "seats": {"type": "integer"},
        "lean": {"type": "integer", "description": "new_wing: within one step of the bloc it came from."},
        "note": {"type": "string"},
        "cause": {"type": "string", "description": "What happened on-screen that did this."},
    }, "required": ["instance_id", "action", "cause"]},
}
