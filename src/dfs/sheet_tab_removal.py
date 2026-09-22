"""Phase 6 Part 4b (2026-09-22): five tabs Sam confirmed he does not use --
`Scratch` (a blank drafting grid, no formulas) and the `EntriesRaw`/
`GPPin`/`DKLineupsRaw`/`DKLineupsFinal` hand-paste DK-contest-history chain
(superseded by the CLI's own CSV-based `dfs week close --csv` path) -- are
retired. `dfs` never read or wrote any of the five except to clear/style
them, so removal is mechanical: no formula anywhere else references them
(grepped to confirm before this module was written).

Asymmetric on purpose, per Sam's own instruction: **template -- delete the
tabs outright** (a future weekly copy starts clean). **Live sheet -- hide,
don't delete** (`EntriesRaw` may hold real pasted contest history a delete
can't recover; hiding costs nothing since `dfs sync` already treats hidden
tabs as fully readable/writable). Both are handled by the same function
here, selected by the `mode` argument, so the two calls this needs (one
per sheet) can't drift into different tab lists.

See `docs/PROMPT_DATA.md` for where a past entry's roster-slot detail --
the one thing only `EntriesRaw` held -- lives now that the tab is gone
(the DK export CSVs already on disk), and CONTRIBUTING.md's structural
changelog for the full before/after.

Also cleans the `Instructions` tab's own rows describing these five --
unlike the tabs themselves, the documentation should read correctly on
BOTH sheets regardless of delete-vs-hide, since a hidden tab a reader
can't see shouldn't still have an instructions row telling them to use
it. Matched by column A text, not a hardcoded row number (Instructions'
row numbers drift as other rows are added/removed over time).
"""

from __future__ import annotations

from typing import Literal

from dfs.sheets import SheetsClient

RETIRED_TABS = ["Scratch", "EntriesRaw", "GPPin", "DKLineupsRaw", "DKLineupsFinal"]

_INSTRUCTIONS_TAB = "Instructions"
_INSTRUCTIONS_SCAN_ROWS = 300


def _instructions_row_is_retired(cell_text: str) -> bool:
    """True if every "/"-separated tab name in this Instructions row's
    column A is one of `RETIRED_TABS` -- covers both a single-tab row
    ("Scratch") and a combined row ("DKLineupsRaw / DKLineupsFinal")
    without hardcoding either exact string."""
    if not cell_text:
        return False
    tokens = [t.strip() for t in cell_text.split("/")]
    return all(t in RETIRED_TABS for t in tokens)


def clean_instructions_tab(client: SheetsClient, tab: str = _INSTRUCTIONS_TAB) -> str:
    """Deletes every Instructions row whose column A names only retired
    tabs. Idempotent: re-running after the rows are already gone finds
    nothing to delete."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    rows = client.read_range(tab, f"A1:A{_INSTRUCTIONS_SCAN_ROWS}")
    to_delete = [i + 1 for i, row in enumerate(rows) if row and _instructions_row_is_retired(row[0])]
    for row_num in reversed(to_delete):
        client.delete_rows(tab, at_row=row_num, count=1)
    if not to_delete:
        return f"{tab}: no retired-tab rows found -- skipped"
    return f"{tab}: removed {len(to_delete)} row(s) describing retired tabs"


def remove_retired_tabs(client: SheetsClient, *, mode: Literal["delete", "hide"]) -> list[str]:
    """`mode="delete"` for the template, `mode="hide"` for the live sheet --
    see this module's docstring for why they differ. Idempotent either
    way: a tab already gone (delete) or already hidden (hide) is skipped,
    so this is safe to re-run against a sheet already fixed. Also cleans
    `Instructions`' own rows for these five, regardless of mode."""
    results = []
    for tab in RETIRED_TABS:
        if not client.tab_exists(tab):
            results.append(f"{tab}: already absent -- skipped")
            continue
        if mode == "delete":
            client.delete_tab(tab)
            results.append(f"{tab}: deleted")
        else:
            client.set_tab_properties(tab, hidden=True)
            results.append(f"{tab}: hidden")
    results.append(clean_instructions_tab(client))
    return results
