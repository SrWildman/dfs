"""Clear last week's typed-in lineup data from the working sheet, leaving
every formula and all formatting (conditional formatting included) intact.

The sheet gets duplicated fresh from a template every week (see the
weekly-template link in README.md), so most tabs start clean automatically.
Four tabs don't, because they hold typed values a human enters while
building lineups, not formulas: `Lineups` and `Player Pool` (a name column
per row, everything else VLOOKUPs off it -- though see Task K below,
`Player Pool`'s Name column isn't actually typed anymore), and
`Scratch`/`DK Upload` (full grids of typed player picks / contest entries
with no formulas at all).

Task K (4.3) made Player Pool's Name column a SORT/FILTER formula off
EdgeRaw's Pool tick column instead of a typed value -- `clear_previous_week`
detects this (a formula in the first block's first cell) and skips
clearing Player Pool entirely rather than destroying the formula, since
the actual per-week state (which players are ticked) lives in EdgeRaw,
which `dfs sync` already rewrites every week regardless. A follow-up to
4.3 (same session) then resized the RB/TE/DST blocks larger (Sam wanted
headroom rather than a manual-override escape hatch) via a real
`insertDimension`-based row insert -- see `sheet_pool_resize.py` -- which
is why PLAYER_POOL_NAME_BLOCKS' row counts aren't uniform per position.

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

Lineups' header itself moved from row 1 to row 11 when
`sheet_pool_deck.py`'s `add_pool_deck` inserted `DECK_ROWS` frozen rows
above it (a real Sheets row insert, so every block below shifted down
with it) -- every row number in LINEUPS_NAME_BLOCKS reflects that. This
shipped in three steps, not one -- a first attempt inserted 7 rows for a
names-only "Bench", superseded by a 14-row "pool deck" carrying full
metric columns, then shrunk to the current 10 rows after a live check
showed two full lineup blocks didn't fit on screen below 14 -- see
CONTRIBUTING.md's changelog for the full history. Only the final +10
matters for this constant.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient

LINEUPS_NAME_BLOCKS = [
    (12, 21),
    (25, 34),
    (38, 47),
    (51, 60),
    (64, 73),
    (77, 86),
    (90, 99),
    (103, 112),
    (116, 125),
    (129, 138),
    (142, 151),
    (155, 164),
    (168, 177),
    (181, 190),
    (194, 203),
    (207, 216),
    (220, 229),
    (233, 242),
    (246, 255),
    (259, 268),
]
PLAYER_POOL_NAME_BLOCKS = [(2, 11), (13, 32), (34, 58), (60, 69), (71, 80)]

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

    first_start, _ = PLAYER_POOL_NAME_BLOCKS[0]
    first_cell = client.read_formula(player_pool_tab, f"A{first_start}")
    is_formula_driven = bool(first_cell and first_cell[0] and str(first_cell[0][0]).startswith("="))
    if is_formula_driven:
        # Task K 4.3: Player Pool's Name column is a SORT/FILTER formula off
        # EdgeRaw's Pool tick column, not a typed value -- clearing it would
        # destroy the feature on the very first `dfs week new` after it
        # ships. Nothing here needs clearing: the ticks live in EdgeRaw,
        # which `dfs sync` already rewrites (and restores, see
        # sources/edge.py) every week regardless.
        summary.append(f"{player_pool_tab}: formula-driven (Task K), Name column left alone")
    else:
        pool_ranges = [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
        client.clear_ranges(player_pool_tab, pool_ranges)
        n = len(PLAYER_POOL_NAME_BLOCKS)
        summary.append(f"{player_pool_tab}: cleared Name column across {n} block(s)")

    client.clear_ranges(scratch_tab, [SCRATCH_RANGE])
    summary.append(f"{scratch_tab}: cleared {SCRATCH_RANGE}")

    client.clear_ranges(dk_upload_tab, [DK_UPLOAD_RANGE])
    summary.append(f"{dk_upload_tab}: cleared {DK_UPLOAD_RANGE}")

    return summary
