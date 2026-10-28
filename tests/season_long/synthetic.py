"""A small synthetic league for the trade tests: no network, no sheet."""

from __future__ import annotations

import pandas as pd

from dfs.season_long import objective
from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long import trade as tr
from dfs.season_long.config import LeagueConfig, SeasonLongConfig
from dfs.season_long.league_view import LeagueView, positions_of
from dfs.season_long.ros import RosResult

SLOTS = ["QB", "RB", "WR", "FLEX"]
WEEKS = [8, 9, 10, 11]

# Sam (1) is deep at WR and thin at RB; team 2 the reverse; team 3 is balanced. Points per week.
PLAYERS = {
    "qb1": ("QB", 1, 20), "rb1": ("RB", 1, 9), "wr1": ("WR", 1, 18), "wr2": ("WR", 1, 16),
    "wr3": ("WR", 1, 15),
    "qb2": ("QB", 2, 19), "rb2": ("RB", 2, 17), "rb3": ("RB", 2, 15), "rb4": ("RB", 2, 14),
    "wr4": ("WR", 2, 8),
    "qb3": ("QB", 3, 18), "rb5": ("RB", 3, 12), "wr5": ("WR", 3, 12), "wr6": ("WR", 3, 11),
    "rb6": ("RB", 3, 10),
    "fa1": ("WR", 0, 4),
}  # fmt: skip


def table() -> pd.DataFrame:
    rows = [
        {"player_id": p, "name": p.upper(), "position": pos, "fantasy_positions": (pos,), "team": "NFL",
         "injury_status": "", "search_rank": 1, "depth": 1}
        for p, (pos, _, _) in PLAYERS.items()
    ]  # fmt: skip
    return pd.DataFrame(rows).set_index("player_id")


def league(chopped: bool = False) -> sl.League:
    teams = []
    for rid in (1, 2, 3):
        players = [p for p, (_, owner, _) in PLAYERS.items() if owner == rid]
        teams.append(sl.Team(rid, f"u{rid}", f"m{rid}", f"Team {rid}", players, [], 3, 4, 0, 700.0, 50))
    return sl.League(
        name="Test", league_id="1", title="Test League", season=2026, scoring={}, slots=list(SLOTS),
        bench_slots=1, reserve_slots=0, playoff_week_start=0 if chopped else 11, playoff_teams=2,
        playoff_round_type=0, trade_deadline=11, waiver_budget=100, chopped=chopped,
        league_type=3 if chopped else 0, current_week=8, last_scored_week=7, teams=teams, my_roster_id=1,
        raw_settings={},
    )  # fmt: skip


def view(chopped: bool = False) -> LeagueView:
    t = table()
    lg = league(chopped)
    weekly = pd.DataFrame(
        [(p, w, float(pts), 0.3 * pts) for p, (_, _, pts) in PLAYERS.items() for w in WEEKS],
        columns=["player_id", "week", "pts", "sd"],
    )
    summary = t[["name", "position", "team", "injury_status", "search_rank"]].copy()
    summary["ros_pts"] = weekly.groupby("player_id")["pts"].sum()
    summary["ros_weighted"] = summary["ros_pts"]
    summary["playoff_pts"] = 0.0
    summary["next_week"] = weekly[weekly["week"] == 8].set_index("player_id")["pts"]
    summary["games_left"] = len(WEEKS)
    summary["games"], summary["season_rate"] = 7, summary["next_week"]
    cfg = SeasonLongConfig(
        leagues=[LeagueConfig(name="Test", league_id="1", username="u1")],
        starter_miss_rate=0.0,
        survival_draws=400,
    )
    v = LeagueView(lg, t, RosResult(weekly, summary, 4.0, {}, []), {}, {}, {}, None, positions_of(t), cfg)
    for team in lg.alive_teams():
        v.values[team.roster_id] = v.team_value(team.roster_id, team.players)
    v.strengths = {rid: tvm.slot_strength(x, weekly, lg.slots) for rid, x in v.values.items()}
    v.needs = tvm.needs(v.strengths)
    return v


def evaluator(v: LeagueView, monkeypatch) -> objective.Evaluator:
    monkeypatch.setattr(objective, "schedule", lambda lg, weeks: {w: [(1, 2)] for w in weeks})
    monkeypatch.setattr(objective, "pickup_prices", lambda lg: [20.0, 10.0, 5.0])
    return objective.Evaluator(v)


def market(v: LeagueView, consensus: dict[str, float] | None = None) -> tr.Market:
    ours = v.ros.summary["ros_pts"] - 16.0
    cons = pd.Series(consensus) if consensus is not None else ours
    return tr.Market(
        consensus=cons.clip(lower=0), ours=ours.clip(lower=0), points_per_dollar=1.0, faab_source="t"
    )
