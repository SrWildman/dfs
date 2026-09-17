"""Phase 3: moves a tab's real, already-populated columns into
`sheet_columns.py`'s designed order.

Four building blocks, meant to be called in this order (`migrate_tab_to_
designed_order` below does exactly that, for one tab):

1. `provision_missing_columns` -- ensures every name in a target order
   exists SOMEWHERE in the header, appending (blank header text, no data)
   whatever's genuinely missing. Purely a header-text step.
2. `sheet_links.link_edge_columns` / `sheet_native_links.
   rewrite_native_lookup_columns` -- fill in a column's actual formula,
   found by header name wherever it currently sits.
3. `reorder_tab_columns` -- physically moves every column into its final
   position via `column_reorder.compute_column_moves` +
   `SheetsClient.move_columns`. Requires the tab's header to already be
   an exact permutation of the target order (run steps 1-2 first) --
   `compute_column_moves` raises loudly on a mismatch rather than
   reordering a tab that's drifted from what's expected. Safe to run
   last: a move only ever relocates a column, formulas and all, so it
   never needs to happen before a column's content is already correct.
4. `resync_header_repeats` -- overwrites every repeated header row
   (Lineups re-prints its header once per lineup slot for readability)
   with the primary header row, verbatim. Belt-and-suspenders: a
   `moveDimension` call only ever relocates a column identically across
   every row, so it can't itself desync a repeat row from the primary
   one -- but a PRE-EXISTING drift (found on the template's Lineups tab
   during Phase 3's own verification, invisible to `doctor`'s narrow
   "column A says Name" check) survives a reorder unchanged instead of
   self-healing, unless something explicitly re-copies the header. This
   step is that something.

`migrate_tab_to_designed_order` deliberately does NOT let step 1 create a
`sheet_columns.LINKED_COLUMNS` header -- see its own docstring for why
that specific ordering trap is the one this module exists to avoid.
"""

from __future__ import annotations

from dfs.column_reorder import compute_column_moves
from dfs.sheet_columns import LINKED_COLUMNS
from dfs.sheet_links import link_edge_columns
from dfs.sheet_native_links import rewrite_native_lookup_columns
from dfs.sheets import SheetsClient, column_letter


def provision_missing_columns(
    client: SheetsClient,
    tab: str,
    target_order: list[str],
    *,
    header_row: int = 1,
    header_repeats_at: list[int] | None = None,
) -> list[str]:
    """Ensures every name in `target_order` exists somewhere in `tab`'s
    header row, appending (blank header text, no data) whatever's
    missing, in `target_order`'s own relative order. Returns the names
    actually created -- empty if the header already had everything.
    Idempotent: re-running after the columns have since been reordered
    elsewhere finds every name already present and creates nothing.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []

    missing = [name for name in target_order if name not in header]
    if not missing:
        return []

    start_col = column_letter(len(header))
    end_col = column_letter(len(header) + len(missing) - 1)
    client.ensure_column_capacity(tab, len(header) + len(missing))
    client.update_range(tab, f"{start_col}{header_row}:{end_col}{header_row}", [missing])
    for row_num in header_repeats_at or []:
        client.update_range(tab, f"{start_col}{row_num}:{end_col}{row_num}", [missing])
    return missing


def rename_header_column(
    client: SheetsClient,
    tab: str,
    old_name: str,
    new_name: str,
    *,
    header_row: int = 1,
    header_repeats_at: list[int] | None = None,
) -> bool:
    """A pure text rename of one header cell, IN PLACE -- no position
    changes, so this is deliberately NOT `provision_missing_columns` +
    `link_edge_columns`. Every column-finding function in this codebase
    (`link_edge_columns`, `provision_missing_columns`, `polish_builder_
    tab`, ...) locates a column by NAME, so simply changing a name on the
    Python side (e.g. Section F's `ImpMove` -> `ImpliedMove`) makes every
    one of them see the OLD sheet text as "genuinely missing" the moment
    they next run -- `link_edge_columns` in particular then tries to
    APPEND a brand-new column under the new name, past the tab's current
    width (hitting `ensure_column_capacity`'s exact "exceeds grid limits"
    failure mode if the tab was already fully linked, found live running
    this), while the real, formula-bearing OLD column sits orphaned under
    its old name. Call this FIRST, before any reorder/link/provision step
    touches a renamed column, so every later name-based lookup finds the
    new name already sitting in the column that was always there.

    No-ops (returns False) if `old_name` isn't present -- either the
    rename already happened, or this tab predates the column entirely.
    Returns True if a cell was actually rewritten.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []
    if old_name not in header:
        return False

    col = column_letter(header.index(old_name))
    client.update_range(tab, f"{col}{header_row}", [[new_name]])
    for row_num in header_repeats_at or []:
        repeat_row = client.read_range(tab, f"A{row_num}:{row_num}")
        repeat_header = list(repeat_row[0]) if repeat_row else []
        if old_name in repeat_header:
            repeat_col = column_letter(repeat_header.index(old_name))
            client.update_range(tab, f"{repeat_col}{row_num}", [[new_name]])
    return True


def resync_header_repeats(
    client: SheetsClient, tab: str, *, header_row: int = 1, header_repeats_at: list[int] | None = None
) -> int:
    """Overwrites every `header_repeats_at` row with the CURRENT primary
    header row, verbatim. Repeated header rows are purely cosmetic labels
    (Lineups re-prints its header once per lineup slot so it stays
    readable while scrolling); nothing reads them except `doctor`'s own
    narrow "column A says Name" check, which is why a real drift here can
    go unnoticed for a long time. Found on the template's Lineups tab
    during Phase 3's own verification pass: every one of its ~19 repeat
    rows had drifted from row 11 -- pre-existing, unrelated to anything
    this reorder did (a `moveDimension` call only ever relocates a
    column, applying the identical operation to every row, so it can
    carry an existing drift forward but can't create one) -- just never
    caught, since `doctor` doesn't check past column A there.
    `migrate_tab_to_designed_order` calls this unconditionally whenever
    `header_repeats_at` is given, specifically so a stale drift like this
    self-heals on every future reorder instead of silently persisting.
    Returns the number of rows rewritten.
    """
    if not header_repeats_at:
        return 0
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []
    if not header:
        return 0

    end_col = column_letter(len(header) - 1)
    for row_num in header_repeats_at:
        client.update_range(tab, f"A{row_num}:{end_col}{row_num}", [header])
    return len(header_repeats_at)


def reorder_tab_columns(
    client: SheetsClient, tab: str, target_order: list[str], *, header_row: int = 1
) -> list[tuple[int, int]]:
    """Physically reorders `tab`'s columns to match `target_order`
    exactly, via a `SheetsClient.move_columns` call per move
    `compute_column_moves` computes. Every name in `target_order` must
    already be present in the header (run `provision_missing_columns`
    first) -- `compute_column_moves` raises `ValueError` naming the
    mismatch otherwise, rather than silently reordering a tab whose
    column set doesn't actually match what's expected. Returns the moves
    applied, in order, for the changelog.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []

    moves = compute_column_moves(header, target_order)
    for from_index, to_index in moves:
        client.move_columns(tab, from_index=from_index, to_index=to_index)
    return moves


def migrate_tab_to_designed_order(
    client: SheetsClient,
    tab: str,
    target_order: list[str],
    *,
    name_blocks: list[tuple[int, int]],
    edge_tab: str,
    header_row: int = 1,
    header_repeats_at: list[int] | None = None,
    rewrite_native: bool,
) -> list[str]:
    """The full Phase 3 migration for one tab, run in the one order that's
    actually safe: provisioning `target_order`'s native/placeholder
    columns FIRST but deliberately EXCLUDING `sheet_columns.
    LINKED_COLUMNS` from that step -- `link_edge_columns` must be the only
    thing that ever creates a linked column's header, because it also
    refreshes every linked column's formula the moment it creates ANY of
    them (exactly what's needed right after `derived.EDGE_COLUMNS` has
    been reordered, since the ten already-linked columns' hardcoded
    VLOOKUP indices are now stale). If this function provisioned linked
    headers too, `link_edge_columns` would see all sixteen names already
    present, treat the tab as already fully linked, and skip writing
    formulas entirely -- silently leaving ten stale, wrong-index formulas
    in place. Order after that: link EdgeRaw in, optionally regenerate the
    native PlayerPoolRaw-lookup formulas (`rewrite_native=True` for Player
    Pool/Lineups; PlayerPoolRaw itself has no such formulas, so its own
    call passes False), then physically move every column into
    `target_order` last -- a move only ever relocates a column, so it's
    safe to run after every column's formula content is already correct.

    Returns the human-readable report line from each step, in order, for
    the changelog.
    """
    native_target = [name for name in target_order if name not in LINKED_COLUMNS]
    created = provision_missing_columns(
        client, tab, native_target, header_row=header_row, header_repeats_at=header_repeats_at
    )
    report = [f"{tab}: provisioned {len(created)} native placeholder column(s): {created}"]

    report.append(
        link_edge_columns(
            client, tab, name_blocks, edge_tab, header_row=header_row, header_repeats_at=header_repeats_at
        )
    )
    if rewrite_native:
        report.append(rewrite_native_lookup_columns(client, tab, name_blocks, header_row=header_row))

    moves = reorder_tab_columns(client, tab, target_order, header_row=header_row)
    report.append(f"{tab}: applied {len(moves)} column move(s)")

    resynced = resync_header_repeats(client, tab, header_row=header_row, header_repeats_at=header_repeats_at)
    if resynced:
        report.append(f"{tab}: resynced {resynced} repeated header row(s) to match the primary header")

    return report


def remove_header_columns(client: SheetsClient, tab: str, names: list[str], *, header_row: int = 1) -> str:
    """Permanently delete each column in `names` from `tab`, found by
    current header text (never a hardcoded position). Unlike
    `rename_header_column`'s per-repeat-row patching, a real
    `deleteDimension` on COLUMNS removes the column across the tab's
    entire height in one shot, so a repeated header row (Lineups reprints
    its header once per lineup block) needs no separate handling here --
    the column is just gone from every row at once, repeats included.

    A name genuinely absent from the header is skipped, not an error --
    same idempotent-migration contract as `sheet_pool_deck.
    remove_pool_deck`: safe to re-run on a tab that's already had this
    applied, or one that never had these columns at all (e.g. a sheet
    built after this migration already shipped). Deletes right-to-left
    (highest index first) so removing one column can't shift the
    still-pending indices for the others -- the one real hazard in
    deleting several columns from the same header in a single pass.
    """
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []
    indices = sorted((header.index(name) for name in names if name in header), reverse=True)
    if not indices:
        return f"{tab}: none of {names} present -- skipped"
    for index in indices:
        client.delete_columns(tab, at_index=index, count=1)
    removed = [name for name in names if name in header]
    return f"{tab}: removed {len(indices)} column(s): {removed}"
