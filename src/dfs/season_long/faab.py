"""What one FAAB dollar is worth in a redraft league, in team-value points.

From the league's own season: every completed waiver claim with a winning bid above $0, and what that player
adds NOW to the roster that won him (its best lineups over the remaining weeks with him minus without him,
playoff weeks at `playoff_weight`). `points per dollar = sum of those gains / sum of the bids`. Measured on
the weeks left, so it shrinks as the season runs out, as a dollar's worth does. `faab_point_value` in a
league's season.toml entry overrides it.

A chopped league never uses this: there FAAB is simulated (`objective`), because what it buys is the chopped
rosters, not the ordinary wire.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.season_long import sleeper_league as sl
from dfs.season_long.objective import Evaluator


@dataclass
class FaabValue:
    points_per_dollar: float
    claims: int
    dollars: int
    source: str  # "fitted", "season.toml", or "no paid claims yet"


def fitted_value(ev: Evaluator) -> FaabValue:
    lg = ev.view.league
    weight = {w: (ev.view.cfg.playoff_weight if w in lg.playoff_weeks else 1.0) for w in ev.weeks}
    gains, dollars, claims = 0.0, 0, 0
    for w in range(1, lg.last_scored_week + 2):
        for t in sl.fetch_transactions(lg.league_id, w, final=w <= lg.last_scored_week):
            bid = int((t.get("settings") or {}).get("waiver_bid") or 0)
            if t.get("type") != "waiver" or t.get("status") != "complete" or bid <= 0:
                continue
            rid = next(iter((t.get("adds") or {}).values()), None)
            if rid is None or int(rid) not in ev.rosters:
                continue
            for pid in t.get("adds") or {}:
                roster = [p for p in ev.rosters[int(rid)] if p != pid]
                gains += sum(
                    weight[wk] * (ev.lineup_total([*roster, pid], wk) - ev.lineup_total(roster, wk))
                    for wk in ev.weeks
                )
            dollars += bid
            claims += 1
    if not dollars:
        return FaabValue(0.0, 0, 0, "no paid claims yet")
    return FaabValue(round(gains / dollars, 3), claims, dollars, "fitted")


def faab_value(ev: Evaluator, override: float | None) -> FaabValue:
    if override is not None:
        return FaabValue(override, 0, 0, "season.toml")
    return fitted_value(ev)
