"""
selector.py — given the user's chosen creator(s) and target watch time,
picks the actual videos to show on the final menu.

Rules (per the original spec):
  - One creator selected  -> pick the 3 best-fit videos from that creator
  - Multiple creators      -> pick 1 best-fit video per creator
  - "Best fit" = newest AND closest to the target time budget, not just
    whichever is newest

Ranking approach: rather than inventing arbitrary weights to combine
"how recent" and "how close to target minutes" (which are on totally
different scales), each video gets a rank position in each dimension
(0 = best), and the two ranks are summed. Lower combined score wins.
This is a simple Borda-count-style approach that avoids weight-tuning.

Pure Python/data logic, no Streamlit imports.
"""

import sqlite3
from datetime import datetime


def _duration_diff_minutes(video: sqlite3.Row, target_minutes: float) -> float:
    return abs(video["duration_seconds"] / 60 - target_minutes)


def _parse_published_at(video: sqlite3.Row) -> datetime:
    # YouTube's publishedAt is ISO 8601 UTC, e.g. "2026-06-30T00:00:00Z"
    return datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))


def rank_videos(videos: list[sqlite3.Row], target_minutes: float) -> list[sqlite3.Row]:
    """
    Returns videos sorted best-fit first.

    Uses a weighted score of (a) how far the video's duration is from the
    target, as a fraction of the target, and (b) recency rank normalized
    to [0, 1). Duration fit is weighted more heavily -- it's the primary
    signal, recency mainly breaks ties between similarly-good durations.
    A pure rank-sum was tried first but let a badly-mismatched-duration
    video "buy back" a good rank just by being newest, which isn't the
    intended behavior.
    """
    if not videos:
        return []
    if len(videos) == 1:
        return list(videos)

    DURATION_WEIGHT = 0.7
    RECENCY_WEIGHT = 0.3

    by_recency = sorted(videos, key=_parse_published_at, reverse=True)
    recency_rank = {v["video_id"]: i for i, v in enumerate(by_recency)}
    n = len(videos)

    def score(v):
        duration_penalty = _duration_diff_minutes(v, target_minutes) / max(target_minutes, 1)
        recency_penalty = recency_rank[v["video_id"]] / (n - 1)
        return DURATION_WEIGHT * duration_penalty + RECENCY_WEIGHT * recency_penalty

    return sorted(videos, key=score)


def select_for_single_creator(
    videos: list[sqlite3.Row], target_minutes: float, count: int = 3
) -> list[sqlite3.Row]:
    """Best `count` videos from a single creator's video list."""
    return rank_videos(videos, target_minutes)[:count]


def select_one_per_creator(
    grouped_videos: dict[str, list[sqlite3.Row]], target_minutes: float
) -> list[sqlite3.Row]:
    """
    Best single video from each creator in grouped_videos, one per
    creator, returned as a flat list ordered best-fit first overall.
    """
    picks = []
    for creator_videos in grouped_videos.values():
        ranked = rank_videos(creator_videos, target_minutes)
        if ranked:
            picks.append(ranked[0])
    return rank_videos(picks, target_minutes)


def select_videos(
    grouped_videos: dict[str, list[sqlite3.Row]],
    selected_creators: list[str],
    target_minutes: float,
) -> list[sqlite3.Row]:
    """
    Main entry point for the picker UI.

    - Exactly one creator selected -> top 3 videos from that creator
    - Multiple creators selected   -> 1 video per creator
    """
    if not selected_creators:
        return []

    relevant = {c: grouped_videos.get(c, []) for c in selected_creators}

    if len(selected_creators) == 1:
        only_creator = selected_creators[0]
        return select_for_single_creator(relevant[only_creator], target_minutes, count=3)

    return select_one_per_creator(relevant, target_minutes)
