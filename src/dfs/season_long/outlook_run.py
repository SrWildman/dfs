"""One league's outlook, assembled for the sheet: playoff odds, byes, divisions, survival curves, power
rankings with their week-over-week move, and strength of schedule for every team and player.

The odds are the evaluator's own `base()` (so the sheet, the trade finder and this tab never disagree);
`outlook.py` has the simulation and `calibration.py` the spread scale. Schedule numbers come from the weekly
projections (`outlook.defense_index`). Pure given a `LeagueRun`'s view and evaluator, apart from reading the
projections (cached) and the history file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from dfs.season_long import history, outlook, ros


@dataclass
class TeamOutlook:
    roster_id: int
    label: str
    manager: str
    mine: bool
    record: str
    rank: int  # power rank: 1 = most valuable roster
    move: int | None  # places gained since the previous snapshot (None: no earlier one)
    value: float
    p_playoffs: float = math.nan
    p_bye: float = math.nan
    p_division: float = math.nan
    exp_wins: float = math.nan
    p_survive: float = math.nan  # chopped: this week
    exp_weeks: float = math.nan
    p_last: float = math.nan
    alive: tuple[float, ...] = ()  # chopped: P(alive after each remaining week)
    opp_strength: float = math.nan  # points per game: remaining opponents over the league's average team
    nfl_ros: float = math.nan  # the starters' mean defense index over the rest of the season (1 = average)
    nfl_playoffs: float = math.nan


@dataclass
class LeagueOutlook:
    league: str
    through: int
    weeks: list[int]  # remaining weeks (a chopped curve's columns)
    teams: list[TeamOutlook]  # best power rank first
    rules: str = ""
    spread_note: str = ""
    sos_ros: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    sos_playoffs: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    index: pd.DataFrame = field(default_factory=pd.DataFrame)

    def team(self, roster_id: int) -> TeamOutlook:
        return next(t for t in self.teams if t.roster_id == roster_id)


def rules_text(view) -> str:
    lg = view.league
    n = len(lg.alive_teams())
    if lg.chopped:
        return f"Chopped: the lowest score of each week is out; {n} teams alive."
    r = outlook.rules_for(lg)
    parts = [f"{r.spots} of {n} teams make the playoffs"]
    if r.byes:
        parts.append(f"the top {r.byes} skip round one")
    parts.append(
        "seeded by wins, then points for" + (" (a median game counts as a win)" if r.median_game else "")
    )
    if r.division:
        parts.append(
            "division leaders seeded first (our reading of Sleeper's setting)"
            if r.division_winners_first
            else "divisions do not affect the seeding"
        )
    return "; ".join(parts) + "."


def compute(run, hist: dict[int, dict] | None = None) -> LeagueOutlook:
    """`run`: a `season_tabs.LeagueRun`. `hist`: `history.load(league)` (for the power move)."""
    view, ev = run.view, run.ev
    lg = view.league
    through = lg.last_scored_week
    hist = hist if hist is not None else history.load(lg.name)
    base = ev.base()
    order = sorted(base, key=lambda r: -base[r].value)
    rank = {rid: i + 1 for i, rid in enumerate(order)}
    mean, _ = ev.team_arrays()
    opp: dict[int, float] = {}
    if not lg.chopped:
        reg = [i for i, w in enumerate(ev.weeks) if w in ev.regular]
        opp = outlook.opponent_strength(ev.ids, mean[reg], [ev.pairs.get(ev.weeks[i], []) for i in reg])
    sos_ros, sos_po, idx = schedule_strength(view)
    weekly = view.ros.weekly
    pts_ros = weekly.groupby("player_id")["pts"].sum()
    pts_po = weekly[weekly["week"].isin(lg.playoff_weeks)].groupby("player_id")["pts"].sum()
    teams = []
    for t in lg.alive_teams():
        o = base[t.roster_id]
        teams.append(
            TeamOutlook(
                roster_id=t.roster_id,
                label=t.label,
                manager=t.manager,
                mine=t.roster_id == lg.my_roster_id,
                record="" if lg.chopped else f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
                rank=rank[t.roster_id],
                move=history.rank_move(hist, through, t.roster_id, rank[t.roster_id]),
                value=o.value,
                p_playoffs=math.nan if lg.chopped else o.p_playoffs,
                p_bye=math.nan if lg.chopped else o.p_bye,
                p_division=math.nan if lg.chopped else o.p_division,
                exp_wins=math.nan if lg.chopped else o.exp_wins,
                p_survive=o.p_survive_now if lg.chopped else math.nan,
                exp_weeks=o.expected_weeks if lg.chopped else math.nan,
                p_last=o.p_last_standing if lg.chopped else math.nan,
                alive=o.alive,
                opp_strength=opp.get(t.roster_id, math.nan),
                nfl_ros=outlook.team_sos(ev.rosters[t.roster_id], sos_ros, pts_ros),
                nfl_playoffs=outlook.team_sos(ev.rosters[t.roster_id], sos_po, pts_po),
            )
        )
    teams.sort(key=lambda x: x.rank)
    return LeagueOutlook(
        league=lg.name,
        through=through,
        weeks=list(ev.weeks),
        teams=teams,
        rules=rules_text(view),
        spread_note=view.spread_note,
        sos_ros=sos_ros,
        sos_playoffs=sos_po,
        index=idx,
    )


def schedule_strength(view) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Per player: the mean defense index of his remaining opponents, and of his playoff-week opponents."""
    lg = view.league
    last = max(lg.last_week, lg.last_scored_week)
    proj = ros.projections(lg.scoring, lg.season, list(range(1, last + 1)), lg.last_scored_week)
    index = outlook.defense_index(proj, view.table["position"])
    s = view.ros.summary
    return (
        outlook.player_sos(proj, s, index, lg.remaining_weeks),
        outlook.player_sos(proj, s, index, lg.playoff_weeks),
        index,
    )


def snapshot(run) -> dict[int, dict]:
    """This league's history snapshot (see `history`)."""
    return history.snapshot_teams(run.ev.base(), run.view.league.chopped, run.view)


def save_history(run) -> bool:
    lg = run.view.league
    return history.save_snapshot(lg.name, lg.last_scored_week, snapshot(run), "sync")
