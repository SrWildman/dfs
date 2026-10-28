"""This week's lineups: the best one with ranges and close calls, matchup-aware, plus K/DEF streams and the
Sunday check. Nothing is written to Sleeper: Sam sets his lineups by hand.

**The lineup** is `team_value.best_lineup` on this week's points (`ros.weekly`). Each starter's range is the
10th / 50th / 90th percentile of a normal with his weekly spread (`ros.fit_spread`), floored at 0.

**Close calls** are bench players within `CLOSE_PTS` of a starter they could replace. Each is decided by what
the league rewards this week, computed for both versions of the lineup (starters independent, normal):

- head-to-head: P(beat this week's opponent's best lineup);
- a league with a median game too: expected wins, that P plus P(score above the week's median), so the
  total counts twice, as it does there;
- chopped: P(not the week's lowest score) among the alive teams.

An underdog's best lineup is the higher-ceiling one at a close call and a favourite's the higher floor, by the
same arithmetic (the DFS late-swap lesson), with no hand-set threshold.

**Streams** (`streams`): the best free-agent K and DEF for each of the next `STREAM_WEEKS`, by Sleeper's
week-by-week projections in this league's scoring (they carry the matchup), against Sam's starter there.

**The Sunday check** (`check`): Sam's lineup as set in Sleeper, slot by slot: an empty slot, a player on bye,
or OUT / IR / suspended / Doubtful (Sleeper's status, with ESPN's game-day injury feed laid over it, as DFS
late swap does) is a `SWAP` with the best replacement (bench first, then free agents); Questionable is a
`LOOK`; a bench player projected `BENCH_GAP` more than the starter he could replace is a `LOOK` too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dfs.player_join import normalize_name
from dfs.season_long import team_value as tvm
from dfs.season_long import trade as tr
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator

Z90 = 1.2816
CLOSE_PTS = 1.5
STREAM_WEEKS = 3
BENCH_GAP = 2.0
DRAWS = 4000
OUT_NEWS = {"Out", "Injured Reserve", "Suspension", "Doubtful"}
OUT_STATUS = {"Out", "IR", "Sus", "Suspended", "PUP", "NFI", "Doubtful"}


@dataclass
class Starter:
    slot: str
    player_id: str
    pts: float
    sd: float
    close: str = ""  # the close call, decided

    @property
    def floor(self) -> float:
        return max(0.0, self.pts - Z90 * self.sd)

    @property
    def ceiling(self) -> float:
        return self.pts + Z90 * self.sd


@dataclass
class Plan:
    league: str
    week: int
    starters: list[Starter]
    mean: float
    sd: float
    objective: str  # what the close calls maximised
    win_prob: float = float("nan")  # head-to-head
    median_prob: float = float("nan")
    survive_prob: float = float("nan")  # chopped
    opponent: str = ""
    opponent_mean: float = float("nan")
    notes: list[str] = field(default_factory=list)


def _week_points(view: LeagueView, week: int) -> tuple[dict[str, float], dict[str, float]]:
    w = view.ros.weekly[view.ros.weekly["week"] == week]
    return dict(zip(w["player_id"], w["pts"], strict=True)), dict(zip(w["player_id"], w["sd"], strict=True))


def _opponent(view: LeagueView, ev: Evaluator, week: int) -> int | None:
    me = view.league.my_roster_id
    for a, b in getattr(ev, "pairs", {}).get(week, []):
        if me in (a, b):
            return b if a == me else a
    return None


class _Scorer:
    """The week's objective for a lineup of (mean, sd), against fixed draws of every other team."""

    def __init__(self, view: LeagueView, ev: Evaluator, week: int):
        lg = view.league
        self.view, self.week = view, week
        me = lg.my_roster_id
        i = ev.weeks.index(week)
        others = [rid for rid in ev.ids if rid != me]
        by = {rid: {w.week: w for w in ev.lineups(ev.rosters[rid])} for rid in others}
        self.mu = np.array([by[r][week].value if week in by[r] else 0.0 for r in others])
        self.sd = np.array([by[r][week].sd if week in by[r] else 0.0 for r in others])
        # Each team keeps its own column of the evaluator's draws, so Sam's draws never double as a rival's.
        self.z = ev.normals[:, i, [ev.ids.index(r) for r in others]]
        self.others = others
        self.opp = _opponent(view, ev, week)
        self.median = bool(lg.raw_settings.get("league_average_match"))
        self.chopped = lg.chopped
        self.mine_z = ev.normals[:, i, ev.ids.index(me)]

    def score_draws(self) -> np.ndarray:
        return self.mu + self.sd * self.z

    def evaluate(self, mean: float, sd: float) -> dict[str, float]:
        others = self.score_draws()
        mine = mean + sd * self.mine_z
        out: dict[str, float] = {}
        if self.chopped:
            out["survive"] = float((mine > others.min(axis=1)).mean())
            out["objective"] = out["survive"]
            return out
        if self.opp is not None and self.opp in self.others:
            j = self.others.index(self.opp)
            out["win"] = float((mine > others[:, j]).mean())
        if self.median:
            everyone = np.column_stack([others, mine])
            out["median"] = float((mine > np.median(everyone, axis=1)).mean())
        out["objective"] = out.get("win", 0.0) + out.get("median", 0.0) if out else mean
        return out


def _objective_name(view: LeagueView, scorer: _Scorer) -> str:
    if view.league.chopped:
        return "P(not the lowest score)"
    if scorer.median and scorer.opp is not None:
        return "expected wins (head-to-head + median game)"
    if scorer.median:
        return "P(above the median)"
    if scorer.opp is not None:
        return "P(beat this week's opponent)"
    return "points"


def plan(view: LeagueView, ev: Evaluator, week: int | None = None) -> Plan:
    lg = view.league
    week = week or ev.weeks[0]
    pts, sds = _week_points(view, week)
    roster = ev.rosters[lg.my_roster_id]
    mine = {p: pts.get(p, 0.0) for p in roster}
    total, chosen = tvm.best_lineup(mine, view.positions, lg.slots)
    scorer = _Scorer(view, ev, week)

    def stats(lineup: dict[int, str]) -> tuple[float, float]:
        m = sum(pts.get(p, 0.0) for p in lineup.values())
        return m, float(np.sqrt(sum(sds.get(p, 0.0) ** 2 for p in lineup.values())))

    goal = _objective_name(view, scorer)
    best = dict(chosen)
    best_eval = scorer.evaluate(*stats(best))
    calls: dict[int, str] = {}
    bench = [p for p in roster if p not in best.values() and mine.get(p, 0.0) > 0]
    for j, starter in list(best.items()):
        for alt in bench:
            if not set(view.positions.get(alt, ())) & tvm.slot_eligible(lg.slots[j]):
                continue
            if abs(mine[alt] - mine[starter]) > CLOSE_PTS:
                continue
            trial = {**best, j: alt}
            got = scorer.evaluate(*stats(trial))
            a, b = view.name(starter), view.name(alt)
            if got["objective"] > best_eval["objective"]:
                calls[j] = f"{b} over {a} ({mine[alt]:.1f} vs {mine[starter]:.1f}): better for {goal}"
                bench = [p for p in bench if p != alt] + [starter]
                best, best_eval = trial, got
            elif abs(got["objective"] - best_eval["objective"]) < 0.005:
                calls[j] = f"{a} vs {b} ({mine[starter]:.1f} vs {mine[alt]:.1f}): a coin flip"
            elif j not in calls:
                calls[j] = f"{a} over {b} ({mine[starter]:.1f} vs {mine[alt]:.1f}): better for {goal}"
    mean, sd = stats(best)
    starters = [
        Starter(lg.slots[j], best[j], pts.get(best[j], 0.0), sds.get(best[j], 0.0), calls.get(j, ""))
        for j in sorted(best)
    ]
    filled = set(best)
    for j, slot in enumerate(lg.slots):
        if j not in filled:
            starters.append(
                Starter(slot, "", 0.0, 0.0, "EMPTY: nobody on the roster can play here this week")
            )
    starters.sort(key=lambda s: lg.slots.index(s.slot))
    out = Plan(lg.name, week, starters, round(mean, 1), round(sd, 1), _objective_name(view, scorer))
    out.win_prob = best_eval.get("win", float("nan"))
    out.median_prob = best_eval.get("median", float("nan"))
    out.survive_prob = best_eval.get("survive", float("nan"))
    if scorer.opp is not None:
        out.opponent = lg.team(scorer.opp).label
        out.opponent_mean = float(scorer.mu[scorer.others.index(scorer.opp)])
    return out


# -- streams -----------------------------------------------------------------------------------------------


def streams(view: LeagueView, ev: Evaluator, top: int = 3) -> dict[str, list[tuple[str, list[float]]]]:
    """Per K / DEF slot the league has: the best free agents by next week, with their next `STREAM_WEEKS`."""
    lg = view.league
    weeks = ev.weeks[:STREAM_WEEKS]
    fa = tr.free_agents(view)
    s = view.ros.summary
    wk = view.ros.weekly.set_index(["player_id", "week"])["pts"]
    out = {}
    for pos in ("K", "DEF"):
        if pos not in lg.slots:
            continue
        mine = [p for p in ev.rosters[lg.my_roster_id] if s["position"].get(p) == pos]
        pool = [p for p in fa if s["position"].get(p) == pos]
        ranked = sorted(pool, key=lambda p: -float(wk.get((p, weeks[0]), 0.0)))[:top]
        out[pos] = [(p, [round(float(wk.get((p, w), 0.0)), 1) for w in weeks]) for p in ranked]
        out[pos] += [
            (f"yours: {view.name(p)}", [round(float(wk.get((p, w), 0.0)), 1) for w in weeks]) for p in mine
        ]
    return out


# -- the Sunday check --------------------------------------------------------------------------------------


@dataclass
class Alert:
    league: str
    cue: str  # SWAP, LOOK
    slot: str
    player: str
    reason: str
    swap: str = ""


def _news_status(news, name: str) -> str:
    if news is None or news.empty:
        return ""
    hit = news[news["Name"].map(normalize_name) == normalize_name(name)]
    return str(hit.sort_values("Updated")["Status"].iloc[-1]) if len(hit) else ""


def check(view: LeagueView, ev: Evaluator, news=None) -> list[Alert]:
    lg = view.league
    week = ev.weeks[0]
    pts, _ = _week_points(view, week)
    t = view.table
    me = lg.me
    set_lineup = list(me.starters) + [""] * (len(lg.slots) - len(me.starters))
    roster = ev.rosters[me.roster_id]
    bench = [p for p in roster if p not in set_lineup]
    fa = tr.free_agents(view)
    alerts: list[Alert] = []

    def status_of(pid: str) -> tuple[str, str]:
        sleeper = str(t.at[pid, "injury_status"]) if pid in t.index else ""
        espn = _news_status(news, view.name(pid))
        if espn in OUT_NEWS:
            return "out", f"{espn} (ESPN)"
        if sleeper in OUT_STATUS:
            return "out", sleeper
        if espn == "Questionable" or sleeper == "Questionable":
            return "q", "Questionable"
        return "", ""

    def playing(pid: str) -> bool:
        return pts.get(pid, 0.0) > 0

    def replacement(slot: str, exclude: set[str]) -> str:
        elig = tvm.slot_eligible(slot)
        for pool, label in ((bench, "bench"), (fa, "free agent")):
            cands = [
                p
                for p in pool
                if p not in exclude
                and set(view.positions.get(p, ())) & elig
                and playing(p)
                and status_of(p)[0] != "out"
            ]
            if cands:
                best = max(cands, key=lambda p: pts.get(p, 0.0))
                return f"start {view.name(best)} ({label}, {pts.get(best, 0.0):.1f})"
        return "no one available"

    used = set(set_lineup)
    for slot, pid in zip(lg.slots, set_lineup, strict=False):
        if not pid:
            alerts.append(
                Alert(lg.name, "SWAP", slot, "(empty)", "nobody in this slot", replacement(slot, used))
            )
            continue
        kind, label = status_of(pid)
        if not playing(pid) and kind != "out":
            alerts.append(
                Alert(
                    lg.name, "SWAP", slot, view.name(pid), "on bye / not projected", replacement(slot, used)
                )
            )
        elif kind == "out":
            alerts.append(Alert(lg.name, "SWAP", slot, view.name(pid), label, replacement(slot, used)))
        elif kind == "q":
            alerts.append(Alert(lg.name, "LOOK", slot, view.name(pid), "Questionable: check before kickoff"))
        else:
            elig = tvm.slot_eligible(slot)
            better = [p for p in bench if set(view.positions.get(p, ())) & elig and status_of(p)[0] != "out"]
            if better:
                top = max(better, key=lambda p: pts.get(p, 0.0))
                gap = pts.get(top, 0.0) - pts.get(pid, 0.0)
                if gap >= BENCH_GAP:
                    reason = f"{view.name(top)} on your bench projects +{gap:.1f}"
                    alerts.append(
                        Alert(lg.name, "LOOK", slot, view.name(pid), reason, f"start {view.name(top)}")
                    )
    return alerts


def plan_lines(view: LeagueView, p: Plan) -> list[str]:
    if view.league.chopped:
        odds = f"P(survive) {p.survive_prob:.0%}"
    else:
        parts = []
        if p.opponent:
            parts.append(f"vs {p.opponent} ({p.opponent_mean:.0f}): win {p.win_prob:.0%}")
        if not np.isnan(p.median_prob):
            parts.append(f"above the median {p.median_prob:.0%}")
        odds = "; ".join(parts) or "no matchup this week"
    lines = [
        f"{p.league} Week {p.week}: {p.mean:.1f} pts (sd {p.sd:.0f}); {odds}; close calls by {p.objective}"
    ]
    for s in p.starters:
        name = view.name(s.player_id) if s.player_id else "-"
        lines.append(
            f"  {s.slot:10s} {name:24s} {s.pts:5.1f}  ({s.floor:.0f}-{s.ceiling:.0f})"
            + (f"  {s.close}" if s.close else "")
        )
    return lines
