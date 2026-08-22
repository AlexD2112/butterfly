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

    # Multiplayer servers/rooms/locations (2026-08-21). A multiplayer_server has exactly one games
    # row (mode='multiplayer', linked both ways - games.multiplayer_server_id and this table's own
    # implicit 1:1, looked up via get_game_for_server rather than a redundant column here, avoiding a
    # circular FK at insert time). A room can have multiple locations (confirmed: multiple concurrent
    # side-conversations can run in one room) - every room gets a default "Main" location so movement
    # always has somewhere valid to land.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS multiplayer_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            system_key TEXT NOT NULL,
            progression_speed TEXT NOT NULL DEFAULT 'day',
            progression_rate_hours REAL NOT NULL DEFAULT 24,
            last_tick_at REAL,
            created_by_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL REFERENCES multiplayer_servers(id),
            name TEXT NOT NULL,
            description TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            name TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    # One row per character (character_id is the primary key) - enforces "every player is constantly
    # located in exactly one room/area at a time" structurally, not just by convention.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS room_occupancy (
            character_id INTEGER PRIMARY KEY REFERENCES characters(id),
            server_id INTEGER NOT NULL REFERENCES multiplayer_servers(id),
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            location_id INTEGER NOT NULL REFERENCES locations(id),
            joined_room_at REAL NOT NULL,
            joined_location_at REAL NOT NULL
        )
        """
    )
    # turns extended for multiplayer's shared message/action log, rather than a parallel table (see
    # the games-schema NOTES.md entry for why). All nullable/defaulted so single-player rows are
    # unaffected - kind defaults to 'action', which is retroactively correct for every row that
    # predates this column (they were all single-player actions). visible_to_json is the actual
    # visibility mechanism (see record_room_message) - a PERMANENT snapshot of who was present when a
    # message was sent, so arriving at a location later never grants retroactive access to what was
    # said before you got there.
    for col_sql in [
        "ALTER TABLE turns ADD COLUMN room_id INTEGER REFERENCES rooms(id)",
        "ALTER TABLE turns ADD COLUMN location_id INTEGER REFERENCES locations(id)",
        "ALTER TABLE turns ADD COLUMN kind TEXT NOT NULL DEFAULT 'action'",
        "ALTER TABLE turns ADD COLUMN reply_to_turn_id INTEGER REFERENCES turns(id)",
        "ALTER TABLE turns ADD COLUMN responder TEXT",
        "ALTER TABLE turns ADD COLUMN visible_to_json TEXT",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    # npcs - real character sheets (personality/memory), not just improvised each time (2026-08-21).
    # Scoped per-game (not per-setting) so a multiplayer server's NPCs are independent of any other
    # game using the same setting. Deliberately NOT auto-seeded from a setting's npcs.md - that's a
    # separate markdown-parsing feature nobody asked for yet; NPCs are created explicitly for now.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id),
            key TEXT NOT NULL,
            name TEXT NOT NULL,
            personality TEXT,
            traits_json TEXT NOT NULL DEFAULT '[]',
            memory_json TEXT NOT NULL DEFAULT '[]',
            room_id INTEGER REFERENCES rooms(id),
            location_id INTEGER REFERENCES locations(id),
            created_at REAL NOT NULL
        )
        """
    )

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


def create_character(user_id, setting_key, system_key, name, state, is_premade=False, game_id=None):
    """Every character created through this path is a single-player game by default - a fresh games
    row is created inline, same as before. Pass an existing multiplayer game_id (see
    create_multiplayer_server/get_game_for_server) to attach the character to that game instead,
    e.g. when a player joins a multiplayer server and creates a character for it."""
    now = time.time()
    conn = get_db()
    if game_id is None:
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


def get_game_for_server(server_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM games WHERE multiplayer_server_id = ?", (server_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ==================== multiplayer servers / rooms / locations ====================

def create_multiplayer_server(name, setting_key, system_key, progression_speed, progression_rate_hours, created_by_user_id):
    """Creates the server's single backing game first (multiplayer_server_id starts NULL - the
    server row doesn't exist yet to reference), then links them - avoids a circular insert between
    games and multiplayer_servers without needing a redundant game_id column on this table."""
    game_id = create_game("multiplayer", setting_key, system_key, multiplayer_server_id=None)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO multiplayer_servers (name, setting_key, system_key, progression_speed, "
        "progression_rate_hours, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, setting_key, system_key, progression_speed, progression_rate_hours, created_by_user_id, time.time()),
    )
    server_id = cur.lastrowid
    conn.execute("UPDATE games SET multiplayer_server_id = ? WHERE id = ?", (server_id, game_id))
    conn.commit()
    conn.close()
    return server_id


def get_multiplayer_server(server_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM multiplayer_servers WHERE id = ?", (server_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_multiplayer_servers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM multiplayer_servers ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_room(server_id, name, description=None):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO rooms (server_id, name, description, created_at) VALUES (?, ?, ?, ?)",
        (server_id, name, description, time.time()),
    )
    room_id = cur.lastrowid
    # Every room needs at least one location to place people in - see create_location for others.
    conn.execute("INSERT INTO locations (room_id, name, created_at) VALUES (?, 'Main', ?)", (room_id, time.time()))
    conn.commit()
    conn.close()
    return room_id


def get_room(room_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_rooms(server_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM rooms WHERE server_id = ? ORDER BY id ASC", (server_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_location(room_id, name):
    conn = get_db()
    cur = conn.execute("INSERT INTO locations (room_id, name, created_at) VALUES (?, ?, ?)", (room_id, name, time.time()))
    conn.commit()
    location_id = cur.lastrowid
    conn.close()
    return location_id


def get_location(location_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_locations(room_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM locations WHERE room_id = ? ORDER BY id ASC", (room_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_default_location(room_id):
    """The first (oldest) location in a room - always exists, since create_room() creates one
    automatically. Used when a move doesn't specify a location explicitly."""
    conn = get_db()
    row = conn.execute("SELECT * FROM locations WHERE room_id = ? ORDER BY id ASC LIMIT 1", (room_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def move_character(character_id, server_id, room_id, location_id):
    """Moves a character into a room+location. character_id is room_occupancy's primary key, so this
    structurally enforces 'exactly one room at a time' - a move always replaces wherever the
    character was, never adds a second location. joined_room_at only resets if the room actually
    changed; moving between locations within the same room doesn't count as re-entering the room."""
    conn = get_db()
    now = time.time()
    existing = conn.execute("SELECT room_id, joined_room_at FROM room_occupancy WHERE character_id = ?", (character_id,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO room_occupancy (character_id, server_id, room_id, location_id, joined_room_at, joined_location_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (character_id, server_id, room_id, location_id, now, now),
        )
    else:
        joined_room_at = now if existing["room_id"] != room_id else existing["joined_room_at"]
        conn.execute(
            "UPDATE room_occupancy SET server_id = ?, room_id = ?, location_id = ?, joined_room_at = ?, "
            "joined_location_at = ? WHERE character_id = ?",
            (server_id, room_id, location_id, joined_room_at, now, character_id),
        )
    conn.commit()
    conn.close()


def get_character_location(character_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM room_occupancy WHERE character_id = ?", (character_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def characters_at_location(location_id):
    """Who's currently present - used to compute a new message's permanent visibility snapshot (see
    record_room_message) and to show a location's current occupants."""
    conn = get_db()
    rows = conn.execute("SELECT character_id FROM room_occupancy WHERE location_id = ?", (location_id,)).fetchall()
    conn.close()
    return [r["character_id"] for r in rows]


# ==================== NPCs (character sheets: personality + memory) ====================

def create_npc(game_id, key, name, personality="", traits=None, room_id=None, location_id=None):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO npcs (game_id, key, name, personality, traits_json, memory_json, room_id, location_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?)",
        (game_id, key, name, personality, json.dumps(traits or []), room_id, location_id, time.time()),
    )
    conn.commit()
    npc_id = cur.lastrowid
    conn.close()
    return npc_id


def get_npc(npc_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_npcs_for_game(game_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM npcs WHERE game_id = ?", (game_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def npcs_at_location(location_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM npcs WHERE location_id = ?", (location_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def move_npc(npc_id, room_id, location_id):
    conn = get_db()
    conn.execute("UPDATE npcs SET room_id = ?, location_id = ? WHERE id = ?", (room_id, location_id, npc_id))
    conn.commit()
    conn.close()


def add_npc_memory(npc_id, memory_text, cap=20):
    """Appends one memory entry, capped at the `cap` most recent (prompt-size sanity - an unbounded
    memory list would eventually blow the context budget, same class of concern already documented
    for single-player's conversation history elsewhere in this app)."""
    conn = get_db()
    row = conn.execute("SELECT memory_json FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if row is None:
        conn.close()
        return
    memory = json.loads(row["memory_json"] or "[]")
    memory.append(memory_text)
    memory = memory[-cap:]
    conn.execute("UPDATE npcs SET memory_json = ? WHERE id = ?", (json.dumps(memory), npc_id))
    conn.commit()
    conn.close()


def build_npc_sheet_text(npc_row):
    """Renders one NPC's character sheet as plain text for injection into an AI prompt - personality,
    traits, and recent memory. The concrete 'fed into the AI and referenced in actions' mechanism."""
    traits = json.loads(npc_row.get("traits_json") or "[]")
    memory = json.loads(npc_row.get("memory_json") or "[]")
    lines = [f"{npc_row['name']} ({npc_row['key']})"]
    if npc_row.get("personality"):
        lines.append(f"Personality: {npc_row['personality']}")
    if traits:
        lines.append(f"Traits: {', '.join(traits)}")
    if memory:
        lines.append("Recent memory: " + "; ".join(memory[-5:]))
    return "\n".join(lines)


# ==================== room messages (multiplayer's shared log, in `turns`) ====================

def record_room_message(game_id, room_id, location_id, character_id, kind, content, reply_to_turn_id=None,
                         responder=None, model_key=None, debug_events=None):
    """Records one row in the shared multiplayer log - a player's message/action, or an AI/NPC/GM
    reply to one - reusing `turns` rather than a parallel table (see the games-schema NOTES.md entry).
    visible_to_json is a PERMANENT snapshot of who was present in this location at send time, taken
    once here and never updated afterward - the actual mechanism that keeps someone who arrives later
    from seeing messages sent before they got there, per direction (not inferred from current
    room_occupancy, which would retroactively leak history to new arrivals)."""
    present = characters_at_location(location_id)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO turns (character_id, turn_number, player_input, narration, model_key, "
        "debug_events_json, game_id, room_id, location_id, kind, reply_to_turn_id, responder, "
        "visible_to_json, created_at) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            character_id,
            content if kind in ("message", "action") else "",
            content if kind in ("npc_reply", "gm_reply") else "",
            model_key,
            json.dumps(debug_events or []),
            game_id, room_id, location_id, kind, reply_to_turn_id, responder,
            json.dumps(present),
            time.time(),
        ),
    )
    conn.commit()
    turn_id = cur.lastrowid
    conn.close()
    return turn_id


def get_turn_by_id(turn_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_room_messages(location_id, viewer_character_id):
    """Every row at this location visible to viewer_character_id, filtered by each row's own
    permanent visible_to_json snapshot - NOT by current room_occupancy. That's the whole point: a
    character who just arrived doesn't retroactively gain access to what was said before they did."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM turns WHERE location_id = ? ORDER BY id ASC", (location_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        visible_to = json.loads(r["visible_to_json"] or "[]")
        if viewer_character_id in visible_to:
            out.append(dict(r))
    return out


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
