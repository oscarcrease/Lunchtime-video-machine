"""
Central configuration for the YouTube Picker app.
Every other module should import constants from here rather than
hardcoding paths, scopes, or defaults.
"""

import os
from pathlib import Path

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

# Google OAuth client secret file (you'll download this from Google Cloud
# Console in the next step and put it here).
CLIENT_SECRET_PATH = BASE_DIR / "client_secret.json"

# Where the user's OAuth token gets cached after first login, so they don't
# have to re-authenticate every single run.
TOKEN_PATH = BASE_DIR / "token.json"

# --- YouTube API ---
# Read-only scope is all we need: reading subscriptions and video metadata.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# How many uploads to pull per channel when refreshing the cache.
VIDEOS_PER_CHANNEL_FETCH = 10

# How long cached channel/video data stays "fresh" before we re-fetch from
# the API (keeps us well under the daily quota).
CACHE_TTL_HOURS = 12

# --- Shorts filtering ---
# Videos at or under this duration are treated as YouTube Shorts and
# excluded entirely from every screen. YouTube's own product now allows
# Shorts up to 3 minutes, so we use that as the safe cutoff rather than
# the older 60-second rule of thumb.
SHORTS_MAX_SECONDS = 180

# --- Time budget buckets (minutes) ---
# Each bucket is (label, min_minutes, max_minutes). max_minutes=None means
# "and up". Used by the time-picker screen and the filtering logic.
TIME_BUCKETS = [
    ("Quick break (< 10 min)", 0, 10),
    ("Short (10-20 min)", 10, 20),
    ("Medium (20-40 min)", 20, 40),
    ("Long (40+ min)", 40, None),
]

# --- Default usage rules ---
# These seed the `settings` table on first run. The user can change them
# later in the Settings screen.
DEFAULT_RULES = {
    # Max times per day the "pick a video" flow can be run, per day-of-week
    # bucket. None = unlimited.
    "weekday_daily_limit": 1,   # Mon-Fri
    "weekend_daily_limit": None,  # Sat-Sun, None = unlimited
    # Optional time-of-day window during which the app is USABLE at all
    # (e.g. lunch break, 12:00-13:00). Outside this window, the app is
    # blocked entirely regardless of daily count remaining. When disabled,
    # there's no time restriction -- only the daily count limit applies.
    "allowed_window_start": "12:00",
    "allowed_window_end": "13:00",
    "allowed_window_enabled": False,
}
