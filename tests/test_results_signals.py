"""Backfilling signals: archives, the kickoff rule, the signal and reliability tables, no lookahead."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from dfs import results_loop, results_signals, signals, signals_data


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(signals_data, "SIGNALS_DIR", tmp_path / "signals")
    monkeypatch.setattr(results_loop, "RAW_DIR", tmp_path / "raw")  # empty: no snapshots to read
    return tmp_path


def _scored(week, n=30, actual_shift=0.0, ref="20261004T160000Z"):
    rows = []
    for i in range(n):
        position = ["WR", "RB", "TE"][i % 3]
        rows.append(
            {
                "season": 2026,
                "week": week,
                "Id": 1000 * week + i,
                "Name": f"P{i}",
                "Position": position,
                "Team": "DEN" if i % 2 else "KC",
                "Salary": 4000 + 100 * i,
                "GameStart": np.nan,
                "Snapshot": ref,
                "RefSnapshot": ref,
                "ProjPts": 6.0 + (i % 10),
                "SleeperPts": np.nan,
                "FantasyProsPts": np.nan,
                "AggPts": 6.0 + (i % 10),
                "UmPts": np.nan,
                "Avail": "",
                "RosterablePool": True,
                "Status": "scored",
                "DkActual": 8.0 + (i % 7) + actual_shift,
                "GsisId": f"g{i}",
            }
        )
    return pd.DataFrame(rows)


def _data(extra_week=None):
    weeks = []
    for week in (1, 2, 3, 4):
        for i in range(30):
            weeks.append(
                {
                    "GsisId": f"g{i}",
                    "Name": f"P{i}",
                    "Team": "DEN" if i % 2 else "KC",
                    "Position": ["WR", "RB", "TE"][i % 3],
                    "season": 2026,
                    "week": week,
                    "targets": 6.0,
                    "carries": 2.0,
                    "team_targets": 30.0,
                    "team_carries": 25.0,
                    "rec_xfp": 7.0,
                    "rush_xfp": 1.0,
                    "pass_xfp": 0.0,
                    "xfp": 8.0,
                    "td": 0.0,
                    "td_exp": 0.0,
                    "dk_actual": 8.0 + (i % 7) + (500.0 if extra_week and week >= extra_week else 0.0),
                }
            )
    return signals.SeasonData(season=2026, ffo_weeks=pd.DataFrame(weeks))


def _all_scored(shift_week=None):
    return pd.concat(
        [_scored(w, actual_shift=500.0 if w == shift_week else 0.0) for w in (1, 2, 3)], ignore_index=True
    )


def test_backfill_writes_one_archive_per_week_stamped_at_the_reference_moment(dirs):
    written = results_signals.backfill(_all_scored(), _data(), season=2026, weeks=[2, 3])
    assert set(written) == {2, 3}
    archives = signals_data.archive_files(2026)
    assert [p.name for _, p in archives] == [
        "signals_2026_w02_20261004T160000Z.csv",
        "signals_2026_w03_20261004T160000Z.csv",
    ]
    table = pd.read_csv(archives[0][1])
    assert {"CalPts", "Edge", "Hit3x%", "Boom%", "Bust%", "InjFrom", "MatchupGroup"} <= set(table.columns)
    assert (table["week"] == 2).all() and len(table) == 30


def test_the_backfilled_week_does_not_change_when_that_weeks_actuals_or_later_data_change(dirs):
    results_signals.backfill(_all_scored(), _data(), season=2026, weeks=[3])
    first = pd.read_csv(signals_data.archive_files(2026, 3)[0][1])
    # week 3's own actuals become absurd, and the ffopportunity file grows absurd week-3 and week-4 games
    results_signals.backfill(_all_scored(shift_week=3), _data(extra_week=3), season=2026, weeks=[3])
    second = pd.read_csv(signals_data.archive_files(2026, 3)[0][1])
    pd.testing.assert_frame_equal(first, second)


def test_a_week_with_no_scored_rows_is_skipped_not_fatal(dirs):
    assert results_signals.backfill(_all_scored(), _data(), season=2026, weeks=[9]) == {}


def test_the_archive_chosen_for_a_player_is_the_last_one_before_his_own_kickoff():
    # GameStart is Eastern wall-clock time labelled Z: 13:00 means 1 pm ET = 17:00 UTC, 18:00 means 22:00 UTC
    def archive(when, calpts):
        frame = pd.DataFrame(
            {
                "Id": [1, 2],
                "CalPts": [calpts, calpts],
                "GameStart": ["2026-10-04T13:00:00Z", "2026-10-04T18:00:00Z"],
            }
        )
        return when, frame

    early = datetime(2026, 10, 3, 12, tzinfo=UTC)
    mid = datetime(2026, 10, 4, 12, tzinfo=UTC)  # before both kickoffs
    late = datetime(2026, 10, 4, 21, tzinfo=UTC)  # after Id 1's 17:00 UTC kickoff, before Id 2's 22:00 UTC
    chosen = signals_data.select_archive_rows(
        [archive(early, 1.0), archive(mid, 2.0), archive(late, 3.0)], None
    )
    chosen = chosen.set_index("Id")
    assert chosen.loc[1, "CalPts"] == 2.0  # the late archive is after his kickoff: never used
    assert chosen.loc[2, "CalPts"] == 3.0  # his game is later, so the late archive is still before it


def test_without_a_kickoff_an_archive_counts_only_if_it_is_at_or_before_the_reference_cutoff():
    frame = pd.DataFrame({"Id": [1], "CalPts": [5.0], "GameStart": [np.nan]})
    stamp = datetime(2026, 10, 4, 12, tzinfo=UTC)
    assert (
        signals_data.select_archive_rows([(stamp, frame)], datetime(2026, 10, 4, 13, tzinfo=UTC)).shape[0]
        == 1
    )
    assert signals_data.select_archive_rows([(stamp, frame)], datetime(2026, 10, 4, 11, tzinfo=UTC)).empty
    assert signals_data.select_archive_rows([(stamp, frame)], None).empty  # cannot prove it predates the game


def _eval(rows):
    base = {
        "ProjPts": 10.0,
        "CalPts": 10.0,
        "DkActual": 10.0,
        "Edge": "",
        "InjFrom": "",
        "MatchupGroup": "",
        "MatchupTop": False,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_signal_report_computes_means_and_hit_rates_by_hand():
    frame = _eval(
        [
            {"Edge": "BUY↑", "DkActual": 14.0, "CalPts": 11.0},  # beat both
            {"Edge": "BUY↑", "DkActual": 8.0, "CalPts": 11.0},  # missed both
            {
                "Edge": "BUY↑ USAGE↑",
                "DkActual": 10.0,
                "CalPts": 9.0,
            },  # tie vs ProjPts (not a hit), beat CalPts
            {"Edge": "FADE↓", "DkActual": 6.0, "CalPts": 12.0},
            {"InjFrom": "questionable", "DkActual": 12.0},
            {"MatchupGroup": "top", "MatchupTop": True, "DkActual": 13.0},
            {"MatchupGroup": "top", "MatchupTop": False, "DkActual": 99.0},  # not a team's top two: excluded
            {"MatchupGroup": "bottom", "MatchupTop": True, "DkActual": 7.0},
        ]
    )
    report = results_signals.signal_report(frame).set_index("Signal")
    buy = report.loc["BUY↑"]
    assert buy["n"] == 3
    assert buy["VsProj"] == pytest.approx((4.0 - 2.0 + 0.0) / 3, abs=0.01)
    assert buy["HitProj"] == pytest.approx(1 / 3, abs=0.001)
    assert buy["VsCal"] == pytest.approx((3.0 - 3.0 + 1.0) / 3, abs=0.01)
    assert buy["HitCal"] == pytest.approx(2 / 3, abs=0.001)
    assert report.loc["USAGE↑", "n"] == 1
    fade = report.loc["FADE↓"]
    assert fade["VsProj"] == -4.0 and fade["HitProj"] == 1.0  # a down signal hits when he scores BELOW
    assert report.loc["INJ+ questionable", "n"] == 1
    assert report.loc["Matchup top 8", "n"] == 1 and report.loc["Matchup bottom 4", "HitProj"] == 1.0
    assert report.loc["USAGE↓", "n"] == 0 and np.isnan(
        report.loc["USAGE↓", "VsProj"]
    )  # listed even when empty
    assert report["Thin"].all()


def test_reliability_bins_predictions_into_deciles_and_flags_big_gaps_with_enough_n():
    n_per, deciles = 60, 10
    predicted, happened = [], []
    for d in range(deciles):
        level = 10 + 10 * d  # decile d predicts exactly `level` percent
        predicted += [float(level)] * n_per
        hits = round(level / 100 * n_per)
        happened += [True] * hits + [False] * (n_per - hits)
    happened = np.array(happened)
    happened[n_per : 2 * n_per] = ~happened[n_per : 2 * n_per]  # decile 2 inverted: a large gap
    actual = np.where(happened, 20.0, 5.0)  # the threshold is 3 x 5.0 = 15
    frame = pd.DataFrame({"Hit3x%": predicted, "Salary": 5000, "DkActual": actual})
    table = results_signals.reliability_report(frame)
    assert table["Measure"].unique().tolist() == ["Hit 3x"]
    assert table["n"].tolist() == [n_per] * deciles
    assert table["Realized"].iloc[0] == pytest.approx(10.0)
    assert table["Gap"].iloc[0] == pytest.approx(0.0)
    assert table[table["Flag"] == "off"]["Decile"].tolist() == [2]  # 20% predicted, 80% realized
    assert not table["Thin"].any()


def test_reliability_gap_is_never_flagged_on_a_thin_decile():
    n = 40  # deciles of 4: far too thin to flag even a huge gap
    frame = pd.DataFrame({"Hit3x%": np.linspace(5, 95, n), "Salary": 5000, "DkActual": 0.0})
    table = results_signals.reliability_report(frame)
    assert (table["Flag"] == "").all() and table["Thin"].all()


def test_bust_counts_a_score_under_two_times_salary():
    frame = pd.DataFrame(
        {"Bust%": np.linspace(10, 90, 20), "Salary": 5000, "DkActual": [9.9] * 10 + [10.0] * 10}
    )
    table = results_signals.reliability_report(frame)
    assert table["Measure"].unique().tolist() == ["Bust under 2x"]
    assert table["Realized"].tolist()[:5] == [100.0] * 5 and table["Realized"].tolist()[5:] == [0.0] * 5
