from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_links import (
    COLOR_SCALE_LINKED_COLUMNS,
    LINKED_EDGE_COLUMNS,
    edge_lookup_formula,
    edge_row_hyperlink_formula,
    link_edge_columns,
    write_edge_row_links,
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
        self.group_calls: list[tuple[str, str, str, bool]] = []
        self.clear_group_calls: list[str] = []
        self.ensure_capacity_calls: list[tuple[str, int]] = []

    def read_range(self, tab_name: str, a1_range: str):
        assert a1_range == "A1:1", f"link_edge_columns should only ever read A1:1, got {a1_range!r}"
        return [self.header_row] if self.header_row else []

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))

    def add_color_scale(self, tab_name, a1_range, **_colors):
        self.color_scale_calls.append((tab_name, a1_range))

    def group_columns(self, tab_name, first_col_a1, last_col_a1, *, collapsed=False):
        self.group_calls.append((tab_name, first_col_a1, last_col_a1, collapsed))

    def clear_column_groups(self, tab_name):
        self.clear_group_calls.append(tab_name)

    def tab_gid(self, tab_name):
        return 999

    def ensure_column_capacity(self, tab_name, min_cols):
        self.ensure_capacity_calls.append((tab_name, min_cols))


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
    # Phase 3 reordered EDGE_COLUMNS into designed IDENTITY/DECISION/GAME/
    # WEATHER/MOVEMENT/INTERNAL zones (see derived.py) and expanded
    # LINKED_EDGE_COLUMNS from 10 to 16 members (Id/OwnPct/ImpliedMove/
    # TotMove/SpdMove/GameStart newly surfaced onto Player Pool/Lineups/
    # PlayerPoolRaw) -- that's fine, since `edge_lookup_formula` derives
    # each column's VLOOKUP index from its own position relative to Name,
    # not from LINKED_EDGE_COLUMNS being contiguous within EDGE_COLUMNS.
    # Phase 5 (2026-09-16) pulled CeilPct/OwnPct out of the tail INTERTAL
    # zone and next to Ceiling/CeilVal and ProjOwn respectively (so
    # EdgeRaw's own position-fair percentile columns are visible while
    # filtering by position, instead of requiring a scroll past
    # Stadium..Id first) -- their EDGE_COLUMNS index dropped a lot (25/26
    # -> 9/11) while everything from Leverage onward shifted +2. Then a
    # native `OppPosRank` was added right after `GameEnv` (Sam: "all data
    # should be in edge raw") -- everything from `Stadium` onward shifted
    # a further +1.
    #
    # Phase 6, Part 2 (2026-09-17): redesigned into a shared spine +
    # collapsed groups (Game, Ceiling detail, Movement, Weather). ProjOwn
    # (was a linked-nowhere native/spine value) is renamed to Own% and
    # stays out of LINKED_EDGE_COLUMNS entirely; Leverage moves off the
    # spine into the Ceiling detail group (Part 7.1, folded into this
    # reorder). This pins the NEW positions; `link-edge --force` must be
    # (and was) re-run on both sheets after this reorder.
    assert [EDGE_COLUMNS.index(c) for c in LINKED_EDGE_COLUMNS] == [
        8,
        10,
        11,
        14,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
    ]


def test_edge_lookup_formula_uses_correct_range_and_column_index():
    # Phase 6, Part 2: Leverage moves off the spine into the collapsed
    # Ceiling detail group, landing at EDGE_COLUMNS index 18 (was 12); the
    # Name-anchored range that's VLOOKUP column 19 (1-based, relative to
    # Name at index 0) regardless of EDGE_DATA_OFFSET (a uniform shift
    # cancels out of a *relative* position) -- but the range's own start/
    # end letters do shift by that offset, since Pool occupies column A
    # ahead of EDGE_COLUMNS. Matches what `sheet_style.polish_edge`
    # reports for the same columns.
    assert EDGE_COLUMNS.index("Leverage") == 18
    start_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
    end_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    assert edge_lookup_formula(5, "EdgeRaw", "Leverage") == (
        f'=IF($A5="","",VLOOKUP($A5,EdgeRaw!${start_col}:${end_col},19,false))'
    )


def test_edge_lookup_formula_wraps_optional_columns_in_ifna():
    formula = edge_lookup_formula(5, "EdgeRaw", "Wind")
    assert 'IF($A5="","",IFNA(VLOOKUP(' in formula
    assert formula.endswith(")))")


def test_edge_lookup_formula_does_not_wrap_reliable_columns_in_ifna():
    formula = edge_lookup_formula(5, "EdgeRaw", "Flag")
    assert "IFNA(" not in formula


def test_edge_lookup_formula_guards_against_a_blank_name():
    # An unfilled Player Pool/Lineups slot's Name cell is blank -- VLOOKUP
    # against a blank key resolves to #N/A, not blank, so every linked
    # column needs this guard to just sit empty instead. Found live: an
    # under-capacity position block showed a wall of #N/A instead.
    formula = edge_lookup_formula(5, "EdgeRaw", "Flag")
    assert formula.startswith('=IF($A5="","",')
    assert formula.endswith(")")


def test_link_edge_columns_writes_header_at_first_free_column():
    client = SpySheetsClient(header_row=["Name", "Pos.", "Team", "DK Sal"])  # width 4 -> next col E
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    # All 16 names are missing, so they're all created (appended) in
    # LINKED_EDGE_COLUMNS' own order -- one contiguous run, E through T.
    header_call = next(c for c in client.update_calls if c[1] == "E1:T1")
    assert header_call[2] == [LINKED_EDGE_COLUMNS]


def test_link_edge_columns_grows_grid_capacity_before_appending():
    # Live bug (Phase 5F): a tab already at its provisioned grid width --
    # e.g. fully linked already, with one name suddenly "missing" because
    # a rename changed what Python looks for -- raised "exceeds grid
    # limits" the instant this tried to write past the current column
    # count, since a sheet's grid width doesn't auto-grow for a write.
    client = SpySheetsClient(header_row=["Name", "Pos.", "Team", "DK Sal"])
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    assert ("Player Pool", 4 + len(LINKED_EDGE_COLUMNS)) in client.ensure_capacity_calls


def test_link_edge_columns_fills_every_row_in_every_block():
    client = SpySheetsClient(header_row=["Name"])  # width 1 -> next col B
    link_edge_columns(client, "Player Pool", [(2, 3), (5, 5)], "EdgeRaw")

    # All 16 created columns land contiguous (B through Q) since they're
    # all newly appended together, so each name_block still writes in one
    # `update_range` call, just a wider one than the old 10-column block.
    block_calls = {a1: rows for _, a1, rows in client.update_calls if a1 not in ("B1:Q1",)}
    assert "B2:Q3" in block_calls
    assert len(block_calls["B2:Q3"]) == 2  # rows 2 and 3
    assert "B5:Q5" in block_calls
    assert len(block_calls["B5:Q5"]) == 1


def test_link_edge_columns_repeats_header_at_given_rows():
    client = SpySheetsClient(header_row=["Name"])
    link_edge_columns(client, "Lineups", [(2, 5)], "EdgeRaw", header_repeats_at=[14, 27])

    repeated = [a1 for _, a1, rows in client.update_calls if rows == [LINKED_EDGE_COLUMNS]]
    assert "B1:Q1" in repeated
    assert "B14:Q14" in repeated
    assert "B27:Q27" in repeated


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


def test_link_edge_columns_force_refreshes_formulas_on_an_already_linked_tab():
    # The exact case `force` exists for: `edge_lookup_formula`'s own
    # generation logic changed (the blank-name guard), not any column's
    # position -- the normal "something's missing" trigger never fires on
    # a tab that's already fully linked, so nothing would otherwise pick
    # up the new formula shape without a manual clear-and-relink.
    already_linked_header = ["Name", "Pos.", "Team", *LINKED_EDGE_COLUMNS]
    client = SpySheetsClient(header_row=already_linked_header)
    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw", force=True)

    assert "skipped" not in result
    assert client.update_calls  # formulas actually rewritten
    formulas = {cell for _, _, rows in client.update_calls for row in rows for cell in row}
    assert edge_lookup_formula(2, "EdgeRaw", "Flag") in formulas


def test_link_edge_columns_force_writes_no_header_row_when_nothing_is_missing():
    # force refreshes formulas, but must not touch row 1 at all when
    # every name is already present -- there's nothing to create.
    already_linked_header = ["Name", "Pos.", "Team", *LINKED_EDGE_COLUMNS]
    client = SpySheetsClient(header_row=already_linked_header)
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw", force=True)

    assert not any(a1.endswith("1") for _, a1, _ in client.update_calls)


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


def test_link_edge_columns_groups_and_collapses_game_ceiling_detail_movement_and_weather():
    # Phase 6, Part 2: GAME (GameEnv is the only linked member -- O/U,
    # Spread, Team Implied, OppPosRank are native, absent from this
    # minimal fixture), CEILING DETAIL (CeilPct/OwnPct/Leverage/LevBasis),
    # MOVEMENT (ImpliedMove/TotMove/SpdMove/GameStart) and WEATHER
    # (Stadium/Roof/Wind -- Venue is native, also absent here) all
    # collapse by default -- CeilVal/Avail/Flag stay ungrouped and always
    # visible. These four zones sit back-to-back with nothing between
    # them (in this fixture, GameEnv lands immediately before CeilPct
    # once all 16 missing linked columns are appended together), so they
    # land as ONE combined group, not four separate `group_columns` calls
    # -- Sheets silently extends an existing group to cover an adjacent
    # `addDimensionGroup`, so independent calls at adjacent ranges make a
    # later call's own collapse-fold request fail outright ("no group
    # spans exactly that range"). Found live, on the template, the first
    # time MOVEMENT/INTERNAL were added as their own groups next to the
    # pre-existing WEATHER one (Phase 3); re-confirmed here now that a
    # fourth zone (GAME) joins the same merge.
    client = SpySheetsClient(header_row=["Name", "Pos."])  # width 2 -> next col C
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")
    assert client.group_calls == [
        ("Player Pool", "F", "Q", True),  # GameEnv..Wind, merged
    ]


def test_link_edge_columns_writes_into_an_interleaved_designed_position():
    # 3.2's core fix: a linked column already sitting somewhere in the
    # middle of the header (as the Phase 3 reorder leaves it -- GameEnv
    # between two native columns here) must get its formulas written INTO
    # that column, never appended as a second copy past the end.
    header = ["Name", "Pos.", "Team Implied", "GameEnv", "OppPosRank"]  # GameEnv is column D
    client = SpySheetsClient(header_row=header)
    link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    # GameEnv's row-2:3 formulas land in column D, not appended past the
    # tab's original width (column F onward).
    assert any(a1 == "D2:D3" for _, a1, _ in client.update_calls)
    # And GameEnv's header cell (D1, already correct) is never rewritten.
    assert not any(a1 == "D1" for _, a1, _ in client.update_calls)


def test_link_edge_columns_only_creates_the_names_actually_missing():
    header = ["Name", "Pos.", "GameEnv"]  # GameEnv already linked in place
    client = SpySheetsClient(header_row=header)
    result = link_edge_columns(client, "Player Pool", [(2, 3)], "EdgeRaw")

    header_writes = [rows[0] for _, a1, rows in client.update_calls if a1.endswith("1") and len(rows) == 1]
    created_names = {name for row in header_writes for name in row}
    assert "GameEnv" not in created_names
    assert created_names == set(LINKED_EDGE_COLUMNS) - {"GameEnv"}
    assert "15 newly created" in result


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


def test_edge_row_hyperlink_formula_targets_the_right_gid_and_column():
    formula = edge_row_hyperlink_formula(5, "EdgeRaw", 999)
    start_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
    assert formula == (
        f'=IF($A5="","",IFNA(HYPERLINK("#gid=999&range={start_col}"&'
        f'MATCH($A5,EdgeRaw!${start_col}:${start_col},0),"Edge ↗"),"-"))'
    )


def test_edge_row_hyperlink_formula_guards_against_a_blank_name():
    formula = edge_row_hyperlink_formula(5, "EdgeRaw", 999)
    assert formula.startswith('=IF($A5="","",')


def test_write_edge_row_links_skips_a_tab_with_no_edge_column():
    client = SpySheetsClient(header_row=["Name", "Pos."])
    result = write_edge_row_links(client, "Player Pool", [(2, 3)], "EdgeRaw")
    assert "no 'Edge ↗' column" in result
    assert client.update_calls == []


def test_write_edge_row_links_writes_every_row_in_every_block():
    client = SpySheetsClient(header_row=["Name", "Edge ↗", "Pos."])
    result = write_edge_row_links(client, "Player Pool", [(2, 3), (5, 6)], "EdgeRaw")

    ranges_written = {a1 for _, a1, _ in client.update_calls}
    assert ranges_written == {"B2:B3", "B5:B6"}
    assert "4 row(s)" in result


# group_lineups_columns/VEGAS_GROUP_COLUMNS removed in Phase 6, Part 2 --
# GAME is now one of the four groups link_edge_columns itself builds
# identically on every tab (see test_link_edge_columns_groups_and_
# collapses_game_ceiling_detail_movement_and_weather below), superseding
# Lineups' own separate "Vegas" group entirely.


def test_write_edge_row_links_honors_a_non_default_header_row():
    client = SpySheetsClient(header_row=["Name", "Edge ↗"])

    class HeaderRowSpy(SpySheetsClient):
        def read_range(self, tab_name: str, a1_range: str):
            assert a1_range == "A11:11"
            return [self.header_row]

    client = HeaderRowSpy(header_row=["Name", "Edge ↗"])
    write_edge_row_links(client, "Lineups", [(12, 13)], "EdgeRaw", header_row=11)
    assert {a1 for _, a1, _ in client.update_calls} == {"B12:B13"}
