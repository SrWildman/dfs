import re

import pytest

from dfs.sources.base import SyncContext
from dfs.sources.rotowire_odds import BASE_URL, OddsFetchError, RotowireOddsSource

FIXTURE = [
    {
        "nickname": "Bills",
        "gameDate": "2026-09-13 13:00:00",
        "homeAway": "away",
        "abbr": "BUF",
        "draftkings_moneyline": -118,
        "draftkings_spread": -1.5,
        "draftkings_ou": 45.5,
        "draftkings_teamTotalOver": 23.5,
    },
    {
        "nickname": "Jets",
        "gameDate": "2026-09-13 13:00:00",
        "homeAway": "home",
        "abbr": "NYJ",
        "draftkings_moneyline": 102,
        "draftkings_spread": 1.5,
        "draftkings_ou": 45.5,
        "draftkings_teamTotalOver": 22.0,
    },
    {
        # no DK odds -- should be dropped
        "nickname": "Byeteam",
        "gameDate": "",
        "draftkings_moneyline": None,
        "draftkings_spread": None,
        "draftkings_ou": None,
    },
]


def test_fetch_parses_dk_odds_and_drops_bye_games(httpx_mock):
    httpx_mock.add_response(url=re.compile(re.escape(BASE_URL)), json=FIXTURE)
    df = RotowireOddsSource().fetch(SyncContext(week=1, season=2026))
    assert len(df) == 2
    assert df.iloc[0]["moneyline"] == "-118"
    assert df.iloc[1]["moneyline"] == "+102"


def test_fetch_raises_when_no_dk_odds_present(httpx_mock):
    httpx_mock.add_response(url=re.compile(re.escape(BASE_URL)), json=[FIXTURE[2]])
    with pytest.raises(OddsFetchError):
        RotowireOddsSource().fetch(SyncContext(week=1, season=2026))


def test_to_sheet_rows_matches_legacy_two_row_header():
    import pandas as pd

    df = pd.DataFrame(
        [{"team": "Bills", "date": "2026-09-13", "moneyline": "-118", "spread": "-1.5", "total": "45.5", "team_points": "23.5"}]
    )
    rows = RotowireOddsSource().to_sheet_rows(df)
    assert rows[0] == ["", "", "Win", "Cover", "Total Points", "Total Touchdowns", "Team Points", "Team TDs", "Team TDs"]
    assert rows[1] == ["Team", "Date", "Moneyline", "Spread", "Over-Under", "Over-Under", "Over-Under", "Over-Under", "Over-Under"]
    assert rows[2] == ["Bills", "2026-09-13", "-118", "-1.5", "45.5", "", "23.5", "", ""]
