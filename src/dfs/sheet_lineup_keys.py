"""Round 5 follow-up item 3: Lineups' duplicate and exposure counts compare RESOLVED names.

Typed names resolve through `NameKey`/`NameAlias` (`sheet_names.resolve_name_expr`), so
"kenneth walker" and "Kenneth Walker III" are one player. Every count that asks "is this
the same player as that one?" -- the DUPLICATE flag inside a lineup, `Min Unique` between
lineups, Exposure's counts, Player Pool's `Used`/`In` -- used to compare the TYPED text,
so the two spellings counted as two players.

`Player Key` is a hidden Lineups column holding, per roster-slot row, DK's canonical name
for whatever is typed in column A -- or the typed text itself when nothing matches, so two
identical unresolved names still count as duplicates, exactly as before. Every count above
compares this column instead of column A. Blank slots stay blank.
"""

from __future__ import annotations

from dfs.sheet_names import resolve_name_expr
from dfs.sheets import SheetsClient, column_letter

LINEUP_KEY_HEADER = "Player Key"


def lineup_key_letter(client: SheetsClient, tab: str, *, header_row: int = 1) -> str | None:
    """The `Player Key` column's letter on `tab`, found by header name (never typed), or
    None when the column doesn't exist yet -- callers then fall back to column A."""
    if not client.tab_exists(tab):
        return None
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = rows[0] if rows else []
    return column_letter(header.index(LINEUP_KEY_HEADER)) if LINEUP_KEY_HEADER in header else None


def lineup_key_formula(row: int, edge_tab: str, *, name_col: str = "A") -> str:
    typed = f"${name_col}{row}"
    return f'=IF({typed}="","",{resolve_name_expr(typed, edge_tab)})'


def write_lineup_keys(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int,
    name_blocks: list[tuple[int, int]],
    edge_tab: str,
) -> str:
    """Writes the key formula on every roster-slot row of every lineup block. Idempotent
    (a full rewrite each call); skips cleanly if the column hasn't been provisioned yet
    (`dfs setup reorder-columns` creates it)."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    key_col = lineup_key_letter(client, tab, header_row=header_row)
    if key_col is None:
        missing = f"{LINEUP_KEY_HEADER!r} column not found"
        return f"{tab}: {missing} (run `dfs setup reorder-columns` first) -- skipped"
    for start, end in name_blocks:
        rows = [[lineup_key_formula(r, edge_tab)] for r in range(start, end + 1)]
        client.update_range(tab, f"{key_col}{start}:{key_col}{end}", rows)
    return f"{tab}: {LINEUP_KEY_HEADER} written for {len(name_blocks)} block(s)"
