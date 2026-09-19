from dfs.sheet_lineup_metrics import (
    LINEUP_METRIC_HEADERS,
    SUB_10_OWNERSHIP_THRESHOLD,
    bring_back_present_formula,
    distinct_games_formula,
    min_unique_formula,
    own_used_formula,
    stack_signature_formula,
    sub_10_percent_formula,
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
    "Own%",
    "OwnStatus",
    "GameID",
    "Issues",
    *LINEUP_METRIC_HEADERS,
]
_BLOCKS = [(2, 10), (13, 21), (24, 32)]


def test_stack_signature_formula_counts_teammates_and_bring_back():
    formula = stack_signature_formula(2, 10, position_col="B", team_col="C", gameid_col="H")
    qb_team = 'IFERROR(INDEX($C$2:$C$10,MATCH("QB",$B$2:$B$10,0)),"")'
    qb_game = 'IFERROR(INDEX($H$2:$H$10,MATCH("QB",$B$2:$B$10,0)),"")'
    stack_count = f'COUNTIFS($C$2:$C$10,{qb_team},$B$2:$B$10,"<>QB")'
    bring_back_count = f'COUNTIFS($H$2:$H$10,{qb_game},$C$2:$C$10,"<>"&{qb_team})'
    assert formula == (
        f'=IF({qb_team}="","no QB",'
        f'"QB+"&{stack_count}&" ("&{qb_team}&")"'
        f'&IF({bring_back_count}>0," + "&{bring_back_count}&" bring-back",""))'
    )


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


def test_bring_back_present_formula_yes_no_blank():
    formula = bring_back_present_formula(2, 10, position_col="B", team_col="C", gameid_col="H")
    qb_team = 'IFERROR(INDEX($C$2:$C$10,MATCH("QB",$B$2:$B$10,0)),"")'
    assert formula.startswith(f'=IF({qb_team}="","",IF(')
    assert formula.endswith('>0,"Yes","No"))')


def test_own_used_formula_blank_until_ownership_is_real():
    formula = own_used_formula(2, 10, 11, own_col="F", own_status_col="G")
    assert formula == '=IF($G11<>"real","",SUM($F$2:$F$10))'


def test_sub_10_percent_formula_uses_the_documented_threshold_blank_pre_publish():
    formula = sub_10_percent_formula(2, 10, 11, own_col="F", own_status_col="G")
    assert SUB_10_OWNERSHIP_THRESHOLD == 0.10
    assert formula == f'=IF($G11<>"real","",COUNTIFS($F$2:$F$10,"<{SUB_10_OWNERSHIP_THRESHOLD}"))'


def test_min_unique_formula_blank_with_no_other_lineups():
    assert min_unique_formula(2, 10, []) == '=""'


def test_min_unique_formula_one_term_per_other_lineup():
    formula = min_unique_formula(2, 10, [(13, 21), (24, 32)])
    assert formula == (
        "=MIN((9-SUMPRODUCT(COUNTIF($A$13:$A$21,$A$2:$A$10)>0)),"
        "(9-SUMPRODUCT(COUNTIF($A$24:$A$32,$A$2:$A$10)>0)))"
    )


def test_write_lineup_metrics_writes_all_six_columns_per_block():
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
