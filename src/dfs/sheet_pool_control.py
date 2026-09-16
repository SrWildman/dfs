"""A3.1 -- the "add a player" control row at the top of Player Pool.

Pool Picks (`sheet_pool_picks.py`, removed -- see CONTRIBUTING.md's A3
changelog entry) existed only because Player Pool's Name column couldn't
be typed into directly once Task K made it a formula. Sam never wanted a
second tab for this; A3 replaces it with one control row pinned at the
top of Player Pool itself: a live type-ahead search box (the same
`ONE_OF_RANGE` validation Pool Picks used) that adds a player to the pool
exactly as ticking them in EdgeRaw does.

Structure, mirroring the label/input pairing `sheet_pool_deck.py`'s own
row 1 already establishes (A1 "Position" label / B1 dropdown, etc.):
  A1   Plain text label, "Add a player".
  B1   The actual input -- a live search box against EdgeRaw's Name
       column. `sheet_pool_formulas._union_array` reads this cell (by
       name, via a VLOOKUP against EdgeRaw for its position) as the
       second half of each position block's union, exactly the role
       Pool Picks' 100-row column used to play.

This is a real, one-time `insertDimension` (row 1 becomes the control,
the old row 1 -- the real header -- moves to row 2, and every block
shifts down by 1: see `weekly_reset.PLAYER_POOL_NAME_BLOCKS`/
`PLAYER_POOL_HEADER_ROW`), so it must run at most once per sheet.
Migration-aware the same way `sheet_pool_deck.add_pool_deck` is: state is
detected from what's actually on the sheet (A1 already holds this
module's own label text means "already migrated"), not tracked
separately, so this is safe to call from `dfs setup polish` on every run
without re-inserting a row it already inserted.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_style import INPUT_BG
from dfs.sheets import SheetsClient, column_letter
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW

_LABEL_CELL = f"A{PLAYER_POOL_CONTROL_ROW}"
_INPUT_CELL = f"B{PLAYER_POOL_CONTROL_ROW}"
_LABEL_TEXT = "Add a player"

_edge_name_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)


def _already_migrated(client: SheetsClient, player_pool_tab: str) -> bool:
    values = client.read_range(player_pool_tab, _LABEL_CELL)
    return bool(values and values[0] and values[0][0] == _LABEL_TEXT)


def ensure_pool_control_row(client: SheetsClient, player_pool_tab: str, edge_tab: str) -> str:
    """Idempotent: inserts the control row exactly once (detected via
    `_already_migrated`), then unconditionally re-applies the label,
    input validation and formatting -- same "structural step runs once,
    everything else reruns" split `add_pool_deck` uses, for the same
    reason (a formatting fix made after this first shipped should still
    reach an already-migrated sheet).
    """
    migrated = _already_migrated(client, player_pool_tab)
    if not migrated:
        client.insert_rows(player_pool_tab, at_row=PLAYER_POOL_CONTROL_ROW, count=1)
        # insertDimension's inheritFromBefore=False inherits the row now
        # pushed below it -- here, the tab's own real header, with its
        # dark fill/bold white text and its own data validation (if any).
        # Reset before writing this row's own content, same discipline as
        # sheet_pool_deck.py's _reset_deck_formatting.
        normal = {
            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
            "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": False},
        }
        last_col = column_letter(25)  # Z -- generous, matches insert_rows' own blank-row width
        control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:{last_col}{PLAYER_POOL_CONTROL_ROW}"
        client.format_range(player_pool_tab, control_row_range, normal)
        client.clear_data_validation(player_pool_tab, control_row_range)

    client.update_range(player_pool_tab, _LABEL_CELL, [[_LABEL_TEXT]])
    client.set_range_dropdown_validation(
        player_pool_tab, _INPUT_CELL, source=f"{edge_tab}!${_edge_name_col}$2:${_edge_name_col}"
    )
    client.format_range(player_pool_tab, _INPUT_CELL, {"backgroundColor": INPUT_BG})

    origin = "refresh" if migrated else "inserted"
    return f"{player_pool_tab}: add-a-player control row ({origin}), input at {_INPUT_CELL}"
