# Local site hosting + a permanent domain

Moves the real Butterfly app (`server.py`, `butterfly.db`) off Render's free tier and onto this
machine, so SQLite data stops getting wiped on every Render redeploy (Render's free web services
have no persistent disk - confirmed by evidence in request logs: character IDs kept resetting to 1
across sessions). The real, permanent public address is now **https://forkingpaths.app**, served by
a NAMED Cloudflare Tunnel (not the free quick-tunnel this setup originally used - see "From quick
tunnel to a named tunnel" below). Render still answers the old
`https://butterfly-haa9.onrender.com` URL, but only as a redirect shim (`proxy_app.py` at the repo
root) pointing old links at the new domain - see that file's own docstring and `NOTES.md` for the
full reasoning.

**Architecture:**

```
Browser --https--> Cloudflare edge (forkingpaths.app) --tunnel--> cloudflared (this machine)
                                                                       |
                                                                       v
                                                         server.py (waitress, 127.0.0.1:5000)
                                                             - the real app, real butterfly.db

Browser --https--> Render (proxy_app.py, butterfly-haa9.onrender.com)
                        --301 redirect--> https://forkingpaths.app/<same path+query>
```

Cloudflare Tunnel is outbound-only from this machine - no inbound port ever opens on your router.
The browser talks to `forkingpaths.app` directly now; Render is no longer in the request path at
all for anyone using the current domain, only for old links.

## One-time setup (already done on this machine - kept here for reference/rebuilding elsewhere)

1. Install `cloudflared` (already true if you've used `local-ollama-proxy/` - reuses the same
   install).
2. Install `waitress` (in the repo's root `requirements.txt`):
   ```
   pip install -r requirements.txt
   ```
   `waitress` is a pure-Python production WSGI server - used specifically because **gunicorn doesn't
   run on Windows** (it depends on `fcntl`, a Unix-only module). Render's redirect shim still uses
   gunicorn (that runs on Render's own Linux infrastructure); this machine uses waitress for the real
   app instead. Never run the public-facing instance via plain `python server.py` - that starts
   Flask's own dev server, which defaults `FLASK_DEBUG` on and would expose Werkzeug's interactive
   debugger (arbitrary code execution if anything throws) to the whole internet the moment a request
   errors. Serving `server:app` through a real WSGI server (waitress or gunicorn) never invokes that
   debug-mode code path at all, regardless of the `FLASK_DEBUG` env var, so this is the actually-safe
   way to expose it publicly.
3. Buy/own a domain and add it to a Cloudflare account (buying directly through Cloudflare Registrar
   skips a nameserver-propagation wait that buying elsewhere and adding it afterward doesn't - see
   NOTES.md).
4. `cloudflared tunnel login` - the ONE interactive step in this whole setup. Opens a browser, you
   log into Cloudflare and authorize, which writes `~/.cloudflared/cert.pem`. Everything after this
   is non-interactive.
5. `cloudflared tunnel create butterfly` - creates the named tunnel (id
   `bf9a4147-6252-4b3e-8911-1dfe7d9257db`) and its credentials file.
6. `~/.cloudflared/config.yml` (NOT in this repo - host-machine config, not app code):
   ```yaml
   tunnel: bf9a4147-6252-4b3e-8911-1dfe7d9257db
   credentials-file: C:\Users\atdan\.cloudflared\bf9a4147-6252-4b3e-8911-1dfe7d9257db.json

   ingress:
     - hostname: forkingpaths.app
       service: http://localhost:5000
     - hostname: www.forkingpaths.app
       service: http://localhost:5000
     - service: http_status:404
   ```
7. `cloudflared tunnel route dns butterfly forkingpaths.app` and again for
   `www.forkingpaths.app` - creates the CNAME records pointing the domain at the tunnel.
8. On Render's dashboard (`butterfly` service): Start Command is `gunicorn proxy_app:app --timeout
   900 --worker-class gthread --threads 4` (see `render.yaml`/`Procfile`) - no env vars needed
   anymore (the old `LOCAL_TUNNEL_URL` is gone, see below).

## Every time you want the live site backed by this machine

1. Make sure this machine's `.env` (repo root, not this folder) has real values -
   `FLASK_SECRET_KEY` especially, so logins survive a restart; Discord OAuth vars if you want Discord
   login; `OLLAMA_TUNNEL_URL`/`OLLAMA_TUNNEL_SECRET` if you want the free local model available too
   (see "Note on the local model" below).

   **`DISCORD_REDIRECT_URI` is deliberately still `https://butterfly-haa9.onrender.com/auth/discord/callback`**
   - NOT `https://forkingpaths.app/auth/discord/callback` - even though the real app runs at the new
     domain now. This isn't a bug: Discord's OAuth2 app only has the OLD address registered as a
     valid redirect right now, and `proxy_app.py`'s redirect preserves the full path+query, so the
     login flow still completes correctly through that one extra hop (see `proxy_app.py`'s own
     docstring for exactly why this works). To fully retire this dependency, add
     `https://forkingpaths.app/auth/discord/callback` as an additional registered redirect in
     Discord's Developer Portal (requires the account owner's own Discord login - not something
     done on anyone's behalf) and then switch `DISCORD_REDIRECT_URI` in `.env` - not done yet, not
     urgent, tracked in NOTES.md.
2. Run `start_local_site.ps1` in this folder (double-click it, or
   `powershell -File start_local_site.ps1`). This opens two windows: the app (via waitress), and the
   named Cloudflare tunnel.
3. That's it. `https://forkingpaths.app` is live - **nothing to copy-paste anywhere**, unlike the old
   quick-tunnel setup (see below). If this machine restarts, the script restarts, or the tunnel
   drops, restarting `start_local_site.ps1` reconnects the SAME tunnel to the SAME domain
   automatically.

**If this machine is off or asleep**, the domain simply stops resolving to anything live - an
accepted tradeoff (same as the free local model's availability) until/unless the site moves to
always-on hosting (see NOTES.md's migration-scoping entry).

## From quick tunnel to a named tunnel (2026-09-02)

This setup originally used a Cloudflare **quick** tunnel (`cloudflared tunnel --url ...`) - a fresh
random `*.trycloudflare.com` URL every time the script ran, requiring a manual copy-paste of that
URL into Render's `LOCAL_TUNNEL_URL` environment variable (and a Render redeploy) every single
restart. Once a real domain was bought, that whole category of manual step went away: a **named**
tunnel gets a permanent hostname (`forkingpaths.app`) that survives restarts, machine reboots, and
tunnel reconnects without ever needing to touch Render again. `LOCAL_TUNNEL_URL` no longer exists as
a concept - Render's service needs zero environment variables now.

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
- Render's env vars for the real app (API keys etc.) are unused while the redirect shim is deployed,
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
