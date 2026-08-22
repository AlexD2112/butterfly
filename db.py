"""
Local SQLite persistence for users, characters, and LLM usage. This is deliberately a thin,
dependency-free layer (stdlib sqlite3 only, no ORM) - the data model is small (three tables) and the
goal is to make a later swap to a hosted Postgres straightforward (see README's hosting notes)
rather than to build out a general-purpose data layer now.

Two users can exist without Discord: `get_or_create_local_user()` returns a single stable "local"
account used for offline/anonymous play, so character creation and saving work today without
requiring login. Once a person logs in with Discord, their characters live under their Discord-
linked account instead - see get_or_create_discord_user().

IMPORTANT: "single stable" means literally one shared row for every anonymous visitor across the
whole deployment - discord_id is UNIQUE and the local sentinel never varies, so there is no
per-browser local identity here at all, only one global one. This is why the usage budget
(budget_usd/usage_events, see get_budget_status/record_usage below) is gated to Discord-linked
users only in server.py's turn route - a per-account budget can't mean anything against an identity
every anonymous visitor shares.

No OAuth tokens are persisted here by design - server.py only uses Discord's identity response once
at login time to find-or-create a user row, then never needs the token again.
"""

import json
import sqlite3
import time
from pathlib import Path

DB_FILE = Path(__file__).parent / "butterfly.db"

_LOCAL_USER_DISCORD_ID = "__local__"  # sentinel, never a real Discord snowflake

DEFAULT_BUDGET_USD = 0.10  # starting LLM usage budget granted to every new account


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT UNIQUE NOT NULL,
            username TEXT NOT NULL,
            avatar_url TEXT,
            is_local INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            setting_key TEXT NOT NULL,
            system_key TEXT NOT NULL,
            name TEXT NOT NULL,
            is_premade INTEGER NOT NULL DEFAULT 0,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    # budget_usd is deliberately not in the CREATE TABLE above, so both a brand-new DB and one
    # created before this feature existed go through the same ALTER TABLE path below. SQLite has no
    # "ADD COLUMN IF NOT EXISTS", so the OperationalError on a rerun (column already exists) is
    # expected and swallowed.
    try:
        conn.execute(f"ALTER TABLE users ADD COLUMN budget_usd REAL NOT NULL DEFAULT {DEFAULT_BUDGET_USD}")
    except sqlite3.OperationalError:
        pass  # column already exists
    # is_admin - deliberately simple/low-stakes (see server.py's ADMIN_UNLOCK_CODE comment): no
    # admin-gated functionality carries real security stakes yet, this just needs to persist across
    # logins so the code doesn't need re-entering every session.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            character_id INTEGER REFERENCES characters(id),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    # Full per-turn transcript - deliberately separate from characters.state_json's own
    # campaign_log_summary (which only ever kept a 400-character truncated snippet of narration,
    # no player input, no mechanical detail). Before this table existed, none of that data survived
    # past the single API response it was generated for - debug_events was only ever returned to the
    # frontend for that one request, and the raw LLM conversation only lived in server.py's in-memory
    # _conversations dict, wiped on every restart. This table is what finally makes a full game
    # transcript reviewable after the fact - see view_transcript.py.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL REFERENCES characters(id),
            turn_number INTEGER NOT NULL,
            player_input TEXT NOT NULL,
            narration TEXT NOT NULL,
            model_key TEXT,
            debug_events_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    # games - the top-level playthrough container multiplayer needs (2026-08-21): a single-player
    # game has exactly one character; a multiplayer game has many, one per participating player, all
    # tied to the same multiplayer_server_id (that table doesn't exist yet - added once the
    # multiplayer-server feature itself is built; NULL here always means single-player for now).
    # "Character log" is deliberately just the roster (which characters belong to this game) - the
    # existing `turns` table is already the message/action log this was meant to avoid duplicating.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL DEFAULT 'single',
            setting_key TEXT NOT NULL,
            system_key TEXT NOT NULL,
            multiplayer_server_id INTEGER,
            created_at REAL NOT NULL
        )
        """
    )
    try:
        conn.execute("ALTER TABLE characters ADD COLUMN game_id INTEGER REFERENCES games(id)")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE turns ADD COLUMN game_id INTEGER REFERENCES games(id)")
    except sqlite3.OperationalError:
        pass  # column already exists
    _backfill_games(conn)
    conn.commit()
    conn.close()


def _backfill_games(conn):
    """Every character that predates the `games` table gets a fresh game_id backfilled (mode=single,
    one game per pre-existing character - a game and a character were the same thing before
    multiplayer existed, so this preserves that 1:1 relationship exactly, losing nothing). Every turn
    belonging to a freshly-backfilled character gets the same game_id. Idempotent by construction -
    only ever touches rows where game_id IS NULL, so calling this on every init_db() (every process
    start) is safe and cheap once a database is fully migrated (a no-op query, not a real migration
    each time)."""
    orphaned = conn.execute("SELECT id, setting_key, system_key FROM characters WHERE game_id IS NULL").fetchall()
    for char in orphaned:
        cur = conn.execute(
            "INSERT INTO games (mode, setting_key, system_key, multiplayer_server_id, created_at) "
            "VALUES ('single', ?, ?, NULL, ?)",
            (char["setting_key"], char["system_key"], time.time()),
        )
        game_id = cur.lastrowid
        conn.execute("UPDATE characters SET game_id = ? WHERE id = ?", (game_id, char["id"]))
        conn.execute("UPDATE turns SET game_id = ? WHERE character_id = ? AND game_id IS NULL", (game_id, char["id"]))


def set_is_admin(user_id, value):
    """See server.py's ADMIN_UNLOCK_CODE - deliberately simple, no real security hardening intended
    yet. NOTE: for a local/anonymous user this grants admin to the single shared row every anonymous
    visitor deployment-wide uses (see this module's own docstring) - meaning every other unauthenticated
    visitor becomes admin too, not just the one who entered the code. Harmless while nothing
    admin-gated exists yet, but worth knowing before this code is shared casually."""
    conn = get_db()
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if value else 0, user_id))
    conn.commit()
    conn.close()


def get_or_create_local_user():
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (_LOCAL_USER_DISCORD_ID,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (discord_id, username, avatar_url, is_local, created_at) VALUES (?, ?, ?, 1, ?)",
            (_LOCAL_USER_DISCORD_ID, "Local Player", None, time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (_LOCAL_USER_DISCORD_ID,)).fetchone()
    conn.close()
    return dict(row)


def get_or_create_discord_user(discord_id, username, avatar_url):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (discord_id, username, avatar_url, is_local, created_at) VALUES (?, ?, ?, 0, ?)",
            (discord_id, username, avatar_url, time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    else:
        # keep username/avatar fresh on repeat logins
        conn.execute("UPDATE users SET username = ?, avatar_url = ? WHERE id = ?", (username, avatar_url, row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    conn.close()
    return dict(row)


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_characters(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, setting_key, system_key, name, is_premade, created_at, updated_at FROM characters "
        "WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_character(user_id, setting_key, system_key, name, state, is_premade=False):
    """Every character created through this path is (for now) automatically a single-player game -
    multiplayer games get created directly via create_game() once that feature exists, with
    characters attached to an already-existing game_id instead."""
    now = time.time()
    conn = get_db()
    game_cur = conn.execute(
        "INSERT INTO games (mode, setting_key, system_key, multiplayer_server_id, created_at) "
        "VALUES ('single', ?, ?, NULL, ?)",
        (setting_key, system_key, now),
    )
    game_id = game_cur.lastrowid
    cur = conn.execute(
        "INSERT INTO characters (user_id, setting_key, system_key, name, is_premade, state_json, game_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, setting_key, system_key, name, 1 if is_premade else 0, json.dumps(state), game_id, now, now),
    )
    conn.commit()
    character_id = cur.lastrowid
    conn.close()
    return character_id


def create_game(mode, setting_key, system_key, multiplayer_server_id=None):
    """Direct game creation, for multiplayer (a game with many characters, all attached to the same
    game_id after the fact) - single-player games are still created inline by create_character()
    above, not through this function, since a single-player game and its one character are always
    created together in one step today."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO games (mode, setting_key, system_key, multiplayer_server_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (mode, setting_key, system_key, multiplayer_server_id, time.time()),
    )
    conn.commit()
    game_id = cur.lastrowid
    conn.close()
    return game_id


def get_game(game_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_game_characters(game_id):
    """The 'character log' for a game - which characters belong to it. See games table comment."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM characters WHERE game_id = ?", (game_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_character(character_id, user_id):
    """Returns the full row (with parsed state) if it exists and belongs to user_id, else None -
    ownership is always checked here rather than trusting a bare character_id from the client."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM characters WHERE id = ? AND user_id = ?", (character_id, user_id)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    out = dict(row)
    out["state"] = json.loads(out.pop("state_json"))
    return out


def save_character_state(character_id, user_id, state):
    conn = get_db()
    cur = conn.execute(
        "UPDATE characters SET state_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (json.dumps(state), time.time(), character_id, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def get_budget_status(user_id):
    """Returns {"budget_usd", "spent_usd", "remaining_usd"} for a user. spent_usd is a live SUM
    over usage_events rather than a separately-maintained running counter, so there's exactly one
    source of truth for spend - no risk of the two drifting out of sync."""
    conn = get_db()
    user_row = conn.execute("SELECT budget_usd FROM users WHERE id = ?", (user_id,)).fetchone()
    spent_row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM usage_events WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    budget = user_row["budget_usd"] if user_row else DEFAULT_BUDGET_USD
    spent = spent_row["spent"]
    return {"budget_usd": budget, "spent_usd": spent, "remaining_usd": max(0.0, budget - spent)}


def record_usage(user_id, character_id, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd):
    conn = get_db()
    conn.execute(
        "INSERT INTO usage_events (user_id, character_id, provider, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            character_id,
            provider,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            cost_usd,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def record_turn(character_id, turn_number, player_input, narration, model_key, debug_events):
    """Saves one turn's full, untruncated record - see the `turns` table's comment in init_db() for
    why this exists. debug_events is stored as JSON exactly as engine.run_turn produced it (dice
    rolls, state changes, canon events, and the Council Mode log if it ran) - the same data the
    frontend's per-turn Mechanics panel shows, just persisted instead of discarded after one
    response. Called from server.py's turn route only on a successful turn, alongside
    save_character_state - a failed/errored turn (caught earlier in that route) never reaches here,
    matching how campaign_log_summary already only records completed turns."""
    conn = get_db()
    char_row = conn.execute("SELECT game_id FROM characters WHERE id = ?", (character_id,)).fetchone()
    game_id = char_row["game_id"] if char_row else None
    conn.execute(
        "INSERT INTO turns (character_id, turn_number, player_input, narration, model_key, "
        "debug_events_json, game_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (character_id, turn_number, player_input, narration, model_key, json.dumps(debug_events), game_id, time.time()),
    )
    conn.commit()
    conn.close()


def get_turns(character_id):
    """Every recorded turn for one character, oldest first - used by view_transcript.py."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM turns WHERE character_id = ? ORDER BY turn_number ASC, id ASC", (character_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_characters_with_turn_counts():
    """Every character across every account, with how many turns have been recorded for it - used by
    view_transcript.py's --list mode to find a character_id worth looking at without needing to
    inspect the database by hand first."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.setting_key, c.updated_at, u.username, u.is_local,
               (SELECT COUNT(*) FROM turns t WHERE t.character_id = c.id) AS turn_count
        FROM characters c JOIN users u ON u.id = c.user_id
        ORDER BY c.updated_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
