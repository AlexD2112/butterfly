"""
Multiplayer message orchestration: deciding whether an AI response to a player's room message is
warranted at all, and if so, in whose voice - a specific present NPC, or the GM. See NOTES.md
(2026-08-21) for the corrected design this implements: a real yes/no gate first, THEN pick a
responder - not "the AI always responds," which was an earlier, wrong reading of the brief.

Reply-threading short-circuits the judge entirely, per direction (not inferred):
  - a reply to another PLAYER's message never needs judging - it's obviously player-to-player, so
    the GM may still add a brief ambient beat, but no NPC voice is ever picked for it.
  - a reply to an existing AI/NPC message never needs judging either - we already know exactly whose
    voice this is continuing.
  - only a fresh, non-reply message needs the judge call at all.

Mechanics (dice, checks, state changes) are completely untouched by any of this - this module never
calls anything in systems/_shared/core.py. It only ever decides who speaks in a room's shared chat
log, and what they say. Full "action"-kind resolution (with initiative/turn order for combat) is a
later phase, not handled here - see server.py's send route, which rejects kind="action" for now with
a clear "not built yet" error rather than faking a response.
"""

import json

import council
import db
import providers

# Same fixed cheap model council.py's judges use, and for the same reason - narrow judgment work
# doesn't need a strong (or the sender's chosen) model. Kept as a separate name here rather than
# importing council.JUDGE_MODEL_KEY directly at call sites, so this module's own intent reads clearly.
JUDGE_MODEL_KEY = council.JUDGE_MODEL_KEY


def _recent_location_context(location_id, limit=8):
    """A short plain-text rendering of the most recent activity at a location, for the judge/reply
    prompts - deliberately small (chat context, not a full transcript)."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM turns WHERE location_id = ? ORDER BY id DESC LIMIT ?", (location_id, limit)
    ).fetchall()
    conn.close()
    lines = []
    for r in reversed(rows):
        if r["kind"] in ("message", "action"):
            lines.append(f"[player #{r['character_id']}]: {r['player_input']}")
        elif r["kind"] in ("npc_reply", "gm_reply"):
            lines.append(f"[{r['responder']}]: {r['narration']}")
    return "\n".join(lines) if lines else "(no prior activity at this location)"


def _npc_roster_text(npcs):
    if not npcs:
        return "(no NPCs currently present at this location)"
    return "\n".join(f"- {n['key']}: {db.build_npc_sheet_text(n)}" for n in npcs)


def judge_responder(provider, model, location_id, content, usage_sink=None):
    """The real yes/no gate for a fresh (non-reply) message: does anyone respond, and if so, a
    present NPC's key or "gm". Returns (responds: bool, responder: str|None, reasoning: str|None).
    Fails safe to (False, None, None) on a missing/malformed response, or if the model names an NPC
    that isn't actually present - silence is the safer default than inventing an unwanted or
    impossible response."""
    npcs = db.npcs_at_location(location_id)
    system_prompt = f"""You are deciding whether anyone responds to a player's message in a
multiplayer scene, and if so, who.

# NPCS PRESENT AT THIS LOCATION
{_npc_roster_text(npcs)}

# RECENT ACTIVITY HERE
{_recent_location_context(location_id)}

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


def generate_reply(provider, model, location_id, responder, content, usage_sink=None):
    """Generates the actual reply text once a responder is already decided (via judge_responder, or
    a reply-threading shortcut). responder is "gm" or a present NPC's key."""
    context = _recent_location_context(location_id)
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
        npcs = db.npcs_at_location(location_id)
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


def handle_message(provider, model, game_id, room_id, location_id, character_id, content,
                    reply_to_turn_id=None, usage_sink=None):
    """The full flow for a "message"-kind send: record it, decide (via reply-threading shortcuts or
    the judge) whether/who responds, generate that reply if any, record it, and update NPC memory if
    an NPC replied. Returns (message_turn_id, reply_turn_id_or_None, responder_or_None) - responder
    is None whenever nobody responds, which is a real, expected outcome now, not a failure."""
    message_turn_id = db.record_room_message(
        game_id, room_id, location_id, character_id, "message", content, reply_to_turn_id=reply_to_turn_id,
    )

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
            judge_provider, judge_model, location_id, content, usage_sink=usage_sink,
        )
        if responds:
            responder = judged_responder

    if responder is None:
        return message_turn_id, None, None

    reply_text = generate_reply(provider, model, location_id, responder, content, usage_sink=usage_sink)
    if not reply_text:
        return message_turn_id, None, None

    reply_kind = "gm_reply" if responder == "gm" else "npc_reply"
    reply_turn_id = db.record_room_message(
        game_id, room_id, location_id, character_id, reply_kind, reply_text,
        reply_to_turn_id=message_turn_id, responder=responder,
    )

    if responder != "gm":
        npcs = db.npcs_at_location(location_id)
        npc = next((n for n in npcs if n["key"] == responder), None)
        if npc:
            db.add_npc_memory(npc["id"], f'Responded to a message: "{content[:120]}" -> "{reply_text[:120]}"')

    return message_turn_id, reply_turn_id, responder
