import re

from dfs.models import ROSTER_SLOTS
from dfs.sheet_instructions import (
    _LAYOUT,
    _TAB_ROWS,
    INSTRUCTIONS_LAST_ROW,
    INSTRUCTIONS_TAB,
    build_instructions_tab,
    render_instructions_grid,
)
from dfs.sheet_pool_cells import POOL_OPTIONS
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS


class FakeClient:
    def __init__(self, *, present: bool = True):
        self._present = present
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.cleared: list[tuple[str, list[str]]] = []
        self.formats: list[tuple[str, dict]] = []
        self.widths: dict[str, int] = {}
        self.heights: dict[int, int] = {}

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def clear_ranges(self, tab_name: str, a1_ranges: list[str]) -> None:
        self.cleared.append((tab_name, a1_ranges))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.formats.append((a1_range, fmt))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.widths = widths

    def set_row_heights(self, tab_name: str, *, start_row: int, end_row: int, pixel_size: int) -> None:
        self.heights[start_row] = pixel_size


def _text() -> str:
    return " ".join(f"{a} {b}" for a, b in render_instructions_grid().values())


def test_skips_cleanly_when_tab_is_absent():
    client = FakeClient(present=False)
    assert "not present" in build_instructions_tab(client)
    assert client.update_calls == [] and client.cleared == []


def test_the_grid_is_one_row_per_layout_entry_and_ends_at_the_last_row():
    grid = render_instructions_grid()
    assert sorted(grid) == list(range(1, INSTRUCTIONS_LAST_ROW + 1))
    assert grid[1][0] == "How to use this sheet"


def test_the_four_parts_the_playbook_promises_are_all_there_in_order():
    sections = [a for kind, a, _b in _LAYOUT if kind == "section"]
    assert sections[:4] == ["READ THIS FIRST", "THE WEEK, STEP BY STEP", "READING THE NUMBERS", "THE TABS"]


def test_every_step_of_the_week_names_a_day_and_the_commands_sam_runs():
    text = _text()
    for command in (
        "dfs week new",
        "dfs sync",
        "dfs sync --live",
        "dfs lineups late-swap",
        "dfs export",
        "dfs week close",
    ):
        assert command in text, command
    for day in ("Tue / Wed", "Thu - Sat", "Sunday", "Mon / Tue"):
        assert day in text, day


def test_every_number_and_chip_sam_asked_about_has_a_plain_line():
    labels = {a for kind, a, _b in _LAYOUT if kind == "row"}
    for name in ("CalPts", "Hit3x%", "Boom%", "Bust%", "CeilM", "ValAdj", "P(cash)", "P(190+)", "The chips"):
        assert name in labels, name
    assert "The arrows (trends)" in labels


def test_no_backticks_no_source_names_no_function_names():
    text = _text()
    assert "`" not in text
    for jargon in ("TFFBOptoRaw", "nflverse", "sheet_", "def ", "config.toml", "gid", "VLOOKUP", "()"):
        assert jargon not in text, jargon


def test_the_text_describes_the_current_sheet_not_a_retired_one():
    text = _text()
    for stale in ("Pool Picks", "PoolSort", "LevBasis", "Per-position leaders", "Punt finder"):
        assert stale not in text, stale
    assert "bold name = at least one flag" in text.lower()
    assert "Pool summary" in text and "Pool check" in text and "Queue" in text
    for option in POOL_OPTIONS:
        assert option in text
    assert "Set dropdown" not in text and "Remove takes" not in text  # the Set dropdown is retired
    assert "Pool dropdown at the left" in text


def test_player_pool_row_derives_caps_not_hardcoded():
    body = next(b for name, b in _TAB_ROWS if name == "Player Pool")
    for position, (start, end) in zip(("QB", "RB", "WR", "TE", "DST"), PLAYER_POOL_NAME_BLOCKS, strict=True):
        assert f"{position} {end - start + 1}" in body


def test_lineups_row_derives_roster_slots_and_block_layout_not_hardcoded():
    body = next(b for name, b in _TAB_ROWS if name == "Lineups")
    assert f"starting at row {LINEUPS_NAME_BLOCKS[0][0]}" in body
    assert f"{len(ROSTER_SLOTS)}-player roster block" in body
    assert f"({', '.join(ROSTER_SLOTS)})" in body
    assert f"{len(LINEUPS_NAME_BLOCKS)} blocks total" in body


def test_every_visible_tab_gets_a_line():
    from dfs.sheet_style import WEEK_ORDER

    labels = {name for name, _ in _TAB_ROWS}
    assert {name for name, _family in WEEK_ORDER} <= labels | {"Season"}


def test_build_clears_first_writes_the_whole_grid_and_styles_the_bands():
    client = FakeClient()
    result = build_instructions_tab(client)
    assert INSTRUCTIONS_TAB in result and "row(s) written" in result
    assert client.cleared and client.cleared[0][1][0].startswith("A1:B")
    (_tab, a1, rows) = client.update_calls[0]
    assert a1 == f"A1:B{INSTRUCTIONS_LAST_ROW}" and len(rows) == INSTRUCTIONS_LAST_ROW
    assert client.widths["B"] > client.widths["A"]
    bands = [r for r, _ in client.formats if re.fullmatch(r"A\d+:B\d+", r) and r != "A1:B120"]
    assert len(bands) == sum(1 for kind, *_ in _LAYOUT if kind == "section")


def test_build_is_idempotent_full_rewrite():
    client = FakeClient()
    build_instructions_tab(client)
    first = len(client.update_calls)
    build_instructions_tab(client)
    assert len(client.update_calls) == first * 2


def test_a_long_row_is_taller_than_a_short_one():
    client = FakeClient()
    build_instructions_tab(client)
    longest = max(range(1, INSTRUCTIONS_LAST_ROW + 1), key=lambda n: len(render_instructions_grid()[n][1]))
    assert client.heights[longest] > client.heights[1]
    assert set(client.heights) == set(range(1, INSTRUCTIONS_LAST_ROW + 1))
