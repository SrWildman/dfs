"""Plan S, check 1: does a player-level spread term beat the per-position tables at the tails?

The distribution engine (`dfs.model.distribution`) gives two players of one position at the same projection
the same distribution. This asks whether a player's own history says his outcomes are wider or narrower than
that: a spread term `delta_i = clip(beta . x_i)` stretches his ratio quantiles above the median by `1 +
delta_i` (`marginal.stretch_upper`, the hook `tail_check` used per role), with `x_i` his standardised
features from his PREVIOUS games only (up to `WINDOW`, at least `MIN_GAMES`, across the season boundary;
never the game itself):

- `adot`: average depth of target, `sum(receiving air yards) / sum(targets)` (a QB: passing air yards per
  attempt);
- `ay_share`: his mean share of the team's air yards (receivers only);
- `tgt_sd`: the standard deviation of his targets over the window (a QB: of his pass attempts);
- `td_share`: the share of his DK points that came from touchdowns;
- `snap_trend`: the slope of his offensive snap share over his last four games (nflverse snap counts);
- `pts_cv`: the coefficient of variation of his DK points (an extra, the plainest "volatility" there is).

Each feature is fitted alone per position, and all of them together (`all`), on 2014-2022 (tables included:
`tail_check.train_tables`), minimising the pinball loss at the 90th, 95th and 99th percentiles; it is judged
on 2023-2026 by the pinball loss at the 90th and 99th, paired by **week** (the house bar: more than 2 SE of
the weekly paired difference AND better in most weeks), and by calibration: the share of actuals above the
predicted 90th / 99th within terciles of the feature, summed as a chi-square against the nominal rate (a real
spread effect shows as the high tercile beating its 90th more often than the low one; the term should flatten
that). A term clears the bar only if the pinball gain clears it AND the calibration chi-square falls by more
than 2 week-bootstrap SE.

    python -m dfs.sim.volatility_check --frame frame.parquet     # a parquet of sim.pipeline.load_frame(2026)

Nothing here changes `models/um/` or the simulator.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

from dfs import tail_check
from dfs.model import distribution as dist
from dfs.sim import marginal

WINDOW = 8
MIN_GAMES = 3
SNAP_WINDOW = 4
POSITIONS = ("QB", "RB", "WR", "TE")  # a DST has none of these features
FEATURES = ("adot", "ay_share", "tgt_sd", "td_share", "snap_trend", "pts_cv")
POSITION_FEATURES = {
    "QB": ("adot", "tgt_sd", "td_share", "snap_trend", "pts_cv"),
    "RB": FEATURES,
    "WR": FEATURES,
    "TE": FEATURES,
}
TRAIN_THROUGH = tail_check.TRAIN_THROUGH
TEST_SEASONS = (2023, 2024, 2025, 2026)
FIT_LEVELS = (0.90, 0.95, 0.99)
JUDGE_LEVELS = (0.90, 0.99)
DELTA_CLIP = 0.5
BETA_BOUNDS = (-0.5, 0.5)
N_BOOT = 200

_LEVELS = np.asarray(dist.LEVELS, dtype=float)
_COL = {q: int(np.argmin(np.abs(_LEVELS - q))) for q in (*FIT_LEVELS, *JUDGE_LEVELS)}


# --- features (previous games only) -------------------------------------------------------------------


def stat_rows(seasons) -> pd.DataFrame:  # noqa: ANN001
    """One row per regular-season player-game from the cached nflverse player stats: the columns the features
    need, keyed `gsis_id, season, week`."""
    from dfs.model import data

    cols = [
        "player_id",
        "season",
        "week",
        "season_type",
        "targets",
        "attempts",
        "receiving_air_yards",
        "passing_air_yards",
        "air_yards_share",
        "receiving_tds",
        "rushing_tds",
        "passing_tds",
    ]
    sp = data.read_season_files("stats_player", list(seasons), columns=cols)
    sp = sp[(sp["season_type"] == "REG") & sp["player_id"].notna()].drop(columns="season_type")
    sp = sp.rename(columns={"player_id": "gsis_id"})
    for c in cols[4:]:
        sp[c] = pd.to_numeric(sp[c], errors="coerce").fillna(0.0)
    sp["season"] = sp["season"].astype(int)
    sp["week"] = sp["week"].astype(int)
    return sp.groupby(["gsis_id", "season", "week"], as_index=False).sum(numeric_only=True)


def snap_rows(seasons) -> pd.DataFrame:  # noqa: ANN001
    """Offensive snap share per player-game, `gsis_id, season, week, snap_pct` (PFR ids crosswalked)."""
    from dfs.research import data as rdata

    snaps = rdata.read_snaps(list(seasons))
    players = pd.read_parquet(rdata.players_path(), columns=["gsis_id", "pfr_id"]).dropna()
    snaps = snaps.merge(players, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    snaps["snap_pct"] = pd.to_numeric(snaps["offense_pct"], errors="coerce")
    snaps["season"] = snaps["season"].astype(int)
    snaps["week"] = snaps["week"].astype(int)
    return snaps.groupby(["gsis_id", "season", "week"], as_index=False)["snap_pct"].max()


def _slope(y: np.ndarray) -> float:
    ok = ~np.isnan(y)
    if ok.sum() < 3:
        return float("nan")
    x = np.arange(len(y), dtype=float)[ok]
    return float(np.polyfit(x, y[ok], 1)[0])


def player_features(games: pd.DataFrame) -> pd.DataFrame:
    """`games` (one player-game per row: `gsis_id, season, week, position, dk, targets, attempts, ...`) with
    every feature computed from the player's PREVIOUS games (sorted by season and week), NaN with fewer than
    `MIN_GAMES` of them."""
    g = games.sort_values(["gsis_id", "season", "week"]).reset_index(drop=True)
    qb = g["position"] == "QB"
    g["_usage"] = np.where(qb, g["attempts"], g["targets"])
    g["_air"] = np.where(qb, g["passing_air_yards"], g["receiving_air_yards"])
    g["_td_pts"] = 6.0 * (g["receiving_tds"] + g["rushing_tds"]) + 4.0 * g["passing_tds"]
    by = g.groupby("gsis_id", sort=False)

    def prior(col: str, fn: str, window: int = WINDOW) -> pd.Series:
        shifted = by[col].shift(1)
        roll = shifted.groupby(g["gsis_id"]).rolling(window, min_periods=MIN_GAMES)
        return getattr(roll, fn)().reset_index(level=0, drop=True).sort_index()

    n_prior = by.cumcount()
    usage, air = prior("_usage", "sum"), prior("_air", "sum")
    g["adot"] = np.where(usage > 0, air / usage.where(usage > 0), np.nan)
    g["ay_share"] = np.where(qb, np.nan, prior("air_yards_share", "mean"))
    g["tgt_sd"] = prior("_usage", "std")
    td, pts = prior("_td_pts", "sum"), prior("dk", "sum")
    g["td_share"] = np.where(pts > 1, td / pts.where(pts > 1), np.nan)
    mean, sd = prior("dk", "mean"), prior("dk", "std")
    g["pts_cv"] = np.where(mean > 1, sd / mean.where(mean > 1), np.nan)
    shifted = by["snap_pct"].shift(1) if "snap_pct" in g else pd.Series(np.nan, index=g.index)
    g["snap_trend"] = (
        shifted.groupby(g["gsis_id"])
        .rolling(SNAP_WINDOW, min_periods=3)
        .apply(_slope, raw=True)
        .reset_index(level=0, drop=True)
        .sort_index()
    )
    g.loc[n_prior < MIN_GAMES, list(FEATURES)] = np.nan
    return g.drop(columns=["_usage", "_air", "_td_pts"])


def build(frame: pd.DataFrame, *, with_snaps: bool = True) -> pd.DataFrame:
    """The check's frame: `frame`'s offensive rows (`sim.pipeline.load_frame`) with every feature attached.
    The window runs over every game the player played (from the stats), not only the rows the model
    projected."""
    seasons = sorted(frame["season"].unique())
    stats = stat_rows(seasons)
    from dfs.model.history import load_history

    history, _ = load_history(seasons)
    pg = history.player_games[["gsis_id", "season", "week", "position", "dk"]]
    games = pg.merge(stats, on=["gsis_id", "season", "week"], how="left").fillna(
        {c: 0.0 for c in stats.columns if c not in ("gsis_id", "season", "week")}
    )
    if with_snaps:
        games = games.merge(snap_rows(seasons), on=["gsis_id", "season", "week"], how="left")
    feats = player_features(games)[["gsis_id", "season", "week", *FEATURES]]
    off = frame[frame["position"].isin(POSITIONS)]
    return off.merge(feats, on=["gsis_id", "season", "week"], how="left")


# --- the spread term --------------------------------------------------------------------------------------


def standardise(rows: pd.DataFrame, train: pd.DataFrame, features) -> np.ndarray:  # noqa: ANN001
    """The features as z-scores on the training rows' mean and sd (a missing value is 0: no stretch)."""
    out = np.zeros((len(rows), len(features)))
    for j, f in enumerate(features):
        mu, sd = float(train[f].mean()), float(train[f].std())
        x = (rows[f].to_numpy(float) - mu) / (sd if sd > 0 else 1.0)
        out[:, j] = np.nan_to_num(np.clip(x, -4.0, 4.0))
    return out


def deltas(x: np.ndarray, beta: np.ndarray) -> np.ndarray:
    return np.clip(x @ np.asarray(beta, float), -DELTA_CLIP, DELTA_CLIP)


def pinball_at(ratios: np.ndarray, pred: np.ndarray, dk: np.ndarray, levels) -> np.ndarray:  # noqa: ANN001
    """Per row, the pinball loss in points summed over `levels`."""
    loss = np.zeros(len(pred))
    for q in levels:
        err = dk - ratios[:, _COL[q]] * pred
        loss += np.maximum(q * err, (q - 1) * err)
    return loss


def fit_beta(ratios: np.ndarray, pred: np.ndarray, dk: np.ndarray, x: np.ndarray) -> np.ndarray:
    """The pinball minimiser (`FIT_LEVELS`) on training rows: one coefficient per feature column."""

    def loss(beta: np.ndarray) -> float:
        return float(pinball_at(marginal.stretch_upper(ratios, deltas(x, beta)), pred, dk, FIT_LEVELS).mean())

    if x.shape[1] == 1:
        res = minimize_scalar(lambda b: loss(np.array([b])), bounds=BETA_BOUNDS, method="bounded")
        return np.array([float(res.x)])
    res = minimize(loss, np.zeros(x.shape[1]), method="Nelder-Mead", options={"maxiter": 400, "xatol": 1e-4})
    return np.asarray(res.x, float)


def _terciles(train_values: pd.Series) -> np.ndarray:
    return np.nanquantile(train_values.to_numpy(float), [1 / 3, 2 / 3])


def chi2_calibration(u_hit: np.ndarray, groups: np.ndarray, q: float) -> float:
    """Sum over groups of (hits - n p)^2 / (n p (1 - p)), p = 1 - q."""
    p, total = 1.0 - q, 0.0
    for g in np.unique(groups):
        sel = groups == g
        n = int(sel.sum())
        if n:
            total += (u_hit[sel].sum() - n * p) ** 2 / (n * p * (1 - p))
    return float(total)


def judge(
    test: pd.DataFrame, base: np.ndarray, new: np.ndarray, groups: np.ndarray, *, seed: int = 0
) -> dict:
    """Held-out: pinball at the 90th / 99th by week, calibration chi-square by feature tercile (base vs
    new)."""
    pred, dk = test["pred"].to_numpy(float), test["dk"].to_numpy(float)
    week = (test["season"].astype(str) + "-" + test["week"].astype(str)).to_numpy()
    out: dict = {"n": len(test)}
    d = pinball_at(base, pred, dk, JUDGE_LEVELS) - pinball_at(new, pred, dk, JUDGE_LEVELS)  # > 0: new better
    per_week = pd.Series(d).groupby(week).mean()
    out["pinball_base"] = float(pinball_at(base, pred, dk, JUDGE_LEVELS).mean())
    out["pinball_gain"] = float(d.mean())
    out["pinball_se"] = float(per_week.std(ddof=1) / np.sqrt(len(per_week)))
    out["weeks_better"] = float((per_week > 0).mean())
    for q in JUDGE_LEVELS:
        dq = pinball_at(base, pred, dk, (q,)) - pinball_at(new, pred, dk, (q,))
        out[f"gain{round(100 * q)}"] = float(dq.mean())
    rng = np.random.default_rng(seed)
    weeks = np.unique(week)
    rows_of = {w: np.flatnonzero(week == w) for w in weeks}
    for q in JUDGE_LEVELS:
        hit_b = dk > base[:, _COL[q]] * pred
        hit_n = dk > new[:, _COL[q]] * pred
        out[f"chi2_{round(100 * q)}_base"] = chi2_calibration(hit_b, groups, q)
        out[f"chi2_{round(100 * q)}_new"] = chi2_calibration(hit_n, groups, q)
        for t in range(3):
            sel = groups == t
            out[f"hit{round(100 * q)}_t{t}_base"] = float(hit_b[sel].mean()) if sel.any() else float("nan")
            out[f"hit{round(100 * q)}_t{t}_new"] = float(hit_n[sel].mean()) if sel.any() else float("nan")
        diffs = []
        for _ in range(N_BOOT):
            idx = np.concatenate([rows_of[w] for w in rng.choice(weeks, size=len(weeks))])
            diffs.append(
                chi2_calibration(hit_b[idx], groups[idx], q) - chi2_calibration(hit_n[idx], groups[idx], q)
            )
        out[f"chi2_{round(100 * q)}_gain"] = (
            out[f"chi2_{round(100 * q)}_base"] - out[f"chi2_{round(100 * q)}_new"]
        )
        out[f"chi2_{round(100 * q)}_se"] = float(np.std(diffs, ddof=1))
    out["clears"] = bool(
        out["pinball_gain"] > 2 * out["pinball_se"]
        and out["weeks_better"] > 0.5
        and out["chi2_90_gain"] > 2 * out["chi2_90_se"]
    )
    return out


def run(feat: pd.DataFrame, tables: pd.DataFrame, *, seed: int = 0) -> pd.DataFrame:
    """One row per (position, feature set): the fitted coefficients and the held-out judgement."""
    rows = []
    for position in POSITIONS:
        rows_p = feat[feat["position"] == position].reset_index(drop=True)
        train = rows_p[rows_p["season"] <= TRAIN_THROUGH].reset_index(drop=True)
        test = rows_p[rows_p["season"].isin(TEST_SEASONS)].reset_index(drop=True)
        r_train = dist.ratio_matrix(position, train["pred"].to_numpy(float), tables=tables)
        r_test = dist.ratio_matrix(position, test["pred"].to_numpy(float), tables=tables)
        names = POSITION_FEATURES[position]
        for fs in [(f,) for f in names] + [names]:
            label = fs[0] if len(fs) == 1 else "all"
            x_tr, x_te = standardise(train, train, fs), standardise(test, train, fs)
            beta = fit_beta(r_train, train["pred"].to_numpy(float), train["dk"].to_numpy(float), x_tr)
            new = marginal.stretch_upper(r_test, deltas(x_te, beta))
            if len(fs) == 1:  # calibration by terciles of the feature (a missing one is its own group)
                cuts = _terciles(train[fs[0]])
                vals = test[fs[0]].to_numpy(float)
                groups = np.where(np.isnan(vals), -1, np.digitize(vals, cuts))
            else:  # the combined term: by terciles of the spread it gives each player
                cuts = _terciles(pd.Series(x_tr @ beta))
                groups = np.digitize(x_te @ beta, cuts)
            res = judge(test, r_test, new, groups, seed=seed)
            rows.append(
                {
                    "position": position,
                    "feature": label,
                    "beta": ", ".join(f"{b:+.3f}" for b in beta),
                    "coverage": float(test[list(fs)].notna().all(axis=1).mean()),
                    **res,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--save", help="CSV for the results")
    ap.add_argument("--features", help="parquet cache of the feature frame (read if it exists, else written)")
    args = ap.parse_args()
    from pathlib import Path

    frame = pd.read_parquet(args.frame)
    if args.features and Path(args.features).exists():
        feat = pd.read_parquet(args.features)
    else:
        feat = build(frame)
        if args.features:
            feat.to_parquet(args.features)
    tables = tail_check.train_tables(frame)
    res = run(feat, tables)
    if args.save:
        res.to_csv(args.save, index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 60)
    show = [
        "position", "feature", "beta", "coverage", "n", "pinball_base", "pinball_gain", "pinball_se",
        "weeks_better", "gain90", "gain99", "chi2_90_base", "chi2_90_new", "chi2_90_se", "chi2_99_base",
        "chi2_99_new", "clears",
    ]  # fmt: skip
    print(res[show].round(4).to_string(index=False))
    tert = [c for c in res.columns if c.startswith("hit90_")]
    print(res[["position", "feature", *tert]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
