"""The mechanism behind game script: how a team's play-calling and its players' volume move with the pre-game
spread and with the score DURING the game.

`script.py` asks whether a role pair's correlation depends on the spread. This module shows WHY it might
(or might not): the nflverse play-by-play gives the in-game score differential on every snap, so it can
say how much a team passes when it trails by 14 against when it leads by 14, how much of that is already in
the pre-game spread, and how the realized margin (known only after the game) ties two players' surprises
together. Descriptive: nothing here feeds the simulator.

The play-by-play is fetched from nflverse's release assets (one parquet per season, about 20 MB) and reduced
to the handful of columns used, cached as `data/model_cache/pbp_script_<season>.parquet` (gitignored, like
the rest of `dfs model fetch`'s cache).
"""

from __future__ import annotations

import io

import httpx
import numpy as np
import pandas as pd

from dfs.model import data
from dfs.sim.script import SPREAD_BUCKETS, spread_bucket

PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
PBP_COLUMNS = [
    "game_id",
    "posteam",
    "season_type",
    "qtr",
    "score_differential",
    "qb_dropback",
    "qb_spike",
    "qb_kneel",
    "qb_scramble",
    "rush",
    "game_seconds_remaining",
    "pass_oe",
]

# The offense's lead (+) or deficit (-) at the snap, in the buckets football people use.
STATE_LABELS = ("trail 15+", "trail 9-14", "trail 1-8", "tied", "lead 1-8", "lead 9-14", "lead 15+")
STATE_EDGES = (-15, -9, -1, 0, 1, 9, 15)  # lower edge of each label above


def state_bucket(diff) -> np.ndarray:
    """Offense's score differential at the snap -> one of `STATE_LABELS`."""
    d = np.asarray(diff, dtype=float)
    conds = [d <= -15, d <= -9, d <= -1, d == 0, d <= 8, d <= 14]
    return np.select(conds, list(STATE_LABELS[:-1]), STATE_LABELS[-1])


def cache_file(season: int):
    return data.CACHE_DIR / f"pbp_script_v2_{season}.parquet"


def reduce_pbp(raw: pd.DataFrame) -> pd.DataFrame:
    """Regular-season designed plays only: dropbacks (passes and sacks, not spikes or kneels) and designed
    runs (not scrambles or kneels), one row each, with the offense's score differential at the snap."""
    p = raw[raw["season_type"] == "REG"].copy()
    p = p[p["posteam"].notna() & p["score_differential"].notna()]
    dropback = (p["qb_dropback"] == 1) & (p["qb_spike"] != 1) & (p["qb_kneel"] != 1)
    run = (p["rush"] == 1) & (p["qb_scramble"] != 1) & (p["qb_kneel"] != 1)
    p = p[dropback | run]
    out = pd.DataFrame(
        {
            "game_id": p["game_id"],
            "team": p["posteam"].map(data.normalize_team),
            "qtr": p["qtr"].astype(int),
            "diff": p["score_differential"].astype(float),
            "seconds_left": p["game_seconds_remaining"].astype(float),
            "pass_oe": p["pass_oe"].astype(float),
            "pass": dropback[p.index].astype(int),
        }
    )
    return out.reset_index(drop=True)


def load_pbp(seasons: list[int], log=lambda msg: None) -> pd.DataFrame:
    """The reduced play-by-play for `seasons`, downloading (and caching) any season not yet cached."""
    frames = []
    for season in seasons:
        path = cache_file(season)
        if not path.exists():
            log(f"fetching play-by-play {season}")
            try:
                resp = httpx.get(PBP_URL.format(season=season), timeout=300, follow_redirects=True)
                resp.raise_for_status()
                raw = pd.read_parquet(io.BytesIO(resp.content), columns=PBP_COLUMNS)
            except (httpx.HTTPError, ValueError, OSError) as e:
                raise data.ModelDataError(f"Request for {season} play-by-play failed: {e}") from e
            reduced = reduce_pbp(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            reduced.to_parquet(path)
        frames.append(pd.read_parquet(path).assign(season=season))
    return pd.concat(frames, ignore_index=True)


# --- tables ---------------------------------------------------------------------------------------------


def team_game_proe(pbp: pd.DataFrame) -> pd.DataFrame:
    """`game_id, team, proe`: each team-game's pass rate over expected (nflfastR's `pass_oe`, in points,
    averaged over the designed plays and returned as a fraction: 0.05 = passed 5 points more than the
    situations called for)."""
    g = pbp.dropna(subset=["pass_oe"]).groupby(["game_id", "team"], as_index=False)["pass_oe"].mean()
    return g.assign(proe=g.pop("pass_oe") / 100.0)


def pass_rate_by_state(pbp: pd.DataFrame, ctx: pd.DataFrame, *, first_half: bool = False) -> pd.DataFrame:
    """Pass rate (dropbacks over dropbacks + designed runs) by the offense's score differential at the snap
    (rows) and its pre-game spread bucket (columns), plus the `all` column and the share of snaps spent in
    each state. `ctx` is `history.team_game_context` (game_id, team, spread)."""
    p = pbp.merge(ctx[["game_id", "team", "spread"]], on=["game_id", "team"], how="left")
    p = p[p["spread"].notna()]
    if first_half:
        p = p[p["qtr"] <= 2]
    p["state"] = state_bucket(p["diff"])
    p["sb"] = spread_bucket(p["spread"])
    cols = {}
    for label, sub in [("all", p), *[(b, p[p["sb"] == b]) for b in SPREAD_BUCKETS]]:
        g = sub.groupby("state")["pass"].agg(["mean", "size"])
        cols[label] = g["mean"].reindex(STATE_LABELS)
        cols[f"snaps_{label}"] = (g["size"] / g["size"].sum()).reindex(STATE_LABELS)
    return pd.DataFrame(cols)


def team_game_script(pbp: pd.DataFrame, ctx: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """One row per team-game: pass rate, snaps, average lead while on offense, the final margin and the
    pre-game spread (team-relative)."""
    g = pbp.groupby(["game_id", "team"]).agg(
        snaps=("pass", "size"), pass_rate=("pass", "mean"), avg_lead=("diff", "mean")
    )
    g = g.reset_index().merge(ctx[["game_id", "team", "spread", "implied"]], on=["game_id", "team"])
    res = games[games["game_type"] == "REG"][["game_id", "home_team", "away_team", "result"]].copy()
    home = res.assign(team=res["home_team"].map(data.normalize_team), margin=res["result"])
    away = res.assign(team=res["away_team"].map(data.normalize_team), margin=-res["result"])
    margins = pd.concat([home, away])[["game_id", "team", "margin"]]
    return g.merge(margins, on=["game_id", "team"], how="left")


def slope(y: pd.Series, x: pd.Series, cluster: pd.Series) -> tuple[float, float]:
    """OLS slope of y on x and its game-clustered standard error."""
    d = pd.DataFrame({"y": y, "x": x, "c": cluster}).dropna()
    xc = d["x"] - d["x"].mean()
    sxx = float((xc**2).sum())
    b = float((xc * d["y"]).sum() / sxx)
    resid = d["y"] - d["y"].mean() - b * xc
    score = (xc * resid).groupby(d["c"].to_numpy()).sum()
    return b, float(np.sqrt((score**2).sum()) / sxx)


def margins(games: pd.DataFrame) -> pd.DataFrame:
    """`game_id, team, margin`: each regular-season team's final margin (positive = it won by that much)."""
    res = games[games["game_type"] == "REG"][["game_id", "home_team", "away_team", "result"]]
    home = res.assign(team=res["home_team"].map(data.normalize_team), margin=res["result"])
    away = res.assign(team=res["away_team"].map(data.normalize_team), margin=-res["result"])
    return pd.concat([home, away])[["game_id", "team", "margin"]].dropna()


def team_volume(player_games: pd.DataFrame, ctx: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """One row per team-game: pass attempts, RB carries, targets to WR / TE and to RB, with the pre-game
    `spread` and the final `margin` (both team-relative)."""
    pg = player_games
    qb, rb = pg["position"] == "QB", pg["position"] == "RB"
    catcher = pg["position"].isin(["WR", "TE"])
    parts = pd.DataFrame(
        {
            "game_id": pg["game_id"],
            "team": pg["team"],
            "attempts": pg["attempts"].where(qb, 0.0),
            "rb_carries": pg["carries"].where(rb, 0.0),
            "wr_te_targets": pg["targets"].where(catcher, 0.0),
            "rb_targets": pg["targets"].where(rb, 0.0),
        }
    )
    t = parts.groupby(["game_id", "team"], as_index=False).sum()
    t = t.merge(ctx[["game_id", "team", "spread"]], on=["game_id", "team"]).merge(
        margins(games), on=["game_id", "team"]
    )
    t["sb"] = spread_bucket(t["spread"])
    return t


def volume_table(t: pd.DataFrame) -> pd.DataFrame:
    """Mean volume per pre-game spread bucket, and the change per +10 points of spread and per +10 points of
    final margin (game-clustered t-statistics), for each volume column."""
    cols = ["attempts", "rb_carries", "wr_te_targets", "rb_targets"]
    means = t.groupby("sb")[cols].mean().reindex(SPREAD_BUCKETS)
    rows = {}
    for c in cols:
        bs, ss = slope(t[c], t["spread"], t["game_id"])
        bm, sm = slope(t[c], t["margin"], t["game_id"])
        rows[c] = {"per10_spread": 10 * bs, "t_spread": bs / ss, "per10_margin": 10 * bm, "t_margin": bm / sm}
    return means.T.join(pd.DataFrame(rows).T)
