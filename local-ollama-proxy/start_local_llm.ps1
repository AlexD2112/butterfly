# Starts the auth/rate-limit proxy and a Cloudflare quick tunnel pointed at it, each in its own
# visible window so you can see the proxy's logs and (importantly) the tunnel's public URL, which
# changes every time this script runs - a quick tunnel has no fixed address.
#
# Run this after Ollama is already running (Ollama's own tray app/service handles that separately -
# this script does not start Ollama itself).
#
# After running, copy the "https://....trycloudflare.com" URL cloudflared prints into Render's
# OLLAMA_TUNNEL_URL environment variable. It will NOT match any URL from a previous run.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path ".env")) {
    Write-Host "No .env found - copy .env.example to .env and set PROXY_SHARED_SECRET first." -ForegroundColor Red
    exit 1
}

$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    Write-Host "cloudflared not found at $cloudflared - check your install location and update this script." -ForegroundColor Red
    exit 1
}

# Read PROXY_PORT from .env (defaults to 11435, matching proxy.py's own default) so the tunnel
# points at whatever port the proxy is actually configured to use.
$proxyPort = 11435
$envLine = Get-Content ".env" | Where-Object { $_ -match "^PROXY_PORT=(\d+)" }
if ($envLine) { $proxyPort = $Matches[1] }

Write-Host "Starting the local auth/rate-limit proxy on port $proxyPort..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; python proxy.py"

Start-Sleep -Seconds 3

Write-Host "Starting the Cloudflare quick tunnel - watch this window for the public URL." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$cloudflared' tunnel --url http://localhost:$proxyPort"

Write-Host ""
Write-Host "Two windows should now be open: the proxy, and the tunnel." -ForegroundColor Green
Write-Host "Copy the https://....trycloudflare.com URL from the tunnel window into Render's" -ForegroundColor Green
Write-Host "OLLAMA_TUNNEL_URL environment variable - it's different every time this script runs." -ForegroundColor Green
