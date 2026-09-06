from dfs.weekly_reset import (
    DK_UPLOAD_RANGE,
    LINEUPS_NAME_BLOCKS,
    PLAYER_POOL_NAME_BLOCKS,
    SCRATCH_RANGE,
    clear_previous_week,
)


class SpySheetsClient:
    """Records clear_ranges calls instead of touching a real sheet."""

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def clear_ranges(self, tab_name, a1_ranges):
        self.calls.append((tab_name, list(a1_ranges)))


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


def test_lineups_blocks_skip_the_repeated_sub_header_row():
    # First block has no sub-header (the tab's own header, immediately
    # above it, covers it); every later block must start one row after
    # where it'd naively be measured, so the sub-header row's own "Name"
    # label in column A never gets cleared. First block starts at row 16
    # (row 15 is the header) since sheet_pool_deck.py's 14-row pool deck
    # insert -- see CONTRIBUTING.md's changelog.
    first_start, _ = LINEUPS_NAME_BLOCKS[0]
    assert first_start == 16
    for (_, prev_end), (start, _) in zip(LINEUPS_NAME_BLOCKS, LINEUPS_NAME_BLOCKS[1:], strict=False):
        assert start == prev_end + 4  # 3-row gap + 1 sub-header row skipped
