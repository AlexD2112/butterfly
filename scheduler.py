"""
Real scheduled background ticks for multiplayer progression - the piece room-conclusion's "daily
sweep" needed but didn't have (2026-08-21). Explicitly NOT a lazy/request-triggered check: you asked
for a real background scheduler specifically so a random player's request never pays the latency of
tick processing (a sweep can mean one AI call per occupied location, server-wide). This module runs
entirely on its own thread via APScheduler's BackgroundScheduler - no request ever touches it.

Design: one recurring "heartbeat" job (every HEARTBEAT_INTERVAL_SECONDS) checks every multiplayer
server's (now - last_tick_at) against its own progression_rate_hours, and fires a real tick for any
server that's due. This is simpler and more robust than scheduling one independent job per server
(which would need dynamic add/remove as servers are created) - the heartbeat interval bounds how late
a tick can fire relative to when it was actually due (up to ~HEARTBEAT_INTERVAL_SECONDS late), which
is a fine tradeoff for day/week/month-scale progression, not a precision timer.

Gated behind ENABLE_TICK_SCHEDULER (see .env.example), off by default - importing server.py (which
every test does) must never silently start a background thread. Only start_scheduler() being called
explicitly turns this on; nothing here runs just from being imported.
"""

import os
import time

import db
import multiplayer
import providers

HEARTBEAT_INTERVAL_SECONDS = 60

_scheduler = None  # module-level singleton - start_scheduler() is idempotent, see below


def _judge_provider_and_model():
    """Same fixed cheap judge model everything else in multiplayer.py uses - a tick's sweep is an
    ambient, no-particular-player-in-control decision, so there's no "sender's chosen model" to defer
    to the way message/action resolution has. Falls back to None if that provider isn't configured at
    all (e.g. no OPENAI_API_KEY on this deployment) - the sweep for that server is just skipped for
    this tick rather than crashing the whole heartbeat over one unconfigured provider."""
    try:
        return providers.get_provider(multiplayer.JUDGE_MODEL_KEY)
    except providers.ProviderError:
        return None, None


def run_tick_heartbeat():
    """Checks every multiplayer server and fires a real tick for any that are due. This is the
    function APScheduler actually calls on its own thread - safe to call directly too (e.g. for
    tests), since it has no Flask/request dependency at all."""
    now = time.time()
    for server in db.list_multiplayer_servers():
        last_tick_at = server.get("last_tick_at") or now
        due_after_seconds = server["progression_rate_hours"] * 3600
        if (now - last_tick_at) < due_after_seconds:
            continue
        fire_tick(server["id"])


def fire_tick(server_id):
    """Advances one server's shared world-clock by one progression_speed unit, runs the daily
    conclusion sweep across every occupied location (which may itself generate rumors for whatever
    it concludes), then compiles and publishes any rumors accumulated since the last tick as one
    server-wide broadcast. Billing for every ambient AI call here goes through the same
    cost-split model storm-out (and now the core gameplay loop) uses (multiplayer.bill_shared_usage)."""
    server = db.get_multiplayer_server(server_id)
    if server is None:
        return
    now = time.time()
    new_date = multiplayer.advance_date_string(server["current_date"], server["progression_speed"])
    db.set_server_tick_state(server_id, new_date, now)

    provider, model = _judge_provider_and_model()
    if provider is None:
        return  # no configured judge provider - date still advanced, sweep/rumors just skipped this tick

    for location_result in multiplayer.sweep_server(provider, model, server_id):
        multiplayer.bill_shared_usage(location_result["usage_sink"], location_result["present_character_ids"])

    # The broadcast benefits everyone currently on the server, not one location - billed across
    # everyone present server-wide, same 0.5x-shared/50%-subsidized model as everything ambient.
    broadcast_usage_sink = []
    multiplayer.compile_and_publish_rumors(provider, model, server_id, usage_sink=broadcast_usage_sink)
    multiplayer.bill_shared_usage(broadcast_usage_sink, db.characters_in_server(server_id))


def start_scheduler():
    """Starts the background heartbeat job. Idempotent - calling this more than once (e.g. if
    server.py's guard were ever accidentally hit twice) just returns the existing scheduler rather
    than starting a second one. Only ever called when ENABLE_TICK_SCHEDULER=1 - see server.py."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(run_tick_heartbeat, "interval", seconds=HEARTBEAT_INTERVAL_SECONDS, id="tick_heartbeat")
    _scheduler.start()
    return _scheduler


def is_enabled():
    return os.environ.get("ENABLE_TICK_SCHEDULER") == "1"
