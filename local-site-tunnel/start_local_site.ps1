# Starts Butterfly's web app (server.py) locally via waitress - for LOCAL DEVELOPMENT/TESTING only.
#
# 2026-09-09: Full hosting migration off this machine is complete (see NOTES.md and render.yaml).
# Render (https://forkingpaths.app) now runs server.py directly via gunicorn, with the Discord bot
# embedded in that same process (DISCORD_BOT_EMBEDDED=1) and the tick scheduler turned on
# (ENABLE_TICK_SCHEDULER=1) - none of it depends on this machine being on anymore. DNS
# (forkingpaths.app and www) points at Render, not at any tunnel from here. This script is no longer
# part of the production path - it exists purely so you can run/test the app locally against your own
# dev copy of butterfly.db.
#
# By default this ONLY starts the local web app (waitress on localhost). It deliberately does NOT
# start the Cloudflare tunnel or the Discord bot anymore:
#   - The tunnel: DNS doesn't point at it, so it wouldn't receive real traffic anyway - starting it
#     would just be a dangling process for no benefit while Render is live.
#   - The bot: Render's embedded bot is already the one live connection on DISCORD_BOT_TOKEN. Starting
#     a second one locally at the same time creates two simultaneous Discord gateway connections on
#     the same token - a real bug that's been hit more than once during the migration. See
#     run_discord_bot.py's own module docstring (DISCORD_BOT_EMBEDDED) for the full explanation.
# Pass -WithTunnel and/or -WithBot if you've deliberately taken this machine out of dev-only mode (e.g.
# testing the full stack with Render's equivalent piece intentionally stopped first).
#
# Not `python server.py`'s own dev server, which defaults FLASK_DEBUG on and would expose Werkzeug's
# interactive debugger (an RCE risk) the moment a request throws, and not gunicorn (Linux-only,
# depends on fcntl - that's what Render uses on its own Linux infrastructure). Mirrors
# local-ollama-proxy/start_local_llm.ps1's pattern.

param(
    [switch]$WithTunnel,
    [switch]$WithBot
)

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

$port = 5000
if ($env:PORT) { $port = $env:PORT }

Write-Host "Starting Butterfly (server.py) via waitress on port $port - LOCAL DEV ONLY, Render is production." -ForegroundColor Cyan
# `python -m waitress`, not the `waitress-serve` console script - pip installs waitress-serve into
# Python's Scripts folder, which isn't reliably on PATH (confirmed: it wasn't on this machine's,
# producing "waitress-serve is not recognized"). `python -m waitress` takes identical arguments and
# only depends on `python` itself being on PATH, which the .env-creation step above already assumes.
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$repoRoot'; python -m waitress --host=127.0.0.1 --port=$port --threads=8 server:app"

if ($WithTunnel) {
    $cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if (-not (Test-Path $cloudflared)) {
        Write-Host "cloudflared not found at $cloudflared - check your install location and update this script." -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 3
    Write-Host "Starting the named Cloudflare tunnel (forkingpaths.app) - watch this window for connection status." -ForegroundColor Cyan
    Write-Host "NOTE: DNS points at Render right now, so this tunnel won't actually receive forkingpaths.app traffic" -ForegroundColor Yellow
    Write-Host "unless you've repointed DNS back at it." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$cloudflared' tunnel run butterfly"
} else {
    Write-Host "Skipping the Cloudflare tunnel (Render is the real production host now). Pass -WithTunnel to start it anyway." -ForegroundColor DarkGray
}

if ($WithBot) {
    Write-Host "Starting the Discord bot locally..." -ForegroundColor Cyan
    Write-Host "WARNING: if Render's embedded bot is still live, this creates a SECOND simultaneous gateway" -ForegroundColor Yellow
    Write-Host "connection on the same DISCORD_BOT_TOKEN. Stop it on Render first (or set DISCORD_BOT_TOKEN blank" -ForegroundColor Yellow
    Write-Host "here) unless you mean to run both." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$repoRoot'; python run_discord_bot.py"
} else {
    Write-Host "Skipping the Discord bot (Render runs it embedded now). Pass -WithBot to start a local one too." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Butterfly (local dev) is reachable at http://localhost:$port on this machine." -ForegroundColor Green
Write-Host "The real, always-on site is https://forkingpaths.app, served by Render - independent of this machine." -ForegroundColor Green
