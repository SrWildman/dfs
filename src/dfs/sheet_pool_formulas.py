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

**Task 5.3 update:** each row also gets a narrow `Source` column (O --
genuinely blank on both sheets, a stray "Cash" label that had been typed
there by hand was cleared during the Task 2 design pass, so this reuses
that slot instead of inserting a new one) stating whether that row's
player came from ticking EdgeRaw or typing into Pool Picks. Deliberately
placed LEFT of the EdgeRaw-linked block (P onward) rather than inserted
into it, so `sheet_links.link_edge_columns`' own "is LINKED_EDGE_COLUMNS
already a contiguous run somewhere in the header" idempotency check is
completely unaffected -- confirmed by re-running `dfs doctor`/
`link-edge` after this shipped.

**Fix 2.10 update:** each block now sorts by Salary descending, not
alphabetically by name -- see `_union_array`'s own docstring for how
Salary rides alongside Name through both sources so `SORT` has something
to key on without a second lookup pass in `_name_formula` itself.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_pool_picks import FIRST_DATA_ROW as _PICKS_FIRST_DATA_ROW
from dfs.sheet_pool_picks import LAST_ROW as _PICKS_LAST_ROW
from dfs.sheet_pool_picks import POOL_PICKS_TAB
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

_NAME_COLUMN = "A"
_POSITION_COLUMN = "B"
_SOURCE_COLUMN = "O"
_SOURCE_HEADER = "Source"
_OVERFLOW_COLUMN = "Z"
_OVERFLOW_HEADER = "Overflow"
# Fix 2.11: surfaces EdgeRaw's own Pool value (blank/Cash/GPP/Both) on
# Player Pool too. Appended past Overflow (the tab's current last column)
# rather than inserted anywhere -- same append-only reasoning as every
# other addition documented in CLAUDE.md's central hazard section.
_POOL_TYPE_COLUMN = "AA"
_POOL_TYPE_HEADER = "Pool"

_EDGE_NAME_COL = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
_EDGE_POSITION_COL = column_letter(EDGE_COLUMNS.index("Position") + EDGE_DATA_OFFSET)
_EDGE_SALARY_COL = column_letter(EDGE_COLUMNS.index("Salary") + EDGE_DATA_OFFSET)
# 1-based position of Salary within EdgeRaw!$Name:$Salary, for the VLOOKUP
# Pool Picks' own rows need (that tab has no Salary column of its own --
# every Pool Picks name is guaranteed to already be in EdgeRaw, since its
# own dropdown only offers names from there).
_EDGE_SALARY_VLOOKUP_INDEX = EDGE_COLUMNS.index("Salary") - EDGE_COLUMNS.index("Name") + 1

# Pool Picks' own fixed layout (sheet_pool_picks.py, Fix 3.1's title row
# pushed this down by one from $A$2:$A$101): column A is the typed name,
# column B its looked-up position, rows 3-102. Derived from that module's
# own FIRST_DATA_ROW/LAST_ROW rather than retyped -- exactly the class of
# cross-tab position CLAUDE.md's central hazard warns never to hardcode.
_PICKS_NAME_RANGE = f"$A${_PICKS_FIRST_DATA_ROW}:$A${_PICKS_LAST_ROW}"
_PICKS_POSITION_RANGE = f"$B${_PICKS_FIRST_DATA_ROW}:$B${_PICKS_LAST_ROW}"


def _union_array(edge_tab: str, position: str) -> str:
    """Both name sources for `position`, stacked as one Sheets array
    literal, each row a (Name, Salary) pair -- shared by the Name formula
    and the overflow count so the two can never disagree about what's
    actually in the pool. Salary rides along so `_name_formula` (Fix 2.10)
    can sort by it without a second pass; `_overflow_formula` only ever
    counts the Name half.

    Pool Picks carries no Salary of its own, so its half looks Salary up
    against EdgeRaw by Name -- safe because that tab's own dropdown only
    offers names that are already in EdgeRaw, and IFERROR->0 degrades a
    typed name EdgeRaw doesn't currently carry (a bye week, a bad paste)
    to "sorts last" rather than breaking the whole block."""
    # Fix 2.11: Pool is a blank/Cash/GPP/Both dropdown now, not a TRUE/
    # FALSE checkbox -- any non-blank value means "in the pool" here.
    edge_filter = (
        f"FILTER({{{edge_tab}!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL},"
        f"{edge_tab}!${_EDGE_SALARY_COL}$2:${_EDGE_SALARY_COL}}},"
        f'{edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN}<>"",'
        f'{edge_tab}!${_EDGE_POSITION_COL}$2:${_EDGE_POSITION_COL}="{position}")'
    )
    picks_names = (
        f"FILTER('{POOL_PICKS_TAB}'!{_PICKS_NAME_RANGE},"
        f"'{POOL_PICKS_TAB}'!{_PICKS_NAME_RANGE}<>\"\","
        f"'{POOL_PICKS_TAB}'!{_PICKS_POSITION_RANGE}=\"{position}\")"
    )
    picks_filter = (
        f"{{{picks_names},IFERROR(VLOOKUP({picks_names},"
        f"{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_SALARY_COL},{_EDGE_SALARY_VLOOKUP_INDEX},FALSE),0)}}"
    )
    return f"{{{edge_filter};{picks_filter}}}"


def _name_formula(edge_tab: str, position: str, cap: int) -> str:
    # Fix 2.10: sorted by Salary (column 2 of the union array) descending,
    # not alphabetically. ARRAY_CONSTRAIN(...,cap,1) both applies the
    # position cap AND drops the Salary column back out -- Player Pool's
    # Name column only ever shows the name.
    union = _union_array(edge_tab, position)
    return f'=IFERROR(ARRAY_CONSTRAIN(SORT(UNIQUE({union}),2,FALSE),{cap},1),"")'


def _source_formula(edge_tab: str, row: int) -> str:
    """EdgeRaw wins the label if a player somehow ends up both ticked and
    typed (matching `_name_formula`'s own UNIQUE, which produces one row
    either way, not two) -- EdgeRaw's Pool dropdown is the primary
    mechanism, Pool Picks the secondary one. Any non-blank Pool value
    counts (Fix 2.11 -- blank/Cash/GPP/Both, not a TRUE/FALSE checkbox)."""
    name_cell = f"${_NAME_COLUMN}{row}"
    edge_check = (
        f'COUNTIFS({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},"<>",'
        f"{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_NAME_COL},{name_cell})"
    )
    picks_check = f"COUNTIF('{POOL_PICKS_TAB}'!{_PICKS_NAME_RANGE},{name_cell})"
    return f'=IF({name_cell}="","",IF({edge_check}>0,"EdgeRaw",IF({picks_check}>0,"Picks","")))'


def _pool_type_formula(edge_tab: str, row: int) -> str:
    """This row's EdgeRaw Pool value (blank/Cash/GPP/Both), by Name --
    INDEX/MATCH rather than VLOOKUP, since Pool (column A) sits to the
    LEFT of Name (column C) on EdgeRaw and plain VLOOKUP can only look
    rightward of its lookup column."""
    name_cell = f"${_NAME_COLUMN}{row}"
    match = f"MATCH({name_cell},{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_NAME_COL},0)"
    return f'=IF({name_cell}="","",IFERROR(INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},{match}),""))'


def _overflow_formula(edge_tab: str, position: str, cap: int) -> str:
    # COUNTA(INDEX(UNIQUE(...),0,1)), not two separate COUNTIFS added
    # together -- a player both ticked in EdgeRaw AND typed into Pool
    # Picks must count once, not twice, or this would warn about an
    # overflow that isn't real. INDEX(...,0,1) takes just the Name column
    # back out of the (Name, Salary) pairs Fix 2.10 added -- COUNTA over
    # both columns would double-count every real row. IFERROR guards the
    # case where FILTER finds nothing at all for this position (an empty
    # pool), which UNIQUE/COUNTA would otherwise propagate as an error
    # instead of 0.
    count = f"IFERROR(COUNTA(INDEX(UNIQUE({_union_array(edge_tab, position)}),0,1)),0)"
    return f'=IF({count}>{cap},{cap}&" {position} slots, "&{count}&" ticked -- some are hidden","")'


def write_pool_formulas(
    client: SheetsClient,
    *,
    player_pool_tab: str,
    edge_tab: str,
    name_blocks: list[tuple[int, int]] = PLAYER_POOL_NAME_BLOCKS,
) -> list[str]:
    """Write the SORT/FILTER/ARRAY_CONSTRAIN Name formula, a per-row
    Source label, and an overflow warning into each block, keyed off
    EdgeRaw's Pool tick column. Only ever touches column A (Name), column
    O (Source) and column Z (Overflow) via `update_range` -- never
    `write_tab` -- so the VLOOKUP columns P..Y already linked by
    `link_edge_columns` are never at risk, matching every other
    presentation primitive's "only touches the range it's given" contract.

    Always fully rewritten (idempotent, safe to rerun) rather than
    gated on "already formula-driven" -- see docs/HANDOFF.md's lesson #4
    on why an early-return-on-no-op is the wrong default here.
    """
    summary = []
    client.update_range(player_pool_tab, f"{_SOURCE_COLUMN}1", [[_SOURCE_HEADER]])
    client.update_range(player_pool_tab, f"{_OVERFLOW_COLUMN}1", [[_OVERFLOW_HEADER]])
    client.update_range(player_pool_tab, f"{_POOL_TYPE_COLUMN}1", [[_POOL_TYPE_HEADER]])

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

        source_rows = [[_source_formula(edge_tab, row)] for row in range(start, end + 1)]
        client.update_range(player_pool_tab, f"{_SOURCE_COLUMN}{start}:{_SOURCE_COLUMN}{end}", source_rows)

        pool_type_rows = [[_pool_type_formula(edge_tab, row)] for row in range(start, end + 1)]
        client.update_range(
            player_pool_tab, f"{_POOL_TYPE_COLUMN}{start}:{_POOL_TYPE_COLUMN}{end}", pool_type_rows
        )

        summary.append(f"{player_pool_tab}!A{start}: {position} block ({cap} slots) now formula-driven")

    return summary
