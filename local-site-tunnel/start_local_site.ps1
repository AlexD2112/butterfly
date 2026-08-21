# Starts the real Butterfly app (server.py) via waitress - NOT `python server.py`'s own dev server,
# which defaults FLASK_DEBUG on and would expose Werkzeug's interactive debugger (an RCE risk) to
# the public internet the moment a request throws, and NOT gunicorn (Linux-only, depends on fcntl -
# that's what Render's proxy_app.py uses instead, on Render's own Linux infrastructure) - and a
# Cloudflare quick tunnel pointed at it, each in its own visible window. Mirrors
# local-ollama-proxy/start_local_llm.ps1's pattern.
#
# Run this instead of `python server.py` whenever you want the live site backed by this machine.
# After running, copy the "https://....trycloudflare.com" URL cloudflared prints into Render's
# LOCAL_TUNNEL_URL environment variable (Render dashboard -> butterfly service -> Environment), on
# the service whose Start Command has been switched to `gunicorn proxy_app:app` - see README.md in
# this folder. The URL is different every time this script runs; there's no fixed address with a
# free Cloudflare quick tunnel.
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
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$repoRoot'; waitress-serve --host=127.0.0.1 --port=$port --threads=8 server:app"

Start-Sleep -Seconds 3

Write-Host "Starting the Cloudflare quick tunnel - watch this window for the public URL." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$cloudflared' tunnel --url http://localhost:$port"

Write-Host ""
Write-Host "Two windows should now be open: the app (waitress), and the tunnel." -ForegroundColor Green
Write-Host "Copy the https://....trycloudflare.com URL from the tunnel window into Render's" -ForegroundColor Green
Write-Host "LOCAL_TUNNEL_URL environment variable, on the service running proxy_app.py." -ForegroundColor Green
Write-Host ""
Write-Host "Butterfly is also reachable directly at http://localhost:$port on this machine right now," -ForegroundColor Green
Write-Host "without waiting on the tunnel or Render at all." -ForegroundColor Green
