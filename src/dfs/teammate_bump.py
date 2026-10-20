"""The teammate bump: what a starter's absence adds to the players around him (a SHADOW).

`research/r7_teammate_bump.py` measured, over 2014-2025, how many DK points a teammate scores above UM's
projection when a regular (WR1, WR2, TE1 or RB1) is out, per (absent role, teammate role), and shrank each
cell toward 0. `models/research/teammate_bump.csv` holds the cells that survive (enough events, the fit and
test periods agree in sign). This module reads it:

- `teammate_bump(out_player, team_frame)` -- the points to add to each teammate's projection;
- `with_teammate_bump(frame, scored, roles)` -- the same for a scored table, as the `TeamBumpPts` column that
  Model Check races beside TFFB and CalPts ("CalPts + teammate bump (shadow)", scored like the RB1 spread
  row).

It touches neither CalPts nor late swap. It joins CalPts only if it beats CalPts over enough weeks, and the
research measured it against UM, so whether the market projections have already priced the absence in is
exactly what the live race answers.

**Roles are the research's**: a player's rank by usage over his team's previous three games (`WR1`, `RB2`,
`RB3+`, ...), and a "regular" is a player with at least 15% of the team's targets or 30% of its carries over
that window. The absent player is a regular listed OUT or IR (`Avail`) before kickoff; the bump applies only
when exactly one regular is out (a team-game with two out was left out of the research).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.derived import OUT_STATUSES
from dfs.paths import REPO_ROOT
from dfs.player_join import join_key

TABLE_PATH = REPO_ROOT / "models" / "research" / "teammate_bump.csv"
SOURCE = "TeamBumpPts"
LABEL = "CalPts + teammate bump (shadow)"
SHIPPED_ABSENT = ("WR1", "WR2", "TE1", "RB1")
DECIMALS = 2


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[tuple[str, str], float]:
    table = pd.read_csv(path)
    table = table[table["absent"].isin(SHIPPED_ABSENT) & (table["bump"] != 0) & table["bump"].notna()]
    return {(r.absent, r.beneficiary): float(r.bump) for r in table.itertuples()}


def load_table(path: Path | None = None) -> dict[tuple[str, str], float]:
    """`{(absent role, teammate role): points}` for the cells that ship. Empty when the file is missing."""
    path = path or TABLE_PATH
    return _load(path) if path.exists() else {}


def teammate_bump(
    out_player, team_frame: pd.DataFrame, table: dict[tuple[str, str], float] | None = None
) -> pd.Series:
    """Points to add to each of `team_frame`'s players because `out_player` is out.

    `out_player` has a `label` (his role, e.g. "WR1"); `team_frame` has a `label` column (each teammate's
    role) and, optionally, `Avail`: a player who is himself OUT or IR gets 0, as does anyone whose
    (absent role, role) cell is not in the table. Indexed like `team_frame`."""
    table = load_table() if table is None else table
    absent = out_player["label"] if "label" in out_player else None
    bump = [table.get((absent, label), 0.0) for label in team_frame["label"]]
    out = pd.Series(bump, index=team_frame.index, dtype=float)
    if "Avail" in team_frame.columns:
        out[team_frame["Avail"].isin(OUT_STATUSES)] = 0.0
    return out


def season_roles(season: int) -> pd.DataFrame:
    """`team, season, week, gsis_id, label, regular`: every player in each team's prior-three-game rotation
    for each game of `season` the nflverse cache has played, with the research's role label. Raises
    `ResearchDataError` when the cache is missing."""
    from dfs.research import data
    from dfs.research import r1_redistribution as r1

    sp, games = data.read_stats_player([season]), data.read_games([season])
    tg = r1.team_game_index(games)
    prior = r1.prior_windows(r1.offense_rows(sp, tg), team_games=tg)
    rot = r1.rank_rotation(prior)
    rot = rot.merge(
        tg.rename(columns={"gidx": "tidx"})[["team", "season", "tidx", "week"]], on=["team", "season", "tidx"]
    )
    rot["regular"] = (rot["share_t"] >= r1.TARGET_REGULAR) | (rot["share_c"] >= r1.CARRY_REGULAR)
    keep = ["team", "season", "week", "gsis_id", "name", "position", "label", "regular"]
    return rot[keep].reset_index(drop=True)


def _key(frame: pd.DataFrame, name: str, team: str, position: str) -> pd.Series:
    return pd.Series(
        [join_key(n, t, p) for n, t, p in zip(frame[name], frame[team], frame[position], strict=True)],
        index=frame.index,
    )


def absent_by_team_week(scored: pd.DataFrame, roles: pd.DataFrame) -> pd.Series:
    """The role of the one regular listed OUT / IR, indexed by (week, Team); team-weeks with none or with more
    than one are left out. A player who sat the game has no stat line, so DK's row has no `GsisId`: he is
    found
    in the rotation by name, team and position (`player_join.join_key`)."""
    out = scored[scored["Avail"].isin(OUT_STATUSES)]
    reg = roles[roles["regular"]]
    if out.empty or reg.empty:
        return pd.Series(dtype=object, index=pd.MultiIndex.from_arrays([[], []], names=["week", "Team"]))
    out = out.assign(_key=_key(out, "Name", "Team", "Position"))
    reg = reg.assign(_key=_key(reg, "name", "team", "position"))
    m = out.merge(reg[["week", "_key", "label"]], on=["week", "_key"])
    m = m.drop_duplicates(["week", "Team", "_key"])
    counts = m.groupby(["week", "Team"])["_key"].transform("size")
    return m[counts == 1].set_index(["week", "Team"])["label"]


def with_teammate_bump(
    frame: pd.DataFrame,
    scored: pd.DataFrame,
    roles: pd.DataFrame,
    table: dict[tuple[str, str], float] | None = None,
) -> pd.DataFrame:
    """`frame` (rows with `week`, `Team`, `GsisId`, `CalPts`) plus `TeamBumpPts`: `CalPts` plus the bump for
    players on a team whose one regular out is a role in the table; NaN for everyone else (nobody out, two
    out,
    a role without a cell). `scored` is the whole week's table (the out player is not in `frame`)."""
    table = load_table() if table is None else table
    out = frame.copy()
    out[SOURCE] = np.nan
    if out.empty or "CalPts" not in out.columns or not table:
        return out
    absent = absent_by_team_week(scored, roles)
    if absent.empty:
        return out
    labels = roles.rename(columns={"gsis_id": "GsisId", "team": "Team", "label": "_role"})[
        ["week", "Team", "GsisId", "_role"]
    ]
    key = out.merge(labels, on=["week", "Team", "GsisId"], how="left")["_role"].to_numpy()
    gone = pd.Series(list(zip(out["week"], out["Team"], strict=True))).map(absent.to_dict()).to_numpy()
    bump = np.array([table.get((a, b), np.nan) for a, b in zip(gone, key, strict=True)], dtype=float)
    calpts = pd.to_numeric(out["CalPts"], errors="coerce").to_numpy()
    out[SOURCE] = np.round(calpts + bump, DECIMALS)
    return out
