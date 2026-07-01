"""
Lunchtime Video Machine — main Streamlit entry point.

Flow:
  rules check -> time picker -> creator picker -> video menu

Run with: streamlit run app.py
"""

import os
import streamlit as st

# Must happen before anything touches core.database, since it reads these
# as plain env vars (keeping that module Streamlit-free and testable).
# No [turso] secrets configured -> database.py falls back to a local file
# automatically, so local dev works unchanged.
if "turso" in st.secrets:
    os.environ["TURSO_DATABASE_URL"] = st.secrets["turso"]["database_url"]
    os.environ["TURSO_AUTH_TOKEN"] = st.secrets["turso"]["auth_token"]

from core.database import init_db
from core import rules_engine as rules
from ui.pages import (
    render_sidebar,
    render_time_picker,
    render_creator_picker,
    render_video_menu,
)

st.set_page_config(
    page_title="Lunchtime Video Machine",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

if "step" not in st.session_state:
    st.session_state.step = "time"

render_sidebar()

rules_result = rules.check_can_use_app()

if not rules_result.allowed:
    st.title("📺 Lunchtime Video Machine")
    st.warning(f"🚫 {rules_result.reason}")
    st.caption("Adjust your limits any time from the settings in the sidebar.")
    st.stop()

if rules_result.daily_limit is not None:
    st.sidebar.caption(
        f"Today's usage: {rules_result.uses_today}/{rules_result.daily_limit}"
    )

if st.session_state.step == "time":
    render_time_picker()
elif st.session_state.step == "creator":
    render_creator_picker()
elif st.session_state.step == "menu":
    render_video_menu()
else:
    st.session_state.step = "time"
    st.rerun()
