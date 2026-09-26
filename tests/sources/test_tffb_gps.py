import pandas as pd
import pytest

from dfs.sources.tffb_gps import (
    GPS_EXPECTED_HEADER,
    TffbGpsFetchError,
    _is_pace_of_play_link,
    parse_gps_csv,
)


def _real_csv(week: int) -> str:
    """Shape confirmed against real Week 2/Week 3 CSVs (Step 0, 2026-09-26):
    title line, group-label banner line, then the real header, then one row
    per team. Two teams (a full game) is enough to exercise the parser."""
    header = ",".join(GPS_EXPECTED_HEADER)
    return (
        f"NFL Pace of Play for Week {week},,,,,,,,,,,,,,,,\r\n"
        f"Week {week},,,,Pace of Play,,,EPA Ranks per FPts,,,,Game Pace Scores,,,,,\r\n"
        f"{header}\r\n"
        ",JAX,Jacksonville Jaguars,29.0,22,55.5,-5.0,7,7,14,15,H,,New England Patriots,46.5,MAIN,2.75\r\n"
        ",NE,New England Patriots,17.5,19,60.0,2.0,20,24,2,17,A,,Jacksonville Jaguars,46.5,MAIN,2.75\r\n"
    )


def test_parse_gps_csv_real_shape():
    df = parse_gps_csv(_real_csv(3))
    assert list(df.columns) == ["Team", "ImpliedTotal", "GPS"]
    assert list(df["Team"]) == ["JAX", "NE"]
    assert list(df["ImpliedTotal"]) == [29.0, 17.5]
    assert list(df["GPS"]) == [2.75, 2.75]


def test_parse_gps_csv_normalizes_team_code():
    text = _real_csv(3).replace(",JAX,", ",JAC,")  # a known drift normalize_team already fixes
    df = parse_gps_csv(text)
    assert df.iloc[0]["Team"] == "JAX"


def test_parse_gps_csv_raises_on_header_drift():
    text = _real_csv(3).replace("Neutral Pace Rk", "Something Else")
    with pytest.raises(TffbGpsFetchError, match="doesn't match the expected shape"):
        parse_gps_csv(text)


def test_parse_gps_csv_coerces_non_numeric_to_nan():
    text = _real_csv(3).replace("29.0", "N/A", 1)
    df = parse_gps_csv(text)
    assert pd.isna(df.iloc[0]["ImpliedTotal"])


@pytest.mark.parametrize(
    ("href", "text", "week", "expected"),
    [
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-2-fantasy-football/", "", 2, True),
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-3-fantasy-football/", "", 3, True),
        ("https://x.com/dfs/nfl-dfs-pace-of-play-stacks-for-week-1-fantasy-football/", "", 1, True),
        # Week 1 must never match week 10-19 -- the real bug this guards against.
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-10-fantasy-football/", "", 1, False),
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-11-fantasy-football/", "", 1, False),
        # Wrong week entirely.
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-2-fantasy-football/", "", 3, False),
        # Right week, wrong article (no "pace of play" anywhere).
        ("https://x.com/dfs/best-plays-for-dfs-week-2-fantasy-football/", "", 2, False),
        # Matches via link text instead of href.
        ("https://x.com/some-slug/", "Pace of Play: Matchups & Stacks for Week 2", 2, True),
    ],
)
def test_is_pace_of_play_link(href, text, week, expected):
    assert _is_pace_of_play_link(href, text, week) is expected
