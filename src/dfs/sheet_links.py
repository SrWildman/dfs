"""Appends EdgeRaw's derived columns (CeilVal, Leverage, Flag, ...) onto the
far right of Player Pool and Lineups, via the same VLOOKUP-by-Name join
those tabs already use against PlayerPoolRaw for Pos./Team/Pts/etc.

Deliberately APPEND-ONLY. Player Pool and Lineups already have live
formulas keyed by hardcoded column-index integers (see CONTRIBUTING.md's
Phase 8 postmortem: inserting a column shifts formula *ranges* app-wide but
not those hardcoded integers). New columns always go strictly after
whatever's currently in the tab, so nothing already there shifts -- and
`link_edge_columns` is idempotent (checks the target header before writing)
so re-running it doesn't append a second copy.

The written cells are plain formula *text*, not a living reference to
`derived.EDGE_COLUMNS` -- if that list's order or membership ever changes,
the already-written formulas in the sheet won't update themselves. Re-run
`dfs sheets link-edge` after such a change (clearing the old linked columns
by hand first, since this only appends, never overwrites).
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS
from dfs.sheets import SheetsClient, column_letter

# The EdgeRaw columns worth surfacing elsewhere -- excludes what Player
# Pool/Lineups already show via PlayerPoolRaw (Position, Team, Opp, Salary,
# ProjPts/Pts, Ceiling/Ceil, Val, ProjOwn/Rstr%), so nothing gets duplicated.
LINKED_EDGE_COLUMNS = [
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

# Color-scaled the same way EdgeRaw itself is (sheet_style.polish_edge) --
# these three are the ones actually worth scanning by eye.
COLOR_SCALE_LINKED_COLUMNS = ("Leverage", "CeilVal", "GameEnv")

# PlayerPoolRaw is the template's hub tab that Player Pool AND Lineups both
# already VLOOKUP against for Pos./Team/Pts/etc. -- fixed name, not
# user-configurable, unlike the dfs-managed source tabs in
# config.toml's tab_mappings. Every column there fills via a formula keyed
# off DkSalClean's own row (e.g. `=DkSalClean!D2`), not a manually-typed
# Name column like Player Pool/Lineups have, so there's exactly one
# contiguous block rather than per-lineup/per-position blocks. Measured
# directly off the live sheet the same way weekly_reset.py's blocks were
# (its Pts column's non-blank formula rows run 2-987) -- re-measure if the
# template's provisioned player-pool capacity ever changes.
PLAYER_POOL_RAW_TAB = "PlayerPoolRaw"
PLAYER_POOL_RAW_BLOCK = [(2, 987)]

# Wrapped in IFNA because these can legitimately be blank for a valid,
# already-typed-in player (a dome game has no Wind; games/weather may not
# be synced this run) -- matching Player Pool's own O/U, Spread, Team
# Implied columns, which use IFNA for the same reason. Everything else here
# should reliably resolve once EdgeRaw is synced, matching the un-wrapped
# VLOOKUPs Pos./Team/Pts/etc. already use.
_OPTIONAL_LINKED_COLUMNS = {"Wind"}

_EDGE_NAME_COLUMN = "Name"
_EDGE_RANGE_START = column_letter(EDGE_COLUMNS.index(_EDGE_NAME_COLUMN))
_EDGE_RANGE_END = column_letter(len(EDGE_COLUMNS) - 1)


def _vlookup_index(column_name: str) -> int:
    """1-based position of `column_name` within EdgeRaw!$<start>:$<end>,
    for VLOOKUP's 3rd argument."""
    return EDGE_COLUMNS.index(column_name) - EDGE_COLUMNS.index(_EDGE_NAME_COLUMN) + 1


def edge_lookup_formula(row: int, edge_tab: str, column_name: str) -> str:
    """The VLOOKUP-by-Name formula for one cell: `row`'s player Name
    (column A of the tab this formula is written into) looked up against
    `edge_tab`'s `column_name`."""
    index = _vlookup_index(column_name)
    base = f"VLOOKUP($A{row},{edge_tab}!${_EDGE_RANGE_START}:${_EDGE_RANGE_END},{index},false)"
    return f"=IFNA({base})" if column_name in _OPTIONAL_LINKED_COLUMNS else f"={base}"


def find_all_contiguous(header: list[str], block: list[str]) -> list[int]:
    """Every index where `block` appears as a contiguous run in `header`.
    Shared with `doctor.py`, which needs to tell "linked exactly once"
    apart from "linked twice" (a duplicate append), not just "linked or
    not"."""
    n = len(block)
    if n == 0:
        return []
    return [i for i in range(len(header) - n + 1) if header[i : i + n] == block]


def _find_contiguous(header: list[str], block: list[str]) -> int | None:
    """First index where `block` appears as a contiguous run in `header`,
    or None. Two sheets built from the same source can still diverge in
    total width (e.g. a template that's picked up an extra column of its
    own), so LINKED_EDGE_COLUMNS can legitimately end up short of the
    tab's last column -- checking only the *tail* missed that case and let
    `link_edge_columns` append a second copy onto a sheet whose layout had
    drifted from the one the tail-check was written against."""
    positions = find_all_contiguous(header, block)
    return positions[0] if positions else None


def link_edge_columns(
    client: SheetsClient,
    tab: str,
    name_blocks: list[tuple[int, int]],
    edge_tab: str,
    *,
    header_row: int = 1,
    header_repeats_at: list[int] | None = None,
) -> str:
    """Append LINKED_EDGE_COLUMNS to `tab`, starting one column past
    whatever's currently there, formulas filled for every row in every
    `name_blocks` range (inclusive). `header_repeats_at` re-prints the
    header text at additional rows (Lineups repeats its header once per
    lineup slot). Idempotent: if LINKED_EDGE_COLUMNS already appears as a
    contiguous run anywhere in the header, does nothing and reports that
    it skipped -- checking anywhere in the header, not just the tail,
    since a tail-only check silently appends a second copy the moment two
    sheets built from the same source diverge in total width (this
    happened for real: the old weekly template picked up two extra
    trailing columns of its own, `Venue`/`Ceil`, so its tail no longer
    matched even though the columns were already linked earlier in the
    row).

    `header_row` defaults to 1, true for Player Pool/PlayerPoolRaw, but
    not for Lineups once `sheet_pool_deck.py`'s `add_pool_deck` inserts
    rows above its header: reading/writing row 1 there finds the deck's
    controls instead of the real header, mistakes an already-linked tab for
    an unlinked one, and appends a *second*, wrongly-positioned copy of
    LINKED_EDGE_COLUMNS starting at column B -- overwriting every lineup
    block's Pos./Team/DK Sal/etc. data with a duplicate EdgeRaw lookup.
    This happened for real on the template; see CONTRIBUTING.md's
    changelog. The CLI passes Lineups' real header row explicitly rather
    than hardcoding it.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_row_values[0] if header_row_values else []

    n = len(LINKED_EDGE_COLUMNS)
    if _find_contiguous(header, LINKED_EDGE_COLUMNS) is not None:
        return f"{tab}: already linked ({n} header columns match somewhere in the row) -- skipped"

    current_width = len(header)
    start_col = column_letter(current_width)
    end_col = column_letter(current_width + n - 1)

    client.update_range(tab, f"{start_col}{header_row}:{end_col}{header_row}", [LINKED_EDGE_COLUMNS])
    for header_row_num in header_repeats_at or []:
        a1 = f"{start_col}{header_row_num}:{end_col}{header_row_num}"
        client.update_range(tab, a1, [LINKED_EDGE_COLUMNS])

    for start, end in name_blocks:
        rows = [
            [edge_lookup_formula(row, edge_tab, col) for col in LINKED_EDGE_COLUMNS]
            for row in range(start, end + 1)
        ]
        client.update_range(tab, f"{start_col}{start}:{end_col}{end}", rows)

    last_row = max(end for _, end in name_blocks)
    for column_name in COLOR_SCALE_LINKED_COLUMNS:
        col = column_letter(current_width + LINKED_EDGE_COLUMNS.index(column_name))
        client.add_color_scale(
            tab,
            f"{col}2:{col}{last_row}",
            min_color={"red": 0.96, "green": 0.80, "blue": 0.80},
            mid_color={"red": 1.0, "green": 1.0, "blue": 0.80},
            max_color={"red": 0.72, "green": 0.88, "blue": 0.72},
        )

    client.group_columns(tab, start_col, end_col)

    return (
        f"{tab}: linked {len(LINKED_EDGE_COLUMNS)} EdgeRaw column(s) "
        f"at column {start_col} (grouped, collapsible)"
    )
