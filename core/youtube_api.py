"""
youtube_api.py — Google OAuth login + YouTube Data API access.

Handles:
  - authenticating the user (opens a browser tab on first run, then
    reuses a cached token)
  - fetching the user's subscriptions
  - fetching recent uploads + durations per channel
  - writing everything into the local cache (core/database.py) so we
    don't re-hit the API more often than CACHE_TTL_HOURS

No Streamlit imports here either — this stays testable as plain Python.
"""

import re
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import (
    CLIENT_SECRET_PATH,
    TOKEN_PATH,
    YOUTUBE_SCOPES,
    YOUTUBE_API_SERVICE_NAME,
    YOUTUBE_API_VERSION,
    VIDEOS_PER_CHANNEL_FETCH,
    CACHE_TTL_HOURS,
)
from core import database as db


# --- Auth ---

def get_authenticated_service():
    """
    Returns an authenticated YouTube API client.

    First run: opens a browser tab for the user to log in and consent,
    then caches the resulting token to TOKEN_PATH.
    Subsequent runs: reuses/refreshes the cached token silently.
    """
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"Missing {CLIENT_SECRET_PATH.name}. Download it from Google Cloud "
                    "Console (OAuth client credentials) and place it in the project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_PATH), YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json())

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)


# --- Duration parsing ---

_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_iso8601_duration(duration: str) -> int:
    """Convert YouTube's ISO 8601 duration (e.g. 'PT15M33S') to seconds."""
    match = _DURATION_RE.match(duration)
    if not match:
        return 0
    parts = match.groupdict(default="0")
    days = int(parts["days"])
    hours = int(parts["hours"])
    minutes = int(parts["minutes"])
    seconds = int(parts["seconds"])
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


# --- Subscriptions ---

def fetch_subscriptions(youtube) -> list[dict]:
    """
    Returns all of the authenticated user's subscriptions as a list of
    dicts: {channel_id, title, thumbnail_url}.
    """
    subscriptions = []
    request = youtube.subscriptions().list(
        part="snippet",
        mine=True,
        maxResults=50,
    )
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            subscriptions.append({
                "channel_id": snippet["resourceId"]["channelId"],
                "title": snippet["title"],
                "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
            })
        request = youtube.subscriptions().list_next(request, response)
    return subscriptions


def _get_uploads_playlist_id(youtube, channel_id: str) -> str | None:
    response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = response.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_recent_videos(youtube, channel_id: str, max_results: int = VIDEOS_PER_CHANNEL_FETCH) -> list[dict]:
    """
    Returns the most recent uploads for a channel as a list of dicts:
    {video_id, title, description, thumbnail_url, published_at}.
    Duration is NOT included yet -- see fetch_video_durations, since
    playlistItems doesn't return it and it requires a separate call.
    """
    uploads_playlist_id = _get_uploads_playlist_id(youtube, channel_id)
    if not uploads_playlist_id:
        return []

    response = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=max_results,
    ).execute()

    videos = []
    for item in response.get("items", []):
        snippet = item["snippet"]
        # Skip private/deleted videos, which show up with this placeholder title
        if snippet["title"] in ("Private video", "Deleted video"):
            continue
        videos.append({
            "video_id": snippet["resourceId"]["videoId"],
            "title": snippet["title"],
            "description": snippet.get("description", ""),
            "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
            "published_at": snippet["publishedAt"],
        })
    return videos


def fetch_video_durations(youtube, video_ids: list[str]) -> dict[str, int]:
    """
    Batch-fetches durations (in seconds) for up to 50 video IDs at a time.
    Returns {video_id: duration_seconds}.
    """
    durations = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        response = youtube.videos().list(part="contentDetails", id=",".join(chunk)).execute()
        for item in response.get("items", []):
            durations[item["id"]] = parse_iso8601_duration(item["contentDetails"]["duration"])
    return durations


# --- Orchestration: refresh the local cache ---

def _is_stale(last_fetched_at: str | None) -> bool:
    if last_fetched_at is None:
        return True
    last_fetched = datetime.fromisoformat(last_fetched_at)
    return datetime.utcnow() - last_fetched > timedelta(hours=CACHE_TTL_HOURS)


def refresh_subscription_cache(youtube, force: bool = False) -> int:
    """
    Pulls subscriptions and their recent videos from the API, but only
    for channels whose cache has gone stale (or all of them, if force=True).
    Writes results into the DB. Returns the number of channels refreshed.
    """
    subscriptions = fetch_subscriptions(youtube)
    refreshed_count = 0

    for sub in subscriptions:
        db.upsert_channel(sub["channel_id"], sub["title"], sub["thumbnail_url"])

        if not force and not _is_stale(db.get_channel_last_fetched(sub["channel_id"])):
            continue

        videos = fetch_recent_videos(youtube, sub["channel_id"])
        if not videos:
            continue

        durations = fetch_video_durations(youtube, [v["video_id"] for v in videos])

        for video in videos:
            duration_seconds = durations.get(video["video_id"], 0)
            db.upsert_video(
                video_id=video["video_id"],
                channel_id=sub["channel_id"],
                title=video["title"],
                description=video["description"],
                thumbnail_url=video["thumbnail_url"],
                published_at=video["published_at"],
                duration_seconds=duration_seconds,
            )

        # Re-touch the channel's last_fetched_at now that videos are cached
        db.upsert_channel(sub["channel_id"], sub["title"], sub["thumbnail_url"])
        refreshed_count += 1

    return refreshed_count
