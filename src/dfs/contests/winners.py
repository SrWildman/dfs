"""What wins in Sam's contests: the top 1% of each GPP's lineups beside the whole field, on how they
were built.

Input is one parsed `standings.Standings` and the contest week's players frame (`field.Roster`'s:
`Name`, `Team`,
`Position`, `Opponent`, `Salary`). For each lineup that resolves: its stack, its bring-back, RB + his
own DST, a
game stack, the nine players' ownership sum and product, the salary left and its DST's ownership. The measures
are the ones Sam asked for (2026-10-18), by his own stack definition: the QB plus one to three teammates
of any
skill position.

- **QB+1 / QB+2 (any teammate):** the QB and at least one / two teammates at RB, WR or TE. `QB+1 WR/TE` and
  `QB+2 WR/TE` are the older pass-catcher-only definition (`field.py`), kept for comparison.
- **Stack + bring-back:** a stack and at least one RB, WR or TE of the QB's opponent.
- **Game stack:** a stack and at least two of the opponent's.
- **RB + own DST:** a running back and the defense of his own team.
- **Ownership sum / product:** from the contest's own `%Drafted`; a player it does not list counts at
  `dups.OWN_FLOOR`. **Salary left:** $50,000 minus the nine salaries. **DST ownership:** the lineup's DST's
  `%Drafted`.

The top 1% of a contest is its best `max(1, ceil(1% of the lineups))` places (ties at the cut are kept, so a
group is never smaller than its place count). Contests of fewer than `MIN_ENTRIES` lineups are left out:
a top 1%
of ten lineups is not a group. Pure functions; no files, no sheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.contests.field import FLEX_POSITIONS, SALARY_CAP, Resolved, Roster
from dfs.contests.standings import Standings
from dfs.sim import dups

MIN_ENTRIES = 700  # the GPP fields the Top 1% line is measured on (docs/SIM.md "Relative lines")
TOP_FRACTION = 0.01
SKILL = frozenset(FLEX_POSITIONS)

WINNERS_COLUMNS = ["measure", "kind", "top1", "field", "diff", "n_top1", "n_field", "contests"]

# (key, label, kind): "share" measures are fractions of lineups, "mean" and "median" are averages.
MEASURES = (
    ("stack_any1", "QB + 1 or more teammates (any position)", "share"),
    ("stack_any2", "QB + 2 or more teammates (any position)", "share"),
    ("stack_wrte1", "QB + 1 or more WR/TE", "share"),
    ("stack_wrte2", "QB + 2 or more WR/TE", "share"),
    ("bringback", "Stack + a bring-back", "share"),
    ("game_stack", "Game stack (stack + 2 or more of the opponent's)", "share"),
    ("rb_own_dst", "A RB with his own DST", "share"),
    ("own_sum", "Ownership sum of the nine", "mean"),
    ("own_product", "Ownership product of the nine (median)", "median"),
    ("salary_left", "Salary left ($)", "mean"),
    ("dst_own", "DST ownership", "mean"),
)
SHARE_KEYS = [k for k, _, kind in MEASURES if kind == "share"]


def _resolve(slots, roster: Roster, known_position: dict[str, str]) -> list[tuple[str, str, Resolved]] | None:
    out = []
    for slot, name in slots:
        player, _ = roster.resolve(name, slot, known_position.get(name))
        if player is None:
            return None
        out.append((slot, name, player))
    return out


def lineup_measures(resolved: list[tuple[str, str, Resolved]], own: dict[str, float]) -> dict:
    """The measures of one resolved lineup (see the module docstring)."""
    qb = next((p for s, _, p in resolved if s == "QB"), None)
    dst = next(((n, p) for s, n, p in resolved if s == "DST"), None)
    offense = [(s, p) for s, _, p in resolved if s not in ("QB", "DST")]
    out: dict = {k: float("nan") for k, _, _ in MEASURES}
    if qb is not None:
        mates = [p for _, p in offense if p.team == qb.team]
        opp = [p for _, p in offense if p.team == qb.opponent]
        out["stack_any1"] = float(len(mates) >= 1)
        out["stack_any2"] = float(len(mates) >= 2)
        out["stack_wrte1"] = float(sum(p.position in ("WR", "TE") for p in mates) >= 1)
        out["stack_wrte2"] = float(sum(p.position in ("WR", "TE") for p in mates) >= 2)
        out["bringback"] = float(len(mates) >= 1 and len(opp) >= 1)
        out["game_stack"] = float(len(mates) >= 1 and len(opp) >= 2)
    if dst is not None:
        out["rb_own_dst"] = float(any(p.position == "RB" and p.team == dst[1].team for _, p in offense))
        out["dst_own"] = own.get(dst[0], float("nan"))
    shares = [own.get(name, float("nan")) for _, name, _ in resolved]
    out["own_sum"] = dups.ownership_sum(shares)
    out["own_product"] = dups.ownership_product(shares)
    salaries = [p.salary for _, _, p in resolved]
    if all(s is not None for s in salaries):
        out["salary_left"] = float(SALARY_CAP - sum(salaries))
    return out


def contest_lineups(standings: Standings, players: pd.DataFrame | None) -> pd.DataFrame:
    """One row per resolved Classic lineup: `contest_id`, `points`, `top1` and the measures. Empty for a
    contest
    that is not Classic, is smaller than `MIN_ENTRIES`, or has no players frame."""
    columns = ["contest_id", "points", "top1", *[k for k, _, _ in MEASURES]]
    if standings.kind != "Classic" or players is None or len(standings.lineups) < MIN_ENTRIES:
        return pd.DataFrame(columns=columns)
    roster = Roster(players)
    owned = standings.ownership
    scale = 100.0 if len(owned) and float(owned["pct_drafted"].max()) > 1.5 else 1.0
    own = {}
    known_position = {}
    if len(owned):
        own = {
            str(p).strip(): float(v) / scale
            for p, v in zip(owned["player"], owned["pct_drafted"], strict=True)
        }
        known_position = {
            str(p).strip(): pos
            for p, pos in zip(owned["player"], owned["dk_position"], strict=True)
            if pos != "FLEX"
        }
    n = len(standings.lineups)
    cut_place = max(1, math.ceil(TOP_FRACTION * n))
    points = standings.lineups["points"].to_numpy(dtype=float)
    cut = np.sort(points)[::-1][cut_place - 1]
    rows = []
    for rec in standings.lineups.itertuples(index=False):
        resolved = _resolve(rec.slots, roster, known_position)
        if resolved is None:
            continue
        rows.append(
            {
                "contest_id": standings.contest_id,
                "points": float(rec.points),
                "top1": bool(rec.points >= cut),
                **lineup_measures(resolved, own),
            }
        )
    return pd.DataFrame(rows, columns=columns)


@dataclass(frozen=True)
class Summary:
    table: pd.DataFrame
    top1_lineups: int
    field_lineups: int
    contests: int


def summarize(lineups: pd.DataFrame) -> Summary:
    """The pooled table: for each measure the mean over the top-1% lineups and over every lineup, their
    difference, and how many lineups each mean rests on. Each contest's top 1% counts as often as its lineups
    do (a pooled mean, not a mean of contest means)."""
    if lineups.empty:
        return Summary(pd.DataFrame(columns=WINNERS_COLUMNS), 0, 0, 0)
    top = lineups[lineups["top1"]]
    rows = []
    for key, label, kind in MEASURES:
        center = np.median if kind == "median" else np.mean  # a product of nine shares is skewed: its median
        t, f = top[key].dropna(), lineups[key].dropna()
        if t.empty or f.empty:
            continue
        rows.append(
            {
                "measure": label,
                "kind": kind,
                "top1": float(center(t)),
                "field": float(center(f)),
                "diff": float(center(t) - center(f)),
                "n_top1": int(len(t)),
                "n_field": int(len(f)),
                "contests": int(lineups["contest_id"].nunique()),
            }
        )
    return Summary(
        pd.DataFrame(rows, columns=WINNERS_COLUMNS),
        int(len(top)),
        int(len(lineups)),
        int(lineups["contest_id"].nunique()),
    )
