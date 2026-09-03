# Starts the Discord bot (run_discord_bot.py) in its own always-on window - same pattern as
# local-site-tunnel/start_local_site.ps1 (a dedicated visible window per process, not a background
# service). Separate process from the web app (waitress) entirely: run_discord_bot.py talks to
# Discord's gateway and reads/writes the SAME butterfly.db file server.py does, but neither process
# imports the other - killing/restarting one never affects the other.
#
# Run this whenever you want the bot actually connected and answering in Discord. If it isn't
# running, the website itself is completely unaffected - multiplayer just has no Discord presence
# until this starts (see NOTES.md's Discord bot entries for the full design).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path ".env")) {
    Write-Host "No .env found - copy .env.example to .env and fill in DISCORD_BOT_TOKEN / DISCORD_APPLICATION_ID" -ForegroundColor Red
    Write-Host "(Developer Portal -> your app -> Bot page for the token) before running this." -ForegroundColor Red
    exit 1
}

Write-Host "Starting the Forking Paths Discord bot..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; python run_discord_bot.py"

Write-Host ""
Write-Host "A new window should now be open, showing the bot's own log - watch it for 'Ready as ...'" -ForegroundColor Green
Write-Host "and any warnings. Closing that window stops the bot; the website keeps running either way." -ForegroundColor Green
