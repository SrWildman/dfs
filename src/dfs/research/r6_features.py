"""R6 features: per-game usage measures and their prior-only L3 / prior-6 windows.

Everything is built per player-game from the public history (stats_player, reduced pbp, snap counts, the PFR
-> gsis crosswalk), then rolled into three numbers per metric for each game, computed from EARLIER games
only:

  `<m>_l3`     the metric over the last 3 games the player played (a level)
  `<m>_prior`  the metric over the 6 games before those
  `<m>_chg`    `_l3` minus `_prior` (a change)

A "game played" is a row in stats_player (REG season), windows continue across the season boundary (as
`dfs.model.features` does), and a window must be full: a level needs 3 earlier games, a change needs 9.
Count and share metrics are the MEAN of the per-game value; ratio metrics (aDOT, goal-line share) are a
ratio of window sums (`sum(num) / sum(den)`), which weights a game by its volume rather than giving a
1-target game the same say as a 10-target one. `tests/research/test_r6_features.py` pins the property that
matters: appending the current game, or putting absurd values in it and every later game, leaves every
feature of that game unchanged."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research.common import OFFENSE_POSITIONS
from dfs.research.pbp_features import RED_ZONE_YARDS, offense_game_table

RECENT, PRIOR = 3, 6
MIN_PRIOR_GAMES = RECENT + PRIOR  # a change needs this many earlier games; a level needs RECENT

GOAL_LINE_YARDS = 5  # "inside the 5": yardline_100 <= 5
HIGH_VALUE_YARDS = 10  # "inside the 10": yardline_100 <= 10
DEEP_AIR_YARDS = 20  # a deep target: air_yards >= 20

# metric -> (numerator column, denominator column or None for a mean of the per-game value)
METRICS: dict[str, tuple[str, str | None]] = {
    "tgt_share": ("target_share", None),
    "carry_share": ("carry_share", None),
    "ay_share": ("air_yards_share", None),
    "wopr": ("wopr", None),
    "rec_pg": ("receptions", None),
    "tgt_pg": ("targets", None),
    "adot": ("receiving_air_yards", "targets"),
    "snap_pct": ("snap_pct", None),
    "ez_tgt_pg": ("ez_targets", None),
    "deep_tgt_pg": ("deep_targets", None),
    "hvt_pg": ("hvt", None),
    "rz_pg": ("rz_opps", None),
    "qb_rush_pg": ("qb_designed_rushes", None),
    "gl_share": ("gl_carries", "team_gl_carries"),
}
TEAM_METRICS: dict[str, tuple[str, str | None]] = {"proe": ("proe", None)}

GAME_KEY = ["gsis_id", "season", "week"]


# --------------------------------------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------------------------------------


def _rolling_sum(prev: pd.Series, key: pd.Series, window: int) -> pd.Series:
    """Trailing `window`-sum of `prev` (already shifted to earlier games) within `key`, NaN unless the window
    is full of non-NaN values. Aligned to `prev`'s index."""
    out = prev.groupby(key, sort=False).rolling(window, min_periods=window).sum()
    return out.reset_index(level=0, drop=True).reindex(prev.index)


def window_features(
    frame: pd.DataFrame,
    metrics: dict[str, tuple[str, str | None]],
    key: str = "gsis_id",
) -> pd.DataFrame:
    """`<m>_l3`, `<m>_prior` and `<m>_chg` for every metric, one row per row of `frame`, aligned to its index.

    `frame` needs `key` and a sortable time `t` (season * 100 + week) and one row per key per game. Features
    use `shift(1)` before any window, so a row's own game is never included."""
    ordered = frame.sort_values([key, "t"], kind="mergesort")
    grp = ordered[key]
    out = {}
    for name, (num, den) in metrics.items():
        cols = [num] if den is None else [num, den]
        sums = {}
        for col in cols:
            prev = ordered.groupby(key, sort=False)[col].shift(1)
            r3 = _rolling_sum(prev, grp, RECENT)
            r9 = _rolling_sum(prev, grp, MIN_PRIOR_GAMES)
            sums[col] = (r3, r9 - r3)
        if den is None:
            l3, prior = sums[num][0] / RECENT, sums[num][1] / PRIOR
        else:
            with np.errstate(divide="ignore", invalid="ignore"):
                d_l3 = sums[den][0].where(sums[den][0] > 0)
                d_pr = sums[den][1].where(sums[den][1] > 0)
                l3, prior = sums[num][0] / d_l3, sums[num][1] / d_pr
        out[f"{name}_l3"] = l3
        out[f"{name}_prior"] = prior
        out[f"{name}_chg"] = l3 - prior
    return pd.DataFrame(out, index=ordered.index).reindex(frame.index)


# --------------------------------------------------------------------------------------------------------
# Per-game measures
# --------------------------------------------------------------------------------------------------------


def box_scores(sp: pd.DataFrame) -> pd.DataFrame:
    """Per skill-position player-game from stats_player: targets, receptions, carries, receiving air yards,
    and the player's share of his team's targets / carries / air yards that game. Target share, air-yards
    share and WOPR (`1.5 * target share + 0.7 * air-yards share`) are nflverse's own columns when the file
    carries them (they use the team's full target and air-yard totals, which is what `dfs.model` reads too);
    otherwise, and for carry share, they are the player's count over the sum of every stats_player row of
    the team-week."""
    s = sp.rename(columns={"player_id": "gsis_id"}).copy()
    for c in ("targets", "receptions", "carries", "receiving_air_yards"):
        s[c] = s[c].fillna(0.0)
    team_key = ["team", "season", "week"]
    for c, share in (
        ("targets", "target_share"),
        ("carries", "carry_share"),
        ("receiving_air_yards", "air_yards_share"),
    ):
        total = s.groupby(team_key)[c].transform("sum")
        own = pd.Series(np.where(total > 0, s[c] / total.where(total > 0, 1.0), 0.0), index=s.index)
        s[share] = s[share].fillna(own) if share in s and share != "carry_share" else own
    if "wopr" in s:
        s["wopr"] = s["wopr"].fillna(1.5 * s["target_share"] + 0.7 * s["air_yards_share"])
    else:
        s["wopr"] = 1.5 * s["target_share"] + 0.7 * s["air_yards_share"]
    s = s[s["position"].isin(OFFENSE_POSITIONS)]
    s["t"] = s["season"] * 100 + s["week"]
    cols = [
        *GAME_KEY,
        "t",
        "team",
        "position",
        "targets",
        "receptions",
        "carries",
        "receiving_air_yards",
        "target_share",
        "carry_share",
        "air_yards_share",
        "wopr",
    ]
    return s[cols].drop_duplicates(GAME_KEY).reset_index(drop=True)


def _plays(pbp: pd.DataFrame) -> pd.DataFrame:
    """Pass and run plays from scrimmage by a team on offense, no two-point tries. nflverse files a QB
    kneel and a spike under their own play types, so they are out; a QB SCRAMBLE is a `run` play with
    `qb_scramble == 1` (and `pass == 1`, `rush == 0` in this release), so it is in."""
    p = pbp[pbp["play_type"].isin(["pass", "run"]) & pbp["posteam"].notna()]
    return p[p["two_point_attempt"] != 1]


def pbp_player_counts(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (player, game) counts from play-by-play, one row per player with at least one target or carry:

    ez_targets   targets whose air yards reach the goal line: `air_yards >= yardline_100`
    deep_targets targets with `air_yards >= 20`
    gl_carries   carries from inside the 5: `yardline_100 <= 5` (scrambles included, kneels not)
    hvt          high-value touches: targets (anywhere) plus carries from inside the 10
    rz_opps      red-zone opportunities: targets plus carries from inside the 20
    qb_designed_rushes  carries that are not scrambles (`qb_scramble == 0`); meaningful for QBs
    """
    p = _plays(pbp)
    tgt = p[(p["pass"] == 1) & p["receiver_player_id"].notna()].copy()
    tgt["gsis_id"] = tgt["receiver_player_id"]
    tgt["ez_targets"] = (tgt["air_yards"] >= tgt["yardline_100"]).astype(float)
    tgt["deep_targets"] = (tgt["air_yards"] >= DEEP_AIR_YARDS).astype(float)
    tgt["targets_pbp"] = 1.0
    tgt["hvt"] = 1.0
    tgt["rz_opps"] = (tgt["yardline_100"] <= RED_ZONE_YARDS).astype(float)
    rsh = p[(p["play_type"] == "run") & p["rusher_player_id"].notna()].copy()
    rsh["gsis_id"] = rsh["rusher_player_id"]
    rsh["carries_pbp"] = 1.0
    rsh["gl_carries"] = (rsh["yardline_100"] <= GOAL_LINE_YARDS).astype(float)
    rsh["hvt"] = (rsh["yardline_100"] <= HIGH_VALUE_YARDS).astype(float)
    rsh["rz_opps"] = (rsh["yardline_100"] <= RED_ZONE_YARDS).astype(float)
    rsh["qb_designed_rushes"] = (rsh["qb_scramble"] != 1).astype(float)
    t_cols = ["targets_pbp", "ez_targets", "deep_targets", "hvt", "rz_opps"]
    r_cols = ["carries_pbp", "gl_carries", "hvt", "rz_opps", "qb_designed_rushes"]
    key = ["gsis_id", "season", "week"]
    t = tgt.groupby(key, as_index=False)[t_cols].sum()
    r = rsh.groupby(key, as_index=False)[r_cols].sum()
    out = t.merge(r, on=key, how="outer", suffixes=("_t", "_r")).fillna(0.0)
    for c in ("hvt", "rz_opps"):
        out[c] = out[f"{c}_t"] + out[f"{c}_r"]
        out = out.drop(columns=[f"{c}_t", f"{c}_r"])
    return out


def team_goal_line_carries(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (team, game): carries from inside the 5 by anyone (the denominator of goal-line carry share)."""
    p = _plays(pbp)
    r = p[(p["play_type"] == "run") & p["rusher_player_id"].notna() & (p["yardline_100"] <= GOAL_LINE_YARDS)]
    out = r.groupby(["posteam", "season", "week"], as_index=False).size()
    return out.rename(columns={"posteam": "team", "size": "team_gl_carries"})


def snap_shares(snaps: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Per (gsis player, season, week): offensive snap share (0-1), through the PFR -> gsis crosswalk. A
    PFR id that maps to two gsis ids, or the reverse, is dropped rather than guessed."""
    cw = players[["pfr_id", "gsis_id"]].drop_duplicates()
    cw = cw[~cw["pfr_id"].duplicated(keep=False) & ~cw["gsis_id"].duplicated(keep=False)]
    s = snaps.merge(cw, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    s = s.groupby(["gsis_id", "season", "week"], as_index=False)["offense_pct"].max()
    return s.rename(columns={"offense_pct": "snap_pct"})


def team_proe(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (team, game): mean pass rate over expected, in percentage points (R2's `proe`)."""
    g = offense_game_table(pbp)
    return g[["team", "season", "week", "t", "proe"]].copy()


def player_game_frame(
    sp: pd.DataFrame, pbp: pd.DataFrame, snaps: pd.DataFrame, players: pd.DataFrame
) -> pd.DataFrame:
    """One row per skill-position player-game with every per-game measure the windows read. A player with a
    stats_player row but no play-by-play touches has zeros there (he played and was not targeted / did not
    carry); a missing snap row is NaN (an unmatched id, not zero snaps), which keeps that game out of any
    snap window."""
    box = box_scores(sp)
    counts = pbp_player_counts(pbp)
    out = box.merge(counts, on=GAME_KEY, how="left")
    pbp_cols = [c for c in counts.columns if c not in GAME_KEY]
    out[pbp_cols] = out[pbp_cols].fillna(0.0)
    out = out.merge(team_goal_line_carries(pbp), on=["team", "season", "week"], how="left")
    out["team_gl_carries"] = out["team_gl_carries"].fillna(0.0)
    out = out.merge(snap_shares(snaps, players), on=GAME_KEY, how="left")
    out = out.merge(
        team_proe(pbp)[["team", "season", "week", "proe"]], on=["team", "season", "week"], how="left"
    )
    return out.sort_values(["gsis_id", "t"]).reset_index(drop=True)


def usage_features(games: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """Window features for every metric, per player-game: (gsis_id, season, week) plus `<m>_l3 / _prior
    / _chg`. The team-level metric (pass rate over expected) is windowed over the team's own games and
    attached to each of its players' games."""
    feats = window_features(games, METRICS)
    out = pd.concat([games[GAME_KEY + ["team"]], feats], axis=1)
    tg = team_games.copy()
    tg["t"] = tg["season"] * 100 + tg["week"]
    tfeat = window_features(tg, TEAM_METRICS, key="team")
    tfeat = pd.concat([tg[["team", "season", "week"]], tfeat], axis=1)
    return out.merge(tfeat, on=["team", "season", "week"], how="left")
