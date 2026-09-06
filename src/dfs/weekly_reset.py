"""Clear last week's typed-in lineup data from the working sheet, leaving
every formula and all formatting (conditional formatting included) intact.

The sheet gets duplicated fresh from a template every week (see the
weekly-template link in README.md), so most tabs start clean automatically.
Four tabs don't, because they hold typed values a human enters while
building lineups, not formulas: `Lineups` and `Player Pool` (a name column
per row, everything else VLOOKUPs off it), and `Scratch`/`DK Upload` (full
grids of typed player picks / contest entries with no formulas at all).

The row blocks below are specific to this sheet's template layout -- they
were measured directly off the live sheet (non-blank formula rows in each
tab's Pts column), not derived from any general rule, because the blocks
aren't even uniformly sized (Lineups' first block is 10 rows, every other
one is 11). If the template's row layout ever changes, these need
re-measuring the same way.

Lineups specifically: every block from the 2nd one on opens with a
repeated sub-header row (Pos./Team/... re-printed so you don't have to
scroll back up to read the column labels for e.g. lineup 5) whose own
column A holds the literal text "Name", not a player slot -- clearing
that row's column A would wipe the label, not a stale pick. The first
block has no such row (the tab's own header, immediately above it,
already covers it), so its start stays as measured; every later block's
start is offset by 1 to skip it. Confirmed once already: an earlier
version of this file didn't do this and clearing wiped those 19 labels,
restored by hand.

Lineups' header itself moved from row 1 to row 8 when `sheet_bench.py`'s
`add_bench` inserted 7 frozen rows above it (a real Sheets row insert, so
every block below shifted down with it) -- every row number in
LINEUPS_NAME_BLOCKS reflects that.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient

LINEUPS_NAME_BLOCKS = [
    (9, 18),
    (22, 31),
    (35, 44),
    (48, 57),
    (61, 70),
    (74, 83),
    (87, 96),
    (100, 109),
    (113, 122),
    (126, 135),
    (139, 148),
    (152, 161),
    (165, 174),
    (178, 187),
    (191, 200),
    (204, 213),
    (217, 226),
    (230, 239),
    (243, 252),
    (256, 265),
]
PLAYER_POOL_NAME_BLOCKS = [(2, 11), (13, 29), (31, 55), (57, 65), (67, 74)]

# Full-grid tabs: clear everything below the header, generously past any
# row/column count actually seen so far.
SCRATCH_RANGE = "A2:I1000"
DK_UPLOAD_RANGE = "A2:M1000"


def clear_previous_week(
    client: SheetsClient, lineups_tab: str, player_pool_tab: str, scratch_tab: str, dk_upload_tab: str
) -> list[str]:
    """Clear last week's lineup data. Returns a human-readable line per tab
    describing what was cleared, for CLI display."""
    summary = []

    lineups_ranges = [f"A{s}:A{e}" for s, e in LINEUPS_NAME_BLOCKS]
    client.clear_ranges(lineups_tab, lineups_ranges)
    summary.append(f"{lineups_tab}: cleared Name column across {len(LINEUPS_NAME_BLOCKS)} lineup slot(s)")

    pool_ranges = [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
    client.clear_ranges(player_pool_tab, pool_ranges)
    summary.append(f"{player_pool_tab}: cleared Name column across {len(PLAYER_POOL_NAME_BLOCKS)} block(s)")

    client.clear_ranges(scratch_tab, [SCRATCH_RANGE])
    summary.append(f"{scratch_tab}: cleared {SCRATCH_RANGE}")

    client.clear_ranges(dk_upload_tab, [DK_UPLOAD_RANGE])
    summary.append(f"{dk_upload_tab}: cleared {DK_UPLOAD_RANGE}")

    return summary
