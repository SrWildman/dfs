"""Pure diffing for `dfs sync --live`'s "what changed" report.

`EdgeRaw`'s `Flags` column already aggregates every fast-moving signal that
matters (`OUT` from DK's `Status`, `WIND` from weather, `LINE↑`/`LINE↓` from
odds, plus `LEVERAGE`/`CHALK`) -- see derived.py's `_flags_for_row`. Diffing
just that one column between two `EdgeRaw` snapshots surfaces the meaningful
subset of what a live re-sync could have changed, without re-deriving a
separate diff per source that feeds it (a second `Status`-diff, a second
`Wind`-diff, etc. would all just restate what already shows up as a `Flags`
change here). Deliberately reads `Flags` (everything that fired), not the
hidden single-priority `Flag` (Part 7.9) -- a diff report should surface a
cleared secondary condition too, not just a change in the top one.
"""

from __future__ import annotations

import pandas as pd

_JOIN_COLUMNS = ["Id", "Name", "Position", "Team"]


def diff_edge_flags(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Every player present in `current` whose `Flags` differs from what it
    was in `previous` (joined on `Id`; a player with no `previous` row --
    new to the slate -- is treated as having had no flags before). Sorted
    with newly-flagged players first, since "this player now needs a look"
    is more actionable than "this player's flags cleared"."""
    prev_flags = previous[["Id", "Flags"]].rename(columns={"Flags": "OldFlag"})
    cur = current[_JOIN_COLUMNS + ["Flags"]].rename(columns={"Flags": "NewFlag"})

    merged = cur.merge(prev_flags, on="Id", how="left")
    merged["OldFlag"] = merged["OldFlag"].fillna("")
    merged["NewFlag"] = merged["NewFlag"].fillna("")

    changed = merged[merged["OldFlag"] != merged["NewFlag"]].copy()
    changed["_newly_flagged"] = changed["NewFlag"].ne("")
    changed = changed.sort_values(by=["_newly_flagged", "Name"], ascending=[False, True]).drop(
        columns="_newly_flagged"
    )
    return changed.reset_index(drop=True)
