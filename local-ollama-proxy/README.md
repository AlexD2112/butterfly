# Local Ollama tunnel proxy

Exposes a local Ollama instance to Butterfly's Render deployment as a free, player-selectable
model - see the main repo's `NOTES.md` for the full design/reasoning. This folder is the *local
machine* half of that feature; `providers.py`'s `OllamaTunnelProvider` is the Butterfly-side half.

**Architecture**: Cloudflare Tunnel is outbound-only from this machine - no inbound port ever opens
on your router, your network stays hidden. Render's Flask backend (not players' browsers) calls the
resulting public HTTPS URL server-to-server. Players never connect to this machine directly.

```
Render (Butterfly backend) --https--> Cloudflare edge --tunnel--> cloudflared (this machine)
                                                                        |
                                                                        v
                                                            proxy.py (127.0.0.1:11435)
                                                              - checks shared secret
                                                              - rate limits
                                                                        |
                                                                        v
                                                              Ollama (127.0.0.1:11434)
```

**Why the proxy exists**: Ollama has no authentication of its own. Once a tunnel exposes it to the
internet, anyone who finds the URL could use your GPU for free, unmetered inference. The proxy sits
in front of Ollama's real port and requires a shared secret + rate limit before forwarding anything.
Never point the Cloudflare tunnel at Ollama's port (11434) directly.

## One-time setup

1. Make sure Ollama is installed and `qwen3:30b` is pulled (`ollama pull qwen3:30b`) - see the main
   repo's `NOTES.md` for how that was set up on this machine.
2. Install this folder's Python dependencies (a plain `pip install`, no venv required, but one's
   fine too):
   ```
   pip install -r requirements.txt
   ```
3. Generate a shared secret and put it in `.env`:
   ```
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Paste the output as `PROXY_SHARED_SECRET=...` in `.env`.
4. Tell Butterfly's Render deployment the same secret: set `OLLAMA_TUNNEL_SECRET` to that exact
   value in Render's environment variables (Render dashboard -> your service -> Environment).

## Every time you want the local model available to the live site

1. Make sure Ollama is running (it normally auto-starts; check the system tray, or run
   `ollama list` to confirm).
2. Run `start_local_llm.ps1` (double-click it, or `powershell -File start_local_llm.ps1`). This
   opens two windows: the proxy, and a Cloudflare quick tunnel pointed at it.
3. **Copy the tunnel's public URL** (`https://something-random.trycloudflare.com`, printed in the
   tunnel window) into Render's `OLLAMA_TUNNEL_URL` environment variable, then save/redeploy on
   Render's side so it picks up the change.

**The tunnel URL changes every single time you run step 2** - this is a Cloudflare *quick* tunnel
(free, no domain/account setup needed), which is ephemeral by design, not a named/persistent tunnel.
If you restart your laptop, restart the script, or the tunnel drops for any reason, you'll get a new
URL and need to update Render's env var again. There's no way around this without registering a
domain in Cloudflare and setting up a named tunnel instead (out of scope for now - a quick tunnel
was the deliberate choice to avoid needing a domain).

Your laptop needs to be on and awake for the local model to be reachable at all - if it's asleep or
the script isn't running, Butterfly's health check will correctly report the local model as
unavailable and hide/disable it in the model picker rather than fail on click.

## Rate limit / cost safety

`proxy.py` enforces a global limit (default 60 requests/minute, see `.env.example`) as a backstop
against a bug or abuse running up your electricity cost - it's not per-player, since the only
expected caller is Butterfly's own backend. There's no dollar cost to you either way (it's your own
GPU), but a runaway loop hammering it at max throughput indefinitely isn't free in electricity or
component wear, hence the limit.

## Stopping

Close both PowerShell windows (proxy and tunnel). Ollama itself keeps running independently (it's
not started or stopped by this script) - stop it separately via the tray icon if you want to.
