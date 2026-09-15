from dfs.weekly_reset import (
    DK_UPLOAD_RANGE,
    LINEUPS_NAME_BLOCKS,
    PLAYER_POOL_NAME_BLOCKS,
    SCRATCH_RANGE,
    clear_previous_week,
    clear_synced_tabs,
)


class SpySheetsClient:
    """Records clear_ranges calls instead of touching a real sheet."""

    def __init__(self, player_pool_a1_formula: str = ""):
        self.calls: list[tuple[str, list[str]]] = []
        self._player_pool_a1_formula = player_pool_a1_formula

    def clear_ranges(self, tab_name, a1_ranges):
        self.calls.append((tab_name, list(a1_ranges)))

    def read_formula(self, tab_name, a1_range):
        return [[self._player_pool_a1_formula]]


def test_clear_previous_week_targets_each_configured_tab():
    client = SpySheetsClient()
    clear_previous_week(
        client,
        lineups_tab="Lineups",
        player_pool_tab="Player Pool",
        scratch_tab="Scratch",
        dk_upload_tab="DK Upload",
    )
    tabs_touched = [tab for tab, _ in client.calls]
    assert tabs_touched == ["Lineups", "Player Pool", "Scratch", "DK Upload"]


def test_clear_previous_week_only_clears_column_a_for_name_columns():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Lineups"] == [f"A{s}:A{e}" for s, e in LINEUPS_NAME_BLOCKS]
    assert calls["Player Pool"] == [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
    assert all(r.startswith("A") and ":A" in r for r in calls["Lineups"])
    assert all(r.startswith("A") and ":A" in r for r in calls["Player Pool"])


def test_clear_previous_week_clears_full_grid_for_scratch_and_dk_upload():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Scratch"] == [SCRATCH_RANGE]
    assert calls["DK Upload"] == [DK_UPLOAD_RANGE]


def test_clear_previous_week_skips_player_pool_when_name_column_is_a_formula():
    # Task K 4.3: once Player Pool's Name column is SORT/FILTER-driven off
    # EdgeRaw, clearing it on `dfs week new` would destroy the feature.
    client = SpySheetsClient(player_pool_a1_formula='=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER(...)),"")')
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    tabs_touched = [tab for tab, _ in client.calls]

    assert "Player Pool" not in tabs_touched
    assert tabs_touched == ["Lineups", "Scratch", "DK Upload"]


def test_clear_previous_week_still_clears_player_pool_when_it_holds_typed_values():
    client = SpySheetsClient(player_pool_a1_formula="Patrick Mahomes")
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Player Pool"] == [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]


def test_lineups_blocks_skip_the_repeated_sub_header_row():
    # First block has no sub-header (the tab's own header, immediately
    # above it, covers it); every later block must start one row after
    # where it'd naively be measured, so the sub-header row's own "Name"
    # label in column A never gets cleared. First block starts at row 12
    # (row 11 is the header) since sheet_pool_deck.py's 10-row pool deck
    # insert -- see CONTRIBUTING.md's changelog.
    #
    # `end` is now the last REAL roster row (Fix 2.4 -- it used to also be
    # that block's totals row), so the gap from one block's `end` to the
    # next block's `start` is 5, not 4: totals row, the "avg salary
    # remaining per unfilled slot" row directly below it, one blank
    # spacer, then the next block's repeated sub-header.
    first_start, _ = LINEUPS_NAME_BLOCKS[0]
    assert first_start == 12
    for (_, prev_end), (start, _) in zip(LINEUPS_NAME_BLOCKS, LINEUPS_NAME_BLOCKS[1:], strict=False):
        assert start == prev_end + 5


class SpyTabClient:
    def __init__(self):
        self.write_tab_calls: list[tuple[str, list, bool]] = []

    def write_tab(self, tab_name, rows, *, create_if_missing: bool = True, clear_first: bool = True):
        self.write_tab_calls.append((tab_name, rows, clear_first))
        return len(rows)


def test_clear_synced_tabs_blanks_every_mapped_source_tab():
    client = SpyTabClient()
    mappings = {"projections": "TFFBOptoRaw", "draftkings": "DKSalRaw", "edge": "EdgeRaw"}

    summary = clear_synced_tabs(client, mappings, ["projections", "draftkings", "edge"])

    assert {tab for tab, _rows, _clear in client.write_tab_calls} == {
        "TFFBOptoRaw",
        "DKSalRaw",
        "EdgeRaw",
    }
    assert all(rows == [] for _tab, rows, _clear in client.write_tab_calls)
    assert all(clear_first is True for _tab, _rows, clear_first in client.write_tab_calls)
    assert len(summary) == 3


def test_clear_synced_tabs_skips_a_source_with_no_tab_mapping():
    client = SpyTabClient()
    summary = clear_synced_tabs(client, {"projections": "TFFBOptoRaw"}, ["projections", "unmapped_source"])

    assert [tab for tab, _rows, _clear in client.write_tab_calls] == ["TFFBOptoRaw"]
    assert len(summary) == 1
