"""
filters.py — turns the raw cached video list into what the picker screens
actually need: unwatched, non-Shorts, duration-matched, grouped by creator.

Pure Python/data logic, no Streamlit imports, so it's easy to test and
reuse from any UI layer.
"""

import sqlite3
from config import TIME_BUCKETS, SHORTS_MAX_SECONDS


def exclude_shorts(videos: list[sqlite3.Row], max_short_seconds: int = SHORTS_MAX_SECONDS) -> list:
    """Drops anything at or under the Shorts duration threshold."""
    return [v for v in videos if v["duration_seconds"] > max_short_seconds]


def filter_by_time_bucket(videos: list[sqlite3.Row], bucket_label: str) -> list:
    """
    Filters videos to those matching a bucket from config.TIME_BUCKETS,
    e.g. "Short (10-20 min)". Raises ValueError on an unknown label.
    """
    bucket = next((b for b in TIME_BUCKETS if b[0] == bucket_label), None)
    if bucket is None:
        raise ValueError(f"Unknown time bucket: {bucket_label!r}")

    _, min_minutes, max_minutes = bucket
    min_seconds = min_minutes * 60
    max_seconds = max_minutes * 60 if max_minutes is not None else None

    if max_seconds is None:
        return [v for v in videos if v["duration_seconds"] >= min_seconds]
    return [v for v in videos if min_seconds <= v["duration_seconds"] <= max_seconds]


def filter_unwatched(videos: list[sqlite3.Row], watched_ids: set[str]) -> list:
    """Drops any video whose ID is in the watched set."""
    return [v for v in videos if v["video_id"] not in watched_ids]


def group_by_creator(videos: list[sqlite3.Row]) -> dict[str, list]:
    """
    Groups videos by channel_title, preserving each channel's videos in
    whatever order they arrived in (callers typically pass already
    recency-sorted lists from database.get_cached_videos()).
    """
    grouped: dict[str, list] = {}
    for v in videos:
        grouped.setdefault(v["channel_title"], []).append(v)
    return grouped


def get_available_creators(videos: list[sqlite3.Row]) -> list[dict]:
    """
    Returns creators present in the given (already filtered) video list,
    as [{channel_title, video_count}], sorted alphabetically. Used to
    populate the creator-picker screen.
    """
    grouped = group_by_creator(videos)
    return [
        {"channel_title": title, "video_count": len(vids)}
        for title, vids in sorted(grouped.items())
    ]


def get_bucket_label_for_minutes(minutes: float) -> str:
    """
    Maps an exact minute value to its matching bucket label from
    config.TIME_BUCKETS, e.g. 15 -> "Short (10-20 min)". Falls back to
    the last bucket (the open-ended "and up" one) if nothing else matches.
    """
    for label, min_minutes, max_minutes in TIME_BUCKETS:
        if minutes >= min_minutes and (max_minutes is None or minutes <= max_minutes):
            return label
    return TIME_BUCKETS[-1][0]


def get_filtered_videos(
    all_cached_videos: list[sqlite3.Row],
    watched_ids: set[str],
    bucket_label: str,
) -> list:
    """
    The full pipeline in one call: exclude Shorts -> match time bucket ->
    drop watched. Returns a flat list; group_by_creator/get_available_creators
    can be applied on top for the creator-picker screen.
    """
    videos = exclude_shorts(all_cached_videos)
    videos = filter_by_time_bucket(videos, bucket_label)
    videos = filter_unwatched(videos, watched_ids)
    return videos
