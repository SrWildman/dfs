import dfs.sheet_pool_deck as sheet_pool_deck
from dfs.sheet_pool_deck import (
    _OLD_BENCH_ROWS,
    _OLD_BENCH_TITLE,
    _OLD_DECK14_HEADER_ROW,
    _OLD_DECK14_ROWS,
    DECK_ROWS,
    POOL_SORT_TAB,
    add_pool_deck,
)
from dfs.sheet_style import HEADER_FMT
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS

_LINEUPS_HEADER_ROW = LINEUPS_NAME_BLOCKS[0][0] - 1

# Player Pool and Lineups' column layouts are each independently derived
# (see `_write_deck_controls`'s own comment) and drifted apart for real:
# Player Pool has no `Check`/`% of Rstr` equivalent (those are per-built-
# lineup concepts) but picked up an unrelated `Cash` column, so its
# linked block sits one column left of Lineups' own. These two fixtures
# mirror that real drift rather than assuming the two tabs match.
_POOL_HEADER = [
    "Name",
    "Pos.",
    "Team",
    "DK Sal",
    "O/U",
    "Spread",
    "Team Implied",
    "Opp.",
    "Venue",
    "OppPosRank",
    "Pts",
    "Ceil",
    "Val",
    "Rstr%",
    "Cash",
    "CeilVal",
    "CeilPct",
    "Leverage",
    "LevBasis",
    "GameEnv",
    "Stadium",
    "Roof",
    "Wind",
    "Avail",
    "Flag",
    "Overflow",
]

_LINEUPS_HEADER = [
    "Name",
    "Pos.",
    "Team",
    "DK Sal",
    "O/U",
    "Spread",
    "Team Implied",
    "Opp.",
    "Venue",
    "OppPosRank",
    "Pts",
    "Ceil",
    "Val",
    "Rstr%",
    "Check",
    "% of Rstr",
    "CeilVal",
    "CeilPct",
    "Leverage",
    "LevBasis",
    "GameEnv",
    "Stadium",
    "Roof",
    "Wind",
    "Avail",
    "Flag",
]


class FakeDeckClient:
    def __init__(self, *, cells: dict[str, str] | None = None):
        self._cells = cells or {}
        self.insert_calls: list[tuple[str, int, int]] = []
        self.delete_calls: list[tuple[str, int, int]] = []
        self.clear_calls: list[tuple[str, list[str]]] = []
        self.write_tab_calls: list[tuple[str, list[list]]] = []
        self.tab_properties_calls: list[tuple[str, bool | None]] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.dropdown_calls: list[tuple[str, list[str]]] = []
        self.clear_validation_calls: list[str] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[tuple] = []
        self.row_height_calls: list[tuple] = []
        self.clear_cf_calls: list[str | None] = []
        self.color_scale_calls: list[tuple[str, dict]] = []

    def read_range(self, tab_name: str, a1_range: str):
        if tab_name == "Player Pool" and a1_range == "A1:Z1":
            return [_POOL_HEADER]
        if tab_name == "Lineups" and a1_range == f"A{_LINEUPS_HEADER_ROW}:{_LINEUPS_HEADER_ROW}":
            # Lineups' own real block header -- what _write_deck_controls
            # reads to build row 3 and the window from (see its own
            # comment on why this must be Lineups' header, not Player
            # Pool's).
            return [_LINEUPS_HEADER]
        if tab_name == "Lineups" and a1_range == "A3:3":
            # Row 3 is written by _write_deck_controls (A3:Z3, a copy of
            # Lineups' own block header) before polish_pool_deck reads it
            # back -- mirrors that in this fake.
            return [_LINEUPS_HEADER]
        if tab_name == "Lineups":
            value = self._cells.get(a1_range, "")
            return [[value]] if value else []
        raise AssertionError(f"unexpected read_range: {tab_name!r} {a1_range!r}")

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((tab_name, at_row, count))

    def delete_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.delete_calls.append((tab_name, at_row, count))

    def clear_ranges(self, tab_name: str, a1_ranges: list[str]) -> None:
        self.clear_calls.append((tab_name, a1_ranges))

    def write_tab(self, tab_name: str, rows: list[list], **_kwargs) -> int:
        self.write_tab_calls.append((tab_name, rows))
        return len(rows)

    def set_tab_properties(self, tab_name: str, *, hidden: bool | None = None, **_kwargs) -> None:
        self.tab_properties_calls.append((tab_name, hidden))

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))

    def set_dropdown_validation(self, tab_name: str, a1_range: str, options: list[str]) -> None:
        self.dropdown_calls.append((a1_range, options))

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        self.clear_validation_calls.append(a1_range)

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))

    def set_row_heights(self, tab_name: str, *, start_row: int, end_row: int, pixel_size: int) -> None:
        self.row_height_calls.append((tab_name, start_row, end_row, pixel_size))

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def clear_conditional_formats(
        self, tab_name: str, *, column: str | None = None, row_range: tuple[int, int] | None = None
    ) -> None:
        self.clear_cf_calls.append(row_range)

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.color_scale_calls.append((a1_range, kwargs))


def test_add_pool_deck_already_present_skips_structure_but_still_refreshes_everything():
    # Structurally a no-op, but every formatting/content fix found after
    # this first shipped only reaches an already-migrated sheet because
    # the rebuild below is unconditional -- an early return here once
    # left exactly that gap (see CONTRIBUTING.md's changelog).
    client = FakeDeckClient(cells={f"A{DECK_ROWS + 1}": "Name"})

    result = add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert "refresh" in result
    assert client.insert_calls == []
    assert client.delete_calls == []
    assert client.clear_calls == []
    assert client.write_tab_calls != []  # PoolSort still gets rebuilt
    reset_call = next(c for c in client.format_calls if c[0] == f"A1:Z{DECK_ROWS}")
    assert reset_call[1]["backgroundColor"] == {"red": 1, "green": 1, "blue": 1}
    g1_call = next(c for c in client.format_calls if c[0] == "G1")
    assert g1_call[1] == {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}


def test_add_pool_deck_from_fresh_inserts_deck_rows_once():
    client = FakeDeckClient()

    result = add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.insert_calls == [("Lineups", 1, DECK_ROWS)]
    assert client.delete_calls == []
    assert client.clear_calls == []
    assert "scratch" in result


def test_add_pool_deck_migrates_old_bench_by_clearing_then_inserting_the_remainder():
    client = FakeDeckClient(cells={"A1": _OLD_BENCH_TITLE})

    result = add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.clear_calls == [("Lineups", ["A1:Z7"])]
    assert client.insert_calls == [("Lineups", 1, DECK_ROWS - _OLD_BENCH_ROWS)]
    assert client.delete_calls == []
    assert "bench migration" in result


def test_add_pool_deck_shrinks_old_fourteen_row_deck_by_deleting_the_tail():
    client = FakeDeckClient(cells={f"A{_OLD_DECK14_HEADER_ROW}": "Name"})

    result = add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    # Deletion must start right after the new window's last row (3 +
    # _WINDOW_SIZE = 9), so the old blank separator row shifts up to
    # land exactly on row DECK_ROWS.
    assert client.delete_calls == [("Lineups", DECK_ROWS, _OLD_DECK14_ROWS - DECK_ROWS)]
    assert client.insert_calls == []
    assert client.clear_calls == []
    assert "14-row deck shrink" in result


def test_add_pool_deck_builds_pool_sort_hidden_tab_with_position_and_sort_formula():
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    tab_name, rows = client.write_tab_calls[0]
    assert tab_name == POOL_SORT_TAB
    assert rows[0] == _POOL_HEADER
    formula = rows[1][0]
    assert "'Player Pool'!$A$2:$Z$80" in formula
    assert 'Lineups!$B$1="ALL"' in formula
    assert "'Player Pool'!$B$2:$B$80=Lineups!$B$1" in formula
    assert "Lineups!$G$1" in formula  # sort column, from the dropdown's MATCH
    assert client.tab_properties_calls == [(POOL_SORT_TAB, True)]


def test_pool_last_row_is_derived_from_player_pool_name_blocks_not_hardcoded():
    # The deck once hardcoded 74 as Player Pool's last row; Task K's block
    # resize silently truncated its view of the DST block to six of ten
    # slots since nothing re-derived this from the blocks that moved. This
    # asserts the constant tracks whatever weekly_reset currently defines,
    # computed independently here (not by re-reading the module's own
    # source).
    assert sheet_pool_deck._POOL_LAST_ROW == max(end for _, end in PLAYER_POOL_NAME_BLOCKS)


def test_add_pool_deck_follows_pool_last_row_if_it_changes(monkeypatch):
    # _POOL_COUNT_FORMULA is baked from _POOL_LAST_ROW at import time, so
    # only PoolSort's own filter/sort formula (built fresh on every call)
    # can be exercised by monkeypatching after import -- that's the exact
    # formula this task's fix touched.
    monkeypatch.setattr(sheet_pool_deck, "_POOL_LAST_ROW", 999)

    client = FakeDeckClient()
    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    formula = client.write_tab_calls[0][1][1][0]
    assert "'Player Pool'!$A$2:$Z$999" in formula


def test_add_pool_deck_writes_controls_defaults_and_dropdowns():
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    control_call = next(c for c in client.update_calls if c[0] == "A1:I1")
    row = control_call[1][0]
    assert row[0:6] == ["Position", "QB", "Sort by", "CeilVal", "Start at", 1]
    assert "MATCH($D$1," in row[6]
    assert "in pool" in row[8]

    assert ("B1", ["ALL", "QB", "RB", "WR", "TE", "DST"]) in client.dropdown_calls
    assert ("D1", ["CeilVal", "Leverage", "Pts", "Ceil", "Val", "DK Sal", "Rstr%"]) in client.dropdown_calls


def test_add_pool_deck_pool_count_readout_uses_countif_not_counta():
    # PoolSort's IFERROR(SORT(FILTER(...)),"") falls back to a single ""
    # cell when the pool is empty, which COUNTA counts as non-blank (a
    # real Sheets gotcha) -- misreporting "1 in pool" on an empty pool.
    # COUNTIF(...,"?*") requires at least one real character and doesn't
    # have this problem.
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    control_call = next(c for c in client.update_calls if c[0] == "A1:I1")
    readout = control_call[1][0][8]
    assert "COUNTA" not in readout
    assert readout.count('COUNTIF(PoolSort!$A$2:$A$80,"?*")') == 3
    # "showing N-M" must be suppressed entirely when the count is 0,
    # not rendered as a nonsensical "showing 1-0" (start past end).
    assert 'IF(COUNTIF(PoolSort!$A$2:$A$80,"?*")=0,""' in readout


def test_add_pool_deck_copies_lineups_own_header_into_row_three_and_styles_it():
    # Row 3 (the deck's own mini-header) must match LINEUPS' OWN block
    # header below it, not Player Pool's -- the two tabs' layouts are not
    # guaranteed to match (they drifted apart for real: Player Pool has
    # no `Check`/`% of Rstr` columns Lineups has). Copying Player Pool's
    # header here would print "CeilVal" in row 3 directly above a block
    # row further down that says "% of Rstr" in the same column.
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    header_call = next(c for c in client.update_calls if c[0] == "A3:Z3")
    assert header_call[1] == [_LINEUPS_HEADER]
    format_call = next(c for c in client.format_calls if c[0] == "A3:Z3")
    assert format_call[1] == HEADER_FMT


def test_add_pool_deck_window_formulas_match_by_header_name_not_position():
    # Player Pool's real header has `CeilVal` one column left of where
    # Lineups' own header has it (Cash vs Check+% of Rstr) -- the window
    # must still pull CeilVal into the position CeilVal actually sits at
    # in row 3 (Lineups' header, index 16), reading from wherever
    # CeilVal actually is in Player Pool's header (index 15), not the
    # same raw index on both sides.
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    window_call = next(c for c in client.update_calls if c[0] == "A4:Z9")
    rows = window_call[1]
    assert len(rows) == 6
    first_row = rows[0]  # deck row 4 -> PoolSort row 2 when F1=1
    assert "INDEX(PoolSort!A:A,$F$1+4-3)" in first_row[0]
    # CeilVal sits at index 16 in Lineups' header (row 3) but index 15 in
    # Player Pool's -- the formula must reference PoolSort's P column
    # (Player Pool's real CeilVal position), placed at window index 16.
    assert "INDEX(PoolSort!P:P,$F$1+4-3)" in first_row[16]
    # Check (14) and % of Rstr (15) exist only on Lineups -- Player Pool
    # has no equivalent, so these stay blank rather than pulling the
    # wrong column (Player Pool's Cash/CeilVal) under the wrong label.
    assert first_row[14] == ""
    assert first_row[15] == ""
    last_row = rows[-1]  # deck row 9
    assert "INDEX(PoolSort!A:A,$F$1+9-3)" in last_row[0]


def test_add_pool_deck_hides_g1_after_the_formatting_reset_not_before():
    # _reset_deck_formatting repaints the whole zone including G1 -- if it
    # ran after G1's own white-on-white hide instead of before, G1 would
    # come back visible (black-on-white) despite still being load-bearing
    # plumbing, not something meant to be read.
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    reset_index = next(i for i, c in enumerate(client.format_calls) if c[0] == f"A1:Z{DECK_ROWS}")
    g1_index = next(i for i, c in enumerate(client.format_calls) if c[0] == "G1")
    assert reset_index < g1_index
    white_text = {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}
    assert client.format_calls[g1_index][1] == white_text


def test_add_pool_deck_shrink_also_rebuilds_controls_and_rehides_g1():
    client = FakeDeckClient(cells={f"A{_OLD_DECK14_HEADER_ROW}": "Name"})

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.write_tab_calls != []  # PoolSort rebuilt even on the shrink path
    g1_call = next(c for c in client.format_calls if c[0] == "G1")
    assert g1_call[1] == {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}


def test_add_pool_deck_clears_inherited_data_validation_from_the_whole_zone():
    # Lineups' column A carries a pre-existing "must be a real player
    # name" validation (looked up against PlayerPoolRaw) that every row
    # this module has ever inserted also inherited, since insert_rows at
    # the top has no way to insert rows without copying the formatting
    # (and validation) of whatever gets pushed below them. A person
    # typing into one of these cells got rejected by it even though no
    # API-level check ever caught it -- see CONTRIBUTING.md's changelog.
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.clear_validation_calls == [f"A1:Z{DECK_ROWS}"]


def test_add_pool_deck_freezes_sets_compact_row_heights_and_resets_formatting():
    client = FakeDeckClient()

    add_pool_deck(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.freeze_calls == [("Lineups", DECK_ROWS, None)]
    assert client.row_height_calls == [("Lineups", 3, 9, 18)]
    reset_call = next(c for c in client.format_calls if c[0] == f"A1:Z{DECK_ROWS}")
    assert reset_call[1] == {
        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
        "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": False},
    }
