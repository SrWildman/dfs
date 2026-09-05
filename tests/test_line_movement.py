import pandas as pd
import pytest

from dfs.line_movement import LINE_MOVE_FLAG_THRESHOLD, LineMovementError, diff_odds


def _snapshot(rows: list[dict]) -> pd.DataFrame:
    base = {
        "team": "Bills",
        "date": "2026-09-13 13:00:00",
        "moneyline": "-118",
        "spread": "-1.5",
        "total": "45.5",
        "team_points": "23.5",
        "home_away": "away",
        "abbr": "BUF",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_diff_computes_deltas_for_matching_teams():
    prev = _snapshot([{"abbr": "BUF", "spread": "-1.5", "total": "45.5", "team_points": "23.5"}])
    cur = _snapshot([{"abbr": "BUF", "spread": "-2.5", "total": "47.0", "team_points": "24.8"}])

    df = diff_odds(prev, cur)
    row = df.iloc[0]
    assert row["SpreadDelta"] == -1.0
    assert row["TotalDelta"] == 1.5
    assert row["TeamPointsDelta"] == 1.3


def test_diff_drops_teams_not_present_in_both_snapshots():
    prev = _snapshot([{"abbr": "BUF"}, {"abbr": "NYJ"}])
    cur = _snapshot([{"abbr": "BUF"}])

    df = diff_odds(prev, cur)
    assert list(df["Abbr"]) == ["BUF"]


def test_diff_raises_when_no_teams_in_common():
    prev = _snapshot([{"abbr": "BUF"}])
    cur = _snapshot([{"abbr": "NYJ"}])
    with pytest.raises(LineMovementError, match="No teams in common"):
        diff_odds(prev, cur)


def test_flag_up_above_threshold():
    prev = _snapshot([{"abbr": "BUF", "team_points": "20.0"}])
    cur = _snapshot([{"abbr": "BUF", "team_points": str(20.0 + LINE_MOVE_FLAG_THRESHOLD)}])
    row = diff_odds(prev, cur).iloc[0]
    assert row["Flag"] == "LINE↑"


def test_flag_down_below_negative_threshold():
    prev = _snapshot([{"abbr": "BUF", "team_points": "20.0"}])
    cur = _snapshot([{"abbr": "BUF", "team_points": str(20.0 - LINE_MOVE_FLAG_THRESHOLD)}])
    row = diff_odds(prev, cur).iloc[0]
    assert row["Flag"] == "LINE↓"


def test_no_flag_for_small_moves():
    prev = _snapshot([{"abbr": "BUF", "team_points": "20.0"}])
    cur = _snapshot([{"abbr": "BUF", "team_points": "20.2"}])
    row = diff_odds(prev, cur).iloc[0]
    assert row["Flag"] == ""


def test_sorted_by_absolute_move_descending():
    prev = _snapshot(
        [
            {"abbr": "BUF", "team_points": "20.0"},
            {"abbr": "NYJ", "team_points": "20.0"},
            {"abbr": "MIA", "team_points": "20.0"},
        ]
    )
    cur = _snapshot(
        [
            {"abbr": "BUF", "team_points": "20.5"},
            {"abbr": "NYJ", "team_points": "24.0"},
            {"abbr": "MIA", "team_points": "19.0"},
        ]
    )
    df = diff_odds(prev, cur)
    assert list(df["Abbr"]) == ["NYJ", "MIA", "BUF"]
