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

# Google OAuth credentials now come from Streamlit secrets (st.secrets),
# not a local client_secret.json file -- see .streamlit/secrets.toml.example.
# This is required for cloud deployment: a local-browser OAuth flow can't
# work on a headless server, and secrets are the standard way to store
# credentials both locally and on Streamlit Community Cloud.

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

# --- Settings lock ---
# This is a self-discipline speed bump, NOT real security -- anyone with
# access to this code (i.e. you) could trivially bypass it by editing the
# database or this file directly. That's intentional: the point is to add
# enough friction that you don't casually loosen your own rules on a whim,
# not to build an adversarial lock. Change the password by editing the
# line below directly -- editing source code is a deliberately higher-
# friction bypass than clicking a button in the UI.
SETTINGS_PASSWORD = "changeme"
SETTINGS_UNLOCK_DELAY_SECONDS = 10

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
