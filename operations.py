"""
Operations: the code-resolved actions that act on mechanics (2026-10-26).

mechanics.py made trackers exist (a seat chart, a garrison's order of battle). Until this module,
nothing ever read them - a vote's result or a street fight's outcome was whatever the GM narrated,
with the tracker updated afterwards to match. These three tools invert that: the application reads
the tracker, rolls, decides the outcome, writes it back, and hands the GM a result to narrate.

  call_vote             - a formal vote in a body that has a seat_chart mechanic
  sway_unit             - an attempt to win over (or stand down) one unit in a forces mechanic
  resolve_confrontation - an armed confrontation between units in a forces mechanic

Conventions shared by all three:
  - "lean" (seat_chart blocs) and "loyalty" (forces units) both run -3..+3 toward the PLAYER'S cause.
    +3 will act for the player unprompted, 0 is neutral, -3 will act against them.
  - A forces unit only takes the field for a side at |loyalty| >= 2. Loyalty -1..+1 means it stays in
    barracks. That rule is the heart of Petrograd 1917: October was decided by which regiments
    refused to move, not by which won a battle.
  - The player's own skill matters through a real roll_check made by the application (speech before
    a vote, an appeal to a regiment, command in a fight) - the GM picks the attribute/skill, the code
    sets the DC from the tracker's state.

Pure functions over the state dict plus a `system` module (for roll_check, so feat bonuses apply).
`rng` is injectable for tests. Nothing here calls a model.
"""

import math
import random

from systems._shared import core

# ---------------------------------------------------------------- votes

SPEECH_SHIFT = {
    "critical_success": 0.18,
    "strong_success": 0.12,
    "success": 0.08,
    "success_at_cost": 0.04,
    "failure": 0.0,
    "critical_failure": -0.08,
}
# How far a speech can move a bloc, by how firmly that bloc already leans either way.
PERSUADABILITY = {0: 1.0, 1: 1.0, 2: 0.5, 3: 0.0}
LEAN_WEIGHT = 0.16
ABSTAIN_RATE = {0: 0.15, 1: 0.06, 2: 0.03, 3: 0.02}
BLOC_NOISE = 0.06
# Contested votes (2026-11-03). Reported: every motion carried ~600-350 because a bloc's lean toward
# the player decided everything - the Bolsheviks voted as one for Martov as vice-chair. Now each
# vote also takes the bloc's stance on THIS motion (does it serve them?), party loyalty counts for
# less, members pulled two ways split and abstain, and a motion put cold to the body finds many
# delegates undecided - who mostly abstain or stick with the status quo - until it's been debated
# and voted on again.
STANCE_LEAN_WEIGHT = 0.08         # loyalty to the player's camp, when the motion's merits are also known
STANCE_WEIGHT = 0.15              # the motion's merits for that bloc, -2..+2
CROSS_PRESSURE_NOISE = 0.15
CROSS_PRESSURE_ABSTAIN = 0.08
UNDECIDED_BY_ROUND = [0.22, 0.11, 0.05]   # share of a bloc still making up its mind, first vote on a motion, second, third
UNDECIDED_SPLIT = (0.5, 0.35, 0.15)       # how the undecided go: abstain, no (status quo), yes


def vote_tool_spec():
    return {
        "name": "call_vote",
        "description": (
            "Resolve a formal vote in a body that has an active seat_chart mechanic (the Soviet, the "
            "Central Committee, a congress). The application decides the result from each bloc's seats "
            "and lean, the player's influence, an optional speech the player makes first (rolled by the "
            "application), and dice. Never narrate a formal vote's result without calling this - narrate "
            "the result it returns. Optionally name the canon event this vote would fire if it passes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "The seat_chart mechanic's instance_id."},
                "motion": {"type": "string", "description": "The motion, in a short sentence."},
                "player_position": {"type": "string", "enum": ["for", "against", "abstain"],
                                    "description": "Where the player character stands on the motion."},
                "speech_attribute": {"type": ["string", "null"], "enum": core.ATTRIBUTES + [None],
                                     "description": "If the player speaks before the vote: the attribute (usually oration)."},
                "speech_skill": {"type": ["string", "null"], "description": "Skill for the speech, if any."},
                "threshold": {"type": "string", "enum": ["majority", "two_thirds"], "description": "Defaults to majority of votes cast."},
                "motion_alignment": {"type": "integer", "enum": [1, -1], "description": (
                    "Only for a chart whose data has lean_toward (its own axis, e.g. 'immediate armed insurrection'): "
                    "1 if a YES vote on this motion moves in that direction, -1 if it moves against it. Ignored otherwise.")},
                "canon_event": {"type": ["string", "null"], "description": "Canon event key this vote fires if it passes (subject to that event's requirements)."},
                "bloc_stances": {"type": "object", "description": (
                    "For EVERY bloc on the chart: how this particular motion squares with that bloc's own interests and "
                    "programme, -2 (against everything they stand for) .. +2 (exactly what they want) - judged on the "
                    "motion, not on who proposes it. Allies often dislike particular motions (a Bolshevik majority won't "
                    "love a vice-chair for Martov). Keys are bloc names as on the chart.")},
                "motion_key": {"type": "string", "description": "Short id for this question, reused when it's put to a vote again after more debate (a re-vote finds fewer undecided)."},
            },
            "required": ["instance_id", "motion", "player_position", "bloc_stances"],
        },
    }


def _speech_dc(factions, player_position):
    """A hostile room is harder to move: 12, plus up to +6 by the share of seats leaning against the
    player's side of the motion."""
    total = sum(f["seats"] for f in factions) or 1
    sign = -1 if player_position == "against" else 1
    opposed = sum(f["seats"] for f in factions if f.get("lean", 0) * sign < 0)
    return 12 + round(6 * opposed / total)


def call_vote(state, system, inp, influence_value=None, rng=random, speech_roller=None, debate=None):
    """Returns (result, changes). result is what the GM gets back; changes are for the turn log."""
    from mechanics import find_instances  # local import: mechanics imports nothing from here
    mechanics_by_id = state.get("mechanics") or {}
    instance_id = inp.get("instance_id")
    m = mechanics_by_id.get(instance_id)
    if not m or m.get("render_primitive") != "seat_chart":
        options = [iid for iid, _ in find_instances(state, primitive="seat_chart")]
        return {"error": f"No seat_chart mechanic '{instance_id}'. Active seat charts: {options or 'none'}."}, []

    data = m["data"]
    factions = data["factions"]
    position = inp.get("player_position", "for")
    sign = -1 if position == "against" else 1

    # Which way a bloc's lean pushes on THIS motion. By default lean runs toward the player, so it
    # pushes toward the player's position. A chart may instead define its own axis (data.lean_toward,
    # e.g. "immediate armed insurrection" for the Central Committee) - then the GM says whether the
    # motion points along that axis (+1) or against it (-1), independent of where the player stands.
    if data.get("lean_toward"):
        alignment = -1 if inp.get("motion_alignment") == -1 else 1
    else:
        alignment = sign if position != "abstain" else 1

    speech = None
    shift = 0.0
    if inp.get("speech_attribute") and position != "abstain":
        dc = _speech_dc(factions, position)
        # speech_roller (2026-11-03): someone else gives the speech - their sheet, their dice
        speech = (speech_roller(inp["speech_attribute"], inp.get("speech_skill"), dc) if speech_roller
                  else system.roll_check(state["character"], inp["speech_attribute"], inp.get("speech_skill"), dc))
        shift = SPEECH_SHIFT[speech["degree"]]

    influence = 0.0
    if influence_value is not None:
        influence = max(-0.1, min(0.1, (influence_value - 50) / 500))

    stances = {str(k).lower(): v for k, v in (inp.get("bloc_stances") or {}).items()}
    parties = {f["name"]: f.get("party") for f in factions}
    def stance_for(name):
        v = stances.get(name.lower())
        if v is None and parties.get(name):  # a stance given for the whole party covers its wings
            v = stances.get(parties[name].lower())
        if v is None:
            v = next((val for k, val in stances.items() if k in name.lower() or name.lower() in k), 0)
        try:
            return max(-2, min(2, int(round(float(v)))))
        except (TypeError, ValueError):
            return 0
    missing = [f["name"] for f in factions if f["name"].lower() not in stances and (f.get("party") or "").lower() not in stances]
    key = (inp.get("motion_key") or inp.get("motion") or "").strip().lower()[:80]
    history = data.setdefault("motion_rounds", {})
    round_no = history.get(key, 0)
    undecided_share = UNDECIDED_BY_ROUND[round_no] if round_no < len(UNDECIDED_BY_ROUND) else 0.0
    if speech and speech["degree"] in core.SUCCESS_DEGREES:
        undecided_share /= 2  # a good speech settles minds
    debate_swing = 0.0
    if debate:  # (swing toward yes, share of the undecided settled) from debate.apply_to_vote
        debate_swing, settled = debate
        undecided_share *= (1 - settled)

    bloc_results = []
    yes_total = no_total = abstain_total = 0
    for f in factions:
        lean = f.get("lean", 0)
        seats = f["seats"]
        stance = stance_for(f["name"])
        # Probability a member of this bloc votes YES on the motion. lean is toward the player, so it
        # pushes toward the player's position on the motion; when the player abstains it still counts
        # as "the player's camp's" default view (lean > 0 means inclined to vote the way the player's
        # allies generally would, i.e. for).
        toward_yes = lean * alignment
        p = 0.5 + STANCE_LEAN_WEIGHT * toward_yes + STANCE_WEIGHT * stance
        p += sign * shift * PERSUADABILITY[abs(lean)]
        p += debate_swing * PERSUADABILITY[abs(lean)]
        p += sign * influence * PERSUADABILITY[abs(lean)] if position != "abstain" else 0
        p = max(0.03, min(0.97, p))
        abstain_rate = ABSTAIN_RATE[abs(lean)]
        noise = BLOC_NOISE
        torn = toward_yes * stance < 0  # their camp says one thing, their interests another
        if torn:
            noise = CROSS_PRESSURE_NOISE
            abstain_rate += CROSS_PRESSURE_ABSTAIN
        undecided = undecided_share * (1 - abs(stance) / 3)
        if seats <= 3:
            yes = no = ab = 0
            for _ in range(seats):
                r = rng.random()
                if r < abstain_rate:
                    ab += 1
                elif rng.random() < p:
                    yes += 1
                else:
                    no += 1
        else:
            unsure = round(seats * undecided * rng.uniform(0.7, 1.3))
            ab = round((seats - unsure) * abstain_rate * rng.uniform(0.5, 1.5))
            voting = seats - unsure - ab
            frac = max(0.0, min(1.0, p + rng.uniform(-noise, noise)))
            yes = round(voting * frac)
            no = voting - yes
            u_ab, u_no = round(unsure * UNDECIDED_SPLIT[0]), round(unsure * UNDECIDED_SPLIT[1])
            ab += u_ab
            no += u_no
            yes += unsure - u_ab - u_no
        yes_total += yes
        no_total += no
        abstain_total += ab
        bloc_results.append({"name": f["name"], "party": f.get("party"), "yes": yes, "no": no, "abstain": ab, "stance": stance,
                             **({"split": True} if torn or min(yes, no) >= 0.2 * max(1, yes + no) else {})})

    if data.get("includes_player") and position in ("for", "against"):
        if position == "for":
            yes_total += 1
        else:
            no_total += 1

    threshold = inp.get("threshold") or "majority"
    cast = yes_total + no_total
    if threshold == "two_thirds":
        passed = cast > 0 and yes_total * 3 >= cast * 2
    else:
        passed = yes_total > no_total

    result = {
        "motion": inp.get("motion", ""),
        "body": m.get("title", instance_id),
        "passed": passed,
        "yes": yes_total, "no": no_total, "abstain": abstain_total,
        "threshold": threshold,
        "player_position": position,
        "blocs": bloc_results,
        "speech": speech,
        "round": round_no + 1,
        "gm_instruction": (
            "Narrate this exact result. The tallies are final; do not change them or narrate a "
            "different outcome." + (" The speech's own result (see speech.degree) shapes how the "
            "room received it." if speech else "") + " Blocs marked split divided - show who broke ranks and why." + (
            " This question was put cold: many delegates hadn't made up their minds, and most of them abstained or "
            "stuck with the way things are. Debate and a second vote would find fewer undecided." if round_no == 0 else "")
        ),
    }
    history[key] = round_no + 1
    if missing:
        result["assumed_neutral"] = missing  # stance not given: taken as 0 (the motion neither helps nor hurts them)
    data["last_vote"] = {
        "motion": result["motion"], "passed": passed, "yes": yes_total, "no": no_total,
        "abstain": abstain_total, "date": state.get("date"), "turn": state.get("turn_number", 0),
    }
    changes = [{"kind": "vote", "instance_id": instance_id, **{k: result[k] for k in ("motion", "passed", "yes", "no", "abstain")}}]
    return result, changes


# ---------------------------------------------------------------- elections (2026-11-03)
# Choosing a person is not a yes/no motion: several candidates, blocs that split between them, and
# often no majority on the first ballot - historically chairmen were made over several rounds.

def election_tool_spec():
    return {
        "name": "call_election",
        "description": (
            "Elect someone to an office in a body with a seat_chart (chairman, presidium seats, a delegate). "
            "Several candidates; each bloc's support for each candidate is set by you, judged on politics "
            "and personalities, not on who the player backs. The application counts the ballot: blocs split, "
            "and a winner needs a majority of votes cast - otherwise nobody is elected this round and the "
            "body debates and votes again (call it again with the same office)."),
        "parameters": {"type": "object", "properties": {
            "instance_id": {"type": "string"},
            "office": {"type": "string", "description": "e.g. 'Chairman of the Petrograd Soviet'."},
            "candidates": {"type": "array", "items": {"type": "string"}, "description": "2-6 names."},
            "bloc_support": {"type": "object", "description": (
                "For every bloc: {candidate: -2..+2} - how much that bloc wants each candidate. Be honest about "
                "splits: Bolsheviks may not agree on one of their own; others may prefer a compromise figure.")},
            "ballot": {"type": "string", "enum": ["open", "secret"], "description": "Secret ballots weaken party discipline."},
            "player_backs": {"type": "string", "description": "The candidate the player openly supports, if any (the player's camp follows a little, more in an open ballot)."},
        }, "required": ["instance_id", "office", "candidates", "bloc_support"]},
    }


def call_election(state, system, inp, rng=random, candidate_swing=None):
    m = (state.get("mechanics") or {}).get(inp.get("instance_id"))
    if not m or m.get("render_primitive") != "seat_chart":
        return {"error": f"No seat_chart mechanic '{inp.get('instance_id')}'."}, []
    cands = [str(c).strip() for c in (inp.get("candidates") or []) if str(c).strip()][:6]
    if len(cands) < 1:
        return {"error": "Name the candidates."}, []
    data = m["data"]
    support = {str(k).lower(): v for k, v in (inp.get("bloc_support") or {}).items()}
    office = (inp.get("office") or "office").strip()
    rounds = data.setdefault("election_rounds", {})
    round_no = rounds.get(office.lower(), 0)
    undecided_share = UNDECIDED_BY_ROUND[round_no] if round_no < len(UNDECIDED_BY_ROUND) else 0.0
    secret = inp.get("ballot") == "secret"
    backs = inp.get("player_backs")
    totals = {c: 0 for c in cands}
    abstain = 0
    blocs = []
    for f in data["factions"]:
        prefs = (support.get(f["name"].lower()) or (support.get(f["party"].lower()) if f.get("party") else None)
                 or next((v for k, v in support.items() if k in f["name"].lower()), {}) or {})
        prefs = {str(k).lower(): v for k, v in prefs.items()} if isinstance(prefs, dict) else {}
        scores = []
        for c in cands:
            try:
                sc = max(-2.0, min(2.0, float(prefs.get(c.lower(), 0))))
            except (TypeError, ValueError):
                sc = 0.0
            sc += 5 * ((candidate_swing or {}).get(c.lower()) or 0)  # what the debate between ballots did for them
            if backs and c.lower() == str(backs).lower():
                sc += f.get("lean", 0) * (0.15 if secret else 0.3)
            sc += rng.gauss(0, 0.7 if secret else 0.45)  # people differ; secrecy loosens the whip
            scores.append(sc)
        weights = [math.exp(1.3 * x) for x in scores]
        wsum = sum(weights) or 1
        seats = f["seats"]
        unsure = round(seats * undecided_share * rng.uniform(0.7, 1.3))
        ab = unsure + round((seats - unsure) * ABSTAIN_RATE[abs(f.get("lean", 0))] * rng.uniform(0.5, 1.5))
        voting = seats - ab
        alloc = [voting * w / wsum for w in weights]
        ints = [int(x) for x in alloc]
        for i in sorted(range(len(alloc)), key=lambda i: alloc[i] - ints[i], reverse=True)[:voting - sum(ints)]:
            ints[i] += 1
        for c, n in zip(cands, ints):
            totals[c] += n
        abstain += ab
        blocs.append({"name": f["name"], "party": f.get("party"), "votes": dict(zip(cands, ints)), "abstain": ab})
    cast = sum(totals.values())
    ranking = sorted(totals.items(), key=lambda kv: -kv[1])
    winner = ranking[0][0] if cast and ranking[0][1] * 2 > cast else None
    rounds[office.lower()] = round_no + 1
    result = {
        "office": office, "round": round_no + 1, "ballot": "secret" if secret else "open",
        "results": [{"candidate": c, "votes": v} for c, v in ranking], "abstain": abstain, "cast": cast,
        "elected": winner, "blocs": blocs,
        "gm_instruction": ("Narrate this exact count. " + (f"{winner} is elected." if winner else
            "Nobody has a majority: no one is elected this round. Show the room reacting - deals in the corridor, a "
            "candidate under pressure to withdraw, a debate before the next ballot (call_election again).")
            + " Show where blocs split between candidates."),
    }
    data["last_vote"] = {"motion": f"Election: {office} (round {round_no + 1})", "passed": bool(winner),
                         "yes": ranking[0][1] if ranking else 0, "no": cast - (ranking[0][1] if ranking else 0),
                         "abstain": abstain, "date": state.get("date"), "turn": state.get("turn_number", 0)}
    return result, [{"kind": "election", "instance_id": inp.get("instance_id"), "office": office, "elected": winner,
                     "results": result["results"]}]


# ---------------------------------------------------------------- swaying units

SWAY_SHIFT = {
    "critical_success": 2,
    "strong_success": 1,
    "success": 1,
    "success_at_cost": 1,
    "failure": 0,
    "critical_failure": -1,
}


def sway_tool_spec():
    return {
        "name": "sway_unit",
        "description": (
            "The player tries to win over one unit in a forces mechanic in person (a speech at its "
            "barracks, a meeting with its soldiers' committee) - or, with direction 'stand_down', to "
            "persuade a hostile unit to stay out of a fight. The application sets the DC from the unit's "
            "current loyalty, rolls, and moves loyalty by the result. One attempt per unit per in-game "
            "day. Use this instead of proposing a loyalty change yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "The forces mechanic's instance_id."},
                "unit_id": {"type": "string", "description": "The unit's id within that mechanic."},
                "attribute": {"type": "string", "enum": core.ATTRIBUTES},
                "skill": {"type": ["string", "null"]},
                "direction": {"type": "string", "enum": ["win_over", "stand_down"], "description": "Defaults to win_over."},
                "description": {"type": "string", "description": "What the player actually does, in-fiction."},
            },
            "required": ["instance_id", "unit_id", "attribute", "description"],
        },
    }


def sway_dc(loyalty, influence_value=None):
    """Firming up a friendly unit is easy; turning a hostile one is very hard. 11 at neutral, -2 per
    step already toward the player, +2 per step against. A strong institutional position (the
    influence resource >= 70) makes soldiers more willing to listen; a weak one (< 35) less."""
    dc = 11 - 2 * loyalty
    if influence_value is not None:
        if influence_value >= 70:
            dc -= 2
        elif influence_value < 35:
            dc += 2
    return max(5, min(21, dc))


def sway_unit(state, system, inp, influence_value=None):
    m = (state.get("mechanics") or {}).get(inp.get("instance_id"))
    if not m or m.get("render_primitive") != "forces":
        return {"error": f"No forces mechanic '{inp.get('instance_id')}'."}, []
    units = [u for x in (state.get("mechanics") or {}).values() if x.get("render_primitive") == "forces" for u in x["data"]["units"]]
    unit = next((u for u in units if u["id"] == inp.get("unit_id")), None)
    if unit is None:
        ids = [u["id"] for u in units]
        return {"error": f"No unit '{inp.get('unit_id')}'. Units: {ids}"}, []
    today = state.get("date")
    if today and unit.get("last_swayed_date") == today:
        return {"error": f"{unit['name']} has already been appealed to today ({today}). Advance time or try another unit."}, []

    direction = inp.get("direction") or "win_over"
    before = unit["loyalty"]
    if direction == "stand_down":
        if before > -FIGHT_THRESHOLD:
            return {"error": f"{unit['name']} wouldn't fight the player's side anyway (loyalty {before}); stand_down only applies to units at -2 or -3."}, []
        # Persuading a hostile unit merely to stay neutral is easier than converting it.
        dc = max(5, sway_dc(before, influence_value) - 3)
    else:
        if before >= 3:
            return {"error": f"{unit['name']} is already fully committed to the player's side (loyalty +3)."}, []
        dc = sway_dc(before, influence_value)

    check = system.roll_check(state["character"], inp["attribute"], inp.get("skill"), dc)
    shift = SWAY_SHIFT[check["degree"]]
    if direction == "stand_down" and shift > 0:
        after = -1  # any success: the unit agrees to stay in barracks, though it doesn't change sides
    else:
        after = max(-3, min(3, before + shift))
    unit["loyalty"] = after
    unit["last_swayed_date"] = today
    result = {
        "unit": unit["name"], "direction": direction, "dc": dc, "check": check,
        "loyalty_before": before, "loyalty_after": after,
        "gm_instruction": (
            "Narrate the appeal and the soldiers' response consistent with this result. Loyalty "
            f"{after}: " + LOYALTY_MEANING[after] + "."
        ),
    }
    changes = [{"kind": "unit_loyalty", "instance_id": inp["instance_id"], "unit": unit["name"], "before": before, "after": after, "cause": "sway_unit"}]
    return result, changes


LOYALTY_MEANING = {
    3: "will march for the player's side unprompted",
    2: "will fight for the player's side when called",
    1: "sympathetic, but won't leave barracks to fight",
    0: "neutral, staying in barracks",
    -1: "uneasy with the player's side, but won't fight it",
    -2: "will fight against the player's side when ordered",
    -3: "actively hostile, will move against the player unprompted",
}

# ---------------------------------------------------------------- confrontations

QUALITY = {1: 0.4, 2: 0.6, 3: 0.8, 4: 1.0, 5: 1.25}
TERRAIN = {"open": 1.0, "street": 1.3, "fortified": 1.8, "fortress": 2.6}
COMMAND_MULTIPLIER = {
    "critical_success": 1.3,
    "strong_success": 1.2,
    "success": 1.1,
    "success_at_cost": 1.05,
    "failure": 0.9,
    "critical_failure": 0.75,
}
WATCHER_SHIFT_CHANCE = 0.5    # chance a neutral unit leans toward the side that just won decisively
FIGHT_THRESHOLD = 2           # |loyalty| a unit needs before it will take the field for a side
COLLAPSE_RATIO = 2.5          # power ratio at which the weaker side's shakier units may melt away
MAX_ADHOC_UNITS = 3
MAX_ADHOC_STRENGTH = 3000
OUTCOMES = [  # (min score, outcome for the attacker)
    (4.0, "decisive_victory"),
    (1.0, "victory"),
    (-1.0, "stalemate"),
    (-4.0, "repulsed"),
    (-math.inf, "routed"),
]
CASUALTY_RATE = {  # (attacker loss, defender loss) as a fraction of committed strength
    "uncontested": (0.0, 0.0),
    "decisive_victory": (0.03, 0.15),
    "victory": (0.05, 0.09),
    "stalemate": (0.05, 0.05),
    "repulsed": (0.09, 0.04),
    "routed": (0.15, 0.03),
}


def confrontation_tool_spec():
    return {
        "name": "resolve_confrontation",
        "description": (
            "Resolve an armed confrontation - seizing a bridge, storming a building, holding a station, "
            "facing down a column - using units from a forces mechanic. The application decides which "
            "units actually take the field (a unit needs loyalty +2/+3 to fight for the player, -2/-3 to "
            "fight against; anything between stays in barracks), lets outmatched waverers melt away, "
            "rolls the fight, applies casualties, and returns the outcome. Never narrate the outcome of "
            "an armed clash without calling this. Units not in the tracker (a column arriving from the "
            "front, a palace guard detachment) can be added as ad-hoc opposition, max 3 units of up to "
            "3000 each."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "The forces mechanic's instance_id."},
                "objective": {"type": "string", "description": "What is being fought over, in a short phrase."},
                "player_side_attacking": {"type": "boolean", "description": "True if the player's side is the one attacking/moving on the objective."},
                "player_units": {"type": "array", "items": {"type": "string"}, "description": "Unit ids the player's side calls out."},
                "opposing_units": {"type": "array", "items": {"type": "string"}, "description": "Unit ids the other side calls out."},
                "adhoc_opposition": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "strength": {"type": "integer"}, "readiness": {"type": "integer"}}},
                    "description": "Opposing forces not in the tracker. Max 3, strength <= 3000 each, readiness 1-5.",
                },
                "terrain": {"type": "string", "enum": list(TERRAIN), "description": "Ground the DEFENDER holds."},
                "player_commanding": {"type": "boolean", "description": "True if the player character personally directs their side."},
                "command_attribute": {"type": ["string", "null"], "enum": core.ATTRIBUTES + [None]},
                "command_skill": {"type": ["string", "null"]},
            },
            "required": ["instance_id", "objective", "player_side_attacking", "player_units", "terrain"],
        },
    }


def _power(units):
    return sum(u["strength"] * QUALITY[u["readiness"]] for u in units)


def resolve_confrontation(state, system, inp, rng=random):
    m = (state.get("mechanics") or {}).get(inp.get("instance_id"))
    if not m or m.get("render_primitive") != "forces":
        return {"error": f"No forces mechanic '{inp.get('instance_id')}'."}, []
    # Units can come from any forces tracker (2026-11-03): a fight at Gatchina can pit the city's Red
    # Guards against Krasnov's Cossacks from the country-wide order of battle.
    trackers = [x for x in (state.get("mechanics") or {}).values() if x.get("render_primitive") == "forces"]
    by_id = {u["id"]: u for t in trackers for u in t["data"]["units"]}
    unknown = [uid for uid in (inp.get("player_units") or []) + (inp.get("opposing_units") or []) if uid not in by_id]
    if unknown:
        return {"error": f"Unknown unit ids {unknown}. Units: {list(by_id)}"}, []
    terrain = inp.get("terrain") if inp.get("terrain") in TERRAIN else "street"

    refused = []
    player_side, other_side = [], []
    for uid in dict.fromkeys(inp.get("player_units") or []):
        u = by_id[uid]
        (player_side if u["loyalty"] >= FIGHT_THRESHOLD else refused).append(u)
    for uid in dict.fromkeys(inp.get("opposing_units") or []):
        u = by_id[uid]
        if uid in {x["id"] for x in player_side}:
            continue
        (other_side if u["loyalty"] <= -FIGHT_THRESHOLD else refused).append(u)
    adhoc = []
    for a in (inp.get("adhoc_opposition") or [])[:MAX_ADHOC_UNITS]:
        adhoc.append({
            "id": None, "name": str(a.get("name", "Unlisted force")),
            "strength": max(0, min(MAX_ADHOC_STRENGTH, int(a.get("strength") or 0))),
            "readiness": max(1, min(5, int(a.get("readiness") or 3))), "loyalty": -3, "adhoc": True,
        })
    other_side += [a for a in adhoc if a["strength"] > 0]

    command = None
    multiplier = 1.0
    if inp.get("player_commanding") and inp.get("command_attribute"):
        command = system.roll_check(state["character"], inp["command_attribute"], inp.get("command_skill"), 13)
        multiplier = COMMAND_MULTIPLIER[command["degree"]]

    attacking = bool(inp.get("player_side_attacking"))

    def side_powers():
        p_player = _power(player_side) * multiplier
        p_other = _power(other_side)
        if attacking:
            p_other *= TERRAIN[terrain]
        else:
            p_player *= TERRAIN[terrain]
        return p_player, p_other

    # Waverers melt away when badly outmatched - this is how most of October's "battles" went.
    melted = []
    p_player, p_other = side_powers()
    if p_player > 0 and p_other > 0:
        weak_is_player = p_player < p_other
        ratio = max(p_player, p_other) / min(p_player, p_other)
        if ratio >= COLLAPSE_RATIO:
            weak = player_side if weak_is_player else other_side
            chance = min(0.85, 0.3 + 0.15 * (ratio - COLLAPSE_RATIO))
            for u in list(weak):
                if u.get("adhoc") or abs(u["loyalty"]) >= 3:
                    continue
                if rng.random() < chance:
                    weak.remove(u)
                    melted.append(u)
            p_player, p_other = side_powers()

    if p_player == 0 and p_other == 0:
        outcome_attacker = "uncontested"
        player_outcome = "no_fight"
        score = 0.0
    elif p_other == 0 or p_player == 0:
        outcome_attacker = "uncontested"
        player_wins = p_other == 0
        player_outcome = "uncontested_success" if player_wins else "uncontested_failure"
        score = 0.0
    else:
        p_att, p_def = (p_player, p_other) if attacking else (p_other, p_player)
        score = 4 * math.log2(p_att / p_def) + (rng.randint(1, 20) - rng.randint(1, 20)) / 3
        outcome_attacker = next(label for floor, label in OUTCOMES if score >= floor)
        player_outcome = outcome_attacker if attacking else {
            "decisive_victory": "routed", "victory": "repulsed", "stalemate": "stalemate",
            "repulsed": "victory", "routed": "decisive_victory"}[outcome_attacker]

    att_rate, def_rate = CASUALTY_RATE[outcome_attacker]
    player_rate, other_rate = (att_rate, def_rate) if attacking else (def_rate, att_rate)
    unit_changes = []

    def bleed(units, rate, lost):
        for u in units:
            loss = int(round(u["strength"] * rate))
            if u.get("adhoc"):
                unit_changes.append({"unit": u["name"], "losses": loss})
                continue
            before = dict(u)
            u["strength"] = max(0, u["strength"] - loss)
            if lost:
                u["readiness"] = max(1, u["readiness"] - 1)
            unit_changes.append({"unit": u["name"], "losses": loss, "readiness": f"{before['readiness']}->{u['readiness']}"})

    player_lost = player_outcome in ("repulsed", "routed")
    other_lost = player_outcome in ("victory", "decisive_victory")
    bleed(player_side, player_rate, player_lost)
    bleed(other_side, other_rate, other_lost)

    # A decisive result moves loyalties: units that broke drift toward the winner, and neutral units
    # that watched it happen take note.
    loyalty_changes = []
    player_unit_ids = set(inp.get("player_units") or [])
    for u in melted:
        # A player unit that melted away cooled toward neutral; an opposing one warmed toward it.
        step = -1 if u["id"] in player_unit_ids else 1
        before = u["loyalty"]
        u["loyalty"] = max(-3, min(3, before + step))
        loyalty_changes.append({"unit": u["name"], "before": before, "after": u["loyalty"], "cause": "stood down"})
    if player_outcome in ("decisive_victory", "routed"):
        # Only true fence-sitters (loyalty 0) take notice, and only some of them - one decisive
        # street fight shouldn't swing the whole garrison on its own.
        step = 1 if player_outcome == "decisive_victory" else -1
        for u in m["data"]["units"]:
            if u["loyalty"] == 0 and u not in melted and rng.random() < WATCHER_SHIFT_CHANCE:
                before = u["loyalty"]
                u["loyalty"] = max(-3, min(3, before + step))
                loyalty_changes.append({"unit": u["name"], "before": before, "after": u["loyalty"], "cause": "watched the outcome"})

    for t in trackers:
        kept = [u for u in t["data"]["units"] if u["strength"] > 0]
        t["data"]["units"] = kept or t["data"]["units"][:1]  # a tracker keeps at least one (zero-strength) unit
    engagement = {
        "objective": inp.get("objective", ""), "player_outcome": player_outcome,
        "date": state.get("date"), "turn": state.get("turn_number", 0),
    }
    m["data"]["last_engagement"] = engagement

    result = {
        "objective": inp.get("objective", ""),
        "terrain": terrain,
        "player_side_attacking": attacking,
        "player_outcome": player_outcome,
        "player_power": round(p_player), "opposing_power": round(p_other),
        "score": round(score, 2),
        "took_the_field": {"player": [u["name"] for u in player_side], "opposing": [u["name"] for u in other_side]},
        "refused_to_fight": [{"unit": u["name"], "loyalty": u["loyalty"]} for u in refused],
        "melted_away": [u["name"] for u in melted],
        "command_check": command,
        "casualties": unit_changes,
        "loyalty_changes": loyalty_changes,
        "gm_instruction": (
            "Narrate this exact outcome from the player's side: " + PLAYER_OUTCOME_MEANING[player_outcome]
            + " Units listed as refusing to fight stayed out of it - narrate their absence; it matters."
        ),
    }
    changes = [{"kind": "confrontation", "instance_id": inp["instance_id"], "objective": result["objective"],
                "player_outcome": player_outcome, "casualties": unit_changes, "loyalty_changes": loyalty_changes,
                "refused": result["refused_to_fight"], "melted": result["melted_away"]}]
    return result, changes


PLAYER_OUTCOME_MEANING = {
    "no_fight": "nobody on either side was willing to fight; nothing was decided by force.",
    "uncontested_success": "the other side had no one willing to fight, so the objective falls without a real clash.",
    "uncontested_failure": "no one on the player's side was willing to fight, so the move collapses before it starts.",
    "decisive_victory": "a clear, decisive win for the player's side.",
    "victory": "the player's side wins, at a real cost.",
    "stalemate": "neither side gets what it wanted; the situation is unresolved and tense.",
    "repulsed": "the player's side is beaten back.",
    "routed": "the player's side is broken and scattered.",
}

PLAYER_WIN_OUTCOMES = ("uncontested_success", "decisive_victory", "victory")
PLAYER_LOSS_OUTCOMES = ("uncontested_failure", "repulsed", "routed")


def forces_alignment(units, scale=100):
    """Strength-weighted mean loyalty, mapped from -3..+3 onto 0..scale. Used to derive a resource
    (e.g. Petrograd's garrison_loyalty) from the order of battle instead of letting it float free."""
    total = sum(u["strength"] for u in units)
    if total <= 0:
        return scale // 2
    mean = sum(u["strength"] * u["loyalty"] for u in units) / total
    return round((mean + 3) / 6 * scale)
