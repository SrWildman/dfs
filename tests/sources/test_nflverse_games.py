import re

import pytest

from dfs.sources.base import SyncContext
from dfs.sources.nflverse_games import (
    GAMES_CSV_URL,
    NflverseGamesFetchError,
    NflverseGamesSource,
    parse_games_csv,
)

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _row(**overrides) -> str:
    base = {
        "game_id": "2026_01_SF_LA",
        "season": "2026",
        "game_type": "REG",
        "week": "1",
        "gameday": "2026-09-10",
        "weekday": "Thursday",
        "gametime": "20:35",
        "away_team": "SF",
        "away_score": "",
        "home_team": "LA",
        "home_score": "",
        "location": "Neutral",
        "result": "",
        "total": "",
        "overtime": "",
        "old_game_id": "",
        "gsis": "",
        "nfl_detail_id": "",
        "pfr": "",
        "pff": "",
        "espn": "",
        "ftn": "",
        "away_rest": "7",
        "home_rest": "7",
        "away_moneyline": "",
        "home_moneyline": "",
        "spread_line": "3.5",
        "away_spread_odds": "",
        "home_spread_odds": "",
        "total_line": "48.5",
        "under_odds": "",
        "over_odds": "",
        "div_game": "1",
        "roof": "dome",
        "surface": "matrixturf",
        "temp": "",
        "wind": "",
        "away_qb_id": "",
        "home_qb_id": "",
        "away_qb_name": "",
        "home_qb_name": "",
        "away_coach": "",
        "home_coach": "",
        "referee": "",
        "stadium_id": "",
        "stadium": "Melbourne Cricket Ground",
    }
    base.update(overrides)
    return ",".join(base[col] for col in HEADER.split(","))


def _csv(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows])


def test_parse_filters_to_requested_week_and_season():
    csv_text = _csv(
        [
            _row(game_id="a", season="2026", week="1"),
            _row(game_id="b", season="2026", week="2"),
            _row(game_id="c", season="2025", week="1"),
        ]
    )
    df = parse_games_csv(csv_text, season=2026, week=1)
    assert len(df) == 1
    assert df.iloc[0]["GameId"] == "a"


def test_parse_renames_and_selects_expected_columns():
    df = parse_games_csv(_csv([_row()]), season=2026, week=1)
    assert list(df.columns) == [
        "GameId",
        "Away",
        "Home",
        "Date",
        "Time",
        "Stadium",
        "Roof",
        "Surface",
        "AwayRest",
        "HomeRest",
        "DivGame",
        "Spread",
        "Total",
    ]


def test_parse_normalizes_la_to_dk_lar():
    df = parse_games_csv(_csv([_row(away_team="SF", home_team="LA")]), season=2026, week=1)
    assert df.iloc[0]["Home"] == "LAR"


def test_parse_raises_when_no_games_for_week():
    with pytest.raises(NflverseGamesFetchError, match="No games found"):
        parse_games_csv(_csv([_row(season="2026", week="1")]), season=2026, week=2)


def test_parse_raises_on_missing_expected_column():
    bad_header = HEADER.replace("roof,", "")
    csv_text = "\n".join([bad_header, _row().replace(",dome,", ",")])
    with pytest.raises(NflverseGamesFetchError, match="missing expected column"):
        parse_games_csv(csv_text, season=2026, week=1)


def test_fetch_hits_games_csv_url(httpx_mock):
    httpx_mock.add_response(url=re.compile(re.escape(GAMES_CSV_URL)), text=_csv([_row()]))
    df = NflverseGamesSource().fetch(SyncContext(week=1, season=2026))
    assert len(df) == 1
