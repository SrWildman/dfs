"""One-time structural resize of Player Pool's position-block row counts.

Follow-up to Task K 4.3 (see docs/planning/HANDOFF.md 4.5): once Player Pool's Name
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
re-running `dfs setup link-edge` after growing a block does NOT backfill
the new rows' EdgeRaw-linked columns. Every new row's B..(last column)
is therefore filled here by copying the row immediately above it byte-
for-byte (read fresh via `read_formula`, since Sheets auto-adjusts THAT
row's own self-references when an earlier insert shifts it down) and
substituting only the `A<row>` self-reference for the new row number --
never reconstructed from `sheet_links.edge_lookup_formula`, because a
copied formula is still correct regardless of which EdgeRaw range it
happens to reference (VLOOKUP only needs the range to reach its target
column), and reconstructing from scratch risks overwriting a working
pattern with a cosmetically different one for no reason. The "last
column" itself is read fresh from the tab's own header on every call
(`grow_block`), not hardcoded -- see its own docstring for why that
specifically was a real, Phase-3-triggered bug here once.

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

from dfs.sheet_color_scales import column_rule_specs
from dfs.sheet_links import COLOR_SCALE_LINKED_COLUMNS
from dfs.sheets import SheetsClient, column_letter

_POSITION_COLUMN = "B"


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
    header_row: int = 1,
) -> int:
    """Grow one position block by `count` rows, inserted immediately after
    `current_last_row`. Caller must pass the block's CURRENT last row (not
    a precomputed one) -- an earlier `grow_block` call on a block above
    this one shifts every row number below it. Returns the block's new
    last row, for the caller to feed into the next `grow_block` call.

    The tab's last formula-bearing column is read fresh from its own
    header every call, never hardcoded -- Phase 3's reorder moved real
    EdgeRaw-linked columns (Roof/Wind/...) onto what used to be this
    tab's actual last column (`Y`), so a hardcoded version of this
    function would silently leave every newly-inserted row missing
    everything from that point on instead of copying it.

    PROMPT_BOARD_FIXES.md item 8 (2026-09-26), found live: `header_row`
    used to be hardcoded to row 1 here (correct when this was first
    written, 2026-09-06 -- Player Pool's header really was row 1 then).
    The 2026-09-16 add-a-player control row pushed it to row 2 without
    this function ever being revisited, since nothing called `grow_block`
    again until this item. Reading `A1:1` then returns the 1-cell control
    row ("Add a player") instead of the real header, so `last_formula_col`
    collapses to `column_letter(0) = "A"` -- BELOW `_POSITION_COLUMN =
    "B"`. The resulting `f"{_POSITION_COLUMN}{row}:{last_formula_col}{row}"`
    range string (e.g. `"B13:A13"`) is inverted, gets silently
    re-ordered to `A13:B13`, and every new row ends up with the position
    label written into columns A AND B, nothing else -- silently missing
    every real EdgeRaw-linked formula from C onward. Caught by real cell
    reads after growing the live sheet for real, not by this file's own
    unit tests (whose fake client, like `fix_color_scale_ranges`' before
    it, ignores the requested range and always returns the same header
    regardless of which row was asked for). `header_row` now defaults to
    1 (correct for a tab whose header really is there) but every real
    caller must pass its own tab's actual header row explicitly."""
    header = client.read_range(tab, f"A{header_row}:{header_row}")[0]
    last_formula_col = column_letter(len(header) - 1)
    template_row = client.read_formula(
        tab, f"{_POSITION_COLUMN}{current_last_row}:{last_formula_col}{current_last_row}"
    )[0]

    insert_at = current_last_row + 1
    client.insert_rows(tab, at_row=insert_at, count=count)

    for i in range(count):
        new_row = insert_at + i
        new_values = [position] + [
            _substitute_self_reference(cell, current_last_row, new_row) for cell in template_row[1:]
        ]
        client.update_range(tab, f"{_POSITION_COLUMN}{new_row}:{last_formula_col}{new_row}", [new_values])

    return current_last_row + count


def resize_player_pool(
    client: SheetsClient,
    *,
    player_pool_tab: str,
    blocks: list[tuple[int, int]],
    positions: list[str],
    target_sizes: list[int],
    header_row: int = 1,
) -> list[tuple[int, int]]:
    """Grow each block in `blocks` (in order, top to bottom) to its
    `target_sizes` count, returning the new block boundaries -- callers
    must hardcode these into `weekly_reset.PLAYER_POOL_NAME_BLOCKS`
    afterward, matching this codebase's existing convention of measuring
    a structural layout once and pinning it as a literal (see
    weekly_reset.py's own docstring). Shrinking is deliberately
    unsupported -- deleting rows that may hold real ticks/formulas is a
    different, much riskier operation this function doesn't attempt.

    `header_row` is threaded straight through to `grow_block` -- see its
    own docstring (PROMPT_BOARD_FIXES.md item 8, 2026-09-26) for the real
    header-row-1 bug this guards against on Player Pool specifically."""
    new_blocks = []
    shift = 0
    for (start, end), position, target in zip(blocks, positions, target_sizes, strict=True):
        start, end = start + shift, end + shift
        current_size = end - start + 1
        if target < current_size:
            raise ValueError(f"{position}: target {target} is smaller than current size {current_size}")
        grow_by = target - current_size
        if grow_by:
            end = grow_block(
                client,
                player_pool_tab,
                position=position,
                current_last_row=end,
                count=grow_by,
                header_row=header_row,
            )
            shift += grow_by
        new_blocks.append((start, end))
    return new_blocks


def fix_color_scale_ranges(
    client: SheetsClient, player_pool_tab: str, *, last_row: int, header_row: int = 1
) -> None:
    """Re-point the three EdgeRaw-linked color scales at `header_row+1:
    last_row`, deleting and re-adding rather than trusting Sheets to have
    auto-extended them through every insert (see module docstring).

    PROMPT_BOARD_FIXES.md item 8 (2026-09-25): found live, running this
    against the real template for the first time since Player Pool's own
    header moved off row 1 (A3, "the add-a-player control row" -- see
    `weekly_reset.PLAYER_POOL_HEADER_ROW`) -- this hardcoded `"A1:1"`/
    `"2:last_row"`, which happened to still work for a tab whose header
    genuinely sits at row 1, but raised `ValueError: 'Leverage' is not in
    list` the moment it ran for real against Player Pool (header row 2).
    Never caught by this file's own unit tests because the fake client
    they use ignores the requested range and always returns the same
    canned header regardless -- a real "verify by reading cells back"
    catch, not a mock-covered one. `header_row` now defaults to 1 (correct
    for a tab whose header really is there) but every real caller must
    pass its own tab's actual header row explicitly.

    PROMPT_BOARD_FIXES.md item 7 (2026-09-25): routed through the shared
    `_scale_rule_specs` dispatch (`sheet_color_scales.py`) instead of the
    hand-rolled colours this used to match against `sheet_links.py`'s own
    (now also routed the same way) -- picks up the same zero-exclusion
    every other scaled column in the workbook now gets.
    """
    header = client.read_range(player_pool_tab, f"A{header_row}:{header_row}")[0]
    data_start = header_row + 1
    client.clear_conditional_formats_for(
        player_pool_tab,
        [(column_letter(header.index(name)), None) for name in COLOR_SCALE_LINKED_COLUMNS],
    )
    gradient_specs: list[dict] = []
    boolean_specs: list[dict] = []
    for column_name in COLOR_SCALE_LINKED_COLUMNS:
        # Round 5 item 3: the shared band dispatch (formula rules plus the grey zero chip).
        gradients, booleans = column_rule_specs(
            column_name, column_letter(header.index(column_name)), data_start, last_row, header=header
        )
        gradient_specs += gradients
        boolean_specs += booleans
    client.add_color_scales(player_pool_tab, gradient_specs)
    client.add_boolean_rules(player_pool_tab, boolean_specs)
