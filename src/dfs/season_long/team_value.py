"""Team value: each remaining week's best legal lineup from a roster, and what that says about needs.

**Lineup.** Each week, the roster's best legal starting lineup for the league's slots (QB, RB, WR, TE, K, DEF,
FLEX = RB/WR/TE, SUPER_FLEX = QB/RB/WR/TE, WRRB_FLEX, REC_FLEX = WR/TE), solved exactly as an assignment
problem (`best_lineup`), so a flex slot always takes the best player left that fits it.

**Bench as cover, a small stated term.** Each starter misses a given week with chance `starter_miss_rate`
(season.toml). The week's value is the full lineup minus the expected loss:
`V = L - p * sum over starters of (L - L without that starter)`. A bench that can fill in shrinks that loss;
that is all a bench is worth here. Byes and known injuries are already 0 in the weekly points (`ros.py`).

**Redraft.** Team value = sum over remaining weeks of `V_w`, a playoff week counting `playoff_weight`.

**Chopped (guillotine).** Points are not the objective, surviving is. `survival` simulates every remaining
week for every alive team (each starter's points normal with `ros` spread, independent), eliminates the lowest
score each week, and values a roster by its chance of surviving each week with week i ahead weighted
`survival_decay ** i`; its expected weeks survived comes with it. Floor counts more than ceiling by
construction: only the bottom of the week matters. Phase 1 does not model survivors picking up the chopped
rosters (that is the FAAB valuation's job).

**Needs.** Per slot type, the team's average starter points per week against the league median for that slot;
the biggest shortfall is the need.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

FLEX_ELIGIBLE = {
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "WRRB_FLEX": {"WR", "RB"},
    "REC_FLEX": {"WR", "TE"},
}
_BIG = 1e6


def slot_eligible(slot: str) -> set[str]:
    return FLEX_ELIGIBLE.get(slot, {slot})


def best_lineup(
    points: dict[str, float], positions: dict[str, tuple[str, ...]], slots: list[str]
) -> tuple[float, dict[int, str]]:
    """Best total and slot index -> player_id (an unfillable slot is left out and scores 0)."""
    players = [p for p in points if points[p] > 0]
    if not players or not slots:
        return 0.0, {}
    cost = np.full((len(players), len(slots)), _BIG)
    for i, p in enumerate(players):
        elig = set(positions.get(p, ()))
        for j, s in enumerate(slots):
            if elig & slot_eligible(s):
                cost[i, j] = -points[p]
    # Empty-slot fillers: worth 0 anywhere.
    cost = np.vstack([cost, np.zeros((len(slots), len(slots)))])
    rows, cols = linear_sum_assignment(cost)
    chosen = {
        int(c): players[r]
        for r, c in zip(rows, cols, strict=False)
        if r < len(players) and cost[r, c] < _BIG / 2
    }
    return float(sum(points[p] for p in chosen.values())), chosen


@dataclass
class WeekLineup:
    week: int
    total: float
    value: float
    starters: dict[int, str]
    sd: float


def week_lineups(
    roster: list[str],
    weekly: pd.DataFrame,
    positions: dict[str, tuple[str, ...]],
    slots: list[str],
    miss_rate: float,
) -> list[WeekLineup]:
    """`weekly` is `ros.RosResult.weekly` indexed or filterable by player_id/week."""
    sub = weekly[weekly["player_id"].isin(roster)]
    out = []
    for week, g in sub.groupby("week"):
        pts = dict(zip(g["player_id"], g["pts"], strict=False))
        sds = dict(zip(g["player_id"], g["sd"], strict=False))
        total, starters = best_lineup(pts, positions, slots)
        loss = 0.0
        if miss_rate > 0:
            for p in set(starters.values()):
                without, _ = best_lineup({q: v for q, v in pts.items() if q != p}, positions, slots)
                loss += total - without
        sd = float(np.sqrt(sum(sds.get(p, 0.0) ** 2 for p in starters.values())))
        out.append(WeekLineup(int(week), total, total - miss_rate * loss, starters, sd))
    return sorted(out, key=lambda w: w.week)


@dataclass
class TeamValue:
    roster_id: int
    weeks: list[WeekLineup]
    value: float  # weighted sum of weekly value (redraft objective)
    lineup_pts: float  # unweighted, no cover term
    playoff_value: float
    cover_cost: float  # the expected loss to missed starters, weighted

    def by_week(self) -> dict[int, WeekLineup]:
        return {w.week: w for w in self.weeks}


def team_value(
    roster_id: int,
    roster: list[str],
    weekly: pd.DataFrame,
    positions: dict[str, tuple[str, ...]],
    slots: list[str],
    miss_rate: float,
    playoff_weeks: list[int],
    playoff_weight: float,
) -> TeamValue:
    weeks = week_lineups(roster, weekly, positions, slots, miss_rate)
    weight = {w.week: (playoff_weight if w.week in playoff_weeks else 1.0) for w in weeks}
    return TeamValue(
        roster_id=roster_id,
        weeks=weeks,
        value=round(sum(w.value * weight[w.week] for w in weeks), 1),
        lineup_pts=round(sum(w.total for w in weeks), 1),
        playoff_value=round(sum(w.value * weight[w.week] for w in weeks if w.week in playoff_weeks), 1),
        cover_cost=round(sum((w.total - w.value) * weight[w.week] for w in weeks), 1),
    )


def slot_strength(tv: TeamValue, weekly: pd.DataFrame, slots: list[str]) -> dict[str, float]:
    """Average points per week from each slot type (all RB slots together, ..., FLEX) in the best lineups."""
    pts = weekly.set_index(["player_id", "week"])["pts"]
    totals = dict.fromkeys(slots, 0.0)
    for w in tv.weeks:
        for j, pid in w.starters.items():
            totals[slots[j]] += float(pts.get((pid, w.week), 0.0))
    n = max(len(tv.weeks), 1)
    return {s: round(t / n, 2) for s, t in totals.items()}


def needs(strengths: dict[int, dict[str, float]]) -> dict[int, list[tuple[str, float, int]]]:
    """Per team, slot types sorted by shortfall: (slot, points per week vs league median, rank 1 = best)."""
    frame = pd.DataFrame(strengths).T.fillna(0.0)
    median = frame.median()
    ranks = frame.rank(ascending=False, method="min").astype(int)
    out = {}
    for rid in frame.index:
        diffs = [
            (s, round(float(frame.at[rid, s] - median[s]), 2), int(ranks.at[rid, s])) for s in frame.columns
        ]
        out[int(rid)] = sorted(diffs, key=lambda d: d[1])
    return out


# --- chopped leagues ----------------------------------------------------------------------------------------


@dataclass
class Survival:
    weeks: list[int]
    alive_after: dict[int, np.ndarray]  # roster_id -> P(alive after each week)
    value: dict[int, float]
    expected_weeks: dict[int, float]
    chop_now: dict[int, float]  # P(chopped this coming week)


def survival(teams: dict[int, list[WeekLineup]], decay: float, draws: int, seed: int = 7) -> Survival:
    """Monte Carlo of the remaining chops. `teams` maps each ALIVE roster to its week lineups (same weeks)."""
    ids = sorted(teams)
    weeks = sorted({w.week for wl in teams.values() for w in wl})
    rng = np.random.default_rng(seed)
    mean = np.array([[{w.week: w.value for w in teams[t]}.get(wk, 0.0) for t in ids] for wk in weeks])
    sd = np.array([[{w.week: w.sd for w in teams[t]}.get(wk, 0.0) for t in ids] for wk in weeks])
    alive = np.ones((draws, len(ids)), dtype=bool)
    alive_after = np.zeros((len(weeks), len(ids)))
    for i in range(len(weeks)):
        if alive.sum(axis=1).max() <= 1:
            alive_after[i] = alive.mean(axis=0)
            continue
        score = mean[i] + sd[i] * rng.standard_normal((draws, len(ids)))
        score = np.where(alive, score, np.inf)
        lowest = np.argmin(score, axis=1)
        still = alive.sum(axis=1) > 1
        alive[np.arange(draws)[still], lowest[still]] = False
        alive_after[i] = alive.mean(axis=0)
    weights = decay ** np.arange(len(weeks))
    value = {t: float((alive_after[:, j] * weights).sum()) for j, t in enumerate(ids)}
    return Survival(
        weeks=weeks,
        alive_after={t: alive_after[:, j] for j, t in enumerate(ids)},
        value=value,
        expected_weeks={t: float(alive_after[:, j].sum()) for j, t in enumerate(ids)},
        chop_now={t: float(1 - alive_after[0, j]) if weeks else 0.0 for j, t in enumerate(ids)},
    )
