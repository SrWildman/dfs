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
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

_NAME_COLUMN = "A"
_POSITION_COLUMN = "B"
_OVERFLOW_COLUMN = "Z"
_OVERFLOW_HEADER = "Overflow"

_EDGE_NAME_COL = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
_EDGE_POSITION_COL = column_letter(EDGE_COLUMNS.index("Position") + EDGE_DATA_OFFSET)


def _name_formula(edge_tab: str, position: str, cap: int) -> str:
    return (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({edge_tab}!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL},"
        f"{edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN}=TRUE,{edge_tab}!${_EDGE_POSITION_COL}$2:"
        f'${_EDGE_POSITION_COL}="{position}"),1,TRUE),{cap},1),"")'
    )


def _overflow_formula(edge_tab: str, position: str, cap: int) -> str:
    count = (
        f"COUNTIFS({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},TRUE,"
        f'{edge_tab}!${_EDGE_POSITION_COL}:${_EDGE_POSITION_COL},"{position}")'
    )
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
