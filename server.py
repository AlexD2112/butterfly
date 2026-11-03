"""
Butterfly webapp entry point.

Run: python server.py   (then open http://127.0.0.1:5000)

API keys and secrets can come from real environment variables, or from a .env file (see
.env.example) - load_dotenv() below picks up .env automatically if present, without overriding any
real env var that's already set. Never put actual secrets in a .py file - .env (and butterfly.db)
are gitignored specifically so they can live on disk without ever being committed.

Every visitor gets a persistent local account automatically (a signed session cookie mapped to a
row in butterfly.db) so character creation and saving work without requiring login. "Login with
Discord" switches the session to a Discord-linked account instead - see discord_oauth.py. The two
identities are NOT merged automatically; characters stay with whichever account created them.
"""

import json
import datetime
import os
import queue
import secrets
import threading
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, session, redirect

load_dotenv(Path(__file__).parent / ".env")

import db
import dynamics
import geodata
import discord_oauth
import discord_webhook
import engine
import live
import multiplayer
import pricing
import providers
import scheduler

db.init_db()

# Real scheduled background ticks for multiplayer progression - off by default (ENABLE_TICK_SCHEDULER
# unset), so importing this module (every test does) never silently starts a background thread. See
# scheduler.py's module docstring for why this needs to be a real background job, not a
# lazy/request-triggered check.
if scheduler.is_enabled():
    scheduler.start_scheduler()

# The Discord bot, embedded in this SAME process (2026-09-03, DISCORD_BOT_EMBEDDED=1 - see
# run_discord_bot.py's own EMBEDDED flag and NOTES.md's migration entry). Only used when this
# process is meant to BE the bot too (Render, one service, one disk) - local dev keeps running the
# bot as its own separate process via start_local_site.ps1/run_discord_bot.py directly, unaffected;
# never set this env var locally, or the same DISCORD_BOT_TOKEN would open two simultaneous gateway
# connections. Same off-by-default reasoning as the scheduler above - importing this module in tests
# must never try to connect to Discord.
if os.environ.get("DISCORD_BOT_EMBEDDED") == "1":
    import run_discord_bot
    run_discord_bot.start_in_background_thread()

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
# NOTE: if FLASK_SECRET_KEY isn't set, a random one is generated per process start, which means
# sessions (and therefore "who am I") reset on every restart/redeploy. Fine for local dev; set a
# real FLASK_SECRET_KEY in production (e.g. Render's environment variables) so logins persist.

# SameSite=Lax blocks the session cookie from being attached to cross-site POSTs (a malicious page
# elsewhere can't ride a victim's session into /api/character/<id>/turn or similar) - this matters
# more than usual here because api_character_turn's request.get_json(force=True) parses the body as
# JSON regardless of Content-Type, which otherwise removes a common implicit CSRF speed bump (a
# cross-site <form> can't normally set Content-Type: application/json without a CORS preflight this
# app doesn't allow, but force=True means it doesn't need to - a text/plain "simple request" works).
# HttpOnly is Flask's default already; set explicitly so it can't be silently lost by a future config
# change. SECURE (cookie only sent over HTTPS) now defaults to True as of the 2026-08-21 hosting move
# - previously left unset because there was no reliable "are we in production" signal available (see
# NOTES.md's earlier entry on this), but every real deployment path now terminates in HTTPS regardless
# of how this process itself is reached (Render's own HTTPS, or a Cloudflare tunnel's HTTPS edge in
# front of this machine - see start_local_site.ps1), so it's safe to default on. Plain `python
# server.py` local dev over http://127.0.0.1 still works: Chrome/Firefox both treat 127.0.0.1 as a
# secure context and send Secure cookies over plain HTTP to it specifically. Override with
# SESSION_COOKIE_SECURE=0 if you ever need to test against a non-localhost plain-HTTP address.
# Logins last 30 days (2026-11-02). Flask's default session cookie dies when the browser closes,
# which silently dropped a Discord-logged-in player back onto the shared local profile - whose games
# list doesn't contain their games - every time a phone browser was closed. That looked exactly like
# "my history is missing."
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"

# Multiplayer actions still keep an in-memory thread per (character, provider). Single-player no
# longer does (2026-11-02): see _single_player_conversation.
_conversations = {}

# Council Mode (council.py) is opt-in as of 2026-11-02: set COUNCIL_MODE=1 to turn it back on. It ran
# every turn - a router, a second candidate, 3-6 judges, a chairman and a voice editor, 7-11 model
# calls before the player saw anything - and the chairman's synthesis of several drafts was itself
# a big part of why narration read as committee-written. One well-prompted pass is faster, cheaper
# and sounds more like a person.
COUNCIL_MODE = os.environ.get("COUNCIL_MODE", "0") == "1"

# Map place lookups (geodata.py) hit Wikidata from the server. GEOCODE=0 turns them off (tests do).
GEOCODE_ENABLED = os.environ.get("GEOCODE", "1") == "1"


def _conversation_for(character_id, provider_key, provider):
    key = (character_id, provider_key)
    if key not in _conversations:
        _conversations[key] = provider.new_conversation()
    return _conversations[key]


def _single_player_conversation(character_id, provider):
    """The GM's memory of recent play, rebuilt from the saved transcript on every turn.

    It used to be an in-memory thread that lived only as long as the server process - every deploy,
    restart or model switch wiped it, so a resumed game's GM had forgotten everything but a few
    400-character summaries. It also grew without bound (every tool call and result, forever), so
    long games got steadily slower and pricier. Rebuilding from db.get_turns fixes both: the window is
    the last engine.MEMORY_TURNS turns, verbatim, plus a one-line record of what the rules decided."""
    return engine.rebuild_conversation(provider, db.get_turns(character_id))


# Serializes turns per-account so two concurrent requests for the same user (a double-click, two
# open tabs, two devices on one Discord account) can't both pass the budget check before either has
# recorded its spend and jointly overshoot it - see NOTES.md for what this does and doesn't cover.
# Grows by one Lock per distinct user_id ever seen and is never pruned; negligible at this app's
# scale (each Lock is a few dozen bytes and account counts are small), not worth a cleanup pass now.
_turn_locks = {}
_turn_locks_guard = threading.Lock()


def _turn_lock_for(user_id):
    with _turn_locks_guard:
        if user_id not in _turn_locks:
            _turn_locks[user_id] = threading.Lock()
        return _turn_locks[user_id]


def current_user():
    user_id = session.get("user_id")
    if user_id:
        user = db.get_user_by_id(user_id)
        if user:
            if not session.permanent:
                session.permanent = True  # upgrade sessions created before the 30-day change
            return user
    user = db.get_or_create_local_user()
    session["user_id"] = user["id"]
    return user


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/me")
def api_me():
    user = current_user()
    # Budget is tied to a real Discord identity, not the shared local/anonymous profile (see
    # api_character_turn) - report a zeroed, non-actionable budget for local users rather than the
    # shared local row's real (and now practically meaningless) numbers.
    if user["is_local"]:
        budget = {"budget_usd": 0.0, "spent_usd": 0.0, "remaining_usd": 0.0, "discord_login_required": True}
    else:
        budget = db.get_budget_status(user["id"])
    return jsonify(
        {
            "id": user["id"],
            "username": user["username"],
            "avatar_url": user["avatar_url"],
            "is_local": bool(user["is_local"]),
            "is_admin": bool(user["is_admin"]),
            "discord_login_available": discord_oauth.is_configured(),
            "budget": budget,
        }
    )


# Deliberately a simple shared string, not a real secret - per explicit instruction, no admin-gated
# functionality carries real security stakes yet. This IS checked into source control - if this repo
# is ever public, treat the code as public knowledge too, not a real access control.
ADMIN_UNLOCK_CODE = "ElfbertWasRight"


@app.route("/api/admin/unlock", methods=["POST"])
def api_admin_unlock():
    user = current_user()
    body = request.get_json(force=True)
    code = body.get("code") or ""
    if code != ADMIN_UNLOCK_CODE:
        return jsonify({"ok": False, "error": "Incorrect code."}), 400
    db.set_is_admin(user["id"], True)
    return jsonify({"ok": True, "is_admin": True})


@app.route("/auth/discord/login")
def auth_discord_login():
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    try:
        url = discord_oauth.authorize_url(state)
    except discord_oauth.DiscordOAuthError as e:
        return jsonify({"error": str(e)}), 400
    return redirect(url)


@app.route("/auth/discord/callback")
def auth_discord_callback():
    error = request.args.get("error")
    if error:
        return redirect(f"/?auth_error={quote(error)}")
    state = request.args.get("state")
    session_state = session.get("oauth_state")
    # secrets.compare_digest over plain != - the state token itself isn't secret (it's a one-time
    # CSRF nonce, not a credential), so a timing side-channel here isn't a realistic attack path, but
    # constant-time comparison is the correct default for any token check and costs nothing.
    if not state or not session_state or not secrets.compare_digest(state, session_state):
        return redirect("/?auth_error=state_mismatch")
    code = request.args.get("code")
    if not code:
        return redirect("/?auth_error=missing_code")
    try:
        token = discord_oauth.exchange_code(code)
        identity = discord_oauth.fetch_identity(token)
    except discord_oauth.DiscordOAuthError as e:
        return redirect(f"/?auth_error={quote(str(e))}")
    user = db.get_or_create_discord_user(identity["id"], identity["username"], identity["avatar_url"])
    session.permanent = True
    session["user_id"] = user["id"]
    session.pop("oauth_state", None)
    return redirect("/")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/models")
def api_models():
    # Only lists entries in ALLOWED_MODEL_KEYS while that's set (see its docstring) - sonnet/haiku/
    # opus are simply absent from the response right now, not shown-but-disabled, per the "hide
    # them" direction. Reverting the lock server-side (set it to None) makes every registered model
    # show up again automatically, no frontend change needed.
    visible_keys = providers.ALLOWED_MODEL_KEYS if providers.ALLOWED_MODEL_KEYS is not None else providers.MODEL_REGISTRY.keys()
    models = []
    for key in visible_keys:
        entry = providers.MODEL_REGISTRY[key]
        configured = providers.provider_available(entry["provider"])
        if key == "local-ollama":
            # "Configured" (env vars set) isn't the same as "actually reachable right now" - the
            # tunnel/laptop can be down independent of whether Render has the env vars set at all.
            # This is the health-check hide/disable path from the feature's design: a player should
            # never see this option as selectable and then have it fail on click.
            available = configured and providers.ollama_tunnel_is_healthy()
            reason = None if available else ("not configured" if not configured else "currently offline")
        else:
            available = configured
            reason = None if available else "missing API key"
        models.append({"key": key, "label": entry["label"], "available": available, "unavailable_reason": reason})
    return jsonify({"models": models, "default": providers.DEFAULT_MODEL_KEY})


@app.route("/api/settings")
def api_settings():
    out = [
        {
            "key": key,
            "label": info["label"],
            "blurb": info["blurb"],
            "system": info["system"],
            "system_label": engine.SYSTEM_REGISTRY[info["system"]]["label"],
            "premade_character_name": info["premade_character_name"],
            "sort_year": info["sort_year"],
        }
        for key, info in engine.SETTING_REGISTRY.items()
    ]
    return jsonify({"settings": out})


@app.route("/api/character_creation_schema/<system_key>")
def api_character_creation_schema(system_key):
    if system_key not in engine.SYSTEM_REGISTRY:
        return jsonify({"error": f"Unknown system: {system_key}"}), 404
    system = engine.get_system(system_key)
    from systems._shared import core

    starting_feats = {k: v for k, v in system.FEAT_CATALOG.items() if v.get("min_level", 1) <= 1}
    return jsonify(
        {
            "attributes": system.ATTRIBUTES,
            "standard_array": core.STANDARD_ATTRIBUTE_ARRAY,
            "skill_point_budget": core.SKILL_POINT_BUDGET,
            "liability_bonus_skill_points": core.LIABILITY_BONUS_SKILL_POINTS,
            "max_starting_expertise_skills": core.MAX_STARTING_EXPERTISE_SKILLS,
            "skills": system.SKILLS,
            "starting_feats": starting_feats,
            "liabilities": system.LIABILITY_CATALOG,
        }
    )


@app.route("/api/settings/<setting_key>/map")
def api_setting_map(setting_key):
    """The game screen's map (2026-11-02): only places whose positions geodata.py verified against
    Wikidata, the available base layers, and `viable` - whether the map is good enough to offer at
    all. The first request for a setting starts the lookups in the background; until they finish,
    `resolving` is true and the frontend keeps the Map button hidden and checks back."""
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 404
    data = geodata.setting_map_data(engine.setting_dir(setting_key))
    if data is None:
        return jsonify({"viable": False, "reason": "No map for this setting."}), 404
    status = geodata.map_status(setting_key, data)
    if status["resolving"] and GEOCODE_ENABLED:
        geodata.start_background_resolve(setting_key, data)
    return jsonify(status)


@app.route("/api/characters/<setting_key>")
def api_characters(setting_key):
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 404
    user = current_user()
    rows = [c for c in db.list_characters(user["id"]) if c["setting_key"] == setting_key]
    return jsonify({"characters": rows, "premade_name": engine.SETTING_REGISTRY[setting_key]["premade_character_name"]})


@app.route("/api/characters")
def api_my_characters():
    """Every single-player game this account has, across every setting - not filtered to one
    setting_key like the route above. Backs the new landing nav's top-level "Your Games" list
    (2026-09-03): pick Singleplayer and see everything at once, period-tagged, rather than choosing a
    setting first and only then finding out what you'd already started there. db.list_characters
    itself is scoped to mode='single' games, so a multiplayer character never shows up here."""
    user = current_user()
    return jsonify({"characters": db.list_characters(user["id"])})


@app.route("/api/characters", methods=["POST"])
def api_create_character():
    user = current_user()
    body = request.get_json(force=True)
    setting_key = body.get("setting_key")
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 400
    system_key = engine.SETTING_REGISTRY[setting_key]["system"]

    # Optional: attach this character to an existing multiplayer server's shared game instead of
    # creating a fresh single-player one - see db.create_character's game_id param.
    game_id = None
    multiplayer_server_id = body.get("multiplayer_server_id")
    if multiplayer_server_id is not None:
        mp_game = db.get_game_for_server(multiplayer_server_id)
        if mp_game is None:
            return jsonify({"error": f"Unknown multiplayer_server_id: {multiplayer_server_id}"}), 400
        game_id = mp_game["id"]

    if body.get("type") == "premade":
        state = engine.load_premade_character_state(setting_key)
        state.pop("_is_new", None)
        name = state["character"]["name"]
        # Claim-lock (2026-08-23): the setting's flagship is a single, specific real figure, same as
        # a generated historical character (see api_create_historical_character) - two players in the
        # same multiplayer server shouldn't both end up playing Trotsky. Single-player is unaffected
        # (game_id is None there, and find_character_by_name_in_game is never meaningful for it).
        if game_id is not None and db.find_character_by_name_in_game(game_id, name) is not None:
            return jsonify({"error": f'"{name}" is already claimed by another player in this server.'}), 409
        character_id = db.create_character(user["id"], setting_key, system_key, name, state, is_premade=True, game_id=game_id)
        return jsonify({"character_id": character_id, "state": engine.build_state_digest(state), "is_new": True})

    if body.get("type") == "custom":
        system = engine.get_system(system_key)
        character, errors = system.create_character(
            body.get("name", ""),
            body.get("role", ""),
            body.get("attribute_assignment") or {},
            body.get("skill_tiers") or {},
            body.get("feat_id"),
            body.get("liability_ids") or [],
        )
        if errors:
            return jsonify({"error": "Character creation failed.", "details": errors}), 400

        fresh_setting = engine.load_premade_character_state(setting_key)
        state = {k: v for k, v in fresh_setting.items() if k != "character" and not k.startswith("_")}
        state["character"] = character
        state["setting_pack"] = setting_key
        state["system"] = system_key
        character_id = db.create_character(user["id"], setting_key, system_key, character["name"], state, is_premade=False, game_id=game_id)
        return jsonify({"character_id": character_id, "state": engine.build_state_digest(state), "is_new": True})

    return jsonify({"error": "body.type must be 'premade' or 'custom'"}), 400


@app.route("/api/characters/historical", methods=["POST"])
def api_create_historical_character():
    """Generates a real historical figure (not the setting's flagship) as a playable character via
    AI - unlike premade/custom creation, this makes a real model call, so it's gated the same way a
    turn is (Discord login + budget), not free. See engine.generate_historical_character for the
    actual eligibility/generation logic and its real refusal path - a "not eligible" response here is
    an expected, successful outcome, not an error, and still costs whatever the judge call cost
    (recorded either way, in the `finally` block below)."""
    user = current_user()
    if user["is_local"]:
        return (
            jsonify(
                {
                    "error": "Log in with Discord to generate a historical character - local/anonymous "
                    "profiles can still play the premade or a custom character.",
                    "discord_login_required": True,
                }
            ),
            403,
        )

    body = request.get_json(force=True)
    setting_key = body.get("setting_key")
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 400
    system_key = engine.SETTING_REGISTRY[setting_key]["system"]
    figure_name = (body.get("figure_name") or "").strip()
    if not figure_name:
        return jsonify({"error": "figure_name is required."}), 400
    model_key = body.get("model_key") or providers.DEFAULT_MODEL_KEY
    if model_key not in providers.MODEL_REGISTRY:
        return jsonify({"error": f"Unknown model key: {model_key}"}), 400
    if providers.ALLOWED_MODEL_KEYS is not None and model_key not in providers.ALLOWED_MODEL_KEYS:
        return jsonify({"error": f"Model '{model_key}' isn't currently selectable."}), 400

    game_id = None
    multiplayer_server_id = body.get("multiplayer_server_id")
    if multiplayer_server_id is not None:
        mp_game = db.get_game_for_server(multiplayer_server_id)
        if mp_game is None:
            return jsonify({"error": f"Unknown multiplayer_server_id: {multiplayer_server_id}"}), 400
        game_id = mp_game["id"]
        # Cheap check before spending an AI call at all - re-checked again after generation too,
        # below, since the figure's actual stored name could in principle differ in casing/whitespace
        # from the raw request, and a second player could claim it in the gap between the two checks.
        if db.find_character_by_name_in_game(game_id, figure_name) is not None:
            return jsonify({"error": f'"{figure_name}" is already claimed by another player in this server.'}), 409

    budget_before = db.get_budget_status(user["id"])
    if budget_before["remaining_usd"] <= 0:
        return jsonify({"error": "Out of usage budget for this account.", "budget": budget_before}), 402

    try:
        provider, model = providers.get_provider(model_key)
    except providers.ProviderError as e:
        return jsonify({"error": str(e)}), 400

    usage_sink = []
    try:
        result = engine.generate_historical_character(
            provider, model, setting_key, system_key, figure_name, usage_sink=usage_sink,
        )
    finally:
        for usage in usage_sink:
            cost = pricing.estimate_cost_usd(usage)
            db.record_usage(
                user["id"], None, usage["provider"], usage["model"],
                usage["input_tokens"], usage["output_tokens"],
                usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0), cost,
            )

    budget_after = db.get_budget_status(user["id"])
    if not result["eligible"]:
        return jsonify({"eligible": False, "reason": result["reason"], "budget": budget_after})

    character = result["character"]
    if game_id is not None and db.find_character_by_name_in_game(game_id, character["name"]) is not None:
        return jsonify({"error": f'"{character["name"]}" was just claimed by another player.', "budget": budget_after}), 409

    fresh_setting = engine.load_premade_character_state(setting_key)
    state = {k: v for k, v in fresh_setting.items() if k != "character" and not k.startswith("_")}
    state["character"] = character
    state["setting_pack"] = setting_key
    state["system"] = system_key
    character_id = db.create_character(user["id"], setting_key, system_key, character["name"], state, is_premade=False, game_id=game_id)
    return jsonify({
        "eligible": True, "character_id": character_id, "state": engine.build_state_digest(state),
        "is_new": True, "budget": budget_after,
    })


@app.route("/api/character/<int:character_id>/state")
def api_character_state(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    # Fill in the clock/world for display before the first turn has run (a brand-new game, or a save
    # from before dynamics.json existed) - display only, nothing is saved here.
    state = row["state"]
    dyn = dynamics.load(row["setting_key"])
    if dyn:
        dynamics.ensure_initialized(state, dyn)
        dynamics.update_calendar(state, dyn)
    return jsonify({"state": engine.build_state_digest(state), "setting_key": row["setting_key"], "system_key": row["system_key"]})


@app.route("/api/character/<int:character_id>/level_up_options")
def api_character_level_up_options(character_id):
    """What a pending level-up can actually be spent on right now - eligible skills (with current
    tier, so the frontend can grey out ones already at Expertise), eligible feats (owned/min_level/
    prerequisite already filtered server-side, not left to the frontend to get right), and current
    attribute values. Single-player and multiplayer both use this (character rows aren't otherwise
    partitioned by context - see /state above)."""
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    character = row["state"]["character"]
    if not character.get("level_up_pending"):
        return jsonify({"pending": False})

    options = multiplayer.level_up_options_for_character(row)
    return jsonify({
        "pending": True, "level": character["level"],
        "skills": options["skills"], "feats": options["feats"], "attributes": options["attributes"],
    })


@app.route("/api/character/<int:character_id>/apply_level_up", methods=["POST"])
def api_character_apply_level_up(character_id):
    """Applies a level-up choice DIRECTLY - system.apply_level_up, no AI call, no narration - the
    same validated logic engine.run_turn's tool loop already uses for a conversational
    propose_level_up, just reached from a UI click instead of the model interpreting chat text (see
    NOTES.md for why: the old path had no code-level guarantee the model's choice matched what the
    player actually asked for)."""
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    state = row["state"]
    if not state["character"].get("level_up_pending"):
        return jsonify({"error": "No level-up is pending."}), 400

    body = request.get_json(force=True) or {}
    system = engine.get_system(row["system_key"])
    changes = system.apply_level_up(state, body)
    db.save_character_state(character_id, user["id"], state)
    applied = not state["character"]["level_up_pending"]

    # Announce a completed level-up in the shared multiplayer log if this character has a presence
    # there - level reached only, never which skill/feat/attribute was picked (per direction).
    # Single-player characters have no presence row, so this is a silent no-op for them.
    if applied:
        presence = db.get_character_presence(character_id)
        if presence is not None:
            turn_id = db.record_subspace_message(
                row["game_id"], presence["location_id"], presence["subspace_id"], character_id, "level_up",
                f"{state['character']['name']} reached level {state['character']['level']}.",
            )
            turn = db.get_turn_by_id(turn_id)
            live.publish_subspace_event(presence["subspace_id"], turn)
            # Real pre-existing gap, closed alongside Discord's own /levelup (2026-09-03): this route
            # records the announcement via db.record_subspace_message directly rather than through
            # multiplayer.handle_message/resolve_action, so it never went through those functions'
            # discord_webhook.mirror_turn call either - a level-up applied on the web never showed up
            # in Discord at all, even though discord_webhook._display_for_turn already had a "level_up"
            # branch ready for exactly this.
            discord_webhook.mirror_turn(turn)

    return jsonify({"changes": changes, "applied": applied, "state": engine.build_state_digest(state)})


@app.route("/api/character/<int:character_id>/opening_scene")
def api_character_opening_scene(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    return jsonify({"text": engine.load_text(row["setting_key"], "opening_scene.md")})


@app.route("/api/character/<int:character_id>/turns")
def api_character_turns(character_id):
    """The full recorded history for a single-player character - db.get_turns() already existed and
    already had everything needed for this (player_input/narration/debug_events_json per turn,
    oldest first), but until now the only thing that ever called it was view_transcript.py, a local
    CLI script. This is what lets the frontend replay a resumed character's past turns into the log
    instead of showing a blank screen with just a status line - see NOTES.md."""
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    return jsonify({"turns": db.get_turns(character_id)})


@app.route("/api/character/<int:character_id>/turn", methods=["POST"])
def api_character_turn(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404

    # Budget is tied to a real Discord identity (per 2026-08-20 direction change - see NOTES.md).
    # get_or_create_local_user() returns ONE single shared row for every anonymous visitor (a fixed
    # sentinel discord_id under a UNIQUE constraint - there is no per-browser local identity at all),
    # so a per-account budget can only mean anything against an identity that's actually unique per
    # person, which today only Discord provides. Local/anonymous play still works for browsing
    # settings and creating characters - only the actual paid action is gated.
    if user["is_local"]:
        return (
            jsonify(
                {
                    "error": "Log in with Discord to send AI turns - local/anonymous profiles can browse "
                    "and create characters, but can't spend AI usage.",
                    "discord_login_required": True,
                }
            ),
            403,
        )

    # Non-blocking: a second concurrent request for the same account fails fast with 429 instead of
    # queueing behind a possibly-slow LLM call, or - the actual bug this prevents - both requests
    # reading the same "budget not yet exhausted" snapshot and jointly overspending it. This only
    # serializes within one process; see NOTES.md for the multi-worker-process caveat.
    lock = _turn_lock_for(user["id"])
    if not lock.acquire(blocking=False):
        return jsonify({"error": "Another turn is already in progress for this account."}), 429

    try:
        budget_before = db.get_budget_status(user["id"])
        if budget_before["remaining_usd"] <= 0:
            return jsonify({"error": "Out of usage budget for this account.", "budget": budget_before}), 402

        body = request.get_json(force=True)
        user_input = (body.get("input") or "").strip()
        model_key = body.get("model_key") or providers.DEFAULT_MODEL_KEY
        if model_key not in providers.MODEL_REGISTRY:
            return jsonify({"error": f"Unknown model key: {model_key}"}), 400
        # ALLOWED_MODEL_KEYS, when set, restricts which registered models are actually selectable
        # right now (see that constant's docstring) - this is the real enforcement point; the
        # frontend only showing the allowed subset in the picker is a UX nicety, not the guard
        # itself, so a direct API call requesting e.g. "sonnet" still gets rejected here.
        if providers.ALLOWED_MODEL_KEYS is not None and model_key not in providers.ALLOWED_MODEL_KEYS:
            return jsonify({"error": f"Model '{model_key}' isn't currently selectable."}), 400
        if not user_input:
            return jsonify({"error": "Empty input."}), 400

        # Fail fast with a clear, distinguishable status before even attempting the (slow) call, if
        # the local model's tunnel is known to be down - avoids the player waiting out a long
        # timeout just to learn what a 3-second health check already knows. local_model_offline is a
        # dedicated flag (not just relying on the generic error text) so the frontend can show the
        # same clear state either here or if the tunnel drops mid-request (see the except block
        # below) rather than a raw error message.
        if model_key == "local-ollama" and not providers.ollama_tunnel_is_healthy():
            return (
                jsonify(
                    {
                        "error": "The local model is currently offline - the host machine or its tunnel isn't reachable right now.",
                        "local_model_offline": True,
                    }
                ),
                503,
            )

        try:
            provider, model = providers.get_provider(model_key)
        except providers.ProviderError as e:
            return jsonify({"error": str(e)}), 400

        state = row["state"]
        provider_key = providers.MODEL_REGISTRY[model_key]["provider"]
        conversation = _single_player_conversation(character_id, provider)

        # usage_sink accumulates one entry per API call made during this turn (a single turn can make
        # several, via the request_check/propose_state_update tool loop). budget_ok recomputes spend
        # from that in-progress list plus the balance already committed to the DB, so a runaway
        # multi-call turn gets cut off as soon as it would exceed budget - not just on the next turn.
        usage_sink = []

        def budget_ok():
            spent_this_turn = sum(pricing.estimate_cost_usd(u) for u in usage_sink)
            return (budget_before["remaining_usd"] - spent_this_turn) > 0

        # Captured before engine.run_turn mutates state (it increments state["turn_number"] itself,
        # the same way campaign_log_summary's own entries do) - this is the turn number actually
        # being played this call, for db.record_turn below.
        turn_number_played = state.get("turn_number")

        try:
            # Council Mode runs on every turn as of 2026-08-20 - no longer a per-campaign opt-in
            # (see NOTES.md for the direction change and cost math). council_mode=True is
            # unconditional here; model_key still matters since it's what council.py locks its own
            # calls to via _cheap_model_key_for.
            narration, debug_events = engine.run_turn(
                provider, model, state, row["setting_key"], row["system_key"], conversation, user_input,
                usage_sink=usage_sink, budget_ok=budget_ok,
                council_mode=COUNCIL_MODE, model_key=model_key, suggestions=True,
            )
        except providers.ProviderError as e:
            # Can come from the main generation call OR from anywhere inside Council Mode (which
            # follows the player's model choice, including local-ollama - see council.py's module
            # docstring) if the tunnel drops mid-turn after the earlier health-check passed. Unlike
            # a budget exhaustion (which council.py degrades gracefully from), a connection failure
            # mid-council fails the whole turn cleanly rather than returning a partial/degraded
            # narration - the underlying infrastructure being down isn't a "keep going with less"
            # situation the way running low on a known, bounded budget is.
            if model_key == "local-ollama":
                return jsonify({"error": str(e), "local_model_offline": True}), 503
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # surface API/model errors to the frontend instead of a bare 500
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
        finally:
            # record whatever calls actually completed even if something above raised, so spend is
            # never lost/undercounted relative to what the provider actually billed.
            for usage in usage_sink:
                cost = pricing.estimate_cost_usd(usage)
                db.record_usage(
                    user["id"], character_id, usage["provider"], usage["model"],
                    usage["input_tokens"], usage["output_tokens"],
                    usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0), cost,
                )

        # Full transcript, saved alongside the state - see db.py's `turns` table comment for why
        # this exists (previously narration/debug_events were discarded after this one response).
        db.record_turn(character_id, turn_number_played, user_input, narration, model_key, debug_events)
        db.save_character_state(character_id, user["id"], state)
        return jsonify(
            {
                "narration": narration,
                "debug_events": debug_events,
                "state": engine.build_state_digest(state),
                "budget": db.get_budget_status(user["id"]),
            }
        )
    finally:
        lock.release()


# ==================== multiplayer: admin-gated server/location/subspace creation ====================

def _require_admin():
    """Returns (user, None) if the current session is an admin, else (None, (response, status)) to
    return directly from the calling route. See ADMIN_UNLOCK_CODE above - still no real security
    hardening intended, this just gates the routes that actually do something now."""
    user = current_user()
    if not user["is_admin"]:
        return None, (jsonify({"error": "Admin access required."}), 403)
    return user, None


@app.route("/api/admin/multiplayer_servers", methods=["POST"])
def api_admin_create_server():
    user, err = _require_admin()
    if err:
        return err
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    setting_key = body.get("setting_key")
    if not name or setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": "name and a valid setting_key are required."}), 400
    system_key = engine.SETTING_REGISTRY[setting_key]["system"]
    progression_speed = body.get("progression_speed") or "day"
    if progression_speed not in ("day", "week", "month"):
        return jsonify({"error": "progression_speed must be 'day', 'week', or 'month'."}), 400
    try:
        progression_rate_hours = float(body.get("progression_rate_hours") or 24)
    except (TypeError, ValueError):
        return jsonify({"error": "progression_rate_hours must be a number."}), 400
    # Seeds the server's shared world-clock from the setting's real starting date (e.g.
    # "1917-09-25") - db.py stays agnostic of setting content, so this is resolved here, not there.
    initial_date = engine.load_premade_character_state(setting_key).get("date")
    server_id = db.create_multiplayer_server(
        name, setting_key, system_key, progression_speed, progression_rate_hours, user["id"],
        initial_date=initial_date,
    )
    # Auto-seed one starting Location (which auto-seeds its own default Subspace - see
    # db.create_location) so players have somewhere to land immediately - an admin no longer has to
    # manually create one before the server is actually usable. Deliberately at this route level, not
    # inside db.create_multiplayer_server itself: plenty of tests build servers as raw fixtures via
    # the db function directly and manage their own locations explicitly - this is a product decision
    # about the real admin-facing creation flow, not a schema invariant.
    db.create_location(
        server_id, "Starting Point",
        "Where this world begins - rename it, or just let the story grow its own places from here.",
    )
    return jsonify({"server_id": server_id})


@app.route("/api/admin/multiplayer_servers/<int:server_id>", methods=["DELETE"])
def api_admin_delete_server(server_id):
    """Irreversible - see db.delete_multiplayer_server's docstring for exactly what this cascades
    through. The frontend confirms with the admin before ever sending this request; this route
    itself doesn't ask for a second confirmation (e.g. re-typing the server name) - the admin gate
    plus one clear confirm dialog is proportionate for a hobby-scale app with no real access control
    stakes yet (see ADMIN_UNLOCK_CODE's own docstring), not a reason to build more ceremony."""
    user, err = _require_admin()
    if err:
        return err
    if db.get_multiplayer_server(server_id) is None:
        return jsonify({"error": "Unknown multiplayer server."}), 404
    db.delete_multiplayer_server(server_id)
    return jsonify({"ok": True})


@app.route("/api/admin/multiplayer_servers/<int:server_id>/locations", methods=["POST"])
def api_admin_create_location(server_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_multiplayer_server(server_id) is None:
        return jsonify({"error": "Unknown multiplayer server."}), 404
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required."}), 400
    location_id = db.create_location(server_id, name, body.get("description"))
    return jsonify({"location_id": location_id})


@app.route("/api/admin/locations/<int:location_id>/subspaces", methods=["POST"])
def api_admin_create_subspace(location_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_location(location_id) is None:
        return jsonify({"error": "Unknown location."}), 404
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required."}), 400
    subspace_id = db.create_subspace(location_id, name)
    return jsonify({"subspace_id": subspace_id})


# ==================== admin: editing/cleaning up Locations and Subspaces ====================
# The safety net for dynamic creation (see multiplayer.detect_new_space): a bad or duplicate
# AI-generated space shouldn't require nuking the whole server to fix. Rename covers a bad name;
# merge covers duplicates (two spaces that turned out to be the same place, described differently by
# two different players/turns); delete covers a space that shouldn't exist at all. All three require
# the target(s) to have no one currently present - see db.py's docstrings for exactly why.

@app.route("/api/admin/locations/<int:location_id>", methods=["PATCH"])
def api_admin_edit_location(location_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_location(location_id) is None:
        return jsonify({"error": "Unknown location."}), 404
    body = request.get_json(force=True)
    name = body.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"error": "name cannot be blank."}), 400
    db.rename_location(location_id, name=name, description=body.get("description"))
    return jsonify({"ok": True})


@app.route("/api/admin/locations/<int:location_id>", methods=["DELETE"])
def api_admin_delete_location(location_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_location(location_id) is None:
        return jsonify({"error": "Unknown location."}), 404
    try:
        db.delete_location(location_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/locations/<int:location_id>/merge_into/<int:target_location_id>", methods=["POST"])
def api_admin_merge_locations(location_id, target_location_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_location(location_id) is None or db.get_location(target_location_id) is None:
        return jsonify({"error": "Unknown location."}), 404
    try:
        db.merge_locations(location_id, target_location_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/subspaces/<int:subspace_id>", methods=["PATCH"])
def api_admin_edit_subspace(subspace_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_subspace(subspace_id) is None:
        return jsonify({"error": "Unknown subspace."}), 404
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required."}), 400
    db.rename_subspace(subspace_id, name)
    return jsonify({"ok": True})


@app.route("/api/admin/subspaces/<int:subspace_id>", methods=["DELETE"])
def api_admin_delete_subspace(subspace_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_subspace(subspace_id) is None:
        return jsonify({"error": "Unknown subspace."}), 404
    try:
        db.delete_subspace(subspace_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/subspaces/<int:subspace_id>/merge_into/<int:target_subspace_id>", methods=["POST"])
def api_admin_merge_subspaces(subspace_id, target_subspace_id):
    user, err = _require_admin()
    if err:
        return err
    if db.get_subspace(subspace_id) is None or db.get_subspace(target_subspace_id) is None:
        return jsonify({"error": "Unknown subspace."}), 404
    try:
        db.merge_subspaces(subspace_id, target_subspace_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/npcs", methods=["POST"])
def api_admin_create_npc():
    """NPCs are scoped per-game, not per-setting - see db.py's npcs table comment. Not auto-seeded
    from a setting's npcs.md; created explicitly here."""
    user, err = _require_admin()
    if err:
        return err
    body = request.get_json(force=True)
    game_id = body.get("game_id")
    key = (body.get("key") or "").strip()
    name = (body.get("name") or "").strip()
    if not (game_id and key and name):
        return jsonify({"error": "game_id, key, and name are required."}), 400
    location_id = body.get("location_id")
    subspace_id = body.get("subspace_id")
    npc_id = db.create_npc(
        game_id, key, name, personality=body.get("personality", ""), traits=body.get("traits"),
        location_id=location_id, subspace_id=subspace_id,
    )
    return jsonify({"npc_id": npc_id})


# ==================== multiplayer: browsing, movement, messaging ====================

@app.route("/api/multiplayer/servers")
def api_multiplayer_servers():
    # game_id is folded in here (servers count is small - hobby scale, an N+1 lookup is fine) rather
    # than added as a real column on multiplayer_servers, since games->multiplayer_servers is already
    # the source of truth (see create_multiplayer_server) - the frontend's admin NPC-creation form is
    # the one place that needs it (api_admin_create_npc requires game_id).
    servers = db.list_multiplayer_servers()
    for s in servers:
        game = db.get_game_for_server(s["id"])
        s["game_id"] = game["id"] if game else None
    return jsonify({"servers": servers})


@app.route("/api/multiplayer/servers/<int:server_id>/locations")
def api_multiplayer_locations(server_id):
    if db.get_multiplayer_server(server_id) is None:
        return jsonify({"error": "Unknown multiplayer server."}), 404
    return jsonify({"locations": db.list_locations(server_id)})


@app.route("/api/multiplayer/locations/<int:location_id>/subspaces")
def api_multiplayer_subspaces(location_id):
    if db.get_location(location_id) is None:
        return jsonify({"error": "Unknown location."}), 404
    return jsonify({"subspaces": db.list_subspaces(location_id)})


@app.route("/api/multiplayer/servers/<int:server_id>/my_characters")
def api_multiplayer_my_characters(server_id):
    """Which of the CURRENT user's characters already belong to this server's game - lets the
    frontend offer "resume as X" instead of always funneling a returning player through character
    creation again. Deliberately just id/name, not full state (get_game_characters returns every
    player's characters server-side; this filters to the caller's own and strips it down to what a
    picker needs)."""
    user = current_user()
    game = db.get_game_for_server(server_id)
    if game is None:
        return jsonify({"error": "Unknown multiplayer server."}), 404
    mine = [c for c in db.get_game_characters(game["id"]) if c["user_id"] == user["id"]]
    return jsonify({"characters": [{"id": c["id"], "name": c["name"]} for c in mine]})


@app.route("/api/multiplayer/subspaces/<int:subspace_id>/detail")
def api_multiplayer_subspace_detail(subspace_id):
    """Everything a subspace's UI needs in one call: who's here (players + NPCs), whether a fight is
    active and whose turn it is, and how conclusion-marking currently stands. Unauthenticated like the
    other browsing routes (servers/locations/subspaces lists) - "who's in this subspace right now"
    isn't treated as private, same as walking into a room and seeing who's there."""
    subspace = db.get_subspace(subspace_id)
    if subspace is None:
        return jsonify({"error": "Unknown subspace."}), 404
    location = db.get_location(subspace["location_id"])

    occupant_ids = db.characters_at_subspace(subspace_id)
    occupants = []
    for cid in occupant_ids:
        c = db.get_character_unchecked(cid)
        if c:
            occupants.append({"character_id": cid, "name": c["name"]})

    npcs = [{"id": n["id"], "key": n["key"], "name": n["name"]} for n in db.npcs_at_subspace(subspace_id)]

    def _participant_name(p):
        if p["character_id"]:
            c = db.get_character_unchecked(p["character_id"])
            return c["name"] if c else f"character #{p['character_id']}"
        return p["npc_key"]

    combat = None
    encounter = db.get_active_combat_encounter(subspace_id)
    if encounter:
        participants = [
            {
                "character_id": p["character_id"], "npc_key": p["npc_key"], "name": _participant_name(p),
                "initiative_roll": p["initiative_roll"], "turn_order_index": p["turn_order_index"],
            }
            for p in db.get_combat_participants(encounter["id"])
        ]
        current_actor = db.get_current_combat_actor(encounter["id"])
        combat = {
            "encounter_id": encounter["id"],
            "current_turn_index": encounter["current_turn_index"],
            "participants": participants,
            "current_actor_character_id": current_actor["character_id"] if current_actor else None,
            "current_actor_name": _participant_name(current_actor) if current_actor else None,
        }

    marked = db.get_subspace_conclusion_marks(subspace_id)
    return jsonify(
        {
            "subspace": subspace,
            "location": location,
            "occupants": occupants,
            "npcs": npcs,
            "combat": combat,
            "conclusion": {"marked_character_ids": sorted(marked), "total_present": len(occupant_ids)},
        }
    )


@app.route("/api/multiplayer/characters/<int:character_id>/move", methods=["POST"])
def api_multiplayer_move(character_id):
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    body = request.get_json(force=True)
    location_id = body.get("location_id")
    location = db.get_location(location_id)
    if location is None:
        return jsonify({"error": "Unknown location."}), 400
    subspace_id = body.get("subspace_id")
    if subspace_id is None:
        subspace_id = db.get_default_subspace(location_id)["id"]
    elif db.get_subspace(subspace_id) is None or db.get_subspace(subspace_id)["location_id"] != location_id:
        return jsonify({"error": "Unknown subspace for this location."}), 400
    db.move_character(character_id, location["server_id"], location_id, subspace_id)
    return jsonify({"ok": True, "location_id": location_id, "subspace_id": subspace_id})


@app.route("/api/multiplayer/characters/<int:character_id>/subspace_messages")
def api_multiplayer_subspace_messages(character_id):
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    presence = db.get_character_presence(character_id)
    if presence is None:
        return jsonify({"error": "This character isn't in a location yet."}), 400
    messages = db.get_subspace_messages(presence["subspace_id"], character_id)
    return jsonify({"messages": messages, "location_id": presence["location_id"], "subspace_id": presence["subspace_id"]})


@app.route("/api/multiplayer/characters/<int:character_id>/peek/<int:subspace_id>")
def api_multiplayer_peek_subspace(character_id, subspace_id):
    """Read-only visibility into another subspace in the character's CURRENT location, without
    actually moving them there - lets a player check what's happening elsewhere in the location
    before deciding whether to go. Deliberately restricted to the current location only (not any
    subspace server-wide): "peek at the subspace next door" is the feature asked for, not "see into
    any subspace anywhere."

    Reuses get_subspace_messages unchanged, so the peeked subspace's existing visible_to_json privacy
    guarantee applies exactly as it does everywhere else - a character who's never actually been
    present at that subspace gets back an empty list, same as if they'd fetched subspace_messages for
    it as their real subspace. This is not a bypass; it's the same rule, just queryable for a
    subspace other than your current one."""
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    presence = db.get_character_presence(character_id)
    if presence is None:
        return jsonify({"error": "This character isn't in a location yet."}), 400
    target = db.get_subspace(subspace_id)
    if target is None:
        return jsonify({"error": "Unknown subspace."}), 404
    if target["location_id"] != presence["location_id"]:
        return jsonify({"error": "You can only peek at subspaces in your current location."}), 400
    messages = db.get_subspace_messages(subspace_id, character_id)
    return jsonify({"messages": messages, "location_id": target["location_id"], "subspace_id": subspace_id})


@app.route("/api/multiplayer/characters/<int:character_id>/send", methods=["POST"])
def api_multiplayer_send(character_id):
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    # Budget is Discord-only, same reasoning as the single-player turn route - see api_character_turn.
    if user["is_local"]:
        return jsonify({"error": "Log in with Discord to send messages.", "discord_login_required": True}), 403

    presence = db.get_character_presence(character_id)
    if presence is None:
        return jsonify({"error": "This character isn't in a location yet."}), 400

    body = request.get_json(force=True)
    kind = body.get("kind")
    content = (body.get("content") or "").strip()
    reply_to_turn_id = body.get("reply_to_turn_id")
    model_key = body.get("model_key") or providers.DEFAULT_MODEL_KEY
    if not content:
        return jsonify({"error": "Empty message."}), 400
    if kind not in ("message", "action"):
        return jsonify({"error": "kind must be 'message' or 'action'."}), 400
    if model_key not in providers.MODEL_REGISTRY:
        return jsonify({"error": f"Unknown model key: {model_key}"}), 400
    if providers.ALLOWED_MODEL_KEYS is not None and model_key not in providers.ALLOWED_MODEL_KEYS:
        return jsonify({"error": f"Model '{model_key}' isn't currently selectable."}), 400

    lock = _turn_lock_for(user["id"])
    if not lock.acquire(blocking=False):
        return jsonify({"error": "Another turn is already in progress for this account."}), 429
    try:
        budget_before = db.get_budget_status(user["id"])
        if budget_before["remaining_usd"] <= 0:
            return jsonify({"error": "Out of usage budget for this account.", "budget": budget_before}), 402

        try:
            provider, model = providers.get_provider(model_key)
        except providers.ProviderError as e:
            return jsonify({"error": str(e)}), 400

        # Interim cost model: the sender is charged at normal single-player rates for now - the
        # confirmed 0.5x-shared/50%-subsidized cost split is a separate piece of the multiplayer
        # budget phase, not built yet (see NOTES.md). Charging the sender normally in the meantime is
        # safer than leaving multiplayer messages untracked/free.
        usage_sink = []

        def budget_ok():
            spent_this_turn = sum(pricing.estimate_cost_usd(u) for u in usage_sink)
            return (budget_before["remaining_usd"] - spent_this_turn) > 0

        response_body = {}
        try:
            if kind == "message":
                message_turn_id, reply_turn_id, responder = multiplayer.handle_message(
                    provider, model, char["game_id"], presence["location_id"], presence["subspace_id"],
                    character_id, content, reply_to_turn_id=reply_to_turn_id, usage_sink=usage_sink,
                )
                response_body = {
                    "kind": "message",
                    "message_turn_id": message_turn_id,
                    "reply_turn_id": reply_turn_id,
                    "responder": responder,
                }
            else:
                provider_key = providers.MODEL_REGISTRY[model_key]["provider"]
                conversation = _conversation_for(character_id, provider_key, provider)
                try:
                    result = multiplayer.resolve_action(
                        provider, model, model_key, char, presence["location_id"], presence["subspace_id"],
                        content, conversation, usage_sink=usage_sink, budget_ok=budget_ok,
                    )
                except multiplayer.CombatTurnError as e:
                    return jsonify({"error": str(e)}), 409
                response_body = {
                    "kind": "action",
                    "turn_id": result["turn_id"],
                    "reply_turn_id": result["reply_turn_id"],
                    "narration": result["narration"],
                    "debug_events": result["debug_events"],
                    "combat_encounter_id": result["combat_encounter_id"],
                    # Set when this action's resolution created a new subspace or a whole new
                    # location, or moved the character into an already-existing one (matched by name)
                    # - see multiplayer.detect_new_space. The frontend treats every case like any other
                    # move: reload location/subspace detail and reopen its SSE stream there.
                    "new_subspace_id": result["new_subspace_id"],
                    "new_location_id": result["new_location_id"],
                    "moved_to_subspace_id": result["moved_to_subspace_id"],
                    "moved_to_location_id": result["moved_to_location_id"],
                }
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
        finally:
            for usage in usage_sink:
                cost = pricing.estimate_cost_usd(usage)
                db.record_usage(
                    user["id"], character_id, usage["provider"], usage["model"],
                    usage["input_tokens"], usage["output_tokens"],
                    usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0), cost,
                )

        return jsonify(
            {
                **response_body,
                "budget": db.get_budget_status(user["id"]),
            }
        )
    finally:
        lock.release()


def _bill_ambient_usage(usage_sink, present_character_ids):
    """Bills a non-player-triggered AI call (a storm-out headcount check, and eventually the daily
    conclusion sweep) per the confirmed cost model in NOTES.md: split evenly across every present
    player, each charged only pricing.AMBIENT_COST_PLAYER_SHARE of their share - the rest isn't
    charged to anyone. Local/anonymous-owned characters are skipped (same as everywhere else in this
    app - they have no real budget to charge). No-op if usage_sink is empty (no AI call was actually
    made, e.g. the headcount check was skipped) or nobody present has a billable account."""
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
    # The real, meaningful number here is the dollar cost, not a precise per-token attribution across
    # players for a call that wasn't really "any one of them acting" anyway - token counts are
    # recorded from whichever usage entry was largest, for observability, not exactness.
    representative = max(usage_sink, key=lambda u: u.get("input_tokens", 0) + u.get("output_tokens", 0))
    for cid, user_id in payers:
        db.record_usage(
            user_id, cid, representative["provider"], representative["model"],
            representative.get("input_tokens", 0), representative.get("output_tokens", 0),
            representative.get("cache_read_tokens", 0), representative.get("cache_creation_tokens", 0),
            per_player_cost,
        )


@app.route("/api/multiplayer/characters/<int:character_id>/mark_concluded", methods=["POST"])
def api_multiplayer_mark_concluded(character_id):
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    presence = db.get_character_presence(character_id)
    if presence is None:
        return jsonify({"error": "This character isn't in a location yet."}), 400

    body = request.get_json(force=True) or {}
    storm_out = bool(body.get("storm_out"))
    model_key = body.get("model_key") or providers.DEFAULT_MODEL_KEY

    if not storm_out:
        # A provider is resolved here even for a plain mark-concluded now, since conclusion may
        # trigger rumor generation (multiplayer.generate_rumor) - unlike before this phase, when
        # marking concluded was pure DB bookkeeping. If unconfigured, conclusion still succeeds;
        # generate_rumor is just never called for it (see multiplayer._conclude_thread).
        provider, model = None, None
        try:
            provider, model = providers.get_provider(model_key)
        except providers.ProviderError:
            provider, model = None, None
        present_before = db.characters_at_subspace(presence["subspace_id"])
        usage_sink = []
        concluded, marks, total = multiplayer.mark_concluded(
            char, presence["location_id"], presence["subspace_id"], provider=provider, model=model, usage_sink=usage_sink,
        )
        if concluded:
            _bill_ambient_usage(usage_sink, present_before)
        return jsonify({"concluded": concluded, "marks": marks, "total_present": total})
    final_message = (body.get("final_message") or "").strip() or None
    remaining_before = [c for c in db.characters_at_subspace(presence["subspace_id"]) if c != character_id]

    # A provider is always worth resolving now, not just when remaining_before is non-empty - even a
    # zero-remaining storm-out still generates a rumor of the concluded thread (see
    # multiplayer._conclude_thread), not just the headcount check this used to be the only reason for.
    provider, model = None, None
    try:
        provider, model = providers.get_provider(model_key)
    except providers.ProviderError:
        provider, model = None, None  # leaving still succeeds; both the headcount check and rumor generation just get skipped

    usage_sink = []
    new_subspace_id, auto_concluded = multiplayer.storm_out(
        provider, model, char, presence["location_id"], presence["subspace_id"], final_message, usage_sink=usage_sink,
    )
    _bill_ambient_usage(usage_sink, remaining_before)

    return jsonify({"new_subspace_id": new_subspace_id, "thread_auto_concluded": auto_concluded})


@app.route("/api/multiplayer/servers/<int:server_id>/rumors")
def api_multiplayer_rumors(server_id):
    if db.get_multiplayer_server(server_id) is None:
        return jsonify({"error": "Unknown multiplayer server."}), 404
    return jsonify({"broadcasts": db.list_rumor_broadcasts(server_id)})


@app.route("/api/multiplayer/characters/<int:character_id>/stream")
def api_multiplayer_stream(character_id):
    """Server-Sent Events - live updates for whichever subspace/server this character is currently
    in, per the explicit "see messages live" requirement (not polling). One queue per connection,
    subscribed under both the subspace and server (see live.py) so a single generator only ever
    drains one queue. A heartbeat comment line is sent whenever nothing real has happened for
    live.HEARTBEAT_TIMEOUT_SECONDS, so proxies/browsers don't treat a quiet connection as dead.

    Known limitation, not solved here: if this character MOVES to a new subspace while a stream is
    open, this connection keeps listening on the OLD subspace - the frontend is expected to close and
    reopen the stream after a successful move, not something this endpoint handles server-side."""
    user = current_user()
    char = db.get_character(character_id, user["id"])
    if char is None:
        return jsonify({"error": "Character not found."}), 404
    presence = db.get_character_presence(character_id)
    if presence is None:
        return jsonify({"error": "This character isn't in a location yet."}), 400

    subspace_id = presence["subspace_id"]
    server_id = presence["server_id"]

    def event_stream():
        q = live.subscribe(subspace_id, server_id)
        try:
            while True:
                try:
                    event = q.get(timeout=live.HEARTBEAT_TIMEOUT_SECONDS)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            live.unsubscribe(subspace_id, server_id, q)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/internal/notify_turn", methods=["POST"])
def api_internal_notify_turn():
    """Bridges a turn recorded by a SEPARATE OS process (run_discord_bot.py) into THIS process's own
    live SSE subscribers (2026-09-03 - a real gap, found live via the actual bot, not hypothetical).

    live.py is deliberately in-process-only (see its own docstring) - multiplayer.handle_message/
    resolve_action already call live.publish_subspace_event unconditionally, but when the BOT calls
    those functions, that publish only reaches ITS OWN empty in-memory subscriber set. Real web
    viewers' SSE connections live in THIS process (server.py, run via waitress) instead - a
    completely separate Python process with its own memory, even though both processes share the
    same SQLite file. So a Discord-originated turn was being recorded correctly (any viewer who
    reloads or reopens the page sees it) but never pushed live to anyone already watching - exactly
    the gap reported. This route is the fix: the bot process asks THIS process (the one that actually
    holds the real subscriber queues) to republish an already-recorded turn on its behalf.

    Gated on a shared secret, not remote_addr - this app is reachable through a Cloudflare tunnel that
    forwards every public request to this same localhost address, so IP alone can't tell a real bot
    call apart from internet traffic. The request only ever names a turn_id and re-fetches the real
    row from the database before publishing anything - it can't be used to inject fabricated event
    content, only to (redundantly, at worst) re-broadcast a turn that's already really there."""
    secret = os.environ.get("INTERNAL_NOTIFY_SECRET", "").strip()
    if not secret or request.headers.get("X-Internal-Secret") != secret:
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(force=True)
    turn = db.get_turn_by_id(body.get("turn_id"))
    if turn is None:
        return jsonify({"error": "unknown turn"}), 404
    live.publish_subspace_event(turn["subspace_id"], turn)
    return jsonify({"ok": True})


# ==================== TEMPORARY: hosting-migration DB transfer (2026-09-03) ====================
# Moves the REAL local butterfly.db (every real character/server/turn/rumor - the actual live data)
# onto Render's persistent disk, once, right after the first deploy that runs server:app there -
# Render's disk has never had this app's real data on it (it only ever ran proxy_app.py before,
# which has no database at all), so a naive cutover would go live with a silently EMPTY database.
# Gated on a one-time secret, not the normal admin-session flow, since this needs to work from a raw
# curl call with no browser session. DELETE THIS WHOLE SECTION (and unset DB_MIGRATION_SECRET on
# Render) once the migration is verified complete - see NOTES.md. Not meant to outlive tonight.

@app.route("/api/internal/db_stats")
def api_internal_db_stats():
    secret = os.environ.get("DB_MIGRATION_SECRET", "").strip()
    if not secret or request.headers.get("X-Migration-Secret") != secret:
        return jsonify({"error": "forbidden"}), 403
    conn = db.get_db()
    tables = ["users", "characters", "turns", "multiplayer_servers", "locations", "subspaces", "rumors", "rumor_broadcasts"]
    counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}
    conn.close()
    return jsonify({"db_file": str(db.DB_FILE), "size_bytes": db.DB_FILE.stat().st_size if db.DB_FILE.exists() else 0, "counts": counts})


@app.route("/api/internal/restore_db", methods=["POST"])
def api_internal_restore_db():
    secret = os.environ.get("DB_MIGRATION_SECRET", "").strip()
    if not secret or request.headers.get("X-Migration-Secret") != secret:
        return jsonify({"error": "forbidden"}), 403
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "no file uploaded - POST multipart/form-data with a 'file' field"}), 400
    db.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    uploaded.save(str(db.DB_FILE))
    return jsonify({"ok": True, "size_bytes": db.DB_FILE.stat().st_size})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port)
