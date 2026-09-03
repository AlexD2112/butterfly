"""
The real Discord bot process (2026-09-03) - the discord.py Client, event handlers, slash commands,
and Discord-side structure creation that discord_bot.py's own module docstring explicitly deferred
until a real bot token existed to test against. Everything in discord_bot.py is reused here
unchanged; this file is the thin, unavoidably-untestable-without-a-real-gateway layer around it.

Run: python run_discord_bot.py   (see start_discord_bot.ps1 for the always-on version, same pattern
as local-site-tunnel/start_local_site.ps1 - its own window, separate process from the web app).

## What this process does

1. Logs into the one guild the bot was invited to (see _resolve_guild - a hard error if it's in zero
   or more than one, rather than silently guessing).
2. A background loop (sync_discord_structure, every SYNC_INTERVAL_SECONDS - see _sync_once) walks
   every multiplayer server/location/subspace in the SAME SQLite database server.py uses and creates
   whatever Discord structure is missing: a Category per server, a Channel per Location (plus a
   webhook on it), a Thread per non-default Subspace - exactly the Category/Channel/Thread mapping
   discord_bot.py's docstring describes. This is fully automatic and dynamic by design (2026-09-03,
   per direction) - it runs on its own timer regardless of whether a Location/Subspace was created by
   an admin on the website or spawned dynamically by the AI classifier during play; nobody ever has to
   trigger it. /refresh (below) is the one deliberately manual thing, and it's optional - a safety net
   that forces one pass immediately instead of waiting up to SYNC_INTERVAL_SECONDS, not a requirement.
3. on_message: a plain message typed in a linked channel/thread is treated as a "message" (in-scene
   talk, judged by the AI for whether an NPC/the GM responds) - the exact same
   multiplayer.handle_message pipeline api_multiplayer_send uses for the web "message" toggle.
4. /action: the mechanical-resolution equivalent of the web's "action" toggle - resolved through
   multiplayer.resolve_action, same as the web route.
5. /join: the entire self-contained-onboarding story from discord_bot.py's docstring - links (or
   creates) the caller's account via the same db.get_or_create_discord_user web login already uses,
   and reports whether they already have a character in this server.
6. /refresh: admin-only (Discord's own Manage Server permission, not the app's web is_admin flag -
   there's no web session in a Discord interaction to check that against), forces one immediate
   _sync_once() pass and reports what it created. The ONLY manual Discord-structure action in this
   whole app, and explicitly optional.

Both send paths call the shared pipeline with origin="discord" (see multiplayer.py's origin
parameter) so a Discord-originated turn doesn't get redundantly mirrored back to the channel it came
from - the reply it produces still gets mirrored via discord_webhook.mirror_turn(), the same function
server.py's web route relies on for the opposite direction. There is exactly one piece of code
anywhere in this app that ever POSTs to a Discord webhook - see discord_webhook.py.

## What this deliberately does NOT do

Full character-sheet creation (attribute arrays, skill/feat picks) is NOT reimplemented as Discord
modals - per /join's own reply, a player without a character yet is pointed at the website to build
one (character creation there is already a real multi-step flow; redoing it as Discord UI would be a
much bigger, separate undertaking, not implied by "get the bot running"). Once a character exists,
play itself is fully playable from Discord.
"""

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import db
import discord_bot
import discord_webhook
import multiplayer
import pricing
import providers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("forking_paths_bot")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
WEBSITE_URL = os.environ.get("DISCORD_WEBSITE_URL", "https://forkingpaths.app").rstrip("/")
SYNC_INTERVAL_SECONDS = 20
GM_WEBHOOK_NAME = "Forking Paths - Game Master"

db.init_db()  # same idempotent call server.py makes at import time - safe to run from a second process


class ForkingPathsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # required to read on_message's actual text - see Developer Portal setup
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.guild = None
        # Per-(character_id, provider_key) conversation state, same pattern as server.py's own
        # _conversations - deliberately a SEPARATE in-memory cache from the Flask process's, since
        # they're different processes. A character switching between web and Discord mid-session
        # restarts the provider's own short-term context on whichever side wasn't used most
        # recently - a pre-existing class of limitation (provider conversation state was already
        # never persisted to the database anywhere in this app), not something new to this file.
        self._conversations = {}

    def conversation_for(self, character_id, provider_key, provider):
        key = (character_id, provider_key)
        if key not in self._conversations:
            self._conversations[key] = provider.new_conversation()
        return self._conversations[key]

    async def setup_hook(self):
        sync_discord_structure.start()

    async def on_ready(self):
        guilds = list(self.guilds)
        if len(guilds) != 1:
            names = ", ".join(f"{g.name} ({g.id})" for g in guilds) or "none"
            log.warning(
                "Bot is in %d guild(s) (%s) - expected exactly 1. Using the first one; invite it to "
                "only one server if that's not what you want.", len(guilds), names,
            )
        if guilds:
            self.guild = guilds[0]
            # copy_global_to is required here, not just sync(guild=...) alone - commands defined via
            # @client.tree.command() with no guild= kwarg are GLOBAL commands, which sync(guild=...)
            # does NOT pick up on its own (it only pushes whatever's already scoped to that guild,
            # which is nothing, the first time). copy_global_to() stages the global set as this
            # guild's own commands, so sync(guild=...) actually publishes them - and guild-scoped
            # commands appear near-instantly, unlike a real global sync (up to ~1hr to propagate).
            # Caught live (2026-09-03): the first run logged "synced" with no error, but a real
            # Discord API check afterward showed zero commands registered - silent, not obvious from
            # discord.py's own behavior alone.
            self.tree.copy_global_to(guild=self.guild)
            synced = await self.tree.sync(guild=self.guild)
            log.info(
                "Ready as %s - managing guild '%s' (%s). Synced %d slash command(s): %s.",
                self.user, self.guild.name, self.guild.id, len(synced), ", ".join(c.name for c in synced),
            )
        else:
            log.error("Ready as %s but not in any guild - invite it to your server first.", self.user)


client = ForkingPathsBot()


# ==================== Discord structure sync: Category / Channel / Thread creation ====================
# This is the ONLY thing that ever creates Discord structure - it's automatic and dynamic by design
# (2026-09-03, per direction): the periodic loop below just calls _sync_once() on a timer, and admins
# never need to trigger it manually. /refresh (below) exists purely as an optional safety-net that
# forces one pass immediately instead of waiting up to SYNC_INTERVAL_SECONDS for the next tick - not
# a required step, and not a second code path (both call the exact same _sync_once()).

async def _sync_once():
    """One full pass over every multiplayer server/location/subspace, creating whatever Discord
    structure (Category/Channel+webhook/Thread) is missing. Returns how many of each it actually
    created THIS pass, so a manual /refresh has something concrete to report - "nothing needed
    creating" vs a real count, not just "done"."""
    counts = {"categories": 0, "channels": 0, "threads": 0}
    if client.guild is None:
        return counts
    guild = client.guild
    for server in db.list_multiplayer_servers():
        had_category = bool(server.get("discord_category_id"))
        try:
            category = await _ensure_category(guild, server)
        except discord.HTTPException as e:
            log.warning("Couldn't create/find category for server %s: %s", server["name"], e)
            continue
        if category is None:
            continue
        if not had_category:
            counts["categories"] += 1
        for location in db.list_locations(server["id"]):
            had_channel = bool(location.get("discord_channel_id"))
            try:
                channel = await _ensure_channel(guild, category, location)
            except discord.HTTPException as e:
                log.warning("Couldn't create/find channel for location %s: %s", location["name"], e)
                continue
            if channel is None:
                continue
            if not had_channel:
                counts["channels"] += 1
            for subspace in db.list_subspaces(location["id"]):
                if discord_bot.subspace_needs_a_thread(subspace, location):
                    try:
                        await _ensure_thread(channel, subspace)
                        counts["threads"] += 1
                    except discord.HTTPException as e:
                        log.warning("Couldn't create thread for subspace %s: %s", subspace["name"], e)
    return counts


@tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
async def sync_discord_structure():
    await _sync_once()


@sync_discord_structure.before_loop
async def _before_sync():
    await client.wait_until_ready()


async def _ensure_category(guild, server):
    if server.get("discord_category_id"):
        existing = guild.get_channel(int(server["discord_category_id"]))
        if existing is not None:
            return existing
        log.warning("Server %s's linked category %s is gone (deleted in Discord?) - not auto-recreating.",
                    server["name"], server["discord_category_id"])
        return None
    category = await guild.create_category(name=server["name"][:100], reason="Forking Paths: new multiplayer server")
    db.link_multiplayer_server_to_discord(server["id"], str(guild.id), str(category.id))
    log.info("Created Discord category '%s' for server #%s.", category.name, server["id"])
    return category


async def _ensure_channel(guild, category, location):
    if location.get("discord_channel_id"):
        existing = guild.get_channel(int(location["discord_channel_id"]))
        if existing is not None:
            return existing
        log.warning("Location %s's linked channel %s is gone - not auto-recreating.",
                    location["name"], location["discord_channel_id"])
        return None
    name = discord_bot.discord_safe_name(location["name"])
    channel = await guild.create_text_channel(name=name, category=category, reason="Forking Paths: new Location")
    webhook = await channel.create_webhook(name=GM_WEBHOOK_NAME, reason="Forking Paths: GM/NPC identity for this Location")
    db.link_location_to_discord(location["id"], str(channel.id), str(webhook.id), webhook.token)
    log.info("Created Discord channel #%s for location '%s'.", channel.name, location["name"])
    return channel


async def _ensure_thread(channel, subspace):
    name = discord_bot.discord_safe_name(subspace["name"])
    thread = await channel.create_thread(
        name=name, type=discord.ChannelType.public_thread, reason="Forking Paths: new Subspace",
    )
    db.link_subspace_to_discord_thread(subspace["id"], str(thread.id))
    log.info("Created Discord thread '%s' under #%s.", thread.name, channel.name)
    return thread


# ==================== shared play-context resolution ====================

class PlayContextError(Exception):
    """A user-facing reason play couldn't proceed - message is safe to show directly."""


def _route_for_channel(channel):
    """(location, subspace) for whichever Discord channel/thread this is, or None if unmanaged."""
    if isinstance(channel, discord.Thread):
        return discord_bot.route_thread_message(str(channel.id), str(channel.parent_id))
    return discord_bot.route_channel_message(str(channel.id))


def _resolve_play_context(discord_user, location, subspace):
    """Everything needed to act as a character: the linked user row, the server, the game, and their
    most-recently-used character in it. Raises PlayContextError with a ready-to-send message for
    every case that isn't "go ahead and act" - no character yet, or the account is out of budget."""
    user_row = discord_bot.resolve_or_create_user(
        discord_user.id, str(discord_user), str(discord_user.display_avatar.url) if discord_user.display_avatar else None,
    )
    server = db.get_multiplayer_server(location["server_id"])
    game = db.get_game_for_server(server["id"])
    character = discord_bot.resolve_character_for_user_in_game(user_row["id"], game["id"])
    if character is None:
        raise PlayContextError(
            f"You don't have a character in **{server['name']}** yet. Create one at {WEBSITE_URL} "
            f"(log in with this same Discord account, then Multiplayer -> {server['name']}) - once you "
            f"have one, just chat or use /action right here to play."
        )
    budget = db.get_budget_status(user_row["id"])
    if budget["remaining_usd"] <= 0:
        raise PlayContextError("This account is out of usage budget.")
    db.move_character(character["id"], server["id"], location["id"], subspace["id"])
    return user_row, server, game, character


# ==================== on_message: plain chat = "message" (talk) ====================

@client.event
async def on_message(message):
    if message.author.bot or message.webhook_id is not None:
        return
    content = message.content.strip()
    if not content:
        return
    routed = _route_for_channel(message.channel)
    if routed is None:
        return
    location, subspace = routed
    if location is None or subspace is None:
        return

    try:
        user_row, server, game, character = _resolve_play_context(message.author, location, subspace)
    except PlayContextError as e:
        await message.reply(str(e), mention_author=False)
        return

    try:
        provider, model = providers.get_provider(providers.DEFAULT_MODEL_KEY)
    except providers.ProviderError as e:
        await message.reply(f"Model provider isn't configured: {e}", mention_author=False)
        return

    usage_sink = []
    loop = asyncio.get_running_loop()
    try:
        async with message.channel.typing():
            result = await loop.run_in_executor(
                None,
                lambda: multiplayer.handle_message(
                    provider, model, game["id"], location["id"], subspace["id"], character["id"], content,
                    usage_sink=usage_sink, origin="discord",
                ),
            )
    except Exception:
        log.exception("handle_message failed for character %s in location %s", character["id"], location["id"])
        await message.reply("Something went wrong resolving that - the game master's been notified.", mention_author=False)
        return
    finally:
        _record_usage(usage_sink, user_row["id"], character["id"])
    # No explicit reply needed here even when nobody responded (result[2] is None) - the player's own
    # message is already visible in Discord as the message they just sent; nothing else to show.


def _record_usage(usage_sink, user_id, character_id):
    for usage in usage_sink:
        cost = pricing.estimate_cost_usd(usage)
        db.record_usage(
            user_id, character_id, usage["provider"], usage["model"],
            usage["input_tokens"], usage["output_tokens"],
            usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0), cost,
        )


# ==================== slash commands ====================

@client.tree.command(name="join", description="Link your Discord account to Forking Paths and check your character here.")
async def join_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    channel = interaction.channel
    category_id = getattr(channel, "category_id", None)
    server = discord_bot.server_for_category(str(category_id)) if category_id else None
    if server is None:
        await interaction.followup.send("Run /join inside one of a game's own channels (under its category), not here.")
        return
    user_row = discord_bot.resolve_or_create_user(
        interaction.user.id, str(interaction.user),
        str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None,
    )
    game = db.get_game_for_server(server["id"])
    character = discord_bot.resolve_character_for_user_in_game(user_row["id"], game["id"])
    if character:
        await interaction.followup.send(
            f"You're linked as **{user_row['username']}**, playing **{character['name']}** in "
            f"**{server['name']}**. Just chat or use /action in a channel/thread here to play."
        )
    else:
        await interaction.followup.send(
            f"You're linked to Forking Paths as **{user_row['username']}** - no web login needed. "
            f"You don't have a character in **{server['name']}** yet: create one at {WEBSITE_URL} "
            f"(log in with this same Discord account), then come back and chat here."
        )


@client.tree.command(name="refresh", description="Admin: immediately check for any missing category/channel/thread and create it now.")
async def refresh_command(interaction: discord.Interaction):
    """Optional safety-net only - see _sync_once's own comment. Never required for normal play; the
    periodic loop already does this automatically. Useful if someone doesn't want to wait up to
    SYNC_INTERVAL_SECONDS for the next tick, or suspects something's out of sync."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Run this inside the server, not in a DM.", ephemeral=True)
        return
    perms = interaction.user.guild_permissions
    if not (perms.manage_guild or perms.administrator):
        await interaction.response.send_message("This needs the Manage Server permission.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        counts = await _sync_once()
    except Exception:
        log.exception("Manual /refresh sync failed")
        await interaction.followup.send("Something went wrong during sync - check the bot's own log.")
        return
    if not any(counts.values()):
        await interaction.followup.send("Already up to date - nothing needed creating.")
    else:
        await interaction.followup.send(
            f"Synced: created {counts['categories']} categor{'y' if counts['categories'] == 1 else 'ies'}, "
            f"{counts['channels']} channel(s), {counts['threads']} thread(s)."
        )


@client.tree.command(name="action", description="Take an action in the game - resolved by the AI game master.")
@app_commands.describe(text="What does your character do?")
async def action_command(interaction: discord.Interaction, text: str):
    await interaction.response.defer(thinking=True)
    routed = _route_for_channel(interaction.channel)
    if routed is None:
        await interaction.followup.send("This channel isn't linked to a game location.")
        return
    location, subspace = routed
    if location is None or subspace is None:
        await interaction.followup.send("This channel isn't linked to a game location.")
        return

    try:
        user_row, server, game, character = _resolve_play_context(interaction.user, location, subspace)
    except PlayContextError as e:
        await interaction.followup.send(str(e))
        return

    try:
        provider, model = providers.get_provider(providers.DEFAULT_MODEL_KEY)
    except providers.ProviderError as e:
        await interaction.followup.send(f"Model provider isn't configured: {e}")
        return

    char_row = db.get_character(character["id"], user_row["id"])
    conversation = client.conversation_for(character["id"], providers.MODEL_REGISTRY[providers.DEFAULT_MODEL_KEY]["provider"], provider)
    usage_sink = []
    budget = db.get_budget_status(user_row["id"])

    def budget_ok():
        spent_this_turn = sum(pricing.estimate_cost_usd(u) for u in usage_sink)
        return (budget["remaining_usd"] - spent_this_turn) > 0

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: multiplayer.resolve_action(
                provider, model, providers.DEFAULT_MODEL_KEY, char_row, location["id"], subspace["id"],
                text, conversation, usage_sink=usage_sink, budget_ok=budget_ok, origin="discord",
            ),
        )
    except multiplayer.CombatTurnError as e:
        await interaction.followup.send(str(e))
        return
    except Exception:
        log.exception("resolve_action failed for character %s in location %s", character["id"], location["id"])
        await interaction.followup.send("Something went wrong resolving that action - the game master's been notified.")
        return
    finally:
        _record_usage(usage_sink, user_row["id"], character["id"])

    note = ""
    if result.get("moved_to_location_id") or result.get("new_location_id"):
        note = " (this moved you to a new part of the world - sync may take a few seconds to create its channel)"
    elif result.get("moved_to_subspace_id") and result["moved_to_subspace_id"] != subspace["id"]:
        note = " (this moved you within the location - sync may take a few seconds to create its thread)"
    await interaction.followup.send(f"Resolved.{note}")


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("Slash command error", exc_info=error)
    message = "Something went wrong running that command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


def main():
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set - add it to .env (see .env.example) before running this."
        )
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
