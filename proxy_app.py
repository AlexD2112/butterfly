"""
Redirect-only shim - what runs on Render now that Butterfly is hosted on the player's own machine
behind a permanent named Cloudflare tunnel (https://forkingpaths.app - see
local-site-tunnel/README.md and NOTES.md, 2026-09-02). This file's only job is a permanent redirect
to the new domain, preserving the original path and query string exactly, so old bookmarks/links to
butterfly-haa9.onrender.com keep working instead of breaking outright.

This USED to be a full reverse proxy (see git history) forwarding every request to whatever
ephemeral Cloudflare quick-tunnel URL the local machine's start script printed that session
(LOCAL_TUNNEL_URL) - that existed to give the site a fixed public address despite the quick
tunnel's own URL changing every restart. A NAMED tunnel with a real domain solves that problem
natively, at the tunnel level, with no extra network hop and no env var to keep updating - so this
file's job shrinks to just "point old links at the new home."

Why DISCORD_REDIRECT_URI (in the real app's own .env, not here) still points at THIS domain's
/auth/discord/callback path rather than forkingpaths.app directly: Discord's OAuth2 app config only
has the OLD address registered as a valid redirect right now. The redirect_uri Discord checks during
login is just a string comparison, never actually fetched or rendered - so as long as this redirect
preserves the full path and query string, the `code` Discord attaches on its way back still reaches
the real app's actual callback handler correctly, just via one extra hop. Fully retiring this file
would mean adding https://forkingpaths.app/auth/discord/callback as an additional registered
redirect in Discord's Developer Portal (a real action needing the account owner's own Discord
login - not something done on their behalf) and then switching DISCORD_REDIRECT_URI over - not done
yet, not urgent, see NOTES.md.
"""

import os

from flask import Flask, redirect, request

app = Flask(__name__)

NEW_HOME = "https://forkingpaths.app"


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def redirect_to_new_home(path):
    target = f"{NEW_HOME}/{path}"
    if request.query_string:
        target += f"?{request.query_string.decode('utf-8')}"
    return redirect(target, code=301)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
