"""Fitting and checking `props.py`'s distribution assumptions on nflverse history (2018 onward).

There is no free historical props line to fit to, so everything here uses a PROXY for what a bettor knew:
the mean of the player's previous eight regular-season games (at least six). The market knows more
(injuries, matchup, game script), so the dispersion fitted here is an upper bound on the market's own
(`props.py` says so).

Three results, all printed by `python -m dfs.props_history --last 2025 --test-from 2023` (downloads the
nflverse files once into `data/props_history/`, which is gitignored with the rest of `data/`):

1. **Dispersion** `phi = Var / mean` per (position, stat): regress the game's stat on the proxy
   (`actual ~ a + b * proxy`, train seasons) so the conditional mean is calibrated (a raw rolling mean
   regresses to the mean at the extremes), then `phi = sum((actual - fitted)^2) / sum(fitted)`.
2. **Survival calibration** (out of sample): for thresholds at 0.6 .. 1.4 times each player's fitted mean,
   the average predicted `P(X > threshold)` under the normal / gamma / Poisson / negative binomial with
   the fitted phi against how often the stat really exceeded it. This is the exact quantity
   `props.mean_from_line` inverts, so it is the honest test of the conversion from a de-vigged
   probability to a mean.
3. **Mean from a line, yardage**: a data-driven median line (median regression of the stat on the proxy,
   fitted on odd weeks, no use of the fitted distribution) is converted to a mean by
   `props.mean_from_line` at `p = 0.5`; on the even weeks that estimate is compared with
   "expected = line" for bias and RMSE against what happened. The split is interleaved, not by season,
   because a by-season split mixes a level drift between eras into the skew being measured (QB passing
   yards ran 5 yards below the earlier seasons' median line in 2023-25). Counts are not tested this way
   (a median of an integer is not a market line); step 2 covers them.
"""

from __future__ import annotations

import argparse
import io
from collections.abc import Iterable

import httpx
import numpy as np
import pandas as pd
from sklearn.linear_model import QuantileRegressor

from dfs import props
from dfs.paths import DATA_DIR
from dfs.player_join import normalize_position

HISTORY_DIR = DATA_DIR / "props_history"
STATS_PLAYER_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.parquet"
FIRST_SEASON = 2018
PRIOR_GAMES = 8  # the proxy: the previous eight games ...
MIN_PRIOR_GAMES = 6  # ... of which at least six exist
MIN_ATTEMPTS = 15  # a QB game counts when he threw at least this many passes (a starter, not mop-up work)
SURVIVAL_MULTIPLES = (0.6, 0.8, 1.0, 1.2, 1.4)

# (position, stat) -> (nflverse column, smallest proxy level worth a prop line)
CASES: dict[tuple[str, str], tuple[str, float]] = {
    ("QB", "pass_yd"): ("passing_yards", 150.0),
    ("QB", "pass_td"): ("passing_tds", 0.5),
    ("QB", "pass_int"): ("passing_interceptions", 0.3),
    ("QB", "rush_yd"): ("rushing_yards", 15.0),
    ("RB", "rush_yd"): ("rushing_yards", 20.0),
    ("RB", "rec"): ("receptions", 1.0),
    ("RB", "rec_yd"): ("receiving_yards", 10.0),
    ("WR", "rec"): ("receptions", 1.5),
    ("WR", "rec_yd"): ("receiving_yards", 20.0),
    ("TE", "rec"): ("receptions", 1.5),
    ("TE", "rec_yd"): ("receiving_yards", 15.0),
}
_COLUMNS = [
    "player_id",
    "player_display_name",
    "position",
    "season",
    "week",
    "season_type",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "rushing_yards",
    "receptions",
    "receiving_yards",
]


def load_history(seasons: Iterable[int], *, cache=HISTORY_DIR) -> pd.DataFrame:
    """nflverse `stats_player` for these seasons, cached on disk after the first download."""
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    for season in seasons:
        path = cache / f"stats_player_week_{season}.parquet"
        if not path.exists():
            resp = httpx.get(STATS_PLAYER_URL.format(season=season), timeout=120, follow_redirects=True)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        have = pd.read_parquet(io.BytesIO(path.read_bytes())).columns
        frames.append(pd.read_parquet(path, columns=[c for c in _COLUMNS if c in have]))
    return pd.concat(frames, ignore_index=True)


def case_frame(history: pd.DataFrame, position: str, stat: str) -> pd.DataFrame:
    """One row per player-game of this (position, stat) that has a proxy: `actual`, `proxy` (mean of the
    previous games, same player, earlier weeks and seasons), `season`, `week`. Only rows whose proxy is at
    least the case's `CASES` floor, i.e. players who would have a line."""
    column, floor = CASES[(position, stat)]
    if column not in history.columns:
        return pd.DataFrame(columns=["player_id", "season", "week", "proxy", "actual"])
    df = history[history["season_type"] == "REG"].copy()
    df["position"] = df["position"].map(normalize_position)
    df = df[df["position"] == position]
    if position == "QB":
        df = df[pd.to_numeric(df["attempts"], errors="coerce") >= MIN_ATTEMPTS]
    df = df.sort_values(["player_id", "season", "week"])
    previous = df.groupby("player_id")[column].shift(1)
    df["proxy"] = previous.groupby(df["player_id"]).transform(
        lambda s: s.rolling(PRIOR_GAMES, min_periods=MIN_PRIOR_GAMES).mean()
    )
    df["actual"] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["proxy", "actual"])
    return df[df["proxy"] >= floor][["player_id", "season", "week", "proxy", "actual"]].reset_index(drop=True)


def fit_conditional_mean(train: pd.DataFrame) -> tuple[float, float]:
    """`actual ~ a + b * proxy` by least squares: the calibrated mean forecast."""
    slope, intercept = np.polyfit(train["proxy"], train["actual"], 1)
    return float(intercept), float(slope)


def fit_phi(train: pd.DataFrame) -> tuple[float, float, float]:
    """(phi, intercept, slope): `phi = sum((actual - fitted)^2) / sum(fitted)` with `fitted` the calibrated
    conditional mean. phi < 1 means under-dispersed against a Poisson (touchdown and interception counts)."""
    intercept, slope = fit_conditional_mean(train)
    fitted = (intercept + slope * train["proxy"]).clip(lower=1e-3)
    phi = float(((train["actual"] - fitted) ** 2).sum() / fitted.sum())
    return phi, intercept, slope


def fit_table(history: pd.DataFrame) -> pd.DataFrame:
    """phi per (position, stat) over everything in `history`, plus the pooled per-stat value (all positions
    together, used for stats/positions not fitted and for the cross-book line shift)."""
    rows = []
    pooled: dict[str, list[pd.DataFrame]] = {}
    for position, stat in CASES:
        frame = case_frame(history, position, stat)
        phi, intercept, slope = fit_phi(frame)
        rows.append(
            {
                "Position": position,
                "Stat": stat,
                "n": len(frame),
                "phi": round(phi, 3),
                "a": round(intercept, 3),
                "b": round(slope, 3),
            }
        )
        pooled.setdefault(stat, []).append(frame)
    for stat, frames in pooled.items():
        if len(frames) > 1:
            phi, _, _ = fit_phi(pd.concat(frames, ignore_index=True))
            rows.append(
                {"Position": "ALL", "Stat": stat, "n": sum(len(f) for f in frames), "phi": round(phi, 3)}
            )
    return pd.DataFrame(rows)


def league_int_rate(history: pd.DataFrame) -> float:
    """Interceptions per game for QBs with at least `MIN_ATTEMPTS` attempts."""
    df = history[history["season_type"] == "REG"]
    df = df[(df["position"] == "QB") & (pd.to_numeric(df["attempts"], errors="coerce") >= MIN_ATTEMPTS)]
    return float(df["passing_interceptions"].sum() / len(df))


def model_over_probability(stat: str, mean: np.ndarray, phi: float, threshold: np.ndarray) -> np.ndarray:
    """`props.over_probability` over arrays."""
    return np.array([props.over_probability(stat, m, phi, t) for m, t in zip(mean, threshold, strict=True)])


def survival_calibration(
    history: pd.DataFrame, train_seasons: set[int], test_seasons: set[int]
) -> pd.DataFrame:
    """Out of sample: fit (a, b, phi) on `train_seasons`, then for each test row and each multiple m of its
    fitted mean, the model's `P(X > m * mean)` against the realised `actual > m * mean`. Reports the mean
    of each per (position, stat, multiple)."""
    rows = []
    for position, stat in CASES:
        frame = case_frame(history, position, stat)
        train, test = frame[frame["season"].isin(train_seasons)], frame[frame["season"].isin(test_seasons)]
        if train.empty or test.empty:
            continue
        phi, intercept, slope = fit_phi(train)
        mean = (intercept + slope * test["proxy"]).clip(lower=1e-3).to_numpy()
        for multiple in SURVIVAL_MULTIPLES:
            # counts: a half-line just under the scaled mean, as a book would post it
            threshold = np.floor(multiple * mean) + 0.5 if stat in props.COUNT_STATS else multiple * mean
            predicted = model_over_probability(stat, mean, phi, threshold)
            realised = (test["actual"].to_numpy() > threshold).astype(float)
            rows.append(
                {
                    "Position": position,
                    "Stat": stat,
                    "Multiple": multiple,
                    "n": len(test),
                    "Predicted": round(float(predicted.mean()), 3),
                    "Realised": round(float(realised.mean()), 3),
                }
            )
    return pd.DataFrame(rows)


def mean_from_line_backtest(history: pd.DataFrame) -> pd.DataFrame:
    """Yardage stats, out of sample (odd weeks fit, even weeks test): the data-driven median line against
    `props.mean_from_line(line, p=0.5)`, each judged as an estimate of the stat's mean (bias = mean of
    actual - estimate, RMSE against the actual)."""
    rows = []
    for position, stat in CASES:
        if stat not in props.YARDAGE_STATS:
            continue
        frame = case_frame(history, position, stat)
        train, test = frame[frame["week"] % 2 == 1], frame[frame["week"] % 2 == 0]
        if train.empty or test.empty:
            continue
        phi, _, _ = fit_phi(train)
        median_fit = QuantileRegressor(quantile=0.5, alpha=0.0, solver="highs").fit(
            train[["proxy"]].to_numpy(), train["actual"].to_numpy()
        )
        line = np.clip(median_fit.predict(test[["proxy"]].to_numpy()), 1.0, None)
        modelled = np.array([props.mean_from_line(stat, float(x), 0.5, phi) for x in line])
        actual = test["actual"].to_numpy()
        for label, estimate in (("expected = line", line), ("mean from line", modelled)):
            rows.append(
                {
                    "Position": position,
                    "Stat": stat,
                    "Estimate": label,
                    "n": len(test),
                    "Bias": round(float(np.mean(actual - estimate)), 2),
                    "RMSE": round(float(np.sqrt(np.mean((actual - estimate) ** 2))), 2),
                    "Median over line": round(float(np.mean(actual > line)), 3),
                }
            )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - downloads
    parser = argparse.ArgumentParser(description="Fit and check the PropPts distribution assumptions.")
    parser.add_argument("--first", type=int, default=FIRST_SEASON)
    parser.add_argument("--last", type=int, required=True, help="last COMPLETE season to use")
    parser.add_argument("--test-from", type=int, required=True, help="first held-out season")
    args = parser.parse_args(argv)
    seasons = list(range(args.first, args.last + 1))
    history = load_history(seasons)
    train = {s for s in seasons if s < args.test_from}
    test = {s for s in seasons if s >= args.test_from}
    pd.set_option("display.width", 200)
    print(f"seasons {seasons[0]}-{seasons[-1]}; train {sorted(train)}; test {sorted(test)}\n")
    print("phi (all seasons) -- paste into props.DISPERSION / DISPERSION_POOLED")
    print(fit_table(history).to_string(index=False))
    print(f"\nleague QB INT/game: {league_int_rate(history):.4f}")
    print("\nsurvival calibration (out of sample)")
    print(survival_calibration(history, train, test).to_string(index=False))
    print("\nmean from line, yardage (fit on odd weeks, tested on even weeks)")
    print(mean_from_line_backtest(history).to_string(index=False))


if __name__ == "__main__":  # pragma: no cover
    main()
