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
from dfs.weekly_reset import (
    PLAYER_POOL_ADDED_NAMES_HEADER,
    PLAYER_POOL_ADDED_NAMES_ROWS,
    PLAYER_POOL_CONTROL_ROW,
    PLAYER_POOL_HEADER_ROW,
)

_LABEL_CELL = f"A{PLAYER_POOL_CONTROL_ROW}"
_INPUT_CELL = f"B{PLAYER_POOL_CONTROL_ROW}"
_LABEL_TEXT = "Add a player"
_NORMAL_ROW1_FORMAT = {
    "backgroundColor": {"red": 1, "green": 1, "blue": 1},
    "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": False},
}

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

    The row-1 background/text reset below runs EVERY call, not just on
    first migration, and covers the tab's own CURRENT width (read from
    row 2's real header), not a hardcoded guess -- found live,
    2026-09-16: the original version only ever reset A:Z, once, at
    `insertDimension` time. Every column added or inserted after that
    (`Edge ↗`, the whole WEATHER/MOVEMENT/INTERNAL zone, `Used`/`In`, this
    session's `OppPosRank`) could inherit a stray dark header fill into
    its own row-1 cell (from `insertDimension`'s own inherit-from-
    neighbor behavior, or a reorder's `moveDimension` carrying formatting
    along with a column) with nothing ever cleaning it up again --
    visible live as a solid dark bar across most of row 1, exactly the
    "black boxes" symptom Sam reported once the pool actually had players
    in it to look at.
    """
    migrated = _already_migrated(client, player_pool_tab)
    if not migrated:
        client.insert_rows(player_pool_tab, at_row=PLAYER_POOL_CONTROL_ROW, count=1)

    # insertDimension's inheritFromBefore=False inherits the row now
    # pushed below it -- here, the tab's own real header, with its dark
    # fill/bold white text and its own data validation (if any). Reset
    # before writing this row's own content, same discipline as
    # sheet_pool_deck.py's _reset_deck_formatting -- and re-applied on
    # every call (not gated behind `not migrated`) so a column added
    # later, which this reset never covered the first time, self-heals
    # the next time `dfs setup polish`/`add-pool-control` runs.
    header_row_values = client.read_range(
        player_pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"
    )
    header = header_row_values[0] if header_row_values else []
    last_col = column_letter(max(len(header), 26) - 1)
    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:{last_col}{PLAYER_POOL_CONTROL_ROW}"
    client.format_range(player_pool_tab, control_row_range, _NORMAL_ROW1_FORMAT)
    client.clear_data_validation(player_pool_tab, control_row_range)

    client.update_range(player_pool_tab, _LABEL_CELL, [[_LABEL_TEXT]])
    client.set_range_dropdown_validation(
        player_pool_tab, _INPUT_CELL, source=f"{edge_tab}!${_edge_name_col}$2:${_edge_name_col}"
    )
    client.format_range(player_pool_tab, _INPUT_CELL, {"backgroundColor": INPUT_BG})

    origin = "refresh" if migrated else "inserted"
    return f"{player_pool_tab}: add-a-player control row ({origin}), input at {_INPUT_CELL}"


def drain_control_cell_into_added_names(client: SheetsClient, player_pool_tab: str) -> str:
    """A6 (2026-09-22): "Adding a player in row one of the pool works, but
    only once. If you try and add a second in the same spot, the first is
    deleted." The control cell (`_INPUT_CELL`) holds one typed name --
    `sheet_pool_formulas._union_array` used to read only that cell, so a
    second typed name replaced the first in every formula that depended
    on it. Fix: called from `dfs sync`, this reads the control cell and,
    if it holds a name, appends it to the next free row of the `Added`
    accumulator column (see `weekly_reset.PLAYER_POOL_ADDED_NAMES_HEADER`)
    and blanks the control cell -- so the NEXT typed name has an empty
    cell to land in, and the previous one keeps showing up (via
    `_union_array`'s third source) instead of vanishing.

    A name already present in the accumulator (the control cell wasn't
    re-cleared for some reason, or `dfs sync` ran twice back to back) is
    not appended a second time -- the control cell is still cleared, but
    nothing new is written; `_union_array`'s `UNIQUE` would have
    deduplicated a re-add anyway, so this is a courtesy against the list
    filling up with repeats, not a correctness requirement. A full
    accumulator (all `PLAYER_POOL_ADDED_NAMES_ROWS` rows already used)
    leaves the control cell UNTOUCHED (not cleared) so the pending name
    isn't silently lost -- same "don't destroy real state" instinct as
    everywhere else in this codebase; the caller's own message says so.
    """
    control_value = client.read_range(player_pool_tab, _INPUT_CELL)
    name = control_value[0][0].strip() if control_value and control_value[0] else ""
    if not name:
        return f"{player_pool_tab}: no pending add-a-player name"

    header_row_values = client.read_range(
        player_pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"
    )
    header = header_row_values[0] if header_row_values else []
    if PLAYER_POOL_ADDED_NAMES_HEADER not in header:
        raise ValueError(
            f"{player_pool_tab!r} header is missing {PLAYER_POOL_ADDED_NAMES_HEADER!r} -- "
            "run `dfs setup reorder-columns` first"
        )
    added_col = column_letter(header.index(PLAYER_POOL_ADDED_NAMES_HEADER))
    first_row = PLAYER_POOL_HEADER_ROW + 1
    last_row = PLAYER_POOL_HEADER_ROW + PLAYER_POOL_ADDED_NAMES_ROWS

    existing_rows = client.read_range(player_pool_tab, f"{added_col}{first_row}:{added_col}{last_row}")
    existing_names = [row[0].strip() for row in existing_rows if row and row[0] and row[0].strip()]

    if name in existing_names:
        client.clear_ranges(player_pool_tab, [_INPUT_CELL])
        return f"{player_pool_tab}: {name!r} already on the add-a-player list -- control cell cleared"

    next_row = first_row + len(existing_names)
    if next_row > last_row:
        return (
            f"{player_pool_tab}: add-a-player list is full ({PLAYER_POOL_ADDED_NAMES_ROWS} slots) -- "
            f"{name!r} NOT added, control cell left as-is"
        )

    client.update_range(player_pool_tab, f"{added_col}{next_row}", [[name]])
    client.clear_ranges(player_pool_tab, [_INPUT_CELL])
    return f"{player_pool_tab}: {name!r} added to the pool ({added_col}{next_row})"
