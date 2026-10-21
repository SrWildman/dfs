"""Is the top of the distribution engine light? Coverage of its upper quantiles, and adjustments tested.

`dfs.model.distribution` stores, per position and projection, the quantiles of `actual / projection`. The
Week 8 copula study found QB1 and WR1 normal scores above their own 90th percentile 10.65% and 10.82% of the
time (not 10%), enough to account for about 15 of the 37 points by which stack booms exceeded the Gaussian
copula's.
This module asks whether that is a property of the TOP of the tables, or of how the check was made.

- `coverage`: how often the actual beats the predicted 90th / 95th / 99th percentile, by position, projection
  band or role, with a normal-approximation z against the nominal rate. Tables fitted on 2014-2022 only
  (`train_tables`), scored on 2023-2026: nothing in it is in sample.
- `fit_adjustment` / `adjust`: the smallest families that could correct a table. Per group (a position, or a
  role): `delta` stretches the ratio quantiles above the median about the median by `1 + delta` (a light or
  heavy TOP), and optionally `scale` multiplies every ratio quantile (a LEVEL shift). Fitted on 2014-2022 to
  minimise the pinball loss summed over the 90 / 95 / 99 percent quantiles (`TAIL_LEVELS`), then judged on
  2023-2026 by the pinball loss at the same quantiles and by the conditional log-likelihood of the rows beyond
  the old model's 90th percentile (`tail_loglik`). Paired differences are clustered by game.

Nothing here changes `models/um/`.

    python -m dfs.tail_check --frame frame.parquet     # a parquet of sim.pipeline.load_frame(2026)
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

from dfs.model import distribution as dist
from dfs.sim import marginal
from dfs.sim.roles import ROLE_POSITION

TAIL_LEVELS = (0.90, 0.95, 0.99)
TRAIN_THROUGH = 2022
POSITIONS = tuple(dist.BUCKET_EDGES)
DELTA_BOUNDS = (-0.5, 0.5)
BAND_CUTS = (0.2, 0.4, 0.6, 0.8, 0.9)  # projection bands within a position: quintiles, then the top decile

_LEVELS = np.asarray(dist.LEVELS, dtype=float)
_TAIL_COLS = [int(np.argmin(np.abs(_LEVELS - q))) for q in TAIL_LEVELS]


def train_tables(frame: pd.DataFrame, through: int = TRAIN_THROUGH) -> pd.DataFrame:
    """The distribution tables as `dfs model train` builds them, from out-of-fold rows through `through`."""
    train = frame[frame["season"] <= through]
    return pd.concat(
        [dist.build_tables(train.loc[train["position"] == p, ["pred", "dk"]], p) for p in POSITIONS],
        ignore_index=True,
    )


def with_pit(frame: pd.DataFrame, tables: pd.DataFrame | None, seed: int = 0) -> pd.DataFrame:
    """`frame` plus `u` (the probability-integral transform of `dk` through the table) and `band` (0-5, the
    projection's band within its position, cut on the training seasons)."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    out["u"] = np.nan
    out["band"] = 0
    for position in POSITIONS:
        sel = out["position"] == position
        m = marginal.marginals(position, out.loc[sel, "pred"].to_numpy(), tables=tables)
        out.loc[sel, "u"] = marginal.cdf_u(m, out.loc[sel, "dk"].to_numpy(), rng)
        cuts = np.quantile(out.loc[sel & (out["season"] <= TRAIN_THROUGH), "pred"], BAND_CUTS)
        out.loc[sel, "band"] = np.digitize(out.loc[sel, "pred"], cuts)
    return out


def coverage(scored: pd.DataFrame, by: tuple[str, ...] = ("position",)) -> pd.DataFrame:
    """Share of rows whose actual beat each predicted upper quantile, and its z against the nominal rate."""
    rows = []
    for key, g in scored.groupby(list(by)):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(by, key, strict=True)) | {"n": len(g)}
        for q in TAIL_LEVELS:
            hit, p = int((g["u"] > q).sum()), 1 - q
            row[f">{round(100 * q)}"] = 100 * hit / len(g)
            row[f"z{round(100 * q)}"] = (hit - len(g) * p) / np.sqrt(len(g) * q * p)
        rows.append(row)
    return pd.DataFrame(rows)


# --- the adjustment -------------------------------------------------------------------------------------


def adjust(ratios: np.ndarray, delta: float, scale: float = 1.0) -> np.ndarray:
    """Ratio quantile rows with everything above the median stretched about it by `1 + delta`, then every
    quantile multiplied by `scale`."""
    return marginal.stretch_upper(ratios, delta) * scale


def pinball(ratios: np.ndarray, pred: np.ndarray, dk: np.ndarray) -> np.ndarray:
    """Per row, the pinball loss in points summed over `TAIL_LEVELS`."""
    loss = np.zeros(len(pred))
    for q, col in zip(TAIL_LEVELS, _TAIL_COLS, strict=True):
        err = dk - ratios[:, col] * pred
        loss += np.maximum(q * err, (q - 1) * err)
    return loss


def _group_rows(
    frame: pd.DataFrame, group: str, name: str, tables
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ratio quantile rows, projections and actuals of one group (a position, or a role)."""
    g = frame[frame[group] == name]
    position = name if group == "position" else ROLE_POSITION[name]
    pred, dk = g["pred"].to_numpy(float), g["dk"].to_numpy(float)
    return (
        dist.ratio_matrix(position, pred, tables=tables) if len(pred) else np.empty((0, len(_LEVELS))),
        pred,
        dk,
    )


def groups_of(group: str) -> tuple[str, ...]:
    return POSITIONS if group == "position" else tuple(ROLE_POSITION)


def fit_adjustment(
    frame: pd.DataFrame,
    tables: pd.DataFrame,
    *,
    group: str = "position",
    level: bool = False,
    through: int = TRAIN_THROUGH,
) -> dict[str, tuple[float, float]]:
    """`(delta, scale)` per group, the pinball minimiser on the training seasons. With `level=False` the scale
    stays 1 (the one-parameter, top-only family)."""
    train = frame[frame["season"] <= through]
    out = {}
    for name in groups_of(group):
        ratios, pred, dk = _group_rows(train, group, name, tables)
        if len(pred) < 100:
            out[name] = (0.0, 1.0)
        elif level:
            res = minimize(
                lambda x, r=ratios, p=pred, d=dk: float(
                    pinball(adjust(r, x[0], float(np.exp(x[1]))), p, d).mean()
                ),
                [0.0, 0.0],
                method="Nelder-Mead",
            )
            out[name] = (float(res.x[0]), float(np.exp(res.x[1])))
        else:
            res = minimize_scalar(
                lambda d, r=ratios, p=pred, a=dk: float(pinball(adjust(r, d), p, a).mean()),
                bounds=DELTA_BOUNDS,
                method="bounded",
            )
            out[name] = (float(res.x), 1.0)
    return out


def tail_loglik(ratios: np.ndarray, actual_ratio: np.ndarray, threshold: np.ndarray) -> np.ndarray:
    """Per row, `log f(r) - log P(R > c)` for rows beyond `threshold` (the OLD model's 90th percentile; NaN
    elsewhere), under the piecewise-linear CDF through the row's quantiles (the engine's own CDF: end segments
    extrapolated to probability 1)."""
    out = np.full(len(ratios), np.nan)
    if not len(ratios):
        return out
    end = (ratios[:, -1] - ratios[:, -2]) / (_LEVELS[-1] - _LEVELS[-2]) * (1 - _LEVELS[-1])
    probs = np.append(_LEVELS, 1.0)
    for i in np.flatnonzero(actual_ratio > threshold):
        knots = np.append(ratios[i], ratios[i, -1] + end[i])
        if actual_ratio[i] > knots[-1]:
            continue
        k = int(np.clip(np.searchsorted(knots, actual_ratio[i], side="right") - 1, 0, len(knots) - 2))
        density = max((probs[k + 1] - probs[k]) / max(knots[k + 1] - knots[k], 1e-9), 1e-12)
        above = max(1.0 - float(np.interp(threshold[i], knots, probs)), 1e-12)
        out[i] = np.log(density) - np.log(above)
    return out


def judge(
    frame: pd.DataFrame,
    tables: pd.DataFrame,
    fitted: dict[str, tuple[float, float]],
    seasons,
    *,
    group: str = "position",
) -> pd.DataFrame:
    """Held-out comparison, old tables against the adjusted ones, per group and overall: mean pinball
    (points), mean conditional tail log-likelihood beyond the old 90th percentile, and the paired difference
    with a game-clustered SE (`*_better` > 0: the adjustment is better; `clears_2se` when it is on both)."""
    test = frame[frame["season"].isin(seasons)]
    parts = []
    for name in groups_of(group):
        ratios, pred, dk = _group_rows(test, group, name, tables)
        if len(pred) == 0:
            continue
        new = adjust(ratios, *fitted[name])
        thr = ratios[:, _TAIL_COLS[0]]
        parts.append(
            pd.DataFrame(
                {
                    "group": name,
                    "game_id": test.loc[test[group] == name, "game_id"].to_numpy(),
                    "pin_old": pinball(ratios, pred, dk),
                    "pin_new": pinball(new, pred, dk),
                    "ll_old": tail_loglik(ratios, dk / pred, thr),
                    "ll_new": tail_loglik(new, dk / pred, thr),
                }
            )
        )
    long = pd.concat(parts, ignore_index=True)
    rows = []
    for name in [*long["group"].unique(), "All"]:
        s = long if name == "All" else long[long["group"] == name]
        row = {"group": name, "n": len(s)}
        if name != "All":
            row["delta"], row["scale"] = fitted[name]
        for label, old, new, sign in (
            ("pinball", "pin_old", "pin_new", 1.0),
            ("tail_ll", "ll_old", "ll_new", -1.0),
        ):
            ok = s[[old, new]].notna().all(axis=1)
            d = sign * (s.loc[ok, old] - s.loc[ok, new]).to_numpy()  # positive: the adjustment is better
            per_game = pd.Series(d).groupby(s.loc[ok, "game_id"].to_numpy()).sum()
            row[f"{label}_old"] = float(s.loc[ok, old].mean())
            row[f"{label}_better"] = float(d.mean()) if len(d) else float("nan")
            row[f"{label}_se"] = float(np.sqrt((per_game**2).sum()) / len(d)) if len(d) else float("nan")
        row["clears_2se"] = bool(
            row["pinball_better"] > 2 * row["pinball_se"] and row["tail_ll_better"] > 2 * row["tail_ll_se"]
        )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    args = ap.parse_args()
    frame = pd.read_parquet(args.frame)
    pd.set_option("display.width", 220)
    tables = train_tables(frame)
    scored = with_pit(frame, tables)
    held = scored[scored["season"].between(2023, 2026)]
    print("coverage, tables fitted on 2014-2022, rows of 2023-2026 (z against the nominal rate)")
    for by in (("position",), ("role",), ("position", "band")):
        print(coverage(held, by).round(2).to_string(index=False))
    for group, level in (("position", False), ("role", False), ("role", True)):
        fitted = fit_adjustment(frame, tables, group=group, level=level)
        label = f"{group}{' with a level scale' if level else ''}"
        print(f"\nadjustment per {label}, fitted 2014-2022, judged 2023-2026")
        print(judge(frame, tables, fitted, range(2023, 2027), group=group).round(4).to_string(index=False))


if __name__ == "__main__":
    main()
