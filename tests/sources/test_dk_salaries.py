import re

import pytest

from dfs.sources.base import SyncContext
from dfs.sources.dk_salaries import (
    DRAFTGROUPS_URL,
    SALARY_CSV_URL,
    DkSalariesFetchError,
    DkSalariesSource,
    find_main_slate_draft_group,
)

SUNDAY_1PM = "2026-09-13T17:00:00.0000000Z"  # 1PM ET
SUNDAY_NIGHT = "2026-09-13T00:20:00.0000000Z"  # SNF, not afternoon


def _draftgroups_payload(*contests):
    return {"draftGroups": list(contests)}


def _nfl_contest(draft_group_id, game_times, contest_type_id=21, state="Upcoming"):
    return {
        "draftGroupId": draft_group_id,
        "draftGroupState": state,
        "contestType": {"sport": "NFL", "contestTypeId": contest_type_id},
        "games": [{"startTime": t} for t in game_times],
    }


def test_find_main_slate_picks_largest_sunday_afternoon_only_contest(httpx_mock):
    payload = _draftgroups_payload(
        _nfl_contest(111, [SUNDAY_1PM, SUNDAY_1PM]),
        _nfl_contest(222, [SUNDAY_1PM, SUNDAY_1PM, SUNDAY_1PM]),
        _nfl_contest(333, [SUNDAY_1PM, SUNDAY_NIGHT]),  # mixed slate, excluded
    )
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=payload)
    assert find_main_slate_draft_group() == 222


def test_find_main_slate_ignores_non_nfl_and_non_millionaire(httpx_mock):
    payload = _draftgroups_payload(
        {
            "draftGroupId": 1,
            "draftGroupState": "Upcoming",
            "contestType": {"sport": "NBA", "contestTypeId": 21},
            "games": [{"startTime": SUNDAY_1PM}],
        },
        _nfl_contest(2, [SUNDAY_1PM], contest_type_id=5),  # wrong contest type
        _nfl_contest(3, [SUNDAY_1PM]),
    )
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=payload)
    assert find_main_slate_draft_group() == 3


def test_find_main_slate_raises_when_none_match(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=_draftgroups_payload())
    with pytest.raises(DkSalariesFetchError):
        find_main_slate_draft_group()


CSV_BODY = (
    "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\n"
    "RB,Jahmyr Gibbs (1),Jahmyr Gibbs,1,RB/FLEX,8000,NO@DET 09/13/2026 01:00PM ET,DET,22.3,\n"
)


def test_fetch_returns_salary_dataframe(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=_draftgroups_payload(_nfl_contest(42, [SUNDAY_1PM])))
    httpx_mock.add_response(url=re.compile(re.escape(SALARY_CSV_URL)), text=CSV_BODY)

    df = DkSalariesSource().fetch(SyncContext(week=1, season=2026))
    assert list(df.columns) == [
        "Position",
        "Name + ID",
        "Name",
        "ID",
        "Roster Position",
        "Salary",
        "Game Info",
        "TeamAbbrev",
        "AvgPointsPerGame",
        "Status",
    ]
    assert df.iloc[0]["Name"] == "Jahmyr Gibbs"
    assert df.iloc[0]["Salary"] == 8000


def test_fetch_raises_on_locked_placeholder_data(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=_draftgroups_payload(_nfl_contest(42, [SUNDAY_1PM])))
    httpx_mock.add_response(
        url=re.compile(re.escape(SALARY_CSV_URL)),
        text="Position,Name\n(LOCKED),(LOCKED)\n",
    )
    with pytest.raises(DkSalariesFetchError, match="locked"):
        DkSalariesSource().fetch(SyncContext(week=1, season=2026))
