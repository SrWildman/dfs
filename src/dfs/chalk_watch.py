"""Chalk you are not holding: a heavily owned player in none of your lineups.

Sam, 2026-10-19: he missed a player owned about 70% and got buried. In a double-up the field is chalkier
than the large-field projection says (players projected over 15% were owned about 39% in his 2026
standings), so a cash lineup set is warned at `CASH_PROJECTED_OWN`, a GPP set only at the higher
`GPP_PROJECTED_OWN`. A warning, never a filter: leaving the chalk out is sometimes the point of a GPP build.

Pure: takes the edge frame (`Name`, `Position`, `Team`, `Own%`, `Avail`) and the names in each lineup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from dfs.derived import OUT_STATUSES

CASH_PROJECTED_OWN = 0.15  # projected (large-field) Own% at which a double-up field is already chalky
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
    """One sentence for a terminal or a Suggested note; empty when nobody is missing."""
    if not chalk:
        return ""
    shown = ", ".join(f"{c.name} ({c.position} {c.team}, {c.own:.0%} projected)" for c in chalk[:MAX_LISTED])
    more = f" and {len(chalk) - MAX_LISTED} more" if len(chalk) > MAX_LISTED else ""
    extra = " A double-up field is usually chalkier than the projection." if kind == "cash" else ""
    return f"Heavy chalk in none of your {kind} lineups: {shown}{more}.{extra}"
