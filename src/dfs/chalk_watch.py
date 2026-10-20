"""Chalk you are not holding: a heavily owned player in none of your lineups.

Sam, 2026-10-19: he missed a player owned about 70% and got buried. In a double-up the field is chalkier
than the large-field projection says (`dups.DOUBLE_UP_OWN_CURVE`: projected 19% -> 27% owned, 31% -> 55%), so
a cash lineup set is warned when a player's EXPECTED double-up ownership reaches `CASH_EXPECTED_OWN` (the
same bar as a projected Own% of `CASH_PROJECTED_OWN`, which is what the sheet reads), a GPP set only at the
higher `GPP_PROJECTED_OWN` (GPP projections are calibrated). A warning, never a filter: leaving the chalk
out is sometimes the point of a GPP build.

Pure: takes the edge frame (`Name`, `Position`, `Team`, `Own%`, `Avail`) and the names in each lineup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from dfs.derived import OUT_STATUSES
from dfs.sim import dups

CASH_EXPECTED_OWN = 0.20  # expected double-up ownership (`dups.double_up_ownership`) that counts as chalk
# ... as a projected (large-field) Own%: the same bar for the sheet, whose formula reads the projection
CASH_PROJECTED_OWN = round(dups.projected_for_double_up(CASH_EXPECTED_OWN), 3)
GPP_PROJECTED_OWN = 0.25
MAX_LISTED = 6


@dataclass(frozen=True)
class Chalk:
    name: str
    position: str
    team: str
    own: float  # projected Own% as a fraction


def missing_chalk(
    edge: pd.DataFrame, lineups: Sequence[Sequence[str]], *, threshold: float, started: set[str] | None = None
) -> list[Chalk]:
    """Players projected at `threshold` or more who are in none of `lineups`, most owned first. Nobody OUT or
    IR, and nobody in `started` (a game already kicked off: it is too late to act, so it is not a warning)."""
    if not lineups or "Own%" not in edge.columns:
        return []
    held = {name for lineup in lineups for name in lineup if name}
    own = pd.to_numeric(edge["Own%"], errors="coerce")
    avail = (
        edge["Avail"].fillna("").astype(str) if "Avail" in edge.columns else pd.Series("", index=edge.index)
    )
    mask = (own >= threshold) & ~edge["Name"].isin(held) & ~avail.isin(OUT_STATUSES)
    if started:
        mask &= ~edge["Name"].isin(started)
    out = edge[mask].assign(OwnNum=own[mask]).sort_values(["OwnNum", "Name"], ascending=[False, True])
    return [
        Chalk(str(r.Name), str(r.Position), str(r.Team), float(r.OwnNum))
        for r in out.drop_duplicates("Name").itertuples(index=False)
    ]


def warning_line(chalk: list[Chalk], *, kind: str) -> str:
    """One sentence for a terminal or a Suggested note; empty when nobody is missing. For a cash set each
    player also shows his expected double-up ownership (the curve)."""
    if not chalk:
        return ""

    def show(c: Chalk) -> str:
        base = f"{c.name} ({c.position} {c.team}, {c.own:.0%} projected"
        if kind == "cash":
            base += f", ~{dups.double_up_ownership(c.own):.0%} in a double-up"
        return base + ")"

    shown = ", ".join(show(c) for c in chalk[:MAX_LISTED])
    more = f" and {len(chalk) - MAX_LISTED} more" if len(chalk) > MAX_LISTED else ""
    return f"Heavy chalk in none of your {kind} lineups: {shown}{more}."
