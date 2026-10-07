"""Team-game offense and red-zone usage tables from the reduced play-by-play (`dfs.research.data`)."""

from __future__ import annotations

import numpy as np
import pandas as pd

RED_ZONE_YARDS = 20


def _scrimmage(pbp: pd.DataFrame) -> pd.DataFrame:
    """Pass and rush plays from scrimmage: no kneels, spikes or two-point tries; a team on offense."""
    p = pbp[pbp["posteam"].notna() & ((pbp["pass"] == 1) | (pbp["rush"] == 1))]
    drop = (p["qb_kneel"] == 1) | (p["qb_spike"] == 1) | (p["two_point_attempt"] == 1)
    return p[~drop]


def offense_game_table(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (offense, game): `plays` (pace), `epa_pass` and `epa_rush` per play, and `proe` (mean
    pass rate over expected, in percentage points). Read from the opposing side, `epa_pass` / `epa_rush`
    are what the defense allowed."""
    p = _scrimmage(pbp)
    p = p.assign(
        epa_pass=p["epa"].where(p["pass"] == 1),
        epa_rush=p["epa"].where(p["rush"] == 1),
        pass_oe_play=p["pass_oe"],
    )
    out = p.groupby(["game_id", "season", "week", "posteam", "defteam"], as_index=False).agg(
        plays=("epa", "size"),
        epa_pass=("epa_pass", "mean"),
        epa_rush=("epa_rush", "mean"),
        proe=("pass_oe_play", "mean"),
    )
    out = out.rename(columns={"posteam": "team", "defteam": "opp"})
    out["t"] = out["season"] * 100 + out["week"]
    return out.sort_values(["team", "t"]).reset_index(drop=True)


def red_zone_usage(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (player, game): red-zone targets and carries (inside the 20) and the player's share of his
    team's red-zone opportunities in that game. Opportunities are targets + carries credited to a player;
    sacks and scrambles-as-dropbacks credit nobody. One row per player-game with at least one."""
    p = _scrimmage(pbp)
    p = p[p["yardline_100"] <= RED_ZONE_YARDS]
    tgt = p[p["receiver_player_id"].notna()].rename(columns={"receiver_player_id": "gsis_id"})
    rsh = p[p["rusher_player_id"].notna()].rename(columns={"rusher_player_id": "gsis_id"})
    key = ["game_id", "season", "week", "posteam", "gsis_id"]
    t = tgt.groupby(key, as_index=False).size().rename(columns={"size": "rz_targets"})
    r = rsh.groupby(key, as_index=False).size().rename(columns={"size": "rz_carries"})
    out = t.merge(r, on=key, how="outer").fillna({"rz_targets": 0, "rz_carries": 0})
    out["rz_opps"] = out["rz_targets"] + out["rz_carries"]
    team_opps = out.groupby(["game_id", "posteam"])["rz_opps"].transform("sum")
    out["rz_share"] = np.where(team_opps > 0, out["rz_opps"] / team_opps.where(team_opps > 0, 1.0), 0.0)
    return out.rename(columns={"posteam": "team"}).reset_index(drop=True)
