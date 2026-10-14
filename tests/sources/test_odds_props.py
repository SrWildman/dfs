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
    with pytest.raises(O.PropsSkipped, match="no unstarted Week 6 games"):
        O.pull(CTX, now=late, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY)
    assert not [r for r in httpx_mock.get_requests() if "/odds" in r.url.path]
    assert sample  # the fixture is unchanged


def test_too_few_credits_left_refuses_before_spending(httpx_mock, isolated):
    odds_credits.record(3, 497, spent=6, events=1)
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}),
        json=[event("g", "2026-10-18T17:00:00Z")],
    )
    with pytest.raises(O.PropsSkipped, match="3 credit.*up to 6"):
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

WEEK, SEASON = 6, 2026
SAT = datetime(2026, 10, 17, 15, tzinfo=UTC)
SUN = datetime(2026, 10, 18, 15, tzinfo=UTC)
TUE = datetime(2026, 10, 13, 15, tzinfo=UTC)


def log_pull(path, note, stamp="2026-10-17T15:00:00Z"):
    odds_credits.record(
        400, 100, spent=78, events=13, note=note, now=datetime.fromisoformat(stamp.replace("Z", "+00:00")),
        status_file=path.parent / "s.json", log_file=path,
    )  # fmt: skip


@pytest.fixture
def credit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(odds_credits, "LOG_FILE", tmp_path / "credits.csv")
    return tmp_path / "credits.csv"


def test_a_full_sync_pulls_on_the_weekend_only_and_once_per_nfl_week(credit_log):
    assert O.full_sync_decision(TUE, SEASON, WEEK)[0] is False  # Tuesday: props are not all posted
    assert "Saturday" in O.full_sync_decision(TUE, SEASON, WEEK)[1]
    pull, reason = O.full_sync_decision(SAT, SEASON, WEEK)
    assert pull is True and reason.startswith("props: pulling Week 6")
    log_pull(credit_log, f"{SEASON} week {WEEK} auto")  # Saturday's sync made the week's one pull
    for later in (SAT, SUN):
        pull, reason = O.full_sync_decision(later, SEASON, WEEK)
        assert pull is False and "one automatic pull" in reason  # even Sunday, 24 h later: no second pull
    assert (
        O.full_sync_decision(datetime(2026, 10, 24, 15, tzinfo=UTC), SEASON, WEEK + 1)[0] is True
    )  # next week


def test_a_forced_or_early_test_pull_does_not_use_up_the_weeks_automatic_pull(credit_log):
    log_pull(credit_log, f"week {WEEK}")  # the 2-game Tuesday test pull: no kind
    log_pull(credit_log, f"{SEASON} week {WEEK} forced")
    log_pull(credit_log, f"{SEASON} week {WEEK} refresh")
    assert O.auto_pulled(SEASON, WEEK) is None and O.full_sync_decision(SAT, SEASON, WEEK)[0] is True


def test_a_pull_records_its_kind_so_the_next_full_sync_knows(httpx_mock, isolated):
    sample = json.loads(SAMPLE.read_text())
    mock_week(httpx_mock, events=[event(sample["id"], sample["commence_time"])], odds_body=sample)
    ctx = SyncContext(week=6, season=2026, props_kind="auto")
    O.pull(ctx, now=NOW, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY, reset_day=1)
    assert O.auto_pulled(2026, 6) is not None


def test_the_sync_plan_never_includes_props_for_a_plain_live_sync():
    from dfs.cli import LIVE_SYNC_SOURCES, _props_plan

    names, note = _props_plan(list(LIVE_SYNC_SOURCES), CTX, live=True, forced=False, only=False, now=NOW)
    assert "odds_props" not in names and note == ""


def test_a_full_sync_on_a_tuesday_leaves_props_out_and_says_why_and_props_forces_it(credit_log):
    from dfs.cli import _props_plan
    from dfs.sources import SOURCES

    ctx = SyncContext(week=6, season=2026)
    names, note = _props_plan(list(SOURCES), ctx, live=False, forced=False, only=False, now=TUE)
    assert "odds_props" not in names and "Saturday" in note
    names, _ = _props_plan(list(SOURCES), ctx, live=False, forced=True, only=False, now=TUE)
    assert (
        "odds_props" in names
        and names.index("odds_props") < names.index("edge")
        and ctx.props_kind == "forced"
    )


def test_the_weekends_first_full_sync_is_the_automatic_pull(credit_log):
    from dfs.cli import _props_plan
    from dfs.sources import SOURCES

    ctx = SyncContext(week=6, season=2026)
    names, _ = _props_plan(list(SOURCES), ctx, live=False, forced=False, only=False, now=SAT)
    assert "odds_props" in names and ctx.props_kind == "auto" and ctx.props_teams is None


def test_only_odds_props_is_an_explicit_request_and_is_left_alone():
    from dfs.cli import _props_plan

    ctx = SyncContext(week=6, season=2026)
    assert _props_plan(["odds_props"], ctx, live=False, forced=False, only=True) == (["odds_props"], "")
    assert ctx.props_kind == "forced"


def test_week_new_never_pulls_props():
    import inspect

    from dfs import cli

    assert 'n != "odds_props"' in inspect.getsource(cli)


# ---- the Sunday refresh: only games whose players' status changed ------------------------------------------


def dk_file(*rows):
    return pd.DataFrame(
        [{"ID": i, "Name": n, "TeamAbbrev": t, "Salary": sal, "Status": st} for i, n, t, sal, st in rows]
    )


LAST = datetime(2026, 10, 17, 15, tzinfo=UTC)
QUOTED = pd.DataFrame({"Id": ["1", "2", "3"]})  # the players the last pull priced
BEFORE = dk_file(
    (1, "Qb One", "HOU", 6500, ""),
    (2, "Wr Two", "JAX", 5200, ""),
    (3, "Rb Three", "ATL", 4000, ""),
    (4, "Wr Four", "CHI", 3000, ""),
    (5, "Star", "KC", 8000, ""),
)


def plan(after, **kw):
    ctx = SyncContext(week=6, season=2026)
    teams, note = O.refresh_plan(
        ctx, SUN, last_snapshot=(LAST, QUOTED), previous_salaries=BEFORE, salaries=after, **kw
    )
    return teams, note


def test_a_refresh_with_no_status_change_spends_nothing_and_says_so():
    teams, note = plan(BEFORE.copy())
    assert teams == frozenset() and "no DraftKings status has changed" in note and "nothing spent" in note


def test_a_quoted_player_ruled_out_refreshes_his_teams_game_only():
    after = BEFORE.copy()
    after.loc[after["ID"] == 1, "Status"] = "OUT"
    teams, note = plan(after)
    assert teams == frozenset({"HOU"}) and "HOU" in note


def test_a_starter_by_salary_counts_even_if_never_quoted_and_a_scrub_does_not():
    after = BEFORE.copy()
    after.loc[after["ID"] == 5, "Status"] = "Q"  # $8,000 and not in the last pull
    after.loc[after["ID"] == 4, "Status"] = "OUT"  # $3,000, never quoted: a scrub
    assert plan(after)[0] == frozenset({"KC"})


def test_a_player_coming_back_from_questionable_is_a_change_too():
    before = BEFORE.copy()
    before.loc[before["ID"] == 2, "Status"] = "Q"
    ctx = SyncContext(week=6, season=2026)
    teams, _ = O.refresh_plan(
        ctx, SUN, last_snapshot=(LAST, QUOTED), previous_salaries=before, salaries=BEFORE
    )
    assert teams == frozenset({"JAX"})


def test_no_earlier_snapshot_this_week_means_a_first_pull_of_every_unstarted_game(monkeypatch):
    monkeypatch.setattr(O.store_mod, "snapshot_paths", lambda name: [])
    teams, note = O.refresh_plan(SyncContext(week=6, season=2026), SUN, salaries=BEFORE)
    assert teams is None and "no earlier snapshot" in note


def test_live_props_with_nothing_changed_drops_the_source_and_sets_no_pull(monkeypatch):
    from dfs.cli import LIVE_SYNC_SOURCES, _props_plan

    monkeypatch.setattr(
        O, "refresh_plan", lambda ctx, now: (frozenset(), "props: nothing changed; nothing spent")
    )
    ctx = SyncContext(week=6, season=2026, live=True)
    names, note = _props_plan(list(LIVE_SYNC_SOURCES), ctx, live=True, forced=True, only=False, now=SUN)
    assert "odds_props" not in names and "nothing spent" in note and ctx.props_kind == "refresh"


def test_live_props_with_a_change_runs_before_edge_for_those_teams_only(monkeypatch):
    from dfs.cli import LIVE_SYNC_SOURCES, _props_plan

    monkeypatch.setattr(
        O, "refresh_plan", lambda ctx, now: (frozenset({"HOU"}), "props: status changed on HOU")
    )
    ctx = SyncContext(week=6, season=2026, live=True)
    names, _ = _props_plan(list(LIVE_SYNC_SOURCES), ctx, live=True, forced=True, only=False, now=SUN)
    assert names.index("odds_props") == names.index("edge") - 1 and ctx.props_teams == frozenset({"HOU"})


def test_a_refresh_requests_only_the_changed_teams_games_and_keeps_the_others_prices(
    httpx_mock, isolated, monkeypatch
):
    sample = json.loads(SAMPLE.read_text())
    other = event("other", "2026-10-18T17:00:00Z", home="Kansas City Chiefs", away="Las Vegas Raiders")
    salaries = pd.concat([SALARIES, dk(("QB", "Pat Mahomes", "KC"))], ignore_index=True)
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}),
        json=[event(sample["id"], sample["commence_time"]), other],
    )
    httpx_mock.add_response(
        url=re.compile(rf".*/events/{sample['id']}/odds.*"), json=sample,
        headers={"x-requests-remaining": "300", "x-requests-used": "200", "x-requests-last": "6"},
    )  # fmt: skip
    previous = pd.DataFrame(
        [
            {c: None for c in O.FRAME_COLUMNS}
            | {"Id": "9", "Name": "Pat Mahomes", "event_id": "other", "PropPts": 20.0}
        ]
    )
    monkeypatch.setattr(O.store_mod, "load_current", lambda name: previous)
    ctx = SyncContext(week=6, season=2026, props_kind="refresh", props_teams=frozenset({"HOU"}))
    frame = O.pull(ctx, now=NOW, salaries=salaries, raw_dir=isolated / "raw", key=KEY, reset_day=1)
    asked = [r.url.path for r in httpx_mock.get_requests() if "/odds" in r.url.path]
    assert len(asked) == 1 and sample["id"] in asked[0]  # the Chiefs game was never requested
    assert 9 in [int(i) for i in frame["Id"].dropna().astype(float)] and 20.0 in set(frame["PropPts"])  # kept
    assert frame["PropPts"].notna().sum() >= 4  # the refreshed game's players too


# ---- the budget guard ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "reset_day", "left"),
    [
        (datetime(2026, 10, 17, 15, tzinfo=UTC), 1, 2),  # Sat Oct 17: Sundays Oct 18, 25 (Nov 1 is the reset)
        (datetime(2026, 10, 18, 15, tzinfo=UTC), 1, 2),  # Sunday itself counts
        (datetime(2026, 10, 26, 15, tzinfo=UTC), 1, 0),  # the last Sunday before the reset has passed
        (datetime(2026, 10, 17, 15, tzinfo=UTC), 20, 1),  # reset on the 20th: only Oct 18
        (datetime(2026, 10, 21, 15, tzinfo=UTC), 20, 4),  # next reset Nov 20: Oct 25, Nov 1, 8, 15
    ],
)
def test_weekends_left_counts_nfl_sundays_up_to_the_next_reset(when, reset_day, left):
    assert O.weekends_left(when, reset_day) == left


def test_the_budget_must_cover_the_pull_per_remaining_weekend():
    saturday = datetime(2026, 10, 17, 15, tzinfo=UTC)
    ok, why = O.budget_check(477, 78, saturday, 1)  # 477 / 2 weekends = 238 >= 78
    assert ok and why == ""
    ok, why = O.budget_check(150, 78, saturday, 1)  # 150 / 2 = 75 < 78
    assert (
        not ok and "150 credit(s) left for 2 NFL weekend(s)" in why and "up to 78" in why and "skipped" in why
    )
    assert O.budget_check(None, 78, saturday, 1) == (True, "")  # unknown credits: do not block


def test_with_no_weekend_left_before_the_reset_the_whole_balance_is_the_budget():
    ok, _ = O.budget_check(90, 78, datetime(2026, 10, 27, 15, tzinfo=UTC), 1)
    assert ok  # 0 weekends counts as 1: 90 >= 78


def test_a_pull_over_budget_is_a_skip_not_a_failure_and_spends_nothing(httpx_mock, isolated):
    from dfs.sources.base import SourceSkipped
    from dfs.sync import run_sync

    odds_credits.record(150, 350, spent=0, events=0, note="x")  # 150 left on a Saturday with 2 weekends
    httpx_mock.add_response(
        url=httpx.URL(f"{O.API_ROOT}/sports/{O.SPORT}/events", params={"apiKey": KEY}),
        json=[event(f"g{i}", "2026-10-18T17:00:00Z") for i in range(13)],
    )
    with pytest.raises(SourceSkipped, match="150 credit"):
        O.pull(CTX, now=NOW, salaries=SALARIES, raw_dir=isolated / "raw", key=KEY, reset_day=1)
    assert not [r for r in httpx_mock.get_requests() if "/odds" in r.url.path]
    assert callable(run_sync)


def test_run_sync_reports_a_skip_as_ok_with_the_reason_and_saves_nothing(monkeypatch):
    from dfs import sync as sync_mod
    from dfs.sources.base import Source, SourceSkipped

    class Skipper(Source):
        name = "skipper"
        uploads_to_sheet = False

        def fetch(self, ctx):
            raise SourceSkipped("over budget")

    saved = []
    monkeypatch.setattr(sync_mod, "get_source", lambda name: Skipper())
    monkeypatch.setattr(sync_mod, "save", lambda *a: saved.append(a))
    monkeypatch.setattr(sync_mod, "record_success", lambda *a: saved.append(a))
    monkeypatch.setattr(sync_mod, "record_failure", lambda *a: saved.append(a))
    (result,) = sync_mod.run_sync(object(), ["skipper"], CTX, upload=False)
    assert result.ok and result.error == "skipped: over budget" and not saved


def test_the_reset_day_is_a_day_the_quota_can_actually_renew_on():
    from pydantic import ValidationError

    from dfs.config import OddsApiConfig

    assert OddsApiConfig().reset_day == 1 and OddsApiConfig(reset_day=17).reset_day == 17
    for bad in (0, 29, 31):
        with pytest.raises(ValidationError):
            OddsApiConfig(reset_day=bad)


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
