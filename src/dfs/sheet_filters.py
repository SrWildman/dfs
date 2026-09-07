"""`dfs setup add-filters` (Task 3): per-user, non-destructive sort/
filter views on tabs that are safe for it.

Filter views (Sheets' Data > Filter views), never a plain filter: a
filter view sorts/filters what one viewer sees without moving a single
stored cell. The plain "Create a filter" button is the opposite -- one
filter per sheet, applied for every viewer, and it physically reorders/
hides stored rows. Confirmed empirically before this shipped, not just
assumed: a filter view added to Slate Grid, sorted by Total descending
from inside the view, left every underlying formula byte-for-byte
identical on a read-back (see CONTRIBUTING.md's changelog).

NOT applied to Player Pool, Lineups, PlayerPoolRaw or Board -- each has
row-position-dependent structure (per-position/per-lineup blocks, a
VLOOKUP-by-row hub, three side-by-side panels) where the pool deck's own
sort/filter (Task 4) is the intended browsing mechanism instead. Sorting
one of those tabs' rows inside a filter view wouldn't touch the stored
formulas, but it WOULD visually separate a lineup's picks from its own
totals row, or one position block's players from their own block -- a
confusing view even though nothing underneath actually moved.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_style import EDGE_ROWS
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN

# Tabs whose entire real range gets one plain filter view (no preset
# criteria) -- a sortable/filterable window, nothing more. Each is either
# plain synced/pasted values (EdgeRaw is handled separately, with its own
# four named views) or, for the four view/log tabs here, laid out one row
# per game/player/week already -- sorting inside a filter view reorders
# the DISPLAY only, never the formula or pasted value underneath it.
FULL_RANGE_FILTER_TABS: list[tuple[str, str]] = [
    ("Slate Grid", "A1:J20"),
    ("Movement", "A3:E60"),
    ("Exposure", "A1:J180"),
    ("Results", "A1:J30"),
    ("SoSQB", "A1:F33"),
    ("SoSRB", "A1:F33"),
    ("SoSWr", "A1:F33"),
    ("SoSTE", "A1:F33"),
    ("SoSDef", "A1:F33"),
    ("SoSComb", "A1:G40"),
]

_FILTER_VIEW_TITLE = "All"


def _edge_col_index(name: str) -> int | None:
    if name not in EDGE_COLUMNS:
        return None
    return EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET


def add_edge_filter_views(client: SheetsClient, edge_tab: str) -> list[str]:
    """The four EdgeRaw filter views from Task 3.3:
    - "Pool picking" -- the full range, no preset. This is the workhorse:
      the Name column's own filter-view header gets a type-ahead search
      box for free, the primary way to find a player without scrolling
      743 rows (see Task 4.1).
    - "Leverage plays" -- Flag = LEVERAGE.
    - "Available only" -- Avail blank (no Q/OUT/IR).
    - "In my pool" -- Pool = TRUE.
    Re-runnable: each view is cleared by title before being re-added.
    """
    if not client.tab_exists(edge_tab):
        return [f"{edge_tab}: not present -- skipped"]

    last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    full_range = f"A1:{last_col}{EDGE_ROWS}"

    views: list[tuple[str, dict[int, dict] | None]] = [("Pool picking", None)]

    flag_idx = _edge_col_index("Flag")
    if flag_idx is not None:
        views.append(
            (
                "Leverage plays",
                {flag_idx: {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "LEVERAGE"}]}}},
            )
        )

    avail_idx = _edge_col_index("Avail")
    if avail_idx is not None:
        views.append(("Available only", {avail_idx: {"condition": {"type": "BLANK"}}}))

    # POOL_COLUMN is EdgeRaw's Pool checkbox column -- its 0-indexed
    # position for the Sheets API's criteria map.
    pool_idx = ord(POOL_COLUMN) - ord("A")
    views.append(
        (
            "In my pool",
            {pool_idx: {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "TRUE"}]}}},
        )
    )

    results = []
    for title, criteria in views:
        client.clear_filter_view(edge_tab, title)
        client.add_filter_view(edge_tab, title=title, a1_range=full_range, criteria=criteria)
        results.append(f"{edge_tab}: filter view {title!r} added")
    return results


def add_plain_filter_views(client: SheetsClient) -> list[str]:
    """One unfiltered, sortable filter view per tab in FULL_RANGE_FILTER_TABS.
    Skips cleanly if a tab doesn't exist -- SoSQB etc. are hand-built and
    week-to-week optional (see `sheet_style.style_sos_tab`)."""
    results = []
    for tab, a1_range in FULL_RANGE_FILTER_TABS:
        if not client.tab_exists(tab):
            results.append(f"{tab}: not present -- skipped")
            continue
        client.clear_filter_view(tab, _FILTER_VIEW_TITLE)
        client.add_filter_view(tab, title=_FILTER_VIEW_TITLE, a1_range=a1_range)
        results.append(f"{tab}: filter view {_FILTER_VIEW_TITLE!r} added over {a1_range}")
    return results


def add_all_filter_views(client: SheetsClient, edge_tab: str) -> list[str]:
    return add_edge_filter_views(client, edge_tab) + add_plain_filter_views(client)


# ---------------------------------------------------------------------------
# Basic filters (Fix 1): a VISIBLE dropdown arrow in every header cell --
# filter views above are a real feature, but they're hidden behind Data >
# Filter views and easy to never discover at all. A basic filter is Sheets'
# plain "Data > Create a filter" -- exactly one per sheet, applied for every
# viewer, and (unlike a filter view) it physically reorders the tab's
# stored rows. Safe on the same plain-value tabs a filter view is safe on;
# `setBasicFilter` always replaces whatever's already there, so this is
# naturally re-runnable with no separate clear step.
#
# Deliberately a SUBSET of FULL_RANGE_FILTER_TABS above: Slate Grid,
# Movement and Exposure are read-only formula views where the filter VIEW
# stays the only sort/search mechanism (a basic filter reordering their
# rows would still leave every formula correct, but there's no typed input
# on those tabs for a reorder to visually separate from its own label the
# way it would on a positional block, so this is a judgement call to keep
# their existing mechanism the only one, not a structural constraint).
BASIC_FILTER_PLAIN_TABS: list[tuple[str, str]] = [
    ("Results", "A1:J30"),
    ("SoSQB", "A1:F33"),
    ("SoSRB", "A1:F33"),
    ("SoSWr", "A1:F33"),
    ("SoSTE", "A1:F33"),
    ("SoSDef", "A1:F33"),
    ("SoSComb", "A1:G40"),
]


def add_basic_filters(client: SheetsClient, *, edge_tab: str, pool_picks_range: str) -> list[str]:
    """EdgeRaw (its full real range) plus Pool Picks and the plain-value
    report tabs in BASIC_FILTER_PLAIN_TABS. `pool_picks_range` is passed in
    rather than hardcoded here since Pool Picks' own row layout
    (`sheet_pool_picks.py`) is its module's to own."""
    results = []

    if client.tab_exists(edge_tab):
        last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
        edge_range = f"A1:{last_col}{EDGE_ROWS}"
        client.set_basic_filter(edge_tab, edge_range)
        results.append(f"{edge_tab}: basic filter added over {edge_range}")
    else:
        results.append(f"{edge_tab}: not present -- skipped")

    if client.tab_exists("Pool Picks"):
        client.set_basic_filter("Pool Picks", pool_picks_range)
        results.append(f"Pool Picks: basic filter added over {pool_picks_range}")
    else:
        results.append("Pool Picks: not present -- skipped")

    for tab, a1_range in BASIC_FILTER_PLAIN_TABS:
        if not client.tab_exists(tab):
            results.append(f"{tab}: not present -- skipped")
            continue
        client.set_basic_filter(tab, a1_range)
        results.append(f"{tab}: basic filter added over {a1_range}")

    return results
