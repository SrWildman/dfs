import math

import httpx
import pytest

from dfs.sources.base import SyncContext
from dfs.sources.sleeper_projections import (
    POSITIONS,
    PROJECTIONS_URL_TEMPLATE,
    SleeperProjectionsFetchError,
    SleeperProjectionsSource,
    fetch_projections,
)

SEASON, WEEK = 2026, 3


def _url(dk_position: str) -> str:
    return PROJECTIONS_URL_TEMPLATE.format(season=SEASON, week=WEEK, position=POSITIONS[dk_position])


def _offense_record(first, last, team, position, **stats):
    return {
        "player": {"first_name": first, "last_name": last, "position": position},
        "team": team,
        "stats": stats,
    }


def _mock_all_positions(httpx_mock, by_position: dict[str, list[dict]]):
    for dk_position in POSITIONS:
        httpx_mock.add_response(url=_url(dk_position), json=by_position.get(dk_position, []))


def test_fetch_projections_extracts_offense_and_dst_and_scores_dk_points(httpx_mock):
    _mock_all_positions(
        httpx_mock,
        {
            "QB": [
                _offense_record(
                    "Josh",
                    "Allen",
                    "BUF",
                    "QB",
                    pass_yd=250.0,
                    pass_td=2.0,
                    pass_int=0.5,
                    rush_yd=30.0,
                    rush_td=0.5,
                )
            ],
            "DST": [
                {
                    "player": {"first_name": "Buffalo", "last_name": "Bills", "position": "DEF"},
                    "team": "BUF",
                    "stats": {"sack": 2.5, "int": 1.0, "fum_rec": 0.5, "pts_allow": 17.0},
                }
            ],
        },
    )

    df = fetch_projections(SEASON, WEEK)

    qb = df[df["Name"] == "Josh Allen"].iloc[0]
    assert qb["Team"] == "BUF"
    assert qb["Position"] == "QB"
    assert qb["DkPts"] > 0

    dst = df[df["Position"] == "DST"].iloc[0]
    assert dst["Team"] == "BUF"
    assert dst["DkPts"] > 0


def test_fetch_projections_treats_ranking_only_stats_as_no_real_projection(httpx_mock):
    # Sleeper returns a `stats` dict for every player in its database, even
    # ones it has no real weekly projection for -- just ranking metadata,
    # no actual per-stat numbers. Found live (Jayden Daniels, Caleb
    # Williams both came back exactly this shape); must score NaN, not 0.
    _mock_all_positions(
        httpx_mock,
        {"QB": [_offense_record("No", "Projection", "WAS", "QB", adp_dd_ppr=1000.0, pos_adp_dd_ppr=1000.0)]},
    )

    df = fetch_projections(SEASON, WEEK)

    row = df[df["Name"] == "No Projection"].iloc[0]
    assert math.isnan(row["DkPts"])


def test_fetch_projections_uses_pts_ppr_order_by_not_ppr(httpx_mock):
    # C3's own explicit rule: the wrong order_by key silently returns
    # placeholder records shaped exactly like real ones.
    for dk_position in POSITIONS:
        assert "order_by=pts_ppr" in _url(dk_position)
        assert "order_by=ppr" not in _url(dk_position).replace("order_by=pts_ppr", "")


def test_fetch_projections_maps_dst_to_def_query_param():
    assert POSITIONS["DST"] == "DEF"


def test_fetch_projections_raises_clear_error_on_http_failure(httpx_mock):
    # QB is the first position fetched -- fetch_projections stops there.
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=_url("QB"))

    with pytest.raises(SleeperProjectionsFetchError):
        fetch_projections(SEASON, WEEK)


def test_fetch_projections_raises_on_non_list_response(httpx_mock):
    httpx_mock.add_response(url=_url("QB"), json={"unexpected": "shape"})

    with pytest.raises(SleeperProjectionsFetchError):
        fetch_projections(SEASON, WEEK)


def test_source_fetch_delegates_to_fetch_projections(httpx_mock):
    _mock_all_positions(
        httpx_mock, {"QB": [_offense_record("Josh", "Allen", "BUF", "QB", pass_yd=250.0, pass_td=2.0)]}
    )
    df = SleeperProjectionsSource().fetch(SyncContext(week=WEEK, season=SEASON))
    assert "Josh Allen" in df["Name"].tolist()
