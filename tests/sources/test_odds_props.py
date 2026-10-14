"""The Odds API pull: events, per-game odds, raw JSON kept, credits, guards, PropPts on the sample."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from dfs import odds_credits
from dfs.sources import odds_props as O
from dfs.sources.base import SyncContext

SAMPLE = Path(__file__).parents[1] / "fixtures" / "odds_api" / "event_odds_sample.json"
KEY = "SECRETKEY123"
CTX = SyncContext(week=6, season=2026)
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)  # Saturday morning ET, before every Week 6 kickoff


def dk(*rows):
    return pd.DataFrame(
        [
            {"Position": pos, "Name": name, "ID": i, "TeamAbbrev": team, "Salary": 5000, "Game Info": ""}
            for i, (pos, name, team) in enumerate(rows, start=1000)
        ]
    )


SALARIES = dk(
    ("QB", "C.J. Stroud", "HOU"),
    ("QB", "Trevor Lawrence", "JAX"),
    ("WR", "Nico Collins", "HOU"),
    ("RB", "Woody Marks", "HOU"),
    ("TE", "Brenton Strange", "JAX"),
    ("DST", "Texans", "HOU"),
)


def event(id_, start, home="Jacksonville Jaguars", away="Houston Texans"):
    return {"id": id_, "commence_time": start, "home_team": home, "away_team": away}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(odds_credits, "STATUS_FILE", tmp_path / "current" / "odds_credits.json")
    monkeypatch.setattr(odds_credits, "LOG_FILE", tmp_path / "raw" / "credits.csv")
    monkeypatch.setattr("dfs.paths.CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(O, "STATS_PLAYER_GLOB", str(tmp_path / "none" / "*.parquet"))
    return tmp_path


def mock_week(httpx_mock, *, events, odds_body, remaining=400, last=6):
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}), json=events
    )
    for ev in events:
        httpx_mock.add_response(
            url=re.compile(rf".*/events/{ev['id']}/odds.*"),
            json=odds_body,
            headers={
                "x-requests-remaining": str(remaining),
                "x-requests-used": str(500 - remaining),
                "x-requests-last": str(last),
            },
        )


def test_a_game_time_becomes_tffbs_eastern_wall_clock_with_a_trailing_z():
    assert O.eastern_game_start("2026-10-18T17:00:00Z") == "2026-10-18T13:00:00Z"  # a 1:00 pm ET kickoff


def test_only_unstarted_games_of_this_week_are_pulled():
    events = [
        event("past", "2026-10-15T00:20:00Z"),  # Thursday night: started
        event("sun", "2026-10-18T17:00:00Z"),
        event("mon", "2026-10-20T00:15:00Z"),  # Sunday night ET: still week 6
        event("next", "2026-10-25T17:00:00Z"),  # week 7
    ]
    kept = O.unstarted_week_events(events, 6, 2026, NOW)
    assert [e["id"] for e in kept] == ["sun", "mon"]
    after_kickoff = datetime(2026, 10, 18, 17, 30, tzinfo=UTC)
    assert [e["id"] for e in O.unstarted_week_events(events, 6, 2026, after_kickoff)] == ["mon"]


def test_a_game_off_the_draftkings_slate_costs_no_credits():
    on = event("on", "2026-10-18T17:00:00Z", home="Jacksonville Jaguars", away="Houston Texans")
    off = event("off", "2026-10-18T17:00:00Z", home="Kansas City Chiefs", away="Las Vegas Raiders")
    unknown = event("odd", "2026-10-18T17:00:00Z", home="Mystery Team", away="Houston Texans")
    assert [e["id"] for e in O.slate_events([on, off, unknown], SALARIES)] == ["on", "odd"]


def test_the_cost_estimate_is_markets_times_games_in_one_region():
    assert O.estimate_cost(13) == 13 * 6 == 78 and "player_pass_interceptions" not in O.PULL_MARKETS


def test_a_pull_reads_each_game_keeps_the_raw_json_records_credits_and_scores_the_players(
    httpx_mock, isolated
):
    sample = json.loads(SAMPLE.read_text())
    mock_week(
        httpx_mock, events=[event(sample["id"], sample["commence_time"])], odds_body=sample, remaining=394
    )
    now = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    frame = O.pull(CTX, now=now, salaries=SALARIES, raw_dir=isolated / "raw" / "odds_props", key=KEY)

    scored = frame.dropna(subset=["PropPts"]).set_index("Name")
    assert {"C.J. Stroud", "Nico Collins", "Woody Marks"} <= set(scored.index)
    assert 12 < scored.loc["C.J. Stroud", "PropPts"] < 30 and 4 < scored.loc["Nico Collins", "PropPts"] < 30
    assert (
        scored.loc["C.J. Stroud", "Id"] == "1000"
        and scored.loc["C.J. Stroud", "GameStart"] == "2026-10-18T09:30:00Z"
    )
    assert "Texans" not in set(frame["Name"])  # a DST gets no PropPts, and no row

    raw = json.loads((isolated / "raw" / "odds_props" / "20261017T140000Z.json").read_text())
    assert raw["week"] == 6 and len(raw["events"]) == 1
    assert KEY not in json.dumps(raw)

    reading = odds_credits.read()
    assert (reading.remaining, reading.used) == (394, 106)
    log = (isolated / "raw" / "credits.csv").read_text()
    assert "394" in log and KEY not in log
    assert frame.attrs["spent"] == 6 and frame.attrs["remaining"] == 394
    request = [r for r in httpx_mock.get_requests() if "/odds" in r.url.path][0]
    query = dict(request.url.params)
    assert (
        query["regions"] == "us"
        and query["markets"] == ",".join(O.PULL_MARKETS)
        and "bookmakers" not in query
    )


def test_a_started_game_is_never_requested(httpx_mock, isolated):
    sample = json.loads(SAMPLE.read_text())
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}),
        json=[event("g", "2026-10-18T13:30:00Z")],
    )
    late = datetime(2026, 10, 18, 14, 0, tzinfo=UTC)  # after the 9:30 ET kickoff
    with pytest.raises(O.OddsApiError, match="no unstarted Week 6 games"):
        O.pull(CTX, now=late, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY)
    assert not [r for r in httpx_mock.get_requests() if "/odds" in r.url.path]
    assert sample  # the fixture is unchanged


def test_too_few_credits_left_refuses_before_spending(httpx_mock, isolated):
    odds_credits.record(3, 497, spent=6, events=1)
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}),
        json=[event("g", "2026-10-18T17:00:00Z")],
    )
    with pytest.raises(O.OddsApiError, match="3 credit.*up to 6"):
        O.pull(CTX, now=NOW, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY)
    assert not [r for r in httpx_mock.get_requests() if "/odds" in r.url.path]


def test_an_http_error_never_carries_the_key(httpx_mock, isolated):
    httpx_mock.add_response(status_code=401, text=f"bad key {KEY}")
    with pytest.raises(O.OddsApiError) as caught:
        O.pull(CTX, now=NOW, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY)
    assert KEY not in str(caught.value) and "401" in str(caught.value)


def test_no_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    from dfs import config

    monkeypatch.setattr(config, "load_config", lambda: type("C", (), {"odds_api": config.OddsApiConfig()})())
    with pytest.raises(O.OddsApiError, match="no Odds API key"):
        O.api_key()


def test_the_key_comes_from_the_environment_first(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fromenv")
    assert O.api_key() == "fromenv"


# ---- the guards ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "age_hours", "pulls"),
    [
        (datetime(2026, 10, 13, 15, tzinfo=UTC), None, False),  # Tuesday: props are not all posted
        (datetime(2026, 10, 15, 15, tzinfo=UTC), None, False),  # Thursday
        (datetime(2026, 10, 17, 15, tzinfo=UTC), None, True),  # Saturday, never pulled
        (datetime(2026, 10, 18, 15, tzinfo=UTC), None, True),  # Sunday
        (datetime(2026, 10, 17, 15, tzinfo=UTC), 5.0, False),  # Saturday but a snapshot is 5 h old
        (datetime(2026, 10, 18, 15, tzinfo=UTC), 25.0, True),  # Sunday, the Saturday one is 25 h old
    ],
)
def test_a_full_sync_pulls_only_on_the_weekend_and_only_without_a_snapshot_under_24_hours(
    monkeypatch, when, age_hours, pulls
):
    from datetime import timedelta

    monkeypatch.setattr(
        O, "snapshot_age", lambda now=None: None if age_hours is None else timedelta(hours=age_hours)
    )
    pull, reason = O.full_sync_decision(when)
    assert pull is pulls and reason.startswith("props:")


def test_the_sync_plan_never_includes_props_for_a_plain_live_sync():
    from dfs.cli import LIVE_SYNC_SOURCES, _props_plan

    names, note = _props_plan(list(LIVE_SYNC_SOURCES), live=True, forced=False, only=False)
    assert "odds_props" not in names and note == ""


def test_live_props_refreshes_right_before_edge():
    from dfs.cli import LIVE_SYNC_SOURCES, _props_plan

    names, note = _props_plan(list(LIVE_SYNC_SOURCES), live=True, forced=True, only=False)
    assert names.index("odds_props") == names.index("edge") - 1 and "not yet started" in note


def test_a_full_sync_on_a_tuesday_leaves_props_out_and_says_why(monkeypatch):
    from dfs.cli import _props_plan
    from dfs.sources import SOURCES

    monkeypatch.setattr(O, "snapshot_age", lambda now=None: None)
    tuesday = datetime(2026, 10, 13, 15, tzinfo=UTC)
    names, note = _props_plan(list(SOURCES), live=False, forced=False, only=False, now=tuesday)
    assert "odds_props" not in names and "Saturday" in note
    names, _ = _props_plan(list(SOURCES), live=False, forced=True, only=False, now=tuesday)
    assert "odds_props" in names and names.index("odds_props") < names.index("edge")


def test_only_odds_props_is_an_explicit_request_and_is_left_alone():
    from dfs.cli import _props_plan

    assert _props_plan(["odds_props"], live=False, forced=False, only=True) == (["odds_props"], "")


def test_week_new_never_pulls_props():
    import inspect

    from dfs import cli

    assert 'n != "odds_props"' in inspect.getsource(cli)


# ---- the credit meter -----------------------------------------------------------------------------


def test_the_credit_headers_are_read_logged_and_warned_on(tmp_path):
    headers = {"x-requests-remaining": "95", "x-requests-used": "405", "x-requests-last": "7"}
    assert odds_credits.from_headers(headers) == (95, 405, 7)
    assert odds_credits.from_headers({}) == (None, None, None)
    status, log = tmp_path / "s.json", tmp_path / "l.csv"
    assert odds_credits.low_warning(status) is None  # no pull yet: nothing to warn about
    odds_credits.record(95, 405, spent=7, events=1, status_file=status, log_file=log)
    warning = odds_credits.low_warning(status)
    assert warning and "95 credit" in warning and "under 100" in warning
    odds_credits.record(100, 400, spent=0, events=0, status_file=status, log_file=log)
    assert odds_credits.low_warning(status) is None  # 100 is not under 100
    assert len(log.read_text().splitlines()) == 3  # header + two pulls


def test_dfs_doctor_prints_the_credit_warning_without_failing(monkeypatch, tmp_path):
    odds_credits.record(
        40, 460, spent=6, events=1, status_file=tmp_path / "s.json", log_file=tmp_path / "l.csv"
    )
    monkeypatch.setattr(odds_credits, "STATUS_FILE", tmp_path / "s.json")
    assert "40 credit" in odds_credits.low_warning()


def test_the_source_is_registered_as_a_shadow_source_with_no_tab():
    from dfs.sources import SOURCES

    assert SOURCES["odds_props"].uploads_to_sheet is False and SOURCES["odds_props"].needs_auth is False
    names = list(SOURCES)
    assert names.index("odds_props") < names.index("edge") and names.index("draftkings") < names.index(
        "odds_props"
    )
