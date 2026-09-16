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
sources -- EdgeRaw's Pool tick (as above) AND (A3 update, see below)
Player Pool's own "add a player" control cell, deduped by `UNIQUE` so a
player who ends up both ticked and typed produces exactly one row, not
two. A player typed into the control cell who isn't ticked in EdgeRaw
still counts against the block's cap and can still trigger the overflow
warning below -- `_overflow_formula` counts both sources for the same
reason silently dropping a manually-added player past the cap would be
the worst failure mode here.

**A3 update:** the second union source used to be `Pool Picks`, a
separate 100-row typed tab (`sheet_pool_picks.py`, removed). Sam never
wanted a second tab for this, and Player Pool's Name column being a
formula meant there was nowhere on Player Pool itself to type a name --
until `sheet_pool_control.py`'s new row 1 control cell (`$B$1`) gave it
one. That cell holds at most one typed name at a time (not Pool Picks'
100 rows); its Position is looked up on the fly via VLOOKUP against
EdgeRaw (Pool Picks stored a Position column of its own -- the control
cell doesn't need one, since a single cell's position is one lookup, not
a column of them). A real per-row "drop this player from the pool"
checkbox was also asked for (A3.2) but turned out to require either a
circular formula (a checkbox on the same computed rows it would need to
exclude) or a second typed list Sam explicitly didn't want -- dropped,
documented in CONTRIBUTING.md; removing a player is still done in
EdgeRaw, one click away via this tab's new "Edge ↗" column.

**Task 5.3 update:** each row also gets a narrow `Source` column stating
whether that row's player came from ticking EdgeRaw or typing into the
add-a-player control cell. `Source`/`Overflow`/`Pool`'s column letters are found by header
name (`sheet_columns.PLAYER_POOL_COLUMN_ORDER` places `Source` right
after `Venue` and appends `Overflow`/`Pool` at the tab's very end), never
hardcoded -- this used to hardcode `Source`=O/`Overflow`=Z/`Pool`=AA,
true only under the pre-Phase-3 append-only layout; Phase 3's reorder
moved real EdgeRaw-linked columns (Flag/Roof/Wind) onto those exact
letters, so a hardcoded version of this module would silently clobber
them the same way `sheet_style.polish_guardrails` once did. See
CONTRIBUTING.md's Phase 3 changelog entry.

**Fix 2.10 update:** each block now sorts by Salary descending, not
alphabetically by name -- see `_union_array`'s own docstring for how
Salary rides alongside Name through both sources so `SORT` has something
to key on without a second lookup pass in `_name_formula` itself.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW, PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

_NAME_COLUMN = "A"
_POSITION_COLUMN = "B"
_SOURCE_HEADER = "Source"
_OVERFLOW_HEADER = "Overflow"
# Fix 2.11: surfaces EdgeRaw's own Pool value (blank/Cash/GPP/Both) on
# Player Pool too.
_POOL_TYPE_HEADER = "Pool"

_EDGE_NAME_COL = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
_EDGE_POSITION_COL = column_letter(EDGE_COLUMNS.index("Position") + EDGE_DATA_OFFSET)
_EDGE_SALARY_COL = column_letter(EDGE_COLUMNS.index("Salary") + EDGE_DATA_OFFSET)
# 1-based position of Salary/Position within EdgeRaw!$Name:$Salary, for
# the VLOOKUPs the control cell's single typed name needs (it has no
# Salary or Position column of its own, unlike a real EdgeRaw row).
_EDGE_SALARY_VLOOKUP_INDEX = EDGE_COLUMNS.index("Salary") - EDGE_COLUMNS.index("Name") + 1
_EDGE_POSITION_VLOOKUP_INDEX = EDGE_COLUMNS.index("Position") - EDGE_COLUMNS.index("Name") + 1

# A3: the add-a-player control cell (sheet_pool_control.py) -- same tab,
# so no tab-prefix needed. Holds at most one typed name at a time.
_CONTROL_CELL = f"$B${PLAYER_POOL_CONTROL_ROW}"


def _union_array(edge_tab: str, position: str) -> str:
    """Both name sources for `position`, stacked as one Sheets array
    literal, each row a (Name, Salary) pair -- shared by the Name formula
    and the overflow count so the two can never disagree about what's
    actually in the pool. Salary rides along so `_name_formula` (Fix 2.10)
    can sort by it without a second pass; `_overflow_formula` only ever
    counts the Name half.

    The control cell carries no Salary or Position of its own, so its
    half looks both up against EdgeRaw by Name -- safe because the
    cell's own dropdown only offers names that are already in EdgeRaw,
    and IFERROR degrades a typed name EdgeRaw doesn't currently carry (a
    bye week, a stale add) to "doesn't match this position" / "sorts
    last" rather than breaking the whole block."""
    # Fix 2.11: Pool is a blank/Cash/GPP/Both dropdown now, not a TRUE/
    # FALSE checkbox -- any non-blank value means "in the pool" here.
    edge_filter = (
        f"FILTER({{{edge_tab}!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL},"
        f"{edge_tab}!${_EDGE_SALARY_COL}$2:${_EDGE_SALARY_COL}}},"
        f'{edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN}<>"",'
        f'{edge_tab}!${_EDGE_POSITION_COL}$2:${_EDGE_POSITION_COL}="{position}")'
    )
    control_position = (
        f"IFERROR(VLOOKUP({_CONTROL_CELL},{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_POSITION_COL},"
        f'{_EDGE_POSITION_VLOOKUP_INDEX},FALSE),"")'
    )
    control_names = (
        f'FILTER({_CONTROL_CELL}:{_CONTROL_CELL},{_CONTROL_CELL}<>"",{control_position}="{position}")'
    )
    control_filter = (
        f"{{{control_names},IFERROR(VLOOKUP({control_names},"
        f"{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_SALARY_COL},{_EDGE_SALARY_VLOOKUP_INDEX},FALSE),0)}}"
    )
    return f"{{{edge_filter};{control_filter}}}"


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
    mechanism, the add-a-player control cell the secondary one. Any
    non-blank Pool value counts (Fix 2.11 -- blank/Cash/GPP/Both, not a
    TRUE/FALSE checkbox)."""
    name_cell = f"${_NAME_COLUMN}{row}"
    edge_check = (
        f'COUNTIFS({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},"<>",'
        f"{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_NAME_COL},{name_cell})"
    )
    return f'=IF({name_cell}="","",IF({edge_check}>0,"EdgeRaw",IF({name_cell}={_CONTROL_CELL},"Added","")))'


def _pool_type_formula(edge_tab: str, row: int) -> str:
    """This row's EdgeRaw Pool value (blank/Cash/GPP/Both), by Name --
    INDEX/MATCH rather than VLOOKUP, since Pool (column A) sits to the
    LEFT of Name on EdgeRaw and plain VLOOKUP can only look rightward of
    its lookup column."""
    name_cell = f"${_NAME_COLUMN}{row}"
    match = f"MATCH({name_cell},{edge_tab}!${_EDGE_NAME_COL}:${_EDGE_NAME_COL},0)"
    return f'=IF({name_cell}="","",IFERROR(INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},{match}),""))'


def _overflow_formula(edge_tab: str, position: str, cap: int) -> str:
    # COUNTA(INDEX(UNIQUE(...),0,1)), not two separate COUNTIFS added
    # together -- a player both ticked in EdgeRaw AND typed into the
    # add-a-player cell must count once, not twice, or this would warn
    # about an overflow that isn't real. INDEX(...,0,1) takes just the Name column
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
    header_row: int = PLAYER_POOL_HEADER_ROW,
) -> list[str]:
    """Write the SORT/FILTER/ARRAY_CONSTRAIN Name formula, a per-row
    Source label, and an overflow warning into each block, keyed off
    EdgeRaw's Pool tick column. `Source`/`Overflow`/`Pool`'s columns are
    found by header name (never hardcoded -- see the module docstring),
    so this only ever touches column A (Name) and those three, never the
    EdgeRaw-linked columns `link_edge_columns` owns.

    `header_row` defaults to `PLAYER_POOL_HEADER_ROW` (A3 moved Player
    Pool's real header from row 1 to row 2 to make room for the
    add-a-player control row above it).

    Always fully rewritten (idempotent, safe to rerun) rather than
    gated on "already formula-driven" -- see docs/HANDOFF.md's lesson #4
    on why an early-return-on-no-op is the wrong default here.
    """
    header_rows = client.read_range(player_pool_tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    missing = [name for name in (_SOURCE_HEADER, _OVERFLOW_HEADER, _POOL_TYPE_HEADER) if name not in header]
    if missing:
        raise ValueError(
            f"{player_pool_tab!r} header is missing column(s) {missing} -- "
            "run `dfs setup reorder-columns` first"
        )
    source_col = column_letter(header.index(_SOURCE_HEADER))
    overflow_col = column_letter(header.index(_OVERFLOW_HEADER))
    pool_type_col = column_letter(header.index(_POOL_TYPE_HEADER))

    summary = []
    client.update_range(player_pool_tab, f"{source_col}{header_row}", [[_SOURCE_HEADER]])
    client.update_range(player_pool_tab, f"{overflow_col}{header_row}", [[_OVERFLOW_HEADER]])
    client.update_range(player_pool_tab, f"{pool_type_col}{header_row}", [[_POOL_TYPE_HEADER]])

    for start, end in name_blocks:
        cap = end - start + 1
        position_cell = client.read_range(player_pool_tab, f"{_POSITION_COLUMN}{start}")
        position = position_cell[0][0].strip() if position_cell and position_cell[0] else ""
        if not position:
            cell = f"{_POSITION_COLUMN}{start}"
            summary.append(f"{player_pool_tab}!A{start}: skipped, no position label in {cell}")
            continue

        name_cell = f"{_NAME_COLUMN}{start}"
        overflow_cell = f"{overflow_col}{start}"
        client.update_range(player_pool_tab, name_cell, [[_name_formula(edge_tab, position, cap)]])
        client.update_range(player_pool_tab, overflow_cell, [[_overflow_formula(edge_tab, position, cap)]])

        source_rows = [[_source_formula(edge_tab, row)] for row in range(start, end + 1)]
        client.update_range(player_pool_tab, f"{source_col}{start}:{source_col}{end}", source_rows)

        pool_type_rows = [[_pool_type_formula(edge_tab, row)] for row in range(start, end + 1)]
        client.update_range(player_pool_tab, f"{pool_type_col}{start}:{pool_type_col}{end}", pool_type_rows)

        summary.append(f"{player_pool_tab}!A{start}: {position} block ({cap} slots) now formula-driven")

    return summary
