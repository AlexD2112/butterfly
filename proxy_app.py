"""
Minimal reverse-proxy app - what actually runs on Render now that Butterfly itself is hosted on the
player's own machine (see NOTES.md, "Move hosting to this local machine"). This file's only job is
to forward every request it receives to LOCAL_TUNNEL_URL (the Cloudflare quick tunnel currently
pointed at the real app - see start_local_site.ps1) and relay the response back byte-for-byte,
so the existing public URL (butterfly-haa9.onrender.com) keeps working for anyone who already has
that link, instead of everyone needing a brand-new tunnel URL.

This is a transparent pass-through, not a second copy of the application: it has no database, no
provider keys, no session logic of its own - all of that lives on the real server.py running
locally. The browser only ever talks to this Render URL; it has no idea a tunnel is involved, which
is what makes cookies (including the Discord OAuth session) keep working with zero changes to
discord_oauth.py or server.py's session config - DISCORD_REDIRECT_URI stays pointed at this Render
URL exactly as it always was, this file just relays the callback through to wherever the real app
currently is.

Why a real reverse proxy instead of just redirecting the browser to the tunnel URL directly: a
redirect would (a) break the moment the tunnel URL rotates - which is EVERY time the local machine
restarts the tunnel, not a rare event - since old bookmarks/links would 404 immediately, and (b)
expose the ephemeral trycloudflare.com URL to end users at all, which the Ollama-tunnel feature
deliberately avoids too (see local-ollama-proxy/README.md). A transparent proxy means the tunnel URL
only ever needs to be known by this one Render env var (LOCAL_TUNNEL_URL), updated each time the
local tunnel restarts - same operational reality as OLLAMA_TUNNEL_URL, not a new kind of chore.

Setup on Render: point the service's start command at THIS app (see Procfile/render.yaml) and set
LOCAL_TUNNEL_URL to the current tunnel URL from start_local_site.ps1's output. If it's unset or the
local machine is offline, every request gets a clear 503 explaining that, instead of a raw connection
error.
"""

import os

from flask import Flask, Response, request
import requests

app = Flask(__name__)

# Same shape as gunicorn's --timeout on the real app (see render.yaml) minus a safety margin, so this
# proxy has a chance to return a clean error response before Render's own gunicorn worker times out
# and kills the request outright. A local-model turn (OllamaTunnelProvider) can legitimately take
# several minutes - see NOTES.md - so this needs to be generous, not a normal web-request timeout.
UPSTREAM_TIMEOUT_SECONDS = 850

# Headers that must never be blindly forwarded in either direction - hop-by-hop headers (RFC 7230
# 6.1) that only make sense between one specific connection and its immediate peer, plus
# Content-Length/Content-Encoding, which `requests` has already resolved by the time we see the
# response (it transparently decompresses gzip/deflate) - forwarding the ORIGINAL values here would
# describe bytes we're no longer sending, and Werkzeug recomputes a correct Content-Length for us
# once it builds the outgoing Response from a plain bytes body.
_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding", "content-length",
}
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection"}


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def proxy(path):
    base_url = (os.environ.get("LOCAL_TUNNEL_URL") or "").rstrip("/")
    if not base_url:
        return (
            "Butterfly's host machine isn't configured right now (LOCAL_TUNNEL_URL is unset on this "
            "Render service) - this is just a pass-through, the real app runs locally. See NOTES.md.",
            503,
        )

    target_url = f"{base_url}/{path}"
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

    try:
        upstream = requests.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            params=request.args,
            data=request.get_data(),
            cookies=None,  # already carried via the forwarded Cookie header, not requests' own jar
            allow_redirects=False,  # relay redirects (e.g. the Discord OAuth flow) as-is to the browser
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        return (
            f"Couldn't reach Butterfly's host machine right now ({type(e).__name__}) - it may be "
            "asleep or its tunnel may have dropped. Try again in a moment.",
            503,
        )

    response_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS]
    return Response(upstream.content, status=upstream.status_code, headers=response_headers)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
