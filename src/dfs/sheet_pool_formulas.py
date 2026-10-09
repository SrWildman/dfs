"""Task K 4.3 -- makes Player Pool's five position blocks formula-driven off
EdgeRaw's Pool tick column, so Sam ticks a player once in EdgeRaw instead of
retyping the name into Player Pool by hand.

Player Pool's Name column is the only typed column in each block --
everything else (Team, DK Sal, Pts, ...) is already a VLOOKUP off it (see
`sheet_links.link_edge_columns` and docs/SHEET_REFERENCE.md's "canonical
column order" section). This module replaces that one typed cell per row
with a single spilling array formula per block, keyed on EdgeRaw's own
Position column rather than a hardcoded QB/RB/WR/TE/DST order -- Position
is read directly off the `Pos.` column of each block's first row (a static
per-row label, independent of Name) rather than assumed, so a future
reordering of the blocks can't silently mismatch a block to the wrong
position the way a hardcoded list could.

Once applied, Player Pool's Name column is not freely typeable -- every
player must be in EdgeRaw's `Pool` first. Three ways to get him there, all
of them setting EdgeRaw's `Pool` (this module reads nothing else): tick it on
EdgeRaw, pick a value in any tab's `Pool` dropdown (the bound Apps Script
writes EdgeRaw), or use Player Pool's add-a-player box (`sheet_pool_control.py`).
The box used to feed a second union source and a hidden `Added` list; both are
retired (actions round, 2026-10-09), so the union below is EdgeRaw's ticks alone.

Column letters are found by header text, never hardcoded (`Name`, `Pos.`, `Id`,
`Overflow`, `Pool`): Pool sits left of Name, so Name is no longer column A.

**Fix 2.10 update:** each block now sorts by Salary descending, not
alphabetically by name -- see `_union_array`'s own docstring for how
Salary rides alongside Name through both sources so `SORT` has something
to key on without a second lookup pass in `_name_formula` itself.

**Part 7.10 update (2026-09-18), Sam: "The pool should order players by
position by salary high to low, but grouped by Both, Cash, GPP."** A
third helper column joins Name/Salary through both union sources: each
row's Pool tag, turned into a rank via `MATCH` against
`sources.edge.POOL_TYPE_SORT_ORDER` (never hand-written into the formula
string -- see that constant's own docstring for why it's a SEPARATE list
from the dropdown's own `POOL_TYPE_OPTIONS` order). `_name_formula`'s
`SORT` becomes two keys (tag rank ascending, then Salary descending);
`ARRAY_CONSTRAIN(...,cap,1)` still drops every helper column back out,
unchanged, regardless of how many there now are.

`MATCH` does not broadcast elementwise against a multi-cell range on its
own in Sheets (`{range, MATCH(range, {...}, 0)}` resolves to `#REF!`,
confirmed empirically on the template's Scratch tab before writing this) --
it only works when it's ITSELF one of `FILTER`'s own array arguments,
which is why the tag-rank column lives inside `_union_array`'s existing
`FILTER(...)` call rather than being computed separately and joined on
after. The control cell's own tag lookup needs no such handling: it
reads one cell, not a range, so plain `MATCH` on a scalar works
everywhere, no `FILTER` needed.
"""

from __future__ import annotations

from dfs import sheet_pool_cells as pc
from dfs.derived import edge_sheet_letter
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN, POOL_TYPE_SORT_ORDER
from dfs.weekly_reset import PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

_NAME_HEADER = "Name"
_POSITION_HEADER = "Pos."
_ID_HEADER = "Id"
_OVERFLOW_HEADER = "Overflow"
# Fix 2.11: surfaces EdgeRaw's own Pool value (blank/Cash/GPP/Both) on
# Player Pool too.
_POOL_TYPE_HEADER = "Pool"

_EDGE_NAME_COL = edge_sheet_letter("Name")
_EDGE_POSITION_COL = edge_sheet_letter("Position")
_EDGE_SALARY_COL = edge_sheet_letter("Salary")
# Part 7.10: `{"Both","Cash","GPP"}`, generated from the one Python list
# that decides the order -- never hand-written into a formula string, so
# renaming/reordering a tag only ever means editing POOL_TYPE_SORT_ORDER.
_TAG_RANK_ARRAY = "{" + ",".join(f'"{tag}"' for tag in POOL_TYPE_SORT_ORDER) + "}"
# A typed name whose EdgeRaw Pool tag doesn't match any of
# POOL_TYPE_SORT_ORDER (blank, or genuinely absent from EdgeRaw) sorts
# after every real tag, not into an arbitrary position.
_UNKNOWN_TAG_RANK = len(POOL_TYPE_SORT_ORDER) + 1


def _union_array(edge_tab: str, position: str) -> str:
    """Every pooled player at `position`, as one array, each row a (Name, Salary, TagRank) triple:
    EdgeRaw's own Pool ticks, the only source since the actions round retired the add-a-player box's own
    contribution (the box now sets EdgeRaw's `Pool` directly, `sheet_pool_control.py`). Salary/TagRank ride
    along so `_name_formula` can sort by them without a second pass; `_overflow_formula` only ever counts the
    Name column.

    `FILTER` raises `#N/A` when nothing matches (this codebase's well-known "FILTER of nothing" failure
    mode, see CONTRIBUTING.md), so the filter is `IFERROR`-guarded to a same-shaped blank placeholder row
    (`{"",0,0}`). Callers (`UNIQUE` then either grouping or counting) must treat a blank-Name row as
    "nothing here", never a real entry; see `_grouped_with_separators_formula`'s `uArr` step and
    `_overflow_formula`'s `SUMPRODUCT`.

    Pool is a blank/Cash/GPP/Both dropdown, so any non-blank value means "in the pool"; every row this
    FILTER keeps therefore has a tag in `POOL_TYPE_SORT_ORDER`, and `MATCH` cannot fail. `MATCH` only
    broadcasts elementwise against a real range because it sits inside FILTER's own array argument (see this
    module's docstring for why a bare `{range, MATCH(range, ...)}` does not work)."""
    placeholder = '{"",0,0}'
    edge_filter = (
        f"FILTER({{{edge_tab}!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL},"
        f"{edge_tab}!${_EDGE_SALARY_COL}$2:${_EDGE_SALARY_COL},"
        f"MATCH({edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN},{_TAG_RANK_ARRAY},0)}},"
        f'{edge_tab}!${POOL_COLUMN}$2:${POOL_COLUMN}<>"",'
        f'{edge_tab}!${_EDGE_POSITION_COL}$2:${_EDGE_POSITION_COL}="{position}")'
    )
    return f"IFERROR({edge_filter},{placeholder})"


def _grouped_with_separators_formula(union: str) -> str:
    """Week 3 fixes, Fix 3 (A7, 2026-09-23). Sam: "I like the blank line
    between cash/gpp/both blocks in the pool, but it doesn't seem to
    consistently work." Root cause: there was no separator logic
    anywhere in this file -- what he saw was an artifact of how `SORT`
    happened to lay out ties, not deliberate. This emits exactly one
    blank row between each pair of ADJACENT NON-EMPTY tag groups (Both/
    Cash/GPP, `sources.edge.POOL_TYPE_SORT_ORDER`'s order) -- skipping
    the separator entirely next to an empty group, so (for example) an
    empty "Both" group this week never leaves a stray leading blank row
    before Cash's names. Still purely inside the array -- never a real
    inserted row, so `PLAYER_POOL_NAME_BLOCKS`' fixed ranges don't move.
    Each separator still consumes one row of the block's own capacity;
    `_name_formula`'s `ARRAY_CONSTRAIN` below applies the same `cap` it
    always did, now against this pre-grouped array.

    Verified empirically on the template's own throwaway scratch tab
    before shipping (this codebase's standing discipline for non-obvious
    array-formula behavior -- see CONTRIBUTING.md), which surfaced two
    findings not obvious from Sheets' own docs: `IFS`, and a single
    `IF` used as one argument of another function, do NOT reliably
    return a spilled multi-row array result (silently `#VALUE!`) the way
    a bare array does, or the way a plain `IF` used as a top-level
    formula result does -- every branch below is therefore a nested
    plain `IF`, never `IFS`. And a `LET` variable name that happens to
    read as a cell reference (e.g. `g1`, which collides with cell G1)
    resolves to `#NAME?` even though it's syntactically a normal
    identifier -- every name below is deliberately not cell-shaped.

    Generalized only as far as Sam's actual 3-tag order needs (3 nested
    `IF`s, one per tag's presence) -- if `POOL_TYPE_SORT_ORDER` ever
    grows past 3 tags, this needs rewriting, not reusing as-is (guarded
    below, not silently mismatched).

    A 4th, unlabeled catch-all group (`grpOther`) holds any row whose tag
    rank ISN'T one of the 3 known tags -- `_UNKNOWN_TAG_RANK`, which
    `_union_array`'s control-cell/added-names sources fall back to for a
    typed name EdgeRaw can't currently match a real Pool tag for (a bye
    week, a stale add typed before EdgeRaw's own tick). The original flat
    `SORT(UNIQUE(union),3,TRUE,2,FALSE)` included these rows too (sorted
    last); a first version of this rewrite silently dropped them by only
    ever filtering for tag ranks 1-3, found and fixed before shipping.
    `grpOther` is appended after the three known groups, with its own
    leading separator only when at least one known group has content
    (skipping the same leading-blank problem the 3 known groups already
    avoid).

    `rawArr,UNIQUE(union)` can now include `_union_array`'s own blank
    (`{"",0,0}`) placeholder rows for a source that had nothing (see that
    function's docstring) -- `uArr` filters those back out before any
    grouping happens, so a placeholder never gets miscategorized as a
    real "unknown tag" row in `grpOther`. Falls back to the same
    placeholder, not an error, when NOTHING real survives that filter --
    the has-checks below already treat that as "empty"."""
    if len(POOL_TYPE_SORT_ORDER) != 3:
        raise ValueError(
            "_grouped_with_separators_formula's separator logic hardcodes exactly 3 pool "
            f"tags; POOL_TYPE_SORT_ORDER now has {len(POOL_TYPE_SORT_ORDER)} -- needs "
            "rewriting for the new count, not reusing as-is."
        )
    sep = '{"",0,0}'
    return (
        "LET("
        f"rawArr,UNIQUE({union}),"
        f'uArr,IFERROR(FILTER(rawArr,INDEX(rawArr,0,1)<>""),{sep}),'
        f"grpOne,IFERROR(SORT(FILTER(uArr,INDEX(uArr,0,3)=1),2,FALSE),{sep}),"
        f"grpTwo,IFERROR(SORT(FILTER(uArr,INDEX(uArr,0,3)=2),2,FALSE),{sep}),"
        f"grpThree,IFERROR(SORT(FILTER(uArr,INDEX(uArr,0,3)=3),2,FALSE),{sep}),"
        "grpOther,IFERROR(SORT(FILTER(uArr,INDEX(uArr,0,3)<>1,INDEX(uArr,0,3)<>2,"
        f"INDEX(uArr,0,3)<>3),2,FALSE),{sep}),"
        'hasOne,INDEX(grpOne,1,1)<>"",'
        'hasTwo,INDEX(grpTwo,1,1)<>"",'
        'hasThree,INDEX(grpThree,1,1)<>"",'
        'hasOther,INDEX(grpOther,1,1)<>"",'
        f"sepRow,{sep},"
        "known,IF(hasOne,"
        "IF(hasTwo,IF(hasThree,{grpOne;sepRow;grpTwo;sepRow;grpThree},{grpOne;sepRow;grpTwo}),"
        "IF(hasThree,{grpOne;sepRow;grpThree},grpOne)),"
        "IF(hasTwo,IF(hasThree,{grpTwo;sepRow;grpThree},grpTwo),IF(hasThree,grpThree,sepRow))"
        "),"
        "hasKnown,OR(hasOne,hasTwo,hasThree),"
        "IF(hasOther,IF(hasKnown,{known;sepRow;grpOther},grpOther),known)"
        ")"
    )


def _name_formula(edge_tab: str, position: str, cap: int) -> str:
    # Fix 2.10 + Part 7.10 + Fix 3 (A7): grouped by TagRank (Both, then
    # Cash, then GPP), Salary descending within each group, one blank
    # row between adjacent non-empty groups (see
    # `_grouped_with_separators_formula`). ARRAY_CONSTRAIN(...,cap,1)
    # both applies the position cap AND drops every helper column back
    # out -- Player Pool's Name column only ever shows the name (or a
    # blank, for a separator row).
    union = _union_array(edge_tab, position)
    grouped = _grouped_with_separators_formula(union)
    return f'=IFERROR(ARRAY_CONSTRAIN({grouped},{cap},1),"")'


def _pool_type_formula(edge_tab: str, row: int, *, id_col: str) -> str:
    """This row's `Pool` cell: EdgeRaw's Pool value found by the `Id` in this row (never by name, so it stays
    right when the Name spill reflows). The same formula as every other tab's `Pool` control; the bound Apps
    Script puts it back after an edit (`sheet_pool_cells.pool_formula`)."""
    return pc.pool_formula(row, edge_tab, id_col=id_col)


def _overflow_formula(edge_tab: str, position: str, cap: int) -> str:
    # SUMPRODUCT((name column<>"")*1), not COUNTA -- a player both ticked
    # in EdgeRaw AND typed into the add-a-player cell must count once,
    # not twice, or this would warn about an overflow that isn't real.
    # INDEX(...,0,1) takes just the Name column back out of the (Name,
    # Salary, TagRank) triples Fix 2.10/Part 7.10 added -- counting over
    # every column would double- (or triple-) count every real row, and
    # this stays correct regardless of how many helper columns
    # `_union_array` carries, since column 1 is always Name.
    #
    # COUNTA specifically (not SUMPRODUCT) is this codebase's own "FILTER
    # of nothing" trap (see CONTRIBUTING.md's Board changelog entry):
    # `_union_array`'s own per-source IFERROR guards (Fix 3, 2026-09-23)
    # mean an empty position's union can still resolve to a formula-
    # produced blank ("") placeholder row rather than a genuine error --
    # and COUNTA counts a formula's own "" result as present, same as it
    # did for Board's empty-state guards. SUMPRODUCT never touches
    # FILTER's own error path so it never has that problem, and correctly
    # reads a placeholder-only union as 0. The outer IFERROR is kept
    # anyway as cheap insurance, not because it's still load-bearing.
    count = f'IFERROR(SUMPRODUCT((INDEX(UNIQUE({_union_array(edge_tab, position)}),0,1)<>"")*1),0)'
    # PROMPT_BOARD_FIXES.md item 8 (2026-09-25): the old check compared
    # PLAYER count against the ROW cap, ignoring that `_grouped_with_
    # separators_formula` also spends up to 2 of those same rows on blank
    # separators (Both/Cash/GPP). 10 QBs split across all three groups
    # need 12 rows in a 10-row block -- 2 are silently cut off by
    # `_name_formula`'s own ARRAY_CONSTRAIN, yet 10 is not greater than
    # 10, so no warning ever fired. Fixed by comparing the cap against
    # `ROWS(...)` of the SAME grouped-with-separators array `_name_
    # formula` actually constrains -- the true row count that array needs
    # (players plus whatever separators this week's own group mix
    # requires), not a recomputation of the separator count that could
    # drift from the real grouping logic.
    grouped = _grouped_with_separators_formula(_union_array(edge_tab, position))
    needed_rows = f"ROWS({grouped})"
    return f'=IF({needed_rows}>{cap},{cap}&" {position} slots, "&{count}&" ticked -- some are hidden","")'


def write_pool_formulas(
    client: SheetsClient,
    *,
    player_pool_tab: str,
    edge_tab: str,
    name_blocks: list[tuple[int, int]] = PLAYER_POOL_NAME_BLOCKS,
    header_row: int = PLAYER_POOL_HEADER_ROW,
) -> list[str]:
    """Write the SORT/FILTER/ARRAY_CONSTRAIN Name formula, the overflow warning and the `Pool` control
    formulas into each block, keyed off EdgeRaw's Pool tick column. `Name`, `Pos.`, `Id`, `Overflow` and
    `Pool` are found by header name (never hardcoded -- see the module docstring), so this only ever touches
    those columns, never the EdgeRaw-linked columns `link_edge_columns` owns.

    `header_row` defaults to `PLAYER_POOL_HEADER_ROW` (A3 moved Player Pool's real header from row 1 to
    row 2 to make room for the add-a-player control row above it).

    Always fully rewritten (idempotent, safe to rerun) rather than gated on "already formula-driven" -- see
    docs/planning/archive/HANDOFF.md's lesson #4 on why an early-return-on-no-op is the wrong default here.
    """
    header_rows = client.read_range(player_pool_tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    needed = (_NAME_HEADER, _POSITION_HEADER, _ID_HEADER, _OVERFLOW_HEADER, pc.POOL_HEADER)
    missing = [name for name in needed if name not in header]
    if missing:
        raise ValueError(
            f"{player_pool_tab!r} header is missing column(s) {missing} -- "
            "run `dfs setup reorder-columns` first"
        )
    name_col = column_letter(header.index(_NAME_HEADER))
    position_col = column_letter(header.index(_POSITION_HEADER))
    id_col = column_letter(header.index(_ID_HEADER))
    overflow_col = column_letter(header.index(_OVERFLOW_HEADER))
    pool_col = column_letter(header.index(pc.POOL_HEADER))

    summary = []
    client.update_range(player_pool_tab, f"{overflow_col}{header_row}", [[_OVERFLOW_HEADER]])
    client.update_range(player_pool_tab, f"{pool_col}{header_row}", [[pc.POOL_HEADER]])

    for start, end in name_blocks:
        cap = end - start + 1
        position_cell = client.read_range(player_pool_tab, f"{position_col}{start}")
        position = position_cell[0][0].strip() if position_cell and position_cell[0] else ""
        if not position:
            cell = f"{position_col}{start}"
            summary.append(f"{player_pool_tab}!{name_col}{start}: skipped, no position label in {cell}")
            continue

        name_cell = f"{name_col}{start}"
        overflow_cell = f"{overflow_col}{start}"
        # The block's name formula SPILLS down its own slots, so every cell below the
        # start must hold nothing typed. A stray formula left there by an older block
        # layout (found live: leftover name formulas at rows 37 and 63 of the template's
        # RB and WR blocks) shows the same players a second time.
        if end > start:
            client.clear_ranges(player_pool_tab, [f"{name_col}{start + 1}:{name_col}{end}"])
        client.update_range(player_pool_tab, name_cell, [[_name_formula(edge_tab, position, cap)]])
        client.update_range(player_pool_tab, overflow_cell, [[_overflow_formula(edge_tab, position, cap)]])

        pool_rows = [[_pool_type_formula(edge_tab, row, id_col=id_col)] for row in range(start, end + 1)]
        client.update_range(player_pool_tab, f"{pool_col}{start}:{pool_col}{end}", pool_rows)

        summary.append(f"{player_pool_tab}!{name_cell}: {position} block ({cap} slots) now formula-driven")

    return summary
