"""Results loop: kickoff time, snapshot selection per player, as-of loading, joins and coverage."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from dfs import results_loop as rl


def _t(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def test_tffb_game_start_is_eastern_wall_clock_time_not_utc():
    # `2026-09-20T16:05:00Z` is the 4:05 pm ET kickoff (EDT = UTC-4), so the real instant is 20:05 UTC.
    values = pd.Series(["2026-09-20T16:05:00Z", "2026-12-06T13:00:00Z", None])
    got = rl.kickoff_utc(values)
    assert got.iloc[0] == pd.Timestamp("2026-09-20 20:05", tz="UTC")
    assert got.iloc[1] == pd.Timestamp("2026-12-06 18:00", tz="UTC")  # EST after the clocks change
    assert pd.isna(got.iloc[2])


def _proj(rows):
    base = {"Name": "x", "Position": "WR", "Team": "AAA", "ProjPts": 10.0, "Ceiling": 20.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_each_player_gets_the_last_snapshot_before_his_own_kickoff():
    # Week 4: Thursday 8:15 pm ET (Oct 1), Sunday 1 pm ET (Oct 4), Monday 8:15 pm ET (Oct 5).
    players = [
        {"Id": 1, "GameStart": "2026-10-01T20:15:00Z"},  # Thursday
        {"Id": 2, "GameStart": "2026-10-04T13:00:00Z"},  # Sunday
        {"Id": 3, "GameStart": "2026-10-05T20:15:00Z"},  # Monday
    ]
    snaps = [
        (_t(9, 30, 12), _proj([{**p, "ProjPts": 1.0} for p in players])),
        (_t(10, 4, 14), _proj([{**p, "ProjPts": 2.0} for p in players])),  # 10 am ET Sunday
        (_t(10, 5, 18), _proj([{**p, "ProjPts": 3.0} for p in players])),  # 2 pm ET Monday
    ]
    chosen = rl.select_projection_rows(snaps, week=4, season=2026).set_index("Id")
    assert chosen.loc[1, "ProjPts"] == 1.0  # Thursday: only the first is before his kickoff
    assert chosen.loc[2, "ProjPts"] == 2.0  # Sunday: the 10 am Sunday snapshot
    assert chosen.loc[3, "ProjPts"] == 3.0  # Monday: Monday afternoon
    assert chosen.loc[1, "Snapshot"] == "20260930T120000Z"


def test_a_snapshot_after_kickoff_is_never_used_and_a_player_with_none_gets_no_row():
    players = [{"Id": 1, "GameStart": "2026-10-04T13:00:00Z"}]  # 17:00 UTC
    snaps = [(_t(10, 4, 18), _proj(players))]  # taken AFTER his kickoff
    assert rl.select_projection_rows(snaps, week=4, season=2026).empty


def test_sunday_morning_snapshots_count_because_game_start_is_eastern():
    # 11:20 am ET Sunday (15:20 UTC) is BEFORE the 1 pm ET (17:00 UTC) kickoff. Read as UTC it would be
    # after "13:00Z" and be wrongly discarded.
    players = [{"Id": 1, "GameStart": "2026-10-04T13:00:00Z"}]
    snaps = [
        (_t(10, 3, 12), _proj([{**players[0], "ProjPts": 1.0}])),
        (_t(10, 4, 15, 20), _proj([{**players[0], "ProjPts": 9.0}])),
    ]
    chosen = rl.select_projection_rows(snaps, week=4, season=2026)
    assert chosen.iloc[0]["ProjPts"] == 9.0


def test_a_snapshot_for_another_week_or_without_game_start_is_ignored():
    wrong_week = _proj([{"Id": 1, "GameStart": "2026-09-27T13:00:00Z"}])  # week 3
    old_schema = pd.DataFrame({"player": ["x"], "proj_pts": [5.0]})  # no GameStart / Id
    snaps = [(_t(10, 1, 12), wrong_week), (_t(10, 1, 13), old_schema)]
    assert rl.select_projection_rows(snaps, week=4, season=2026).empty


def test_reference_time_is_the_snapshot_most_players_used():
    selected = pd.DataFrame({"Snapshot": ["20261004T150000Z"] * 3 + ["20261004T100000Z"]})
    assert rl.reference_time(selected) == _t(10, 4, 15)
    assert rl.reference_time(pd.DataFrame()) is None


def test_as_of_picks_the_latest_snapshot_at_or_before_the_time(monkeypatch, tmp_path):
    monkeypatch.setattr(rl, "RAW_DIR", tmp_path)
    directory = tmp_path / "draftkings"
    directory.mkdir()
    for stamp, value in (("20261001T100000Z", 1), ("20261003T100000Z", 2), ("20261005T100000Z", 3)):
        pd.DataFrame({"v": [value]}).to_csv(directory / f"{stamp}.csv", index=False)
    assert rl.as_of("draftkings", _t(10, 4, 0))["v"].iloc[0] == 2
    assert rl.as_of("draftkings", _t(10, 3, 10))["v"].iloc[0] == 2  # exactly at the stamp counts
    assert rl.as_of("draftkings", _t(9, 1, 0)) is None  # nothing that early: absent, not an error
    assert rl.as_of("nonexistent", _t(10, 4, 0)) is None


def _dk_frame():
    return pd.DataFrame(
        [
            {"Id": "1", "Name": "Alpha Back", "Team": "AAA", "Position": "RB"},
            {"Id": "2", "Name": "Bench Guy", "Team": "AAA", "Position": "WR"},
            {"Id": "3", "Name": "Nobody Known", "Team": "AAA", "Position": "TE"},
            {"Id": "4", "Name": "Aces", "Team": "AAA", "Position": "DST"},
        ]
    )


def test_actual_points_join_marks_scored_dnp_and_unmatched_and_dst_by_team():
    week = pd.DataFrame(
        [
            {
                "player_id": "g1",
                "Name": "Alpha Back",
                "Team": "AAA",
                "Position": "RB",
                "week": 1,
                "dk_actual": 12.5,
            }
        ]
    )
    season = pd.concat(
        [
            week,
            pd.DataFrame(  # Bench Guy has a stat line in ANOTHER week only: he did not play this one.
                [
                    {
                        "player_id": "g2",
                        "Name": "Bench Guy",
                        "Team": "AAA",
                        "Position": "WR",
                        "week": 2,
                        "dk_actual": 3.0,
                    }
                ]
            ),
        ]
    )
    dst = pd.DataFrame([{"Team": "AAA", "week": 1, "Position": "DST", "dk_actual": 9.0}])
    out = rl.attach_actual(_dk_frame(), week, season, dst).set_index("Id")
    assert (out.loc["1", "Status"], out.loc["1", "DkActual"], out.loc["1", "GsisId"]) == (
        "scored",
        12.5,
        "g1",
    )
    assert out.loc["2", "Status"] == "dnp" and pd.isna(out.loc["2", "DkActual"])  # NOT an actual of 0
    assert out.loc["3", "Status"] == "unmatched"
    assert (out.loc["4", "Status"], out.loc["4", "DkActual"]) == ("scored", 9.0)


def test_coverage_reports_both_populations_and_counts_dnp_as_joined():
    scored = pd.DataFrame(
        {
            "RosterablePool": [True, True, True, False],
            "ProjPts": [10, 8, 6, 3],
            "Status": ["scored", "dnp", "unmatched", "scored"],
        }
    )
    cov = rl.coverage(scored).set_index("Population")
    pool = cov.loc["rosterable pool"]
    assert (pool["Players"], pool["Scored"], pool["DNP"], pool["Unmatched"]) == (3, 1, 1, 1)
    assert pool["JoinedPct"] == pytest.approx(66.7)
    assert cov.loc["everyone with ProjPts > 0", "Players"] == 4


def test_a_week_is_complete_only_when_every_game_has_a_final_score():
    scores = pd.DataFrame(
        {
            "week": [1, 1, 2, 2],
            "home_score": [20, 10, 17, None],
            "away_score": [13, 24, 3, None],
        }
    )
    assert rl.completed_weeks(scores) == [1]
