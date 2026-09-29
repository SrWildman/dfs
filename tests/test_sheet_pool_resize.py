import pytest

from dfs.sheet_pool_resize import fix_color_scale_ranges, grow_block, resize_player_pool


class SpySheetsClient:
    """Records insert_rows/update_range calls and fakes read_formula off a
    small in-memory grid -- same convention as this repo's other Spy*
    fakes (test_sheet_links.py, test_sheet_pool_deck.py)."""

    def __init__(self, rows: dict[int, list[str]], header: list[str] | None = None, header_row: int = 1):
        self._rows = rows  # {row_number: [B, C, ..., last]}
        # Last column is derived from this header's own width (see
        # grow_block's docstring on why it used to be hardcoded to "Y" --
        # a real Phase-3-triggered bug); 6 columns here (A-F) matches
        # _rb_row_29()'s 5 formula cells (B-F) below.
        self._header = header or ["Name", "Pos.", "Team", "CeilVal", "Leverage", "GameEnv"]
        # PROMPT_BOARD_FIXES.md item 8: the header doesn't ALWAYS sit at
        # row 1 (Player Pool's real one sits at row 2, past the
        # add-a-player control row) -- only returning it for the row it
        # actually claims to be at (blank otherwise) is what makes a wrong
        # `header_row` argument fail realistically here, the same way it
        # failed for real against the live template (`ValueError:
        # 'Leverage' is not in list`), rather than a fake that returns the
        # same canned header regardless of which row was asked for.
        self._header_row = header_row
        self.insert_calls: list[tuple[int, int]] = []
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.conditional_format_calls: list[tuple[str, str | None]] = []
        self.color_scale_calls: list[tuple[str, str]] = []
        self.boolean_rule_calls: list[tuple[str, str]] = []

    def read_formula(self, tab_name: str, a1_range: str):
        row_num = int(a1_range.split(":")[0][1:])
        return [self._rows[row_num]]

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((at_row, count))

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def read_range(self, tab_name: str, a1_range: str):
        row_num = int(a1_range.split(":")[0][1:])
        return [self._header] if row_num == self._header_row else [[]]

    def clear_conditional_formats(self, tab_name: str, *, column: str | None = None) -> None:
        self.conditional_format_calls.append((tab_name, column))

    def clear_conditional_formats_for(self, tab_name, targets) -> None:
        for column, _row_range in targets:
            self.clear_conditional_formats(tab_name, column=column)

    def add_color_scale(self, tab_name, a1_range, **_colors):
        self.color_scale_calls.append((tab_name, a1_range))

    def add_boolean_rule(self, tab_name, a1_range, **_kwargs):
        self.boolean_rule_calls.append((tab_name, a1_range))

    def add_color_scales(self, tab_name, specs):
        for spec in specs:
            self.color_scale_calls.append((tab_name, spec["a1_range"]))

    def add_boolean_rules(self, tab_name, specs):
        for spec in specs:
            self.boolean_rule_calls.append((tab_name, spec["a1_range"]))


def _rb_row_29():
    # B..(the fake header's last column) for a real RB row, trimmed to a
    # few representative formula shapes actually seen on the live
    # template.
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
    assert ranges_written == {"B30:F30", "B31:F31"}

    written = {a1: rows[0] for _, a1, rows in client.update_calls}
    row30 = written["B30:F30"]
    assert row30[0] == "RB"
    assert row30[1] == "=VLOOKUP(A30,PlayerPoolRaw!A:C,3,false)"
    assert row30[3] == "=VLOOKUP($A30,EdgeRaw!$B:$T,10,false)"
    assert row30[4] == "=IFNA(VLOOKUP($A30,EdgeRaw!$B:$T,17,false))"

    row31 = written["B31:F31"]
    assert row31[1] == "=VLOOKUP(A31,PlayerPoolRaw!A:C,3,false)"


def test_grow_block_derives_its_last_column_from_the_header_not_a_hardcode():
    # The exact regression this guards against: `grow_block` used to
    # hardcode "Y" as the last formula column -- true only under the
    # pre-Phase-3 layout. A wider header (more columns than the old Y)
    # must extend the copied range past Y, not silently stop there and
    # leave the new rows missing everything beyond it.
    header = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "AA", "AB"]  # 28 columns -> last is AB
    client = SpySheetsClient({29: _rb_row_29()}, header=header)
    grow_block(client, "Player Pool", position="RB", current_last_row=29, count=1)
    assert {a1 for _, a1, _ in client.update_calls} == {"B30:AB30"}


def test_grow_block_reads_the_header_from_its_own_real_row():
    # PROMPT_BOARD_FIXES.md item 8, found live (2026-09-26): `header_row`
    # used to be hardcoded to row 1 -- correct when this was first written
    # (2026-09-06, Player Pool's header really was row 1 then), but the
    # 2026-09-16 add-a-player control row pushed it to row 2 without this
    # function ever being revisited. Reading row 1 then returns the
    # 1-cell control row instead of the real header, so `last_formula_col`
    # collapses to `column_letter(0) = "A"` -- BELOW `_POSITION_COLUMN =
    # "B"`, producing an inverted "B{row}:A{row}" range that silently
    # reorders to "A{row}:B{row}" and writes the position label into
    # BOTH Name and Pos., losing every real formula from C onward. Caught
    # for real on the live sheet: every newly-grown row across all five
    # blocks was missing its EdgeRaw-linked VLOOKUPs entirely.
    client = SpySheetsClient({29: _rb_row_29()}, header_row=2)
    grow_block(client, "Player Pool", position="RB", current_last_row=29, count=1, header_row=2)
    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert ranges_written == {"B30:F30"}
    written = {a1: rows[0] for _, a1, rows in client.update_calls}
    row30 = written["B30:F30"]
    assert row30[0] == "RB"
    assert row30[1] == "=VLOOKUP(A30,PlayerPoolRaw!A:C,3,false)"


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


def test_resize_player_pool_threads_header_row_through_to_grow_block():
    # Same shape as the header-row regression above, but through the
    # public `resize_player_pool` entry point -- this is what
    # `dfs setup resize-player-pool` actually calls, so a header_row that
    # gets dropped here is just as live-breaking as in `grow_block` itself.
    client = SpySheetsClient({29: _rb_row_29()}, header_row=2)
    new_blocks = resize_player_pool(
        client,
        player_pool_tab="Player Pool",
        blocks=[(13, 29)],
        positions=["RB"],
        target_sizes=[18],
        header_row=2,
    )
    assert new_blocks == [(13, 30)]
    written = {a1: rows[0] for _, a1, rows in client.update_calls}
    assert written["B30:F30"][1] == "=VLOOKUP(A30,PlayerPoolRaw!A:C,3,false)"


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
    ranges_added = {a1 for _, a1 in client.boolean_rule_calls}
    assert ranges_added == {"D2:D80", "E2:E80", "F2:F80"}


def test_fix_color_scale_ranges_reads_the_header_from_its_own_real_row():
    # PROMPT_BOARD_FIXES.md item 8, found live: this used to hardcode
    # "A1:1" regardless of caller -- correct for a tab whose header really
    # is row 1, but Player Pool's own header sits at row 2 (past the
    # add-a-player control row), and the old hardcoded version raised
    # `ValueError: 'Leverage' is not in list` the first time this actually
    # ran against it. `header_row` must be threaded through to both the
    # header read AND the scale range's own data-start row.
    client = SpySheetsClient({}, header_row=2)
    fix_color_scale_ranges(client, "Player Pool", last_row=101, header_row=2)

    ranges_added = {a1 for _, a1 in client.boolean_rule_calls}
    assert ranges_added == {"D3:D101", "E3:E101", "F3:F101"}
