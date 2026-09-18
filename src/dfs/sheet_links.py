"""Writes EdgeRaw's derived columns (CeilVal, Leverage, Flag, ...) into
Player Pool, Lineups and PlayerPoolRaw, via the same VLOOKUP-by-Name join
those tabs already use against PlayerPoolRaw for Pos./Team/Pts/etc.

Phase 3 rewrite: finds each linked column BY HEADER NAME rather than
assuming a contiguous appended block. The old append-only design (see
CONTRIBUTING.md's Phase 8 postmortem) worked only because every linked
column sat past everything native; `sheet_columns.py`'s designed order
deliberately interleaves them (GameEnv inside GAME, Stadium/Roof/Wind as
their own WEATHER group, etc.), which a pure append can no longer produce.
`link_edge_columns` now writes into whatever column already carries a
given name in the header -- created via `SheetsClient.move_columns` as
part of the Phase 3 reorder itself, not by this function -- and only
creates (appends) a column here as a fallback for a name genuinely absent
from the header, e.g. a fresh sheet build that hasn't been through that
reorder. Still idempotent: if every name in LINKED_EDGE_COLUMNS is already
present somewhere in the header, it reports "already linked" and does
nothing.

The written cells are plain formula *text*, not a living reference to
`derived.EDGE_COLUMNS` -- if that list's order or membership ever changes,
the already-written formulas in the sheet won't update themselves. Re-run
`dfs setup link-edge` after such a change (clearing the old linked columns
by hand first).
"""

from __future__ import annotations

from dfs.column_reorder import group_into_contiguous_runs
from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_columns import CEILING_DETAIL, GAME, LINKED_COLUMNS, MOVEMENT, WEATHER
from dfs.sheets import SheetsClient, column_letter

# The EdgeRaw columns worth surfacing elsewhere -- excludes what Player
# Pool/Lineups already show via PlayerPoolRaw (Position, Team, Opp, Salary,
# ProjPts/Pts, Ceiling/Ceil, Val, Own%), so nothing gets duplicated.
# Re-exports `sheet_columns.LINKED_COLUMNS` (defined there, next to the
# zones it's drawn from) under this module's established name.
LINKED_EDGE_COLUMNS = LINKED_COLUMNS

# Color-scaled the same way EdgeRaw itself is (sheet_style.polish_edge) --
# these three are the ones actually worth scanning by eye.
COLOR_SCALE_LINKED_COLUMNS = ("Leverage", "CeilVal", "GameEnv")

# PlayerPoolRaw is the template's hub tab that Player Pool AND Lineups both
# already VLOOKUP against for Pos./Team/Pts/etc. -- fixed name, not
# user-configurable, unlike the dfs-managed source tabs in
# config.toml's tab_mappings. Every column there fills via a formula keyed
# off DkSalClean's own row (e.g. `=DkSalClean!D2`), not a manually-typed
# Name column like Player Pool/Lineups have, so there's exactly one
# contiguous block rather than per-lineup/per-position blocks. Measured
# directly off the live sheet the same way weekly_reset.py's blocks were
# (its Pts column's non-blank formula rows run 2-987) -- re-measure if the
# template's provisioned player-pool capacity ever changes.
PLAYER_POOL_RAW_TAB = "PlayerPoolRaw"
PLAYER_POOL_RAW_BLOCK = [(2, 987)]

# Wrapped in IFNA because these can legitimately be blank for a valid,
# already-typed-in player (a dome game has no Wind; games/weather may not
# be synced this run) -- matching Player Pool's own O/U, Spread, Team
# Implied columns, which use IFNA for the same reason. Everything else here
# should reliably resolve once EdgeRaw is synced, matching the un-wrapped
# VLOOKUPs Pos./Team/Pts/etc. already use.
_OPTIONAL_LINKED_COLUMNS = {"Wind"}

_EDGE_NAME_COLUMN = "Name"
_EDGE_RANGE_START = column_letter(EDGE_COLUMNS.index(_EDGE_NAME_COLUMN) + EDGE_DATA_OFFSET)
_EDGE_RANGE_END = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)


def _vlookup_index(column_name: str) -> int:
    """1-based position of `column_name` within EdgeRaw!$<start>:$<end>,
    for VLOOKUP's 3rd argument."""
    return EDGE_COLUMNS.index(column_name) - EDGE_COLUMNS.index(_EDGE_NAME_COLUMN) + 1


def edge_lookup_formula(row: int, edge_tab: str, column_name: str) -> str:
    """The VLOOKUP-by-Name formula for one cell: `row`'s player Name
    (column A of the tab this formula is written into) looked up against
    `edge_tab`'s `column_name`. Wrapped in an `IF($A<row>="","",...)`
    blank-name guard -- an unfilled Player Pool/Lineups slot's Name cell
    is blank, and VLOOKUP against a blank key resolves to `#N/A` rather
    than blank, unlike `sheet_pool_formulas.py`'s own Source/Pool columns
    (which always had this guard). Found live: a lineup/position block
    with more capacity than actual picks showed a wall of `#N/A` across
    every linked column instead of just sitting empty."""
    index = _vlookup_index(column_name)
    base = f"VLOOKUP($A{row},{edge_tab}!${_EDGE_RANGE_START}:${_EDGE_RANGE_END},{index},false)"
    lookup = f"IFNA({base})" if column_name in _OPTIONAL_LINKED_COLUMNS else base
    return f'=IF($A{row}="","",{lookup})'


def edge_row_hyperlink_formula(row: int, edge_tab: str, edge_gid: int) -> str:
    """A3: `=HYPERLINK("#gid=...&range=...","Edge ↗")` jumping straight to
    this row's own player on `edge_tab`, so removing someone from the pool
    (unchecking EdgeRaw's Pool column) is one click away instead of a
    scroll/search through 743 rows. `#gid=<id>&range=<a1>` is Sheets' own
    same-spreadsheet navigation syntax -- no full URL needed, so this
    keeps working if the spreadsheet itself is ever renamed or moved.
    Blank-name guarded like `edge_lookup_formula`; `IFNA` guards a name
    that doesn't currently match anything on EdgeRaw (a bye-week pick, a
    stale add) so it reads as a dash rather than `#N/A`."""
    match = f"MATCH($A{row},{edge_tab}!${_EDGE_RANGE_START}:${_EDGE_RANGE_START},0)"
    target = f'"#gid={edge_gid}&range={_EDGE_RANGE_START}"&{match}'
    return f'=IF($A{row}="","",IFNA(HYPERLINK({target},"Edge ↗"),"-"))'


def write_edge_row_links(
    client: SheetsClient,
    tab: str,
    name_blocks: list[tuple[int, int]],
    edge_tab: str,
    *,
    header_row: int = 1,
) -> str:
    """Fills the "Edge ↗" column (A3, if present in `tab`'s header) across
    every row in `name_blocks` with `edge_row_hyperlink_formula`. Skips
    cleanly if the tab has no "Edge ↗" column -- older sheets, or a tab
    this feature was never asked for on. Always fully rewritten (same
    idempotency stance as `sheet_pool_formulas.write_pool_formulas`):
    there's no "already has it" state worth skipping, and `edge_gid` is
    re-read fresh every call rather than cached, so a tab that was ever
    deleted and recreated self-heals on the next `dfs setup polish` rather
    than needing a manual fix.
    """
    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if "Edge ↗" not in header:
        return f"{tab}: no 'Edge ↗' column -- skipped"

    col = column_letter(header.index("Edge ↗"))
    edge_gid = client.tab_gid(edge_tab)
    for start, end in name_blocks:
        rows = [[edge_row_hyperlink_formula(row, edge_tab, edge_gid)] for row in range(start, end + 1)]
        client.update_range(tab, f"{col}{start}:{col}{end}", rows)

    total_rows = sum(end - start + 1 for start, end in name_blocks)
    return f"{tab}: 'Edge ↗' links written for {total_rows} row(s)"


def link_edge_columns(
    client: SheetsClient,
    tab: str,
    name_blocks: list[tuple[int, int]],
    edge_tab: str,
    *,
    header_row: int = 1,
    header_repeats_at: list[int] | None = None,
    force: bool = False,
) -> str:
    """Fill LINKED_EDGE_COLUMNS' VLOOKUP-by-Name formulas into `tab`, into
    whatever column already carries each name in the header. A name
    genuinely absent from the header is created by appending past the
    current width -- a fallback for a fresh sheet build that hasn't been
    through the Phase 3 reorder yet (`column_reorder.compute_column_moves`
    + `SheetsClient.move_columns` puts a freshly-created column into its
    designed zone; this function only guarantees the header text and
    formulas exist somewhere for that reorder to move). `header_repeats_at`
    re-prints newly-created header text at additional rows (Lineups
    repeats its header once per lineup slot). Idempotent: if every name in
    LINKED_EDGE_COLUMNS is already present somewhere in the header, does
    nothing and reports that it skipped -- unless `force=True`, which
    refreshes every linked column's formula in place regardless (still
    finding each by name, never touching what it creates/moves). Needed
    whenever `edge_lookup_formula`'s own generation logic changes (e.g.
    the blank-name-guard fix below) rather than a column's position: the
    normal "something's missing" trigger never fires for a tab that's
    already fully linked, so nothing would otherwise pick up the new
    formula shape.

    `header_row` defaults to 1, true for every tab this function links now
    -- Lineups' own header briefly sat elsewhere while the pool deck
    occupied the rows above it (2026-09-06 through 2026-09-16, removed
    entirely -- see `sheet_pool_deck.py`'s module docstring); reading/
    writing row 1 there during that window found the deck's controls
    instead of the real header, mistook an already-linked tab for an
    unlinked one, and appended a *second*, wrongly-positioned copy of
    LINKED_EDGE_COLUMNS starting at column B -- overwriting every lineup
    block's Pos./Team/DK Sal/etc. data with a duplicate EdgeRaw lookup.
    This happened for real on the template; see CONTRIBUTING.md's
    changelog. The CLI still passes Lineups' header row explicitly
    (derived from `LINEUPS_NAME_BLOCKS`, currently 1) rather than
    hardcoding it, so this stays correct if it ever moves again.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []

    missing = [name for name in LINKED_EDGE_COLUMNS if name not in header]
    if not missing and not force:
        return f"{tab}: already linked ({len(LINKED_EDGE_COLUMNS)} header column(s) present) -- skipped"

    if missing:
        created_start = column_letter(len(header))
        header.extend(missing)
        created_end = column_letter(len(header) - 1)
        # A tab already at its provisioned grid width (e.g. fully linked
        # before a rename made one name look "missing" again -- see
        # `sheet_reorder.rename_header_column`, which exists so a pure
        # rename never reaches this append path in the first place)
        # otherwise raises "exceeds grid limits" the instant this tries
        # to write past the current column count. Same fix as
        # `provision_missing_columns`.
        client.ensure_column_capacity(tab, len(header))
        client.update_range(tab, f"{created_start}{header_row}:{created_end}{header_row}", [missing])
        for header_row_num in header_repeats_at or []:
            a1 = f"{created_start}{header_row_num}:{created_end}{header_row_num}"
            client.update_range(tab, a1, [missing])

    columns = {name: header.index(name) for name in LINKED_EDGE_COLUMNS}

    # Write one `update_range` call per contiguous run of column indices
    # per name_block, not one per individual column -- the designed order
    # keeps some linked columns adjacent (Leverage/Avail/Flag, the three
    # collapsed groups) even though it's no longer ONE contiguous block,
    # so this still batches almost as well as the old append-only version
    # did, and stays correct (one call per column) for a name that ends up
    # isolated (CeilVal, GameEnv).
    runs = group_into_contiguous_runs(LINKED_EDGE_COLUMNS, columns)

    for start, end in name_blocks:
        for run in runs:
            start_col = column_letter(columns[run[0]])
            end_col = column_letter(columns[run[-1]])
            rows = [
                [edge_lookup_formula(row, edge_tab, name) for name in run] for row in range(start, end + 1)
            ]
            client.update_range(tab, f"{start_col}{start}:{end_col}{end}", rows)

    last_row = max(end for _, end in name_blocks)
    for column_name in COLOR_SCALE_LINKED_COLUMNS:
        col = column_letter(columns[column_name])
        client.add_color_scale(
            tab,
            f"{col}2:{col}{last_row}",
            min_color={"red": 0.96, "green": 0.80, "blue": 0.80},
            mid_color={"red": 1.0, "green": 1.0, "blue": 0.80},
            max_color={"red": 0.72, "green": 0.88, "blue": 0.72},
        )

    # Phase 6, Part 2: GAME/CEILING DETAIL/MOVEMENT/WEATHER all collapse by
    # default now (Sam's own fixed left-to-right order) -- the spine
    # (DK Sal..Flag) stays expanded, everything else is one click away.
    # Built from the tab's own FULL header (`header.index`), not the
    # `columns` dict above -- GAME and WEATHER each mix linked columns
    # (GameEnv; Stadium/Roof/Wind) with native ones (O/U/Spread/Team
    # Implied/OppPosRank; Venue) that aren't in LINKED_EDGE_COLUMNS at
    # all, so `columns[name]` would KeyError on the native members. Only
    # collapses a zone whose members actually landed contiguous in this
    # tab's header -- true once the Phase 3/6 reorder has run, but not yet
    # true right after this function's own append-missing fallback
    # creates a column, so a fresh/partially-reordered sheet doesn't get
    # an accidental collapse spanning unrelated columns in between.
    #
    # These four zones used to sit back-to-back with nothing native
    # between them, so their column ranges were themselves adjacent -- and
    # Sheets does NOT create independent groups for adjacent
    # `addDimensionGroup` calls at the same depth (nesting doesn't help
    # either, verified live on the template's Scratch tab); it silently
    # EXTENDS the first group to cover the later ones, which then makes a
    # later call's own `updateDimensionGroup` (folding shut) fail outright
    # ("no group spans exactly that range"). Found running this live
    # (originally with three zones, Phase 3) -- WEATHER alone (the only
    # group that existed before Phase 3) had never hit this since nothing
    # used to sit adjacent to it. Fixed two ways, stacked: the merge logic
    # below combines any ranges that STILL end up touching (belt and
    # suspenders, and the only thing standing between Sam and one big
    # merged group before the zone-label fix); and each zone now has its
    # own real label column (`sheet_columns.GAME_LABEL` etc.) immediately
    # before it, which is what actually keeps the four ranges apart in
    # practice and lets Sam independently expand/collapse each one --
    # see `sheet_columns.py`'s own module docstring for why a label can't
    # live inside the range it names.
    zone_ranges: list[tuple[int, int]] = []
    for group in (GAME, CEILING_DETAIL, MOVEMENT, WEATHER):
        indices = sorted(header.index(name) for name in group if name in header)
        if indices and indices == list(range(indices[0], indices[0] + len(indices))):
            zone_ranges.append((indices[0], indices[-1]))

    merged_ranges: list[tuple[int, int]] = []
    for start, end in zone_ranges:
        if merged_ranges and merged_ranges[-1][1] + 1 == start:
            merged_ranges[-1] = (merged_ranges[-1][0], end)
        else:
            merged_ranges.append((start, end))

    client.clear_column_groups(tab)
    client.set_column_group_control_before(tab)
    for start, end in merged_ranges:
        client.group_columns(tab, column_letter(start), column_letter(end), collapsed=True)

    return f"{tab}: linked {len(LINKED_EDGE_COLUMNS)} EdgeRaw column(s) ({len(missing)} newly created)"


# Phase 5D's Lineups-only "Vegas" group (O/U, Spread, Team Implied) and
# the `group_lineups_columns` function that built it are REMOVED in Phase
# 6, Part 2: GAME (O/U, Spread, Team Implied, GameEnv, OppPosRank) is now
# one of the four uniformly-collapsed groups `link_edge_columns` itself
# builds identically on every tab, including Lineups -- the Vegas trio is
# a strict subset of it. Left in place, this function would actively
# corrupt the new grouping: it's called right after `link_edge_columns`
# in the same command, and `clear_column_groups` wipes every group on a
# tab, so its own rebuild (still using the old three-zone WEATHER/
# MOVEMENT/INTERNAL split) would silently replace the correct four-zone
# GAME/CEILING_DETAIL/MOVEMENT/WEATHER grouping `link_edge_columns` just
# created moments before. See CONTRIBUTING.md's changelog for the removal.
