"""A sync never wipes data (Plan C 4b): `sync_guard.guard` and its place in `run_sync`."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from dfs import nfl_calendar
from dfs.config import Config
from dfs.sources.base import Source, SyncContext
from dfs.sync import run_sync
from dfs.sync_guard import BLANK_GUARD_MAX_ROWS, guard

SEASON = 2026
SUNDAY = date(2026, 10, 25)  # EDT: 1 pm ET = 17:00 UTC
WEEK = nfl_calendar.week_for_date(SUNDAY, SEASON)
EARLY = "2026-10-25T13:00:00Z"  # TFFB writes Eastern wall clock with a "Z"
LATE = "2026-10-25T16:25:00Z"
AFTER_EARLY_KICKOFF = datetime(2026, 10, 25, 19, 18, tzinfo=UTC)  # 3:18 pm ET
BEFORE_ANY_KICKOFF = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)  # 11 am ET


def _projections(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["Id", "Name", "Team", "ProjPts", "ProjOwn", "GameStart"])


def _slate() -> pd.DataFrame:
    return _projections(
        [
            (1, "Early QB", "SF", 20.0, 12.0, EARLY),
            (2, "Early WR", "SF", 15.0, 20.0, EARLY),
            (3, "Late RB", "DET", 18.0, 30.0, LATE),
            (4, "Late TE", "DET", 9.0, 5.0, LATE),
        ]
    )


def _run(name: str, new: pd.DataFrame, old: pd.DataFrame | None, now: datetime = AFTER_EARLY_KICKOFF):
    return guard(name, new, old, week=WEEK, season=SEASON, now=now)


def test_live_sync_after_kickoff_keeps_started_players_values():
    old = _slate()
    new = _slate()
    new.loc[new["Id"] == 1, "ProjPts"] = 0.0  # TFFB zeroes a started player
    new.loc[new["Id"] == 2, "ProjOwn"] = None  # and blanks another's ownership
    new.loc[new["Id"] == 3, "ProjPts"] = 21.5  # an unstarted player's real update

    result = _run("projections", new, old)

    out = result.frame.set_index("Id")
    assert out.loc[1, "ProjPts"] == 20.0
    assert out.loc[2, "ProjOwn"] == 20.0
    assert out.loc[3, "ProjPts"] == 21.5
    assert any("game has started" in n for n in result.notes)


def test_before_kickoff_every_update_is_taken():
    new = _slate()
    new.loc[new["Id"] == 1, "ProjPts"] = 22.0
    out = _run("projections", new, _slate(), now=BEFORE_ANY_KICKOFF).frame.set_index("Id")
    assert out.loc[1, "ProjPts"] == 22.0


def test_feed_with_fewer_rows_keeps_the_missing_rows():
    old = _slate()
    new = _slate()[lambda f: f["Id"].isin([3, 4])]  # the feed dropped the started game

    result = _run("projections", new, old)

    assert sorted(result.frame["Id"].tolist()) == [1, 2, 3, 4]
    assert result.frame.set_index("Id").loc[1, "ProjPts"] == 20.0
    assert any("no longer lists 2 row(s)" in n and "2 of them already kicked off" in n for n in result.notes)


def _wide_slate(n: int) -> pd.DataFrame:
    return _projections([(i, f"Late {i}", "DET", 10.0 + i, 5.0, LATE) for i in range(1, n + 1)])


def test_a_column_blanked_for_more_than_a_few_rows_keeps_its_values():
    old = _wide_slate(10)
    new = old.copy()
    lost = BLANK_GUARD_MAX_ROWS + 1
    new.loc[: lost - 1, "ProjOwn"] = None

    result = _run("projections", new, old, now=BEFORE_ANY_KICKOFF)

    assert result.frame["ProjOwn"].notna().all()
    assert any(f"blanked ProjOwn for {lost} row(s)" in n for n in result.notes)


def test_a_few_blanks_are_taken():
    old = _wide_slate(10)
    new = old.copy()
    new.loc[: BLANK_GUARD_MAX_ROWS - 1, "ProjOwn"] = None

    result = _run("projections", new, old, now=BEFORE_ANY_KICKOFF)

    assert result.frame["ProjOwn"].isna().sum() == BLANK_GUARD_MAX_ROWS
    assert result.notes == []


def test_edge_flags_may_clear_for_many_rows():
    """Blank Avail is a real value (a cleared Questionable): the column guard leaves it alone."""
    old = _wide_slate(10).assign(Avail="Q", ValAdj=range(10))
    new = old.assign(Avail="")
    result = _run("edge", new, old, now=BEFORE_ANY_KICKOFF)
    assert (result.frame["Avail"] == "").all()


def test_edge_rows_kept_are_resorted_by_valadj():
    old = _slate().assign(ValAdj=[50.0, 40.0, 60.0, 10.0])
    new = old[old["Id"].isin([3, 4])]
    out = _run("edge", new, old).frame
    assert out["ValAdj"].tolist() == [60.0, 50.0, 40.0, 10.0]


def test_a_postponed_game_is_not_frozen():
    old = _slate()
    new = _slate()
    new.loc[new["Id"] == 1, ["GameStart", "ProjPts"]] = ["2026-10-25T20:20:00Z", 19.0]
    out = _run("projections", new, old).frame.set_index("Id")
    assert out.loc[1, "ProjPts"] == 19.0


def test_last_weeks_file_is_never_merged():
    old = _slate().assign(GameStart="2026-10-18T13:00:00Z")
    new = _slate()[lambda f: f["Id"] == 3]
    result = _run("projections", new, old)
    assert result.frame["Id"].tolist() == [3]
    assert result.notes == []


def test_a_source_without_a_policy_passes_through():
    new = pd.DataFrame({"a": [1]})
    assert _run("sleeper", new, pd.DataFrame({"a": [1, 2]})).frame is new


def _dk(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ID", "Name", "TeamAbbrev", "Salary", "Game Info", "Status"])


EARLY_INFO = "SF@ATL 10/25/2026 01:00PM ET"
LATE_INFO = "GB@DET 10/25/2026 04:25PM ET"


def test_draftkings_switching_to_the_afternoon_slate_keeps_the_main_slate():
    """Week 7, 3:18 pm: DK's feed became the 4 pm-only slate with new IDs."""
    main = _dk(
        [
            (101, "Early QB", "SF", 6000, EARLY_INFO, "OUT"),
            (102, "Early WR", "ATL", 5000, EARLY_INFO, "Q"),
            (103, "Late RB", "DET", 7000, LATE_INFO, "Q"),
            (104, "Late TE", "DET", 3000, LATE_INFO, None),
        ]
    )
    afternoon = _dk(
        [
            (903, "Late RB", "DET", 8800, LATE_INFO, None),  # cleared: a real update
            (904, "Late TE", "DET", 4100, LATE_INFO, "OUT"),  # ruled out: a real update
        ]
    )

    result = _run("draftkings", afternoon, main)

    out = result.frame.set_index("ID")
    assert sorted(out.index) == [101, 102, 103, 104]
    assert out.loc[101, "Status"] == "OUT"  # started: untouched
    assert pd.isna(out.loc[103, "Status"]) or out.loc[103, "Status"] in ("", None)
    assert out.loc[104, "Status"] == "OUT"
    assert out.loc[103, "Salary"] == 7000  # the main slate's salary, not the afternoon slate's
    assert any("different slate" in n and "refreshed Status for 2" in n for n in result.notes)


def test_draftkings_new_main_slate_before_kickoff_is_taken():
    main = _dk([(101, "Early QB", "SF", 6000, EARLY_INFO, None)])
    other = _dk([(201, "Other QB", "KC", 6100, EARLY_INFO, None)])
    out = _run("draftkings", other, main, now=BEFORE_ANY_KICKOFF).frame
    assert out["ID"].tolist() == [201]


def test_draftkings_cleared_status_is_taken_for_unstarted_players():
    main = _dk([(i, f"P{i}", "DET", 5000, LATE_INFO, "Q") for i in range(10)])
    new = main.assign(Status=None)
    out = _run("draftkings", new, main).frame
    assert out["Status"].isna().all()


def test_odds_for_started_games_are_kept():
    old = pd.DataFrame(
        {
            "team": ["49ers", "Falcons", "Lions"],
            "date": ["2026-10-25 13:00:00", "2026-10-25 13:00:00", "2026-10-25 16:25:00"],
            "total": [49.5, 49.5, 51.5],
            "abbr": ["SF", "ATL", "DET"],
        }
    )
    new = old[old["abbr"] == "DET"]
    out = _run("nfl_odds", new, old).frame
    assert sorted(out["abbr"]) == ["ATL", "DET", "SF"]


# --- run_sync: the guard runs before anything is saved -----------------------------------------------------


class _DroppingProjections(Source):
    """A TFFB feed after kickoff: the started game is gone and an unstarted player moved."""

    name = "projections"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        frame = _slate()[lambda f: f["Id"].isin([3, 4])].copy()
        frame.loc[frame["Id"] == 3, "ProjPts"] = 21.0
        return frame


@pytest.fixture
def cfg():
    return Config.model_validate(
        {"google_sheets": {"sheet_id": "fake", "credentials_file": "creds.json", "tab_mappings": {}}}
    )


def test_run_sync_saves_the_guarded_frame_and_reports_it(monkeypatch, cfg, tmp_path):
    import dfs.store as store

    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "MANIFEST_FILE", tmp_path / "manifest.json")
    (tmp_path / "current").mkdir()
    _slate().to_csv(tmp_path / "current" / "projections.csv", index=False)
    monkeypatch.setattr("dfs.sync.get_source", lambda name: _DroppingProjections())

    [result] = run_sync(
        cfg, ["projections"], SyncContext(week=WEEK, season=SEASON), upload=False, now=AFTER_EARLY_KICKOFF
    )

    saved = store.load_current("projections").set_index("Id")
    assert result.ok and result.rows == 4
    assert sorted(saved.index) == [1, 2, 3, 4]
    assert saved.loc[1, "ProjPts"] == 20.0
    assert saved.loc[3, "ProjPts"] == 21.0
    assert result.notes


# --- a kept row never resurrects an OUT player ------------------------------------------------------------


def _tffb(rows: list[tuple]) -> pd.DataFrame:
    base = {
        "Position": "RB",
        "Team": "DET",
        "Opp": "GB",
        "ProjOwn": 0,
        "Salary": 6000,
        "Ceiling": 30.0,
        "ImpPts": 25.0,
        "OU": 46.5,
        "Spread": -3.0,
        "Game": "Green Bay Packers_Detroit Lions",
        "GameStart": LATE,
        "Venue": "H",
    }
    return pd.DataFrame([{**base, "Id": i, "Name": n, "ProjPts": p} for i, n, p in rows])


def _salary_file(statuses: dict[int, str]) -> pd.DataFrame:
    names = {10: "Star Back", 11: "Other Back", 12: "Third Back"}
    return pd.DataFrame(
        [
            {
                "Position": "RB",
                "Name + ID": f"{names[i]} ({i})",
                "Name": names[i],
                "ID": i,
                "Roster Position": "RB/FLEX",
                "Salary": 6000,
                "Game Info": LATE_INFO,
                "TeamAbbrev": "DET",
                "AvgPointsPerGame": 15.0,
                "Status": status,
            }
            for i, status in statuses.items()
        ]
    )


@pytest.mark.parametrize("dk_status", ["OUT", "IR"])
def test_a_dropped_row_that_dk_marks_out_is_never_built_or_offered(dk_status):
    """Rule 2 keeps Star Back's projection row when TFFB's feed drops him; DraftKings has ruled him out. The
    kept row must not bring him back: not in the builder's pool, not offered as a late swap."""
    from dfs.derived import build_edge_frame
    from dfs.late_swap_search import swap_pool
    from dfs.lineup_suggest import CASH_TAGS, GPP_TAGS, pool_for

    full = [(10, "Star Back", 30.0), (11, "Other Back", 15.0), (12, "Third Back", 12.0)]
    old_proj, old_dk = _tffb(full), _salary_file({10: "", 11: "", 12: ""})
    old_edge = build_edge_frame(old_proj, old_dk).frame

    proj = _run("projections", _tffb(full[1:]), old_proj).frame  # the feed dropped him: kept
    dk = _run("draftkings", _salary_file({10: dk_status, 11: "", 12: ""}), old_dk).frame
    edge = _run("edge", build_edge_frame(proj, dk).frame, old_edge).frame

    assert "Star Back" in proj["Name"].tolist()  # rule 2 did keep the row
    assert edge.set_index("Name").loc["Star Back", "Avail"] == dk_status
    tags = {"Star Back": "Both", "Other Back": "Both", "Third Back": "Both"}
    for wanted in (CASH_TAGS, GPP_TAGS):
        built = pool_for(edge, tags, wanted, now=AFTER_EARLY_KICKOFF, metric="ProjPts")
        assert "Star Back" not in [p.name for p in built] and built
    offered = swap_pool(
        edge, now=AFTER_EARLY_KICKOFF, metric="ProjPts", pool_names=set(tags), all_players=True
    )
    assert "Star Back" not in [p.name for p in offered] and offered


def test_the_same_kept_row_is_used_when_he_is_active():
    """The control: with no DK status he is the best back in both pools."""
    from dfs.derived import build_edge_frame
    from dfs.late_swap_search import swap_pool
    from dfs.lineup_suggest import CASH_TAGS, pool_for

    full = [(10, "Star Back", 30.0), (11, "Other Back", 15.0), (12, "Third Back", 12.0)]
    old_proj, old_dk = _tffb(full), _salary_file({10: "", 11: "", 12: ""})
    proj = _run("projections", _tffb(full[1:]), old_proj).frame
    edge = build_edge_frame(proj, _run("draftkings", old_dk, old_dk).frame).frame
    tags = {"Star Back": "Both", "Other Back": "Both"}
    assert "Star Back" in [
        p.name for p in pool_for(edge, tags, CASH_TAGS, now=AFTER_EARLY_KICKOFF, metric="ProjPts")
    ]
    offered = swap_pool(edge, now=AFTER_EARLY_KICKOFF, metric="ProjPts", pool_names=set(tags))
    assert "Star Back" in [p.name for p in offered]
