"""
rules_engine.py — decides whether the user is currently allowed to run
the "pick a video" flow, based on settings (core/database.py's
`settings` table) and today's usage_log count.

Two independent checks, both must pass:
  1. Time-of-day window (optional) -- if enabled, the app is only usable
     inside this window (e.g. lunch break, 12:00-13:00). Outside it,
     blocked regardless of remaining daily count.
  2. Daily count limit -- separate limits for weekdays vs weekends,
     either of which can be None for "unlimited".

Pure Python/data logic, no Streamlit imports.
"""

from dataclasses import dataclass
from datetime import datetime, date, time as dt_time

from core import database as db


@dataclass
class RulesCheckResult:
    allowed: bool
    reason: str
    uses_today: int
    daily_limit: int | None  # None = unlimited


def _parse_time(hhmm: str) -> dt_time:
    hour, minute = hhmm.split(":")
    return dt_time(int(hour), int(minute))


def _is_within_window(now: datetime, start_str: str, end_str: str) -> bool:
    """
    True if now's time-of-day falls within [start, end]. Handles windows
    that cross midnight (e.g. start=22:00, end=02:00).
    """
    current = now.time()
    start = _parse_time(start_str)
    end = _parse_time(end_str)

    if start <= end:
        return start <= current <= end
    # Window crosses midnight
    return current >= start or current <= end


def _get_daily_limit_for(target_date: date) -> int | None:
    """Returns today's applicable limit (None = unlimited)."""
    is_weekend = target_date.weekday() >= 5  # 5=Sat, 6=Sun
    key = "weekend_daily_limit" if is_weekend else "weekday_daily_limit"
    return db.get_setting(key)


def check_can_use_app(now: datetime | None = None) -> RulesCheckResult:
    """
    Main entry point. Checks the time window (if enabled) and the daily
    count limit for today, and returns whether the user is currently
    allowed to run the picker flow.
    """
    now = now or datetime.now()
    today = now.date()

    window_enabled = db.get_setting("allowed_window_enabled", False)
    if window_enabled:
        start = db.get_setting("allowed_window_start")
        end = db.get_setting("allowed_window_end")
        if not _is_within_window(now, start, end):
            uses_today = db.get_usage_count_for_date(today)
            limit = _get_daily_limit_for(today)
            return RulesCheckResult(
                allowed=False,
                reason=f"Only available between {start} and {end}.",
                uses_today=uses_today,
                daily_limit=limit,
            )

    uses_today = db.get_usage_count_for_date(today)
    limit = _get_daily_limit_for(today)

    if limit is not None and uses_today >= limit:
        return RulesCheckResult(
            allowed=False,
            reason=f"You've used your allowance for today ({uses_today}/{limit}).",
            uses_today=uses_today,
            daily_limit=limit,
        )

    return RulesCheckResult(
        allowed=True,
        reason="OK",
        uses_today=uses_today,
        daily_limit=limit,
    )


def record_session():
    """Call this once the user has completed a full picker flow (i.e.
    they've reached the video menu / clicked to watch something), so it
    counts against today's limit."""
    db.log_usage()
