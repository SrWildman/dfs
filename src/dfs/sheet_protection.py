"""Task 5.1 -- `dfs setup protect`: warning-only protection (Sheets'
"Protect sheet... except certain cells", never a hard lock) on every tab
whose cells are entirely formula-driven, so a stray keystroke gets a
dismissible warning instead of silently overwriting a working formula.

`warningOnly=True` throughout, on every tab: a hard lock would need
Sam's own account added as an editor exception every time he legitimately
needs to fix something by hand, and this project already leans on
dismissible warnings elsewhere (`ONE_OF_RANGE` validation, non-strict)
rather than hard blocks -- a mistake should stay reversible, not become a
support ticket against his own sheet. It also never blocks this client's
own service-account writes, so leaving protection on every formula tab
is safe for every future `dfs sync`/`dfs setup polish` run.

Exactly four things are typed by hand in the whole workbook (see
`sheet_style.py`'s visual-grammar docstring): EdgeRaw's Pool column,
Player Pool's own add-a-player control cell (A3, `sheet_pool_control.py`
-- replaced the old separate `Pool Picks` tab), Lineups' block column A,
and Exposure's Target column -- plus the pool deck's B1/D1/F1 controls.
EdgeRaw is deliberately absent from `protect_workbook` below: its ENTIRE
point is that Sam types into it (the Pool column) constantly, protecting
it would mean protecting the one thing this task exists to make
typeable. Player Pool, Exposure and Lineups all need the
`unprotectedRanges` carve-out instead of a blanket protection for the
same reason.
"""

from __future__ import annotations

from dfs.sheet_links import PLAYER_POOL_RAW_TAB
from dfs.sheet_pool_deck import POOL_SORT_TAB
from dfs.sheets import SheetsClient
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_CONTROL_ROW

# Whole tab, no exceptions -- entirely computed or spilled-array output,
# nothing here is ever typed by hand.
FULLY_PROTECTED_TABS = [
    PLAYER_POOL_RAW_TAB,
    "Board",
    "Slate Grid",
    "Movement",
    POOL_SORT_TAB,
]

_FORMULA_DESCRIPTION = "Formula-driven -- check before typing here (dfs setup protect)"


def protect_workbook(
    client: SheetsClient,
    *,
    exposure_tab: str = "Exposure",
    player_pool_tab: str = "Player Pool",
    lineups_tab: str,
    name_blocks: list[tuple[int, int]] = LINEUPS_NAME_BLOCKS,
) -> list[str]:
    """Re-runnable: every tab's existing protected ranges are cleared
    before this tab's own are (re-)added, so running twice never stacks
    duplicate warning dialogs on the same edit."""
    results = []

    for tab in FULLY_PROTECTED_TABS:
        if not client.tab_exists(tab):
            results.append(f"{tab}: not present -- skipped")
            continue
        client.clear_protected_ranges(tab)
        client.protect_sheet(tab, description=_FORMULA_DESCRIPTION)
        results.append(f"{tab}: whole tab protected (warning-only)")

    if not client.tab_exists(player_pool_tab):
        results.append(f"{player_pool_tab}: not present -- skipped")
    else:
        control_cell = f"B{PLAYER_POOL_CONTROL_ROW}"
        client.clear_protected_ranges(player_pool_tab)
        client.protect_sheet(
            player_pool_tab,
            unprotected_ranges=[control_cell],
            description=f"{_FORMULA_DESCRIPTION} (except the add-a-player control, {control_cell})",
        )
        results.append(f"{player_pool_tab}: whole tab protected except the add-a-player control")

    if not client.tab_exists(exposure_tab):
        results.append(f"{exposure_tab}: not present -- skipped")
    else:
        client.clear_protected_ranges(exposure_tab)
        client.protect_sheet(
            exposure_tab,
            unprotected_ranges=["F:F"],
            description=f"{_FORMULA_DESCRIPTION} (except Target, column F)",
        )
        results.append(f"{exposure_tab}: whole tab protected except Target (F)")

    if not client.tab_exists(lineups_tab):
        results.append(f"{lineups_tab}: not present -- skipped")
    else:
        client.clear_protected_ranges(lineups_tab)
        unprotected = ["B1", "D1", "F1", *[f"A{start}:A{end}" for start, end in name_blocks]]
        client.protect_sheet(
            lineups_tab,
            unprotected_ranges=unprotected,
            description=f"{_FORMULA_DESCRIPTION} (except the deck controls and each block's Name column)",
        )
        results.append(f"{lineups_tab}: whole tab protected except deck controls + block column A")

    return results
