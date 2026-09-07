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
    assert ranges_written == {"Z1", "A2", "Z2", "A13", "Z13", "A31", "Z31", "A57", "Z57", "A67", "Z67"}


def test_name_formula_uses_the_position_actually_read_from_the_sheet():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert 'EdgeRaw!$D$2:$D="QB"' in formulas["A2"]
    assert 'EdgeRaw!$D$2:$D="RB"' in formulas["A13"]
    assert 'EdgeRaw!$D$2:$D="WR"' in formulas["A31"]
    assert 'EdgeRaw!$D$2:$D="TE"' in formulas["A57"]
    assert 'EdgeRaw!$D$2:$D="DST"' in formulas["A67"]
    assert f"EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}=TRUE" in formulas["A2"]


def test_name_formula_caps_at_the_blocks_own_row_count():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert ",10,1)" in formulas["A2"]  # QB: 11-2+1 = 10
    assert ",17,1)" in formulas["A13"]  # RB: 29-13+1 = 17
    assert ",25,1)" in formulas["A31"]  # WR: 55-31+1 = 25
    assert ",9,1)" in formulas["A57"]  # TE: 65-57+1 = 9
    assert ",8,1)" in formulas["A67"]  # DST: 74-67+1 = 8


def test_overflow_formula_thresholds_on_the_same_cap():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    union = (
        "{"
        f'FILTER(EdgeRaw!$C$2:$C,EdgeRaw!${POOL_COLUMN}$2:${POOL_COLUMN}=TRUE,EdgeRaw!$D$2:$D="QB");'
        "FILTER('Pool Picks'!$A$2:$A$101,'Pool Picks'!$A$2:$A$101<>\"\",'Pool Picks'!$B$2:$B$101=\"QB\")"
        "}"
    )
    count = f"IFERROR(COUNTA(UNIQUE({union})),0)"
    assert formulas["Z2"] == f'=IF({count}>10,10&" QB slots, "&{count}&" ticked -- some are hidden","")'


def test_name_formula_unions_edgeraw_ticks_with_pool_picks_typed_rows():
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    name_formula = formulas["A2"]
    assert "UNIQUE({" in name_formula
    assert "FILTER(EdgeRaw!$C$2:$C" in name_formula
    assert "FILTER('Pool Picks'!$A$2:$A$101,'Pool Picks'!$A$2:$A$101<>\"\"," in name_formula
    assert "'Pool Picks'!$B$2:$B$101=\"QB\"" in name_formula


def test_overflow_formula_counts_the_deduped_union_not_edgeraw_alone():
    # A player ticked in EdgeRaw AND typed into Pool Picks must count
    # once toward the cap, not twice -- COUNTA(UNIQUE(...)), not two
    # separate COUNTIFS added together.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    formulas = {a1: rows[0][0] for _, a1, rows in client.update_calls}
    assert "COUNTA(UNIQUE(" in formulas["Z2"]
    assert "FILTER('Pool Picks'!" in formulas["Z2"]


def test_skips_a_block_with_no_position_label_instead_of_writing_a_broken_formula():
    client = SpySheetsClient({"B2": "QB"})  # every other block's position cell is blank
    result = write_pool_formulas(
        client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS
    )

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert "A13" not in ranges_written and "Z13" not in ranges_written
    assert any("skipped" in line for line in result)


def test_never_writes_outside_column_a_and_column_z():
    # The Name formula and overflow warning are the only two columns this
    # function is allowed to touch -- every other column (B..Y) already
    # holds a VLOOKUP written by link_edge_columns and must never be
    # rewritten with a blank/placeholder value.
    client = SpySheetsClient(_POSITIONS)
    write_pool_formulas(client, player_pool_tab="Player Pool", edge_tab="EdgeRaw", name_blocks=_BLOCKS)

    for _, a1_range, _ in client.update_calls:
        assert a1_range[0] in ("A", "Z")
