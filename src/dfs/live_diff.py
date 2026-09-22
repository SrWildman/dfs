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


# Phase 6, Part 3: the Board's "Queue" section. `Flags`' own "OUT" token
# (derived.OUT_STATUSES) already covers a move to OUT or IR, but NOT a
# move to "Q" -- Part 3's own spec explicitly wants all three. Salary
# changing is never reflected in Flags at all. Neither gap can be closed
# by re-using `diff_edge_flags` alone, so this adds both as independent
# triggers alongside it rather than trying to stretch that one column
# comparison to cover cases it was never built for.
_QUEUE_AVAIL_WATCH = frozenset({"OUT", "IR", "Q"})


def diff_queue_changes(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Every player in `current` who: had their `Flags` change (same join
    as `diff_edge_flags`, which already covers WIND/LINE↑/LINE↓/OUT/IR
    appearing or clearing); OR newly moved into `Avail` being OUT/IR/Q
    (a transition, not merely being OUT/IR/Q already -- a player already
    OUT last sync doesn't need to be queued again); OR whose `Salary`
    changed. A player with no `previous` row (new to the slate) is
    treated as having had no flags and the same salary, same convention
    `diff_edge_flags` already uses -- a genuinely new signal still
    surfaces (a real Flag or Avail value), but "salary changed" can't
    fire for a player who wasn't on the slate to have an old salary.

    Returns one row per changed player with a human-readable `Reason`
    summarizing every trigger that fired (a player can trip more than
    one at once), sorted with anything Avail/Flags-concerning first.
    """
    prev = previous[["Id", "Avail", "Flags", "Salary"]].rename(
        columns={"Avail": "OldAvail", "Flags": "OldFlag", "Salary": "OldSalary"}
    )
    cur = current[["Id", "Name", "Position", "Team", "Avail", "Flags", "Salary"]].rename(
        columns={"Avail": "NewAvail", "Flags": "NewFlag", "Salary": "NewSalary"}
    )

    merged = cur.merge(prev, on="Id", how="left")
    merged["OldAvail"] = merged["OldAvail"].fillna("")
    merged["OldFlag"] = merged["OldFlag"].fillna("")
    merged["OldSalary"] = merged["OldSalary"].fillna(merged["NewSalary"])

    flag_changed = merged["OldFlag"] != merged["NewFlag"]
    moved_to_watch_status = merged["NewAvail"].isin(_QUEUE_AVAIL_WATCH) & (
        merged["NewAvail"] != merged["OldAvail"]
    )
    salary_changed = merged["OldSalary"] != merged["NewSalary"]

    changed = merged[flag_changed | moved_to_watch_status | salary_changed].copy()

    def _reason(row: pd.Series) -> str:
        parts = []
        if row["NewAvail"] != row["OldAvail"] and row["NewAvail"] in _QUEUE_AVAIL_WATCH:
            parts.append(f"Avail -> {row['NewAvail']}")
        if row["OldFlag"] != row["NewFlag"]:
            parts.append(f"Flags {row['OldFlag'] or '(none)'} -> {row['NewFlag'] or '(none)'}")
        if row["OldSalary"] != row["NewSalary"]:
            parts.append(f"Salary {row['OldSalary']:g} -> {row['NewSalary']:g}")
        return "; ".join(parts)

    changed["Reason"] = changed.apply(_reason, axis=1)
    changed["_concerning"] = changed["NewAvail"].isin(_QUEUE_AVAIL_WATCH) | changed["NewFlag"].ne("")
    changed = changed.sort_values(by=["_concerning", "Name"], ascending=[False, True]).drop(
        columns="_concerning"
    )
    return changed.reset_index(drop=True)[["Id", "Name", "Position", "Team", "Reason"]]
