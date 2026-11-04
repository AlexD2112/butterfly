"""
Debate (2026-11-03): assemblies argue before they vote.

Reported: a motion went from "I ask to be heard" to the count in one exchange, and the GM wrote the
player's speech for him. In debate pace (the default, chosen per game) a question in a body with a
seat_chart is argued over several turns before it can be put:
  - debate_speech: one speech for or against the motion (or for/against a candidate in an
    election) by the player or by anyone with a sheet. Each is rolled; a good speech moves the
    room a little (diminishing for the same speaker), a bad one costs ground. The room's lean is
    kept per motion and feeds the vote, and every speech settles some of the undecided;
  - a vote needs enough speeches first: procedural 0, substantive 2, major 4, and between election
    ballots 2 - otherwise call_vote says the debate hasn't run its course;
  - in quick pace, one argument and the vote can happen in the same turn (the old behaviour).
State: chart data["debates"][motion_key] = {motion, speeches: [...], swing, candidate_swing, since_ballot}.
"""

PACES = ("debate", "quick")
MIN_SPEECHES = {"procedural": 0, "substantive": 2, "major": 4}
BETWEEN_BALLOTS = 2
SWING = {"critical_success": 0.10, "strong_success": 0.07, "success": 0.045, "success_at_cost": 0.02,
         "failure": 0.0, "critical_failure": -0.05}
SAME_SPEAKER_DECAY = 0.6
MAX_SWING = 0.30
UNDECIDED_CUT_PER_SPEECH = 0.15


def pace(state):
    return state.get("pace") if state.get("pace") in PACES else "debate"


def key_for(inp):
    if inp.get("office") and (inp.get("candidate") or inp.get("candidates") or not (inp.get("motion_key") or inp.get("motion"))):
        return str(inp["office"]).strip().lower()[:80]  # an election debate is keyed by its office
    return (inp.get("motion_key") or inp.get("motion") or "").strip().lower()[:80]


def _debate(chart, key, motion):
    d = chart["data"].setdefault("debates", {}).setdefault(key, {"motion": motion, "speeches": [], "swing": 0.0,
                                                               "candidate_swing": {}, "since_ballot": 0})
    return d


def speech(state, setting_key, system, chart, inp):
    import npcs
    import operations
    key = key_for(inp)
    if not key:
        return {"error": "Which question? (motion_key, or the motion's text, or the office for an election)"}
    d = _debate(chart, key, inp.get("motion") or inp.get("office") or key)
    speaker = inp.get("speaker") or "player"
    side = "against" if inp.get("side") == "against" else "for"
    dc = operations._speech_dc(chart["data"]["factions"], side)
    attribute, skill = inp.get("attribute") or "oration", inp.get("skill")
    if str(speaker).lower() in ("player", "me"):
        speaker = "player"
        roll = system.roll_check(state["character"], attribute, skill, dc)
        name = "the player"
    else:
        roll = npcs.roll_for(state, setting_key, system, speaker, attribute, skill, dc) if setting_key else None
        if roll is None:
            return {"error": f"'{speaker}' has no sheet; give them one (create_npc_sheet) or narrate their speech without a roll."}
        name = roll["actor_name"]
    earlier = sum(1 for s in d["speeches"] if s["speaker"] == speaker)
    move = SWING[roll["degree"]] * (SAME_SPEAKER_DECAY ** earlier)
    direction = -1 if side == "against" else 1
    candidate = inp.get("candidate")
    if candidate:
        cs = d["candidate_swing"]
        cs[candidate] = max(-MAX_SWING, min(MAX_SWING, cs.get(candidate, 0.0) + direction * move))
    else:
        d["swing"] = max(-MAX_SWING, min(MAX_SWING, d["swing"] + direction * move))
    d["speeches"].append({"speaker": speaker, "name": name, "side": side, "candidate": candidate,
                          "point": str(inp.get("point") or "")[:200], "degree": roll["degree"], "turn": state.get("turn_number", 0)})
    d["since_ballot"] += 1
    weight = inp.get("weight") or "substantive"
    need = MIN_SPEECHES.get(weight, 2)
    have = len(d["speeches"])
    mood = d["swing"] if not candidate else d["candidate_swing"].get(candidate, 0)
    room = ("warming to it" if mood > 0.12 else "inclined to it" if mood > 0.04 else "hostile" if mood < -0.12
            else "cooling" if mood < -0.04 else "undecided")
    return {"roll": roll, "speaker": name, "side": side, "candidate": candidate, "speeches_so_far": have,
            "room": room, "ready_for_vote": pace(state) == "quick" or have >= need,
            "gm_instruction": (roll["gm_instruction"] + " Narrate this one speech and how the hall takes it - interruptions, "
                               "heckling, who stirs. Then the floor passes: an opponent replies, a procedural point is raised, "
                               "or the player speaks again. Don't call the vote yet unless the debate has run its course.")}


def vote_ready(state, chart, inp, kind="vote"):
    """None if the question has been argued enough to be put, else a result explaining why not."""
    if pace(state) == "quick" or inp.get("extraordinary"):
        return None
    d = (chart["data"].get("debates") or {}).get(key_for(inp)) or {}
    if kind == "election":
        rounds = (chart["data"].get("election_rounds") or {}).get((inp.get("office") or "office").strip().lower(), 0)
        if rounds and d.get("since_ballot", 0) < BETWEEN_BALLOTS:
            return {"not_voted": True, "reason": "debate_first", "speeches_needed": BETWEEN_BALLOTS - d.get("since_ballot", 0),
                    "gm_instruction": ("Between ballots the hall argues: supporters speak for their candidates, someone presses a "
                                       "withdrawal, a deal is floated. Run that (debate_speech with candidate) before the next ballot.")}
        return None
    weight = inp.get("weight") or "substantive"
    need = MIN_SPEECHES.get(weight, 2)
    have = len(d.get("speeches") or [])
    if have < need:
        return {"not_voted": True, "reason": "debate_first", "speeches_needed": need - have,
                "gm_instruction": (f"The question hasn't been argued yet ({have} of {need} speeches). The chair calls the next "
                                   "speaker: give the floor - to the player if he's asked for it, and wait for his words - and to "
                                   "the other side. Use debate_speech for each speech.")}
    return None


def apply_to_vote(chart, inp):
    """What the debate on this motion adds to the vote: (swing toward yes, how much of the undecided it settled)."""
    d = (chart["data"].get("debates") or {}).get(key_for(inp)) or {}
    n = len(d.get("speeches") or [])
    return d.get("swing", 0.0), min(1.0, n * UNDECIDED_CUT_PER_SPEECH)


def after_ballot(chart, inp):
    d = (chart["data"].get("debates") or {}).get(key_for(inp))
    if d:
        d["since_ballot"] = 0


def prompt_section(state):
    if pace(state) == "quick":
        return ("PACE: quick (the player chose it). Resolve an argument in one exchange: the player's case, a roll, and the "
                "vote can happen in the same turn.")
    return ("PACE: debate (the player chose it). Assemblies argue before they vote. A motion is moved; then speeches for and "
            "against - the player's, his allies', his opponents' - each one debate_speech, each narrated with the hall's "
            "reaction; procedural fights and amendments in between. The application won't take a vote until the question "
            "has been argued (substantive: 2 speeches, major: 4; between election ballots: 2). When the player asks for the "
            "floor or says he'll speak but hasn't given his words, give him the floor and STOP - end the turn with the hall "
            "waiting for him. Never write his speech for him; roll on his actual words.")


SPEC = {
    "name": "debate_speech",
    "description": ("One speech in a debate in a body with a seat_chart: for or against the motion (or for/against a candidate). "
                    "By the player - only once he has given his actual words - or by anyone with a sheet (Dan replying, Martov "
                    "raising an objection). The application rolls it and moves the room."),
    "parameters": {"type": "object", "properties": {
        "instance_id": {"type": "string"},
        "motion": {"type": "string"}, "motion_key": {"type": "string", "description": "Same key the vote will use."},
        "office": {"type": "string", "description": "For an election debate: the office."},
        "candidate": {"type": "string", "description": "For an election debate: the candidate this speech is for or against."},
        "speaker": {"type": "string", "description": "'player' or an NPC id."},
        "side": {"type": "string", "enum": ["for", "against"]},
        "point": {"type": "string", "description": "The speech's main point, one line - for the player, from his own words."},
        "attribute": {"type": "string"}, "skill": {"type": "string"},
        "weight": {"type": "string", "enum": ["procedural", "substantive", "major"]},
    }, "required": ["instance_id", "speaker", "side", "point"]},
}
