"""The Cockpit tab's sheet side against a recording fake: what it reads, how soft it fails, what it writes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import cockpit, sheet_cockpit
from dfs import sheet_dk_upload as dku
from dfs.entries import SheetLineup, TabRow
from dfs.lineups import DK_UPLOAD_SHEET_HEADER
from dfs.sheets import SheetsError

NOW = datetime(2026, 10, 25, 15, 30, tzinfo=UTC)
NAMES = ("Qb One", "Rb One", "Rb Two", "Wr One", "Wr Two", "Wr Three", "Te One", "Flex Wr", "Home DST")


class FakeClient:
    def __init__(self, tabs=("Board", "Lineups", "Cockpit"), values=None):
        self.tabs = list(tabs)
        self.values = values or {}
        self.calls: list[tuple] = []

    def describe(self):
        return "Week 7", "https://example.test/sheet"

    def tab_exists(self, tab):
        return tab in self.tabs

    def list_tabs(self):
        return [SimpleNamespace(title=t) for t in self.tabs]

    def read_range_unformatted(self, tab, rng):
        return self.values[(tab, rng)]

    def write_tab(self, tab, rows, **kw):
        self.calls.append(("write_tab", tab, rows))
        if tab not in self.tabs:
            self.tabs.append(tab)
        return len(rows)

    def __getattr__(self, name):  # every formatting call is just recorded
        def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))

        return record


def cfg():
    return SimpleNamespace(
        lineups=SimpleNamespace(builder_tab="Lineups", upload_tab="DK Upload"),
        bankroll=object(),
        stakes=object(),
    )


def edge():
    return pd.DataFrame({"Name": list(NAMES), "Position": ["QB"] * 9, "Team": ["ATL"] * 9})


def tab_row(entry_id, fee, fmt, lineup):
    cells = [""] * len(DK_UPLOAD_SHEET_HEADER)
    cells[dku.column_index("Entry Fee")] = fee
    cells[dku.column_index("Format")] = fmt
    return TabRow(entry_id=entry_id, lineup=lineup, cells=(), row=2, raw=tuple(cells))


# ---- reading ----------------------------------------


def test_dk_entries_read_fee_format_and_cash_class_by_header_position(monkeypatch):
    rows = {
        "1": tab_row("1", "$25", "Double Up", "L2"),
        "2": tab_row("2", "$5.00", "GPP", ""),
        "3": tab_row("3", "free", "Freeroll", ""),
    }
    monkeypatch.setattr(dku, "read_tab_rows", lambda client, tab: rows)
    out = sheet_cockpit.dk_entries(FakeClient(), "DK Upload")
    assert [(e.entry_id, e.fee, e.fmt, e.cash, e.lineup) for e in out] == [
        ("1", 25.0, "Double Up", True, "L2"),
        ("2", 5.0, "GPP", False, ""),
        ("3", None, "Freeroll", False, ""),
    ]


def test_exposure_targets_accept_percent_text_fractions_and_whole_percents():
    grid = [
        ["Name", "Pos", "Salary", "# Lineups", "Exposure", "Target"],
        ["A", "QB", 1, 2, 0.5, "60%"],
        ["B", "RB", 1, 2, 0.5, 0.25],
        ["C", "WR", 1, 2, 0.5, 40],
        ["D", "TE", 1, 2, 0.5, ""],
        ["E", "TE", 1, 2],
    ]
    client = FakeClient(values={("Exposure", "A1:G80"): grid})
    assert sheet_cockpit.exposure_targets(client, "Exposure") == {"A": 0.6, "B": 0.25, "C": 0.4}
    client = FakeClient(values={("Exposure", "A1:G80"): [["Something", "Else"]]})
    assert sheet_cockpit.exposure_targets(client, "Exposure") == {}


def patch_reads(monkeypatch, *, dk=None, plan=None, lineups=None):
    monkeypatch.setattr(
        dku, "read_lineups", lambda client, tab: lineups if lineups is not None else [SheetLineup(1, NAMES)]
    )

    def boom(error):
        def raiser(*a, **k):
            raise error

        return raiser

    monkeypatch.setattr(sheet_cockpit, "dk_entries", dk if callable(dk) else (lambda c, t: []))
    monkeypatch.setattr(sheet_cockpit, "plan_numbers", plan if callable(plan) else (lambda *a, **k: None))
    monkeypatch.setattr(sheet_cockpit, "exposure_targets", lambda c, t: {"Qb One": 0.5})
    return boom


def test_gather_collects_every_input_and_reads_the_week_from_the_title(monkeypatch):
    patch_reads(monkeypatch)
    inputs = sheet_cockpit.gather(FakeClient(), cfg(), edge=edge(), now=NOW, season=2026)
    assert inputs.week == 7
    assert inputs.sheet_title == "Week 7"
    assert inputs.targets == {"Qb One": 0.5} and inputs.unavailable == {}
    assert len(inputs.lineups) == 1 and inputs.now == NOW


def test_an_unreadable_dk_upload_or_bankroll_becomes_a_named_gap_not_a_failure(monkeypatch):
    def dk(client, tab):
        raise SheetsError("DK Upload is gone")

    def plan(*a, **k):
        raise ValueError("Weekly Budget cell is blank")

    patch_reads(monkeypatch, dk=dk, plan=plan)
    inputs = sheet_cockpit.gather(FakeClient(), cfg(), edge=edge(), now=NOW)
    assert inputs.dk_entries is None and inputs.plan is None
    assert inputs.unavailable == {"dk": "DK Upload is gone", "plan": "Weekly Budget cell is blank"}
    text = " ".join(str(c) for row in cockpit.build_layout(inputs).rows for c in row)
    assert "DK Upload could not be read: DK Upload is gone" in text
    assert "Budget could not be read: Weekly Budget cell is blank" in text


def test_a_sheet_title_that_is_not_a_week_leaves_the_week_blank(monkeypatch):
    patch_reads(monkeypatch)
    client = FakeClient()
    client.describe = lambda: ("Template", "u")
    assert sheet_cockpit.gather(client, cfg(), edge=edge(), now=NOW).week is None


# ---- refreshing ----------------------------------------


def test_a_refresh_skips_a_sheet_without_a_cockpit_and_writes_nothing(monkeypatch):
    patch_reads(monkeypatch)
    client = FakeClient(tabs=("Board", "Lineups"))
    assert sheet_cockpit.refresh_if_present(client, cfg(), edge=edge(), now=NOW) == (True, "")
    assert client.calls == []


def test_a_refresh_rewrites_the_tab_when_it_exists(monkeypatch):
    patch_reads(monkeypatch)
    client = FakeClient()
    ok, line = sheet_cockpit.refresh_if_present(client, cfg(), edge=edge(), now=NOW)
    assert ok and line.startswith("Cockpit: built (")
    writes = [c for c in client.calls if c[0] == "write_tab"]
    assert len(writes) == 1 and writes[0][1] == "Cockpit"
    assert writes[0][2][0][0].startswith("COCKPIT  Week 7")


def test_a_failing_refresh_never_raises(monkeypatch):
    def lineups(client, tab):
        raise SheetsError("quota")

    monkeypatch.setattr(dku, "read_lineups", lineups)
    ok, line = sheet_cockpit.refresh_if_present(FakeClient(), cfg(), edge=edge(), now=NOW)
    assert not ok and "Cockpit not refreshed (quota)" in line and "dfs setup cockpit" in line


# ---- writing ----------------------------------------


def test_write_cockpit_styles_every_cue_and_section_and_freezes_the_title(monkeypatch):
    patch_reads(monkeypatch, dk=lambda c, t: [cockpit.DkEntry("1", 5.0, "GPP", False, "")])
    inputs = sheet_cockpit.gather(FakeClient(), cfg(), edge=edge(), now=NOW)
    layout = cockpit.build_layout(inputs)
    client = FakeClient()
    sheet_cockpit.write_cockpit(client, layout)
    formatted = {c[2] for c in client.calls if c[0] == "format_range"}
    for row in layout.cue_rows:
        assert f"A{row}" in formatted
    for row in layout.section_rows:
        assert f"A{row}:{sheet_cockpit.LAST_COLUMN}{row}" in formatted
    assert ("freeze", "Cockpit", {"rows": layout.status_row, "cols": 0}) in client.calls
    widths = next(c for c in client.calls if c[0] == "set_column_widths")[2]
    assert widths["A"] == sheet_cockpit.CUE_PX and len(widths) == cockpit.COLUMN_COUNT


def test_the_tab_is_placed_right_after_board_and_a_refresh_never_moves_it():
    client = FakeClient(tabs=("Instructions", "Slate Grid", "Board", "Edge Finder", "Cockpit"))
    assert sheet_cockpit.place_after_board(client).endswith("right after Board")
    props = next(c for c in client.calls if c[0] == "set_tab_properties")
    assert props[1] == "Cockpit" and props[2]["index"] == 3 and props[2]["hidden"] is False

    before = FakeClient(tabs=("Cockpit", "Board", "Edge Finder"))
    sheet_cockpit.place_after_board(before)
    assert next(c for c in before.calls if c[0] == "set_tab_properties")[2]["index"] == 1

    no_board = FakeClient(tabs=("Cockpit",))
    assert "left where it was" in sheet_cockpit.place_after_board(no_board)
    assert not [c for c in no_board.calls if c[0] == "set_tab_properties"]


@pytest.mark.parametrize("cue", cockpit.CUES)
def test_every_cue_has_a_colour(cue):
    assert cue in sheet_cockpit.CUE_COLORS
