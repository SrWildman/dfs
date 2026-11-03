"""Plan D item 2: non-NFL DK contests get their own Bankroll ledger and Season columns, so Cash and GPP are
NFL only and Ending Bankroll still counts every dollar."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

import pytest

from dfs import bankroll, season
from dfs.config import BankrollConfig, EntryTableConfig, SeasonConfig
from dfs.models import ContestEntry
from dfs.sheet_bankroll_other import (
    OTHER_HEADER,
    OTHER_SUMMARY_ROW,
    OtherBlockError,
    build_other_sports_block,
    problems,
    read_state,
)
from dfs.sheet_season_view import (
    CHART_COLS,
    HEADER,
    OTHER_NET_HEADER,
    OTHER_RISKED_HEADER,
    YTD_OTHER_ROW,
    SeasonOtherError,
    add_other_columns,
    other_columns_state,
)
from dfs.sheets import column_index, column_letter

CASH = EntryTableConfig(header_row=38, first_row=39, last_row=83)
GPP = EntryTableConfig(header_row=85, first_row=86, last_row=149)
BETS = EntryTableConfig(header_row=16, first_row=17, last_row=36)


class Grid:
    """An in-memory tab set: A1 cells, values and formulas alike (a formula's value is not computed)."""

    def __init__(self, cells: dict[str, dict[str, object]] | None = None):
        self.cells = {tab: dict(c) for tab, c in (cells or {}).items()}
        self.inserted: list[tuple[str, str]] = []
        self.charts = 0

    @staticmethod
    def _bounds(a1: str):
        m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", a1)
        c1, r1 = column_index(m.group(1)), int(m.group(2))
        c2, r2 = (column_index(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
        return c1, r1, c2, r2

    def _read(self, tab, a1, blank):
        c1, r1, c2, r2 = self._bounds(a1)
        cells = self.cells.get(tab, {})
        rows = [
            [cells.get(f"{column_letter(c)}{r}", blank) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)
        ]
        return rows

    def read_range(self, tab, a1):
        return [[str(v) for v in row] for row in self._read(tab, a1, "")]

    read_formula = read_range

    def read_range_unformatted(self, tab, a1):
        return self._read(tab, a1, "")

    def update_range(self, tab, a1, rows):
        c1, r1, _, _ = self._bounds(a1)
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                self.cells.setdefault(tab, {})[f"{column_letter(c1 + j)}{r1 + i}"] = v

    def update_ranges(self, tab, updates):
        for a1, rows in updates.items():
            self.update_range(tab, a1, rows)

    def insert_columns(self, tab, *, at_column, count=1):
        self.inserted.append((tab, at_column))
        start = column_index(at_column)
        moved = {}
        for key, v in self.cells.get(tab, {}).items():
            m = re.fullmatch(r"([A-Z]+)(\d+)", key)
            c = column_index(m.group(1))
            moved[f"{column_letter(c + count if c >= start else c)}{m.group(2)}"] = v
        self.cells[tab] = moved

    def format_range(self, *a, **k):
        pass

    def add_boolean_rule(self, *a, **k):
        pass

    def hide_columns(self, *a, **k):
        pass

    def delete_charts(self, *a, **k):
        pass

    def add_line_chart(self, *a, **k):
        self.charts += 1


def _entry(sport: str, key: str, fee: str, won: str, when=datetime(2026, 10, 3, 13, 0)) -> ContestEntry:
    return ContestEntry(
        sport=sport,
        game_type="Classic",
        entry_key=key,
        entry=f"{sport} contest",
        contest_key="c",
        contest_date=when,
        place=1,
        points=1.0,
        winnings_non_ticket=Decimal(won),
        winnings_ticket=Decimal("0"),
        contest_entries=100,
        entry_fee=Decimal(fee),
        prize_pool=Decimal("90"),
        places_paid=20,
    )


# --- which ledger ------------------------------------------------------------------------------------------


def test_non_nfl_entries_go_to_the_other_ledger_and_nfl_ones_keep_their_format():
    assert bankroll.ledger_bucket(_entry("MLB", "1", "1", "75")) == "other"
    assert bankroll.ledger_bucket(_entry("NHL", "2", "1", "0")) == "other"
    gpp = _entry("NFL", "3", "5", "0")
    assert bankroll.ledger_bucket(gpp) == bankroll.classify_entry(gpp) == "gpp"


def test_other_sports_by_week_sums_net_and_fees_by_nfl_week():
    entries = [
        _entry("MLB", "1", "1", "75"),  # Week 4 (Oct 3)
        _entry("MLB", "2", "0", "50"),
        _entry("NFL", "3", "25", "0"),  # NFL: not counted
        _entry("NHL", "4", "1", "5", when=datetime(2026, 10, 13, 19, 0)),  # Week 6
    ]
    assert bankroll.other_sports_by_week(entries, 2026) == {4: (124.0, 1.0), 6: (4.0, 1.0)}


def test_the_other_table_defaults_to_right_under_gpp():
    other = BankrollConfig(cash=CASH, gpp=GPP).other_table()
    assert (other.header_row, other.first_row, other.last_row) == (151, 152, 171)
    assert other.entry_key_column == GPP.entry_key_column
    set_ = EntryTableConfig(header_row=200, first_row=201, last_row=210)
    assert BankrollConfig(gpp=GPP, other=set_).other_table() == set_


# --- the Bankroll block ------------------------------------------------------------------------------------


def _bankroll_before() -> Grid:
    """Bankroll as the template has it today: B7/B9 sum rows 12-14, GPP's sums reach row 171."""
    return Grid(
        {
            "Bankroll": {
                "A7": "Weekly Cost",
                "B7": "=SUM(D12:D14)",
                "A9": "Weekly Net",
                "B9": "=SUM(H12:H14)",
                "A12": "Weekly Cash %",
                "A13": "Weekly GPP %",
                "D13": "=SUM(F86:F171)",
                "F13": "=SUM(D86:D171)",
                "A14": "Weekly Betting %",
                f"A{GPP.header_row}": "GPP Entry",
            }
        }
    )


def test_the_block_is_built_and_every_rollup_counts_it_once():
    grid = _bankroll_before()
    other = BankrollConfig(gpp=GPP).other_table()
    assert problems(read_state(grid, "Bankroll", other), other, GPP)  # doctor flags the old layout

    build_other_sports_block(grid, "Bankroll", other=other, gpp=GPP)

    cells = grid.cells["Bankroll"]
    assert [cells[f"{column_letter(i)}151"] for i in range(10)] == OTHER_HEADER
    assert cells["I152"] == '=IF(E152="","",IFERROR(H152/E152,""))'
    assert cells["J171"] == '=IF(E171="","",IFERROR(B171/E171,""))'
    r = OTHER_SUMMARY_ROW
    assert cells[f"D{r}"] == "=SUM(F152:F171)" and cells[f"F{r}"] == "=SUM(D152:D171)"
    assert cells[f"H{r}"] == f"=F{r}-D{r}"
    assert cells["B7"] == "=SUM(D11:D14)" and cells["B9"] == "=SUM(H11:H14)"  # Ending Bankroll counts it
    assert cells["D13"] == "=SUM(F86:F149)" and cells["F13"] == "=SUM(D86:D149)"  # GPP no longer reaches it
    assert problems(read_state(grid, "Bankroll", other), other, GPP) == []

    build_other_sports_block(grid, "Bankroll", other=other, gpp=GPP)  # re-running is a no-op rewrite
    assert problems(read_state(grid, "Bankroll", other), other, GPP) == []


def test_the_block_refuses_to_overwrite_anything():
    other = BankrollConfig(gpp=GPP).other_table()
    grid = _bankroll_before()
    grid.cells["Bankroll"]["A160"] = "a note Sam typed"
    with pytest.raises(OtherBlockError, match="not blank"):
        build_other_sports_block(grid, "Bankroll", other=other, gpp=GPP)
    assert "A151" not in grid.cells["Bankroll"]

    grid = _bankroll_before()
    grid.cells["Bankroll"][f"A{OTHER_SUMMARY_ROW}"] = "something else"
    with pytest.raises(OtherBlockError):
        build_other_sports_block(grid, "Bankroll", other=other, gpp=GPP)


# --- the Season tab ----------------------------------------------------------------------------------------


def _season_before() -> Grid:
    """Season before the upgrade: A:Q, R blank, the hidden chart block at S:W, a Week 4 with MLB money in
    GPP."""
    old_header = HEADER[:17]
    cells = {f"{column_letter(i)}1": h for i, h in enumerate(old_header)}
    cells.update({"S1": "Week", "T1": "Cash", "U1": "GPP", "V1": "Betting", "W1": "Total", "S2": 0})
    cells.update(
        {"A2": 3, "B2": 56.0, "C2": -18.0, "H2": 88.0, "A3": 4, "B3": -47.55, "C3": 201.0, "H3": 130.0}
    )
    for r in range(4, 20):
        cells[f"A{r}"] = r + 1
    return Grid({"Season": cells})


def test_add_other_columns_inserts_at_s_and_moves_the_chart_block():
    grid = _season_before()
    assert other_columns_state(grid.read_range("Season", "A1:Z1")[0]) == "ready"

    add_other_columns(grid, "Season")

    cells = grid.cells["Season"]
    assert grid.inserted == [("Season", "S")]
    assert (cells["R1"], cells["S1"]) == (OTHER_NET_HEADER, OTHER_RISKED_HEADER)
    assert cells["T1"] == "Week" and CHART_COLS[0] == "T"  # the chart block moved right with the insert
    assert cells["E2"] == "=B2+C2+D2+R2" and cells["Q2"] == "=SUM($E$2:E2)"
    assert cells[f"A{YTD_OTHER_ROW}"] == "Other sports"
    assert "SUM(S2:S19)" in cells["C26"]  # Total risked counts other sports
    assert grid.charts == 1
    assert other_columns_state(grid.read_range("Season", "A1:Z1")[0]) == "done"

    add_other_columns(grid, "Season")  # re-running inserts nothing more
    assert grid.inserted == [("Season", "S")]


def test_add_other_columns_stops_on_an_unexpected_layout():
    grid = _season_before()
    grid.cells["Season"]["R1"] = "Notes"
    with pytest.raises(SeasonOtherError):
        add_other_columns(grid, "Season")
    assert grid.inserted == []


def test_the_history_split_moves_non_nfl_money_from_gpp_to_other_once():
    grid = _season_before()
    add_other_columns(grid, "Season")
    cfg = SeasonConfig()
    plan = season.plan_other_split(season.read_season_rows(grid, cfg), {4: (125.0, 1.0), 9: (3.0, 1.0)})
    assert [(p.week, p.gpp_net, p.gpp_risked, p.other_net, p.other_risked) for p in plan] == [
        (4, (201.0, 76.0), (130.0, 129.0), 125.0, 1.0)  # Week 9 has no row with data yet: skipped
    ]
    season.apply_other_split(grid, cfg, plan)
    cells = grid.cells["Season"]
    assert (cells["C3"], cells["H3"], cells["R3"], cells["S3"]) == (76.0, 129.0, 125.0, 1.0)
    assert cells["C2"] == -18.0  # Week 3 had no other sports
    assert season.plan_other_split(season.read_season_rows(grid, cfg), {4: (125.0, 1.0)}) == []  # never twice


# --- a closed week's row, re-read from its own Bankroll ----------------------------------------------------


def test_read_week_close_takes_the_ledger_as_it_stands_now():
    """Week 8: the Season row taken at the close said GPP -53.50 / betting -11.21 / ending 115.36; Sam then
    zeroed a promo fee, corrected a win and settled bets, and the Bankroll tab says GPP -48 / -0.38 /
    131.69."""
    grid = Grid(
        {
            "Bankroll": {
                "B2": 131.69,
                "D12": 75.0,
                "H12": -66.0,
                "D13": 101.0,
                "H13": -48.0,
                "B17": 43.0,
                "D17": 4.95,
                "E17": 0.0,
                "B18": 58.0,
                "D18": 4.8,
                "E18": 8.0,
            }
        }
    )
    close = season.read_week_close(grid, BankrollConfig(cash=CASH, gpp=GPP, bets=BETS), 8)
    assert (close.totals.gpp_net, close.totals.gpp_risked, close.ending_bankroll) == (-48.0, 101.0, 131.69)
    assert close.betting.wins == 1 and close.betting.losses == 1
    assert round(close.betting.net, 2) == -1.75


def test_season_row_changes_names_what_moved():
    before = [8, -66.0, -53.5, -11.21, None, 115.36, 75.0, 104.0, 52.26]
    after = [8, -66.0, -48.0, -0.38, None, 131.69, 75.0, 101.0, 52.26]
    assert season.season_row_changes(before, after) == [
        "GPP Net -53.50 -> -48.00",
        "Betting Net -11.21 -> -0.38",
        "Ending Bankroll 115.36 -> 131.69",
        "GPP Risked 104.00 -> 101.00",
    ]


def test_carry_forward_includes_other_columns():
    row = ["8", "-66", "-48", "-0.38", "x", "131.69", "75", "101", "52", "5", "7", "0", "6.2"] + [""] * 4
    cols = season.extract_season_value_columns([row + ["4.5", "2"]])
    assert cols["R:S"] == [["4.5", "2"]]
