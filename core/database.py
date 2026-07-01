"""
database.py — SQLite persistence layer for the YouTube Picker app.

No Streamlit imports here on purpose: this module is pure data access,
so it can be tested standalone and reused by any UI layer.

Tables:
    channels        cached subscription channels
    videos          cached video metadata per channel
    watched_videos  videos the user has clicked "watch" on, via this app
    settings        key/value store for user-configurable rules
    usage_log       one row per completed "pick a video" session, used
                    by rules_engine.py to enforce daily limits
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, date

from config import DB_PATH, DATA_DIR, DEFAULT_RULES

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    thumbnail_url   TEXT,
    last_fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id            TEXT PRIMARY KEY,
    channel_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    thumbnail_url       TEXT,
    published_at        TEXT NOT NULL,
    duration_seconds    INTEGER NOT NULL,
    cached_at           TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);

CREATE TABLE IF NOT EXISTS watched_videos (
    video_id    TEXT PRIMARY KEY,
    watched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    used_at     TEXT NOT NULL
);
"""


@contextmanager
def get_connection():
    """Context-managed SQLite connection with row access by column name."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist and seed default settings."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_RULES.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


# --- Channels ---

def upsert_channel(channel_id: str, title: str, thumbnail_url: str | None):
    with get_connection() as conn:
        conn.execute(
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
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_fetched_at FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return row["last_fetched_at"] if row else None


def get_all_channels() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM channels ORDER BY title").fetchall()


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
    with get_connection() as conn:
        conn.execute(
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


def get_cached_videos(channel_ids: list[str] | None = None) -> list[sqlite3.Row]:
    """All cached videos, optionally filtered to a set of channel IDs."""
    with get_connection() as conn:
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            query = f"""
                SELECT videos.*, channels.title AS channel_title
                FROM videos
                JOIN channels ON channels.channel_id = videos.channel_id
                WHERE videos.channel_id IN ({placeholders})
                ORDER BY videos.published_at DESC
            """
            return conn.execute(query, channel_ids).fetchall()
        query = """
            SELECT videos.*, channels.title AS channel_title
            FROM videos
            JOIN channels ON channels.channel_id = videos.channel_id
            ORDER BY videos.published_at DESC
        """
        return conn.execute(query).fetchall()


# --- Watched videos ---

def mark_watched(video_id: str):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO watched_videos (video_id, watched_at)
            VALUES (?, ?)
            ON CONFLICT(video_id) DO UPDATE SET watched_at = excluded.watched_at
            """,
            (video_id, datetime.utcnow().isoformat()),
        )


def get_watched_video_ids() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT video_id FROM watched_videos").fetchall()
        return {row["video_id"] for row in rows}


def is_watched(video_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM watched_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None


# --- Settings ---

def get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])


def set_setting(key: str, value):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value)),
        )


def get_all_settings() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}


# --- Usage log (for rules_engine.py) ---

def log_usage():
    """Record that the user completed one full 'pick a video' session."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_log (used_at) VALUES (?)",
            (datetime.utcnow().isoformat(),),
        )


def get_usage_count_for_date(target_date: date) -> int:
    start = datetime.combine(target_date, datetime.min.time()).isoformat()
    end = datetime.combine(target_date, datetime.max.time()).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM usage_log WHERE used_at BETWEEN ? AND ?",
            (start, end),
        ).fetchone()
        return row["cnt"]
