"""Waivers: every free agent ranked by what he adds to Sam's team, with a FAAB bid guide from the league.

**Value.** For each of the best `POOL` free agents (by rest-of-season points), Sam's roster with him added and
the player he misses least dropped (`trade.settle` with the evaluator: a needed bye or injury cover is never
the drop, because dropping it would cost lineup points). A quick screen on weighted lineup points, then the
full objective (`objective.Evaluator`: playoff-odds-weighted team value, or survival in a chopped league) for
the best `FULL`. This week's lineup gain is shown apart.

**Bid guide** (`bid_guide`). From the league's own completed waiver claims with a bid: claims of the same
position whose player was projected (by Sleeper, in this league's scoring, for the claim's week) within
`COMP_PTS` points a week of this player's projection now. The range is the median to the 80th percentile of
those winning bids, capped at Sam's FAAB, beside what the player is worth to him (value / points per dollar,
`faab`; in a chopped league survival gain over the simulated value of a dollar) and the rivals who could
outbid him (FAAB at least the range's low end) and need the position.

**Contingency** (`handcuff_value`). A backup RB2 whose RB1 misses a week scores more: the DFS side's measured
teammate bump (RB1 out: RB2 +4.27 DraftKings points a game, `models/research/teammate_bump.csv`, shrunk; a
PPR measure, used as is in other formats). His contingency value is the expected lineup gain from those weeks:
the RB1's chance of missing each week (`starter_miss_rate`; Questionable 25%, Doubtful 75% this week) times
what the bumped RB2 adds to Sam's lineup. Cheap handcuffs to stash are flagged.

**Chopped bid plan** (`chop_plan`). The roster most likely to be chopped this week: its best players, what
each adds to Sam's survival, what that is worth in dollars, and what the league has paid for a chopped
roster's first, second and third pick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.paths import REPO_ROOT
from dfs.season_long import faab as faab_mod
from dfs.season_long import objective, ros
from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long import trade as tr
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator

POOL = 40
FULL = 12
COMP_PTS = 3.0
MIN_COMPS = 3
BUMP_TABLE = REPO_ROOT / "models" / "research" / "teammate_bump.csv"
# The top list shows at most this many players of one position (five backup QBs all do one job).
PER_POSITION = 2
THIS_WEEK_MISS = {"Questionable": 0.25, "Doubtful": 0.75, "Out": 1.0}


@dataclass
class BidGuide:
    low: int
    high: int
    comps: int
    worth: int | None
    rivals: list[tuple[str, int]] = field(default_factory=list)

    def text(self, have: int) -> str:
        if self.comps == 0:
            price = "no paid claims like him yet"
        else:
            price = f"${self.low}-{self.high} to win ({self.comps} similar claims)"
        worth = f"; worth up to ${self.worth} to you" if self.worth is not None else ""
        cap = f" (you have ${have})" if self.high > have else ""
        rivals = (
            "; " + ", ".join(f"{name} has ${f}" for name, f in self.rivals[:3]) + " and needs one"
            if self.rivals
            else ""
        )
        return f"{price}{cap}{worth}{rivals}"


@dataclass
class Pickup:
    player_id: str
    value: float  # change in Sam's objective (team value, or survival value)
    this_week: float
    drop: str | None
    bid: BidGuide
    handcuff: float = 0.0  # contingency value included in `value`'s screen
    note: str = ""
    expected_weeks: float = float("nan")  # chopped: change in expected weeks survived


# -- history --------------------------------------------------------------------------------


def claims(league: sl.League) -> pd.DataFrame:
    """Completed waiver claims with a bid: [player_id, week, bid, roster_id]."""
    rows = []
    for w in range(1, league.last_scored_week + 2):
        for t in sl.fetch_transactions(league.league_id, w, final=w <= league.last_scored_week):
            bid = int((t.get("settings") or {}).get("waiver_bid") or 0)
            if t.get("type") != "waiver" or t.get("status") != "complete" or bid <= 0:
                continue
            for pid, rid in (t.get("adds") or {}).items():
                rows.append(
                    {"player_id": str(pid), "week": int(t.get("leg") or w), "bid": bid, "roster_id": int(rid)}
                )
    return pd.DataFrame(rows, columns=["player_id", "week", "bid", "roster_id"])


def claim_values(league: sl.League, history: pd.DataFrame) -> pd.Series:
    """Each claim's player, projected by Sleeper for the claim's week in this league's scoring."""
    if history.empty:
        return pd.Series(dtype=float)
    out = []
    for w, g in history.groupby("week"):
        proj = ros.projections(league.scoring, league.season, [int(w)], league.last_scored_week)
        by = proj.set_index("player_id")["proj"]
        out.append(g["player_id"].map(by))
    return pd.concat(out).reindex(history.index)


def bid_guide(
    view: LeagueView,
    history: pd.DataFrame,
    values: pd.Series,
    pid: str,
    worth: int | None,
    inactive: set[int] | None = None,
) -> BidGuide:
    lg = view.league
    s = view.ros.summary
    pos = s["position"].get(pid, "")
    now = float(s["next_week"].get(pid, 0.0) or 0.0)
    if math.isnan(now) or now <= 0:
        now = float(s["ros_pts"].get(pid, 0.0)) / max(float(s["games_left"].get(pid, 1) or 1), 1.0)
    positions = history["player_id"].map(s["position"])
    near = history[(positions == pos) & ((values - now).abs() <= COMP_PTS)]
    if len(near) < MIN_COMPS:
        near = history[(positions == pos) & ((values - now).abs() <= 2 * COMP_PTS)]
    bids = near["bid"].astype(float)
    low = int(np.ceil(bids.median())) if len(bids) else 0
    high = int(np.ceil(bids.quantile(0.8))) if len(bids) else 0
    me = lg.my_roster_id
    rivals = []
    for t in lg.alive_teams():
        # An inactive manager (`activity`) is not a bidder to worry about.
        if t.roster_id == me or t.faab_left < max(low, 1) or t.roster_id in (inactive or set()):
            continue
        need = dict((slot, d) for slot, d, _ in view.needs.get(t.roster_id, []))
        if need.get(pos, 0.0) < 0:
            rivals.append((t.label, t.faab_left))
    rivals.sort(key=lambda r: -r[1])
    return BidGuide(low, high, len(bids), worth, rivals)


# -- contingency --------------------------------------------------------------------------------


def rb2_bump() -> float:
    try:
        table = pd.read_csv(BUMP_TABLE)
    except OSError:
        return 0.0
    row = table[(table["absent"] == "RB1") & (table["beneficiary"] == "RB2")]
    return float(row["bump"].iloc[0]) if len(row) else 0.0


def handcuff_value(
    view: LeagueView, ev: Evaluator, pid: str, roster: list[str], bump: float
) -> tuple[float, str]:
    """Expected lineup gain from the weeks his RB1 misses, and the RB1's id ('' if not a backup RB2)."""
    t = view.table
    if pid not in t.index or t.at[pid, "position"] != "RB" or int(t.at[pid, "depth"]) != 2 or bump <= 0:
        return 0.0, ""
    team = t.at[pid, "team"]
    starters = t[(t["team"] == team) & (t["position"] == "RB") & (t["depth"] == 1)]
    if starters.empty:
        return 0.0, ""
    rb1 = str(starters.index[0])
    status = str(t.at[rb1, "injury_status"])
    weekly = view.ros.weekly
    pts = weekly[weekly["player_id"] == pid].set_index("week")["pts"]
    total = 0.0
    for i, w in enumerate(ev.weeks):
        p_miss = (
            THIS_WEEK_MISS.get(status, view.cfg.starter_miss_rate) if i == 0 else view.cfg.starter_miss_rate
        )
        if p_miss <= 0 or float(pts.get(w, 0.0)) <= 0:
            continue
        base = ev._pts.get(w, {})
        bumped = {p: base.get(p, 0.0) for p in roster}
        bumped[pid] = float(pts.get(w, 0.0)) + bump
        with_him = tvm.best_lineup(bumped, view.positions, view.league.slots)[0]
        total += p_miss * max(0.0, with_him - ev.lineup_total(roster, w))
    return round(total, 1), rb1


# -- the ranking --------------------------------------------------------------------------------


def rank_pickups(
    view: LeagueView,
    ev: Evaluator,
    top: int = 10,
    only: list[str] | None = None,
    inactive: set[int] | None = None,
) -> list[Pickup]:
    lg = view.league
    me = lg.my_roster_id
    mine = ev.rosters[me]
    fa = tr.free_agents(view)
    pool = only if only is not None else fa[:POOL]
    weights = _weights(view, ev)
    bump = rb2_bump()

    def screen_total(roster: list[str]) -> float:
        return sum(weights[w] * ev.lineup_total(roster, w) for w in ev.weeks)

    base_total = screen_total(mine)
    screened = []
    for pid in pool:
        new, dropped, _ = tr.settle([*mine, pid], len(mine), view, [], ev)
        if pid not in new:
            continue
        cuff, _ = handcuff_value(view, ev, pid, [p for p in new if p != pid] + [pid], bump)
        screened.append((screen_total(new) - base_total + cuff, pid, new, dropped, cuff))
    screened.sort(key=lambda x: -x[0])
    base = ev.base()[me]
    history = claims(lg)
    values = claim_values(lg, history)
    dollar = _dollar_value(view, ev)
    out = []
    for _, pid, new, dropped, cuff in screened[: max(FULL, top) if only is None else len(screened)]:
        after = ev.evaluate(rosters={me: new})[me]
        # A handcuff's contingency is lineup points; a chopped league's value is survival, so there it is
        # shown, not added.
        delta = after.value - base.value + (0.0 if lg.chopped else cuff)
        first = ev.weeks[0] if ev.weeks else None
        this_week = (ev.lineup_total(new, first) - ev.lineup_total(mine, first)) if first else 0.0
        worth = int(max(0.0, delta) / dollar) if dollar > 0 else None
        pick = Pickup(
            player_id=pid,
            value=round(delta, 3 if lg.chopped else 1),
            this_week=round(this_week, 1),
            drop=dropped[0] if dropped else None,
            bid=bid_guide(view, history, values, pid, worth, inactive),
            handcuff=cuff,
            expected_weeks=after.expected_weeks - base.expected_weeks if lg.chopped else float("nan"),
        )
        if cuff > 0:
            pick.note = f"handcuff: +{cuff:.1f} if his RB1 misses time"
        out.append(pick)
    out.sort(key=lambda p: -p.value)
    if only is not None:
        return out
    shown, count = [], {}
    for p in out:
        pos = view.ros.summary["position"].get(p.player_id, "")
        if p.value <= 0 or count.get(pos, 0) >= PER_POSITION:
            continue
        count[pos] = count.get(pos, 0) + 1
        shown.append(p)
    return shown[:top]


def handcuffs(view: LeagueView, ev: Evaluator, top: int = 3) -> list[tuple[str, float, str]]:
    """Free-agent RB2s by contingency value to Sam's lineup: (player_id, value, his RB1's id)."""
    mine = ev.rosters[view.league.my_roster_id]
    bump = rb2_bump()
    out = []
    for pid in tr.free_agents(view)[: POOL * 3]:
        value, rb1 = handcuff_value(view, ev, pid, [*mine, pid], bump)
        if value > 0:
            out.append((pid, value, rb1))
    return sorted(out, key=lambda x: -x[1])[:top]


def _weights(view: LeagueView, ev: Evaluator) -> dict[int, float]:
    lg = view.league
    if lg.chopped:
        return {w: view.cfg.survival_decay**i for i, w in enumerate(ev.weeks)}
    p = ev.base()[lg.my_roster_id].p_playoffs
    return {w: (view.cfg.playoff_weight * p if w in lg.playoff_weeks else 1.0) for w in ev.weeks}


def _dollar_value(view: LeagueView, ev: Evaluator) -> float:
    """Objective units one FAAB dollar is worth to Sam."""
    if view.league.chopped:
        return max(ev.faab_dollar_value(view.league.my_roster_id), 0.0)
    lc = next((x for x in view.cfg.leagues if x.name == view.league.name), None)
    return faab_mod.faab_value(ev, lc.faab_point_value if lc else None).points_per_dollar


# -- chopped --------------------------------------------------------------------------------


@dataclass
class ChopPlan:
    team: str
    chop_risk: float
    picks: list[tuple[str, float, int, float]]  # (player_id, Sam's survival gain, worth $, league price $)

    def bid(self, worth: int, price: float) -> str:
        """From what the league pays for that pick up to what he is worth to Sam, never past his FAAB."""
        top = min(worth, self.faab)
        if top <= 0:
            return "no bid: he does not help you survive"
        low = min(int(price), top)
        return f"bid ${low}-{top}" if low < top else f"bid ${top}"

    faab: int
    rivals: list[tuple[str, int]]


def chop_plan(view: LeagueView, ev: Evaluator, n: int = objective.PICKUPS_PER_CHOP) -> ChopPlan | None:
    lg = view.league
    if not lg.chopped:
        return None
    me = lg.my_roster_id
    base = ev.base()
    others = [rid for rid in ev.ids if rid != me]
    if not others:
        return None
    target = max(others, key=lambda r: 1 - base[r].p_survive_now)
    s = view.ros.summary
    best = s["ros_pts"].reindex(ev.rosters[target]).dropna().sort_values(ascending=False).index[:n]
    dollar = max(ev.faab_dollar_value(me), 1e-9)
    mine = ev.rosters[me]
    picks = []
    for k, pid in enumerate(best):
        new, _, _ = tr.settle([*mine, pid], len(mine), view, [], ev)
        gain = ev.evaluate(rosters={me: new})[me].value - base[me].value
        picks.append((pid, round(gain, 3), int(max(gain, 0.0) / dollar), float(ev.prices[k])))
    rivals = sorted(((lg.team(r).label, ev.faab[r]) for r in others if r != target), key=lambda x: -x[1])
    return ChopPlan(lg.team(target).label, 1 - base[target].p_survive_now, picks, ev.faab[me], rivals[:3])


def pickup_line(view: LeagueView, p: Pickup) -> str:
    s = view.ros.summary
    pos = s["position"].get(p.player_id, "")
    gain = f"{p.expected_weeks:+.2f} wks survived" if view.league.chopped else f"{p.value:+.1f} value"
    drop = f"drop {view.name(p.drop)}" if p.drop else "open spot"
    note = f"; {p.note}" if p.note else ""
    return (
        f"add {view.name(p.player_id)} ({pos}), {drop} | {gain}, this week {p.this_week:+.1f} | "
        f"{p.bid.text(view.league.me.faab_left)}{note}"
    )
