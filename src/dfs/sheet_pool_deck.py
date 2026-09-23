"""Phase 5 (Section E/user feedback, 2026-09-16): the pool deck --
frozen rows at the top of `Lineups` holding a sortable/filterable window
into `Player Pool` -- is REMOVED, not shrunk. Sam, after a week building
real lineups against it: "I've used it week 1 and it was a pain... we
either need to really reconsider it or get rid of it," and on the one
piece worth keeping (a "where is this player" jump, no browsing) --
"Where do they sit in the pool doesn't get me much. Cut it."

This superseded `docs/planning/ROADMAP.md`'s own "shrink to a jump control"
proposal (written before this feedback landed) -- full removal, not a
smaller deck. The pool-browsing job it existed for is now covered by
Player Pool's own colour scales/chips/`Used`/`In` columns (Phase 5
Sections B/C), which is exactly why Sam could tell within a week that the
separate window had stopped earning its screen space.

What this module now does: `remove_pool_deck`, a one-time, idempotent
migration deleting the `DECK_ROWS`-row block this module used to build
(and the hidden `PoolSort` helper tab it depended on) -- symmetric with
the removed `add_pool_deck`'s own migration-state detection, so it's
skipped cleanly on a sheet that was never migrated, or already has been.

Everything this module used to also do -- write row 1's Position/Sort/
Start-at controls, materialize `PoolSort`, window formulas, colour-scale
helper rows, `H1`'s "how many lineups this week" control -- is gone or
moved. `H1` (Phase 5A's Exposure divisor) moved to `Exposure!H1` itself
(`sheet_views.py`'s own `LINEUP_COUNT_CELL`/`DEFAULT_LINEUP_COUNT`) --
that control was never really about the deck, just parked in its row 1
for lack of anywhere better, and Exposure is the one tab that actually
reads it. See CONTRIBUTING.md's structural changelog for the full
before/after on `LINEUPS_NAME_BLOCKS` (every row shifts up by
`DECK_ROWS`, the fourth such shift) and the list of every symbol that
depended on the deck's existence.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient

DECK_ROWS = 10
POOL_SORT_TAB = "PoolSort"


def _cell_equals(client: SheetsClient, tab: str, a1: str, expected: str) -> bool:
    values = client.read_range(tab, a1)
    return bool(values and values[0] and values[0][0] == expected)


def remove_pool_deck(client: SheetsClient, lineups_tab: str, *, pool_sort_tab: str = POOL_SORT_TAB) -> str:
    """Deletes the `DECK_ROWS`-row pool deck from `lineups_tab` (a real
    `deleteDimension`, so every block below it shifts up by `DECK_ROWS`)
    and the hidden `PoolSort` tab it depended on. Idempotent: a sheet
    whose row 1 already reads "Name" (the real header, not the deck's own
    `B1` position-dropdown row) is left alone -- either never migrated to
    the deck in the first place, or already migrated back off it.
    """
    if _cell_equals(client, lineups_tab, "A1", "Name"):
        return f"{lineups_tab}: no pool deck present -- skipped"

    client.delete_rows(lineups_tab, at_row=1, count=DECK_ROWS)
    removed_pool_sort = False
    if client.tab_exists(pool_sort_tab):
        client.delete_tab(pool_sort_tab)
        removed_pool_sort = True

    note = f", {pool_sort_tab!r} tab removed" if removed_pool_sort else ""
    return f"{lineups_tab}: pool deck removed ({DECK_ROWS} row(s) deleted){note}"
