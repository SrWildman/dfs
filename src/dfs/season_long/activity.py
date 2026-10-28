"""Two more edges: each player's playoff-week schedule, and how active each manager is.

**Playoff schedule** (`playoff_schedule`). For the league's playoff weeks (15-17 where it has them): each
player's opponents and his schedule ease, the points per game Sleeper projects for him in those weeks (the
projections carry the matchup) over his rest-of-season points per game. Above 1, an easier run-in than his
season; below 1, a harder one. A bye in the playoffs shows as `BYE`.

**Manager activity** (`manager_activity`), from the league's transactions and past matchups:

- moves (waiver and free-agent claims that went through), trades and the positions traded, and the last week
  of either;
- lineup misses over the last `RECENT_WEEKS` finished weeks: starting slots left empty or filled by a player
  whose team did not play;
- `inactive`: no move in `INACTIVE_WEEKS` weeks and at least one recent lineup miss, a trade target who may
  not answer and a FAAB rival who will not bid (the bid guide leaves him out); `trader`: `TRADER_MIN` trades
  or more.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from dfs.season_long import ros
from dfs.season_long import sleeper_league as sl
from dfs.season_long.league_view import LeagueView

RECENT_WEEKS = 3
INACTIVE_WEEKS = 3
TRADER_MIN = 2


def playoff_schedule(view: LeagueView) -> pd.DataFrame:
    """Per player_id: `PlayoffOpp` ("NYJ, MIA, BYE") and `PlayoffEase` (playoff over ROS points per game)."""
    lg = view.league
    weeks = lg.playoff_weeks
    if not weeks:
        return pd.DataFrame(columns=["PlayoffOpp", "PlayoffEase"])
    proj = ros.projections(lg.scoring, lg.season, weeks, lg.last_scored_week)
    team_opp = {
        (w, t): o for w, t, o in zip(proj["week"], proj["team"], proj["opponent"], strict=True) if t and o
    }
    s = view.ros.summary
    weekly = view.ros.weekly
    po = weekly[weekly["week"].isin(weeks)].groupby("player_id")["pts"].agg(["sum", lambda x: (x > 0).sum()])
    po.columns = ["pts", "games"]
    rate = s["ros_pts"] / s["games_left"].where(s["games_left"] > 0)
    out = pd.DataFrame(index=s.index)
    out["PlayoffOpp"] = [
        ", ".join(team_opp.get((w, team), "BYE") if team else "-" for w in weeks) for team in s["team"]
    ]
    po_rate = po["pts"] / po["games"].where(po["games"] > 0)
    out["PlayoffEase"] = (po_rate.reindex(s.index) / rate).round(2)
    return out


@dataclass
class Activity:
    roster_id: int
    moves: int = 0
    trades: int = 0
    traded_positions: dict[str, int] = field(default_factory=dict)
    last_move: int = 0
    lineup_misses: int = 0

    def label(self, current_week: int) -> str:
        tags = []
        if self.trades >= TRADER_MIN:
            tags.append("trader")
        if current_week - self.last_move >= INACTIVE_WEEKS and self.lineup_misses > 0:
            tags.append("inactive")
        return ", ".join(tags)

    def positions_text(self) -> str:
        return ", ".join(f"{p} x{n}" for p, n in sorted(self.traded_positions.items(), key=lambda x: -x[1]))


def manager_activity(view: LeagueView) -> dict[int, Activity]:
    lg = view.league
    pos = view.table["position"]
    out = {t.roster_id: Activity(t.roster_id) for t in lg.teams}
    for w in range(1, lg.last_scored_week + 2):
        for t in sl.fetch_transactions(lg.league_id, w, final=w <= lg.last_scored_week):
            if t.get("status") != "complete":
                continue
            week = int(t.get("leg") or w)
            kind = t.get("type")
            rids = [int(r) for r in (t.get("roster_ids") or [])]
            if kind == "trade":
                for rid in rids:
                    a = out.setdefault(rid, Activity(rid))
                    a.trades += 1
                    a.last_move = max(a.last_move, week)
                for pid, rid in (t.get("adds") or {}).items():
                    a = out.setdefault(int(rid), Activity(int(rid)))
                    p = str(pos.get(str(pid), "?"))
                    a.traded_positions[p] = a.traded_positions.get(p, 0) + 1
            elif kind in {"waiver", "free_agent"}:
                for rid in rids:
                    a = out.setdefault(rid, Activity(rid))
                    a.moves += 1
                    a.last_move = max(a.last_move, week)
    recent = list(range(max(1, lg.last_scored_week - RECENT_WEEKS + 1), lg.last_scored_week + 1))
    if recent:
        playing = ros.teams_playing(ros.projections(lg.scoring, lg.season, recent, lg.last_scored_week))
        team = view.table["team"]
        for w in recent:
            for m in sl.fetch_matchups(lg.league_id, w, final=True):
                rid = int(m.get("roster_id") or 0)
                if rid not in out:
                    continue
                starters = list(m.get("starters") or [])
                misses = sum(
                    1
                    for p in starters
                    if not p
                    or p == "0"
                    or (str(p) in team.index and team[str(p)] not in playing.get(w, set()))
                )
                out[rid].lineup_misses += misses
    return out


def is_inactive(acts: dict[int, Activity], rid: int, current_week: int) -> bool:
    a = acts.get(rid)
    return a is not None and "inactive" in a.label(current_week)
