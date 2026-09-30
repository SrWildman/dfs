import dfs.sheet_formula_ranges as mod
from dfs.config import Config
from dfs.sheet_formula_ranges import (
    DKSALCLEAN_TAB,
    FormulaRange,
    describe_gap,
    find_gaps,
    formula_ranges,
    referenced_rows,
    repair_formula_ranges,
    shift_rows,
)
from dfs.sheet_links import PLAYER_POOL_RAW_BLOCK, PLAYER_POOL_RAW_TAB
from dfs.sheets import column_letter


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


class FakeClient:
    """A tab is `{row: {col_index: text}}`; read_formula returns the rectangle asked for."""

    def __init__(self, tabs: dict[str, dict[int, dict[int, str]]], row_counts: dict[str, int] | None = None):
        self.tabs = tabs
        self.row_counts = row_counts or {}
        self.updates: list[tuple[str, str, list[list[str]]]] = []
        self.cleared: list[tuple[str, list[str]]] = []

    def read_formula(self, tab, a1):
        import re

        m = re.fullmatch(r"A(\d+):([A-Z]+)(\d+)", a1)
        first, last = int(m.group(1)), int(m.group(3))
        width = 0
        for ch in m.group(2):
            width = width * 26 + ord(ch) - 64
        rows = []
        for r in range(first, last + 1):
            cells = self.tabs[tab].get(r, {})
            rows.append([cells.get(c, "") for c in range(width)])
        while rows and not any(rows[-1]):
            rows.pop()  # gspread trims trailing empty rows
        return rows

    def row_count(self, tab):
        return self.row_counts.get(tab, 1000)

    def update_range(self, tab, a1, rows):
        self.updates.append((tab, a1, rows))

    def clear_ranges(self, tab, ranges):
        self.cleared.append((tab, ranges))


def test_referenced_rows_reads_relative_refs_only():
    assert referenced_rows('=IF($B2="DST",VLOOKUP($A2,TFFBOptoRaw!$D:F,2,false),F2/E2)') == {2}
    assert referenced_rows("=DKSalRaw!A3083") == {3083}
    assert referenced_rows("=SUM($B$2:B5)") == {5}  # $B$2 is deliberately anchored, B5 is the row
    assert referenced_rows("=LOG10(A7)") == {7}  # not a cell ref: LOG10(


def test_shift_rows_rewrites_only_the_pattern_rows_own_references():
    formula = '=IF(DKSalRaw!H745 = INDEX(SPLIT(DKSalRaw!G745,"@"),0,1), $A745, "")'
    expected = '=IF(DKSalRaw!H900 = INDEX(SPLIT(DKSalRaw!G900,"@"),0,1), $A900, "")'
    assert shift_rows(formula, 745, 900) == expected
    assert shift_rows("=SUM($B$2:B5)", 5, 6) == "=SUM($B$2:B6)"  # the anchored $B$2 stays
    assert shift_rows("=F2/E2+G3", 2, 9) == "=F9/E9+G3"


def _dksalclean(rows: dict[int, dict[int, str]]) -> FormulaRange:
    return FormulaRange(DKSALCLEAN_TAB, {0: "Position", 1: "Team"}, 2, 10, clear_tail=True)


def _healthy(first: int, last: int) -> dict[int, dict[int, str]]:
    return {r: {0: f"=DKSalRaw!A{r}", 1: f"=DKSalRaw!H{r}"} for r in range(first, last + 1)}


def test_find_gaps_reports_a_missing_row_and_a_row_that_reads_another_row():
    tab = _healthy(2, 10)
    tab[5] = {0: "typed", 1: "=DKSalRaw!H5"}  # a typed value, not a formula
    tab[8][1] = "=DKSalRaw!H2091"  # the old row-deletion artifact
    gaps = find_gaps(FakeClient({DKSALCLEAN_TAB: tab}), _dksalclean(tab))
    assert [(g.column, g.kind, g.rows) for g in gaps] == [("A", "missing", (5,)), ("B", "wrong-row", (8,))]
    assert describe_gap(gaps[0]) == "'DkSalClean' column A (Position): row(s) 5 have no formula"


def test_find_gaps_is_silent_on_a_healthy_range_and_ignores_wrong_rows_when_not_same_row():
    tab = _healthy(2, 10)
    assert find_gaps(FakeClient({DKSALCLEAN_TAB: tab}), _dksalclean(tab)) == []
    tab[4][0] = "=DkSalClean!D17"
    spec = FormulaRange(PLAYER_POOL_RAW_TAB, {0: "Name", 1: "Team"}, 2, 10, same_row_only=False)
    assert find_gaps(FakeClient({PLAYER_POOL_RAW_TAB: tab}), spec) == []


def test_repair_rewrites_each_gap_from_the_nearest_healthy_row_above(monkeypatch):
    tab = _healthy(2, 10)
    for r in (7, 8, 9, 10):
        del tab[r][1]  # column B missing from row 7 on
    tab[4][0] = "=DKSalRaw!A99"  # and a wrong-row cell in column A
    client = FakeClient({DKSALCLEAN_TAB: tab})
    spec = _dksalclean(tab)
    monkeypatch.setattr(mod, "formula_ranges", lambda cfg, headers: [spec])
    report = repair_formula_ranges(client, _cfg(), {})
    assert (DKSALCLEAN_TAB, "A4:A4", [["=DKSalRaw!A4"]]) in client.updates  # from row 3
    b = [u for u in client.updates if u[1] == "B7:B10"][0]
    assert b[2] == [["=DKSalRaw!H7"], ["=DKSalRaw!H8"], ["=DKSalRaw!H9"], ["=DKSalRaw!H10"]]  # from row 6
    assert len(report) == 2


def test_repair_clears_stale_formulas_below_the_last_row_but_leaves_typed_values(monkeypatch):
    tab = _healthy(2, 10)
    tab[11] = {0: "=DKSalRaw!A3083", 1: "=DKSalRaw!H3083"}
    tab[12] = {0: "=DKSalRaw!A3084", 1: "typed value"}
    client = FakeClient({DKSALCLEAN_TAB: tab}, row_counts={DKSALCLEAN_TAB: 12})
    spec = _dksalclean(tab)
    monkeypatch.setattr(mod, "formula_ranges", lambda cfg, headers: [spec])
    report = repair_formula_ranges(client, _cfg(), {})
    assert client.updates == []
    assert (DKSALCLEAN_TAB, ["A11:A12"]) in client.cleared
    assert (DKSALCLEAN_TAB, ["B11:B11"]) in client.cleared  # the typed value on row 12 is not cleared
    assert any("typed values past row 10 left alone: B12" in line for line in report)


def test_formula_ranges_derives_rows_and_columns_instead_of_typing_them():
    headers = {
        "Results": ["Week", "Cash Pts", "Cash Line", "Cash Results", "H2H Entered", "H2H Win", "H2H %"],
        DKSALCLEAN_TAB: ["Position", "Team", "ID", "Name", "Salary", "Team", "OPP"],
        PLAYER_POOL_RAW_TAB: ["Name", "GAME", "Pos.", "CEIL", "USAGE"],
    }
    by_tab = {s.tab: s for s in formula_ranges(_cfg(), headers)}
    assert by_tab["Results"].columns == {3: "Cash Results", 6: "H2H %"}
    assert (by_tab["Results"].first_row, by_tab["Results"].last_row) == (2, 20)  # config, not typed here
    assert (by_tab[DKSALCLEAN_TAB].first_row, by_tab[DKSALCLEAN_TAB].last_row) == (
        min(s for s, _ in PLAYER_POOL_RAW_BLOCK),
        max(e for _, e in PLAYER_POOL_RAW_BLOCK),
    )
    assert len(by_tab[DKSALCLEAN_TAB].columns) == 7
    # the zone-label columns are header-only by design
    assert by_tab[PLAYER_POOL_RAW_TAB].columns == {0: "Name", 2: "Pos."}
    assert by_tab[PLAYER_POOL_RAW_TAB].same_row_only is False
    assert column_letter(6) == "G"


def _results(rows: dict[int, dict[int, str]]) -> FormulaRange:
    return FormulaRange("Results", {6: "H2H %"}, 2, 4, guarded=True)


def test_a_guarded_range_accepts_the_guarded_formula_and_flags_the_bare_division():
    tab = {r: {6: f'=IF(E{r}="","",IFERROR(F{r}/E{r},""))'} for r in range(2, 5)}
    assert find_gaps(FakeClient({"Results": tab}), _results(tab)) == []
    tab[3][6] = "=F3/E3"  # lost its guard
    tab[4][6] = ""  # and one is missing altogether -- still caught
    gaps = find_gaps(FakeClient({"Results": tab}), _results(tab))
    assert [(g.kind, g.rows) for g in gaps] == [("missing", (4,)), ("unguarded", (3,))]
    assert "row(s) 3 divide with no empty-state guard" in describe_gap(gaps[1])


def test_repair_guards_an_unguarded_row_in_place_and_guards_rows_it_rewrites(monkeypatch):
    tab = {2: {6: '=IF(E2="","",IFERROR(F2/E2,""))'}, 3: {6: "=F3/E3"}, 4: {}}
    client = FakeClient({"Results": tab})
    spec = _results(tab)
    monkeypatch.setattr(mod, "formula_ranges", lambda cfg, headers: [spec])
    repair_formula_ranges(client, _cfg(), {})
    written = {a1: rows for _, a1, rows in client.updates}
    assert written["G3:G3"] == [['=IF(E3="","",IFERROR(F3/E3,""))']]
    assert written["G4:G4"] == [['=IF(E4="","",IFERROR(F4/E4,""))']]
