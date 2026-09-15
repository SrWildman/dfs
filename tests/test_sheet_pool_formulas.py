from dfs.sheet_pool_formulas import write_pool_formulas
from dfs.sources.edge import POOL_COLUMN


class SpySheetsClient:
    """Records update_range calls and fakes read_range for the Position
    column lookup -- same convention as test_sheet_links.py's spy."""

    def __init__(self, positions: dict[str, str]):
        self._positions = positions  # {"B2": "QB", "B13": "RB", ...}
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
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
        "O1",
        "Z1",
        "AA1",
        "A2",
        "Z2",
        "O2:O11",
        "AA2:AA11",
        "A13",
        "Z13",
        "O13:O29",
        "AA13:AA29",
        "A31",
        "Z31",
        "O31:O55",
        "AA31:AA55",
        "A57",
        "Z57",
        "O57:O65",
        "AA57:AA65",
        "A67",
        "Z67",
        "O67:O74",
        "AA67:AA74",
    }


def test_name_formula_uses_the_position_actually_read_from_the_sheet():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert 'EdgeRaw!$D$2:$D="QB"' in formulas["A2"]
    assert 'EdgeRaw!$D$2:$D="RB"' in formulas["A13"]
    assert 'EdgeRaw!$D$2:$D="WR"' in formulas["A31"]
    assert 'EdgeRaw!$D$2:$D="TE"' in formulas["A57"]
    assert 'EdgeRaw!$D$2:$D="DST"' in formulas["A67"]
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


_PICKS_NAMES = (
    "FILTER('Pool Picks'!$A$3:$A$102,'Pool Picks'!$A$3:$A$102<>\"\",'Pool Picks'!$B$3:$B$102=\"QB\")"
)


def test_overflow_formula_thresholds_on_the_same_cap():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    edge_filter = (
        f"FILTER({{EdgeRaw!$C$2:$C,EdgeRaw!$G$2:$G}},"
        f'EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}<>"",EdgeRaw!$D$2:$D="QB")'
    )
    picks_filter = f"{{{_PICKS_NAMES},IFERROR(VLOOKUP({_PICKS_NAMES},EdgeRaw!$C:$G,5,FALSE),0)}}"
    union = f"{{{edge_filter};{picks_filter}}}"
    count = f"IFERROR(COUNTA(INDEX(UNIQUE({union}),0,1)),0)"
    assert formulas["Z2"] == f'=IF({count}>10,10&" QB slots, "&{count}&" ticked -- some are hidden","")'


def test_name_formula_unions_edgeraw_ticks_with_pool_picks_typed_rows():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas["A2"]
    assert "UNIQUE({" in name_formula
    assert "FILTER({EdgeRaw!$C$2:$C,EdgeRaw!$G$2:$G}" in name_formula
    assert "FILTER('Pool Picks'!$A$3:$A$102,'Pool Picks'!$A$3:$A$102<>\"\"," in name_formula
    assert "'Pool Picks'!$B$3:$B$102=\"QB\"" in name_formula
    assert "VLOOKUP(" in name_formula  # Pool Picks' half looks Salary up against EdgeRaw


def test_name_formula_sorts_by_salary_descending_not_alphabetically():
    # Fix 2.10.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "SORT(UNIQUE(" in formulas["A2"]
    assert ",2,FALSE)" in formulas["A2"]  # sort key = column 2 (Salary), descending


def test_overflow_formula_counts_the_deduped_union_not_edgeraw_alone():
    # A player ticked in EdgeRaw AND typed into Pool Picks must count
    # once toward the cap, not twice -- COUNTA(INDEX(UNIQUE(...),0,1)),
    # not two separate COUNTIFS added together. INDEX(...,0,1) takes just
    # the Name column back out of the (Name, Salary) pairs Fix 2.10 added.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "COUNTA(INDEX(UNIQUE(" in formulas["Z2"]
    assert "FILTER('Pool Picks'!" in formulas["Z2"]


def test_skips_a_block_with_no_position_label_instead_of_writing_a_broken_formula():
    client = SpySheetsClient({"B2": "QB"})  # every other block's position cell is blank
    result = write_pool_formulas(
        client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS
    )

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert "A13" not in ranges_written
    assert "Z13" not in ranges_written
    assert "O13:O29" not in ranges_written
    assert "AA13:AA29" not in ranges_written
    assert any("skipped" in line for line in result)


def test_never_writes_outside_columns_a_o_z_and_aa():
    # Name, Source, the overflow warning, and (Fix 2.11) the surfaced Pool
    # value are the only columns this function is allowed to touch --
    # every other column (P..Y) already holds a VLOOKUP written by
    # link_edge_columns and must never be rewritten with a blank/
    # placeholder value. "AA..." ranges start with "A" too, so this same
    # check already covers them.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    for _, a1_range, _ in client.update_calls:
        assert a1_range[0] in ("A", "O", "Z")


def test_pool_type_formula_looks_up_edgeraw_by_name_with_index_match():
    # Fix 2.11: Pool (column A on EdgeRaw) sits LEFT of Name (column C),
    # so this can't be a plain VLOOKUP -- it must be INDEX/MATCH.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    pool_call = next(c for c in client.update_calls if c[1] == "AA2:AA11")
    first_row_formula = pool_call[2][0][0]
    assert 'IF($A2="","",' in first_row_formula
    assert "INDEX(EdgeRaw!$A:$A," in first_row_formula
    assert "MATCH($A2,EdgeRaw!$C:$C,0)" in first_row_formula


def test_pool_type_column_header_is_written_once():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    header_call = next(c for c in client.update_calls if c[1] == "AA1")
    assert header_call[2] == [["Pool"]]


def test_source_formula_labels_edgeraw_ticks_and_pool_picks_typed_rows():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    source_call = next(c for c in client.update_calls if c[1] == "O2:O11")
    first_row_formula = source_call[2][0][0]
    assert 'IF($A2="","",' in first_row_formula
    assert '"EdgeRaw"' in first_row_formula
    assert '"Picks"' in first_row_formula
    assert f"EdgeRaw!${POOL_COLUMN}:${POOL_COLUMN}" in first_row_formula
    assert "'Pool Picks'!$A$3:$A$102" in first_row_formula


def test_source_column_header_is_written_once():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)
    header_call = next(c for c in client.update_calls if c[1] == "O1")
    assert header_call[2] == [["Source"]]
