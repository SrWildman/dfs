import re

from dfs.config import Config
from dfs.sheet_empty_guards import (
    describe,
    find_unguarded,
    guard_formula,
    is_unguarded,
    repair_unguarded,
)


def _cfg() -> Config:
    return Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "x",
                "credentials_file": "c.json",
                "tab_mappings": {"edge": "EdgeRaw"},
            },
            "lineups": {
                "upload_tab": "DK Upload",
                "builder_tab": "Lineups",
                "player_pool_tab": "Player Pool",
            },
            "bankroll": {"tab": "Bankroll"},
        }
    )


def test_a_division_by_a_cell_returns_blank_when_the_denominator_is_empty_or_zero():
    assert guard_formula("=F2/E2") == '=IF(E2="","",IFERROR(F2/E2,""))'
    assert guard_formula("=D12/B7") == '=IF(B7="","",IFERROR(D12/B7,""))'
    assert guard_formula("=(B9/B7)+1") == '=IF(B7="","",IFERROR((B9/B7)+1,""))'
    assert guard_formula("=(D1-D2)/D1") == '=IF(D1="","",IFERROR((D1-D2)/D1,""))'
    assert guard_formula("=B23/$C$23") == '=IF($C$23="","",IFERROR(B23/$C$23,""))'


def test_two_different_denominators_fall_back_to_a_plain_iferror():
    assert guard_formula("=A1/B1+C1/D1") == '=IFERROR(A1/B1+C1/D1,"")'


def test_a_bare_average_is_guarded_on_an_empty_range():
    assert guard_formula("=AVERAGE(B2:B20)") == '=IF(COUNT(B2:B20)=0,"",AVERAGE(B2:B20))'


def test_guarding_is_idempotent_and_leaves_other_formulas_alone():
    once = guard_formula("=F2/E2")
    assert guard_formula(once) == once and not is_unguarded(once)
    for untouched in (
        '=IF(B2, B2>C2, "")',  # no division
        "=SUM(E2:E22)",
        "=B4*100/100",  # divides by a literal, never an error
        '=IF($B17="","",LET(p,$B17/100,p))',  # hand-built guard
        "typed text",
        "",
    ):
        assert guard_formula(untouched) == untouched and not is_unguarded(untouched)


class FakeClient:
    """`tabs` maps tab -> {(row, col): formula}, 1-based rows and 0-based columns."""

    def __init__(self, tabs):
        self.tabs = tabs
        self.updates: list[tuple[str, str, list[list[str]]]] = []

    def tab_exists(self, tab):
        return tab in self.tabs

    def row_count(self, tab):
        return 40

    def read_formula(self, tab, a1):
        cells = self.tabs[tab]
        last = int(re.fullmatch(r"A1:Z(\d+)", a1).group(1))
        rows = [[cells.get((r, c), "") for c in range(26)] for r in range(1, last + 1)]
        while rows and not any(rows[-1]):
            rows.pop()
        return rows

    def update_range(self, tab, a1, rows):
        self.updates.append((tab, a1, rows))


def _healthy():
    return {
        "Results": {
            (2, 6): '=IF(E2="","",IFERROR(F2/E2,""))',
            (23, 1): '=IF(COUNT(B2:B20)=0,"",AVERAGE(B2:B20))',
        },
        "Bankroll": {(12, 1): '=IF(B7="","",IFERROR(D12/B7,""))'},
        "Season": {(23, 3): '=IF(C23="","",IFERROR(B23/C23,""))'},
    }


def test_find_unguarded_is_silent_on_guarded_formulas():
    assert find_unguarded(FakeClient(_healthy()), _cfg()) == []


def test_find_unguarded_names_the_tab_column_and_rows_and_repair_rewrites_only_those():
    tabs = _healthy()
    tabs["Bankroll"] |= {(13, 1): "=D13/B7", (14, 1): "=D14/B7", (10, 1): "=(B9/B7)+1"}
    tabs["Results"][(23, 6)] = "=F23/E23"  # the totals row, outside the per-row range
    tabs["Results"][(5, 6)] = "=F5/E5"  # inside it: owned by sheet_formula_ranges, not reported here
    client = FakeClient(tabs)
    found = find_unguarded(client, _cfg())
    assert [(f.tab, f.column, f.rows) for f in found] == [
        ("Results", "G", (23,)),
        ("Bankroll", "B", (10, 13, 14)),
    ]
    assert "row(s) 10, 13, 14" in describe(found[1])
    report = repair_unguarded(client, _cfg())
    assert sorted((t, a1) for t, a1, _ in client.updates) == [
        ("Bankroll", "B10:B10"),
        ("Bankroll", "B13:B14"),
        ("Results", "G23:G23"),
    ]
    written = {a1: rows for _, a1, rows in client.updates}
    assert written["B13:B14"] == [['=IF(B7="","",IFERROR(D13/B7,""))'], ['=IF(B7="","",IFERROR(D14/B7,""))']]
    assert len(report) == 3


def test_a_tab_missing_from_the_sheet_is_skipped_by_the_repair():
    client = FakeClient({"Results": {}})
    assert repair_unguarded(client, _cfg()) == []
