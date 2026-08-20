"""
Local SQLite persistence for users and characters. This is deliberately a thin, dependency-free
layer (stdlib sqlite3 only, no ORM) - the data model is small (two tables) and the goal is to make
a later swap to a hosted Postgres straightforward (see README's hosting notes) rather than to build
out a general-purpose data layer now.

Two users can exist without Discord: `get_or_create_local_user()` returns a single stable "local"
account used for offline/anonymous play, so character creation and saving work today without
requiring login. Once a person logs in with Discord, their characters live under their Discord-
linked account instead - see get_or_create_discord_user().

No OAuth tokens are persisted here by design - server.py only uses Discord's identity response once
at login time to find-or-create a user row, then never needs the token again.
"""

import json
import sqlite3
import time
from pathlib import Path

DB_FILE = Path(__file__).parent / "butterfly.db"

_LOCAL_USER_DISCORD_ID = "__local__"  # sentinel, never a real Discord snowflake


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
    now = time.time()
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO characters (user_id, setting_key, system_key, name, is_premade, state_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, setting_key, system_key, name, 1 if is_premade else 0, json.dumps(state), now, now),
    )
    conn.commit()
    character_id = cur.lastrowid
    conn.close()
    return character_id


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
