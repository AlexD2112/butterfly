"""
Multiplayer orchestration: two mostly-independent pieces sharing this module.

1. Message judging - deciding whether an AI response to a player's subspace message is warranted at
   all, and if so, in whose voice: a specific present NPC, or the GM. See NOTES.md (2026-08-21) for
   the corrected design this implements: a real yes/no gate first, THEN pick a responder - not "the
   AI always responds," which was an earlier, wrong reading of the brief.

   Reply-threading short-circuits the judge entirely, per direction (not inferred):
     - a reply to another PLAYER's message never needs judging - it's obviously player-to-player, so
       the GM may still add a brief ambient beat, but no NPC voice is ever picked for it.
     - a reply to an existing AI/NPC message never needs judging either - we already know exactly
       whose voice this is continuing.
     - only a fresh, non-reply message needs the judge call at all.

2. Action resolution + combat initiative - resolve_action() deliberately reuses engine.run_turn
   UNCHANGED for the actual mechanical resolution (dice, state updates) - the same trusted,
   single-player-tested path, not a parallel reimplementation. This module only wraps combat
   DETECTION and turn-order ENFORCEMENT around that call: after a free action resolves, a cheap
   classifier decides whether it ignited a fight and who else is now involved (initiative rolled by
   the app - system._shared.core.attribute_modifier + a d20, never trusted to the model); while an
   encounter is active, an action from a listed participant is rejected outright if it isn't their
   turn - "no free-for-all resolution," enforced in code, not by asking nicely. A resolved action is
   also where detect_new_subspace (2026-08-22, see below) checks for organic world growth.

3. Subspace conclusion - mark_concluded()/storm_out(). "Thread" is scoped per-SUBSPACE, not per-
   location (subspaces are the actual sub-conversation unit built for message visibility - see
   NOTES.md's location/subspace rename entry), so conclusion tracking is per-subspace too. A thread
   concludes once every PLAYER currently at that subspace has marked it (NPCs don't count - the brief
   says "players"). Storming out sends an optional final flavor message, moves the character to their
   location's default subspace, and - if anyone remains - checks whether the scene should
   auto-conclude without them (deterministically if literally nobody's left, via a cheap ambient
   classifier otherwise).

4. Rumors + flags (2026-08-21). generate_rumor() fires whenever _conclude_thread() runs (from
   mark_concluded, storm_out, or the sweep) - a summary whose LENGTH scales with judged importance and
   whose DETAIL scales with judged publicity, per direction, held unpublished until
   compile_and_publish_rumors() (called by scheduler.py at each tick) folds every rumor accumulated
   since the last tick into one cohesive server-wide broadcast. Flags are separate and free: no new AI
   call - resolve_action() reuses council.detect_stakes_signals (the same rule-based significance
   detector single-player's own council-router already relies on) to mark an action's turns row when
   it's mechanically significant (a critical roll, a canon event, a big resource swing).

5. Live updates (SSE) - every function here that records something player-visible (a message, a
   reply, a conclusion) also calls live.publish_subspace_event/publish_server_event right after, so
   an open SSE connection (server.py's stream route) sees it appear without polling. See live.py's
   own docstring for the pub/sub mechanics and its known limitations.

6. Dynamic subspace creation (2026-08-22) - detect_new_subspace() runs after a resolved ACTION (never
   a message - those are dialogue, not movement) and decides whether the narration describes arriving
   somewhere genuinely new and distinct within the character's current LOCATION. Deliberately scoped
   to a cheap prompt (the player's action text + the resulting narration + the names of subspaces
   already there, NOT the full game/location context) and instructed to be conservative - biased
   toward "no" unless the signal is unambiguous, so a passing glance or a minor beat in the same space
   doesn't spawn a new subspace. Only ever creates a SUBSPACE nested under the character's current
   LOCATION, never a whole new top-level Location - that stays admin-only, keeping the overall map
   shape under admin control while the texture inside each location can grow through play. A
   dynamically-created subspace is immediately visible/movable-to for everyone at that location, same
   as an admin-created one - no separate discovery mechanic (see NOTES.md for that decision).

Mechanics (dice, checks, state changes) still only ever happen inside engine.run_turn's existing
tool loop - this module never calls systems/_shared/core.py's check/state functions directly, only
engine.run_turn (for actions) and combat initiative's own dice roll (a narrow, separate mechanic).
"""

import datetime
import json
import random

import council
import db
import engine
import live
import pricing
import providers
from systems._shared import core as shared_core


class CombatTurnError(Exception):
    """Raised by resolve_action when a character tries to act out of turn during an active combat
    encounter they're a participant in. Callers (server.py) turn this into a clear 409, not a 500."""

# Same fixed cheap model council.py's judges use, and for the same reason - narrow judgment work
# doesn't need a strong (or the sender's chosen) model. Kept as a separate name here rather than
# importing council.JUDGE_MODEL_KEY directly at call sites, so this module's own intent reads clearly.
JUDGE_MODEL_KEY = council.JUDGE_MODEL_KEY


def _recent_subspace_context(subspace_id, limit=8):
    """A short plain-text rendering of the most recent activity at a subspace, for the judge/reply
    prompts - deliberately small (chat context, not a full transcript)."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM turns WHERE subspace_id = ? ORDER BY id DESC LIMIT ?", (subspace_id, limit)
    ).fetchall()
    conn.close()
    lines = []
    for r in reversed(rows):
        if r["kind"] in ("message", "action"):
            lines.append(f"[player #{r['character_id']}]: {r['player_input']}")
        elif r["kind"] in ("npc_reply", "gm_reply"):
            lines.append(f"[{r['responder']}]: {r['narration']}")
    return "\n".join(lines) if lines else "(no prior activity at this subspace)"


def _npc_roster_text(npcs):
    if not npcs:
        return "(no NPCs currently present at this subspace)"
    return "\n".join(f"- {n['key']}: {db.build_npc_sheet_text(n)}" for n in npcs)


def judge_responder(provider, model, subspace_id, content, usage_sink=None):
    """The real yes/no gate for a fresh (non-reply) message: does anyone respond, and if so, a
    present NPC's key or "gm". Returns (responds: bool, responder: str|None, reasoning: str|None).
    Fails safe to (False, None, None) on a missing/malformed response, or if the model names an NPC
    that isn't actually present - silence is the safer default than inventing an unwanted or
    impossible response."""
    npcs = db.npcs_at_subspace(subspace_id)
    system_prompt = f"""You are deciding whether anyone responds to a player's message in a
multiplayer scene, and if so, who.

# NPCS PRESENT AT THIS SUBSPACE
{_npc_roster_text(npcs)}

# RECENT ACTIVITY HERE
{_recent_subspace_context(subspace_id)}

# THE NEW MESSAGE
{content}

If the message is clearly just banter between players with nothing for an NPC or the GM to
meaningfully add, respond with responds=false. If it's addressed to (or would naturally draw a
reaction from) a specific present NPC, respond=true with that NPC's key as responder. If it's not
addressed to any specific NPC but the GM would naturally narrate a small ambient beat, respond=true
with responder="gm". Submit your decision via decide_response."""
    tool_specs = [
        {
            "name": "decide_response",
            "description": "Decide whether anyone responds to this message, and who.",
            "parameters": {
                "type": "object",
                "properties": {
                    "responds": {"type": "boolean"},
                    "responder": {
                        "type": ["string", "null"],
                        "description": "An NPC key from the roster above, or 'gm'. Null if responds is false.",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["responds", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_response")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return False, None, None
    decision = result.tool_calls[0]["input"]
    if not decision.get("responds"):
        return False, None, decision.get("reasoning")
    responder = decision.get("responder")
    if responder != "gm" and not any(n["key"] == responder for n in npcs):
        return False, None, decision.get("reasoning")
    return True, responder, decision.get("reasoning")


def generate_reply(provider, model, subspace_id, responder, content, usage_sink=None):
    """Generates the actual reply text once a responder is already decided (via judge_responder, or
    a reply-threading shortcut). responder is "gm" or a present NPC's key."""
    context = _recent_subspace_context(subspace_id)
    if responder == "gm":
        system_prompt = f"""You are the game master narrating a small ambient beat in a multiplayer
scene - a reaction, an environmental detail, a passing NPC's glance - in response to what a player
just said. Keep it brief (1-3 sentences), grounded, not a full scene. Do not invent dice results or
mechanical state changes - this is pure narration.

# RECENT ACTIVITY HERE
{context}

# WHAT THE PLAYER JUST SAID
{content}

Write only the narration, nothing else."""
    else:
        npcs = db.npcs_at_subspace(subspace_id)
        npc = next((n for n in npcs if n["key"] == responder), None)
        sheet = db.build_npc_sheet_text(npc) if npc else responder
        system_prompt = f"""You are roleplaying {responder} in a multiplayer scene, responding
in-character to a player who just addressed you. Stay strictly in character.

# YOUR CHARACTER
{sheet}

# RECENT ACTIVITY HERE
{context}

# WHAT WAS JUST SAID TO YOU
{content}

Write only your in-character response, nothing else."""

    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Respond now.")
    result = provider.call(model, system_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    text = "\n".join(result.text_parts).strip()
    return text or None


def handle_message(provider, model, game_id, location_id, subspace_id, character_id, content,
                    reply_to_turn_id=None, usage_sink=None):
    """The full flow for a "message"-kind send: record it, decide (via reply-threading shortcuts or
    the judge) whether/who responds, generate that reply if any, record it, and update NPC memory if
    an NPC replied. Returns (message_turn_id, reply_turn_id_or_None, responder_or_None) - responder
    is None whenever nobody responds, which is a real, expected outcome now, not a failure."""
    message_turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, "message", content, reply_to_turn_id=reply_to_turn_id,
    )
    live.publish_subspace_event(subspace_id, db.get_turn_by_id(message_turn_id))

    responder = None
    if reply_to_turn_id:
        prior = db.get_turn_by_id(reply_to_turn_id)
        if prior and prior["kind"] in ("npc_reply", "gm_reply"):
            responder = prior["responder"]  # continue the same voice - no judging needed
        else:
            responder = "gm"  # a reply to a player's message - no judging, GM may add an ambient beat
    else:
        try:
            judge_provider, judge_model = providers.get_provider(JUDGE_MODEL_KEY)
        except providers.ProviderError:
            # Same fallback pattern as council.py: judge model's provider isn't configured, fall
            # back to whatever the sender is already using rather than failing the whole message.
            judge_provider, judge_model = provider, model
        responds, judged_responder, _reasoning = judge_responder(
            judge_provider, judge_model, subspace_id, content, usage_sink=usage_sink,
        )
        if responds:
            responder = judged_responder

    if responder is None:
        return message_turn_id, None, None

    reply_text = generate_reply(provider, model, subspace_id, responder, content, usage_sink=usage_sink)
    if not reply_text:
        return message_turn_id, None, None

    reply_kind = "gm_reply" if responder == "gm" else "npc_reply"
    reply_turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, reply_kind, reply_text,
        reply_to_turn_id=message_turn_id, responder=responder,
    )
    live.publish_subspace_event(subspace_id, db.get_turn_by_id(reply_turn_id))

    if responder != "gm":
        npcs = db.npcs_at_subspace(subspace_id)
        npc = next((n for n in npcs if n["key"] == responder), None)
        if npc:
            db.add_npc_memory(npc["id"], f'Responded to a message: "{content[:120]}" -> "{reply_text[:120]}"')

    return message_turn_id, reply_turn_id, responder


# ==================== action resolution + combat initiative ====================

def _roll_initiative(character_row):
    """d20 + Presence modifier - switched from Physique to Presence per direction (2026-08-21):
    this game's combat is political/social as much as physical (revolutionary Petrograd, feudal
    courts, the Roman Senate, a crusader camp), so "who seizes the initiative in a confrontation"
    reads truer as presence/force-of-personality than raw physical quickness here."""
    score = character_row["state"]["character"]["attributes"].get("presence", 10)
    return random.randint(1, 20) + shared_core.attribute_modifier(score)


def _roll_initiative_for_npc():
    """NPCs don't carry an attribute block yet (see the NPC character-sheet phase - personality/
    memory only, no stats) - flat d20 with no modifier until that changes. A real simplification,
    not a hidden assumption - flagged in NOTES.md."""
    return random.randint(1, 20)


def _combat_actor_label(participant):
    if participant is None:
        return "someone else"
    if participant.get("npc_key"):
        return participant["npc_key"]
    return f"character #{participant['character_id']}"


def detect_combat_start(provider, model, subspace_id, narration, acting_character_id, usage_sink=None):
    """After a free (non-combat) action resolves, ask a cheap judge whether it ignited a fight, and
    if so which OTHER present characters/NPCs are now involved. Returns a list of participant
    descriptors ({"character_id": id} or {"npc_key": key}) NOT including the acting character - the
    caller always adds them separately, since anyone whose action starts a fight is unquestionably in
    it. Empty list means no combat, including the trivial case where nobody else is even present."""
    present_chars = [c for c in db.characters_at_subspace(subspace_id) if c != acting_character_id]
    present_npcs = db.npcs_at_subspace(subspace_id)
    if not present_chars and not present_npcs:
        return []

    roster_lines = [f"character #{c}" for c in present_chars] + [n["key"] for n in present_npcs]
    system_prompt = f"""An action was just narrated in a multiplayer scene. Decide whether it ignited
a fight/combat encounter involving anyone else present.

# OTHERS PRESENT
{", ".join(roster_lines)}

# WHAT JUST HAPPENED
{narration}

If this is NOT combat, respond with started=false. If it IS combat, respond with started=true and
list which of the others present (using the exact identifiers above) are now combat participants -
only include people the fiction actually pulls into the fight, not everyone in the room. Submit via
decide_combat."""
    tool_specs = [
        {
            "name": "decide_combat",
            "description": "Decide whether combat just started, and who besides the acting character is involved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "started": {"type": "boolean"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact identifiers from the roster above, e.g. ['character #3', 'lenin'].",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["started", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_combat")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return []
    decision = result.tool_calls[0]["input"]
    if not decision.get("started"):
        return []

    out = []
    for p in decision.get("participants") or []:
        if p.startswith("character #"):
            try:
                cid = int(p[len("character #"):].strip())
            except ValueError:
                continue
            if cid in present_chars:
                out.append({"character_id": cid})
        elif any(n["key"] == p for n in present_npcs):
            out.append({"npc_key": p})
    return out


def detect_combat_end(provider, model, narration, usage_sink=None):
    """After a combat participant's turn resolves, ask whether the fight is now over. Fails safe to
    "still ongoing" on a missing/malformed response - silently dropping turn enforcement because a
    judge call hiccuped would be a worse failure than one extra round of asking."""
    system_prompt = f"""A combat participant's turn was just narrated. Decide whether the fight is
now over (resolved, one side fled/yielded/was defeated, a truce was struck, etc.) or still ongoing.

# WHAT JUST HAPPENED
{narration}

Submit via decide_combat_status."""
    tool_specs = [
        {
            "name": "decide_combat_status",
            "description": "Decide whether the fight has ended.",
            "parameters": {
                "type": "object",
                "properties": {"ended": {"type": "boolean"}, "reasoning": {"type": "string"}},
                "required": ["ended", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_combat_status")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return False
    return bool(result.tool_calls[0]["input"].get("ended"))


def _start_combat(location_id, subspace_id, acting_character_row, other_participants):
    """Rolls initiative for the acting character plus every newly-detected participant, seats them in
    descending order, and starts the rotation at whoever comes right after the acting character -
    they already just acted (that's what triggered this), so the next real turn belongs to someone
    else, not them again immediately."""
    acting_character_id = acting_character_row["id"]
    rolled = [{"character_id": acting_character_id, "initiative_roll": _roll_initiative(acting_character_row)}]
    for p in other_participants:
        if "character_id" in p:
            other_row = db.get_character_unchecked(p["character_id"])
            if other_row is None:
                continue
            rolled.append({"character_id": p["character_id"], "initiative_roll": _roll_initiative(other_row)})
        elif "npc_key" in p:
            rolled.append({"npc_key": p["npc_key"], "initiative_roll": _roll_initiative_for_npc()})

    rolled.sort(key=lambda r: r["initiative_roll"], reverse=True)
    acting_index = next(i for i, r in enumerate(rolled) if r.get("character_id") == acting_character_id)
    start_turn_index = (acting_index + 1) % len(rolled) if len(rolled) > 1 else 0

    return db.create_combat_encounter(location_id, subspace_id, rolled, start_turn_index=start_turn_index)


def detect_new_subspace(provider, model, action_content, narration, existing_subspace_names, usage_sink=None):
    """After a resolved action, decide whether the character has arrived somewhere genuinely new and
    distinct within their current location - and if a new place, propose a short name for it, or if
    it matches one of the already-existing subspaces there, say which one instead of inventing a
    near-duplicate. Deliberately a NARROW prompt (just the player's action text + the resulting
    narration + the existing subspace names at this location, not the full game/location/occupant
    context) - kept cheap since this runs on every action, not gated behind a free pre-filter.

    Deliberately conservative: only proposes a new subspace when the narration clearly, unambiguously
    describes the character now being in a distinct, separate sub-area - not just looking somewhere,
    mentioning a place in passing, or a minor beat in the same space. When in doubt, this should say
    no - a missed subspace is a much smaller problem than the world fragmenting into junk locations
    from every minor narrative beat.

    Returns {"new_subspace_name": str} if a genuinely new subspace should be created, or
    {"matched_existing_name": str} if it matches one already listed, or None if neither (nothing
    warrants a subspace at all). Fails safe to None on a missing/malformed response."""
    system_prompt = f"""A player's action was just resolved in a multiplayer scene. Decide whether it
describes the character arriving somewhere genuinely new and distinct within their current location -
a separate sub-area worth tracking on its own, not just a different way of describing where they
already were.

# SUBSPACES THAT ALREADY EXIST HERE
{", ".join(existing_subspace_names) if existing_subspace_names else "(none yet)"}

# THE PLAYER'S ACTION
{action_content}

# WHAT ACTUALLY HAPPENED
{narration}

Be conservative. Only decide a new subspace is warranted if the narration clearly and unambiguously
describes the character now being in a distinct, separate sub-area - climbing down into a cellar,
stepping through a door into another room, moving to a genuinely separate part of the scene. A
glance, a mention in passing, or continuing to act in the same space is NOT a new subspace. If it's
unclear either way, decide no new subspace.

If it does warrant one, also check whether it actually matches one of the subspaces already listed
above (e.g. someone else already found "The Cellar" and this is the same place) rather than a truly
new one - reuse the existing name in that case instead of inventing a near-duplicate.

Submit via decide_subspace."""
    tool_specs = [
        {
            "name": "decide_subspace",
            "description": "Decide whether this action warrants a new or existing subspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "warrants_subspace": {"type": "boolean"},
                    "matches_existing": {
                        "type": "boolean",
                        "description": "True if this matches one of the existing subspaces listed above.",
                    },
                    "name": {
                        "type": "string",
                        "description": "A short name for the new subspace, or the exact existing name it matches.",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["warrants_subspace", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_subspace")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return None
    decision = result.tool_calls[0]["input"]
    if not decision.get("warrants_subspace"):
        return None
    name = (decision.get("name") or "").strip()
    if not name:
        return None
    if decision.get("matches_existing"):
        return {"matched_existing_name": name}
    return {"new_subspace_name": name}


def resolve_action(provider, model, model_key, char_row, location_id, subspace_id, content, conversation,
                    usage_sink=None, budget_ok=None):
    """Resolves a multiplayer "action" - the mechanical resolution ALWAYS goes through
    engine.run_turn unchanged (dice/state stay app-owned via the same trusted, tested path
    single-player uses). This function's job is what happens around that call: turn-order enforcement
    if the acting character is a combat participant, combat start/end detection afterward, and (new,
    2026-08-22) checking whether the action warrants spawning/moving into a new subspace. Raises
    CombatTurnError instead of resolving anything if it isn't the character's turn.

    Returns a dict: {"turn_id", "reply_turn_id", "narration", "debug_events", "combat_encounter_id",
    "new_subspace_id", "moved_to_subspace_id"} - combat_encounter_id is set whenever this action
    happened during (or just started) an active encounter, else None; new_subspace_id is set only
    when this action's resolution just created a brand new subspace; moved_to_subspace_id is set
    whenever the character's subspace changed as a result of this action (whether the subspace was
    newly created or already existed) - the caller (server.py) uses this to tell the frontend it
    needs to reload location detail for wherever the character actually ended up.
    """
    character_id = char_row["id"]
    game_id = char_row["game_id"]

    encounter = db.get_active_combat_encounter(subspace_id)
    if encounter:
        participant_char_ids = {p["character_id"] for p in db.get_combat_participants(encounter["id"]) if p["character_id"]}
        if character_id in participant_char_ids:
            current_actor = db.get_current_combat_actor(encounter["id"])
            is_my_turn = current_actor is not None and current_actor.get("character_id") == character_id
            if not is_my_turn:
                raise CombatTurnError(
                    f"It isn't your turn in this fight - waiting on {_combat_actor_label(current_actor)}."
                )

    state = char_row["state"]
    narration, debug_events = engine.run_turn(
        provider, model, state, char_row["setting_key"], char_row["system_key"], conversation, content,
        usage_sink=usage_sink, budget_ok=budget_ok,
        # Council Mode is single-player-shaped (one narration, one player) - layering it onto a
        # multiplayer subspace's shared action log is a separate design question, not tackled here,
        # so it's explicitly off for this path rather than silently half-applied.
        council_mode=False, model_key=model_key,
    )

    # Significance flag - free, no new AI call. Reuses council.detect_stakes_signals, the same
    # rule-based detector single-player's own council-router already relies on (critical rolls,
    # canon events, resource label-band crossings, liability gains) - "a small flag gets created on
    # it," per direction, using a mechanism that already existed rather than a new judge call.
    stakes_signals = council.detect_stakes_signals(debug_events)
    flagged = bool(stakes_signals)
    flag_reason = "; ".join(stakes_signals) if stakes_signals else None

    turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, "action", content, model_key=model_key,
        debug_events=debug_events, flagged=flagged, flag_reason=flag_reason,
    )
    live.publish_subspace_event(subspace_id, db.get_turn_by_id(turn_id))
    reply_turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, "gm_reply", narration,
        reply_to_turn_id=turn_id, responder="gm", model_key=model_key,
    )
    live.publish_subspace_event(subspace_id, db.get_turn_by_id(reply_turn_id))

    result = {
        "turn_id": turn_id, "reply_turn_id": reply_turn_id,
        "narration": narration, "debug_events": debug_events, "combat_encounter_id": None,
        "new_subspace_id": None, "moved_to_subspace_id": None,
    }

    if encounter:
        current_actor = db.get_current_combat_actor(encounter["id"])
        if current_actor and current_actor.get("character_id") == character_id:
            # This WAS the current combat actor's turn (already verified above) - advance or end.
            if detect_combat_end(provider, model, narration, usage_sink=usage_sink):
                db.end_combat_encounter(encounter["id"])
            else:
                db.advance_combat_turn(encounter["id"])
                result["combat_encounter_id"] = encounter["id"]
        else:
            # A non-participant (bystander) acted freely alongside an ongoing fight - encounter
            # continues untouched.
            result["combat_encounter_id"] = encounter["id"]
    else:
        new_participants = detect_combat_start(provider, model, subspace_id, narration, character_id, usage_sink=usage_sink)
        if new_participants:
            encounter_id = _start_combat(location_id, subspace_id, char_row, new_participants)
            result["combat_encounter_id"] = encounter_id

    # Dynamic subspace creation (2026-08-22) - only checked for a free (non-combat) action, since a
    # fight in progress isn't the moment the world should be growing new geography, and only when a
    # judge provider actually resolved (same graceful-skip pattern as everywhere else in this module).
    if not result["combat_encounter_id"]:
        try:
            judge_provider, judge_model = providers.get_provider(JUDGE_MODEL_KEY)
        except providers.ProviderError:
            judge_provider, judge_model = None, None
        if judge_provider is not None:
            existing_names = [s["name"] for s in db.list_subspaces(location_id)]
            decision = detect_new_subspace(
                judge_provider, judge_model, content, narration, existing_names, usage_sink=usage_sink,
            )
            if decision:
                # char_row (from db.get_character) has no server_id of its own - characters aren't
                # scoped to a server directly, only via game_id - so this is always resolved from the
                # location, not read off the character.
                server_id = db.get_location(location_id)["server_id"]
                if "new_subspace_name" in decision:
                    new_subspace_id = db.create_subspace(
                        location_id, decision["new_subspace_name"], origin="dynamic",
                        created_by_character_id=character_id,
                    )
                    result["new_subspace_id"] = new_subspace_id
                    result["moved_to_subspace_id"] = new_subspace_id
                    db.move_character(character_id, server_id, location_id, new_subspace_id)
                else:
                    matched = db.find_subspace_by_name(location_id, decision["matched_existing_name"])
                    if matched and matched["id"] != subspace_id:
                        result["moved_to_subspace_id"] = matched["id"]
                        db.move_character(character_id, server_id, location_id, matched["id"])

    return result


# ==================== subspace conclusion (mark-concluded / storm-out) ====================

def _conclude_thread(game_id, location_id, subspace_id, triggered_by_character_id, note="The scene concludes.",
                      provider=None, model=None, usage_sink=None):
    """Logs a visible marker and resets conclusion tracking so a fresh thread can start being
    counted at this subspace. triggered_by_character_id attributes the log row to whoever caused the
    conclusion (the last to mark it, whoever stormed out, or - for the daily sweep, which has no real
    triggering player - just the first present character) - turns.character_id is NOT NULL with a
    real FK, and there's no "system" character to attribute a purely time-driven event to instead.
    That attribution is bookkeeping only; `note` is what a player actually reads, which is why the
    sweep passes a time-flavored note rather than implying some player caused it.

    provider/model, if given, trigger rumor generation for the just-concluded thread - optional
    because not every caller has a resolved provider available (see mark_concluded/storm_out)."""
    turn_id = db.record_subspace_message(game_id, location_id, subspace_id, triggered_by_character_id, "thread_concluded", note)
    live.publish_subspace_event(subspace_id, db.get_turn_by_id(turn_id))
    db.clear_subspace_conclusions(subspace_id)
    if provider is not None:
        generate_rumor(provider, model, game_id, location_id, subspace_id, usage_sink=usage_sink)


def mark_concluded(char_row, location_id, subspace_id, provider=None, model=None, usage_sink=None):
    """Regular (non-storm-out) mark-concluded. Returns (concluded: bool, marks_so_far: int,
    total_present: int). Unanimous means every PLAYER character currently at this subspace has
    marked it - NPCs aren't counted, per the brief's literal "all players" wording."""
    character_id = char_row["id"]
    game_id = char_row["game_id"]
    db.mark_subspace_concluded_by(subspace_id, character_id)

    present = db.characters_at_subspace(subspace_id)
    marks = db.get_subspace_conclusion_marks(subspace_id)
    if present and set(present) <= marks:
        _conclude_thread(game_id, location_id, subspace_id, character_id, provider=provider, model=model, usage_sink=usage_sink)
        return True, len(present), len(present)
    return False, len(marks & set(present)), len(present)


def detect_thread_should_continue(provider, model, subspace_id, remaining_character_count, usage_sink=None):
    """After someone storms out, ask whether enough people remain for the scene to meaningfully
    continue. Only ever called when at least one person remains - the zero-remaining case is decided
    deterministically by the caller (storm_out below), no AI call needed. Fails safe to "continues"
    on a missing/malformed response - silently auto-concluding a scene because a judge call hiccuped
    would be a worse failure than asking again next time."""
    context = _recent_subspace_context(subspace_id)
    system_prompt = f"""A character just stormed out of a scene, leaving {remaining_character_count}
participant(s) behind. Decide whether the scene has enough left to meaningfully continue, or whether
it should be considered concluded now that they're gone.

# RECENT ACTIVITY HERE
{context}

Submit via decide_continuation."""
    tool_specs = [
        {
            "name": "decide_continuation",
            "description": "Decide whether the remaining scene should continue or be concluded.",
            "parameters": {
                "type": "object",
                "properties": {"continues": {"type": "boolean"}, "reasoning": {"type": "string"}},
                "required": ["continues", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_continuation")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return True
    return bool(result.tool_calls[0]["input"].get("continues", True))


def storm_out(provider, model, char_row, location_id, subspace_id, final_message, usage_sink=None):
    """Sends an optional final flavor message (pure narrative text - no AI judging/reply, same as
    any player-authored line that isn't addressed to anyone), moves the character to their location's
    default subspace, clears their own conclusion mark at the subspace they left (their vote no
    longer counts once they're gone), and checks whether the scene should auto-conclude without them.

    provider/model may be None (e.g. the caller couldn't resolve a configured provider) - the leave
    itself still succeeds either way; only the headcount check gets skipped, defaulting to "still
    ongoing" rather than blocking someone from leaving because the AI is temporarily unavailable.

    Returns (new_subspace_id, thread_auto_concluded: bool).
    """
    character_id = char_row["id"]
    game_id = char_row["game_id"]

    if final_message:
        storm_turn_id = db.record_subspace_message(game_id, location_id, subspace_id, character_id, "storm_out", final_message)
        live.publish_subspace_event(subspace_id, db.get_turn_by_id(storm_turn_id))

    db.clear_character_conclusion_mark(subspace_id, character_id)

    new_subspace_id = db.get_default_subspace(location_id)["id"]
    server_id = db.get_location(location_id)["server_id"]
    db.move_character(character_id, server_id, location_id, new_subspace_id)

    remaining = [c for c in db.characters_at_subspace(subspace_id) if c != character_id]
    auto_concluded = False
    if not remaining:
        _conclude_thread(game_id, location_id, subspace_id, character_id, provider=provider, model=model, usage_sink=usage_sink)
        auto_concluded = True
    elif provider is not None:
        continues = detect_thread_should_continue(provider, model, subspace_id, len(remaining), usage_sink=usage_sink)
        if not continues:
            _conclude_thread(game_id, location_id, subspace_id, character_id, provider=provider, model=model, usage_sink=usage_sink)
            auto_concluded = True
    # else: no provider available - skip the check, thread stays open (fail-safe default).

    return new_subspace_id, auto_concluded


# ==================== ambient AI-call billing (shared by storm-out and the sweep) ====================

def bill_ambient_usage(usage_sink, present_character_ids):
    """Bills a non-player-triggered AI call (a storm-out headcount check, the daily sweep) per the
    confirmed cost model in NOTES.md: split evenly across every present player, each charged only
    pricing.AMBIENT_COST_PLAYER_SHARE of their share - the rest isn't charged to anyone.
    Local/anonymous-owned characters are skipped (no real budget to charge, same as everywhere else
    in this app). No-op if usage_sink is empty or nobody present has a billable account. Pure
    db/pricing calls, no Flask dependency - safe to call from the background scheduler thread as well
    as from a request handler."""
    if not usage_sink:
        return
    total_cost = sum(pricing.estimate_cost_usd(u) for u in usage_sink)
    payers = []
    for cid in present_character_ids:
        owner = db.get_character_owner(cid)
        if owner and not owner["is_local"]:
            payers.append((cid, owner["id"]))
    if not payers:
        return
    per_player_cost = pricing.split_ambient_cost_per_player(total_cost, len(payers))
    representative = max(usage_sink, key=lambda u: u.get("input_tokens", 0) + u.get("output_tokens", 0))
    for cid, user_id in payers:
        db.record_usage(
            user_id, cid, representative["provider"], representative["model"],
            representative.get("input_tokens", 0), representative.get("output_tokens", 0),
            representative.get("cache_read_tokens", 0), representative.get("cache_creation_tokens", 0),
            per_player_cost,
        )


# ==================== progression ticks + the daily conclusion sweep ====================

_MONTH_APPROX_DAYS = 30  # see advance_date_string's docstring


def advance_date_string(date_str, progression_speed):
    """Advances an ISO 'YYYY-MM-DD' date string by one progression_speed unit, using plain
    datetime.date + timedelta - no extra dependency. "month" is approximated as 30 days rather than
    a real calendar month (exact month-length math needs python-dateutil, a dependency this app
    doesn't otherwise need) - a documented simplification, not a hidden bug. Returns date_str
    unchanged if it can't be parsed, rather than crashing a tick over bad/missing data."""
    try:
        d = datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return date_str
    if progression_speed == "week":
        d += datetime.timedelta(days=7)
    elif progression_speed == "month":
        d += datetime.timedelta(days=_MONTH_APPROX_DAYS)
    else:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def detect_subspace_should_conclude(provider, model, subspace_id, usage_sink=None):
    """The daily sweep's per-subspace check: given the passage of time, should this subspace's
    ongoing thread now be considered concluded? Fails safe to "leave it open" on a missing/malformed
    response - an accidental auto-conclusion is worse than asking again at the next tick."""
    context = _recent_subspace_context(subspace_id)
    system_prompt = f"""A real amount of time has passed in this multiplayer scene since the last
check. Decide whether the ongoing activity at this subspace should now be considered concluded, or
is still meaningfully ongoing and should be left open.

# RECENT ACTIVITY HERE
{context}

Submit via decide_sweep."""
    tool_specs = [
        {
            "name": "decide_sweep",
            "description": "Decide whether this subspace's ongoing thread should be concluded.",
            "parameters": {
                "type": "object",
                "properties": {"should_conclude": {"type": "boolean"}, "reasoning": {"type": "string"}},
                "required": ["should_conclude", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_sweep")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return False
    return bool(result.tool_calls[0]["input"].get("should_conclude"))


def sweep_server(provider, model, server_id):
    """The daily/weekly/monthly conclusion sweep - called by the tick scheduler (scheduler.py), never
    by a player request. Walks every subspace in the server; subspaces with nobody present are
    skipped entirely (nothing there to conclude, and no one to bill even if there were - matches the
    same reasoning used to skip combat-classifier calls when nobody else is present). For each
    occupied subspace, asks detect_subspace_should_conclude and concludes it if so.

    Returns a list of per-subspace {"subspace_id", "usage_sink", "present_character_ids",
    "concluded"} dicts - billing each subspace's usage (via bill_ambient_usage above) is left to the
    caller, so this function's job stays "decide and log," not "decide and log and bill" tangled
    together."""
    results = []
    game = db.get_game_for_server(server_id)
    for subspace in db.list_subspaces_for_server(server_id):
        subspace_id = subspace["id"]
        present = db.characters_at_subspace(subspace_id)
        if not present:
            continue
        usage_sink = []
        should_conclude = detect_subspace_should_conclude(provider, model, subspace_id, usage_sink=usage_sink)
        if should_conclude:
            # provider/model passed through so this conclusion generates a rumor too, same as
            # mark-concluded/storm-out - its cost lands in the same usage_sink the caller
            # (scheduler.fire_tick) already bills for this subspace's sweep decision.
            _conclude_thread(
                game["id"], subspace["location_id"], subspace_id, present[0],
                note="Time passes, and the scene quietly concludes.",
                provider=provider, model=model, usage_sink=usage_sink,
            )
        results.append({
            "subspace_id": subspace_id, "usage_sink": usage_sink,
            "present_character_ids": present, "concluded": should_conclude,
        })
    return results


# ==================== rumors ====================

def generate_rumor(provider, model, game_id, location_id, subspace_id, usage_sink=None):
    """Generates a rumor summary of a just-concluded thread, stored unpublished until the next tick's
    broadcast (see compile_and_publish_rumors). Per direction: summary LENGTH scales with judged
    importance, DETAIL scales with judged publicity (a shouted public brawl spreads with real detail;
    something done in secret barely spreads at all, and vaguely). A single call handles both the
    importance/publicity judgment and the summary itself, rather than two separate calls - the model
    is capable of following a length/detail rubric within one structured response, and this is
    already the third AI call layered onto some conclusions (headcount/sweep check, then this) so
    keeping it to one call matters for cost, not just simplicity.

    Silently does nothing (no rumor stored) if there's no real activity at the subspace to summarize,
    or the call fails to return usable structured output - an unnecessary or missing rumor is a much
    smaller problem than a call that crashes a conclusion."""
    context = _recent_subspace_context(subspace_id, limit=30)
    if context == "(no prior activity at this subspace)":
        return None
    system_prompt = f"""A scene/thread at this subspace just concluded. Write a brief rumor summary
of what happened, suitable for spreading through the wider server as gossip/news.

# WHAT HAPPENED
{context}

Judge two things first, then write the summary calibrated to them:
- importance (1-5): how significant this was - 1 for trivial background chatter, 5 for something
  genuinely momentous. LOW importance should produce a very short, throwaway line (a single terse
  sentence is fine, or even just "nothing worth repeating"). HIGH importance should produce a fuller
  summary with real substance.
- publicity (1-5): how public/witnessed this was - 1 for something done in total secrecy, 5 for
  something loud and witnessed by many. LOW publicity should produce vague, secondhand, uncertain
  details (rumors distort what wasn't directly witnessed). HIGH publicity should produce concrete,
  specific, confidently-reported detail.

Submit via submit_rumor."""
    tool_specs = [
        {
            "name": "submit_rumor",
            "description": "Submit the rumor summary and its importance/publicity judgment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                    "publicity": {"type": "integer", "minimum": 1, "maximum": 5},
                    "summary": {"type": "string"},
                },
                "required": ["importance", "publicity", "summary"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Submit the rumor now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="submit_rumor")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return None
    data = result.tool_calls[0]["input"]
    summary = (data.get("summary") or "").strip()
    if not summary:
        return None
    server_id = db.get_location(location_id)["server_id"]
    return db.create_rumor(
        server_id, location_id, subspace_id, summary,
        data.get("importance", 1), data.get("publicity", 1),
    )


def compile_and_publish_rumors(provider, model, server_id, usage_sink=None):
    """Called by scheduler.py at each tick - compiles every rumor accumulated since the last tick
    into one cohesive server-wide broadcast and publishes it. No-op (no AI call, nothing published)
    if there's nothing unpublished to compile - a quiet tick shouldn't manufacture news."""
    unpublished = db.get_unpublished_rumors(server_id)
    if not unpublished:
        return None
    rumor_lines = "\n".join(
        f"- (importance {r['importance']}/5, publicity {r['publicity']}/5): {r['summary']}" for r in unpublished
    )
    system_prompt = f"""Compile the following individual rumors/events from across the server into
one cohesive news/rumor broadcast - a few short paragraphs of in-world gossip/news, weaving the
individual items together into something that reads like a period-appropriate grapevine report, not
a bulleted list. More important/public items should get more prominent, detailed treatment; minor or
secretive ones should stay as a brief aside, or be dropped if they don't fit naturally.

# RUMORS SINCE THE LAST TICK
{rumor_lines}

Write only the broadcast text, nothing else."""
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Write the broadcast now.")
    result = provider.call(model, system_prompt, [], conversation)
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    content = "\n".join(result.text_parts).strip()
    if not content:
        return None
    broadcast_id = db.publish_rumor_broadcast(server_id, content, len(unpublished))
    live.publish_server_event(server_id, {"type": "rumor_broadcast", "id": broadcast_id, "content": content})
    return broadcast_id
