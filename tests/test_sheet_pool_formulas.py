import pytest

from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_pool_cells import pool_formula
from dfs.sheet_pool_formulas import (
    _TAG_RANK_ARRAY,
    _UNKNOWN_TAG_RANK,
    _grouped_with_separators_formula,
    _union_array,
    write_pool_formulas,
)
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_HEADER_ROW

OVERFLOW_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Overflow"))
POOL_TYPE_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Pool"))
NAME_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Name"))
POS_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Pos."))
ID_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Id"))
_HEADER_A1 = f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"


class SpySheetsClient:
    """Records update_range calls and fakes read_range for the header row
    (Overflow/Pool lookup) and the Position column lookup -- same
    convention as test_sheet_links.py's spy."""

    def __init__(self, positions: dict[str, str], header: list[str] = PLAYER_POOL_COLUMN_ORDER):
        self._positions = positions  # {"B2": "QB", "B13": "RB", ...}
        self._header = header
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.clear_calls: list[tuple[str, list[str]]] = []

    def clear_ranges(self, tab_name: str, ranges: list[str]) -> None:
        self.clear_calls.append((tab_name, ranges))

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == _HEADER_A1:
            return [self._header]
        value = self._positions.get(a1_range, "")
        return [[value]] if value else [[]]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))


_BLOCKS = [(2, 11), (13, 29), (31, 55), (57, 65), (67, 74)]
_POSITIONS = {
    f"{POS_COL}2": "QB",
    f"{POS_COL}13": "RB",
    f"{POS_COL}31": "WR",
    f"{POS_COL}57": "TE",
    f"{POS_COL}67": "DST",
}


def test_pool_sits_left_of_name_so_name_is_no_longer_column_a():
    assert (POOL_TYPE_COL, NAME_COL, POS_COL) == ("A", "B", "C")


def test_writes_a_name_formula_and_overflow_formula_per_block_only():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    expected = {f"{OVERFLOW_COL}{PLAYER_POOL_HEADER_ROW}", f"{POOL_TYPE_COL}{PLAYER_POOL_HEADER_ROW}"}
    for start, end in _BLOCKS:
        expected |= {
            f"{NAME_COL}{start}",
            f"{OVERFLOW_COL}{start}",
            f"{POOL_TYPE_COL}{start}:{POOL_TYPE_COL}{end}",
        }
    assert ranges_written == expected  # no Added header any more


def test_raises_when_overflow_or_pool_column_is_missing():
    # The exact regression this guards against: Phase 3 moved Overflow/
    # Pool off their old hardcoded Z/AA letters -- those letters now hold
    # real EdgeRaw-linked columns (Roof/Wind). write_pool_formulas must
    # refuse rather than clobber them.
    header = [name for name in PLAYER_POOL_COLUMN_ORDER if name != "Overflow"]
    client = SpySheetsClient(_POSITIONS, header=header)
    with pytest.raises(ValueError, match="Overflow"):
        write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)


def test_name_formula_uses_the_position_actually_read_from_the_sheet():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert 'EdgeRaw!$C$2:$C="QB"' in formulas[f"{NAME_COL}2"]
    assert 'EdgeRaw!$C$2:$C="RB"' in formulas[f"{NAME_COL}13"]
    assert 'EdgeRaw!$C$2:$C="WR"' in formulas[f"{NAME_COL}31"]
    assert 'EdgeRaw!$C$2:$C="TE"' in formulas[f"{NAME_COL}57"]
    assert 'EdgeRaw!$C$2:$C="DST"' in formulas[f"{NAME_COL}67"]
    assert f'EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}<>""' in formulas[f"{NAME_COL}2"]


def test_name_formula_caps_at_the_blocks_own_row_count():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert ",10,1)" in formulas[f"{NAME_COL}2"]  # QB: 11-2+1 = 10
    assert ",17,1)" in formulas[f"{NAME_COL}13"]  # RB: 29-13+1 = 17
    assert ",25,1)" in formulas[f"{NAME_COL}31"]  # WR: 55-31+1 = 25
    assert ",9,1)" in formulas[f"{NAME_COL}57"]  # TE: 65-57+1 = 9
    assert ",8,1)" in formulas[f"{NAME_COL}67"]  # DST: 74-67+1 = 8


def test_overflow_formula_thresholds_on_the_same_cap():
    # Fix 3 (2026-09-23): SUMPRODUCT, not COUNTA -- `_union_array`'s own
    # per-source IFERROR guards mean an empty position's union can now
    # resolve to a blank ("") placeholder row instead of erroring, and
    # COUNTA counts a formula-produced "" as present (this codebase's
    # well-known "FILTER of nothing" trap). Built from the real
    # `_union_array` output rather than hand-reconstructed, so this stays
    # in sync with that function automatically.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    union = _union_array("EdgeRaw", "QB")
    count = f'IFERROR(SUMPRODUCT((INDEX(UNIQUE({union}),0,1)<>"")*1),0)'
    # PROMPT_BOARD_FIXES.md item 8 (2026-09-25): the threshold is now
    # ROWS() of the SAME grouped-with-separators array `_name_formula`
    # itself constrains -- not the player count alone -- so a block that
    # needs separator rows to fit its own group mix is compared against
    # its TRUE row need, not just how many players are in it. See
    # test_overflow_formula_fires_when_separators_alone_push_past_the_cap
    # for the case this replaces (10 players, exactly at the old flat
    # count-only cap, that the old formula silently missed).
    grouped = _grouped_with_separators_formula(union)
    needed_rows = f"ROWS({grouped})"
    assert formulas[f"{OVERFLOW_COL}2"] == (
        f'=IF({needed_rows}>10,10&" QB slots, "&{count}&" ticked -- some are hidden","")'
    )


def test_overflow_formula_fires_when_separators_alone_push_past_the_cap():
    # PROMPT_BOARD_FIXES.md item 8's own named bug: 10 QBs split across
    # all three Both/Cash/GPP groups need 12 rows (10 players + 2
    # separators) in a 10-row block -- 2 are silently cut off by
    # ARRAY_CONSTRAIN, yet the OLD check (`count > cap`, 10 > 10) never
    # fired. The formula must now compare the array's own real row need
    # (ROWS(grouped), which already accounts for however many separators
    # this exact group mix needs) against the cap, not the player count.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    overflow_formula = formulas[f"{OVERFLOW_COL}2"]
    # The comparison is against ROWS(...), never a bare player count --
    # this is what actually fixes the bug (ROWS already includes whatever
    # separators this week's own group mix needs, so 10 players spread
    # across all 3 groups correctly evaluates to 12 > 10).
    assert overflow_formula.startswith("=IF(ROWS(")
    assert "IF(ROWS(LET(" in overflow_formula


def test_name_formula_reads_edgeraw_pool_ticks_alone_no_box_and_no_added_list():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    name_formula = {a1: rows[0][0] for _, a1, rows in client.update_calls}[f"{NAME_COL}2"]
    assert "UNIQUE(IFERROR(FILTER({" in name_formula
    assert "FILTER({EdgeRaw!$B$2:$B,EdgeRaw!$F$2:$F,MATCH(" in name_formula
    assert "$B$1" not in name_formula and "$C$1" not in name_formula  # the add-a-player box feeds nothing
    assert name_formula.count("VLOOKUP(") == 0  # nothing looked up by typed name
    assert "Added" not in name_formula


def test_union_array_is_one_guarded_source_so_it_never_errors():
    # `FILTER` of nothing raises #N/A; the single source is IFERROR-guarded to a same-shaped blank row.
    union = _union_array("EdgeRaw", "QB")
    assert union.startswith("IFERROR(FILTER(") and union.endswith('{"",0,0})')
    assert union.count('{"",0,0}') == 1


def test_name_formula_sorts_by_tag_rank_then_salary_descending():
    # Fix 2.10 (Salary) + Part 7.10 (tag rank), Sam: "The pool should
    # order players by position by salary high to low, but grouped by
    # Both, Cash, GPP." Tag rank (column 3, via each tag's own LET group)
    # is the primary grouping -- Both/Cash/GPP in that order -- Salary
    # (column 2) descending breaks ties within a tag group. Fix 3 (A7)
    # replaced the single flat SORT(UNIQUE(...),3,TRUE,2,FALSE) with a
    # per-tag SORT inside a LET, so this checks the per-group SORT/UNIQUE
    # calls rather than that literal string.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas[f"{NAME_COL}2"]
    assert "LET(rawArr,UNIQUE(" in name_formula
    assert "SORT(FILTER(uArr,INDEX(uArr,0,3)=1),2,FALSE)" in name_formula
    assert "SORT(FILTER(uArr,INDEX(uArr,0,3)=2),2,FALSE)" in name_formula
    assert "SORT(FILTER(uArr,INDEX(uArr,0,3)=3),2,FALSE)" in name_formula


def test_name_formula_emits_one_blank_row_between_tag_groups():
    # Fix 3 (A7, 2026-09-23): "I like the blank line between cash/gpp/both
    # blocks in the pool, but it doesn't seem to consistently work." The
    # separator is a same-shape (Name, Salary, TagRank) blank row --
    # {"",0,0} -- inserted between adjacent groups via nested IF/hasX
    # checks, never a real inserted sheet row.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas[f"{NAME_COL}2"]
    assert 'sepRow,{"",0,0}' in name_formula
    assert "IF(hasOne,IF(hasTwo,IF(hasThree,{grpOne;sepRow;grpTwo;sepRow;grpThree}" in name_formula


def test_name_formula_does_not_silently_drop_an_unknown_tag_rank_row():
    # A typed (control-cell/added-list) name EdgeRaw can't currently match
    # a real Pool tag for falls back to _UNKNOWN_TAG_RANK (4) -- an
    # earlier version of Fix 3 only ever filtered for tag ranks 1-3 and
    # silently dropped these rows from the pool entirely, a regression
    # from the pre-Fix-3 flat SORT(UNIQUE(...)) which included them
    # (sorted last). `grpOther` must still catch anything that isn't 1,
    # 2, or 3.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas[f"{NAME_COL}2"]
    assert "grpOther,IFERROR(SORT(FILTER(uArr,INDEX(uArr,0,3)<>1,INDEX(uArr,0,3)<>2,INDEX(uArr,0,3)<>3)" in (
        name_formula
    )
    assert "IF(hasOther,IF(hasKnown,{known;sepRow;grpOther},grpOther),known)" in name_formula


def test_grouped_separator_formula_raises_if_pool_tag_count_ever_changes(monkeypatch):
    # Guarded, not silently mismatched -- the 3-way nested IF only makes
    # sense for exactly the 3 tags it was built for.
    import dfs.sheet_pool_formulas as pool_formulas

    monkeypatch.setattr(pool_formulas, "POOL_TYPE_SORT_ORDER", ["Both", "Cash", "GPP", "Extra"])
    with pytest.raises(ValueError, match="hardcodes exactly 3 pool tags"):
        pool_formulas._grouped_with_separators_formula("{1,2,3}")


def test_tag_rank_array_matches_pool_type_sort_order_not_hand_written():
    from dfs.sources.edge import POOL_TYPE_SORT_ORDER

    assert _TAG_RANK_ARRAY == "{" + ",".join(f'"{tag}"' for tag in POOL_TYPE_SORT_ORDER) + "}"
    assert _UNKNOWN_TAG_RANK == len(POOL_TYPE_SORT_ORDER) + 1


def test_overflow_formula_counts_the_deduped_union_not_edgeraw_alone():
    # A player ticked in EdgeRaw AND typed into the control cell must
    # count once toward the cap, not twice -- SUMPRODUCT over
    # INDEX(UNIQUE(...),0,1), not two separate COUNTIFS added together.
    # INDEX(...,0,1) takes just the Name column back out of the (Name,
    # Salary, TagRank) triples Fix 2.10/Part 7.10 added. SUMPRODUCT, not
    # COUNTA, since Fix 3 (2026-09-23): see
    # test_overflow_formula_thresholds_on_the_same_cap.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "SUMPRODUCT((INDEX(UNIQUE(" in formulas[f"{OVERFLOW_COL}2"]


def test_skips_a_block_with_no_position_label_instead_of_writing_a_broken_formula():
    client = SpySheetsClient({"B2": "QB"})  # every other block's position cell is blank
    result = write_pool_formulas(
        client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS
    )

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert f"{NAME_COL}13" not in ranges_written
    assert f"{OVERFLOW_COL}13" not in ranges_written
    assert f"{POOL_TYPE_COL}13:{POOL_TYPE_COL}29" not in ranges_written
    assert any("skipped" in line for line in result)


def test_never_writes_outside_name_overflow_and_pool_columns():
    # Name, the overflow warning, and the Pool control are the only columns this function may touch -- every
    # other column already holds a VLOOKUP written by link_edge_columns.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    import re

    allowed_columns = {NAME_COL, OVERFLOW_COL, POOL_TYPE_COL}
    for _, a1_range, _ in client.update_calls:
        assert re.match(r"[A-Z]+", a1_range).group(0) in allowed_columns


def test_the_pool_cell_finds_the_player_by_the_id_in_its_own_row_not_by_name():
    # Same formula as every other tab's Pool control, keyed on this row's hidden Id: it stays right when the
    # Name spill reflows, and it is the text the Apps Script restores after an edit.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    pool_call = next(c for c in client.update_calls if c[1] == f"{POOL_TYPE_COL}2:{POOL_TYPE_COL}11")
    formulas = [row[0] for row in pool_call[2]]
    assert formulas[0] == pool_formula(2, "EdgeRaw", id_col=ID_COL)
    assert formulas[3] == pool_formula(5, "EdgeRaw", id_col=ID_COL)  # each row reads its own Id
    assert f"${ID_COL}2" in formulas[0] and f"${NAME_COL}2" not in formulas[0]


def test_pool_type_column_header_is_written_once():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    header_call = next(c for c in client.update_calls if c[1] == f"{POOL_TYPE_COL}{PLAYER_POOL_HEADER_ROW}")
    assert header_call[2] == [["Pool"]]


def test_each_blocks_spill_area_is_cleared_of_stray_formulas():
    """Found live: leftover name formulas at rows 37/63 of the template's RB/WR blocks made
    every RB and WR show twice. A block's formula spills, so the cells below its start must
    hold nothing typed."""
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    cleared = [rng for _tab, ranges in client.clear_calls for rng in ranges]
    assert cleared == [f"{NAME_COL}{start + 1}:{NAME_COL}{end}" for start, end in _BLOCKS]
