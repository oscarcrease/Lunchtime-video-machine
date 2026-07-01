"""
ui/components.py — reusable Streamlit UI pieces.

Currently just the video card, since that's the one piece reused across
every screen (creator's videos, final picked menu, etc). Page-level
layout (the time picker, creator picker, etc.) lives in ui/pages.py.
"""

import sqlite3
import streamlit as st
import streamlit.components.v1 as components

from core import database as db


def _format_duration(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def render_video_card(video: sqlite3.Row, key_prefix: str = "") -> bool:
    """
    Renders one video as a card: thumbnail, title, channel + duration,
    a collapsible description, and a Watch button.

    Clicking Watch marks the video as watched in the DB and opens it on
    YouTube in a new browser tab, leaving this app open in the original
    tab.

    Returns True if Watch was clicked on this run (so callers, e.g. the
    video-menu page, can react -- log a session, show a confirmation,
    move on, etc).
    """
    video_id = video["video_id"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    with st.container(border=True):
        if video["thumbnail_url"]:
            st.image(video["thumbnail_url"], use_container_width=True)

        st.markdown(f"**{video['title']}**")
        st.caption(f"{video['channel_title']} · {_format_duration(video['duration_seconds'])}")

        with st.expander("Description"):
            st.write(video["description"] or "_No description available._")

        clicked = st.button(
            "▶️ Watch on YouTube",
            key=f"{key_prefix}watch_{video_id}",
            use_container_width=True,
        )

        if clicked:
            db.mark_watched(video_id)
            # Open YouTube in a new tab via a tiny injected script, rather
            # than navigating the whole Streamlit app away to YouTube.
            components.html(
                f"<script>window.open('{video_url}', '_blank')</script>",
                height=0,
                width=0,
            )
            st.success("Marked as watched — opening in a new tab!")

        return clicked


def render_video_grid(videos: list[sqlite3.Row], columns: int = 3) -> list[str]:
    """
    Lays out a list of videos in a responsive grid of cards.
    Returns the video_ids of any videos marked watched during this run.
    """
    if not videos:
        st.info("No videos to show here.")
        return []

    watched_this_run = []
    cols = st.columns(columns)
    for i, video in enumerate(videos):
        with cols[i % columns]:
            if render_video_card(video, key_prefix=f"grid{i}_"):
                watched_this_run.append(video["video_id"])
    return watched_this_run
