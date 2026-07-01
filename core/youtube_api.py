"""
youtube_api.py — Google OAuth login + YouTube Data API access.

Handles:
  - authenticating the user via a web-redirect OAuth flow (works on a
    headless deployed server, unlike a local-browser flow)
  - fetching the user's subscriptions
  - fetching recent uploads + durations per channel
  - writing everything into the local cache (core/database.py) so we
    don't re-hit the API more often than CACHE_TTL_HOURS

Note: this file DOES import Streamlit, unlike the other core/ modules.
OAuth's redirect-based flow is inherently tied to the request cycle
(reading the ?code= query param Google redirects back with, and caching
the resulting client in the user's session), which are Streamlit-specific
concepts. Everything else in core/ stays framework-agnostic; this is the
one deliberate exception.
"""

import re
from datetime import datetime, timedelta

import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config import (
    YOUTUBE_SCOPES,
    YOUTUBE_API_SERVICE_NAME,
    YOUTUBE_API_VERSION,
    VIDEOS_PER_CHANNEL_FETCH,
    CACHE_TTL_HOURS,
)
from core import database as db


# --- Auth ---

def _oauth_secrets() -> dict:
    if "google_oauth" not in st.secrets:
        raise KeyError(
            "Missing [google_oauth] section in secrets. Add client_id, "
            "client_secret, and redirect_uri -- see .streamlit/secrets.toml.example."
        )
    return st.secrets["google_oauth"]


def _build_flow() -> Flow:
    secrets = _oauth_secrets()
    client_config = {
        "web": {
            "client_id": secrets["client_id"],
            "client_secret": secrets["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=YOUTUBE_SCOPES,
        redirect_uri=secrets["redirect_uri"],
        # PKCE is optional for confidential clients like this one (we have
        # a real client_secret, unlike a mobile/SPA app that PKCE is
        # designed for). It's disabled here because the authorization-url
        # step and the token-exchange step each build a fresh Flow object
        # in separate Streamlit reruns -- there's no way to share an
        # auto-generated code_verifier between them across the browser
        # redirect round-trip, which caused "Missing code verifier" /
        # InvalidGrantError on every login attempt.
        autogenerate_code_verifier=False,
    )


def get_login_url() -> str:
    """Builds the Google consent-screen URL for the 'Log in' link."""
    flow = _build_flow()
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # forces a fresh refresh_token every time, not just the first
    )
    return auth_url


def _credentials_from_refresh_token(refresh_token: str) -> Credentials:
    secrets = _oauth_secrets()
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=YOUTUBE_SCOPES,
    )
    creds.refresh(Request())
    return creds


def get_authenticated_service():
    """
    Returns an authenticated YouTube API client, or None if the user
    still needs to log in (caller should show a login link from
    get_login_url()).

    Checked in order:
      1. Already authenticated earlier this session -> reuse it.
      2. A saved refresh_token in secrets -> silent login, no redirect
         needed. This is what makes a deployed app usable without
         re-consenting on every visit; see the setup notes for how to
         save one after first login.
      3. A fresh authorization 'code' Google just redirected back with
         -> completes first-time login.
      4. None of the above -> caller must show the login link.
    """
    if "youtube_service" in st.session_state:
        return st.session_state.youtube_service

    saved_refresh_token = st.secrets.get("google_oauth", {}).get("refresh_token")
    if saved_refresh_token:
        creds = _credentials_from_refresh_token(saved_refresh_token)
        service = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)
        st.session_state.youtube_service = service
        return service

    code = st.query_params.get("code")
    if code:
        # Streamlit can run the script twice in quick succession right
        # after a fresh page load/redirect. If a second run starts before
        # the first one finishes, both could see the same code and both
        # try to redeem it -- authorization codes are single-use, and a
        # second attempt fails with InvalidGrantError. Track the exact
        # code value in session_state (a fast local write, unlike
        # clearing the URL which has to round-trip to the browser) so a
        # near-simultaneous second run recognizes it's already being
        # handled and backs off instead of re-exchanging it.
        if st.session_state.get("_last_oauth_code") == code:
            return st.session_state.get("youtube_service")
        st.session_state["_last_oauth_code"] = code

        for key in list(st.query_params.keys()):
            del st.query_params[key]

        flow = _build_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        service = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)
        st.session_state.youtube_service = service
        # Surface the refresh token once so the sidebar can prompt the
        # user to save it to secrets for persistent login.
        st.session_state.new_refresh_token = creds.refresh_token
        return service

    return None


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


def refresh_subscription_cache(youtube, force: bool = False, progress_callback=None) -> int:
    """
    Pulls subscriptions and their recent videos from the API, but only
    for channels whose cache has gone stale (or all of them, if force=True).
    Writes results into the DB. Returns the number of channels refreshed.

    progress_callback, if given, is called after each subscription is
    processed as progress_callback(index, total, channel_title) -- lets
    the UI show real progress instead of a blind spinner, since a
    first-time sync across many channels makes one API round-trip per
    channel and can genuinely take several minutes.
    """
    subscriptions = fetch_subscriptions(youtube)
    refreshed_count = 0
    total = len(subscriptions)

    for i, sub in enumerate(subscriptions, start=1):
        # Check staleness against the EXISTING stored timestamp, before
        # touching it. Calling upsert_channel() first (as this used to)
        # would overwrite last_fetched_at with "now" before we ever
        # checked it, making every channel look artificially fresh and
        # skipping video fetching entirely -- on every single run,
        # including the very first one.
        existing_last_fetched = db.get_channel_last_fetched(sub["channel_id"])
        if not force and not _is_stale(existing_last_fetched):
            if progress_callback:
                progress_callback(i, total, sub["title"])
            continue

        db.upsert_channel(sub["channel_id"], sub["title"], sub["thumbnail_url"])

        videos = fetch_recent_videos(youtube, sub["channel_id"])
        if videos:
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
            refreshed_count += 1

        if progress_callback:
            progress_callback(i, total, sub["title"])

    return refreshed_count
