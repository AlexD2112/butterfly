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
   also where detect_new_space (2026-08-22, widened 2026-08-23, see below) checks for organic world
   growth.

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

6. Dynamic space creation (2026-08-22, widened 2026-08-23) - detect_new_space() runs after a resolved
   ACTION (never a message - those are dialogue, not movement) and decides whether the narration
   describes arriving somewhere genuinely new: a new SUBSPACE within the character's current LOCATION,
   or a whole new LOCATION entirely (originally Subspace-only; Location-level creation and admin-
   controlled granularity both landed 2026-08-23 - see NOTES.md). Deliberately scoped to a cheap
   prompt (the player's action text + the resulting narration + the setting's own scale guidance +
   the names of Locations/Subspaces that already exist, NOT the full game/occupant context) and
   instructed to be conservative - biased toward "no" unless the signal is unambiguous, so a passing
   glance or a minor beat in the same space doesn't spawn anything. What counts as "a new Location" vs
   "just a new Subspace in the same place" is setting-relative, not a fixed universal threshold - each
   setting in engine.SETTING_REGISTRY carries its own location_scale_hint (building-scale for a
   single-city setting like Petrograd, city/region-scale for a marching-army setting like the
   Crusades) which gets folded directly into the classifier prompt. A new Location auto-seeds its own
   default Subspace (see db.create_location) and a dynamically-created space of either kind is
   immediately visible/movable-to for everyone who can see it, same as an admin-created one - no
   separate discovery mechanic (see NOTES.md). Admins can rename, delete, or merge Locations/Subspaces
   after the fact (db.merge_locations/merge_subspaces/delete_location/delete_subspace/rename_location/
   rename_subspace) as the cleanup mechanism for a bad or duplicate dynamic generation - this is what
   makes admin-free organic growth viable instead of a one-way ratchet toward junk.

Mechanics (dice, checks, state changes) still only ever happen inside engine.run_turn's existing
tool loop - this module never calls systems/_shared/core.py's check/state functions directly, only
engine.run_turn (for actions) and combat initiative's own dice roll (a narrow, separate mechanic).
"""

import datetime
import json
import random

import council
import db
import discord_webhook
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
    """A short plain-text rendering of the most recent activity at a subspace, by real character NAME
    (2026-09-03 - was "player #<id>", which meant a prompt could never actually reference another
    character by name even where it mattered) - for the judge/reply prompts, and (2026-09-03) the
    mechanical action-resolution prompt too, see resolve_action's extra_context param below.
    Deliberately small (chat context, not a full transcript)."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT t.*, c.name AS character_name FROM turns t LEFT JOIN characters c ON c.id = t.character_id "
        "WHERE t.subspace_id = ? ORDER BY t.id DESC LIMIT ?", (subspace_id, limit)
    ).fetchall()
    conn.close()
    lines = []
    for r in reversed(rows):
        speaker = r["character_name"] or f"player #{r['character_id']}"
        if r["kind"] in ("message", "action"):
            lines.append(f"[{speaker}]: {r['player_input']}")
        elif r["kind"] in ("npc_reply", "gm_reply"):
            lines.append(f"[{r['responder']}]: {r['narration']}")
    return "\n".join(lines) if lines else "(no prior activity at this subspace)"


def _npc_roster_text(npcs):
    if not npcs:
        return "(no NPCs currently present at this subspace)"
    return "\n".join(f"- {n['key']}: {db.build_npc_sheet_text(n)}" for n in npcs)


def classify_message_or_action(provider, model, content, usage_sink=None):
    """Cheap classification: does this plain-typed text read as "message" (in-scene talk - banter,
    addressing someone, describing tone/attitude, nothing attempted beyond talking) or "action" (the
    character trying to actually DO something - move, fight, search, use/take/give something, attempt
    a skilled task - anything that should go through real mechanical resolution)?

    Only ever used by run_discord_bot.py's on_message handler (2026-09-03, per direction) - the
    website has an explicit Message/Action toggle the player chooses themselves; Discord has no
    equivalent UI, so a plain-typed message needs to be classified instead of always defaulting to
    "message" (which is what silently caused the earlier "typed a move, got talk-only handling with
    no dynamic-space detection" gap - see NOTES.md). /action and /message on Discord bypass this
    entirely - an explicit command is never reclassified, only genuinely unlabeled text is.

    Fails safe to "message" - the cheaper, lower-mechanical-stakes default - on any missing/malformed
    response, same fail-safe direction judge_responder already uses."""
    system_prompt = f"""Decide whether the following text, sent by a player in a multiplayer historical
RPG scene, is:
- "message": in-scene talk - conversation, banter, addressing another character or NPC, describing
  speech/tone/attitude, with no attempt to physically or mechanically DO anything beyond talking.
- "action": an attempted action - the character trying to physically or mechanically do something
  (move somewhere, fight, search, use/take/give an item, attempt a skilled task) - anything that
  should be resolved with dice/game mechanics rather than just a conversational reaction.

# THE MESSAGE
{content}

When genuinely ambiguous, prefer "message" - it's the lower-stakes default. Submit via classify."""
    tool_specs = [
        {
            "name": "classify",
            "description": "Classify the message as 'message' or 'action'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["message", "action"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["kind", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Classify now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="classify")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return "message"
    kind = result.tool_calls[0]["input"].get("kind")
    return kind if kind in ("message", "action") else "message"


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
        # Naming the actual current place explicitly (2026-09-03, real fix - see NOTES.md) is what
        # makes the "don't narrate a move" instruction actually stick. An earlier version of this
        # prompt only said "don't narrate moving to a new location" in the abstract, with no anchor
        # for what "here" even was - caught live via a real Discord message ("Go to the greek side
        # camp"), where the model dropped the literal "you travel to..." phrasing but still narrated
        # the character AT the Greek camp, since it had nothing telling it where they actually were
        # to stay grounded to instead. Repeating the real place name below is what closes that gap.
        subspace = db.get_subspace(subspace_id)
        location = db.get_location(subspace["location_id"]) if subspace else None
        current_place = f"{location['name']} - {subspace['name']}" if location and subspace else "the current scene"
        system_prompt = f"""You are the game master narrating a small ambient beat in a multiplayer
scene - a reaction, an environmental detail, a passing NPC's glance - in response to what a player
just said. Keep it brief (1-3 sentences), grounded, not a full scene. Do not invent dice results or
mechanical state changes - this is pure narration.

# WHERE THE CHARACTER ACTUALLY IS RIGHT NOW
{current_place}

Critically: this is a "message" (talk), not an "action" - the character has NOT moved and is still
exactly at {current_place}, no matter what they just said, asked, or expressed intent to do. Never
describe them as being anywhere else, arriving anywhere else, or experiencing a different physical
setting - not even briefly, not even to acknowledge the request. If their message reads like an
attempted move (e.g. "go to X", "head over to Y") rather than in-scene talk, react only to what's
happening right at {current_place} - a passing thought, someone else's reaction to the idea, an
ambient detail - never a description of wherever they were asking about. A real move only ever
happens through a separate mechanical resolution path (an "action"), never through this kind of reply.

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
                    reply_to_turn_id=None, usage_sink=None, origin="web"):
    """The full flow for a "message"-kind send: record it, decide (via reply-threading shortcuts or
    the judge) whether/who responds, generate that reply if any, record it, and update NPC memory if
    an NPC replied. Returns (message_turn_id, reply_turn_id_or_None, responder_or_None) - responder
    is None whenever nobody responds, which is a real, expected outcome now, not a failure.

    origin (2026-09-03): "web" (default, from server.py's route) also mirrors the player's own
    message into the location's linked Discord channel/thread, if any - see discord_webhook.py.
    "discord" (from run_discord_bot.py) skips ONLY that part, since a Discord-originated message is
    already visible in Discord as the player's own message there - no need to mirror it back to
    itself. Either way, any AI reply this produces IS always mirrored (below) - a Discord-originated
    message still needs its GM/NPC reply posted into Discord, since that reply doesn't exist there
    yet until this call creates it."""
    message_turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, "message", content, reply_to_turn_id=reply_to_turn_id,
    )
    message_turn = db.get_turn_by_id(message_turn_id)
    live.publish_subspace_event(subspace_id, message_turn)
    if origin != "discord":
        discord_webhook.mirror_turn(message_turn)

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
    reply_turn = db.get_turn_by_id(reply_turn_id)
    live.publish_subspace_event(subspace_id, reply_turn)
    discord_webhook.mirror_turn(reply_turn)  # both origins want the reply mirrored - see docstring above

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


def detect_new_space(provider, model, action_content, narration, setting_label, location_scale_hint,
                      all_locations_with_subspaces, current_location_name, usage_sink=None):
    """After a resolved action, decide whether the character has arrived somewhere genuinely new -
    either a new Subspace within their current Location, or a whole new Location entirely (2026-08-23
    - widened from the original subspace-only version, same call, same cost: still one cheap classifier
    check per action, not two). Deliberately a NARROW prompt (action text + narration + the setting's
    own scale guidance + a name-only map of what already exists, not the full game/occupant context) -
    kept cheap since this runs on every free action.

    all_locations_with_subspaces: {location_name: [subspace_name, ...]} for EVERY location on the
    server, not just the current one (2026-08-23 fix - see NOTES.md's launch-readiness entry: the
    original current-location-only visibility meant "I head back to the telegraph room" after leaving
    for a different Location couldn't recognize the telegraph room it already knew about elsewhere,
    and created a duplicate under the wrong Location instead of routing back to the right one).
    current_location_name identifies which entry is "home" for the new_subspace/matched_subspace
    decisions (those are always scoped to the character's CURRENT Location specifically).

    Deliberately conservative throughout: only proposes a new space when the narration clearly,
    unambiguously describes the character now being somewhere distinct - not just looking somewhere,
    mentioning a place in passing, or a minor beat in the same space. When in doubt, this should say
    no - a missed space is a much smaller problem than the world fragmenting into junk locations from
    every minor narrative beat. The Location/Subspace distinction itself is setting-relative
    (location_scale_hint tells the model what "a new Location" actually means for THIS setting - a
    building in Petrograd, a city in the Crusades campaign - so the same classifier logic gives
    sensibly different granularity per setting rather than one fixed universal threshold).

    Returns one of: None (nothing warrants a new space), {"new_subspace_name": str},
    {"matched_subspace_name": str}, {"new_location_name": str, "target_subspace_name": str|None}, or
    {"matched_location_name": str, "target_subspace_name": str|None}. target_subspace_name (only ever
    set alongside a *_location result) names a specific subspace within that OTHER location - existing
    (reuse it) or new (create it there) - rather than always landing on that location's plain default.
    Fails safe to None on a missing/malformed response."""
    location_lines = []
    for loc_name, subspace_names in all_locations_with_subspaces.items():
        marker = " (character's CURRENT location)" if loc_name == current_location_name else ""
        location_lines.append(f"- {loc_name}{marker} - subspaces: {', '.join(subspace_names) if subspace_names else '(none yet)'}")

    system_prompt = f"""A player's action was just resolved in a multiplayer scene. Decide whether it
describes the character arriving somewhere genuinely new - either a new sub-area within their current
Location, or an entirely new Location (which may be one they've visited before) - or nowhere new at all.

# SETTING
{setting_label}
{location_scale_hint}

# EVERY LOCATION THAT ALREADY EXISTS ON THIS SERVER, AND ITS SUBSPACES
{chr(10).join(location_lines) if location_lines else "(none yet)"}

# THE PLAYER'S ACTION
{action_content}

# WHAT ACTUALLY HAPPENED
{narration}

Decide exactly one of:
- no_change: nothing here warrants tracking a new space at all - a glance, a mention in passing, or
  continuing to act in the same space.
- new_subspace: the character is now in a distinct, separate sub-area WITHIN their CURRENT Location -
  climbing down into a cellar, stepping through a door into another room, moving to a genuinely
  separate part of the same broader place. Use the setting's own scale guidance above to judge what
  counts as "the same Location." Never used if the destination actually matches a subspace already
  listed under a DIFFERENT Location below - that's matched_location instead (see its target_subspace).
- matched_subspace: same as new_subspace, but it actually matches one of the CURRENT Location's own
  subspaces listed above (e.g. someone else already found "The Cellar" and this is the same place) -
  reuse that name, don't invent a near-duplicate.
- new_location: the character has left their current Location entirely for a genuinely different,
  never-before-seen place, at the granularity described in the setting's scale guidance above - not a
  different room in the same building, but a different building/city/region as appropriate for this
  setting. You may optionally also name a specific subspace within it (target_subspace_name) if the
  narration is specific enough - otherwise leave target_subspace_name empty and the app will use that
  new Location's default space.
- matched_location: same as new_location, but the DESTINATION LOCATION matches one already listed
  above (reuse its exact name) - including when the action describes RETURNING somewhere the character
  has been before. If the narration also names a specific room/spot, check whether that name already
  appears in that Location's own subspace list above and set target_subspace_name to the EXACT
  matching name if so (don't invent a near-duplicate of a place that already exists there); if it's a
  genuinely new spot within that other Location, you may still set target_subspace_name to a new name
  for it. Leave target_subspace_name empty if the narration doesn't single out a specific spot.

Be conservative throughout. If it's unclear, decide no_change. Submit via decide_space."""
    tool_specs = [
        {
            "name": "decide_space",
            "description": "Decide whether this action warrants a new or existing Location/Subspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["no_change", "new_subspace", "matched_subspace", "new_location", "matched_location"],
                    },
                    "name": {
                        "type": "string",
                        "description": "For new_subspace/new_location: a short name for the new space. For matched_subspace/matched_location: the exact existing name it matches.",
                    },
                    "target_subspace_name": {
                        "type": ["string", "null"],
                        "description": "Only for new_location/matched_location: optionally, a specific subspace within that OTHER location to land in - an existing name from its list to reuse, or a new name to create there. Omit/null to just use that location's default space.",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["kind", "reasoning"],
            },
        }
    ]
    conversation = provider.new_conversation()
    provider.append_user_message(conversation, "Decide now.")
    result = provider.call(model, system_prompt, tool_specs, conversation, force_tool_name="decide_space")
    if usage_sink is not None and result.usage is not None:
        usage_sink.append(result.usage)
    if not result.tool_calls:
        return None
    decision = result.tool_calls[0]["input"]
    kind = decision.get("kind")
    if kind is None or kind == "no_change":
        return None
    name = (decision.get("name") or "").strip()
    if not name:
        return None
    target_subspace_name = (decision.get("target_subspace_name") or "").strip() or None
    return {
        "new_subspace": {"new_subspace_name": name},
        "matched_subspace": {"matched_subspace_name": name},
        "new_location": {"new_location_name": name, "target_subspace_name": target_subspace_name},
        "matched_location": {"matched_location_name": name, "target_subspace_name": target_subspace_name},
    }.get(kind)


def resolve_action(provider, model, model_key, char_row, location_id, subspace_id, content, conversation,
                    usage_sink=None, budget_ok=None, origin="web"):
    """Resolves a multiplayer "action" - the mechanical resolution ALWAYS goes through
    engine.run_turn unchanged (dice/state stay app-owned via the same trusted, tested path
    single-player uses). This function's job is what happens around that call: turn-order enforcement
    if the acting character is a combat participant, combat start/end detection afterward, and (2026-
    08-22, widened 2026-08-23) checking whether the action warrants spawning/moving into a new
    Subspace OR a whole new Location. Raises CombatTurnError instead of resolving anything if it
    isn't the character's turn.

    Returns a dict: {"turn_id", "reply_turn_id", "narration", "debug_events", "combat_encounter_id",
    "new_subspace_id", "new_location_id", "moved_to_subspace_id", "moved_to_location_id"} -
    combat_encounter_id is set whenever this action happened during (or just started) an active
    encounter, else None; new_subspace_id/new_location_id are set only when this action's resolution
    just created that brand new space (never both at once); moved_to_subspace_id is set whenever the
    character's subspace changed as a result of this action, regardless of whether it's newly
    created, matched by name, or the default subspace of a newly-created/matched Location;
    moved_to_location_id is set alongside it, but only when the character's LOCATION also changed
    (a Subspace-only move within the same Location leaves it None) - the caller (server.py) uses
    these to tell the frontend it needs to reload location/subspace detail for wherever the character
    actually ended up.

    origin (2026-09-03): see handle_message's docstring - same meaning, same "always mirror the
    reply, only skip mirroring the player's own turn when it's already visible in Discord" rule.
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

    # Real bug, found live via real Discord play (2026-09-03): engine.run_turn is single-player-shaped
    # and, unchanged, resolves an action with ZERO awareness that anyone else is even in the scene - a
    # character's action would get narrated as if they were alone, even seconds after another
    # character in the SAME subspace had just done something the narration should plausibly have
    # reacted to. _recent_subspace_context is the exact same shared-scene helper judge_responder/
    # generate_reply already use for the "message" pipeline - resolve_action just never fed it into
    # the mechanical pipeline until now. Skip it entirely (None, not the placeholder string) when
    # there's genuinely no prior activity - no point padding the prompt with a section announcing
    # there's nothing to be aware of.
    scene_context = _recent_subspace_context(subspace_id)
    if scene_context == "(no prior activity at this subspace)":
        scene_context = None

    state = char_row["state"]
    narration, debug_events = engine.run_turn(
        provider, model, state, char_row["setting_key"], char_row["system_key"], conversation, content,
        usage_sink=usage_sink, budget_ok=budget_ok,
        # Council Mode is single-player-shaped (one narration, one player) - layering it onto a
        # multiplayer subspace's shared action log is a separate design question, not tackled here,
        # so it's explicitly off for this path rather than silently half-applied.
        council_mode=False, model_key=model_key,
        extra_context=scene_context,
        # The server's shared date is advanced by scheduler.py ticks, never by one player's turn.
        allow_time_advance=False,
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
    action_turn = db.get_turn_by_id(turn_id)
    live.publish_subspace_event(subspace_id, action_turn)
    if origin != "discord":
        discord_webhook.mirror_turn(action_turn)
    reply_turn_id = db.record_subspace_message(
        game_id, location_id, subspace_id, character_id, "gm_reply", narration,
        reply_to_turn_id=turn_id, responder="gm", model_key=model_key,
    )
    reply_turn = db.get_turn_by_id(reply_turn_id)
    live.publish_subspace_event(subspace_id, reply_turn)
    discord_webhook.mirror_turn(reply_turn)

    result = {
        "turn_id": turn_id, "reply_turn_id": reply_turn_id,
        "narration": narration, "debug_events": debug_events, "combat_encounter_id": None,
        "new_subspace_id": None, "new_location_id": None,
        "moved_to_subspace_id": None, "moved_to_location_id": None,
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

    # Dynamic space creation (2026-08-22, widened 2026-08-23 to also cover whole new Locations, not
    # just Subspaces) - only checked for a free (non-combat) action, since a fight in progress isn't
    # the moment the world should be growing new geography, and only when a judge provider actually
    # resolved (same graceful-skip pattern as everywhere else in this module).
    if not result["combat_encounter_id"]:
        try:
            judge_provider, judge_model = providers.get_provider(JUDGE_MODEL_KEY)
        except providers.ProviderError:
            judge_provider, judge_model = None, None
        if judge_provider is not None:
            # char_row (from db.get_character) has no server_id of its own - characters aren't scoped
            # to a server directly, only via game_id - so this is always resolved from the location,
            # not read off the character.
            server_id = db.get_location(location_id)["server_id"]
            setting_info = engine.SETTING_REGISTRY.get(char_row["setting_key"], {})
            current_location = db.get_location(location_id)
            all_locations_with_subspaces = {
                l["name"]: [s["name"] for s in db.list_subspaces(l["id"])]
                for l in db.list_locations(server_id)
            }
            decision = detect_new_space(
                judge_provider, judge_model, content, narration,
                setting_info.get("label", char_row["setting_key"]),
                setting_info.get("location_scale_hint", ""),
                all_locations_with_subspaces, current_location["name"], usage_sink=usage_sink,
            )
            if decision:
                if "new_subspace_name" in decision:
                    new_subspace_id = db.create_subspace(
                        location_id, decision["new_subspace_name"], origin="dynamic",
                        created_by_character_id=character_id,
                    )
                    result["new_subspace_id"] = new_subspace_id
                    result["moved_to_subspace_id"] = new_subspace_id
                    db.move_character(character_id, server_id, location_id, new_subspace_id)
                elif "matched_subspace_name" in decision:
                    matched = db.find_subspace_by_name(location_id, decision["matched_subspace_name"])
                    if matched and matched["id"] != subspace_id:
                        result["moved_to_subspace_id"] = matched["id"]
                        db.move_character(character_id, server_id, location_id, matched["id"])
                elif "new_location_name" in decision:
                    new_location_id = db.create_location(server_id, decision["new_location_name"])
                    landing_subspace = _resolve_target_subspace(
                        new_location_id, decision.get("target_subspace_name"), character_id,
                    )
                    result["new_location_id"] = new_location_id
                    result["moved_to_location_id"] = new_location_id
                    result["moved_to_subspace_id"] = landing_subspace["id"]
                    db.move_character(character_id, server_id, new_location_id, landing_subspace["id"])
                elif "matched_location_name" in decision:
                    matched_location = db.find_location_by_name(server_id, decision["matched_location_name"])
                    if matched_location and matched_location["id"] != location_id:
                        landing_subspace = _resolve_target_subspace(
                            matched_location["id"], decision.get("target_subspace_name"), character_id,
                        )
                        result["moved_to_location_id"] = matched_location["id"]
                        result["moved_to_subspace_id"] = landing_subspace["id"]
                        db.move_character(character_id, server_id, matched_location["id"], landing_subspace["id"])

    return result


def _resolve_target_subspace(location_id, target_subspace_name, character_id):
    """Helper for resolve_action's new_location/matched_location branches: picks which subspace to
    actually land the character in within that Location. No target_subspace_name given -> that
    Location's default subspace (the common case). A name given -> reuse it if a subspace with that
    name already exists there (case-insensitive), else create a new one - this is what lets the
    classifier route "I head back to the telegraph room" straight to the SAME Telegraph Room subspace
    under whichever Location it actually belongs to, instead of duplicating it under wherever the
    character happened to be standing (see NOTES.md's launch-readiness entry for the real miscalibration
    this fixes)."""
    if not target_subspace_name:
        return db.get_default_subspace(location_id)
    existing = db.find_subspace_by_name(location_id, target_subspace_name)
    if existing:
        return existing
    new_id = db.create_subspace(location_id, target_subspace_name, origin="dynamic", created_by_character_id=character_id)
    return db.get_subspace(new_id)


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
    discord_webhook.post_rumor_broadcast(server_id, content)
    return broadcast_id


# ==================== level-up options (shared by the web picker and Discord's /levelup) ====================

def level_up_options_for_character(char_row):
    """The eligible skill/feat/attribute choices for a character's pending level-up - the same logic
    server.py's GET /api/character/<id>/level_up_options exposes to the web picker, extracted here
    (2026-09-03) so run_discord_bot.py's /levelup command uses the EXACT same eligibility rules
    instead of a second, driftable copy of them. Returns {"skills", "feats", "attributes"} - callers
    are responsible for checking character.level_up_pending themselves first (this doesn't gate on
    it, since some callers already know it's pending by the time they get here)."""
    character = char_row["state"]["character"]
    system = engine.get_system(char_row["system_key"])
    skills = [
        {"skill": name, "attribute": entry["attribute"], "tier": entry["tier"]}
        for name, entry in (character.get("skills") or {}).items()
    ]
    existing_feat_ids = {f.get("id") for f in character.get("feats", [])}
    feats = []
    for feat_id, entry in system.FEAT_CATALOG.items():
        if feat_id in existing_feat_ids or entry.get("min_level", 1) > character["level"]:
            continue
        prereq = entry.get("prerequisite_skill")
        if prereq:
            have = (character.get("skills") or {}).get(prereq["skill"])
            if (have["tier"] if have else 0) < prereq["min_tier"]:
                continue
        feats.append({"id": feat_id, "name": entry["name"], "description": entry["description"]})
    return {"skills": skills, "feats": feats, "attributes": character["attributes"]}
