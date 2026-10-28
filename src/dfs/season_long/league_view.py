"""One league, assembled: Sleeper's state, rest-of-season points, every team's value and needs, and (chopped
leagues) the survival simulation. The CLI, the analyzer, the finder and the sheet all start from here."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dfs.season_long import ros
from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long.config import LeagueConfig, SeasonLongConfig


@dataclass
class LeagueView:
    league: sl.League
    table: pd.DataFrame
    ros: ros.RosResult
    values: dict[int, tvm.TeamValue]
    strengths: dict[int, dict[str, float]]
    needs: dict[int, list[tuple[str, float, int]]]
    survival: tvm.Survival | None
    positions: dict[str, tuple[str, ...]]
    cfg: SeasonLongConfig

    def name(self, pid: str) -> str:
        return str(self.table.at[pid, "name"]) if pid in self.table.index else pid

    def value_rank(self, roster_id: int) -> int:
        if self.survival is not None:
            order = sorted(self.survival.value, key=lambda r: -self.survival.value[r])
        else:
            order = sorted(self.values, key=lambda r: -self.values[r].value)
        return order.index(roster_id) + 1 if roster_id in order else 0

    def team_value(self, roster_id: int, roster: list[str]) -> tvm.TeamValue:
        lg = self.league
        return tvm.team_value(
            roster_id,
            roster,
            self.ros.weekly,
            self.positions,
            lg.slots,
            self.cfg.starter_miss_rate,
            lg.playoff_weeks,
            self.cfg.playoff_weight,
        )


def positions_of(table: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    return dict(zip(table.index, table["fantasy_positions"], strict=False))


def build(lc: LeagueConfig, cfg: SeasonLongConfig, table: pd.DataFrame | None = None) -> LeagueView:
    if lc.platform != "sleeper":
        raise sl.SleeperError(f"{lc.name}: {lc.platform} leagues come in phase 2; only Sleeper syncs today.")
    league = sl.load_league(lc.name, lc.league_id, lc.username)
    if lc.playoff_week_start and not league.playoff_week_start:
        league.playoff_week_start = lc.playoff_week_start
    table = ros.player_table(sl.fetch_players()) if table is None else table
    result = ros.build_ros(league, table, ir_weeks_out=cfg.ir_weeks_out, playoff_weight=cfg.playoff_weight)
    positions = positions_of(table)
    view = LeagueView(league, table, result, {}, {}, {}, None, positions, cfg)
    for t in league.alive_teams():
        view.values[t.roster_id] = view.team_value(t.roster_id, t.players)
    view.strengths = {
        rid: tvm.slot_strength(v, result.weekly, league.slots) for rid, v in view.values.items()
    }
    view.needs = tvm.needs(view.strengths)
    if league.chopped:
        view.survival = tvm.survival(
            {rid: v.weeks for rid, v in view.values.items()}, cfg.survival_decay, cfg.survival_draws
        )
    return view
