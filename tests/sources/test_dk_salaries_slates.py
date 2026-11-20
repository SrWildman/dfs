"""The draft-group picker with two Classic slates in one week (`dfs.slate`, Thanksgiving first).

The draftgroups payload is hand-made in the shape DraftKings' public list has (the fields the pickers read;
the game times as DraftKings sends them, UTC with seven fractional digits): no DraftKings data is kept in git.
Week 12 of 2026: Thanksgiving is Thursday Nov 26 (12:30, 4:30 and 8:20 pm ET), the main slate Sunday Nov 29.
"""

from __future__ import annotations

import re

import pytest

from dfs import slate
from dfs.sources.base import SyncContext
from dfs.sources.dk_salaries import (
    DRAFTGROUPS_URL,
    SALARY_CSV_URL,
    DkSalariesFetchError,
    DkSalariesSource,
    find_main_slate_draft_group,
    kickoff_eastern,
    pick_slate_draft_group,
)

THANKSGIVING = [
    "2026-11-26T17:30:00.0000000Z",
    "2026-11-26T21:30:00.0000000Z",
    "2026-11-27T01:20:00.0000000Z",
]
SUNDAY_EARLY = "2026-11-29T18:00:00.0000000Z"  # 1:00 pm ET
SUNDAY_LATE = "2026-11-29T21:25:00.0000000Z"  # 4:25 pm ET
SUNDAY_NIGHT = "2026-11-30T01:20:00.0000000Z"  # SNF, Monday in UTC
NEXT_THURSDAY = "2026-12-04T01:15:00.0000000Z"  # Week 13's TNF


def _group(group_id, starts, contest_type_id=21, state="Upcoming", sport="NFL"):
    return {
        "draftGroupId": group_id,
        "draftGroupState": state,
        "contestType": {"sport": sport, "contestTypeId": contest_type_id},
        "games": [{"startDate": t} for t in starts],
    }


PAYLOAD = {
    "draftGroups": [
        _group(9001, THANKSGIVING),  # Thanksgiving Classic, 3 games: the thu slate
        _group(9002, THANKSGIVING[1:]),  # Thanksgiving late games only, smaller
        _group(9003, THANKSGIVING[:1], contest_type_id=96),  # a Thanksgiving Showdown
        _group(9100, [SUNDAY_EARLY] * 8 + [SUNDAY_LATE] * 3),  # the Sunday main slate, 11 games
        _group(9101, [SUNDAY_EARLY] * 8),  # Sunday early only
        _group(9102, [SUNDAY_LATE, SUNDAY_NIGHT]),  # Sunday into the night
        _group(9200, [NEXT_THURSDAY, NEXT_THURSDAY]),  # next week's Thursday
        _group(9300, THANKSGIVING * 2, state="Started"),  # not upcoming
        _group(9400, THANKSGIVING * 2, sport="NBA"),
    ]
}


def test_eastern_kickoffs_from_draftkings_times():
    assert kickoff_eastern(THANKSGIVING[0]).isoformat() == "2026-11-26T12:30:00"
    assert kickoff_eastern(THANKSGIVING[2]).isoformat() == "2026-11-26T20:20:00"  # UTC Friday, ET Thursday
    assert kickoff_eastern("2026-11-26T12:30:00").isoformat() == "2026-11-26T12:30:00"  # no zone: Eastern
    assert kickoff_eastern("2026-11-26T17:30:00+00:00").hour == 12
    assert kickoff_eastern("") is None and kickoff_eastern("Thu 12:30PM") is None


def test_thanksgiving_picks_the_largest_classic_group_all_on_its_thursday():
    assert pick_slate_draft_group(PAYLOAD, "thu", 2026, 12) == (9001, 3)


def test_the_slates_week_decides_which_thursday():
    assert pick_slate_draft_group(PAYLOAD, "thu", 2026, 13) == (9200, 2)


def test_no_group_on_the_slates_day_is_an_error():
    with pytest.raises(DkSalariesFetchError, match="Saturday"):
        pick_slate_draft_group(PAYLOAD, "sat", 2026, 12)


def test_the_main_slate_still_picks_the_sunday_main(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=PAYLOAD)
    assert find_main_slate_draft_group() == 9100


CSV_BODY = (
    "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\n"
    "QB,Jared Goff (1),Jared Goff,1,QB,6500,GB@DET 11/26/2026 12:30PM ET,DET,20.1,\n"
)


def test_a_thursday_sync_fetches_the_thanksgiving_salaries(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=PAYLOAD)
    httpx_mock.add_response(url=re.compile(re.escape(SALARY_CSV_URL) + r".*draftGroupId=9001"), text=CSV_BODY)
    with slate.active("thu"):
        df = DkSalariesSource().fetch(SyncContext(week=12, season=2026))
    assert df.iloc[0]["Name"] == "Jared Goff"


def test_a_main_sync_fetches_the_main_salaries(httpx_mock):
    httpx_mock.add_response(url=DRAFTGROUPS_URL, json=PAYLOAD)
    httpx_mock.add_response(url=re.compile(re.escape(SALARY_CSV_URL) + r".*draftGroupId=9100"), text=CSV_BODY)
    df = DkSalariesSource().fetch(SyncContext(week=12, season=2026))
    assert len(df) == 1


def test_no_group_says_what_draftkings_did_list():
    with pytest.raises(DkSalariesFetchError) as err:
        pick_slate_draft_group({"draftGroups": [PAYLOAD["draftGroups"][3]]}, "thu", 2026, 12)
    assert "9100 (11 games, Sun Nov 29 1:00 pm - Sun Nov 29 4:25 pm ET)" in str(err.value)
