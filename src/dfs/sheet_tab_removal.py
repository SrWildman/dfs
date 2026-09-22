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
"""

from __future__ import annotations

from typing import Literal

from dfs.sheets import SheetsClient

RETIRED_TABS = ["Scratch", "EntriesRaw", "GPPin", "DKLineupsRaw", "DKLineupsFinal"]


def remove_retired_tabs(client: SheetsClient, *, mode: Literal["delete", "hide"]) -> list[str]:
    """`mode="delete"` for the template, `mode="hide"` for the live sheet --
    see this module's docstring for why they differ. Idempotent either
    way: a tab already gone (delete) or already hidden (hide) is skipped,
    so this is safe to re-run against a sheet already fixed."""
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
    return results
