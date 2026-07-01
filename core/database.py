"""
core/database.py — persistence layer.

Uses Turso (a hosted, SQLite-compatible database) via the `libsql`
package so data survives Streamlit Community Cloud's periodic reboots.
A plain local SQLite file does NOT survive those reboots/redeploys,
which defeats the entire point of tracking watched videos.

Falls back automatically to a local SQLite file (same `libsql` package,
just given a local file path instead of a Turso URL) when no
TURSO_DATABASE_URL environment variable is set -- so local development
and testing work exactly as before, no Turso account required for that.

libsql's connection/cursor API deliberately mirrors Python's built-in
sqlite3 module (Turso markets it as a drop-in replacement), which is why
this still reads almost identically to a plain sqlite3 version. One
change: rows come back as plain dicts (built from cursor.description)
rather than sqlite3.Row objects, since this project's dev environment
had no network access to verify libsql's own row-factory support ahead
of time. Plain dicts give identical `row["column"]` access, which is
all every other module in this codebase actually uses.

Still no Streamlit imports here: TURSO_DATABASE_URL / TURSO_AUTH_TOKEN
are read from plain environment variables, which app.py populates from
st.secrets at startup. That keeps this module framework-agnostic and
testable standalone, same as before.
"""

import os
import json
from datetime import datetime, date

import libsql

from config import DATA_DIR, DB_PATH, DEFAULT_RULES

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS channels (
        channel_id      TEXT PRIMARY KEY,
        title           TEXT NOT NULL,
        thumbnail_url   TEXT,
        last_fetched_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS videos (
        video_id            TEXT PRIMARY KEY,
        channel_id          TEXT NOT NULL,
        title               TEXT NOT NULL,
        description         TEXT,
        thumbnail_url       TEXT,
        published_at        TEXT NOT NULL,
        duration_seconds    INTEGER NOT NULL,
        cached_at           TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id)",
    """
    CREATE TABLE IF NOT EXISTS watched_videos (
        video_id    TEXT PRIMARY KEY,
        watched_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key     TEXT PRIMARY KEY,
        value   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        used_at     TEXT NOT NULL
    )
    """,
]

# Module-level singleton connection. This persists across Streamlit
# reruns without any Streamlit-specific caching, because Python only
# imports/executes a module once per process -- a plain global variable
# here survives every rerun of app.py within that process automatically.
_connection = None


def _get_connection():
    global _connection
    if _connection is None:
        turso_url = os.environ.get("TURSO_DATABASE_URL")
        turso_token = os.environ.get("TURSO_AUTH_TOKEN")
        if turso_url:
            _connection = libsql.connect(database=turso_url, auth_token=turso_token)
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _connection = libsql.connect(database=str(DB_PATH))
    return _connection


def _rows_from_cursor(cursor) -> list[dict]:
    if cursor.description is None:
        return []
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _execute(sql: str, params: tuple = ()):
    """Run a single write statement and commit immediately."""
    conn = _get_connection()
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a single read statement and return rows as plain dicts."""
    cursor = _get_connection().execute(sql, params)
    return _rows_from_cursor(cursor)


def init_db():
    """Create tables if they don't exist and seed default settings."""
    conn = _get_connection()
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()

    for key, value in DEFAULT_RULES.items():
        _execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )


# --- Channels ---

def upsert_channel(channel_id: str, title: str, thumbnail_url: str | None):
    _execute(
        """
        INSERT INTO channels (channel_id, title, thumbnail_url, last_fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title = excluded.title,
            thumbnail_url = excluded.thumbnail_url,
            last_fetched_at = excluded.last_fetched_at
        """,
        (channel_id, title, thumbnail_url, datetime.utcnow().isoformat()),
    )


def get_channel_last_fetched(channel_id: str) -> str | None:
    rows = _query(
        "SELECT last_fetched_at FROM channels WHERE channel_id = ?", (channel_id,)
    )
    return rows[0]["last_fetched_at"] if rows else None


def get_all_channels() -> list[dict]:
    return _query("SELECT * FROM channels ORDER BY title")


# --- Videos ---

def upsert_video(
    video_id: str,
    channel_id: str,
    title: str,
    description: str,
    thumbnail_url: str | None,
    published_at: str,
    duration_seconds: int,
):
    _execute(
        """
        INSERT INTO videos (
            video_id, channel_id, title, description,
            thumbnail_url, published_at, duration_seconds, cached_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            thumbnail_url = excluded.thumbnail_url,
            published_at = excluded.published_at,
            duration_seconds = excluded.duration_seconds,
            cached_at = excluded.cached_at
        """,
        (
            video_id, channel_id, title, description,
            thumbnail_url, published_at, duration_seconds,
            datetime.utcnow().isoformat(),
        ),
    )


def get_cached_videos(channel_ids: list[str] | None = None) -> list[dict]:
    """All cached videos, optionally filtered to a set of channel IDs."""
    if channel_ids:
        placeholders = ",".join("?" for _ in channel_ids)
        query = f"""
            SELECT videos.*, channels.title AS channel_title
            FROM videos
            JOIN channels ON channels.channel_id = videos.channel_id
            WHERE videos.channel_id IN ({placeholders})
            ORDER BY videos.published_at DESC
        """
        return _query(query, tuple(channel_ids))
    query = """
        SELECT videos.*, channels.title AS channel_title
        FROM videos
        JOIN channels ON channels.channel_id = videos.channel_id
        ORDER BY videos.published_at DESC
    """
    return _query(query)


# --- Watched videos ---

def mark_watched(video_id: str):
    _execute(
        """
        INSERT INTO watched_videos (video_id, watched_at)
        VALUES (?, ?)
        ON CONFLICT(video_id) DO UPDATE SET watched_at = excluded.watched_at
        """,
        (video_id, datetime.utcnow().isoformat()),
    )


def get_watched_video_ids() -> set[str]:
    rows = _query("SELECT video_id FROM watched_videos")
    return {row["video_id"] for row in rows}


def is_watched(video_id: str) -> bool:
    rows = _query("SELECT 1 FROM watched_videos WHERE video_id = ?", (video_id,))
    return len(rows) > 0


# --- Settings ---

def get_setting(key: str, default=None):
    rows = _query("SELECT value FROM settings WHERE key = ?", (key,))
    if not rows:
        return default
    return json.loads(rows[0]["value"])


def set_setting(key: str, value):
    _execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, json.dumps(value)),
    )


def get_all_settings() -> dict:
    rows = _query("SELECT key, value FROM settings")
    return {row["key"]: json.loads(row["value"]) for row in rows}


# --- Usage log (for rules_engine.py) ---

def log_usage():
    """Record that the user completed one full 'pick a video' session."""
    _execute(
        "INSERT INTO usage_log (used_at) VALUES (?)",
        (datetime.utcnow().isoformat(),),
    )


def get_usage_count_for_date(target_date: date) -> int:
    start = datetime.combine(target_date, datetime.min.time()).isoformat()
    end = datetime.combine(target_date, datetime.max.time()).isoformat()
    rows = _query(
        "SELECT COUNT(*) AS cnt FROM usage_log WHERE used_at BETWEEN ? AND ?",
        (start, end),
    )
    return rows[0]["cnt"]
