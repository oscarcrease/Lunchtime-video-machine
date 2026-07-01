"""
ui/pages.py — the individual screens of the picker flow.

app.py wires these together and manages which one is showing via
st.session_state. Each render_* function reads/writes session_state
directly since Streamlit's rerun model makes passing state around by
return value awkward for anything beyond a single value.
"""

import streamlit as st

from core import database as db
from core import filters
from core import selector
from core import rules_engine as rules
from core import youtube_api
from ui.components import render_video_grid


# --- Sidebar: settings + subscription refresh ---

def render_sidebar():
    with st.sidebar:
        st.header("⚙️ Settings")

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

        st.divider()
        st.header("📡 Subscriptions")
        channels = db.get_all_channels()
        st.caption(f"{len(channels)} channels cached")

        if st.button("🔄 Refresh from YouTube", use_container_width=True):
            with st.spinner("Fetching your subscriptions... this can take a minute."):
                try:
                    yt = youtube_api.get_authenticated_service()
                    count = youtube_api.refresh_subscription_cache(yt)
                    st.success(f"Refreshed {count} channels!")
                    st.rerun()
                except FileNotFoundError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Something went wrong refreshing subscriptions: {e}")


def _parse_hhmm(hhmm: str):
    from datetime import time as dt_time
    hour, minute = hhmm.split(":")
    return dt_time(int(hour), int(minute))


# --- Screen 1: time picker ---

def render_time_picker():
    st.title("📺 Lunchtime Video Machine")
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
        st.session_state.step = "creator"
        st.rerun()


# --- Screen 2: creator picker ---

def render_creator_picker():
    st.title("Who do you want to watch?")

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

    options = [f"{c['channel_title']} ({c['video_count']})" for c in creators]
    label_to_title = {
        f"{c['channel_title']} ({c['video_count']})": c["channel_title"] for c in creators
    }

    selected_labels = st.multiselect("Pick one or more creators", options)

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("⬅️ Back", use_container_width=True):
            st.session_state.step = "time"
            st.rerun()
    with col2:
        if st.button(
            "Show me videos ➡️", type="primary", use_container_width=True,
            disabled=not selected_labels,
        ):
            st.session_state.selected_creators = [
                label_to_title[label] for label in selected_labels
            ]
            st.session_state.step = "menu"
            st.session_state.session_logged = False
            st.rerun()


# --- Screen 3: video menu ---

def render_video_menu():
    st.title("Pick something to watch")

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
        st.rerun()
