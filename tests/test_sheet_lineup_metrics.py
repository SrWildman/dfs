from dfs.sheet_lineup_metrics import (
    LINEUP_METRIC_HEADERS,
    distinct_games_formula,
    min_unique_formula,
    write_lineup_metrics,
)


class SpySheetsClient:
    """Records update_range calls and fakes read_range for the header row,
    same convention as test_sheet_pool_formulas.py's own spy."""

    def __init__(self, header: list[str]):
        self._header = header
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))


_HEADER = [
    "Name",
    "Pos.",
    "Team",
    "Opp.",
    "DK Sal",
    "GameID",
    "Issues",
    *LINEUP_METRIC_HEADERS,
]
_BLOCKS = [(2, 10), (13, 21), (24, 32)]


def test_distinct_games_formula_counts_unique_nonblank_gameids():
    formula = distinct_games_formula(2, 10, gameid_col="H")
    assert formula == '=IFERROR(ROWS(UNIQUE(FILTER($H$2:$H$10,$H$2:$H$10<>""))),0)'


def test_distinct_games_formula_uses_rows_not_counta():
    """Found live (2026-09-19): with a genuinely empty range, FILTER
    errors (#N/A) and COUNTA absorbs that error into a valid count of 1
    -- an error value still "counts" as present -- before IFERROR ever
    sees an error to catch, so this silently read 1, not 0, for every
    still-empty lineup. `ROWS` propagates the error instead, so
    `IFERROR(ROWS(...),0)` genuinely degrades to 0."""
    formula = distinct_games_formula(2, 10, gameid_col="H")
    assert "ROWS(" in formula
    assert "COUNTA(" not in formula


def test_min_unique_formula_blank_with_no_other_lineups():
    assert min_unique_formula(2, 10, []) == '=""'


def test_min_unique_formula_blank_while_this_block_is_empty():
    # Fix 6.4 (2026-09-23): COUNTIF(other_rng, this_rng) treats a blank
    # cell in this_rng as matching any blank cell in other_rng, so an
    # unbuilt lineup compared against another still-unbuilt one read
    # "Min Unique: 0" -- indistinguishable from two complete duplicates.
    formula = min_unique_formula(2, 10, [(13, 21)])
    assert formula.startswith('=IF(COUNTA($A$2:$A$10)=0,"",MIN(')


def test_min_unique_formula_one_term_per_other_lineup():
    formula = min_unique_formula(2, 10, [(13, 21), (24, 32)])
    assert formula == (
        '=IF(COUNTA($A$2:$A$10)=0,"",MIN('
        "(9-SUMPRODUCT(COUNTIF($A$13:$A$21,$A$2:$A$10)>0)),"
        "(9-SUMPRODUCT(COUNTIF($A$24:$A$32,$A$2:$A$10)>0))))"
    )


def test_write_lineup_metrics_writes_both_columns_per_block():
    client = SpySheetsClient(_HEADER)

    result = write_lineup_metrics(client, "Lineups", header_row=1, name_blocks=_BLOCKS)

    written_ranges = {a1 for _tab, a1, _rows in client.update_calls}
    for _start, end in _BLOCKS:
        totals_row = end + 1
        for header_name in LINEUP_METRIC_HEADERS:
            col = _HEADER.index(header_name)
            from dfs.sheets import column_letter

            assert f"{column_letter(col)}{totals_row}" in written_ranges
    assert "Lineups: lineup metrics" in result
    assert f"{len(_BLOCKS)} block(s)" in result


def test_write_lineup_metrics_excludes_this_blocks_own_range_from_min_unique():
    client = SpySheetsClient(_HEADER)
    write_lineup_metrics(client, "Lineups", header_row=1, name_blocks=_BLOCKS)

    from dfs.sheets import column_letter

    min_unique_col = column_letter(_HEADER.index("Min Unique"))
    first_block_formula = next(
        rows[0][0] for _tab, a1, rows in client.update_calls if a1 == f"{min_unique_col}11"
    )
    # Block (2,10)'s own range must never appear as one of the "other" terms.
    assert "$A$2:$A$10,$A$2:$A$10" not in first_block_formula
    assert "$A$13:$A$21" in first_block_formula
    assert "$A$24:$A$32" in first_block_formula


def test_write_lineup_metrics_skips_when_a_required_column_is_missing():
    header = [name for name in _HEADER if name != "GameID"]
    client = SpySheetsClient(header)

    result = write_lineup_metrics(client, "Lineups", header_row=1, name_blocks=_BLOCKS)

    assert "GameID" in result
    assert "skipped" in result
    assert client.update_calls == []


def test_write_lineup_metrics_skips_when_tab_absent():
    class AbsentClient(SpySheetsClient):
        def tab_exists(self, tab_name: str) -> bool:
            return False

    client = AbsentClient(_HEADER)
    result = write_lineup_metrics(client, "Lineups", header_row=1, name_blocks=_BLOCKS)
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_removed_lineup_metrics_stay_removed():
    """Round 5 1c: Sam doesn't use these; Stack's job moved to item 4's tints."""
    assert LINEUP_METRIC_HEADERS == ["Games", "Min Unique"]
