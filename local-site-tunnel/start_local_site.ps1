# Starts the real Butterfly app (server.py) via waitress - NOT `python server.py`'s own dev server,
# which defaults FLASK_DEBUG on and would expose Werkzeug's interactive debugger (an RCE risk) to
# the public internet the moment a request throws, and NOT gunicorn (Linux-only, depends on fcntl -
# that's what Render's proxy_app.py used instead, on Render's own Linux infrastructure, back when
# Render was in the loop at all - see below) - and a NAMED Cloudflare tunnel pointed at it, each in
# its own visible window. Mirrors local-ollama-proxy/start_local_llm.ps1's pattern.
#
# Run this instead of `python server.py` whenever you want the live site backed by this machine.
#
# 2026-09-02: switched from a Cloudflare QUICK tunnel (`cloudflared tunnel --url ...`, a fresh
# ephemeral trycloudflare.com URL every run, requiring a manual copy-paste into Render's
# LOCAL_TUNNEL_URL env var each time) to a NAMED tunnel ("butterfly", id
# bf9a4147-6252-4b3e-8911-1dfe7d9257db) permanently routed to https://forkingpaths.app and
# https://www.forkingpaths.app via `cloudflared tunnel route dns` - a fixed hostname that never
# changes across restarts. This needed a one-time `cloudflared tunnel login` (the only step that
# ever requires a human at a real browser - produces ~/.cloudflared/cert.pem) and `cloudflared
# tunnel create butterfly`, both already done; this script just runs the tunnel that already
# exists. The ingress config (which hostnames route to localhost:$port) lives in the default
# location, ~/.cloudflared/config.yml, not in this repo - it's host-machine config, not app code.
#
# Render (proxy_app.py) is RETIRED from the real hosting path now that the tunnel itself has a
# permanent address - it added an extra network hop, a request-timeout ceiling, and the manual
# LOCAL_TUNNEL_URL update this script used to remind you about, all to solve a problem (the quick
# tunnel's URL changing every restart) that a named tunnel no longer has. The old
# butterfly-haa9.onrender.com URL now 301-redirects to forkingpaths.app (proxy_app.py rewritten to
# just do that) rather than being decommissioned outright, so existing bookmarks/links don't break.
#
# butterfly.db lives on this machine's own disk from here on, so it persists normally across
# restarts of this script - no more resets on every Render redeploy (see NOTES.md).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $here
Set-Location $repoRoot

if (-not (Test-Path ".env")) {
    Write-Host "No .env found - creating one from .env.example with a freshly generated FLASK_SECRET_KEY" -ForegroundColor Yellow
    Write-Host "(a stable secret is what lets sessions/logins survive restarts once this app is hosted persistently)." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    $secret = python -c "import secrets; print(secrets.token_hex(32))"
    (Get-Content ".env") -replace '^FLASK_SECRET_KEY=$', "FLASK_SECRET_KEY=$secret" | Set-Content ".env"
    Write-Host "Created .env. Still fill in ANTHROPIC_API_KEY / OPENAI_API_KEY / DISCORD_* yourself" -ForegroundColor Yellow
    Write-Host "before real players show up - see README's Setup section." -ForegroundColor Yellow
}

$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    Write-Host "cloudflared not found at $cloudflared - check your install location and update this script." -ForegroundColor Red
    exit 1
}

$port = 5000
if ($env:PORT) { $port = $env:PORT }

Write-Host "Starting Butterfly (server.py) via waitress on port $port..." -ForegroundColor Cyan
# `python -m waitress`, not the `waitress-serve` console script - pip installs waitress-serve into
# Python's Scripts folder, which isn't reliably on PATH (confirmed: it wasn't on this machine's,
# producing "waitress-serve is not recognized"). `python -m waitress` takes identical arguments and
# only depends on `python` itself being on PATH, which the .env-creation step above already assumes.
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$repoRoot'; python -m waitress --host=127.0.0.1 --port=$port --threads=8 server:app"

Start-Sleep -Seconds 3

Write-Host "Starting the named Cloudflare tunnel (forkingpaths.app) - watch this window for connection status." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$cloudflared' tunnel run butterfly"

Write-Host ""
Write-Host "Two windows should now be open: the app (waitress), and the tunnel." -ForegroundColor Green
Write-Host "The site is live at https://forkingpaths.app - that address is permanent now, nothing to" -ForegroundColor Green
Write-Host "copy-paste anywhere after a restart." -ForegroundColor Green
Write-Host ""
Write-Host "Butterfly is also reachable directly at http://localhost:$port on this machine right now," -ForegroundColor Green
Write-Host "without waiting on the tunnel at all." -ForegroundColor Green
