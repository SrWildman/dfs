"""The three context sources: reduction, and fail-soft (a missing file is an empty frame, not a failure)."""

import pandas as pd
import pytest

from dfs.sources import ffopportunity, nflverse_depth, nflverse_injuries
from dfs.sources import nflverse_files as nf
from dfs.sources.base import SyncContext


def _boom(*args, **kwargs):
    raise nf.ContextFetchError("not published")


def test_injuries_keep_regular_season_skill_players_and_map_team_codes():
    raw = pd.DataFrame(
        [
            {
                "season": 2026,
                "game_type": "REG",
                "team": "LA",
                "week": 4,
                "gsis_id": "a",
                "position": "WR",
                "full_name": "A",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "game_type": "REG",
                "team": "DEN",
                "week": 4,
                "gsis_id": "b",
                "position": "LB",
                "full_name": "B",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "game_type": "POST",
                "team": "DEN",
                "week": 20,
                "gsis_id": "c",
                "position": "RB",
                "full_name": "C",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "game_type": "REG",
                "team": "DEN",
                "week": 4,
                "gsis_id": None,
                "position": "RB",
                "full_name": "D",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "game_type": "REG",
                "team": "DEN",
                "week": 4,
                "gsis_id": "e",
                "position": "HB",
                "full_name": "E",
                "report_status": "Questionable",
            },
        ]
    )
    out = nflverse_injuries.reduce_injuries(raw)
    assert out["GsisId"].tolist() == ["a", "e"]
    assert out["Team"].tolist() == ["LAR", "DEN"] and out["Position"].tolist() == ["WR", "RB"]
    assert list(out.columns) == nflverse_injuries.OUTPUT_COLUMNS
    assert nflverse_injuries.reduce_injuries(None).empty


def _depth_raw():
    rows = []
    for dt in (
        "2026-10-04T08:00:00Z",
        "2026-10-04T20:00:00Z",
        "2026-10-05T08:00:00Z",
        "2026-10-06T08:00:00Z",
    ):
        # a player appears under two formation groups: only his best pos_rank is kept
        rows += [
            {"dt": dt, "team": "DEN", "gsis_id": "a", "player_name": "A", "pos_abb": "WR", "pos_rank": 3},
            {"dt": dt, "team": "DEN", "gsis_id": "a", "player_name": "A", "pos_abb": "WR", "pos_rank": 1},
            {"dt": dt, "team": "DEN", "gsis_id": "k", "player_name": "K", "pos_abb": "K", "pos_rank": 1},
        ]
    return pd.DataFrame(rows)


def test_depth_keeps_the_last_snapshot_of_each_recent_day_and_skill_positions_only():
    out = nflverse_depth.reduce_depth(_depth_raw(), keep_days=2)
    assert sorted(out["dt"].unique()) == ["2026-10-05T08:00:00Z", "2026-10-06T08:00:00Z"]
    assert out["Position"].unique().tolist() == ["WR"] and out["pos_rank"].tolist() == [
        1,
        1,
    ]  # best rank, once
    full = nflverse_depth.reduce_depth(_depth_raw(), keep_days=None)
    assert full["dt"].nunique() == 4  # the backfill keeps every snapshot
    only_last_of_day = nflverse_depth.reduce_depth(_depth_raw(), keep_days=3)
    assert "2026-10-04T08:00:00Z" not in set(only_last_of_day["dt"])  # the day's EARLIER snapshot is dropped
    assert "2026-10-04T20:00:00Z" in set(only_last_of_day["dt"])


@pytest.mark.parametrize(
    ("module", "source", "columns"),
    [
        (nflverse_injuries, nflverse_injuries.NflverseInjuriesSource, nflverse_injuries.OUTPUT_COLUMNS),
        (nflverse_depth, nflverse_depth.NflverseDepthSource, nflverse_depth.OUTPUT_COLUMNS),
    ],
)
def test_a_missing_file_is_an_empty_frame_with_the_right_columns(monkeypatch, module, source, columns):
    monkeypatch.setattr(nf, "download", _boom)
    frame = source().fetch(SyncContext(week=5, season=2026))
    assert frame.empty and list(frame.columns) == columns
    assert source().uploads_to_sheet is False


def test_ffopportunity_returns_both_seasons_with_actual_points_and_fails_soft(monkeypatch):
    def ffo(season):
        return pd.DataFrame(
            [
                {
                    "season": season,
                    "posteam": "DEN",
                    "week": 1,
                    "player_id": "a",
                    "full_name": "A",
                    "position": "WR",
                    "rec_attempt": 5,
                    "rush_attempt": 0,
                    "rec_attempt_team": 30,
                    "rush_attempt_team": 25,
                    "receptions_exp": 4.0,
                    "rec_yards_gained_exp": 50.0,
                    "rec_touchdown_exp": 0.3,
                    "rec_touchdown": 1,
                }
            ]
        )

    monkeypatch.setattr(ffopportunity, "fetch_ffo", ffo)
    monkeypatch.setattr(
        ffopportunity,
        "fetch_offense_actual",
        lambda season: pd.DataFrame({"player_id": ["a"], "week": [1], "dk_actual": [17.0]}),
    )
    out = ffopportunity.FfopportunitySource().fetch(SyncContext(week=5, season=2026))
    assert sorted(out["season"].unique()) == [2025, 2026]
    this_season = out[out["season"] == 2026].iloc[0]
    assert this_season["dk_actual"] == 17.0 and this_season["xfp"] == pytest.approx(4 + 5 + 1.8)
    # last season's actuals ride along too: the FADE window runs across the season boundary
    assert out[out["season"] == 2025]["dk_actual"].notna().all()

    monkeypatch.setattr(ffopportunity, "fetch_ffo", _boom)
    empty = ffopportunity.FfopportunitySource().fetch(SyncContext(week=5, season=2026))
    assert empty.empty and list(empty.columns) == ffopportunity.OUTPUT_COLUMNS


def test_last_seasons_file_missing_costs_only_the_history(monkeypatch):
    def ffo(season):
        if season == 2025:
            raise nf.ContextFetchError("gone")
        return pd.DataFrame(
            [
                {
                    "season": 2026,
                    "posteam": "DEN",
                    "week": 1,
                    "player_id": "a",
                    "full_name": "A",
                    "position": "WR",
                    "receptions_exp": 4.0,
                }
            ]
        )

    monkeypatch.setattr(ffopportunity, "fetch_ffo", ffo)
    monkeypatch.setattr(ffopportunity, "fetch_offense_actual", _boom)
    out = ffopportunity.FfopportunitySource().fetch(SyncContext(week=5, season=2026))
    assert out["season"].unique().tolist() == [2026] and out["dk_actual"].isna().all()
