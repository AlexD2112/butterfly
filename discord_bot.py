"""
Pure routing/identity logic for the Discord bot (2026-09-03) - deliberately discord.py-agnostic:
every function here takes and returns plain values (ids, dicts), never a discord.py object, so this
whole module is unit-testable without a real Discord connection (see
tests/test_discord_bot_routing.py). The actual discord.py Client, event handlers, and slash commands
live in run_discord_bot.py, which imports and calls these - that module can't be meaningfully unit
tested (it needs a real gateway connection), so the goal here is to keep as much real logic as
possible on THIS side of that line.

## Structure

Category = one multiplayer server. Channel = one Location. Thread = one non-default Subspace,
nested inside its Location's channel. A Location's default/"Main" subspace IS that Location's own
channel - it never gets its own thread. This maps the app's real three-level hierarchy
(Server -> Location -> Subspace) onto Discord's actual two-level one (Category -> Channel, with
Threads as a third level nested under Channel) more cleanly than a channel-per-subspace design
would, since Discord has no Category-within-Category nesting.

Mapping is stored on the existing rows (multiplayer_servers.discord_guild_id/discord_category_id,
locations.discord_channel_id/discord_webhook_id/discord_webhook_token,
subspaces.discord_thread_id) - see db.py's "Discord bot linking" section. A server/location/subspace
with no Discord link is just a server/location/subspace with NULL columns; nothing about the rest of
the app changes or requires a Discord presence to keep working.

## Identity - no separate account system

A Discord user's account IS the same `users` row Discord OAuth web login already creates - same
discord_id, same schema-level starting budget (`users.budget_usd`'s DEFAULT, see db.py). Onboarding
entirely from Discord (no requirement to log into the website first, per direction) just means
calling db.get_or_create_discord_user with the interaction's own author info - no OAuth token
exchange needed, since the bot already receives trustworthy identity data from Discord itself with
every interaction. Whichever path (Discord first, or web first) a person uses, they end up as one
unified account, not two.

## Sync

Discord is a genuine second live client of the same game, not a separate one, per direction. A
message/action sent from Discord goes through the exact same multiplayer.handle_message/
resolve_action pipeline server.py's routes already use - run_discord_bot.py calls those directly,
this module has no parallel implementation of them. A message/action sent from the WEB gets mirrored
into the matching Discord channel/thread via webhook, hooked wherever live.publish_subspace_event
already fires (see run_discord_bot.py).
"""

import re

import db

# Discord channel/thread names: lowercase, hyphens instead of spaces/most punctuation, 100 char cap
# (Discord's own limit) - this app's Location/Subspace names are free text ("The Smolny Institute",
# "Trotsky's Office"), which Discord's API would otherwise reject outright.
_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def discord_safe_name(name):
    """Turns a free-text Location/Subspace name into a valid Discord channel/thread name. Never
    empty - falls back to "space" if sanitizing strips everything (e.g. a name that's pure emoji
    or punctuation), since Discord rejects an empty channel name."""
    slug = _NAME_SANITIZE_RE.sub("-", name.lower()).strip("-")
    return (slug or "space")[:100]


def resolve_or_create_user(discord_id, username, avatar_url=None):
    """The entire self-contained Discord onboarding, in one call - reuses the exact function web
    OAuth login already calls. See this module's own docstring for why that's sufficient: same
    discord_id always resolves to the same users row and the same starting budget, regardless of
    which path (Discord or web) reaches it first."""
    return db.get_or_create_discord_user(str(discord_id), username, avatar_url)


def location_for_channel(channel_id):
    """The Location a Discord channel is linked to, or None if it isn't one this app manages."""
    return db.get_location_by_discord_channel(str(channel_id))


def subspace_for_thread(thread_id):
    """The Subspace a Discord thread is linked to, or None if it isn't one this app manages."""
    return db.get_subspace_by_discord_thread(str(thread_id))


def server_for_category(category_id):
    return db.get_multiplayer_server_by_discord_category(str(category_id))


def route_channel_message(channel_id):
    """A plain message/action posted directly in a Location's channel (not a thread) always targets
    that Location's default Subspace - the channel-level "main" scene. Returns (location, subspace)
    or None if this channel isn't linked to anything."""
    location = location_for_channel(channel_id)
    if location is None:
        return None
    subspace = db.get_default_subspace(location["id"])
    return (location, subspace)


def route_thread_message(thread_id, parent_channel_id):
    """A message/action posted in a thread targets that thread's own Subspace if the thread itself
    is linked (the common case); if not (a thread the bot didn't create - e.g. an admin made a plain
    Discord thread by hand), falls back to the parent channel's Location and its default Subspace,
    same as posting directly in the channel would. Returns (location, subspace) or None if neither
    the thread nor its parent channel is linked to anything."""
    subspace = subspace_for_thread(thread_id)
    if subspace is not None:
        location = db.get_location(subspace["location_id"])
        return (location, subspace)
    return route_channel_message(parent_channel_id)


def resolve_character_for_user_in_game(user_id, game_id):
    """Which character this Discord-mapped user should act as in a given game. Returns the row for
    their most-recently-updated character there if they have one or more, else None (the caller
    should prompt them to create one - see run_discord_bot.py's /join handling - same as the web
    lobby prompting a first-time visitor)."""
    characters = [c for c in db.get_game_characters(game_id) if c["user_id"] == user_id]
    if not characters:
        return None
    characters.sort(key=lambda c: c["updated_at"], reverse=True)
    return characters[0]


def subspace_needs_a_thread(subspace, location):
    """True if this Subspace should have its own Discord thread (it's not the Location's default
    one) and doesn't have one linked yet - the two conditions run_discord_bot.py checks before
    creating a thread for a dynamically-spawned Subspace."""
    default = db.get_default_subspace(location["id"])
    is_default = default is not None and default["id"] == subspace["id"]
    return (not is_default) and not subspace.get("discord_thread_id")
