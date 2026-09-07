"""Task K 4.3 -- makes Player Pool's five position blocks formula-driven off
EdgeRaw's Pool tick column, so Sam ticks a player once in EdgeRaw instead of
retyping the name into Player Pool by hand.

Player Pool's Name column (A) is the only typed column in each block --
everything else (Team, DK Sal, Pts, ...) is already a VLOOKUP off it (see
`sheet_links.link_edge_columns` and docs/SHEET_REFERENCE.md's "canonical
column order" section). This module replaces that one typed cell per row
with a single spilling array formula per block, keyed on EdgeRaw's own
Position column rather than a hardcoded QB/RB/WR/TE/DST order -- Position
is read directly off column B of each block's first row (verified via a
live template read to be a static per-row label, independent of Name)
rather than assumed, so a future reordering of the blocks can't silently
mismatch a block to the wrong position the way a hardcoded list could.

**Trade-off, must be confirmed before this ships (see docs/HANDOFF.md
4.5):** once applied, Player Pool's Name column stops being freely
typeable -- every player must be ticked in EdgeRaw first.

**Task 4.3 update:** each block's Name formula is now the UNION of two
sources -- EdgeRaw's Pool tick (as above) AND `Pool Picks`' typed rows
(`sheet_pool_picks.py`) for the same position, deduped by `UNIQUE` so a
player who ends up both ticked and typed produces exactly one row, not
two. A player typed into Pool Picks who isn't ticked in EdgeRaw still
counts against the block's cap and can still trigger the overflow
warning below -- `_overflow_formula` counts both sources for the same
reason silently dropping a Pool-Picks player past the cap would be the
worst failure mode here.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_pool_picks import POOL_PICKS_TAB
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

_NAME_COLUMN = "A"
_POSITION_COLUMN = "B"
_OVERFLOW_COLUMN = "Z"
_OVERFLOW_HEADER = "Overflow"

_EDGE_NAME_COL = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
_EDGE_POSITION_COL = column_letter(EDGE_COLUMNS.index("Position") + EDGE_DATA_OFFSET)

# Pool Picks' own fixed layout (sheet_pool_picks.py): column A is the
# typed name, column B its looked-up position, rows 2-101.
_PICKS_NAME_RANGE = "$A$2:$A$101"
_PICKS_POSITION_RANGE = "$B$2:$B$101"


def _union_array(edge_tab: str, position: str) -> str:
    """Both name sources for `position`, stacked as one Sheets array
    literal -- shared by the Name formula and the overflow count so the
    two can never disagree about what's actually in the pool."""
    edge_filter = (
        f"FILTER({edge_tab}!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL},"
        f"{edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN}=TRUE,"
        f'{edge_tab}!${_EDGE_POSITION_COL}$2:${_EDGE_POSITION_COL}="{position}")'
    )
    picks_filter = (
        f"FILTER('{POOL_PICKS_TAB}'!{_PICKS_NAME_RANGE},"
        f"'{POOL_PICKS_TAB}'!{_PICKS_NAME_RANGE}<>\"\","
        f"'{POOL_PICKS_TAB}'!{_PICKS_POSITION_RANGE}=\"{position}\")"
    )
    return f"{{{edge_filter};{picks_filter}}}"


def _name_formula(edge_tab: str, position: str, cap: int) -> str:
    union = _union_array(edge_tab, position)
    return f'=IFERROR(ARRAY_CONSTRAIN(SORT(UNIQUE({union}),1,TRUE),{cap},1),"")'


def _overflow_formula(edge_tab: str, position: str, cap: int) -> str:
    # COUNTA(UNIQUE(...)), not two separate COUNTIFS added together --
    # a player both ticked in EdgeRaw AND typed into Pool Picks must
    # count once, not twice, or this would warn about an overflow that
    # isn't real. IFERROR guards the case where FILTER finds nothing at
    # all for this position (an empty pool), which UNIQUE/COUNTA would
    # otherwise propagate as an error instead of 0.
    count = f"IFERROR(COUNTA(UNIQUE({_union_array(edge_tab, position)})),0)"
    return f'=IF({count}>{cap},{cap}&" {position} slots, "&{count}&" ticked -- some are hidden","")'


def write_pool_formulas(
    client: SheetsClient,
    *,
    player_pool_tab: str,
    edge_tab: str,
    name_blocks: list[tuple[int, int]] = PLAYER_POOL_NAME_BLOCKS,
) -> list[str]:
    """Write the SORT/FILTER/ARRAY_CONSTRAIN Name formula and an overflow
    warning into each block, keyed off EdgeRaw's Pool tick column. Only
    ever touches column A (Name) and column Z (Overflow) via `update_range`
    -- never `write_tab` -- so the VLOOKUP columns B..Y already linked by
    `link_edge_columns` are never at risk, matching every other
    presentation primitive's "only touches the range it's given" contract.

    Always fully rewritten (idempotent, safe to rerun) rather than
    gated on "already formula-driven" -- see docs/HANDOFF.md's lesson #4
    on why an early-return-on-no-op is the wrong default here.
    """
    summary = []
    client.update_range(player_pool_tab, f"{_OVERFLOW_COLUMN}1", [[_OVERFLOW_HEADER]])

    for start, end in name_blocks:
        cap = end - start + 1
        position_cell = client.read_range(player_pool_tab, f"{_POSITION_COLUMN}{start}")
        position = position_cell[0][0].strip() if position_cell and position_cell[0] else ""
        if not position:
            cell = f"{_POSITION_COLUMN}{start}"
            summary.append(f"{player_pool_tab}!A{start}: skipped, no position label in {cell}")
            continue

        name_cell = f"{_NAME_COLUMN}{start}"
        overflow_cell = f"{_OVERFLOW_COLUMN}{start}"
        client.update_range(player_pool_tab, name_cell, [[_name_formula(edge_tab, position, cap)]])
        client.update_range(player_pool_tab, overflow_cell, [[_overflow_formula(edge_tab, position, cap)]])
        summary.append(f"{player_pool_tab}!A{start}: {position} block ({cap} slots) now formula-driven")

    return summary
