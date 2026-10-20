"""Recalibrating Hit3x%, Boom% and Bust% on this season's results (a shadow; nothing here is on by default).

The probability columns come from the distribution engine (`dfs.model.distribution`, tables learned from 12
seasons of UM errors) applied to `CalPts` (`probabilities.outcome_columns`). Model Check shows them running a
little high on Hit 3x and Boom and a little low on Bust. This asks whether this season's results can correct
that out of sample, with two families, both shrunk toward "no change" so a thin sample cannot move them far:

- **scale** (structural): the engine is asked about `c * CalPts` instead of `CalPts`, `c` per position (or one
  for everyone), fitted by least squares on the three Brier scores together. A projection that runs high for a
  position pulls all three probabilities the way the table shows. The tables themselves are not touched.
- **Platt** (statistical): `p' = logistic(a + b * logit(p))` per measure, the generic fix for a probability
that
  is too confident or too optimistic.

Shrinkage is the repository's convention (`correlation.SHRINK_PRIOR_N`): the fitted log-scale (or `a`, `b -
1`) is multiplied by `n / (n + PRIOR_N)`, `n` the rows behind it.

Judged the way the other shadows are: fit on earlier weeks, score the held-out weeks. `rolling` refits before
each week and scores that week (the way it would run live); `split` is the one-cut version (fit weeks 1-4,
test 5-6). The Brier score is for the probability the sheet shows (to one decimal), per measure, with a paired
standard error clustered by team-week.

    python -m dfs.prob_recal            # the held-out report, from data/results and data/signals
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from dfs import probabilities
from dfs.model import distribution
from dfs.paths import REPO_ROOT

PRIOR_N = 200
SCALE_BOUNDS = (-0.4, 0.4)  # log c
MEASURES = (
    ("Hit3x%", probabilities.HIT_MULTIPLE, "at_least"),
    ("Boom%", probabilities.BOOM_MULTIPLE, "at_least"),
    ("Bust%", probabilities.BUST_MULTIPLE, "below"),
)
POSITIONS = probabilities.MODEL_POSITIONS
FILE = REPO_ROOT / "models" / "um" / "prob_recal.json"


@dataclass
class Recal:
    """Fitted corrections. `scale`: position -> c (1.0 = none). `platt`: measure -> (a, b, centre), where
    (0, 1) is no change."""

    scale: dict[str, float] = field(default_factory=dict)
    platt: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    n: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"scale": self.scale, "platt": self.platt, "n": self.n}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Recal:
        d = json.loads(text)
        return cls(
            {k: float(v) for k, v in d["scale"].items()}, {k: tuple(v) for k, v in d["platt"].items()}, d["n"]
        )


# --- outcomes and probabilities ----------------------------------------------------------------------------


def outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    """`y_<measure>` (0/1): did the actual reach 3x / 4x salary per $1,000, or fall under 2x."""
    actual = pd.to_numeric(frame["DkActual"], errors="coerce")
    k = pd.to_numeric(frame["Salary"], errors="coerce") / 1000.0
    out = pd.DataFrame(index=frame.index)
    for col, multiple, kind in MEASURES:
        out[f"y_{col}"] = (actual >= multiple * k) if kind == "at_least" else (actual < multiple * k)
    return out.astype(float).where(actual.notna() & (k > 0))


def engine_probs(
    position: str, projection: np.ndarray, salary: np.ndarray, c: float = 1.0
) -> dict[str, np.ndarray]:
    """The engine's three probabilities (0-1) for players of `position` projected at `c * projection`."""
    proj, k = np.asarray(projection, dtype=float) * c, np.asarray(salary, dtype=float) / 1000.0
    hit = distribution.prob_at_least_many(position, proj, probabilities.HIT_MULTIPLE * k)
    boom = distribution.prob_at_least_many(position, proj, probabilities.BOOM_MULTIPLE * k)
    bust = 1.0 - distribution.prob_at_least_many(position, proj, probabilities.BUST_MULTIPLE * k)
    return {"Hit3x%": hit, "Boom%": boom, "Bust%": bust}


def rated(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows the probability columns exist for (the engine's own rule: a CalPts, a salary, QBs from 10)."""
    calpts = pd.to_numeric(frame["CalPts"], errors="coerce")
    ok = (
        calpts.notna()
        & (pd.to_numeric(frame["Salary"], errors="coerce") > 0)
        & frame["Position"].isin(POSITIONS)
    )
    ok &= ~((frame["Position"] == "QB") & (calpts < probabilities.PROB_QB_MIN_CALPTS))
    ok &= pd.to_numeric(frame["DkActual"], errors="coerce").notna()
    return frame[ok]


# --- the fits ----------------------------------------------------------------------------------------------


def _brier_sum(position: str, rows: pd.DataFrame, y: pd.DataFrame, log_c: float) -> float:
    p = engine_probs(position, rows["CalPts"].to_numpy(), rows["Salary"].to_numpy(), float(np.exp(log_c)))
    return float(sum(((p[col] - y[f"y_{col}"].to_numpy()) ** 2).sum() for col, _, _ in MEASURES))


def fit_scale(rows: pd.DataFrame, by_position: bool = True, prior_n: int = PRIOR_N) -> tuple[dict, dict]:
    """`(scale, n)`: log c per position (or one `"all"` value), least squares on the summed Brier of the three
    measures, then shrunk by `n / (n + prior_n)`."""
    scale, counts = {}, {}
    groups = [(p, rows[rows["Position"] == p]) for p in POSITIONS] if by_position else [("all", rows)]
    y_all = outcomes(rows)
    for name, g in groups:
        if len(g) < 20:
            continue
        y = y_all.loc[g.index]
        if name == "all":

            def loss(lc, g=g, y=y):
                return sum(
                    _brier_sum(p, g[g["Position"] == p], y.loc[g[g["Position"] == p].index], lc)
                    for p in POSITIONS
                    if (g["Position"] == p).any()
                )
        else:

            def loss(lc, name=name, g=g, y=y):
                return _brier_sum(name, g, y, lc)

        best = minimize_scalar(loss, bounds=SCALE_BOUNDS, method="bounded").x
        w = len(g) / (len(g) + prior_n)
        scale[name], counts[name] = float(np.exp(w * best)), len(g)
    return scale, counts


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit_platt(rows: pd.DataFrame, prior_n: int = PRIOR_N) -> tuple[dict, dict]:
    """`(platt, n)`: per measure, the (a, b) minimising the log loss of `logistic(c + a + b * (logit(p) - c))`,
    c the centre, over the rows' shown probabilities (`<measure>` columns), then pulled toward (0, 1) by
    `prior_n / (n + prior_n)`."""
    from scipy.optimize import minimize

    y_all = outcomes(rows)
    platt, counts = {}, {}
    for col, _, _ in MEASURES:
        p, y = pd.to_numeric(rows[col], errors="coerce") / 100.0, y_all[f"y_{col}"]
        ok = p.notna() & y.notna()
        if ok.sum() < 50:
            continue
        x, yy = _logit(p[ok].to_numpy()), y[ok].to_numpy()
        centre = float(x.mean())  # (a, b): the level shift at the centre logit and the slope about it

        def nll(theta, x=x, yy=yy, centre=centre):
            z = centre + theta[0] + theta[1] * (x - centre)
            return float(np.sum(np.logaddexp(0, z) - yy * z))

        a, b = minimize(nll, [0.0, 1.0], method="Nelder-Mead").x
        w = ok.sum() / (ok.sum() + prior_n)
        platt[col], counts[col] = (float(w * a), float(1 + w * (b - 1)), centre), int(ok.sum())
    return platt, counts


def fit(rows: pd.DataFrame, by_position: bool = True, prior_n: int = PRIOR_N) -> Recal:
    scale, n_scale = fit_scale(rows, by_position, prior_n)
    platt, n_platt = fit_platt(rows, prior_n)
    return Recal(
        scale,
        platt,
        {**{f"scale:{k}": v for k, v in n_scale.items()}, **{f"platt:{k}": v for k, v in n_platt.items()}},
    )


# --- applying a fit ----------------------------------------------------------------------------------------


def apply_scale(rows: pd.DataFrame, scale: dict[str, float]) -> pd.DataFrame:
    """The three probabilities (percent, one decimal, as the sheet shows them) with each position's `c`."""
    out = pd.DataFrame(np.nan, index=rows.index, columns=[m[0] for m in MEASURES])
    for position in POSITIONS:
        mask = (rows["Position"] == position).to_numpy()
        if not mask.any():
            continue
        c = scale.get(position, scale.get("all", 1.0))
        p = engine_probs(
            position, rows.loc[mask, "CalPts"].to_numpy(), rows.loc[mask, "Salary"].to_numpy(), c
        )
        for col in out.columns:
            out.loc[mask, col] = np.round(100 * p[col], probabilities.PROB_DECIMALS)
    return out


def apply_platt(shown: pd.DataFrame, platt: dict[str, tuple[float, float, float]]) -> pd.DataFrame:
    out = shown.copy()
    for col, (a, b, centre) in platt.items():
        z = centre + a + b * (_logit(shown[col] / 100.0) - centre)
        out[col] = np.round(100 / (1 + np.exp(-z)), probabilities.PROB_DECIMALS)
    return out


def recalibrated(rows: pd.DataFrame, recal: Recal, kind: str) -> pd.DataFrame:
    if kind == "scale":
        return apply_scale(rows, recal.scale)
    if kind == "platt":
        return apply_platt(rows[[m[0] for m in MEASURES]], recal.platt)
    raise ValueError(kind)


# --- the held-out score ------------------------------------------------------------------------------------


def brier_rows(rows: pd.DataFrame, shown: pd.DataFrame) -> pd.DataFrame:
    """Per row and measure: squared error of the shown probability (0-1)."""
    y = outcomes(rows)
    return pd.DataFrame({col: (shown[col] / 100.0 - y[f"y_{col}"]) ** 2 for col, _, _ in MEASURES})


def evaluate(frame: pd.DataFrame, plan: list[tuple[list[int], list[int]]], by_position: bool = True) -> dict:
    """For each (fit weeks, test weeks) in `plan`: fit on the earlier rows and score the later ones. Returns
    the per-row Brier of the old probabilities (as shown on the day) and of each recalibration, over every
    test
    row, plus the paired differences and standard errors by measure."""
    rows = rated(frame)
    olds, news = [], {"scale": [], "platt": [], "scale_global": []}
    for fit_weeks, test_weeks in plan:
        train, test = rows[rows["week"].isin(fit_weeks)], rows[rows["week"].isin(test_weeks)]
        if train.empty or test.empty:
            continue
        shown_old = test[[m[0] for m in MEASURES]]
        olds.append(
            brier_rows(test, shown_old).assign(team_week=test["Team"] + "|" + test["week"].astype(str))
        )
        rc = fit(train, by_position=by_position)
        news["scale"].append(brier_rows(test, apply_scale(test, rc.scale)))
        news["scale_global"].append(
            brier_rows(test, apply_scale(test, fit_scale(train, by_position=False)[0]))
        )
        news["platt"].append(brier_rows(test, apply_platt(shown_old, rc.platt)))
    old = pd.concat(olds)
    out = {"n": len(old), "old": old[[m[0] for m in MEASURES]].mean().to_dict(), "kinds": {}}
    for kind, parts in news.items():
        new = pd.concat(parts)
        res = {}
        for col, _, _ in MEASURES:
            d = old[col] - new[col]
            per = d.groupby(old["team_week"]).sum()
            res[col] = {
                "new": float(new[col].mean()),
                "better": float(d.mean()),
                "se": float(np.sqrt((per**2).sum()) / len(d)),
            }
        out["kinds"][kind] = res
    return out


SPLIT_PLAN = [([1, 2, 3, 4], [5, 6])]
ROLLING_PLAN = [(list(range(1, k)), [k]) for k in range(3, 8)]


def render(result: dict, title: str) -> str:
    lines = [f"{title}: {result['n']} held-out player-rows (Brier, lower is better; old - new, +SE)"]
    for kind, res in result["kinds"].items():
        parts = [
            f"{col} {result['old'][col]:.4f} -> {r['new']:.4f} ({r['better']:+.4f}, SE {r['se']:.4f})"
            for col, r in res.items()
        ]
        lines.append(f"  {kind:13} " + "  |  ".join(parts))
    return "\n".join(lines)


def load_frame(season: int = 2026) -> pd.DataFrame:
    from dfs import results_signals, results_update

    return results_signals.evaluation_frame(results_update.load_all_scored(season), season, None)


def save(recal: Recal, path: Path = FILE) -> Path:
    path.write_text(recal.to_json() + "\n")
    return path


if __name__ == "__main__":
    frame = load_frame()
    top = int(frame["week"].max())
    print(render(evaluate(frame, SPLIT_PLAN), "fit weeks 1-4, test 5-6"))
    print(render(evaluate(frame, [p for p in ROLLING_PLAN if p[1][0] <= top]), f"rolling, weeks 3-{top}"))
    print()
    print(fit(rated(frame)).to_json())
