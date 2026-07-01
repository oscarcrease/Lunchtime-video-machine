"""
ui/pages.py — the individual screens of the picker flow.

app.py wires these together and manages which one is showing via
st.session_state. Each render_* function reads/writes session_state
directly since Streamlit's rerun model makes passing state around by
return value awkward for anything beyond a single value.
"""

import time
import streamlit as st
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from config import SETTINGS_PASSWORD, SETTINGS_UNLOCK_DELAY_SECONDS
from core import database as db
from core import filters
from core import selector
from core import rules_engine as rules
from core import youtube_api
from ui.components import render_video_grid


def toggle_creator_selection(selected: list[str], creator: str) -> list[str]:
    """Adds creator if absent, removes it if present. Returns a new list
    (doesn't mutate the input) so it's easy to reason about and test."""
    if creator in selected:
        return [c for c in selected if c != creator]
    return selected + [creator]


def _render_step_indicator(current_step: int):
    labels = ["1. Time", "2. Creators", "3. Watch"]
    parts = []
    for i, label in enumerate(labels, start=1):
        parts.append(f"**{label}**" if i == current_step else label)
    st.caption(" → ".join(parts))


# --- Sidebar: settings (locked) + subscription refresh (open) ---

def render_sidebar():
    with st.sidebar:
        st.header("⚙️ Settings")
        _render_settings_lock_gate()

        st.divider()
        st.header("📡 Subscriptions")
        channels = db.get_all_channels()
        st.caption(f"{len(channels)} channels cached")

        cached_videos = db.get_cached_videos()
        with_duration = sum(1 for v in cached_videos if v["duration_seconds"] > 0)
        st.caption(f"{len(cached_videos)} videos cached ({with_duration} with duration data)")

        try:
            yt = youtube_api.get_authenticated_service()
        except KeyError as e:
            st.error(str(e))
            yt = None
        except RefreshError:
            st.error(
                "Your saved YouTube login has expired or been revoked. "
                "Remove `refresh_token` from your secrets and log in again below."
            )
            yt = None

        if yt is None:
            try:
                login_url = youtube_api.get_login_url()
                st.link_button("🔐 Log in with Google", login_url, use_container_width=True)
            except KeyError as e:
                st.error(
                    f"Missing or incomplete [google_oauth] secrets: {e}. "
                    "Check that client_id, client_secret, and redirect_uri are "
                    "all present under [google_oauth] in your Secrets."
                )
        else:
            new_token = st.session_state.get("new_refresh_token")
            if new_token:
                st.warning("Save this to stay logged in permanently:")
                st.code(new_token, language=None)
                st.caption(
                    "Add it to your app's Secrets as "
                    '`refresh_token = "paste-here"` under `[google_oauth]`, '
                    "then reboot the app. Otherwise you'll need to log in again "
                    "next time this session ends."
                )

            if st.button("🔄 Refresh from YouTube", use_container_width=True):
                with st.spinner("Fetching your subscriptions... this can take a minute."):
                    try:
                        count = youtube_api.refresh_subscription_cache(yt)
                        st.success(f"Refreshed {count} channels!")
                        st.rerun()
                    except HttpError as e:
                        status = e.resp.status if hasattr(e, "resp") else None
                        if status == 403 and "quota" in str(e).lower():
                            st.error(
                                "YouTube's daily API quota has been used up. This resets "
                                "at midnight Pacific time -- try again tomorrow, or use "
                                "whatever's already cached in the meantime."
                            )
                        else:
                            st.error(f"YouTube API error ({status}): {e}")
                    except Exception as e:
                        st.error(f"Something went wrong refreshing subscriptions: {e}")


def _render_settings_lock_gate():
    """
    Settings stay locked behind a password + a real-time delay, so you
    can't casually loosen your own limits on a whim. Not real security --
    see the note in config.py -- just enough friction to make you pause.
    """
    if st.session_state.get("settings_unlocked"):
        _render_settings_controls()
        if st.button("🔒 Lock settings again"):
            st.session_state.settings_unlocked = False
            st.session_state.settings_unlock_requested_at = None
            st.rerun()
        return

    st.caption("🔒 Settings are locked to stop impulse changes.")
    password = st.text_input("Password", type="password", key="_settings_password_input")

    requested_at = st.session_state.get("settings_unlock_requested_at")

    if requested_at is None:
        if st.button("Unlock", use_container_width=True):
            if password == SETTINGS_PASSWORD:
                st.session_state.settings_unlock_requested_at = time.time()
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        elapsed = time.time() - requested_at
        remaining = SETTINGS_UNLOCK_DELAY_SECONDS - elapsed
        if remaining > 0:
            st.info(f"Confirming in {remaining:.0f}s... click Confirm once the wait is up.")
            if st.button("Confirm unlock", use_container_width=True):
                st.rerun()  # just re-checks elapsed time on next run
        else:
            if password == SETTINGS_PASSWORD:
                if st.button("Confirm unlock", use_container_width=True, type="primary"):
                    st.session_state.settings_unlocked = True
                    st.session_state.settings_unlock_requested_at = None
                    st.rerun()
            else:
                st.warning("Wait's over -- re-enter the password to confirm.")
                if st.button("Confirm unlock", use_container_width=True):
                    st.rerun()


def _render_settings_controls():
    weekday_limit = st.session_state.get(
        "_weekday_limit_input", db.get_setting("weekday_daily_limit")
    )
    weekend_limit = st.session_state.get(
        "_weekend_limit_input", db.get_setting("weekend_daily_limit")
    )

    weekday_unlimited = st.checkbox(
        "Unlimited on weekdays", value=weekday_limit is None, key="weekday_unlimited"
    )
    if not weekday_unlimited:
        weekday_val = st.number_input(
            "Times per day (Mon-Fri)",
            min_value=1, max_value=20,
            value=weekday_limit if weekday_limit is not None else 1,
            key="_weekday_limit_input",
        )
    else:
        weekday_val = None

    weekend_unlimited = st.checkbox(
        "Unlimited on weekends", value=weekend_limit is None, key="weekend_unlimited"
    )
    if not weekend_unlimited:
        weekend_val = st.number_input(
            "Times per day (Sat-Sun)",
            min_value=1, max_value=20,
            value=weekend_limit if weekend_limit is not None else 1,
            key="_weekend_limit_input",
        )
    else:
        weekend_val = None

    st.divider()

    window_enabled = st.checkbox(
        "Only allow use during a specific time window",
        value=db.get_setting("allowed_window_enabled", False),
        key="_window_enabled_input",
    )
    window_start = db.get_setting("allowed_window_start", "12:00")
    window_end = db.get_setting("allowed_window_end", "13:00")
    if window_enabled:
        col1, col2 = st.columns(2)
        with col1:
            start_time = st.time_input(
                "From", value=_parse_hhmm(window_start), key="_window_start_input"
            )
        with col2:
            end_time = st.time_input(
                "To", value=_parse_hhmm(window_end), key="_window_end_input"
            )
    else:
        start_time = _parse_hhmm(window_start)
        end_time = _parse_hhmm(window_end)

    if st.button("Save settings", use_container_width=True):
        db.set_setting("weekday_daily_limit", weekday_val)
        db.set_setting("weekend_daily_limit", weekend_val)
        db.set_setting("allowed_window_enabled", window_enabled)
        db.set_setting("allowed_window_start", start_time.strftime("%H:%M"))
        db.set_setting("allowed_window_end", end_time.strftime("%H:%M"))
        st.success("Settings saved!")
        st.rerun()


def _parse_hhmm(hhmm: str):
    from datetime import time as dt_time
    hour, minute = hhmm.split(":")
    return dt_time(int(hour), int(minute))


# --- Screen 1: time picker ---

def render_time_picker():
    st.title("📺 Lunchtime Video Machine")
    _render_step_indicator(1)

    if not db.get_all_channels():
        st.info(
            "👋 First time here? Open the sidebar (top-left arrow) and click "
            "**🔄 Refresh from YouTube** to pull in your subscriptions before "
            "picking a time budget."
        )
        return

    st.subheader("How much time do you have?")

    minutes = st.slider(
        "Minutes to spend on YouTube",
        min_value=5, max_value=120, value=20, step=5,
    )
    bucket_label = filters.get_bucket_label_for_minutes(minutes)
    st.caption(f"Looking for videos in the **{bucket_label}** range.")

    if st.button("Find videos ➡️", type="primary", use_container_width=True):
        st.session_state.target_minutes = minutes
        st.session_state.bucket_label = bucket_label
        st.session_state.selected_creators = []
        st.session_state.step = "creator"
        st.rerun()


# --- Screen 2: creator picker ---

def render_creator_picker():
    st.title("Who do you want to watch?")
    _render_step_indicator(2)

    if "selected_creators" not in st.session_state:
        st.session_state.selected_creators = []

    all_videos = db.get_cached_videos()
    watched_ids = db.get_watched_video_ids()
    filtered = filters.get_filtered_videos(
        all_videos, watched_ids, st.session_state.bucket_label
    )
    creators = filters.get_available_creators(filtered)

    if not creators:
        st.warning(
            "No unwatched videos found in that time range. Try a different "
            "amount of time, or refresh your subscriptions from the sidebar."
        )
        if st.button("⬅️ Back"):
            st.session_state.step = "time"
            st.rerun()
        return

    st.caption(f"{len(creators)} creators have unwatched videos in your time range.")

    # Drop any stale selections (e.g. a creator that no longer has videos
    # in this time range) before syncing to widget state.
    valid_titles = {c["channel_title"] for c in creators}
    st.session_state.selected_creators = [
        c for c in st.session_state.selected_creators if c in valid_titles
    ]

    # Set the multiselect's widget state BEFORE creating it, so tile
    # clicks (which update selected_creators and rerun) are reflected in
    # the search box too. Widget state can only be set before the widget
    # with that key is instantiated on a given run.
    st.session_state["_creator_multiselect"] = st.session_state.selected_creators

    creator_titles = [c["channel_title"] for c in creators]
    multiselect_value = st.multiselect(
        "Search or pick creators",
        options=creator_titles,
        key="_creator_multiselect",
    )
    # A manual edit in the search box is the source of truth for this run.
    st.session_state.selected_creators = multiselect_value

    # --- Thumbnail tile grid ---
    thumbnails = {c["title"]: c["thumbnail_url"] for c in db.get_all_channels()}

    TILES_PER_ROW = 4
    for row_start in range(0, len(creators), TILES_PER_ROW):
        row_creators = creators[row_start:row_start + TILES_PER_ROW]
        cols = st.columns(TILES_PER_ROW)
        for col, creator in zip(cols, row_creators):
            title = creator["channel_title"]
            is_selected = title in st.session_state.selected_creators
            with col:
                with st.container(border=True):
                    thumb = thumbnails.get(title)
                    if thumb:
                        st.image(thumb, use_container_width=True)
                    else:
                        st.markdown(
                            "<div style='text-align:center; font-size:2.5em;'>👤</div>",
                            unsafe_allow_html=True,
                        )
                    prefix = "✅ " if is_selected else ""
                    st.markdown(f"**{prefix}{title}**")
                    st.caption(f"{creator['video_count']} video(s)")

                    button_label = "➖ Remove" if is_selected else "➕ Add"
                    if st.button(button_label, key=f"tile_{title}", use_container_width=True):
                        st.session_state.selected_creators = toggle_creator_selection(
                            st.session_state.selected_creators, title
                        )
                        st.rerun()

    st.divider()
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("⬅️ Back", use_container_width=True):
            st.session_state.step = "time"
            st.session_state.selected_creators = []
            st.rerun()
    with col2:
        if st.button(
            "Show me videos ➡️", type="primary", use_container_width=True,
            disabled=not st.session_state.selected_creators,
        ):
            st.session_state.step = "menu"
            st.session_state.session_logged = False
            st.rerun()


# --- Screen 3: video menu ---

def render_video_menu():
    st.title("Pick something to watch")
    _render_step_indicator(3)

    all_videos = db.get_cached_videos()
    watched_ids = db.get_watched_video_ids()
    filtered = filters.get_filtered_videos(
        all_videos, watched_ids, st.session_state.bucket_label
    )
    grouped = filters.group_by_creator(filtered)

    picks = selector.select_videos(
        grouped, st.session_state.selected_creators, st.session_state.target_minutes
    )

    if not picks:
        st.warning("Couldn't find videos for that selection anymore -- they may have been watched already.")
    else:
        # Log this as a completed session the first time this screen renders
        # for the current selection, not on every rerun (e.g. from clicking Watch).
        if not st.session_state.get("session_logged"):
            rules.record_session()
            st.session_state.session_logged = True

        watched_now = render_video_grid(picks, columns=min(3, len(picks)))
        if watched_now:
            st.balloons()

    if st.button("⬅️ Start over"):
        st.session_state.step = "time"
        st.session_state.selected_creators = []
        st.rerun()
