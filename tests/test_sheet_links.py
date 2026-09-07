from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_links import (
    COLOR_SCALE_LINKED_COLUMNS,
    LINKED_EDGE_COLUMNS,
    edge_lookup_formula,
    link_edge_columns,
)
from dfs.sheets import column_letter


class SpySheetsClient:
    """Records calls instead of touching a real sheet. `header_row` is the
    tab's *entire* current header row -- link_edge_columns only ever reads
    "A1:1", so that's the only read_range response this fake needs to
    fake realistically."""

    def __init__(self, header_row: list[str]):
        self.header_row = header_row
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.color_scale_calls: list[tuple[str, str]] = []
        self.group_calls: list[tuple[str, str, str]] = []
        self.clear_group_calls: list[str] = []

    def read_range(self, tab_name: str, a1_range: str):
        assert a1_range == "A1:1", f"link_edge_columns should only ever read A1:1, got {a1_range!r}"
        return [self.header_row] if self.header_row else []

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))

    def add_color_scale(self, tab_name, a1_range, **_colors):
        self.color_scale_calls.append((tab_name, a1_range))

    def group_columns(self, tab_name, first_col_a1, last_col_a1):
        self.group_calls.append((tab_name, first_col_a1, last_col_a1))

    def clear_column_groups(self, tab_name):
        self.clear_group_calls.append(tab_name)


def test_already_linked_columns_positions_never_move():
    # `dfs setup link-edge` writes VLOOKUP formulas into PlayerPoolRaw/
    # Player Pool/Lineups with a HARDCODED column-index integer per column
    # (see edge_lookup_formula/_vlookup_index) -- those formulas are plain
    # text, not live references to EDGE_COLUMNS, so if a column already
    # linked this way ever moves position in EDGE_COLUMNS, every
    # already-written formula for every column *after* it silently starts
    # reading the wrong data. This actually happened once (LineMove was
    # inserted between GameEnv and Stadium instead of appended at the very
    # end) and was only caught by re-verifying resolved values on the live
    # sheet, not by any test -- this pins the exact positions the current
    # live sheet's already-written formulas depend on, so it can't happen
    # silently again. If this test ever needs to change, `dfs sheets
    # link-edge` must be re-run (after clearing the old linked block) on
    # every sheet it's been applied to.
    assert [EDGE_COLUMNS.index(c) for c in LINKED_EDGE_COLUMNS] == [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]


def test_edge_lookup_formula_uses_correct_range_and_column_index():
    # Leverage sits at EDGE_COLUMNS index 12; within the Name-anchored
    # range that's still VLOOKUP column 12 regardless of EDGE_DATA_OFFSET
    # (a uniform shift cancels out of a *relative* position) -- but the
    # range's own start/end letters do shift by that offset, since Pool
    # occupies column A ahead of EDGE_COLUMNS. Matches what
    # `sheet_style.polish_edge` reports for the same columns.
    assert EDGE_COLUMNS.index("Leverage") == 12
    start_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
    end_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    assert (
        edge_lookup_formula(5, "EdgeRaw", "Leverage")
        == f"=VLOOKUP($A5,EdgeRaw!${start_col}:${end_col},12,false)"
    )


def test_edge_lookup_formula_wraps_optional_columns_in_ifna():
    formula = edge_lookup_formula(5, "EdgeRaw", "Wind")
    assert formula.startswith("=IFNA(VLOOKUP(")
    assert formula.endswith("))")


def test_edge_lookup_formula_does_not_wrap_reliable_columns_in_ifna():
    formula = edge_lookup_formula(5, "EdgeRaw", "Flag")
    assert not formula.startswith("=IFNA(")


def test_link_edge_columns_writes_header_at_first_free_column():
    client = SpySheetsClient(header_row=["Name", "Pos.", "Team", "DK Sal"])  # width 4 -> next col E
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    header_call = next(c for c in client.update_calls if c[1] == "E1:N1")
    assert header_call[2] == [LINKED_EDGE_COLUMNS]


def test_link_edge_columns_fills_every_row_in_every_block():
    client = SpySheetsClient(header_row=["Name"])  # width 1 -> next col B
    link_edge_columns(client, "Player Pool", [(2, 3), (5, 5)], "EdgeRaw")

    block_calls = {a1: rows for _, a1, rows in client.update_calls if a1 not in ("B1:K1",)}
    assert "B2:K3" in block_calls
    assert len(block_calls["B2:K3"]) == 2  # rows 2 and 3
    assert "B5:K5" in block_calls
    assert len(block_calls["B5:K5"]) == 1


def test_link_edge_columns_repeats_header_at_given_rows():
    client = SpySheetsClient(header_row=["Name"])
    link_edge_columns(client, "Lineups", [(2, 5)], "EdgeRaw", header_repeats_at=[14, 27])

    repeated = [a1 for _, a1, rows in client.update_calls if rows == [LINKED_EDGE_COLUMNS]]
    assert "B1:K1" in repeated
    assert "B14:K14" in repeated
    assert "B27:K27" in repeated


def test_link_edge_columns_is_idempotent_when_already_linked():
    # The exact bug this guards against: a header whose *last* N columns
    # already match LINKED_EDGE_COLUMNS must be detected as already-linked,
    # not treated as "current width, so append past it" -- the latter is
    # always true right after a successful run (that's what shipped one
    # real duplicate append to a live sheet before this test existed).
    already_linked_header = ["Name", "Pos.", "Team", *LINKED_EDGE_COLUMNS]
    client = SpySheetsClient(header_row=already_linked_header)
    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    assert client.update_calls == []
    assert client.color_scale_calls == []
    assert client.group_calls == []
    assert "skipped" in result


def test_link_edge_columns_skips_when_linked_block_is_not_at_the_tail():
    # The exact drift found on the old weekly template: LINKED_EDGE_COLUMNS
    # is linked correctly, but two more columns (Venue, Ceil) sit after it
    # because that sheet diverged from the one the tail-only check was
    # written against. A tail-only check sees `[..., "Flag", "Venue",
    # "Ceil"]` as not-yet-linked and appends a second copy; the fix must
    # find the block anywhere in the row and skip.
    header = ["Name", "Pos.", "Team", *LINKED_EDGE_COLUMNS, "Venue", "Ceil"]
    client = SpySheetsClient(header_row=header)
    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    assert client.update_calls == []
    assert client.color_scale_calls == []
    assert client.group_calls == []
    assert "skipped" in result


def test_link_edge_columns_not_fooled_by_a_short_header():
    # A header shorter than LINKED_EDGE_COLUMNS can't possibly already be
    # linked -- must not raise or false-positive on the slice comparison.
    client = SpySheetsClient(header_row=["Name", "Pos."])
    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")
    assert "skipped" not in result
    assert client.update_calls


def test_link_edge_columns_applies_color_scale_to_three_columns_only():
    client = SpySheetsClient(header_row=["Name"])
    link_edge_columns(client, "Player Pool", [(2, 10)], "EdgeRaw")
    assert len(client.color_scale_calls) == len(COLOR_SCALE_LINKED_COLUMNS)


def test_link_edge_columns_groups_exactly_the_new_columns():
    client = SpySheetsClient(header_row=["Name", "Pos."])  # width 2 -> next col C
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")
    assert client.group_calls == [("Player Pool", "C", "L")]


def test_link_edge_columns_run_twice_only_appends_once():
    # The direct regression test for the shipped bug: calling twice against
    # the *same* evolving header (as a real re-run would see) must not grow
    # the tab a second time.
    header = ["Name", "Pos."]
    client = SpySheetsClient(header_row=header)
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    # Simulate the header as it'd really look after that write.
    client.header_row = header + LINKED_EDGE_COLUMNS
    first_run_call_count = len(client.update_calls)

    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")
    assert len(client.update_calls) == first_run_call_count  # no new writes
    assert "skipped" in result
