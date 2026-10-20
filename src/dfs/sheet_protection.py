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

Exactly five things are typed by hand in the whole workbook (see
`sheet_style.py`'s visual-grammar docstring): EdgeRaw's Pool column,
Player Pool's own add-a-player control cell (A3, `sheet_pool_control.py`
-- replaced the old separate `Pool Picks` tab), Lineups' block column A (plus each block's Cash/GPP marker),
Exposure's Target column plus its `LINEUP_COUNT_CELL` (moved here
from the pool deck's own B1/D1/F1 controls when the deck was removed
entirely, Phase 5, 2026-09-16), plus DK Upload's Lineup dropdown (`protect_dk_upload`: the
one choice Sam makes per contest entry) and the Suggested tab's lock ticks and Name column (the
decision cells, the rules box and the names he types over a build's players:
`sheet_suggested.typed_ranges`). EdgeRaw is deliberately absent from
`protect_workbook` below: its ENTIRE point is that Sam types into it (the
Pool column) constantly, protecting it would mean protecting the one
thing this task exists to make typeable. Player Pool, Exposure, Lineups and Suggested all need the
`unprotectedRanges` carve-out instead of a blanket protection for the same reason.
"""

from __future__ import annotations

from dfs import edge_finder_tab as eft
from dfs.cockpit import COCKPIT_TAB
from dfs.sheet_contests import CONTESTS_TAB
from dfs.sheet_dk_upload import unprotected_ranges as dk_upload_unprotected_ranges
from dfs.sheet_links import PLAYER_POOL_RAW_TAB
from dfs.sheet_pool_control import control_cells
from dfs.sheet_suggested import PROTECTION_DESCRIPTION as SUGGESTED_DESCRIPTION
from dfs.sheet_suggested import TAB as SUGGESTED_TAB
from dfs.sheet_suggested import typed_ranges as suggested_typed_ranges
from dfs.sheet_views import BOARD_TAB, LINEUP_COUNT_CELL
from dfs.sheets import SheetsClient, column_letter
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW

# Whole tab, no exceptions -- entirely computed or spilled-array output,
# nothing here is ever typed by hand.
FULLY_PROTECTED_TABS = [
    PLAYER_POOL_RAW_TAB,
    "Slate Grid",
    "Movement",
    CONTESTS_TAB,  # rebuilt by `dfs week close` / `dfs contests update`; nothing on it is typed
    COCKPIT_TAB,  # rewritten whole by `dfs sync --live` / `late-swap` / `setup cockpit`; nothing is typed
]
# Deliberately NOT protected (Sam, 2026-10-09): opening or closing a row group counts as an edit of
# the rows it hides, so whole-tab protection showed a "Heads up" dialog on every fold. Their safety is
# elsewhere: the sync rewrites the Edge Finder every run, `dfs setup build-views` rebuilds the Board,
# the Apps Script puts a Pool cell's formula back after an edit, and `dfs doctor` flags a Pool cell
# that holds a typed value. Any protection a previous run left on them is removed.
UNPROTECTED_VIEW_TABS = [eft.EDGE_FINDER_TAB, BOARD_TAB]

_FORMULA_DESCRIPTION = "Formula-driven -- check before typing here (dfs setup protect)"


def lineups_unprotected_ranges(name_blocks: list[tuple[int, int]] = LINEUPS_NAME_BLOCKS) -> list[str]:
    """What Sam types on Lineups: each block's nine Name cells (column A) and the Cash / GPP marker, which is
    column A of the block's Total row, the row right under them (`weekly_reset.LINEUPS_TOTALS_ROWS`). The
    marker was behind the warning until 2026-10-17: it is a dropdown he sets on every lineup."""
    return [f"A{start}:A{end}" for start, end in name_blocks] + [f"A{end + 1}" for _start, end in name_blocks]


def protect_dk_upload(client: SheetsClient, tab: str) -> str:
    """DK Upload: the whole tab warns on an edit (every column but one is written by `dfs entries fill`)
    except the Lineup dropdown, the one cell Sam types per contest entry
    (`sheet_dk_upload.unprotected_ranges`)."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    header = client.read_range(tab, "A1:1")
    if "Lineup" not in [str(c).strip() for c in (header[0] if header else [])]:
        return f"{tab}: the entries layout is not built yet (run `dfs setup dk-upload`) -- left as it is"
    client.clear_protected_ranges(tab)
    exempt = dk_upload_unprotected_ranges()
    client.protect_sheet(
        tab,
        unprotected_ranges=exempt,
        description=f"{_FORMULA_DESCRIPTION} (except the Lineup dropdown, {', '.join(exempt)})",
    )
    return f"{tab}: whole tab protected except the Lineup dropdown ({', '.join(exempt)})"


def protect_workbook(
    client: SheetsClient,
    *,
    exposure_tab: str = "Exposure",
    player_pool_tab: str = "Player Pool",
    lineups_tab: str,
    name_blocks: list[tuple[int, int]] = LINEUPS_NAME_BLOCKS,
    dk_upload_tab: str | None = None,
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

    for tab in UNPROTECTED_VIEW_TABS:
        if not client.tab_exists(tab):
            results.append(f"{tab}: not present -- skipped")
            continue
        client.clear_protected_ranges(tab)
        results.append(
            f"{tab}: left unprotected (folding its groups must not warn); any old protection removed"
        )

    if not client.tab_exists(SUGGESTED_TAB):
        results.append(f"{SUGGESTED_TAB}: not present -- skipped")
    else:
        # Written by `dfs lineups suggest` (Copy builds to Lineups only reads it) and iterated on by hand: the
        # lock column and the Name column (decisions, rules box, typed-over names) are carved out. Whole
        # columns, because the rows move with every run.
        client.clear_protected_ranges(SUGGESTED_TAB)
        client.protect_sheet(
            SUGGESTED_TAB, unprotected_ranges=suggested_typed_ranges(), description=SUGGESTED_DESCRIPTION
        )
        results.append(
            f"{SUGGESTED_TAB}: whole tab protected except the lock column and the Name column "
            "(decisions, rules box, typed names)"
        )

    if not client.tab_exists(player_pool_tab):
        results.append(f"{player_pool_tab}: not present -- skipped")
    else:
        # The Pool column and the add-a-player box (found from the header, never a fixed letter).
        header_rows = client.read_range(
            player_pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"
        )
        header = header_rows[0] if header_rows else []
        pool_col = column_letter(header.index("Pool")) if "Pool" in header else "A"
        box = control_cells(header)["input"]
        client.clear_protected_ranges(player_pool_tab)
        client.protect_sheet(
            player_pool_tab,
            unprotected_ranges=[f"{pool_col}:{pool_col}", box],
            description=f"{_FORMULA_DESCRIPTION} (except the Pool column and the add-a-player box, {box})",
        )
        results.append(
            f"{player_pool_tab}: whole tab protected except the Pool column and the add-a-player box"
        )

    if not client.tab_exists(exposure_tab):
        results.append(f"{exposure_tab}: not present -- skipped")
    else:
        client.clear_protected_ranges(exposure_tab)
        client.protect_sheet(
            exposure_tab,
            unprotected_ranges=["F:F", LINEUP_COUNT_CELL],
            description=f"{_FORMULA_DESCRIPTION} (except Target, column F, and {LINEUP_COUNT_CELL})",
        )
        results.append(f"{exposure_tab}: whole tab protected except Target (F) and {LINEUP_COUNT_CELL}")

    if not client.tab_exists(lineups_tab):
        results.append(f"{lineups_tab}: not present -- skipped")
    else:
        client.clear_protected_ranges(lineups_tab)
        unprotected = lineups_unprotected_ranges(name_blocks)
        client.protect_sheet(
            lineups_tab,
            unprotected_ranges=unprotected,
            description=f"{_FORMULA_DESCRIPTION} (except each block's Name column and its Cash/GPP marker)",
        )
        results.append(
            f"{lineups_tab}: whole tab protected except each block's Name column and Cash/GPP marker"
        )

    if dk_upload_tab:
        results.append(protect_dk_upload(client, dk_upload_tab))

    return results
