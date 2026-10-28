"""The standings simulation, pure: playoff odds, byes and division titles for a redraft league, and the
strength-of-schedule numbers beside them. No network, no sheet.

**Odds** (`simulate`). Every remaining regular-season game is played `draws` times: each team's week is a
normal draw around its best lineup's expected points (`mean`, from `team_value`) with its lineup spread
(`sd`, scaled by `calibration.spread_scale`, measured on the leagues' real weekly scores); a head-to-head game
goes to the higher score; where the league plays the median (`league_average_match`) the week's second game
is won by every team above the week's median score (Sleeper counts that win in the record). Final
standings sort by wins, then points for. Seeds follow:

- the top `spots` teams make the playoffs, the top `byes` of them skip the first round (a bracket rounds up to
  a power of two, so 6 teams = 2 byes);
- with divisions and Sleeper's `playoff_seed_type` 1, each division's leader qualifies and is seeded ahead of
  the wildcards (the NFL way; this is our reading of Sleeper's setting, `Rules.division_winners_first`);
  otherwise divisions only name a division leader and the seeding ignores them.

**Schedule** (`defense_index`, `player_sos`). How soft an NFL defense is for a position, from the weekly
projections themselves: each player's projection against that defense over his own average projection,
averaged over the players who face it (weighted by that average, shrunk toward 1 when there are few
player-games).
A player's schedule is the mean index of his opponents over the weeks asked for; above 1 is easier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Shrink a defense's index toward 1 by this many player-games.
SOS_SHRINK = 8.0
# A player's average projection must reach this to count when measuring a defense; he needs this many weeks.
SOS_MIN_AVG = 4.0
SOS_MIN_WEEKS = 3
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True)
class Rules:
    spots: int
    byes: int = 0
    median_game: bool = False
    division: dict[int, int] = field(default_factory=dict)  # roster_id -> division (empty: none)
    division_winners_first: bool = False


def byes_for(spots: int) -> int:
    """A bracket rounds up to a power of two; the seeds it has no room for skip round one."""
    if spots < 2:
        return 0
    return (1 << (spots - 1).bit_length()) - spots


def rules_for(league) -> Rules:
    """`Rules` from a `sleeper_league.League` (divisions from the rosters, seeding from the settings)."""
    spots = league.playoff_teams or 6
    divisions = {t.roster_id: t.division for t in league.teams if t.division}
    seed_type = int(league.raw_settings.get("playoff_seed_type") or 0)
    return Rules(
        spots=spots,
        byes=byes_for(spots),
        median_game=bool(league.raw_settings.get("league_average_match")),
        division=divisions,
        division_winners_first=bool(divisions) and seed_type == 1,
    )


def seed_rank(wins: np.ndarray, points_for: np.ndarray, ids: list[int], rules: Rules) -> np.ndarray:
    """Seed of every team, 0 = best: wins, then points for; division leaders first when the rules say so.
    `wins` / `points_for`: draws x teams."""
    key = wins * 1e5 + points_for
    if rules.division_winners_first and rules.division:
        div = np.array([rules.division.get(i, 0) for i in ids])
        leader = np.zeros(key.shape, dtype=bool)
        rows = np.arange(key.shape[0])
        for d in set(div.tolist()) - {0}:
            members = np.where(div == d)[0]
            leader[rows, members[np.argmax(key[:, members], axis=1)]] = True
        key = key + leader * 1e9
    return (-key).argsort(axis=1).argsort(axis=1)


def division_leaders(wins: np.ndarray, points_for: np.ndarray, ids: list[int], rules: Rules) -> np.ndarray:
    """Boolean draws x teams: the team leads its division."""
    key = wins * 1e5 + points_for
    out = np.zeros(key.shape, dtype=bool)
    if not rules.division:
        return out
    div = np.array([rules.division.get(i, 0) for i in ids])
    rows = np.arange(key.shape[0])
    for d in set(div.tolist()) - {0}:
        members = np.where(div == d)[0]
        out[rows, members[np.argmax(key[:, members], axis=1)]] = True
    return out


@dataclass
class Standings:
    ids: list[int]
    p_playoffs: np.ndarray
    p_bye: np.ndarray
    p_division: np.ndarray  # nan where the league has no divisions
    exp_wins: np.ndarray
    p_top_seed: np.ndarray


def simulate(
    ids: list[int],
    mean: np.ndarray,
    sd: np.ndarray,
    normals: np.ndarray,
    pairs: list[list[tuple[int, int]]],
    wins: np.ndarray,
    points_for: np.ndarray,
    rules: Rules,
) -> Standings:
    """Play the remaining regular season. `mean` / `sd`: weeks x teams; `normals`: draws x weeks x teams;
    `pairs[i]`: week i's head-to-head games as roster id pairs; `wins` / `points_for`: the standings now."""
    draws = normals.shape[0]
    w = np.tile(wins.astype(float), (draws, 1))
    pf = np.tile(points_for.astype(float), (draws, 1))
    col = {rid: j for j, rid in enumerate(ids)}
    for i in range(mean.shape[0]):
        score = mean[i] + sd[i] * normals[:, i, :]
        pf += score
        for a, b in pairs[i]:
            if a in col and b in col:
                ja, jb = col[a], col[b]
                w[:, ja] += score[:, ja] > score[:, jb]
                w[:, jb] += score[:, jb] > score[:, ja]
        if rules.median_game:
            w += score > np.median(score, axis=1, keepdims=True)
    rank = seed_rank(w, pf, ids, rules)
    has_div = bool(rules.division)
    return Standings(
        ids=list(ids),
        p_playoffs=(rank < rules.spots).mean(axis=0),
        p_bye=(rank < rules.byes).mean(axis=0),
        p_division=division_leaders(w, pf, ids, rules).mean(axis=0) if has_div else np.full(len(ids), np.nan),
        exp_wins=w.mean(axis=0),
        p_top_seed=(rank == 0).mean(axis=0),
    )


def opponent_strength(
    ids: list[int], mean: np.ndarray, pairs: list[list[tuple[int, int]]]
) -> dict[int, float]:
    """Per team: its remaining opponents' average expected weekly points, over the league's average team
    (points per game; a positive number is a harder schedule). Teams with no head-to-head game left: nan."""
    col = {rid: j for j, rid in enumerate(ids)}
    league_avg = float(mean.mean()) if mean.size else 0.0
    faced: dict[int, list[float]] = {rid: [] for rid in ids}
    for i, games in enumerate(pairs):
        for a, b in games:
            if a in col and b in col:
                faced[a].append(float(mean[i, col[b]]))
                faced[b].append(float(mean[i, col[a]]))
    return {rid: (float(np.mean(v)) - league_avg if v else math.nan) for rid, v in faced.items()}


# -- strength of schedule ----------------------------------------------------------------------------------


def defense_index(proj: pd.DataFrame, position: pd.Series) -> pd.DataFrame:
    """Per NFL defense and position, how many times a player's own average the projections give his
    opponents against it (1.0 = average). `proj`: [player_id, week, proj, team, opponent]; `position`:
    player_id -> position. Index: team (the defense), columns: positions."""
    df = proj[(proj["proj"] > 0) & (proj["opponent"].astype(str).str.len() > 0)].copy()
    df["position"] = df["player_id"].map(position)
    df = df.dropna(subset=["position"])
    g = df.groupby("player_id")["proj"]
    df["avg"] = g.transform("mean")
    df["games"] = g.transform("size")
    df = df[(df["avg"] >= SOS_MIN_AVG) & (df["games"] >= SOS_MIN_WEEKS)]
    df["factor"] = df["proj"] / df["avg"]
    out: dict[str, dict[str, float]] = {}
    for (opp, pos), grp in df.groupby(["opponent", "position"]):
        raw = float(np.average(grp["factor"], weights=grp["avg"]))
        n = len(grp)
        out.setdefault(pos, {})[opp] = 1.0 + (raw - 1.0) * n / (n + SOS_SHRINK)
    return pd.DataFrame(out).reindex(columns=list(POSITIONS))


def player_sos(proj: pd.DataFrame, summary: pd.DataFrame, index: pd.DataFrame, weeks: list[int]) -> pd.Series:
    """Per player_id: the mean defense index of his opponents in `weeks` (nan with no game in them)."""
    if not weeks:
        return pd.Series(dtype=float, index=summary.index)
    sub = proj[proj["week"].isin(weeks) & (proj["opponent"].astype(str).str.len() > 0)]
    opp_by_team = {(w, t): o for w, t, o in zip(sub["week"], sub["team"], sub["opponent"], strict=True)}
    out = {}
    for pid, team, pos in zip(summary.index, summary["team"], summary["position"], strict=True):
        vals = []
        for w in weeks:
            opp = opp_by_team.get((w, team))
            if opp is not None and pos in index.columns and opp in index.index:
                v = index.at[opp, pos]
                if not pd.isna(v):
                    vals.append(float(v))
        out[pid] = float(np.mean(vals)) if vals else math.nan
    return pd.Series(out)


def team_sos(roster: list[str], sos: pd.Series, weights: pd.Series) -> float:
    """A roster's schedule: its players' index averaged by their points in those weeks (nan with none)."""
    have = [p for p in roster if p in sos.index and not pd.isna(sos[p]) and weights.get(p, 0.0) > 0]
    if not have:
        return math.nan
    return float(np.average([sos[p] for p in have], weights=[weights[p] for p in have]))
