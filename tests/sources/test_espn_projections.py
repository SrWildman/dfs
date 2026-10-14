"""ESPN projections: the filter, picking the weekly projected entry, the stat-id map, DK scoring, errors."""

import json
import math

import httpx
import pytest

from dfs import dk_scoring
from dfs.sources import espn_projections as E
from dfs.sources.base import SyncContext

SEASON, WEEK = 2026, 6
URL = E.URL_TEMPLATE.format(season=SEASON)


def entry(stats, *, week=WEEK, source=1, split=1, id_=None):
    return {
        "id": id_ or f"11{SEASON}{week}",
        "statSourceId": source,
        "statSplitTypeId": split,
        "scoringPeriodId": week,
        "appliedTotal": 99.9,  # ESPN's own scoring: must never be used
        "stats": {str(k): v for k, v in stats.items()},
    }


def player(name, pos_id, team_id, *entries):
    """One ESPN player record (the dict inside the response's `{"player": ...}` wrapper)."""
    return {"fullName": name, "defaultPositionId": pos_id, "proTeamId": team_id, "stats": list(entries)}


QB = player(
    "Josh Allen",
    1,
    2,
    entry({3: 250.0, 4: 2.0, 20: 0.5, 24: 30.0, 25: 0.5, 72: 0.2, 19: 0.1}),
    entry({3: 999.0}, source=0),  # an ACTUAL game log: ignored
)
WR = player("Puka Nacua", 3, 14, entry({53: 7.0, 42: 99.8, 43: 0.6}))
BYE = player("Jahmyr Gibbs", 2, 8, entry({}))  # no projection: bye / out
DST = player(
    "Texans D/ST", 16, 34, entry({99: 2.5, 95: 1.0, 96: 0.8, 97: 0.1, 98: 0.05, 105: 0.2, 120: 22.0})
)


def test_the_filter_asks_for_this_weeks_projection_by_slot_sorted_by_ownership():
    header = json.loads(E.filter_header(E.OFFENSE_SLOTS, 1200, SEASON, WEEK))["players"]
    assert header["filterSlotIds"]["value"] == [0, 2, 4, 6]
    assert header["filterStatsForSourceIds"]["value"] == [1]
    assert header["filterStatsForTopScoringPeriodIds"]["additionalValue"] == ["1120266"]
    assert header["sortPercOwned"]["sortAsc"] is False and header["limit"] == 1200


def test_the_weekly_projection_is_the_projected_single_week_entry_not_an_actual_or_another_week():
    assert E.weekly_projection(QB, WEEK)[3] == 250.0
    assert E.weekly_projection(QB, WEEK + 1) is None
    assert E.weekly_projection(BYE, WEEK) is None  # empty stats = no projection, never zeros


def test_offense_scores_with_dk_rules_from_component_stats_never_espns_own_total():
    frame = E.rows_from_players([QB, WR, BYE], [DST], WEEK).set_index("Name")
    assert set(frame.index) == {"Josh Allen", "Puka Nacua", "Texans"}  # the bye player is left out
    allen = frame.loc["Josh Allen"]
    expect = dk_scoring.score_offense_row(
        {
            "pass_yd": 250.0, "pass_td": 2.0, "pass_int": 0.5, "rush_yd": 30.0, "rush_td": 0.5,
            "rec": 0.0, "rec_yd": 0.0, "rec_td": 0.0, "fum_lost": 0.2, "two_pt": 0.1,
        }
    )  # fmt: skip
    assert allen["DkPts"] == pytest.approx(expect) and allen["DkPts"] != 99.9
    assert (allen["Team"], allen["Position"]) == ("BUF", "QB")
    assert frame.loc["Puka Nacua", "rec"] == 7.0 and frame.loc["Puka Nacua", "Team"] == "LAR"


def test_receptions_fall_back_to_the_game_log_id_when_a_projection_lacks_53():
    log_style = player("Old Style", 3, 1, entry({41: 5.0, 42: 60.0}))
    assert E.rows_from_players([log_style], [], WEEK).iloc[0]["rec"] == 5.0


def test_a_defense_is_named_by_nickname_scored_on_the_dst_fields_and_keeps_its_team():
    frame = E.rows_from_players([], [DST], WEEK).iloc[0]
    expect = dk_scoring.score_dst_row(
        {
            "sack": 2.5, "def_int": 1.0, "fum_rec": 0.8, "blocked_kick": 0.1,
            "safety": 0.05, "def_td": 0.2, "points_allowed": 22.0,
        }
    )  # fmt: skip
    assert (frame["Name"], frame["Team"], frame["Position"]) == ("Texans", "HOU", "DST")
    assert frame["DkPts"] == pytest.approx(expect)


def test_every_dk_scoring_field_is_mapped_and_every_espn_team_id_is_unique():
    assert set(E.OFFENSE_STAT_IDS) | {"two_pt"} == set(dk_scoring.OFFENSE_STAT_FIELDS)
    assert set(E.DST_STAT_IDS) == set(dk_scoring.DST_STAT_FIELDS)
    assert len(set(E.TEAM_CODES.values())) == len(E.TEAM_CODES) == 32


def test_nothing_projected_at_all_fails_loudly():
    with pytest.raises(E.EspnFetchError, match="no projections for week 6"):
        E.rows_from_players([BYE], [], WEEK)


def test_fetch_sends_the_filter_header_and_a_user_agent_and_reads_both_player_lists(httpx_mock):
    httpx_mock.add_response(url=URL, json={"players": [{"player": p} for p in (QB, WR, BYE)]})
    httpx_mock.add_response(url=URL, json={"players": [{"player": DST}]})
    frame = E.EspnProjectionsSource().fetch(SyncContext(week=WEEK, season=SEASON))
    assert sorted(frame["Position"].unique()) == ["DST", "QB", "WR"]
    first, second = httpx_mock.get_requests()
    assert json.loads(first.headers["x-fantasy-filter"])["players"]["filterSlotIds"]["value"] == [0, 2, 4, 6]
    assert json.loads(second.headers["x-fantasy-filter"])["players"]["filterSlotIds"]["value"] == [16]
    assert "dfs" in first.headers["user-agent"] and "cookie" not in first.headers


def test_source_is_registered_with_no_sheet_tab_of_its_own():
    from dfs.sources import get_source

    source = get_source("espn")
    assert source.uploads_to_sheet is False and source.needs_auth is False


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>blocked</html>"),
        httpx.Response(200, json={"nope": 1}),
    ],
)
def test_a_bad_response_raises_a_clear_error(httpx_mock, response):
    httpx_mock.add_response(url=URL, status_code=response.status_code, content=response.content)
    with pytest.raises(E.EspnFetchError):
        E.fetch_projections(SEASON, WEEK)


def test_scores_are_finite_numbers():
    frame = E.rows_from_players([QB, WR], [DST], WEEK)
    assert all(math.isfinite(v) for v in frame["DkPts"])
