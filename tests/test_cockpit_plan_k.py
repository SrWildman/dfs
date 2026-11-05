"""The Cockpit's Plan K blocks: 8 (inactives and pivots) and 9 (practice reports), below the first seven, and
what the sheet side gathers for them (against a fake sheet; `data/` is the test's own)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from dfs import cockpit, entered, paths, practice, sheet_cockpit, week_inputs
from dfs.cockpit import CockpitInputs
from dfs.entries import SheetLineup
from tests.test_sheet_cockpit import FakeClient, cfg, patch_reads

NOW = datetime(2026, 10, 25, 15, 30, tzinfo=UTC)  # 11:30 ET, before every game
EARLY, LATE = "2026-10-25T13:00:00Z", "2026-10-25T16:25:00Z"
NAMES = ["Qb One", "Rb One", "Rb Two", "Wr One", "Wr Two", "Wr Three", "Te One", "Flex Wr", "Home DST"]
POSITIONS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]


def edge(**avail) -> pd.DataFrame:
    rows = [
        {
            "Name": n,
            "Position": p,
            "Team": "GB",
            "Salary": 5000,
            "GameStart": LATE if i in (3, 4) else EARLY,
            "Avail": avail.get(n, ""),
            "CalPts": 12.0 - i,
            "Leverage": "",
            "GameID": "g",
        }
        for i, (n, p) in enumerate(zip(NAMES, POSITIONS, strict=True))
    ]
    rows += [
        {"Name": "Wr Pivot", "Position": "WR", "Team": "NO", "Salary": 5500, "GameStart": LATE, "Avail": "",
         "CalPts": 14.0, "Leverage": "", "GameID": "h"},
        {"Name": "Wr Pricey", "Position": "WR", "Team": "NO", "Salary": 9500, "GameStart": LATE, "Avail": "",
         "CalPts": 30.0, "Leverage": "", "GameID": "h"},
    ]  # fmt: skip
    return pd.DataFrame(rows)


def inputs(**kw) -> CockpitInputs:
    base = {
        "now": NOW,
        "sheet_title": "Week 7",
        "edge": edge(),
        "lineups": [SheetLineup(1, tuple(NAMES))],
        "week": 7,
    }
    base.update(kw)
    return CockpitInputs(**base)


def block(layout: cockpit.Layout, number: int) -> list[list]:
    marks = layout.section_rows
    start = next(r for r in marks if str(layout.rows[r - 1][0]).startswith(f"{number}."))
    later = [r for r in marks if r > start]
    end = later[0] if later else len(layout.rows) + 1
    return layout.rows[start : end - 1]


def test_the_first_seven_blocks_keep_their_order_and_the_new_ones_follow():
    layout = cockpit.build_layout(inputs())
    titles = [str(layout.rows[r - 1][0]).split(".")[0] for r in layout.section_rows]
    assert titles == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]


def test_block_8_lists_the_flagged_player_with_pivots_that_fit_the_salary():
    layout = cockpit.build_layout(inputs(edge=edge(**{"Wr Two": "OUT", "Wr One": "Q"})))
    rows = block(layout, 8)
    flagged = [r for r in rows if r[0] in ("SWAP", "LOOK")]
    assert [r[0] for r in flagged] == ["SWAP", "LOOK"]  # OUT before Q
    out = flagged[0]
    assert out[1] == "Wr Two WR GB" and out[2].startswith("OUT · Sun 4:25")
    assert out[3] == "L1 (cash)" and out[4] == "WR $10,000"  # 50,000 - 45,000 left + his 5,000
    assert out[5:7] == ["W. Pricey $9.5K 30.0 4:25", "W. Pivot $5.5K 14.0 4:25"]


def test_a_started_or_clean_lineup_says_ok():
    rows = block(cockpit.build_layout(inputs(edge=edge(**{"Rb Two": "OUT"}))), 8)  # his 1:00 game is on later
    assert any("No OUT, Doubtful or Questionable" in str(r[1]) for r in rows) is False
    later = NOW.replace(hour=17)
    rows = block(cockpit.build_layout(inputs(edge=edge(**{"Rb Two": "OUT"}), now=later)), 8)
    assert rows[0][0] == "ok"


def test_the_entered_record_wins_over_the_built_lineups():
    record = {
        "lineups": [{"names": NAMES, "label": "Entered A", "contests": [{"name": "c", "format": "SE GPP"}]}]
    }
    rows = block(cockpit.build_layout(inputs(edge=edge(**{"Wr Two": "D"}), entered=record)), 8)
    swap = next(r for r in rows if r[0] == "SWAP")
    assert swap[3] == "Entered A (gpp)"
    assert "you entered" in " ".join(str(c) for r in rows for c in r)


def test_block_9_shows_the_practice_trend_of_q_and_d_players():
    first, _ = practice.week_window(2026, 8)
    trend = practice.Trend(steps=[("Wed", "DNP"), ("Thu", "Limited")], report="Questionable")
    week = practice.WeekPractice({practice.player_key("Wr One", "GB"): trend}, 3, ["Fri"])
    rows = block(cockpit.build_layout(inputs(edge=edge(**{"Wr One": "Q"}), practice=week)), 9)
    look = next(r for r in rows if r[0] == "LOOK")
    assert look[1:7] == ["Wr One WR GB", "Q", "DNP Wed · Limited Thu", "improving", "Questionable", "L1"]
    assert "No sync on Fri" in " ".join(str(c) for r in rows for c in r)


def test_block_9_without_snapshots_says_so():
    rows = block(cockpit.build_layout(inputs()), 9)
    assert "No practice reports saved this week" in rows[0][1]


def test_gather_reads_the_entered_record_and_practice_and_saves_the_lineups_for_the_page(monkeypatch):
    patch_reads(monkeypatch, lineups=[SheetLineup(1, tuple(NAMES), marker="GPP")])
    path = entered.record_path(2026, 7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lineups": [{"names": NAMES, "contests": []}]}))
    first, _ = practice.week_window(2026, 7)
    folder = paths.DATA_DIR / practice.SNAPSHOT_SUBDIR
    folder.mkdir(parents=True)
    stamp = datetime(first.year, first.month, first.day + 1, 23, tzinfo=UTC).strftime(practice.SNAPSHOT_STAMP)
    (folder / f"{stamp}.csv").write_text(
        "season,week,Team,Name,report_status,practice_status\n2026,7,GB,Wr One,,Limited Participation in "
        "Practice\n"
    )
    live = FakeClient()
    live._cfg = SimpleNamespace(sheet_id="live-id")
    config = cfg()
    config.google_sheets = SimpleNamespace(sheet_id="live-id")
    copy = FakeClient()
    copy._cfg = SimpleNamespace(sheet_id="a-test-copy")
    sheet_cockpit.gather(copy, config, edge=edge(), now=NOW, season=2026)
    assert (
        week_inputs.load_input("sheet_lineups", 2026, 7) is None
    )  # a copy never overwrites the page's lineups
    got = sheet_cockpit.gather(live, config, edge=edge(), now=NOW, season=2026)
    assert got.entered == {"lineups": [{"names": NAMES, "contests": []}]}
    assert got.practice.for_player("Wr One", "GB").text() == "Limited Wed"
    saved = week_inputs.load_input("sheet_lineups", 2026, 7)
    assert saved["lineups"] == [{"label": "L1", "marker": "GPP", "names": NAMES}]


def test_a_sheet_whose_title_names_no_week_still_reads_this_weeks_practice(monkeypatch):
    from dfs import nfl_calendar

    patch_reads(monkeypatch)
    monkeypatch.setattr(nfl_calendar, "current_week", lambda *a, **k: 7)
    client = FakeClient()
    client.describe = lambda: ("Copy of Week 7 - scratch", "u")
    first, _ = practice.week_window(2026, 7)
    folder = paths.DATA_DIR / practice.SNAPSHOT_SUBDIR
    folder.mkdir(parents=True)
    stamp = datetime(first.year, first.month, first.day + 1, 23, tzinfo=UTC).strftime(practice.SNAPSHOT_STAMP)
    (folder / f"{stamp}.csv").write_text(
        "season,week,Team,Name,report_status,practice_status\n2026,7,GB,Wr One,,Full Participation in "
        "Practice\n"
    )
    got = sheet_cockpit.gather(client, cfg(), edge=edge(), now=NOW, season=2026)
    assert got.week is None and got.practice is not None


def test_blank_statuses_read_into_a_frame_do_not_hide_every_pivot():
    frame = edge(**{"Wr Two": "OUT"})
    frame["Avail"] = frame["Avail"].replace("", float("nan"))
    rows = block(cockpit.build_layout(inputs(edge=frame)), 8)
    swap = next(r for r in rows if r[0] == "SWAP")
    assert swap[5].startswith("W. Pricey")


def test_the_new_blocks_write_to_the_fake_sheet_with_their_cues_styled(monkeypatch):
    patch_reads(monkeypatch)
    got = sheet_cockpit.gather(FakeClient(), cfg(), edge=edge(**{"Wr Two": "OUT"}), now=NOW, season=2026)
    layout = cockpit.build_layout(got)
    client = FakeClient()
    sheet_cockpit.write_cockpit(client, layout)
    [write] = [c for c in client.calls if c[0] == "write_tab"]
    written = write[2]
    assert any(str(r[0]).startswith("8. INACTIVES AND PIVOTS") for r in written)
    assert any(str(r[0]).startswith("9. PRACTICE REPORTS") for r in written)
    assert all(len(r) == cockpit.COLUMN_COUNT for r in written)
    formatted = {c[2] for c in client.calls if c[0] == "format_range"}
    swap_row = next(i for i, r in enumerate(written, start=1) if r[0] == "SWAP" and r[1] == "Wr Two WR GB")
    assert f"A{swap_row}" in formatted
