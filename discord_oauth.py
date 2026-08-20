"""
Minimal Discord OAuth2 login - deliberately just `requests` against Discord's documented OAuth2 and
API endpoints, no extra OAuth framework dependency for a flow this small.

Requires three settings, read from the environment (via .env or real env vars, same pattern as the
LLM provider API keys - see .env.example):
  DISCORD_CLIENT_ID      - from your Discord Developer Application
  DISCORD_CLIENT_SECRET  - from the same application; NEVER put this in a .py file, .env only
  DISCORD_REDIRECT_URI    - must exactly match a redirect registered on the Discord application,
                             e.g. http://127.0.0.1:5000/auth/discord/callback for local dev, or
                             https://<your-app>.onrender.com/auth/discord/callback once deployed

Flow:
  1. authorize_url() -> send the user to Discord to approve.
  2. Discord redirects back to DISCORD_REDIRECT_URI with a `code` query param.
  3. exchange_code(code) -> access token.
  4. fetch_identity(access_token) -> {"id", "username", "avatar_url"}.
server.py wires these into /auth/discord/login and /auth/discord/callback.
"""

import os
from urllib.parse import urlencode

import requests

DISCORD_API_BASE = "https://discord.com/api"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
USER_URL = f"{DISCORD_API_BASE}/users/@me"


class DiscordOAuthError(RuntimeError):
    pass


def _config():
    client_id = os.environ.get("DISCORD_CLIENT_ID")
    client_secret = os.environ.get("DISCORD_CLIENT_SECRET")
    redirect_uri = os.environ.get("DISCORD_REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        raise DiscordOAuthError(
            "Discord login isn't configured - set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, and "
            "DISCORD_REDIRECT_URI (see README's Discord login section)."
        )
    return client_id, client_secret, redirect_uri


def is_configured():
    return bool(
        os.environ.get("DISCORD_CLIENT_ID")
        and os.environ.get("DISCORD_CLIENT_SECRET")
        and os.environ.get("DISCORD_REDIRECT_URI")
    )


def authorize_url(state):
    client_id, _client_secret, redirect_uri = _config()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code):
    client_id, client_secret, redirect_uri = _config()
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(TOKEN_URL, data=data, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise DiscordOAuthError(f"Discord token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def fetch_identity(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(USER_URL, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise DiscordOAuthError(f"Discord identity fetch failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    avatar_url = None
    if data.get("avatar"):
        ext = "gif" if data["avatar"].startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{data['id']}/{data['avatar']}.{ext}"
    username = data.get("global_name") or data.get("username") or "Discord User"
    return {"id": data["id"], "username": username, "avatar_url": avatar_url}
