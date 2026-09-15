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

# Fix 2.4: each tuple is now the NINE REAL ROSTER ROWS ONLY (QB, RB, RB,
# WR, WR, WR, TE, FLEX, DEF) -- `end` used to also be that block's totals
# row (the SUM/remaining-salary row directly below the 9th slot), which
# meant `dfs setup link-edge`/`polish_guardrails` treated it as a tenth
# roster slot: a permanent #N/A tenth-player VLOOKUP that could never
# resolve, since the totals row's own Name cell (column A) is always
# blank. The totals row for a given block is now `LINEUPS_TOTALS_ROWS`'
# corresponding entry (always `end + 1`) -- every consumer that used to
# read `end` as "the totals row" and back out the last real slot via
# `end - 1` now reads `end` directly as the last real slot, and reaches
# for `LINEUPS_TOTALS_ROWS` (or an inline `end + 1`) for the totals row
# itself. See CONTRIBUTING.md's structural changelog for the full list of
# symbols this touched.
LINEUPS_NAME_BLOCKS = [
    (12, 20),
    (25, 33),
    (38, 46),
    (51, 59),
    (64, 72),
    (77, 85),
    (90, 98),
    (103, 111),
    (116, 124),
    (129, 137),
    (142, 150),
    (155, 163),
    (168, 176),
    (181, 189),
    (194, 202),
    (207, 215),
    (220, 228),
    (233, 241),
    (246, 254),
    (259, 267),
]
LINEUPS_TOTALS_ROWS = [end + 1 for _, end in LINEUPS_NAME_BLOCKS]
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


def clear_synced_tabs(
    client: SheetsClient, tab_mappings: dict[str, str], source_names: list[str]
) -> list[str]:
    """Fix 2.14 -- Sam: "If it's not ready when we do our new week, blank
    is better than bad." A synced source's own upload step already
    clears its tab right before writing fresh data, but only on success
    (`run_sync` continues past a failed source rather than stopping, so a
    source that fails on `dfs week new`'s first-ever sync against a
    brand-new sheet copy never touches its tab at all). That tab is then
    left holding whatever the TEMPLATE happened to carry -- which can be
    real-looking, wrong data, not an obvious blank: the canonical
    template is periodically rebuilt from a real past week's live sheet
    (see CONTRIBUTING.md), so its raw source tabs can still hold that
    week's actual numbers.

    Called unconditionally before the first sync a new week runs, this
    blanks every tab a source in `source_names` is mapped to, so a
    failure during that sync leaves a genuinely empty tab instead of a
    stale-but-plausible one. `client.write_tab(tab, [])` is `ws.clear()`
    with nothing written back -- the same clear every source's own
    upload already does, just run for all of them up front rather than
    one at a time on success.
    """
    summary = []
    for name in source_names:
        tab = tab_mappings.get(name)
        if not tab:
            continue
        client.write_tab(tab, [], clear_first=True)
        summary.append(f"{tab}: cleared before first sync ({name})")
    return summary
