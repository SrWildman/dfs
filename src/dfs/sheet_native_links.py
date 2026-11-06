"""Regenerates the hardcoded VLOOKUP-by-Name formulas Player Pool and
Lineups use to pull PlayerPoolRaw's native columns (Team, DK Sal, Pts,
...) -- the exact "Google fixes ranges, never the integer argument"
hazard CONTRIBUTING.md's central hazard section warns about. Every
formula here is `=VLOOKUP($A<row>,PlayerPoolRaw!$A:$<col>,<index>,false)`
(optionally IFNA-wrapped), with `<col>`/`<index>` both derived from
`sheet_columns.PLAYER_POOL_RAW_COLUMN_ORDER` -- re-run this (never
hand-edit the formula text) whenever that order changes, the same
"derive positions, never hardcode them" rule as everywhere else in this
repo. This is 3.4.d's tool: nothing regenerated these before Phase 3,
because nothing had ever reordered PlayerPoolRaw before.

Deliberately excludes Name (typed directly, not a VLOOKUP), Pos. (set by
each block's own IF formula, not looked up), the four `SoS n` placeholder
columns (nothing to look up yet), and every `sheet_links.
LINKED_EDGE_COLUMNS` member (CeilVal, GameEnv, ...) -- those are looked up
against EdgeRaw, not PlayerPoolRaw, and stay `sheet_links.py`'s job.
"""

from __future__ import annotations

from dfs.column_reorder import group_into_contiguous_runs
from dfs.sheet_columns import PLAYER_POOL_RAW_COLUMN_ORDER
from dfs.sheet_names import resolve_name_expr
from dfs.sheets import SheetsClient, column_letter

NATIVE_LOOKUP_COLUMNS = [
    "Team",
    "Opp.",
    "Venue",
    "DK Sal",
    "Pts",
    "Val",
    "Ceil",
    "Own%",
    "O/U",
    "Spread",
    "Team Implied",
    "OppPosRank",
]

# Can legitimately be blank (a bye-week/off-slate player has no O/U yet, a
# game with no line posted has no meaningful spread) -- matches
# PlayerPoolRaw's own IFNA use for these same three columns.
_OPTIONAL_NATIVE_COLUMNS = {"O/U", "Spread", "Team Implied"}


def native_lookup_formula(
    row: int, column_name: str, edge_tab: str = "EdgeRaw", *, name_col: str = "A"
) -> str:
    """The VLOOKUP-by-Name formula for one cell on Player Pool/Lineups,
    pulling `column_name` off PlayerPoolRaw -- `row`'s player Name (this
    tab's own `name_col`, found from its header) looked up against PlayerPoolRaw, range always
    starting at PlayerPoolRaw's own column A. Wrapped in an
    `IF($A<row>="","",...)` blank-name guard -- see `sheet_links.
    edge_lookup_formula`'s docstring for why (same live-sheet finding,
    same fix, mirrored here for the native half of these two tabs'
    columns)."""
    index = PLAYER_POOL_RAW_COLUMN_ORDER.index(column_name) + 1  # 1-based, range starts at A
    col = column_letter(index - 1)
    # Round 5 item 6: resolve the typed name to DK's canonical spelling first.
    name_cell = f"${name_col}{row}"
    base = f"VLOOKUP({resolve_name_expr(name_cell, edge_tab)},PlayerPoolRaw!$A:${col},{index},false)"
    lookup = f"IFNA({base})" if column_name in _OPTIONAL_NATIVE_COLUMNS else base
    return f'=IF({name_cell}="","",{lookup})'


def rewrite_native_lookup_columns(
    client: SheetsClient,
    tab: str,
    name_blocks: list[tuple[int, int]],
    *,
    header_row: int = 1,
) -> str:
    """Overwrites every NATIVE_LOOKUP_COLUMNS cell in `tab` (for every row
    in every `name_blocks` range) with a formula rebuilt from
    PlayerPoolRaw's CURRENT column order -- 3.4.d's fix, run once right
    after PlayerPoolRaw's own columns have actually moved. Finds each
    column by header name, same convention as `sheet_links.
    link_edge_columns`; every name here must already exist in `tab`'s
    header (all twelve have always been native columns; this never
    creates one) -- raises `ValueError` naming whatever's missing rather
    than silently skipping it.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []

    missing = [name for name in NATIVE_LOOKUP_COLUMNS if name not in header]
    if missing:
        raise ValueError(f"{tab!r} header is missing native column(s) {missing} -- nothing to rewrite")

    columns = {name: header.index(name) for name in NATIVE_LOOKUP_COLUMNS}
    name_col = column_letter(header.index("Name")) if "Name" in header else "A"
    runs = group_into_contiguous_runs(NATIVE_LOOKUP_COLUMNS, columns)

    for start, end in name_blocks:
        for run in runs:
            start_col = column_letter(columns[run[0]])
            end_col = column_letter(columns[run[-1]])
            rows = [
                [native_lookup_formula(row, name, name_col=name_col) for name in run]
                for row in range(start, end + 1)
            ]
            client.update_range(tab, f"{start_col}{start}:{end_col}{end}", rows)

    return f"{tab}: rewrote {len(NATIVE_LOOKUP_COLUMNS)} native PlayerPoolRaw-lookup column(s)"


# -- Plan N item 16 (the Joe Milton #N/A): stale baked EdgeRaw letters ------------------------------------
#
# Every formula above resolves the typed name through `resolve_name_expr`, which BAKES EdgeRaw's `Name` and
# `NameKey` column letters into the text when it is written. EdgeRaw is rewritten by value, never by an
# insert, so when a new EdgeRaw column lands ahead of `NameKey` nothing shifts those letters: Week 9's Player
# Pool and Lineups still matched against `$BS:$BS` (NameKey had moved to `CO`), so any player whose typed name
# needs the NameKey match (Joe Milton for Joe Milton III) read #N/A in every native column.


def native_lookup_tabs(cfg) -> list[tuple[str, list[tuple[int, int]], int]]:
    """(tab, name blocks, header row) for the two tabs that carry native lookups."""
    from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

    return [
        (cfg.lineups.player_pool_tab, PLAYER_POOL_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW),
        (cfg.lineups.builder_tab, LINEUPS_NAME_BLOCKS, LINEUPS_NAME_BLOCKS[0][0] - 1),
    ]


def stale_native_cells(
    client, tab: str, name_blocks: list[tuple[int, int]], *, header_row: int = 1
) -> list[str]:
    """Every native-lookup cell on `tab` whose formula is not what `native_lookup_formula` writes today (a
    stale baked letter, a typed value, a blank). Read-only; [] when the tab has no native columns."""
    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_rows[0]) if header_rows else []
    if any(name not in header for name in NATIVE_LOOKUP_COLUMNS):
        return []
    columns = {name: header.index(name) for name in NATIVE_LOOKUP_COLUMNS}
    name_col = column_letter(header.index("Name")) if "Name" in header else "A"
    first_index, last_index = min(columns.values()), max(columns.values())
    first_col, last_col = column_letter(first_index), column_letter(last_index)
    stale = []
    for start, end in name_blocks:
        rows = client.read_formula(tab, f"{first_col}{start}:{last_col}{end}")
        for offset in range(end - start + 1):
            row = start + offset
            cells = rows[offset] if offset < len(rows) else []
            for name, index in columns.items():
                i = index - first_index
                got = str(cells[i]) if i < len(cells) else ""
                if got != native_lookup_formula(row, name, name_col=name_col):
                    stale.append(f"{column_letter(index)}{row}")
    return stale


def refresh_native_lookups(client, cfg, *, only_if_stale: bool = True) -> list[str]:
    """Rewrite the native lookups on Player Pool and Lineups (`rewrite_native_lookup_columns`) wherever a
    cell is stale (or always, `only_if_stale=False`). One line per tab it rewrote, with its stale count.
    Run by `dfs setup native-lookups` and, fail-soft, after every plain `dfs sync`."""
    lines = []
    for tab, blocks, header_row in native_lookup_tabs(cfg):
        if not client.tab_exists(tab):
            continue
        stale = stale_native_cells(client, tab, blocks, header_row=header_row)
        if only_if_stale and not stale:
            continue
        rewrite_native_lookup_columns(client, tab, blocks, header_row=header_row)
        lines.append(
            f"{tab}: native lookups rewritten ({len(stale)} stale cell(s)"
            + (f", first {stale[0]}" if stale else "")
            + ")"
        )
    return lines
