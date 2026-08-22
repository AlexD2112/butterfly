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
            current_date TEXT,
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
    # Combat: one active encounter at a time per location, participants in a fixed initiative order -
    # "turn order/initiative must be respected... no free-for-all resolution" (2026-08-21). Rolling
    # initiative and detecting when combat starts/ends happens in multiplayer.py (mechanics stay
    # app-owned, not model-trusted); this is just the persisted state those decisions act on.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS combat_encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            location_id INTEGER NOT NULL REFERENCES locations(id),
            status TEXT NOT NULL DEFAULT 'active',
            current_turn_index INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            ended_at REAL
        )
        """
    )
    # Exactly one of character_id/npc_key is set per row - a combatant is always either a player
    # character or an NPC, never both and never neither.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS combat_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id INTEGER NOT NULL REFERENCES combat_encounters(id),
            character_id INTEGER REFERENCES characters(id),
            npc_key TEXT,
            initiative_roll INTEGER NOT NULL,
            turn_order_index INTEGER NOT NULL
        )
        """
    )
    # Room conclusion (2026-08-21): who has marked the CURRENT thread at a location concluded. One
    # row per character per location - PRIMARY KEY doubles as the "already marked" check. Cleared
    # (see clear_location_conclusions) once a thread actually concludes, so a fresh thread at the
    # same location starts tracking from zero again.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS location_conclusions (
            location_id INTEGER NOT NULL REFERENCES locations(id),
            character_id INTEGER NOT NULL REFERENCES characters(id),
            marked_at REAL NOT NULL,
            PRIMARY KEY (location_id, character_id)
        )
        """
    )
    # current_date wasn't in multiplayer_servers' original CREATE TABLE (added 2026-08-21 for real
    # scheduled ticks) - same ALTER-and-swallow pattern as every other column added after a table
    # already existed, so an existing deployment's servers pick it up on the next restart too.
    try:
        conn.execute("ALTER TABLE multiplayer_servers ADD COLUMN current_date TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Significance flags on individual turns (2026-08-21) - "a small flag gets created on it" for an
    # in-progress action the app judges important. Reuses council.detect_stakes_signals, the same
    # FREE, rule-based signal detector single-player's own council-router already relies on - no new
    # AI call needed per action, just reading debug_events for critical rolls/canon events/big
    # resource swings that are already being computed anyway.
    for col_sql in [
        "ALTER TABLE turns ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE turns ADD COLUMN flag_reason TEXT",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Rumors (2026-08-21): a summary generated whenever a thread concludes (see
    # multiplayer.generate_rumor), held unpublished until the next tick compiles every accumulated
    # rumor into one server-wide rumor_broadcasts entry (multiplayer.compile_and_publish_rumors).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rumors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL REFERENCES multiplayer_servers(id),
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            location_id INTEGER NOT NULL REFERENCES locations(id),
            summary TEXT NOT NULL,
            importance INTEGER NOT NULL,
            publicity INTEGER NOT NULL,
            created_at REAL NOT NULL,
            published_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rumor_broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL REFERENCES multiplayer_servers(id),
            content TEXT NOT NULL,
            rumor_count INTEGER NOT NULL,
            published_at REAL NOT NULL
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

def create_multiplayer_server(name, setting_key, system_key, progression_speed, progression_rate_hours,
                               created_by_user_id, initial_date=None):
    """Creates the server's single backing game first (multiplayer_server_id starts NULL - the
    server row doesn't exist yet to reference), then links them - avoids a circular insert between
    games and multiplayer_servers without needing a redundant game_id column on this table.

    initial_date: the setting's real starting date (e.g. "1917-09-25") - the CALLER resolves this
    from the setting's premade character state (see server.py), not this function, so db.py stays
    agnostic of setting content the same way it already is everywhere else. Also seeds last_tick_at
    to now, so the first real tick fires a full progression_rate_hours after creation, not
    immediately on the scheduler's next heartbeat."""
    game_id = create_game("multiplayer", setting_key, system_key, multiplayer_server_id=None)
    conn = get_db()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO multiplayer_servers (name, setting_key, system_key, progression_speed, "
        "progression_rate_hours, current_date, last_tick_at, created_by_user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, setting_key, system_key, progression_speed, progression_rate_hours, initial_date, now, created_by_user_id, now),
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


def list_locations_for_server(server_id):
    """Every location across every room in a server, with its room_id alongside - the sweep needs to
    walk all of them, not just one room at a time."""
    conn = get_db()
    rows = conn.execute(
        "SELECT l.* FROM locations l JOIN rooms r ON r.id = l.room_id WHERE r.server_id = ? ORDER BY l.id ASC",
        (server_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_server_tick_state(server_id, new_date, tick_time):
    conn = get_db()
    conn.execute(
        "UPDATE multiplayer_servers SET current_date = ?, last_tick_at = ? WHERE id = ?",
        (new_date, tick_time, server_id),
    )
    conn.commit()
    conn.close()


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
                         responder=None, model_key=None, debug_events=None, flagged=False, flag_reason=None):
    """Records one row in the shared multiplayer log - a player's message/action, or an AI/NPC/GM
    reply to one - reusing `turns` rather than a parallel table (see the games-schema NOTES.md entry).
    visible_to_json is a PERMANENT snapshot of who was present in this location at send time, taken
    once here and never updated afterward - the actual mechanism that keeps someone who arrives later
    from seeing messages sent before they got there, per direction (not inferred from current
    room_occupancy, which would retroactively leak history to new arrivals). flagged/flag_reason are
    the significance markers (see turns' flagged column comment) - set by the caller from
    council.detect_stakes_signals, a free check, not a new AI call."""
    # Which field content actually lands in, by kind: player-authored text (message/action/storm_out
    # - storm_out's final flavor line is still the PLAYER's own words) goes to player_input;
    # AI/system-voiced text (npc_reply/gm_reply, and thread_concluded's note - narrated BY the game,
    # not said by the attributed character) goes to narration. Originally only covered
    # ("message","action") / ("npc_reply","gm_reply") - storm_out and thread_concluded fell into
    # NEITHER bucket, silently discarding their actual text. Found via a real SSE smoke test (an
    # empty narration field on a live thread_concluded event), not a code review - see NOTES.md.
    player_authored_kinds = ("message", "action", "storm_out")
    ai_or_system_kinds = ("npc_reply", "gm_reply", "thread_concluded")
    present = characters_at_location(location_id)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO turns (character_id, turn_number, player_input, narration, model_key, "
        "debug_events_json, game_id, room_id, location_id, kind, reply_to_turn_id, responder, "
        "visible_to_json, flagged, flag_reason, created_at) "
        "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            character_id,
            content if kind in player_authored_kinds else "",
            content if kind in ai_or_system_kinds else "",
            model_key,
            json.dumps(debug_events or []),
            game_id, room_id, location_id, kind, reply_to_turn_id, responder,
            json.dumps(present),
            1 if flagged else 0, flag_reason,
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


# ==================== combat encounters (initiative-ordered turn enforcement) ====================

def get_active_combat_encounter(location_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM combat_encounters WHERE location_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (location_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_combat_encounter(room_id, location_id, participants, start_turn_index=0):
    """participants: a list of {"character_id": int|None, "npc_key": str|None, "initiative_roll": int}
    dicts, already in the order the caller (multiplayer.py) wants them seated - turn_order_index is
    just each entry's position in that list. start_turn_index lets the caller begin the rotation
    partway through (e.g. skipping the participant who just acted to trigger this fight)."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO combat_encounters (room_id, location_id, status, current_turn_index, created_at) "
        "VALUES (?, ?, 'active', ?, ?)",
        (room_id, location_id, start_turn_index, time.time()),
    )
    encounter_id = cur.lastrowid
    for idx, p in enumerate(participants):
        conn.execute(
            "INSERT INTO combat_participants (encounter_id, character_id, npc_key, initiative_roll, turn_order_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (encounter_id, p.get("character_id"), p.get("npc_key"), p["initiative_roll"], idx),
        )
    conn.commit()
    conn.close()
    return encounter_id


def get_combat_participants(encounter_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM combat_participants WHERE encounter_id = ? ORDER BY turn_order_index ASC", (encounter_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_combat_actor(encounter_id):
    """The combat_participants row whose turn it currently is, or None if the encounter/participant
    lookup fails (shouldn't happen in practice, but callers should treat None as 'nobody's turn is
    clearly established' rather than crashing)."""
    conn = get_db()
    encounter = conn.execute("SELECT current_turn_index FROM combat_encounters WHERE id = ?", (encounter_id,)).fetchone()
    if encounter is None:
        conn.close()
        return None
    row = conn.execute(
        "SELECT * FROM combat_participants WHERE encounter_id = ? AND turn_order_index = ?",
        (encounter_id, encounter["current_turn_index"]),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def advance_combat_turn(encounter_id):
    """Moves current_turn_index to the next participant, wrapping back to the start after the last."""
    conn = get_db()
    encounter = conn.execute("SELECT current_turn_index FROM combat_encounters WHERE id = ?", (encounter_id,)).fetchone()
    count = conn.execute("SELECT COUNT(*) AS n FROM combat_participants WHERE encounter_id = ?", (encounter_id,)).fetchone()["n"]
    if encounter is None or count == 0:
        conn.close()
        return
    next_index = (encounter["current_turn_index"] + 1) % count
    conn.execute("UPDATE combat_encounters SET current_turn_index = ? WHERE id = ?", (next_index, encounter_id))
    conn.commit()
    conn.close()


def end_combat_encounter(encounter_id):
    conn = get_db()
    conn.execute("UPDATE combat_encounters SET status = 'ended', ended_at = ? WHERE id = ?", (time.time(), encounter_id))
    conn.commit()
    conn.close()


# ==================== room conclusion (mark-concluded / storm-out) ====================

def mark_location_concluded_by(location_id, character_id):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO location_conclusions (location_id, character_id, marked_at) VALUES (?, ?, ?)",
        (location_id, character_id, time.time()),
    )
    conn.commit()
    conn.close()


def clear_character_conclusion_mark(location_id, character_id):
    """Removes just one character's mark - used when they storm out, so their vote no longer counts
    toward unanimity (they're gone, same as leaving a room mid-conversation in person)."""
    conn = get_db()
    conn.execute("DELETE FROM location_conclusions WHERE location_id = ? AND character_id = ?", (location_id, character_id))
    conn.commit()
    conn.close()


def get_location_conclusion_marks(location_id):
    conn = get_db()
    rows = conn.execute("SELECT character_id FROM location_conclusions WHERE location_id = ?", (location_id,)).fetchall()
    conn.close()
    return {r["character_id"] for r in rows}


def clear_location_conclusions(location_id):
    """Resets tracking entirely - called once a thread actually concludes, so whatever happens next
    at this location starts a fresh count."""
    conn = get_db()
    conn.execute("DELETE FROM location_conclusions WHERE location_id = ?", (location_id,))
    conn.commit()
    conn.close()


def get_character_owner(character_id):
    """The user row that owns a character, regardless of who's asking - like
    get_character_unchecked, only for multiplayer contexts that legitimately need to know (e.g.
    whether a present character's account can be billed at all), never from raw client input."""
    conn = get_db()
    row = conn.execute(
        "SELECT u.* FROM users u JOIN characters c ON c.user_id = u.id WHERE c.id = ?", (character_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def characters_in_server(server_id):
    """Every character currently present anywhere in a server (any room, any location) - used for
    server-wide billing (the rumor broadcast) and server-wide SSE subscriptions, not location-scoped
    like characters_at_location."""
    conn = get_db()
    rows = conn.execute("SELECT character_id FROM room_occupancy WHERE server_id = ?", (server_id,)).fetchall()
    conn.close()
    return [r["character_id"] for r in rows]


# ==================== rumors ====================

def create_rumor(server_id, room_id, location_id, summary, importance, publicity):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO rumors (server_id, room_id, location_id, summary, importance, publicity, created_at, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (server_id, room_id, location_id, summary, importance, publicity, time.time()),
    )
    conn.commit()
    rumor_id = cur.lastrowid
    conn.close()
    return rumor_id


def get_unpublished_rumors(server_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM rumors WHERE server_id = ? AND published_at IS NULL ORDER BY created_at ASC", (server_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def publish_rumor_broadcast(server_id, content, rumor_count):
    """Marks every currently-unpublished rumor for this server as published (they're now folded into
    this one broadcast) and records the broadcast itself. Both happen together so a rumor can never
    be silently left in limbo (published_at NULL forever) or double-counted in a later broadcast."""
    conn = get_db()
    now = time.time()
    conn.execute(
        "UPDATE rumors SET published_at = ? WHERE server_id = ? AND published_at IS NULL", (now, server_id)
    )
    cur = conn.execute(
        "INSERT INTO rumor_broadcasts (server_id, content, rumor_count, published_at) VALUES (?, ?, ?, ?)",
        (server_id, content, rumor_count, now),
    )
    conn.commit()
    broadcast_id = cur.lastrowid
    conn.close()
    return broadcast_id


def list_rumor_broadcasts(server_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM rumor_broadcasts WHERE server_id = ? ORDER BY published_at DESC", (server_id,)
    ).fetchall()
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


def get_character_unchecked(character_id):
    """Same as get_character but WITHOUT an ownership check - only for multiplayer contexts that
    legitimately need to read another player's character state (e.g. rolling their initiative, or
    building combat context), never for anything the client can trigger against an arbitrary
    character_id on someone else's behalf. Callers stay responsible for verifying the REQUESTING
    character (the one the current session actually owns) via get_character first, same as today -
    this is only ever used to look up OTHER participants already established as present/involved by
    trusted server-side logic (room_occupancy, combat_participants), not from raw client input."""
    conn = get_db()
    row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
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
