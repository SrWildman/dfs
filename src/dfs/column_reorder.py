"""Phase 3: pure logic for reordering a tab's columns into a new,
designed order via a sequence of single-column moves -- kept separate
from `sheets.py`'s API wrapper per CONTRIBUTING.md's "split the pure
logic out" rule, and offline-testable without touching a real sheet.

Google Sheets has no "set this exact permutation" primitive; the only
structural operation available is `moveDimension`, which relocates one
contiguous range of columns at a time (and, critically, auto-updates
every RANGE reference elsewhere in the workbook to follow that column --
see CONTRIBUTING.md's central hazard section. It does NOT update a
hardcoded integer argument inside a formula, e.g. VLOOKUP's index -- that
still needs the separate renumbering `sheet_pool_formulas.py` handles).

`compute_column_moves` turns an arbitrary (current_order -> target_order)
permutation into a sequence of single-column moves, in the same
"remove from here, reinsert there" semantics a Python list slice
operation uses. `SheetsClient.move_columns` (see sheets.py) is
responsible for translating each move into the real Sheets API's own
`destinationIndex` convention, which is NOT the same number -- verified
empirically against a scratch tab before ever running this against a
real tab's structure, per CONTRIBUTING.md's "verify with a real API read
of the resolved value" mandate.
"""

from __future__ import annotations


def compute_column_moves(current_order: list[str], target_order: list[str]) -> list[tuple[int, int]]:
    """Returns `(from_index, to_index)` pairs (0-based), in the order
    they must be applied, that turn `current_order` into `target_order`
    -- each pair means "remove the item currently at `from_index`, then
    insert it at `to_index`", exactly matching `list.pop`/`list.insert`
    semantics (and, once translated by the caller, `moveDimension`'s).

    Walks `target_order` left to right and fixes one position at a time:
    for each target position `i`, finds where that name currently sits
    and moves it there if it isn't already -- classic selection-sort by
    single-element moves. Never moves a name already in place, so two
    already-adjacent-in-the-right-order columns produce zero moves
    between them.

    Raises `ValueError` if the two orders aren't the same multiset of
    names (a silent length/membership mismatch here would otherwise
    surface as a confusing Sheets API error, or worse, a quietly wrong
    permutation).
    """
    if sorted(current_order) != sorted(target_order):
        current_extra = set(current_order) - set(target_order)
        target_extra = set(target_order) - set(current_order)
        raise ValueError(
            "current_order and target_order must contain exactly the same names -- "
            f"only in current: {sorted(current_extra)}, only in target: {sorted(target_extra)}"
        )

    order = list(current_order)
    moves: list[tuple[int, int]] = []
    for i, name in enumerate(target_order):
        j = order.index(name)
        if j != i:
            order.pop(j)
            order.insert(i, name)
            moves.append((j, i))
    assert order == target_order  # noqa: S101 - internal self-check, not a public contract
    return moves


def group_into_contiguous_runs(names: list[str], columns: dict[str, int]) -> list[list[str]]:
    """Groups `names` into runs that sit at consecutive column indices
    (per `columns`), in column order -- so a caller writing formulas into
    a designed, partially-interleaved order (`sheet_links.
    link_edge_columns`, `sheet_native_links.rewrite_native_lookup_columns`)
    can still batch one `update_range` call per contiguous run instead of
    one per individual column, the same way the old fully-contiguous
    layout always did. A name that ends up with no neighbor in `names`
    becomes its own one-item run; never merges two names whose sheet
    columns are adjacent only by coincidence -- `names`' own membership is
    what defines a run, not just numeric adjacency in `columns`.
    """
    ordered = sorted(names, key=lambda name: columns[name])
    runs: list[list[str]] = []
    for name in ordered:
        if runs and columns[runs[-1][-1]] + 1 == columns[name]:
            runs[-1].append(name)
        else:
            runs.append([name])
    return runs
