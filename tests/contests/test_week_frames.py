"""The past-week players frame: the last DraftKings snapshot before the Sunday lock, main slate only."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from dfs.contests import analysis, week_frames
from dfs.contests.week_frames import find_snapshot, lock_utc, sunday_of, week_frame

HEADER = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\n"


def row(pos, name, salary, game, team):
    return f"{pos},{name} (1),{name},1,{pos},{salary},{game},{team},10.0,\n"


def write(folder, stamp, rows):
    path = folder / f"{stamp}Z.csv"
    path.write_text(HEADER + "".join(rows))
    return path


SUNDAY_1PM = "CIN@MIA 10/11/2026 01:00PM ET"
SUNDAY_425 = "DET@ARI 10/11/2026 04:25PM ET"
LONDON = "HOU@JAX 10/11/2026 09:30AM ET"
MONDAY = "NYJ@BUF 10/12/2026 08:15PM ET"


def test_the_week_5_lock_is_one_pm_eastern_on_sunday_october_11():
    assert str(sunday_of(2026, 5)) == "2026-10-11"
    assert lock_utc(2026, 5) == datetime(2026, 10, 11, 17, 0, tzinfo=UTC)


def test_the_last_snapshot_before_the_lock_wins_and_later_ones_are_ignored(tmp_path):
    write(tmp_path, "20261008T130902", [row("RB", "Early Back", 5000, SUNDAY_1PM, "CIN")])
    last = write(tmp_path, "20261011T163812", [row("RB", "Late Back", 5100, SUNDAY_1PM, "CIN")])
    write(tmp_path, "20261011T180000", [row("RB", "After Lock", 5200, SUNDAY_1PM, "CIN")])
    snap = find_snapshot(tmp_path, 2026, 5)
    assert snap is not None and snap.path == last


def test_a_snapshot_for_another_sundays_games_is_not_this_weeks(tmp_path):
    write(tmp_path, "20261011T120000", [row("RB", "Next Week", 5000, "CIN@MIA 10/18/2026 01:00PM ET", "CIN")])
    assert find_snapshot(tmp_path, 2026, 5) is None
    frame, note = week_frame(tmp_path, 2026, 5)
    assert frame is None and "Week 5" in note


def test_the_frame_has_team_opponent_and_salary_for_the_main_slate_only(tmp_path):
    write(
        tmp_path,
        "20261011T163812",
        [
            row("RB", "Home Back", 5000, SUNDAY_1PM, "MIA"),
            row("WR", "Away Wideout", 6100, SUNDAY_1PM, "CIN"),
            row("WR", "Late Wideout", 7000, SUNDAY_425, "DET"),
            row("WR", "London Wideout", 4000, LONDON, "HOU"),
            row("QB", "Monday Passer", 6000, MONDAY, "NYJ"),
            row("DST", "Bengals", 3500, SUNDAY_1PM, "CIN"),
        ],
    )
    frame, note = week_frame(tmp_path, 2026, 5)
    assert note == "20261011T163812Z.csv"
    assert list(frame.columns) == week_frames.FRAME_COLUMNS
    assert set(frame["Name"]) == {"Home Back", "Away Wideout", "Late Wideout", "Bengals"}
    by_name = frame.set_index("Name")
    assert (by_name.loc["Home Back", "Team"], by_name.loc["Home Back", "Opponent"]) == ("MIA", "CIN")
    assert (by_name.loc["Away Wideout", "Team"], by_name.loc["Away Wideout", "Opponent"]) == ("CIN", "MIA")
    assert by_name.loc["Late Wideout", "Salary"] == 7000
    assert by_name.loc["Bengals", "Position"] == "DST"
    assert set(frame["Week"]) == {5} and set(frame["Season"]) == {2026}


def test_a_folder_with_no_snapshots_gives_no_frame(tmp_path):
    frame, note = week_frame(tmp_path, 2026, 5)
    assert frame is None and note


FIXTURES = Path(__file__).parent / "fixtures"


def run_fixture(tmp_path, **kw):
    return analysis.run(
        [FIXTURES / "history_export.csv"],
        tmp_path / "out",
        standings_dir=FIXTURES / "standings",
        me="test_me",
        fetch_season=None,
        **kw,
    )


def tiny_row(res):
    rows = res.field_rows
    return rows[(rows["contest_id"] == "900000001") & (rows["group"] == "all")].iloc[0]


def test_the_frames_hook_builds_each_contests_week_and_reports_the_snapshot(tmp_path):
    asked = []

    def frames(season, week):
        asked.append((season, week))
        return pd.read_csv(FIXTURES / "players_week.csv"), "fake_snapshot.csv"

    res = run_fixture(tmp_path, frames=frames)
    assert (2025, 6) in asked and len(asked) == len(set(asked))  # one build per week
    assert res.frame_sources[(2025, 6)] == "fake_snapshot.csv"
    assert tiny_row(res)["avg_unspent"] == pytest.approx(2620.0)
    assert any("Week 6 2025: fake_snapshot.csv" in line for line in analysis.summary_lines(res))


def test_a_week_with_no_frame_is_a_warning_and_blank_measures_never_a_guess(tmp_path):
    res = run_fixture(tmp_path, frames=lambda season, week: (None, "no snapshot"))
    assert pd.isna(tiny_row(res)["avg_unspent"])
    assert any("no players frame for Week 6 2025: no snapshot" in w for w in res.warnings)


def test_a_supplied_frame_with_week_columns_is_cut_to_each_contests_week(tmp_path):
    players = pd.read_csv(FIXTURES / "players_week.csv")
    right = players.assign(Season=2025, Week=6)
    wrong = players.assign(Season=2025, Week=7, Salary=1)
    res = run_fixture(tmp_path, players=pd.concat([right, wrong], ignore_index=True))
    assert tiny_row(res)["avg_unspent"] == pytest.approx(2620.0)
