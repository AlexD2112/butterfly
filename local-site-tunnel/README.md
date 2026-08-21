# Local site hosting + Render pass-through

Moves the real Butterfly app (`server.py`, `butterfly.db`) off Render's free tier and onto this
machine, so SQLite data stops getting wiped on every Render redeploy (Render's free web services
have no persistent disk - confirmed by evidence in request logs: character IDs kept resetting to 1
across sessions). Render keeps serving the existing public URL
(`https://butterfly-haa9.onrender.com`) as a thin pass-through (`proxy_app.py` at the repo root)
instead of running the real app - see `proxy_app.py`'s own docstring and `NOTES.md` for the full
reasoning.

**Architecture** - same shape as `local-ollama-proxy/`, just one hop simpler (no auth layer needed
here; the real app already has its own via Discord login + the per-account budget):

```
Browser --https--> Render (proxy_app.py)
                        --https--> Cloudflare edge --tunnel--> cloudflared (this machine)
                                                                    |
                                                                    v
                                                      server.py (waitress, 127.0.0.1:5000)
                                                          - the real app, real butterfly.db
```

Cloudflare Tunnel is outbound-only from this machine - no inbound port ever opens on your router.
Render's pass-through (not players' browsers) calls the resulting public HTTPS URL
server-to-server.

## One-time setup

1. Make sure `cloudflared` is installed (already true if you've used `local-ollama-proxy/` - this
   reuses the same install, no second install needed).
2. Install `waitress` if you haven't already (it's in the repo's root `requirements.txt`):
   ```
   pip install -r requirements.txt
   ```
   `waitress` is a pure-Python production WSGI server - used here specifically because **gunicorn
   doesn't run on Windows** (it depends on `fcntl`, a Unix-only module). Render still uses gunicorn
   for the pass-through (that half runs on Render's own Linux infrastructure); this machine uses
   waitress for the real app instead. Never run the public-facing instance via plain
   `python server.py` - that starts Flask's own dev server, which defaults `FLASK_DEBUG` on and
   would expose Werkzeug's interactive debugger (arbitrary code execution if anything throws) to the
   whole internet the moment a request errors. Serving `server:app` through a real WSGI server
   (waitress or gunicorn) never invokes that debug-mode code path at all, regardless of the
   `FLASK_DEBUG` env var, so this is the actually-safe way to expose it publicly.
3. On Render's dashboard (`butterfly` service):
   - Change **Start Command** to `gunicorn proxy_app:app --timeout 900 --worker-class gthread
     --threads 4` (matches the updated `render.yaml`/`Procfile` in this repo - re-syncing the
     blueprint should also pick this up, but a manual dashboard change works too and is more likely
     to actually take effect on an already-running service).
   - Add environment variable `LOCAL_TUNNEL_URL` (blank for now - filled in every time you start the
     tunnel, step 2 below).
   - The existing `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`DISCORD_*`/`OLLAMA_TUNNEL_*` env vars on
     Render are no longer used by the pass-through (it does no LLM calls or DB access of its own) -
     `render.yaml` now keeps them commented-out rather than deleting them, so switching back to
     Render-hosted is just uncommenting + restoring the old start command, not re-entering every
     secret from scratch. The real app now reads all of these from **this machine's** `.env`
     instead (see repo root `.env.example`).

## Every time you want the live site backed by this machine

1. Make sure this machine's `.env` (repo root, not this folder) has real values - `FLASK_SECRET_KEY`
   especially, so logins survive a restart of the app; Discord OAuth vars if you want Discord login;
   `OLLAMA_TUNNEL_URL`/`OLLAMA_TUNNEL_SECRET` if you want the free local model available too (see
   "Note on the local model" below).
2. Run `start_local_site.ps1` in this folder (double-click it, or
   `powershell -File start_local_site.ps1`). This opens two windows: the app (via waitress), and a
   Cloudflare quick tunnel pointed at it.
3. **Copy the tunnel's public URL** into Render's `LOCAL_TUNNEL_URL` environment variable, then
   save/redeploy on Render's side so it picks up the change.

**The tunnel URL changes every single time you run step 2** - same ephemeral-quick-tunnel tradeoff
as `local-ollama-proxy/`. If this machine restarts, the script restarts, or the tunnel drops, you'll
get a new URL and need to update Render's `LOCAL_TUNNEL_URL` again. A named/persistent Cloudflare
tunnel (needs a registered domain) would remove this step but is out of scope for now, same call as
the Ollama tunnel.

**If this machine is off or asleep**, `proxy_app.py` answers Render's public URL with a clear 503
instead of a broken or blank response - the site is only fully live while this machine is on and
awake, an accepted tradeoff (same as the free local model's availability).

## Note on the local model, now that the whole app runs here too

`OllamaTunnelProvider` still talks to Ollama via the public `OLLAMA_TUNNEL_URL` (out through
Cloudflare and back), even though, once the real app is running on this same machine, that's a
same-machine call taking an unnecessary round trip through the public internet. It still works
correctly as-is - just flagging as an easy future simplification (point `OLLAMA_TUNNEL_URL` at the
local proxy's own `http://127.0.0.1:11435` once the app itself is local), not done now since it's
outside this change's scope and nothing is actually broken by leaving it as-is.

## What this does NOT change

- `butterfly.db` now lives on this machine's disk (repo root), not Render's - genuinely persistent
  (a normal local file), but only as durable as this machine's own disk. Back it up if you care about
  not losing it (see `ROADMAP.md`'s "Backups/export" item - still not built).
- Local/Discord account separation, the $0.10 budget, Council Mode, the free local model option -
  none of that logic changes; it's the exact same `server.py` running in a different place.
- Render's env vars for the real app (API keys etc.) are unused while the pass-through is deployed,
  but left in place (commented in `render.yaml`) so switching back is just a Start Command change,
  not a re-entry of every secret.

**Anyone who visited before this change and had a character under Render's old (now-abandoned)
`butterfly.db`** loses access to it - that database is no longer read by anything. Given the known
wipe-on-redeploy bug, it's unlikely much survived there anyway; flagging so it isn't a surprise.

## Reverting to Render-hosted

Change Render's Start Command back to `gunicorn server:app --timeout 900 --worker-class gthread
--threads 4`, uncomment the env vars in `render.yaml` (or re-enter them in the dashboard) and
re-sync, and stop running the local tunnel. Note this goes back to ephemeral storage - the reason
this change exists in the first place.
