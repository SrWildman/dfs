"""kickoff.py: TFFB's GameStart is Eastern wall-clock time labelled "Z"."""

from datetime import UTC, datetime

import pandas as pd

from dfs.kickoff import games_state, kickoff_utc, parse_kickoff


def test_edt_and_est_kickoffs_convert_to_the_right_utc_instant():
    got = kickoff_utc(pd.Series(["2026-09-20T16:05:00Z", "2026-12-06T13:00:00Z", None, "garbage"]))
    assert got.iloc[0] == pd.Timestamp("2026-09-20 20:05", tz="UTC")  # 4:05 pm EDT
    assert got.iloc[1] == pd.Timestamp("2026-12-06 18:00", tz="UTC")  # 1:00 pm EST
    assert pd.isna(got.iloc[2]) and pd.isna(got.iloc[3])


def test_the_labelled_z_is_not_trusted_as_utc():
    # nflverse publishes this kickoff as 16:05 Eastern; TFFB's string matches it digit for digit.
    assert parse_kickoff("2026-09-20T16:05:00Z") != datetime(2026, 9, 20, 16, 5, tzinfo=UTC)
    assert parse_kickoff("2026-09-20T16:05:00Z") == datetime(2026, 9, 20, 20, 5, tzinfo=UTC)


def test_parse_kickoff_returns_none_for_blank_missing_or_unparseable():
    assert parse_kickoff(None) is None
    assert parse_kickoff(float("nan")) is None
    assert parse_kickoff("  ") is None
    assert parse_kickoff("soon") is None


def test_games_state_started_and_finished_use_real_kickoff_times():
    starts = pd.Series(["2026-09-14T13:00:00Z", "2026-09-14T20:20:00Z"])  # 1 pm ET and 8:20 pm ET
    hours = 3.5
    assert games_state(starts, datetime(2026, 9, 14, 16, 30, tzinfo=UTC), hours) == (
        False,
        False,
    )  # 12:30 pm ET
    assert games_state(starts, datetime(2026, 9, 14, 17, 30, tzinfo=UTC), hours) == (
        True,
        False,
    )  # 1:30 pm ET
    # The last kickoff is 8:20 pm ET = 00:20 UTC; +3.5 h = 03:50 UTC.
    assert games_state(starts, datetime(2026, 9, 15, 3, 0, tzinfo=UTC), hours) == (True, False)
    assert games_state(starts, datetime(2026, 9, 15, 4, 0, tzinfo=UTC), hours) == (True, True)


def test_games_state_with_nothing_parseable_is_not_started_and_not_finished():
    assert games_state(pd.Series([None, "x"]), datetime(2026, 9, 14, 20, 0, tzinfo=UTC), 3.5) == (
        False,
        False,
    )
