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
   whatever Discord structure is missing: a Category per server, a #rumors channel+webhook per server
   (2026-09-03), a Channel per Location (plus a webhook on it), a Thread per non-default Subspace -
   exactly the Category/Channel/Thread mapping discord_bot.py's docstring describes. This is fully
   automatic and dynamic by design (2026-09-03, per direction) - it runs on its own timer regardless of
   whether a Location/Subspace was created by an admin on the website or spawned dynamically by the AI
   classifier during play; nobody ever has to trigger it. /refresh (below) is the one deliberately
   manual thing, and it's optional - a safety net that forces one pass immediately instead of waiting
   up to SYNC_INTERVAL_SECONDS, not a requirement.
3. on_message: a plain message typed in a linked channel/thread is dynamically classified by a cheap
   AI call (multiplayer.classify_message_or_action, 2026-09-03, per direction) as "message" (in-scene
   talk - multiplayer.handle_message, same as the web "message" toggle) or "action" (mechanical -
   multiplayer.resolve_action, same as the web "action" toggle), then routed accordingly - see
   _run_message/_run_action below, the one shared execution path per kind used by BOTH this classified
   routing and the explicit commands below it. Discord has no visible Message/Action toggle the way
   the website does, so a plain-typed message needs classifying instead of always defaulting to
   "message" - the earlier, silent gap (see NOTES.md): a typed move got talk-only handling, no
   mechanical resolution, no dynamic-space detection, with no error and no visible sign anything was
   wrong.
4. /action and /message: the explicit, always-honored equivalents of the web's Action/Message toggle -
   bypass classification entirely, since a slash-command interaction never reaches on_message at all
   (Discord dispatches interactions and message events separately).
5. /join: the entire self-contained-onboarding story from discord_bot.py's docstring - links (or
   creates) the caller's account via the same db.get_or_create_discord_user web login already uses,
   and reports whether they already have a character in this server.
6. /sheet (2026-09-03): a compact embed of the caller's own character - attributes, notable skills,
   feats, liabilities, and whether a level-up is pending. Not a full replica of the web sheet panel
   (resources/relationships/enmity/commitments/canon/divergence-log stay web-only) - a quick reference,
   not a second UI to maintain in lockstep with the real one.
7. /levelup (2026-09-03): a real interactive equivalent of the web's level-up picker, via chained
   Discord Select menus (skill/feat/attribute, then the specific choice) - same eligibility rules and
   the same system.apply_level_up call the web route uses, via multiplayer.level_up_options_for_character
   so the two can't silently drift apart. One simplification, not hidden: attribute points are
   1-per-selected-attribute (up to 3), not "put multiple points on one attribute" like the web allows.
8. /refresh: admin-only (Discord's own Manage Server permission, not the app's web is_admin flag -
   there's no web session in a Discord interaction to check that against), forces one immediate
   _sync_once() pass and reports what it created. The ONLY manual Discord-structure action in this
   whole app, and explicitly optional.

Both send paths call the shared pipeline with origin="discord" (see multiplayer.py's origin
parameter) so a Discord-originated turn doesn't get redundantly mirrored back to the channel it came
from - the reply it produces still gets mirrored via discord_webhook.mirror_turn(), the same function
server.py's web route relies on for the opposite direction. There is exactly one piece of code
anywhere in this app that ever POSTs to a Discord webhook - see discord_webhook.py.

Every turn either send path produces also gets pushed to the website's own live view via
_notify_web_live (2026-09-03, a real fix, not a hypothetical one - found live, via the actual bot):
this process is a completely separate Python program from server.py/waitress, so
multiplayer.handle_message/resolve_action's own live.publish_subspace_event calls, made from THIS
process, only ever reach THIS process's own empty in-memory SSE subscriber set - never the real web
viewers connected to the actual server.py process. _notify_web_live asks that other process (over a
plain internal HTTP call, shared-secret gated - see /api/internal/notify_turn in server.py) to
republish the turn on this process's behalf, since it's the one actually holding the real
subscriber queues.

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
import threading
from pathlib import Path

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import db
import discord_bot
import discord_webhook
import engine
import live
import multiplayer
import pricing
import providers
from systems._shared import core as shared_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("forking_paths_bot")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
WEBSITE_URL = os.environ.get("DISCORD_WEBSITE_URL", "https://forkingpaths.app").rstrip("/")
SYNC_INTERVAL_SECONDS = 20
GM_WEBHOOK_NAME = "Forking Paths - Game Master"

# 2026-09-03: true when this module is running INSIDE the same process as server.py (Render, via
# server.py's own DISCORD_BOT_EMBEDDED=1 check - see start_in_background_thread below), as opposed to
# its own separate OS process (local dev, via start_local_site.ps1 launching this file directly).
# Changes exactly one thing - see _notify_web_live - everything else about this module behaves
# identically either way.
EMBEDDED = os.environ.get("DISCORD_BOT_EMBEDDED") == "1"

# See server.py's /api/internal/notify_turn for why this exists: when NOT embedded, this process's
# own live.publish_subspace_event calls (inside multiplayer.handle_message/resolve_action) reach only
# THIS process's empty in-memory SSE subscriber set, never the real web viewers connected to the
# actual server.py/waitress process - a separate OS process even though both share the same SQLite
# file. INTERNAL_API_BASE defaults to localhost since local dev always runs both on the same machine
# (see start_local_site.ps1) - override via env if that's ever no longer true. Unused when EMBEDDED.
INTERNAL_API_BASE = os.environ.get("INTERNAL_API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
INTERNAL_NOTIFY_SECRET = os.environ.get("INTERNAL_NOTIFY_SECRET", "").strip()

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
    counts = {"categories": 0, "channels": 0, "threads": 0, "rumors_channels": 0}
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
        had_rumors_channel = bool(server.get("discord_rumors_channel_id"))
        try:
            await _ensure_rumors_channel(guild, category, server)
        except discord.HTTPException as e:
            log.warning("Couldn't create/find rumors channel for server %s: %s", server["name"], e)
        else:
            if not had_rumors_channel:
                counts["rumors_channels"] += 1
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


RUMORS_WEBHOOK_NAME = "Forking Paths - News"
RUMORS_CHANNEL_NAME = "rumors"


async def _ensure_rumors_channel(guild, category, server):
    """A dedicated #rumors channel per server (2026-09-03), sibling to the per-Location channels -
    every compiled rumor broadcast posts here via discord_webhook.post_rumor_broadcast, called from
    scheduler.py's tick in the WEB process, not this one - same "just a webhook, no gateway needed"
    reasoning as Location channels."""
    if server.get("discord_rumors_channel_id"):
        existing = guild.get_channel(int(server["discord_rumors_channel_id"]))
        if existing is not None:
            return existing
        log.warning("Server %s's linked rumors channel %s is gone - not auto-recreating.",
                    server["name"], server["discord_rumors_channel_id"])
        return None
    channel = await guild.create_text_channel(
        name=RUMORS_CHANNEL_NAME, category=category, reason="Forking Paths: server rumors/news feed",
    )
    webhook = await channel.create_webhook(name=RUMORS_WEBHOOK_NAME, reason="Forking Paths: rumor broadcast identity")
    db.link_server_rumors_channel_to_discord(server["id"], str(channel.id), str(webhook.id), webhook.token)
    log.info("Created Discord rumors channel #%s for server #%s.", channel.name, server["id"])
    return channel


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


def _resolve_own_character_by_category(discord_user, channel):
    """Lighter-weight resolution for /sheet and /levelup - neither needs a specific location/subspace
    (a character sheet isn't tied to where you're standing), just which SERVER this channel/thread's
    category belongs to, same as /join's own lookup. No budget check either - looking at your own
    sheet or applying an already-earned level-up doesn't cost anything, unlike a real AI-resolved
    turn."""
    category_id = getattr(channel, "category_id", None)
    server = discord_bot.server_for_category(str(category_id)) if category_id else None
    if server is None:
        raise PlayContextError("Run this inside one of a game's own channels (under its category), not here.")
    user_row = discord_bot.resolve_or_create_user(
        discord_user.id, str(discord_user), str(discord_user.display_avatar.url) if discord_user.display_avatar else None,
    )
    game = db.get_game_for_server(server["id"])
    character = discord_bot.resolve_character_for_user_in_game(user_row["id"], game["id"])
    if character is None:
        raise PlayContextError(
            f"You don't have a character in **{server['name']}** yet. Create one at {WEBSITE_URL} "
            f"(log in with this same Discord account, then Multiplayer -> {server['name']})."
        )
    return user_row, server, game, character


def _record_usage(usage_sink, user_id, character_id):
    for usage in usage_sink:
        cost = pricing.estimate_cost_usd(usage)
        db.record_usage(
            user_id, character_id, usage["provider"], usage["model"],
            usage["input_tokens"], usage["output_tokens"],
            usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0), cost,
        )


def _notify_web_live(turn_id):
    """Real fix for the "doesn't live-update on the website" gap (2026-09-03, confirmed live, not
    hypothetical) - see server.py's /api/internal/notify_turn for the full explanation. Every turn
    this bot produces already gets recorded to the shared database correctly; this is what makes it
    ALSO push live to anyone already watching the website via SSE, instead of only showing up on
    their next reload. Best-effort and silent on failure - a missed live-update ping should never
    surface as an error for a Discord action that already succeeded and was already recorded.

    EMBEDDED (2026-09-03): when this module runs inside the SAME process as server.py, the HTTP
    round-trip isn't just unnecessary, it's not even reliably correct - Render assigns $PORT
    dynamically, so INTERNAL_API_BASE's localhost:5000 default wouldn't reliably match it. Skip the
    network hop entirely and publish directly into the exact same live.py module both this thread and
    Flask's own request-handling code have imported - a genuinely shared in-memory subscriber set now,
    not two processes pretending to share one over HTTP."""
    if not turn_id:
        return
    if EMBEDDED:
        turn = db.get_turn_by_id(turn_id)
        if turn:
            live.publish_subspace_event(turn["subspace_id"], turn)
        return
    if not INTERNAL_NOTIFY_SECRET:
        return
    try:
        requests.post(
            f"{INTERNAL_API_BASE}/api/internal/notify_turn", timeout=5,
            json={"turn_id": turn_id}, headers={"X-Internal-Secret": INTERNAL_NOTIFY_SECRET},
        )
    except requests.RequestException:
        pass


# ==================== shared action/message execution - one real code path per kind, used by
# on_message's auto-classified routing AND the explicit /action and /message commands, so all three
# entry points behave identically (2026-09-03) ====================

async def _run_action(user_row, character, location, subspace, content):
    """Runs a real mechanical action through resolve_action. Returns (result_dict, error_message) -
    exactly one is None. Handles usage billing and the website live-notify bridge itself."""
    try:
        provider, model = providers.get_provider(providers.DEFAULT_MODEL_KEY)
    except providers.ProviderError as e:
        return None, f"Model provider isn't configured: {e}"

    char_row = db.get_character(character["id"], user_row["id"])
    conversation = client.conversation_for(character["id"], providers.MODEL_REGISTRY[providers.DEFAULT_MODEL_KEY]["provider"], provider)
    usage_sink = []
    budget = db.get_budget_status(user_row["id"])

    def budget_ok():
        spent_this_turn = sum(pricing.estimate_cost_usd(u) for u in usage_sink)
        return (budget["remaining_usd"] - spent_this_turn) > 0

    loop = asyncio.get_running_loop()
    result = None
    error = None
    try:
        result = await loop.run_in_executor(
            None,
            lambda: multiplayer.resolve_action(
                provider, model, providers.DEFAULT_MODEL_KEY, char_row, location["id"], subspace["id"],
                content, conversation, usage_sink=usage_sink, budget_ok=budget_ok, origin="discord",
            ),
        )
    except multiplayer.CombatTurnError as e:
        error = str(e)
    except Exception:
        log.exception("resolve_action failed for character %s in location %s", character["id"], location["id"])
        error = "Something went wrong resolving that action - the game master's been notified."
    finally:
        _record_usage(usage_sink, user_row["id"], character["id"])

    if result is not None:
        _notify_web_live(result.get("turn_id"))
        _notify_web_live(result.get("reply_turn_id"))
    return result, error


async def _run_message(user_row, game, character, location, subspace, content):
    """Runs a real 'message' (talk) turn through handle_message. Returns (result_tuple,
    error_message) - exactly one is None. Same shared-execution reasoning as _run_action."""
    try:
        provider, model = providers.get_provider(providers.DEFAULT_MODEL_KEY)
    except providers.ProviderError as e:
        return None, f"Model provider isn't configured: {e}"

    usage_sink = []
    loop = asyncio.get_running_loop()
    result = None
    error = None
    try:
        result = await loop.run_in_executor(
            None,
            lambda: multiplayer.handle_message(
                provider, model, game["id"], location["id"], subspace["id"], character["id"], content,
                usage_sink=usage_sink, origin="discord",
            ),
        )
    except Exception:
        log.exception("handle_message failed for character %s in location %s", character["id"], location["id"])
        error = "Something went wrong resolving that - the game master's been notified."
    finally:
        _record_usage(usage_sink, user_row["id"], character["id"])

    if result is not None:
        message_turn_id, reply_turn_id, _responder = result
        _notify_web_live(message_turn_id)
        _notify_web_live(reply_turn_id)
    return result, error


# ==================== on_message: dynamically classified as "message" (talk) or "action"
# (mechanical) via multiplayer.classify_message_or_action (2026-09-03, per direction) - Discord has no
# explicit Message/Action toggle the way the website does, so a plain-typed message needs classifying
# instead of always defaulting to "message" (the earlier, silent gap - see NOTES.md: a typed move got
# talk-only handling, no mechanical resolution, no dynamic-space detection). /action and /message
# (below) bypass this entirely - a slash-command interaction never reaches on_message at all, so an
# explicit command is never reclassified, only genuinely unlabeled text is. ====================

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
        judge_provider, judge_model = providers.get_provider(multiplayer.JUDGE_MODEL_KEY)
    except providers.ProviderError:
        try:
            judge_provider, judge_model = providers.get_provider(providers.DEFAULT_MODEL_KEY)
        except providers.ProviderError as e:
            await message.reply(f"Model provider isn't configured: {e}", mention_author=False)
            return

    loop = asyncio.get_running_loop()
    classify_usage = []
    kind = await loop.run_in_executor(
        None,
        lambda: multiplayer.classify_message_or_action(judge_provider, judge_model, content, usage_sink=classify_usage),
    )
    _record_usage(classify_usage, user_row["id"], character["id"])

    async with message.channel.typing():
        if kind == "action":
            result, error = await _run_action(user_row, character, location, subspace, content)
        else:
            result, error = await _run_message(user_row, game, character, location, subspace, content)

    if error:
        await message.reply(error, mention_author=False)
    # No explicit reply needed on success (either kind) - the player's own message is already visible
    # in Discord as the message they just sent; the GM/mechanical reply posts separately via the
    # webhook mirror, and a plain "message" that nobody responds to has nothing else to show either.


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


def _build_sheet_embed(char_row):
    """A compact Discord embed for /sheet - deliberately NOT a full replica of the web sheet panel
    (resources, relationships, enmity, commitments, canon events, divergence log all stay web-only) -
    just what's useful as a quick reference from Discord: attributes, skills worth knowing about
    (Expertise/Proficiency - Untrained is skipped, it's just the attribute modifier alone), feats,
    liabilities, and whether a level-up is waiting."""
    c = char_row["state"]["character"]
    xp_needed = 100 + (c["level"] - 1) * 50
    embed = discord.Embed(
        title=c["name"],
        description=f"Level {c['level']} · Proficiency +{c['proficiency_bonus']} · {c['xp']}/{xp_needed} XP",
        color=0x7A9B5F,
    )
    attrs = c.get("attributes") or {}
    attr_lines = "\n".join(f"**{name}** {score} ({shared_core.attribute_modifier(score):+d})" for name, score in attrs.items())
    embed.add_field(name="Attributes", value=attr_lines or "-", inline=False)

    skills = c.get("skills") or {}
    expertise = sorted(name.replace("_", " ") for name, s in skills.items() if s.get("tier") == 2)
    proficiency = sorted(name.replace("_", " ") for name, s in skills.items() if s.get("tier") == 1)
    embed.add_field(name="Expertise", value=", ".join(expertise) or "None yet", inline=True)
    embed.add_field(name="Proficiency", value=", ".join(proficiency) or "None yet", inline=True)

    feats = c.get("feats") or []
    embed.add_field(name="Feats", value=", ".join(f["name"] for f in feats) or "None yet", inline=False)

    liabilities = c.get("liabilities") or []
    embed.add_field(name="Liabilities", value=", ".join(l["name"] for l in liabilities) or "None", inline=False)

    if c.get("level_up_pending"):
        embed.add_field(name="\U0001F389 Level-up available", value="Use /levelup to apply it.", inline=False)
    return embed


@client.tree.command(name="sheet", description="View your own character sheet for this server.")
async def sheet_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        user_row, server, game, character = _resolve_own_character_by_category(interaction.user, interaction.channel)
    except PlayContextError as e:
        await interaction.followup.send(str(e))
        return
    char_row = db.get_character(character["id"], user_row["id"])
    await interaction.followup.send(embed=_build_sheet_embed(char_row))


# ==================== /levelup: a real, interactive Discord equivalent of the web's level-up picker
# (2026-09-03) - three Select menus (skill/feat/attribute), same eligibility rules and same
# system.apply_level_up call the web route uses, via multiplayer.level_up_options_for_character so
# neither side can silently drift from the other. One simplification from the web picker, called out
# here rather than hidden: attribute points are 1-per-selected-attribute (pick up to 3 attributes),
# not "put up to 3 points on one attribute" - a Discord Select can't express "how many" per option
# without a second picker per attribute, which felt like more UI than this was worth for now. ====================

def _describe_level_up_change(c):
    kind = c.get("kind")
    if kind == "skill_upgrade":
        return f"{c['skill'].replace('_', ' ')}: {c['before']} → {c['after']}"
    if kind == "skill_upgrade_rejected":
        return f"{c['skill'].replace('_', ' ')} not upgraded ({c['reason']})"
    if kind == "feat_gained":
        return f"gained {c['name']}"
    if kind == "feat_rejected":
        return f"feat not gained ({c['reason']})"
    if kind == "attribute_improvement":
        return f"{c['attribute']}: {c['before']} → {c['after']}"
    if kind == "level_up_rejected":
        return c.get("reason", "no change")
    return kind or "no change"


async def _apply_level_up_and_reply(interaction, character_id, user_id, body):
    char_row = db.get_character(character_id, user_id)
    state = char_row["state"]
    system = engine.get_system(char_row["system_key"])
    changes = system.apply_level_up(state, body)
    db.save_character_state(character_id, user_id, state)
    applied = not state["character"]["level_up_pending"]

    if applied:
        presence = db.get_character_presence(character_id)
        if presence is not None:
            turn_id = db.record_subspace_message(
                char_row["game_id"], presence["location_id"], presence["subspace_id"], character_id, "level_up",
                f"{state['character']['name']} reached level {state['character']['level']}.",
            )
            turn = db.get_turn_by_id(turn_id)
            discord_webhook.mirror_turn(turn)
            _notify_web_live(turn_id)

    summary = "; ".join(_describe_level_up_change(c) for c in changes)
    prefix = "Applied" if applied else "Not applied"
    await interaction.response.edit_message(content=f"{prefix}: {summary}", view=None)


class SkillUpgradeSelect(discord.ui.Select):
    def __init__(self, character_id, user_id, eligible_skills):
        options = [
            discord.SelectOption(
                label=s["skill"].replace("_", " ")[:100], value=s["skill"],
                description=f"{s['attribute']} · currently {shared_core.TIER_NAMES[s['tier']]}"[:100],
            )
            for s in eligible_skills[:25]
        ]
        super().__init__(placeholder="Pick up to 2 skills to upgrade one tier...", options=options,
                          min_values=1, max_values=min(2, len(options)))
        self.character_id = character_id
        self.user_id = user_id
        self.by_name = {s["skill"]: s for s in eligible_skills}

    async def callback(self, interaction: discord.Interaction):
        upgrades = [{"skill": name, "attribute": self.by_name[name]["attribute"]} for name in self.values]
        await _apply_level_up_and_reply(interaction, self.character_id, self.user_id, {"choice": "skill", "skill_upgrades": upgrades})


class FeatSelect(discord.ui.Select):
    def __init__(self, character_id, user_id, feats):
        options = [
            discord.SelectOption(label=f["name"][:100], value=f["id"], description=(f.get("description") or "")[:100])
            for f in feats[:25]
        ]
        super().__init__(placeholder="Pick a feat...", options=options, min_values=1, max_values=1)
        self.character_id = character_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        await _apply_level_up_and_reply(interaction, self.character_id, self.user_id, {"choice": "feat", "feat_id": self.values[0]})


class AttributeSelect(discord.ui.Select):
    def __init__(self, character_id, user_id, attributes):
        options = [discord.SelectOption(label=name, value=name, description=f"currently {score}") for name, score in attributes.items()]
        super().__init__(placeholder="Pick up to 3 attributes (+1 each)...", options=options,
                          min_values=1, max_values=min(3, len(options)))
        self.character_id = character_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        points = {name: 1 for name in self.values}
        await _apply_level_up_and_reply(interaction, self.character_id, self.user_id, {"choice": "attribute", "attribute_points": points})


class LevelUpTypeSelect(discord.ui.Select):
    def __init__(self, character_id, user_id, options_data):
        options = [
            discord.SelectOption(label="Upgrade a Skill", value="skill", description="Raise up to 2 skills one tier each"),
            discord.SelectOption(label="Learn a Feat", value="feat", description="Gain one eligible feat"),
            discord.SelectOption(label="Increase Attributes", value="attribute", description="+1 to up to 3 attributes"),
        ]
        super().__init__(placeholder="Choose what to spend this level-up on...", options=options, min_values=1, max_values=1)
        self.character_id = character_id
        self.user_id = user_id
        self.options_data = options_data

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "skill":
            eligible = [s for s in self.options_data["skills"] if s["tier"] < 2]
            if not eligible:
                await interaction.response.edit_message(content="No eligible skills to upgrade (everything's already at Expertise).", view=None)
                return
            select = SkillUpgradeSelect(self.character_id, self.user_id, eligible)
        elif choice == "feat":
            if not self.options_data["feats"]:
                await interaction.response.edit_message(content="No eligible feats right now.", view=None)
                return
            select = FeatSelect(self.character_id, self.user_id, self.options_data["feats"])
        else:
            select = AttributeSelect(self.character_id, self.user_id, self.options_data["attributes"])
        next_view = discord.ui.View(timeout=120)
        next_view.add_item(select)
        await interaction.response.edit_message(content="Now choose specifically:", view=next_view)


@client.tree.command(name="levelup", description="Apply a pending level-up for your character here.")
async def levelup_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        user_row, server, game, character = _resolve_own_character_by_category(interaction.user, interaction.channel)
    except PlayContextError as e:
        await interaction.followup.send(str(e))
        return
    char_row = db.get_character(character["id"], user_row["id"])
    if not char_row["state"]["character"].get("level_up_pending"):
        await interaction.followup.send("No level-up is pending for your character right now.")
        return
    options_data = multiplayer.level_up_options_for_character(char_row)
    view = discord.ui.View(timeout=120)
    view.add_item(LevelUpTypeSelect(character["id"], user_row["id"], options_data))
    await interaction.followup.send("Level-up available - choose what to spend it on:", view=view)


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
            f"{counts['channels']} channel(s), {counts['threads']} thread(s), "
            f"{counts['rumors_channels']} rumors channel(s)."
        )


@client.tree.command(name="action", description="Take an action in the game - resolved by the AI game master. Bypasses auto-classification.")
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

    result, error = await _run_action(user_row, character, location, subspace, text)
    if error:
        await interaction.followup.send(error)
        return

    note = ""
    if result.get("moved_to_location_id") or result.get("new_location_id"):
        note = " (this moved you to a new part of the world - sync may take a few seconds to create its channel)"
    elif result.get("moved_to_subspace_id") and result["moved_to_subspace_id"] != subspace["id"]:
        note = " (this moved you within the location - sync may take a few seconds to create its thread)"
    await interaction.followup.send(f"Resolved.{note}")


@client.tree.command(name="message", description="Say/do something purely in-scene - no dice, no mechanical resolution. Bypasses auto-classification.")
@app_commands.describe(text="What does your character say or do socially?")
async def message_command(interaction: discord.Interaction, text: str):
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

    result, error = await _run_message(user_row, game, character, location, subspace, text)
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send("Sent.")


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


def start_in_background_thread():
    """Starts the bot inside a background daemon thread of WHATEVER process calls this - used when
    this process ALSO runs the web app (Render, via server.py's DISCORD_BOT_EMBEDDED=1 check) instead
    of as its own separate process (local dev, via start_local_site.ps1 launching this file directly
    through main() below). Never run both at once against the same DISCORD_BOT_TOKEN - two
    simultaneous gateway connections on one bot token is a real, confusing bug (duplicate slash-
    command registration, every message/action potentially handled twice), not just wasteful.
    Silently no-ops (logs a warning, doesn't crash the caller) if no token is configured - unlike
    main()'s hard SystemExit, since the web app must keep running fine either way."""
    if not TOKEN:
        log.warning("DISCORD_BOT_TOKEN not set - the embedded Discord bot will not start.")
        return
    thread = threading.Thread(target=lambda: client.run(TOKEN, log_handler=None), daemon=True, name="discord-bot")
    thread.start()
    log.info("Discord bot starting in a background thread (embedded mode).")


def main():
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set - add it to .env (see .env.example) before running this."
        )
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
