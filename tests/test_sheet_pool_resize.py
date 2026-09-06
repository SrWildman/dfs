import pytest

from dfs.sheet_pool_resize import fix_color_scale_ranges, grow_block, resize_player_pool


class SpySheetsClient:
    """Records insert_rows/update_range calls and fakes read_formula off a
    small in-memory grid -- same convention as this repo's other Spy*
    fakes (test_sheet_links.py, test_sheet_pool_deck.py)."""

    def __init__(self, rows: dict[int, list[str]]):
        self._rows = rows  # {row_number: [B, C, ..., Y]}
        self.insert_calls: list[tuple[int, int]] = []
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.conditional_format_calls: list[tuple[str, str | None]] = []
        self.color_scale_calls: list[tuple[str, str]] = []

    def read_formula(self, tab_name: str, a1_range: str):
        row_num = int(a1_range.split(":")[0][1:])
        return [self._rows[row_num]]

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((at_row, count))

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def read_range(self, tab_name: str, a1_range: str):
        return [["Name", "Pos.", "Team", "CeilVal", "Leverage", "GameEnv"]]

    def clear_conditional_formats(self, tab_name: str, *, column: str | None = None) -> None:
        self.conditional_format_calls.append((tab_name, column))

    def add_color_scale(self, tab_name, a1_range, **_colors):
        self.color_scale_calls.append((tab_name, a1_range))


def _rb_row_29():
    # B..Y for a real RB row, trimmed to a few representative formula
    # shapes actually seen on the live template.
    return [
        "RB",
        "=VLOOKUP(A29,PlayerPoolRaw!A:C,3,false)",
        "=VLOOKUP(A29,PlayerPoolRaw!$A:D,4,false)",
        "=VLOOKUP($A29,EdgeRaw!$B:$T,10,false)",
        "=IFNA(VLOOKUP($A29,EdgeRaw!$B:$T,17,false))",
    ]


def test_grow_block_inserts_immediately_after_the_current_last_row():
    client = SpySheetsClient({29: _rb_row_29()})
    grow_block(client, "Player Pool", position="RB", current_last_row=29, count=3)
    assert client.insert_calls == [(30, 3)]


def test_grow_block_returns_the_new_last_row():
    client = SpySheetsClient({29: _rb_row_29()})
    new_last = grow_block(client, "Player Pool", position="RB", current_last_row=29, count=3)
    assert new_last == 32


def test_grow_block_writes_position_label_and_substitutes_row_number_only():
    client = SpySheetsClient({29: _rb_row_29()})
    grow_block(client, "Player Pool", position="RB", current_last_row=29, count=2)

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert ranges_written == {"B30:Y30", "B31:Y31"}

    written = {a1: rows[0] for _, a1, rows in client.update_calls}
    row30 = written["B30:Y30"]
    assert row30[0] == "RB"
    assert row30[1] == "=VLOOKUP(A30,PlayerPoolRaw!A:C,3,false)"
    assert row30[3] == "=VLOOKUP($A30,EdgeRaw!$B:$T,10,false)"
    assert row30[4] == "=IFNA(VLOOKUP($A30,EdgeRaw!$B:$T,17,false))"

    row31 = written["B31:Y31"]
    assert row31[1] == "=VLOOKUP(A31,PlayerPoolRaw!A:C,3,false)"


def test_grow_block_with_zero_count_is_not_expected_to_be_called_but_would_no_op():
    client = SpySheetsClient({29: _rb_row_29()})
    new_last = grow_block(client, "Player Pool", position="RB", current_last_row=29, count=0)
    assert new_last == 29
    assert client.insert_calls == [(30, 0)]
    assert client.update_calls == []


def test_resize_player_pool_only_grows_blocks_that_need_it_and_shifts_the_rest():
    # Template rows are looked up at each block's CURRENT (shifted) last
    # row, not its original position -- TE's last row shifts from 65 to
    # 68 once RB grows by 3 ahead of it, and DST's from 74 to 78 once RB
    # (+3) and TE (+1) have both grown ahead of it.
    rows = {
        11: _rb_row_29(),
        29: _rb_row_29(),
        55: _rb_row_29(),
        68: _rb_row_29(),
        78: _rb_row_29(),
    }
    client = SpySheetsClient(rows)
    blocks = [(2, 11), (13, 29), (31, 55), (57, 65), (67, 74)]
    positions = ["QB", "RB", "WR", "TE", "DST"]
    targets = [10, 20, 25, 10, 10]  # QB/WR unchanged, RB+3, TE+1, DST+2

    new_blocks = resize_player_pool(
        client, player_pool_tab="Player Pool", blocks=blocks, positions=positions, target_sizes=targets
    )

    assert new_blocks == [(2, 11), (13, 32), (34, 58), (60, 69), (71, 80)]
    # QB never grows -> no insert_rows call for it.
    assert client.insert_calls == [(30, 3), (69, 1), (79, 2)]


def test_resize_player_pool_rejects_a_shrink():
    client = SpySheetsClient({11: _rb_row_29()})
    with pytest.raises(ValueError, match="smaller than current size"):
        resize_player_pool(
            client,
            player_pool_tab="Player Pool",
            blocks=[(2, 11)],
            positions=["QB"],
            target_sizes=[5],
        )


def test_fix_color_scale_ranges_clears_and_readds_each_linked_column_to_the_new_last_row():
    client = SpySheetsClient({})
    fix_color_scale_ranges(client, "Player Pool", last_row=80)

    cleared_columns = {col for _, col in client.conditional_format_calls}
    assert cleared_columns == {"D", "E", "F"}  # CeilVal, Leverage, GameEnv per the fake header
    ranges_added = {a1 for _, a1 in client.color_scale_calls}
    assert ranges_added == {"D2:D80", "E2:E80", "F2:F80"}
