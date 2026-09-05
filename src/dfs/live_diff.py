"""Pure diffing for `dfs sync --live`'s "what changed" report.

`EdgeRaw`'s `Flag` column already aggregates every fast-moving signal that
matters (`OUT` from DK's `Status`, `WIND` from weather, `LINE↑`/`LINE↓` from
odds, plus `LEVERAGE`/`CHALK`) -- see derived.py's `_flag_for_row`. Diffing
just that one column between two `EdgeRaw` snapshots surfaces the meaningful
subset of what a live re-sync could have changed, without re-deriving a
separate diff per source that feeds it (a second `Status`-diff, a second
`Wind`-diff, etc. would all just restate what already shows up as a `Flag`
change here).
"""

from __future__ import annotations

import pandas as pd

_JOIN_COLUMNS = ["Id", "Name", "Position", "Team"]


def diff_edge_flags(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Every player present in `current` whose `Flag` differs from what it
    was in `previous` (joined on `Id`; a player with no `previous` row --
    new to the slate -- is treated as having had no flag before). Sorted
    with newly-flagged players first, since "this player now needs a look"
    is more actionable than "this player's flag cleared"."""
    prev_flags = previous[["Id", "Flag"]].rename(columns={"Flag": "OldFlag"})
    cur = current[_JOIN_COLUMNS + ["Flag"]].rename(columns={"Flag": "NewFlag"})

    merged = cur.merge(prev_flags, on="Id", how="left")
    merged["OldFlag"] = merged["OldFlag"].fillna("")
    merged["NewFlag"] = merged["NewFlag"].fillna("")

    changed = merged[merged["OldFlag"] != merged["NewFlag"]].copy()
    changed["_newly_flagged"] = changed["NewFlag"].ne("")
    changed = changed.sort_values(by=["_newly_flagged", "Name"], ascending=[False, True]).drop(
        columns="_newly_flagged"
    )
    return changed.reset_index(drop=True)
