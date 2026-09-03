"""
Web -> Discord mirroring (2026-09-03): posts a turn recorded on the web side into the matching
Discord channel/thread via a plain webhook HTTP call - deliberately does NOT import discord.py or
hold any gateway connection, so this works directly from the Flask process (server.py/multiplayer.py)
with zero coupling to whether run_discord_bot.py is even running. See discord_bot.py's module
docstring for the overall Discord design.

The other direction (a message/action a player sent FROM Discord) is handled by run_discord_bot.py
instead, which calls multiplayer.handle_message/resolve_action with origin="discord" specifically so
THIS module doesn't also try to mirror it back to Discord - see those functions' origin parameter.
run_discord_bot.py reuses mirror_turn() below to post the resulting GM/NPC reply back into Discord
too, so there's exactly one code path that ever talks to a Discord webhook, used by both directions.
"""

import requests

import db

_WEBHOOK_TIMEOUT = 8
_DISCORD_CONTENT_LIMIT = 2000


def _display_for_turn(turn):
    """(content, username) to post for one turn row, or (None, None) if this kind has nothing worth
    mirroring into Discord (e.g. a silent bookkeeping row, if one is ever added later)."""
    kind = turn["kind"]
    if kind in ("message", "action"):
        verb = "says" if kind == "message" else "does"
        name = turn.get("character_name") or "A player"
        return f"*{name} {verb}:* {turn['player_input']}", name
    if kind == "gm_reply":
        return turn["narration"], "Game Master"
    if kind == "npc_reply":
        npc = next((n for n in db.npcs_at_subspace(turn["subspace_id"]) if n["key"] == turn["responder"]), None)
        return turn["narration"], (npc["name"] if npc else turn["responder"])
    if kind in ("storm_out", "thread_concluded", "level_up"):
        text = turn.get("narration") or turn.get("player_input") or ""
        return text, "Game Master"
    return None, None


def mirror_turn(turn):
    """Posts a turn into Discord if its location is linked; a silent no-op otherwise (unlinked
    location, a subspace whose thread the sync loop hasn't created yet, or the HTTP call itself
    failing) - a Discord-mirroring hiccup should never surface as an error to whoever already got
    their real response, since the turn itself is already recorded and returned by the time this
    runs. Safe to call for every turn regardless of origin; callers are responsible for only calling
    it for turns that should be mirrored (see multiplayer.py's origin parameter)."""
    location = db.get_location(turn["location_id"])
    if not location or not location.get("discord_webhook_id"):
        return
    content, username = _display_for_turn(turn)
    if not content:
        return

    default = db.get_default_subspace(turn["location_id"])
    is_default = default is not None and default["id"] == turn["subspace_id"]
    thread_id = None
    if not is_default:
        subspace = db.get_subspace(turn["subspace_id"])
        thread_id = (subspace or {}).get("discord_thread_id")
        if not thread_id:
            return  # dynamic subspace whose thread the sync loop hasn't created yet - nothing to post into

    url = f"https://discord.com/api/webhooks/{location['discord_webhook_id']}/{location['discord_webhook_token']}"
    params = {"thread_id": thread_id} if thread_id else {}
    payload = {"content": content[:_DISCORD_CONTENT_LIMIT]}
    if username:
        payload["username"] = username[:80]
    try:
        requests.post(url, params=params, json=payload, timeout=_WEBHOOK_TIMEOUT)
    except requests.RequestException:
        pass
