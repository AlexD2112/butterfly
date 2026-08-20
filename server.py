"""
Butterfly webapp entry point.

Run: python server.py   (then open http://127.0.0.1:5000)

API keys and secrets can come from real environment variables, or from an ai_rpg/.env file (see
.env.example) - load_dotenv() below picks up .env automatically if present, without overriding any
real env var that's already set. Never put actual secrets in a .py file - .env (and butterfly.db)
are gitignored specifically so they can live on disk without ever being committed.

Every visitor gets a persistent local account automatically (a signed session cookie mapped to a
row in butterfly.db) so character creation and saving work without requiring login. "Login with
Discord" switches the session to a Discord-linked account instead - see discord_oauth.py. The two
identities are NOT merged automatically; characters stay with whichever account created them.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session, redirect

load_dotenv(Path(__file__).parent / ".env")

import db
import discord_oauth
import engine
import providers

db.init_db()

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
# NOTE: if FLASK_SECRET_KEY isn't set, a random one is generated per process start, which means
# sessions (and therefore "who am I") reset on every restart/redeploy. Fine for local dev; set a
# real FLASK_SECRET_KEY in production (e.g. Render's environment variables) so logins persist.

# Per-character, per-provider conversation threads - see providers.py's docstring for why these
# aren't translated between providers. Keyed by (character_id, provider_key); character ownership
# is re-checked on every request via db.get_character(), so this cache can't leak across users.
_conversations = {}


def _conversation_for(character_id, provider_key, provider):
    key = (character_id, provider_key)
    if key not in _conversations:
        _conversations[key] = provider.new_conversation()
    return _conversations[key]


def current_user():
    user_id = session.get("user_id")
    if user_id:
        user = db.get_user_by_id(user_id)
        if user:
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
    return jsonify(
        {
            "id": user["id"],
            "username": user["username"],
            "avatar_url": user["avatar_url"],
            "is_local": bool(user["is_local"]),
            "discord_login_available": discord_oauth.is_configured(),
        }
    )


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
        return redirect(f"/?auth_error={error}")
    state = request.args.get("state")
    if not state or state != session.get("oauth_state"):
        return redirect("/?auth_error=state_mismatch")
    code = request.args.get("code")
    if not code:
        return redirect("/?auth_error=missing_code")
    try:
        token = discord_oauth.exchange_code(code)
        identity = discord_oauth.fetch_identity(token)
    except discord_oauth.DiscordOAuthError as e:
        return redirect(f"/?auth_error={e}")
    user = db.get_or_create_discord_user(identity["id"], identity["username"], identity["avatar_url"])
    session["user_id"] = user["id"]
    session.pop("oauth_state", None)
    return redirect("/")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.route("/api/models")
def api_models():
    models = [
        {
            "key": key,
            "label": entry["label"],
            "available": providers.provider_available(entry["provider"]),
        }
        for key, entry in providers.MODEL_REGISTRY.items()
    ]
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
            "max_starting_expertise_skills": core.MAX_STARTING_EXPERTISE_SKILLS,
            "skills": system.SKILLS,
            "starting_feats": starting_feats,
            "liabilities": system.LIABILITY_CATALOG,
        }
    )


@app.route("/api/characters/<setting_key>")
def api_characters(setting_key):
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 404
    user = current_user()
    rows = [c for c in db.list_characters(user["id"]) if c["setting_key"] == setting_key]
    return jsonify({"characters": rows, "premade_name": engine.SETTING_REGISTRY[setting_key]["premade_character_name"]})


@app.route("/api/characters", methods=["POST"])
def api_create_character():
    user = current_user()
    body = request.get_json(force=True)
    setting_key = body.get("setting_key")
    if setting_key not in engine.SETTING_REGISTRY:
        return jsonify({"error": f"Unknown setting: {setting_key}"}), 400
    system_key = engine.SETTING_REGISTRY[setting_key]["system"]

    if body.get("type") == "premade":
        state = engine.load_premade_character_state(setting_key)
        state.pop("_is_new", None)
        name = state["character"]["name"]
        character_id = db.create_character(user["id"], setting_key, system_key, name, state, is_premade=True)
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
        character_id = db.create_character(user["id"], setting_key, system_key, character["name"], state, is_premade=False)
        return jsonify({"character_id": character_id, "state": engine.build_state_digest(state), "is_new": True})

    return jsonify({"error": "body.type must be 'premade' or 'custom'"}), 400


@app.route("/api/character/<int:character_id>/state")
def api_character_state(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    return jsonify({"state": engine.build_state_digest(row["state"]), "setting_key": row["setting_key"], "system_key": row["system_key"]})


@app.route("/api/character/<int:character_id>/opening_scene")
def api_character_opening_scene(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404
    return jsonify({"text": engine.load_text(row["setting_key"], "opening_scene.md")})


@app.route("/api/character/<int:character_id>/turn", methods=["POST"])
def api_character_turn(character_id):
    user = current_user()
    row = db.get_character(character_id, user["id"])
    if row is None:
        return jsonify({"error": "Character not found."}), 404

    body = request.get_json(force=True)
    user_input = (body.get("input") or "").strip()
    model_key = body.get("model_key") or providers.DEFAULT_MODEL_KEY
    if model_key not in providers.MODEL_REGISTRY:
        return jsonify({"error": f"Unknown model key: {model_key}"}), 400
    if not user_input:
        return jsonify({"error": "Empty input."}), 400

    try:
        provider, model = providers.get_provider(model_key)
    except providers.ProviderError as e:
        return jsonify({"error": str(e)}), 400

    state = row["state"]
    provider_key = providers.MODEL_REGISTRY[model_key]["provider"]
    conversation = _conversation_for(character_id, provider_key, provider)

    try:
        narration, debug_events = engine.run_turn(
            provider, model, state, row["setting_key"], row["system_key"], conversation, user_input
        )
    except Exception as e:  # surface API/model errors to the frontend instead of a bare 500
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502

    db.save_character_state(character_id, user["id"], state)
    return jsonify(
        {
            "narration": narration,
            "debug_events": debug_events,
            "state": engine.build_state_digest(state),
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port)
