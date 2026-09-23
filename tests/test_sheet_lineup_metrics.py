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
    # Fix 5 (2026-09-23): both counts now also exclude DST ("<>DST"), not
    # just QB -- a same-team DST used to read as a stack piece ("QB+1"
    # for a QB plus his own defense, no real stack at all). Fix 6.4
    # (2026-09-23): the whole thing is also blank while the block is
    # entirely empty, not just "no QB".
    formula = stack_signature_formula(2, 10, position_col="B", team_col="C", gameid_col="H")
    qb_team = 'IFERROR(INDEX($C$2:$C$10,MATCH("QB",$B$2:$B$10,0)),"")'
    qb_game = 'IFERROR(INDEX($H$2:$H$10,MATCH("QB",$B$2:$B$10,0)),"")'
    stack_count = f'COUNTIFS($C$2:$C$10,{qb_team},$B$2:$B$10,"<>QB",$B$2:$B$10,"<>DST")'
    bring_back_count = f'COUNTIFS($H$2:$H$10,{qb_game},$C$2:$C$10,"<>"&{qb_team},$B$2:$B$10,"<>DST")'
    assert formula == (
        f'=IF(COUNTA($A$2:$A$10)=0,"",'
        f'IF({qb_team}="","no QB",'
        f'"QB+"&{stack_count}&" ("&{qb_team}&")"'
        f'&IF({bring_back_count}>0," + "&{bring_back_count}&" bring-back","")))'
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
    assert '$B$2:$B$10,"<>DST"' in formula


def test_stack_and_bring_back_exclude_dst_not_via_an_rb_wr_te_allowlist():
    # Fix 5 (2026-09-23): implemented as a denylist ("<>QB","<>DST") on
    # the existing static Pos. column, not an allowlist of
    # {"RB","WR","TE"}. d08b0b3's own commit message already analyzed
    # this exact function when fixing the RB/GAME guardrail's FLEX blind
    # spot and concluded a denylist needs no FLEX-to-real-position
    # resolution here: DK's FLEX slot can never legally hold a QB or a
    # DST, so a FLEX row's own static label ("FLEX") already satisfies
    # both exclusions without a lookup. An allowlist would have needed
    # that resolution -- a FLEX row's label is never literally
    # "RB"/"WR"/"TE", so it would have silently dropped every
    # FLEX-rostered stack piece. This guards against that regression.
    formula = stack_signature_formula(2, 10, position_col="B", team_col="C", gameid_col="H")
    assert '"RB"' not in formula
    assert '"WR"' not in formula
    assert '"TE"' not in formula
    # bring_back_count's own text (which itself contains one "<>DST")
    # appears twice in the final string (once in the IF condition, once
    # in the displayed text) -- so the true count is 1 (stack_count) + 2
    # (bring_back_count's two appearances) = 3, not simply "one per
    # formula." Assert on the two distinct exclusion clauses instead.
    assert '$B$2:$B$10,"<>QB",$B$2:$B$10,"<>DST")' in formula  # stack_count
    assert '"<>"&' in formula and formula.count('$B$2:$B$10,"<>DST")') == 3


def test_own_used_formula_blank_until_ownership_is_real():
    formula = own_used_formula(2, 10, 11, own_col="F", own_status_col="G")
    assert formula == '=IF($G11<>"real","",SUM($F$2:$F$10))'


def test_sub_10_percent_formula_uses_the_documented_threshold_blank_pre_publish():
    formula = sub_10_percent_formula(2, 10, 11, own_col="F", own_status_col="G")
    assert SUB_10_OWNERSHIP_THRESHOLD == 0.10
    assert formula == (
        f'=IF(OR($G11<>"real",COUNTA($A$2:$A$10)=0),"",COUNTIFS($F$2:$F$10,"<{SUB_10_OWNERSHIP_THRESHOLD}"))'
    )


def test_sub_10_percent_formula_blank_on_an_empty_block_even_if_ownership_is_real():
    # Fix 6.4 (2026-09-23): COUNTIFS(...,"<0.10") treats a blank cell as
    # satisfying "< 0.10" -- an entirely empty lineup, once ownership is
    # published, read "9" (every unfilled slot miscounted as sub-10%-
    # owned) instead of blank.
    formula = sub_10_percent_formula(2, 10, 11, own_col="F", own_status_col="G")
    assert "COUNTA($A$2:$A$10)=0" in formula


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
