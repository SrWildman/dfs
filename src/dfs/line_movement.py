"""Line movement: diffs the two most recent `nfl_odds` snapshots to show
which teams' Vegas lines moved since the last sync -- what gives a
Sunday-morning re-sync something to *say* instead of silently rewriting
the same numbers with no indication anything changed.

Needs no new storage: `store.save()` has always kept every timestamped
snapshot under `data/raw/<source>/` (see `store.py`'s docstring), so
`store.load_previous("nfl_odds")` already has what this needs.

Joined on `abbr` (Rotowire's own DK-compatible team code, e.g. "LAR") --
already the more stable, exact key here, not `team` (Rotowire's nickname,
kept only for display).
"""

from __future__ import annotations

import pandas as pd

# Retuned 2026-09-17 (Phase 6, Part 1.1) the same way LEVERAGE_FLAG_THRESHOLD
# was: against real data, not intuition. The old flat 1.0 fired on 29/30
# real per-team |TeamPointsDelta| values (96.7%) from the live Week 2 odds
# history in data/raw/nfl_odds/ (baseline = earliest snapshot on disk,
# current = latest -- the same fallback dfs sync itself uses when no
# snapshot exists yet at/after the configured week's own start date) --
# because derived._flag_for_row returns on the FIRST match and LINE sits
# above LEVERAGE/CHALK, this was suppressing nearly every other flag in
# the system. That distribution: mean 2.87, std 1.94, min 0, max 8
# (quartiles 1.0 / 3.0 / 4.0; sorted values 8,7,5,5,5,5,4,4,4,4,3,3,3,3,3,
# 3,2,2,2,2,1x8,0 -- a real gap between 5 and 7 with nothing at 6). 6.0
# sits in that gap and flags 2/30 (6.7%) -- inside the 5-10% target band,
# same shape of fix as LEVERAGE_FLAG_THRESHOLD's own comment above.
LINE_MOVE_FLAG_THRESHOLD = 6.0


class LineMovementError(Exception):
    pass


def _parse_signed_float(value) -> float:
    try:
        return float(str(value).replace("+", ""))
    except (TypeError, ValueError):
        return float("nan")


def diff_odds(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Per-team deltas between two `nfl_odds` snapshots, sorted by the
    size of the move. A team present in only one snapshot (a bye week
    resolving, or a rare mid-week schedule change) is dropped -- nothing
    to diff without both sides."""
    prev = previous[["abbr", "spread", "total", "team_points"]].rename(
        columns={"spread": "SpreadPrev", "total": "TotalPrev", "team_points": "TeamPointsPrev"}
    )
    cur = current[["abbr", "team", "date", "spread", "total", "team_points"]].rename(
        columns={
            "team": "Team",
            "date": "Date",
            "spread": "SpreadCur",
            "total": "TotalCur",
            "team_points": "TeamPointsCur",
        }
    )

    merged = cur.merge(prev, on="abbr", how="inner").rename(columns={"abbr": "Abbr"})
    if merged.empty:
        raise LineMovementError(
            "No teams in common between the two most recent nfl_odds snapshots -- can't diff line movement."
        )

    for col in ("SpreadPrev", "SpreadCur", "TotalPrev", "TotalCur", "TeamPointsPrev", "TeamPointsCur"):
        merged[col] = merged[col].apply(_parse_signed_float)

    merged["SpreadDelta"] = (merged["SpreadCur"] - merged["SpreadPrev"]).round(1)
    merged["TotalDelta"] = (merged["TotalCur"] - merged["TotalPrev"]).round(1)
    merged["TeamPointsDelta"] = (merged["TeamPointsCur"] - merged["TeamPointsPrev"]).round(1)

    def _flag(delta: float) -> str:
        if pd.isna(delta):
            return ""
        if delta >= LINE_MOVE_FLAG_THRESHOLD:
            return "LINE↑"
        if delta <= -LINE_MOVE_FLAG_THRESHOLD:
            return "LINE↓"
        return ""

    merged["Flag"] = merged["TeamPointsDelta"].apply(_flag)

    columns = [
        "Abbr",
        "Team",
        "Date",
        "SpreadPrev",
        "SpreadCur",
        "SpreadDelta",
        "TotalPrev",
        "TotalCur",
        "TotalDelta",
        "TeamPointsPrev",
        "TeamPointsCur",
        "TeamPointsDelta",
        "Flag",
    ]
    return (
        merged[columns]
        .sort_values("TeamPointsDelta", key=lambda s: s.abs(), ascending=False, na_position="last")
        .reset_index(drop=True)
    )
