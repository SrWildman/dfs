"""One-time structural resize of Player Pool's position-block row counts.

Follow-up to Task K 4.3 (see docs/HANDOFF.md 4.5): once Player Pool's Name
column became tick-driven instead of typed, Sam asked to raise the
per-position caps rather than add a manual-override escape hatch, so a
deep tick list is never silently hidden by the overflow warning (4.3
already made that visible, never silent -- this just makes it less
likely to fire).

Grows a block by inserting real rows via `SheetsClient.insert_rows` with
`inheritFromBefore=True` -- unlike the pool deck's insert at row 1 (no
row "before" the insertion point, forced `inheritFromBefore=False`,
which is why that insert inherited the WRONG row's formatting), inserting
in the middle of an existing block always has a real, already-correctly-
formatted in-block row immediately above it, so inheriting from it is
exactly right here.

`insertDimension` only creates blank rows -- it copies formatting and
data validation, never formula text -- and `sheet_links.link_edge_columns`
is idempotent-by-skipping (`"already linked ... skipped"`), so simply
re-running `dfs sheets link-edge` after growing a block does NOT backfill
the new rows' EdgeRaw-linked columns. Every new row's B..Y columns are
therefore filled here by copying the row immediately above it byte-for-
byte (read fresh via `read_formula`, since Sheets auto-adjusts THAT row's
own self-references when an earlier insert shifts it down) and
substituting only the `A<row>` self-reference for the new row number --
never reconstructed from `sheet_links.edge_lookup_formula`, because the
live sheet's already-written formulas turned out to reference an older,
narrower EdgeRaw range (`$B:$T`) than today's `derived.EDGE_COLUMNS`
would generate (`$B:$V`) -- still correct (VLOOKUP only needs the range
to reach its target column), just a harmless historical artifact that
reconstructing from scratch would have overwritten with a cosmetically
different (but not more correct) pattern.

The three EdgeRaw-linked color scales (`sheet_links.COLOR_SCALE_LINKED_COLUMNS`)
are a separate hazard: they were written once, by `link_edge_columns`, to
a hardcoded `row 2 : max(block ends)` range that is never revisited on
later runs (same idempotency skip) -- so they do NOT reliably auto-extend
across every insertion point (Sheets only auto-grows a conditional format
range for an insert strictly *inside* it; growing the very last block
inserts at the range's tail, which is not reliably "inside"). Rather than
depend on that ambiguity, `fix_color_scale_ranges` deletes and re-adds
all three with the verified original colors, explicitly spanning the new
full range.
"""

from __future__ import annotations

import re

from dfs.sheet_links import COLOR_SCALE_LINKED_COLUMNS
from dfs.sheets import SheetsClient, column_letter

_POSITION_COLUMN = "B"
_LAST_FORMULA_COLUMN = "Y"

# Matches sheet_links.link_edge_columns' own color-scale colors exactly --
# these three ranges are corrected in place here, not reinvented.
_MIN_COLOR = {"red": 0.96, "green": 0.80, "blue": 0.80}
_MID_COLOR = {"red": 1.0, "green": 1.0, "blue": 0.80}
_MAX_COLOR = {"red": 0.72, "green": 0.88, "blue": 0.72}


def _substitute_self_reference(formula: str, old_row: int, new_row: int) -> str:
    """`=VLOOKUP($A29,...)` (or bare `A29`) -> the same formula pointing at
    `new_row` instead. Only ever called on a row's own formulas, where the
    row number appears exclusively as part of its own column-A
    self-reference (verified directly against the live sheet before
    writing this) -- a plain substring match would risk touching an
    unrelated numeric literal that happens to equal the row number."""
    if not formula.startswith("="):
        return formula
    return re.sub(rf"A{old_row}\b", f"A{new_row}", formula)


def grow_block(
    client: SheetsClient,
    tab: str,
    *,
    position: str,
    current_last_row: int,
    count: int,
) -> int:
    """Grow one position block by `count` rows, inserted immediately after
    `current_last_row`. Caller must pass the block's CURRENT last row (not
    a precomputed one) -- an earlier `grow_block` call on a block above
    this one shifts every row number below it. Returns the block's new
    last row, for the caller to feed into the next `grow_block` call."""
    template_row = client.read_formula(
        tab, f"{_POSITION_COLUMN}{current_last_row}:{_LAST_FORMULA_COLUMN}{current_last_row}"
    )[0]

    insert_at = current_last_row + 1
    client.insert_rows(tab, at_row=insert_at, count=count)

    for i in range(count):
        new_row = insert_at + i
        new_values = [position] + [
            _substitute_self_reference(cell, current_last_row, new_row) for cell in template_row[1:]
        ]
        client.update_range(tab, f"{_POSITION_COLUMN}{new_row}:{_LAST_FORMULA_COLUMN}{new_row}", [new_values])

    return current_last_row + count


def resize_player_pool(
    client: SheetsClient,
    *,
    player_pool_tab: str,
    blocks: list[tuple[int, int]],
    positions: list[str],
    target_sizes: list[int],
) -> list[tuple[int, int]]:
    """Grow each block in `blocks` (in order, top to bottom) to its
    `target_sizes` count, returning the new block boundaries -- callers
    must hardcode these into `weekly_reset.PLAYER_POOL_NAME_BLOCKS`
    afterward, matching this codebase's existing convention of measuring
    a structural layout once and pinning it as a literal (see
    weekly_reset.py's own docstring). Shrinking is deliberately
    unsupported -- deleting rows that may hold real ticks/formulas is a
    different, much riskier operation this function doesn't attempt."""
    new_blocks = []
    shift = 0
    for (start, end), position, target in zip(blocks, positions, target_sizes, strict=True):
        start, end = start + shift, end + shift
        current_size = end - start + 1
        if target < current_size:
            raise ValueError(f"{position}: target {target} is smaller than current size {current_size}")
        grow_by = target - current_size
        if grow_by:
            end = grow_block(client, player_pool_tab, position=position, current_last_row=end, count=grow_by)
            shift += grow_by
        new_blocks.append((start, end))
    return new_blocks


def fix_color_scale_ranges(client: SheetsClient, player_pool_tab: str, *, last_row: int) -> None:
    """Re-point the three EdgeRaw-linked color scales at `2:last_row`,
    deleting and re-adding rather than trusting Sheets to have
    auto-extended them through every insert (see module docstring)."""
    header = client.read_range(player_pool_tab, "A1:1")[0]
    for column_name in COLOR_SCALE_LINKED_COLUMNS:
        col = column_letter(header.index(column_name))
        client.clear_conditional_formats(player_pool_tab, column=col)
        client.add_color_scale(
            player_pool_tab,
            f"{col}2:{col}{last_row}",
            min_color=_MIN_COLOR,
            mid_color=_MID_COLOR,
            max_color=_MAX_COLOR,
        )
