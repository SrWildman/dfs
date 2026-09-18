import pytest

from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_pool_formulas import _TAG_RANK_ARRAY, _UNKNOWN_TAG_RANK, write_pool_formulas
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW, PLAYER_POOL_HEADER_ROW

SOURCE_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Source"))
OVERFLOW_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Overflow"))
POOL_TYPE_COL = column_letter(PLAYER_POOL_COLUMN_ORDER.index("Pool"))
_HEADER_A1 = f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"
_CONTROL_CELL = f"$B${PLAYER_POOL_CONTROL_ROW}"


class SpySheetsClient:
    """Records update_range calls and fakes read_range for the header row
    (Source/Overflow/Pool lookup) and the Position column lookup -- same
    convention as test_sheet_links.py's spy."""

    def __init__(self, positions: dict[str, str], header: list[str] = PLAYER_POOL_COLUMN_ORDER):
        self._positions = positions  # {"B2": "QB", "B13": "RB", ...}
        self._header = header
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == _HEADER_A1:
            return [self._header]
        value = self._positions.get(a1_range, "")
        return [[value]] if value else [[]]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))


_BLOCKS = [(2, 11), (13, 29), (31, 55), (57, 65), (67, 74)]
_POSITIONS = {"B2": "QB", "B13": "RB", "B31": "WR", "B57": "TE", "B67": "DST"}


def test_writes_a_name_formula_and_overflow_formula_per_block_only():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert ranges_written == {
        f"{SOURCE_COL}{PLAYER_POOL_HEADER_ROW}",
        f"{OVERFLOW_COL}{PLAYER_POOL_HEADER_ROW}",
        f"{POOL_TYPE_COL}{PLAYER_POOL_HEADER_ROW}",
        "A2",
        f"{OVERFLOW_COL}2",
        f"{SOURCE_COL}2:{SOURCE_COL}11",
        f"{POOL_TYPE_COL}2:{POOL_TYPE_COL}11",
        "A13",
        f"{OVERFLOW_COL}13",
        f"{SOURCE_COL}13:{SOURCE_COL}29",
        f"{POOL_TYPE_COL}13:{POOL_TYPE_COL}29",
        "A31",
        f"{OVERFLOW_COL}31",
        f"{SOURCE_COL}31:{SOURCE_COL}55",
        f"{POOL_TYPE_COL}31:{POOL_TYPE_COL}55",
        "A57",
        f"{OVERFLOW_COL}57",
        f"{SOURCE_COL}57:{SOURCE_COL}65",
        f"{POOL_TYPE_COL}57:{POOL_TYPE_COL}65",
        "A67",
        f"{OVERFLOW_COL}67",
        f"{SOURCE_COL}67:{SOURCE_COL}74",
        f"{POOL_TYPE_COL}67:{POOL_TYPE_COL}74",
    }


def test_raises_when_source_overflow_or_pool_column_is_missing():
    # The exact regression this guards against: Phase 3 moved Source/
    # Overflow/Pool off their old hardcoded O/Z/AA letters -- those
    # letters now hold real EdgeRaw-linked columns (Flag/Roof/Wind).
    # write_pool_formulas must refuse rather than clobber them.
    header = [name for name in PLAYER_POOL_COLUMN_ORDER if name != "Overflow"]
    client = SpySheetsClient(_POSITIONS, header=header)
    with pytest.raises(ValueError, match="Overflow"):
        write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)


def test_name_formula_uses_the_position_actually_read_from_the_sheet():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert 'EdgeRaw!$C$2:$C="QB"' in formulas["A2"]
    assert 'EdgeRaw!$C$2:$C="RB"' in formulas["A13"]
    assert 'EdgeRaw!$C$2:$C="WR"' in formulas["A31"]
    assert 'EdgeRaw!$C$2:$C="TE"' in formulas["A57"]
    assert 'EdgeRaw!$C$2:$C="DST"' in formulas["A67"]
    assert f'EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}<>""' in formulas["A2"]


def test_name_formula_caps_at_the_blocks_own_row_count():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert ",10,1)" in formulas["A2"]  # QB: 11-2+1 = 10
    assert ",17,1)" in formulas["A13"]  # RB: 29-13+1 = 17
    assert ",25,1)" in formulas["A31"]  # WR: 55-31+1 = 25
    assert ",9,1)" in formulas["A57"]  # TE: 65-57+1 = 9
    assert ",8,1)" in formulas["A67"]  # DST: 74-67+1 = 8


_CONTROL_NAMES = (
    f'FILTER({_CONTROL_CELL}:{_CONTROL_CELL},{_CONTROL_CELL}<>"",'
    f'IFERROR(VLOOKUP({_CONTROL_CELL},EdgeRaw!$B:$C,2,FALSE),"")="QB")'
)


def test_overflow_formula_thresholds_on_the_same_cap():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    edge_filter = (
        f"FILTER({{EdgeRaw!$B$2:$B,EdgeRaw!$F$2:$F,"
        f"MATCH(EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN},{_TAG_RANK_ARRAY},0)}},"
        f'EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}<>"",EdgeRaw!$C$2:$C="QB")'
    )
    control_pool_tag = (
        f'IFERROR(INDEX(EdgeRaw!${POOL_COLUMN}:${POOL_COLUMN},MATCH({_CONTROL_CELL},EdgeRaw!$B:$B,0)),"")'
    )
    control_tag_rank = f"IFERROR(MATCH({control_pool_tag},{_TAG_RANK_ARRAY},0),{_UNKNOWN_TAG_RANK})"
    control_filter = (
        f"{{{_CONTROL_NAMES},IFERROR(VLOOKUP({_CONTROL_NAMES},EdgeRaw!$B:$F,5,FALSE),0),{control_tag_rank}}}"
    )
    union = f"{{{edge_filter};{control_filter}}}"
    count = f"IFERROR(COUNTA(INDEX(UNIQUE({union}),0,1)),0)"
    assert formulas[f"{OVERFLOW_COL}2"] == (
        f'=IF({count}>10,10&" QB slots, "&{count}&" ticked -- some are hidden","")'
    )


def test_name_formula_unions_edgeraw_ticks_with_the_control_cell():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas["A2"]
    assert "UNIQUE({" in name_formula
    assert "FILTER({EdgeRaw!$B$2:$B,EdgeRaw!$F$2:$F,MATCH(" in name_formula
    assert f'FILTER({_CONTROL_CELL}:{_CONTROL_CELL},{_CONTROL_CELL}<>"",' in name_formula
    assert f'VLOOKUP({_CONTROL_CELL},EdgeRaw!$B:$C,2,FALSE),"")="QB"' in name_formula
    assert "VLOOKUP(" in name_formula  # the control cell's half looks Salary up against EdgeRaw


def test_name_formula_sorts_by_tag_rank_then_salary_descending():
    # Fix 2.10 (Salary) + Part 7.10 (tag rank), Sam: "The pool should
    # order players by position by salary high to low, but grouped by
    # Both, Cash, GPP." Tag rank (column 3) is the primary ascending key
    # -- Both/Cash/GPP in that order -- Salary (column 2) descending
    # breaks ties within a tag group.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "SORT(UNIQUE(" in formulas["A2"]
    assert ",3,TRUE,2,FALSE)" in formulas["A2"]


def test_tag_rank_array_matches_pool_type_sort_order_not_hand_written():
    from dfs.sources.edge import POOL_TYPE_SORT_ORDER

    assert _TAG_RANK_ARRAY == "{" + ",".join(f'"{tag}"' for tag in POOL_TYPE_SORT_ORDER) + "}"
    assert _UNKNOWN_TAG_RANK == len(POOL_TYPE_SORT_ORDER) + 1


def test_overflow_formula_counts_the_deduped_union_not_edgeraw_alone():
    # A player ticked in EdgeRaw AND typed into the control cell must
    # count once toward the cap, not twice -- COUNTA(INDEX(UNIQUE(...),0,1)),
    # not two separate COUNTIFS added together. INDEX(...,0,1) takes just
    # the Name column back out of the (Name, Salary, TagRank) triples Fix
    # 2.10/Part 7.10 added.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "COUNTA(INDEX(UNIQUE(" in formulas[f"{OVERFLOW_COL}2"]
    assert _CONTROL_CELL in formulas[f"{OVERFLOW_COL}2"]


def test_skips_a_block_with_no_position_label_instead_of_writing_a_broken_formula():
    client = SpySheetsClient({"B2": "QB"})  # every other block's position cell is blank
    result = write_pool_formulas(
        client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS
    )

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert "A13" not in ranges_written
    assert f"{OVERFLOW_COL}13" not in ranges_written
    assert f"{SOURCE_COL}13:{SOURCE_COL}29" not in ranges_written
    assert f"{POOL_TYPE_COL}13:{POOL_TYPE_COL}29" not in ranges_written
    assert any("skipped" in line for line in result)


def test_never_writes_outside_name_source_overflow_and_pool_columns():
    # Name, Source, the overflow warning, and (Fix 2.11) the surfaced Pool
    # value are the only columns this function is allowed to touch --
    # every other column already holds a VLOOKUP written by
    # link_edge_columns and must never be rewritten with a blank/
    # placeholder value.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    allowed_first_letters = {"A", SOURCE_COL[0], OVERFLOW_COL[0], POOL_TYPE_COL[0]}
    for _, a1_range, _ in client.update_calls:
        assert a1_range[0] in allowed_first_letters


def test_pool_type_formula_looks_up_edgeraw_by_name_with_index_match():
    # Fix 2.11: Pool (column A on EdgeRaw) sits LEFT of Name (column B),
    # so this can't be a plain VLOOKUP -- it must be INDEX/MATCH.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    pool_call = next(c for c in client.update_calls if c[1] == f"{POOL_TYPE_COL}2:{POOL_TYPE_COL}11")
    first_row_formula = pool_call[2][0][0]
    assert 'IF($A2="","",' in first_row_formula
    assert "INDEX(EdgeRaw!$A:$A," in first_row_formula
    assert "MATCH($A2,EdgeRaw!$B:$B,0)" in first_row_formula


def test_pool_type_column_header_is_written_once():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    header_call = next(c for c in client.update_calls if c[1] == f"{POOL_TYPE_COL}{PLAYER_POOL_HEADER_ROW}")
    assert header_call[2] == [["Pool"]]


def test_source_formula_labels_edgeraw_ticks_and_the_control_cell():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    source_call = next(c for c in client.update_calls if c[1] == f"{SOURCE_COL}2:{SOURCE_COL}11")
    first_row_formula = source_call[2][0][0]
    assert 'IF($A2="","",' in first_row_formula
    assert '"EdgeRaw"' in first_row_formula
    assert '"Added"' in first_row_formula
    assert f"EdgeRaw!${POOL_COLUMN}:${POOL_COLUMN}" in first_row_formula
    assert f"$A2={_CONTROL_CELL}" in first_row_formula


def test_source_column_header_is_written_once():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    header_call = next(c for c in client.update_calls if c[1] == f"{SOURCE_COL}{PLAYER_POOL_HEADER_ROW}")
    assert header_call[2] == [["Source"]]
