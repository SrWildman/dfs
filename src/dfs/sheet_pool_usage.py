"""Phase 5B: `Used`/`In` on Player Pool -- the one thing a second browser
window (the whole reason split-screen works, see `sheet_pool_deck.py`'s
module docstring) can't tell you on its own: whether a player you're
looking at is already rostered somewhere.

Both are native, self-referential formulas (no EdgeRaw lookup involved),
appended past Player Pool's existing columns (`sheet_columns.
PLAYER_POOL_COLUMN_ORDER`'s own tail) the same append-only way
`sheet_links.link_edge_columns` treats its own linked block -- never
inserted, so nothing to the left ever shifts.

`Used` is a plain `COUNTIF` against the whole of `Lineups!$A:$A` --
deliberately the whole column, not just the real slot rows
(`LINEUPS_NAME_BLOCKS`), because every repeated sub-header row's own
column A holds the literal text "Name" (see `weekly_reset.py`'s module
docstring), which can never collide with an actual player name.

`In` lists WHICH lineups, e.g. "L1, L3, L7" -- built as one `IF(COUNTIF(...))`
term per `LINEUPS_NAME_BLOCKS` entry, joined with `TEXTJOIN`, generated in
Python from that constant rather than hand-typed (twenty terms hand-typed
once is twenty terms someone forgets to update the next time a block moves
-- exactly this codebase's central hazard). If a future, much larger
`LINEUPS_NAME_BLOCKS` would push the generated formula past Sheets'
real ~50,000-character ceiling, `In` is left blank tab-wide rather than
silently written for only the first N blocks -- `_MAX_IN_FORMULA_LEN` is
checked once (the same formula shape recurs at every row, only the cell
reference changes) before any row is written.
"""

from __future__ import annotations

from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_reorder import provision_missing_columns
from dfs.sheets import SheetsClient, column_letter
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

USED_COLUMN = "Used"
IN_COLUMN = "In"

# Real ceiling is ~50,000 characters (Sheets' own formula-length limit);
# kept far below it so this is a real, reachable check rather than a
# comment promising one that can never fire.
_MAX_IN_FORMULA_LEN = 40_000


def _used_formula(name_cell: str, lineups_tab: str) -> str:
    return f'=IF({name_cell}="","",COUNTIF({lineups_tab}!$A:$A,{name_cell}))'


def _in_formula(name_cell: str, lineups_tab: str) -> str:
    """Found live, 2026-09-16: missing the same blank-name guard
    `_used_formula` already has -- `COUNTIF(range, "")` counts truly
    EMPTY cells in `range` as matches, so a blank Player Pool row (no
    player typed/ticked in yet) matched every still-empty Lineups block,
    showing "L1, L2, ..., L20" on every unused row rather than nothing.
    Invisible until the pool actually had unfilled rows sitting next to
    real ones for someone to compare -- reported as unexpected data
    showing up in a column that should be blank for anyone not rostered
    anywhere yet."""
    terms = []
    for i, (start, end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
        rng = f"{lineups_tab}!$A${start}:$A${end}"
        terms.append(f'IF(COUNTIF({rng},{name_cell})>0,"L{i}","")')
    return f'=IF({name_cell}="","",IFERROR(TEXTJOIN(", ",TRUE,{",".join(terms)}),""))'


def write_pool_usage_columns(
    client: SheetsClient, player_pool_tab: str, lineups_tab: str, *, header_row: int = PLAYER_POOL_HEADER_ROW
) -> str:
    """Creates `Used`/`In` on `player_pool_tab` if either is missing
    (append-only, via `provision_missing_columns`), then (re)writes both
    columns' formulas across every `PLAYER_POOL_NAME_BLOCKS` row. Always
    fully rewritten on every call -- same idempotency stance as
    `sheet_links.write_edge_row_links` -- so re-running after
    `LINEUPS_NAME_BLOCKS` or `PLAYER_POOL_NAME_BLOCKS` changes self-heals
    rather than needing a manual fix.
    """
    provision_missing_columns(client, player_pool_tab, PLAYER_POOL_COLUMN_ORDER, header_row=header_row)

    header = client.read_range(player_pool_tab, f"A{header_row}:{header_row}")[0]
    name_col = column_letter(header.index("Name"))
    used_col = column_letter(header.index(USED_COLUMN))
    in_col = column_letter(header.index(IN_COLUMN)) if IN_COLUMN in header else None

    # The formula shape (not its cell reference) is what can get too long,
    # and it's identical at every row -- checked once here against a
    # representative cell rather than re-checked per row.
    sample_in_formula = _in_formula(f"${name_col}3", lineups_tab)
    in_too_long = len(sample_in_formula) > _MAX_IN_FORMULA_LEN

    for start, end in PLAYER_POOL_NAME_BLOCKS:
        used_rows = [[_used_formula(f"${name_col}{r}", lineups_tab)] for r in range(start, end + 1)]
        client.update_range(player_pool_tab, f"{used_col}{start}:{used_col}{end}", used_rows)
        if in_col and not in_too_long:
            in_rows = [[_in_formula(f"${name_col}{r}", lineups_tab)] for r in range(start, end + 1)]
            client.update_range(player_pool_tab, f"{in_col}{start}:{in_col}{end}", in_rows)

    total_rows = sum(end - start + 1 for start, end in PLAYER_POOL_NAME_BLOCKS)
    note = ""
    if in_col and in_too_long:
        note = (
            f" -- 'In' left blank: its generated formula ({len(sample_in_formula)} chars) "
            f"exceeds the {_MAX_IN_FORMULA_LEN}-char safety ceiling"
        )
    return f"{player_pool_tab}: Used/In written for {total_rows} row(s){note}"
