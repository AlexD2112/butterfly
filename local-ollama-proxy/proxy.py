"""
Local reverse-proxy sitting between a Cloudflare quick tunnel and Ollama's raw API port.

Ollama (localhost:11434) has NO built-in authentication - anyone who can reach that port gets free,
unmetered use of this machine's GPU. The whole point of exposing it via a Cloudflare tunnel is to
let Butterfly's Render-hosted backend call it from the public internet (server-to-server - players'
browsers never touch this machine, see butterfly's README), which means that port would otherwise be
reachable by literally anyone who guesses/finds the tunnel URL. This proxy sits in front of Ollama's
real port and enforces two things Ollama doesn't:
  1. A shared-secret bearer token, checked on every request BEFORE it reaches Ollama.
  2. A simple global rate limit, as a backstop against a bug or abuse causing runaway GPU/
     electricity cost - this machine is the only thing paying for those tokens, there's no billing
     relationship with whoever ends up calling this.

Point the Cloudflare quick tunnel at THIS proxy's port (default 11435), NEVER at Ollama's port
(11434) directly - see README.md in this folder for the full setup.

Forwards specifically to Ollama's NATIVE /api/chat endpoint, not the OpenAI-compatible /v1/
surface - confirmed by direct testing (2026-08-20) that Ollama's OpenAI-compat endpoint silently
ignores a num_ctx override in the request body (`ollama ps` kept reporting the desktop app's
configured context regardless of what was sent), while the native endpoint honors it correctly.
Since the whole point of this proxy injecting num_ctx is to NOT depend on the desktop app's
context-length setting (which defaults far too low for this app's ~9,500-token system prompt - see
NOTES.md), the native endpoint is the one that actually delivers on that.

Setup:
  pip install flask requests python-dotenv
  copy .env.example to .env and set PROXY_SHARED_SECRET (see README.md for how to generate one)
  python proxy.py
"""

import os
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
import requests

load_dotenv(Path(__file__).parent / ".env")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
SHARED_SECRET = os.environ.get("PROXY_SHARED_SECRET")
if not SHARED_SECRET:
    raise RuntimeError(
        "PROXY_SHARED_SECRET is not set - copy .env.example to .env and set it (see README.md). "
        "Refusing to start without it: an unauthenticated proxy would expose this machine's GPU to "
        "the open internet the moment the tunnel comes up."
    )

# Global fixed-window rate limit - deliberately NOT per-caller/per-IP, since the only expected
# caller is Butterfly's own Render backend (one server calling another, not many players calling
# this directly). This exists purely as a backstop against a retry-loop bug or a leaked tunnel URL
# running up electricity cost, not to distinguish individual players from each other. 60/minute is
# generous for real gameplay (Council Mode's full pipeline is ~7-10 calls per single player turn,
# and a human can't type turns anywhere near that fast) while still bounding worst-case runaway
# cost if something malfunctions. Tune via RATE_LIMIT_MAX_REQUESTS if needed.
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", 60))
RATE_LIMIT_WINDOW_SECONDS = 60
_request_times = deque()

# Context window forced on every request, overriding whatever the Ollama desktop app has configured
# (confirmed by testing to default to ~4K there, far below what this app needs). Empirically tested
# against this machine's actual hardware (RTX 4080 Laptop, 12GB VRAM) on 2026-08-20 before picking
# this number - see NOTES.md for the full reasoning:
#   - Butterfly's real system prompt (rules/NPCs/factions/canon/resources/state) measures ~9,500
#     tokens; plus tool definitions (~1,140 tokens), the floor requirement per call is ~10,650
#     tokens with ZERO room for conversation history or output - too tight to actually use.
#   - Raising context does cost GPU-resident model layers (more of the 30B model gets offloaded to
#     CPU to make room for the larger KV cache), but the measured cost was gentler than expected for
#     this MoE model: ~54% GPU at the ~4K default, ~51% at 16K, ~49% at 24K, ~47% at 32K - a shallow
#     slope, not a cliff.
#   - 24576 (24K) covers the ~10,650-token floor with ~14K tokens of headroom for conversation
#     growth over a session (conversation history isn't summarized/trimmed anywhere in this app yet
#     - a known, already-documented gap - so it grows unboundedly turn over turn regardless of this
#     number), while keeping just under half the model CPU-offloaded rather than pushing further.
# Change here (not in the Ollama desktop app) if a different value is ever needed - this is the
# actual source of truth per the user's explicit instruction not to rely on the app's setting.
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", 24576))

app = Flask(__name__)


def _rate_limit_ok():
    now = time.monotonic()
    while _request_times and now - _request_times[0] > RATE_LIMIT_WINDOW_SECONDS:
        _request_times.popleft()
    if len(_request_times) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    _request_times.append(now)
    return True


@app.route("/health")
def health():
    # Deliberately unauthenticated and not rate-limited - Butterfly checks this fairly often
    # (cached on its side, but still) to decide whether to even show the local model as selectable,
    # and answering it costs nothing (no Ollama call, no secret needed to ask "are you up").
    return jsonify({"status": "ok"})


@app.route("/api/chat", methods=["POST"])
def proxy_chat():
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {SHARED_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_limit_ok():
        return jsonify({"error": "rate limited - too many requests to the local model right now, try again shortly"}), 429

    body = request.get_json(force=True, silent=True) or {}
    # Force num_ctx on every request rather than trusting the caller to set it (or the Ollama
    # desktop app's own default) - merge into any options the caller already sent so nothing else
    # gets clobbered.
    body["options"] = {**(body.get("options") or {}), "num_ctx": NUM_CTX}

    try:
        upstream = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=body,
            # Generation speed on this machine varies a fair amount (observed roughly 9-29 tok/s
            # across testing, plus a one-time 30-50s+ reload whenever the model/context changes) -
            # this timeout is generous specifically to avoid the proxy giving up before a slow-but-
            # legitimate response finishes. Butterfly's own request to this proxy needs a matching
            # or longer timeout, or it'll give up first regardless of what this does.
            timeout=280,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"couldn't reach Ollama on this machine: {e}"}), 502

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PROXY_PORT", 11435))
    print(f"Local Ollama auth/rate-limit proxy listening on http://127.0.0.1:{port}")
    print(f"Forwarding authenticated requests to {OLLAMA_URL}")
    print("Point the Cloudflare quick tunnel at this port, never at Ollama's port directly.")
    app.run(host="127.0.0.1", port=port)
