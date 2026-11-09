"""Plan S, check 2: does the "surprise" method understate the QB-receiver correlation?

The shipped table (`correlation.py`) is the Pearson correlation of normal scores ("surprises": each actual
pushed through the player's own predicted distribution), shrunk toward 0. QB1-WR1 comes out +0.31; a friend's
tool hand-sets 0.45 and the literature cites 0.4-0.5. This compares, per role pair (QB1 with WR1, WR2, TE1 and
RB1 of his team; QB1 against the opposing DST), with game-level bootstrap 90% intervals:

- `surprise`: the shipped method (Pearson of z, unshrunk; `surprise_shrunk` with the shipped prior);
- `resid_pearson`: plain Pearson of the residuals in points, `actual - projection`;
- `resid_spearman`: Spearman of the residuals; `z_spearman`: Spearman of the surprises (rank-free of the
  margins);
- `points`: Pearson of the raw DK points (what the cited figures measure).

**The decider is calibration**, held out: every estimate is fitted on 2014-2022 (tables and pairs), and on
2023-2026 the share of team-games where BOTH players beat their own predicted 90th (and 95th) percentile is
compared with what a Gaussian copula with that correlation says: `n * P(Z1 > c, Z2 > c; rho)` with
`c = Phi^-1(q)`, which is exactly what `simulate_lineups` gives for two players (each keeps his own marginal,
so the joint rate depends only on the pair's rho; `tests/sim/test_qbwr_check.py` checks that against the
simulator). The observed count's SE is a game bootstrap. A candidate replaces the shipped constant only if it
cuts the calibration miss by more than 2 SE. Two views:

- `table margins`: u through the 2014-2022 tables, as the simulator scores a held-out game (the marginal miss
  counts against every candidate alike);
- `exact margins`: u replaced by its rank within the held-out role (each margin exactly uniform), which
  isolates the dependence from the tables: the earlier finding was that part of the stack-boom excess came
  from the marginals (`tail_check`, about a tenth once measured held out).

Also the held-out Gaussian-copula log-likelihood of the z pairs under each rho (per 1,000 pairs, minus the
shipped method's, game-clustered SE): the sensitive pair-level test.

    python -m dfs.sim.qbwr_check --frame frame.parquet     # a parquet of sim.pipeline.load_frame(2026)

Nothing here changes `models/sim/`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import multivariate_normal, norm, rankdata

from dfs import tail_check
from dfs.sim import correlation

PAIRS: tuple[tuple[str, str, str], ...] = (
    ("same_team", "QB1", "WR1"),
    ("same_team", "QB1", "WR2"),
    ("same_team", "QB1", "TE1"),
    ("same_team", "QB1", "RB1"),
    ("opp", "QB1", "DST"),
)
METHODS = ("surprise", "surprise_shrunk", "resid_pearson", "resid_spearman", "z_spearman", "points")
TRAIN_THROUGH = tail_check.TRAIN_THROUGH
TEST_SEASONS = (2023, 2024, 2025, 2026)
QUANTILES = (0.90, 0.95)
# The outside value per pair: QB-WR 0.45 is the friend's hand-set constant and the middle of the cited 0.4-0.5
# (for WR1 and WR2 alike: his tool has one QB-WR number); the others are docs/SIM.md's "Cited" column.
HAND_SET = {"QB1-WR1": 0.45, "QB1-WR2": 0.45, "QB1-TE1": 0.30, "QB1-RB1": 0.10, "QB1-opp DST": -0.46}
N_BOOT = 1000
RHO_CLIP = 0.995


def with_scores(frame: pd.DataFrame, tables: pd.DataFrame | None, seed: int = 0) -> pd.DataFrame:
    """`frame` plus `z` (normal score through `tables`), `u = Phi(z)` and `resid = dk - pred`."""
    out = frame.copy()
    out["z"] = correlation.normal_scores(out, tables=tables, seed=seed)
    out["u"] = norm.cdf(out["z"])
    out["resid"] = out["dk"] - out["pred"]
    return out


def pairs_of(scored: pd.DataFrame, relation: str, a: str, b: str) -> pd.DataFrame:
    """One row per (game, team) holding both roles: `game_id, season, za, zb, ua, ub, ra, rb, pa, pb`."""
    cols = ["game_id", "season", "team", "opp", "z", "u", "resid", "dk"]
    left = scored.loc[scored["role"] == a, cols]
    right = scored.loc[scored["role"] == b, cols]
    if relation == "same_team":
        m = left.merge(right, on=["game_id", "season", "team"], suffixes=("_a", "_b"))
    else:
        m = left.merge(
            right,
            left_on=["game_id", "season", "opp"],
            right_on=["game_id", "season", "team"],
            suffixes=("_a", "_b"),
        )
    return pd.DataFrame(
        {
            "game_id": m["game_id"].to_numpy(),
            "season": m["season"].to_numpy(),
            "za": m["z_a"].to_numpy(),
            "zb": m["z_b"].to_numpy(),
            "ua": m["u_a"].to_numpy(),
            "ub": m["u_b"].to_numpy(),
            "ra": m["resid_a"].to_numpy(),
            "rb": m["resid_b"].to_numpy(),
            "pa": m["dk_a"].to_numpy(),
            "pb": m["dk_b"].to_numpy(),
        }
    )


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 else float("nan")


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    return _pearson(rankdata(x), rankdata(y)) if len(x) > 2 else float("nan")


def estimates(p: pd.DataFrame) -> dict[str, float]:
    """Every method's correlation on the pair rows `p`."""
    r = _pearson(p["za"].to_numpy(), p["zb"].to_numpy())
    return {
        "surprise": r,
        "surprise_shrunk": float(correlation.shrink(r, len(p))),
        "resid_pearson": _pearson(p["ra"].to_numpy(), p["rb"].to_numpy()),
        "resid_spearman": _spearman(p["ra"].to_numpy(), p["rb"].to_numpy()),
        "z_spearman": _spearman(p["za"].to_numpy(), p["zb"].to_numpy()),
        "points": _pearson(p["pa"].to_numpy(), p["pb"].to_numpy()),
    }


def _resample_index(games: np.ndarray, rng: np.random.Generator, n_boot: int):
    """Row indices of `n_boot` game-level bootstrap samples (whole games drawn with replacement)."""
    codes, inverse = np.unique(games, return_inverse=True)
    rows_of = [np.flatnonzero(inverse == g) for g in range(len(codes))]
    for _ in range(n_boot):
        pick = rng.integers(0, len(codes), size=len(codes))
        yield np.concatenate([rows_of[g] for g in pick])


def bootstrap(p: pd.DataFrame, *, n_boot: int = N_BOOT, seed: int = 0, level: float = 0.90) -> pd.DataFrame:
    """Per method: the estimate and its game-bootstrap interval at `level`."""
    rng = np.random.default_rng(seed)
    draws = [estimates(p.iloc[idx]) for idx in _resample_index(p["game_id"].to_numpy(), rng, n_boot)]
    point = estimates(p)
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    rows = []
    for m in METHODS:
        vals = np.array([d[m] for d in draws])
        rows.append(
            {
                "method": m,
                "rho": point[m],
                "lo": float(np.quantile(vals, lo)),
                "hi": float(np.quantile(vals, hi)),
            }
        )
    return pd.DataFrame(rows)


def as_copula_rho(method: str, value: float) -> float:
    """The Gaussian-copula correlation a method's estimate implies: Spearman's rho converted
    (`2 sin(pi rho_s / 6)`, exact for a Gaussian copula); every Pearson estimate used as it is."""
    if method in ("resid_spearman", "z_spearman"):
        value = 2 * np.sin(np.pi * value / 6)
    return float(np.clip(value, -RHO_CLIP, RHO_CLIP))


def joint_tail_prob(rho: float, q: float) -> float:
    """P(Z1 > c, Z2 > c) for a standard bivariate normal with correlation `rho`, `c = Phi^-1(q)`."""
    c = norm.ppf(q)
    # P(Z1 > c, Z2 > c) = P(-Z1 < -c, -Z2 < -c), and (-Z1, -Z2) has the same correlation.
    return float(multivariate_normal(mean=[0.0, 0.0], cov=[[1.0, rho], [rho, 1.0]]).cdf([-c, -c]))


def exact_margins(p: pd.DataFrame) -> pd.DataFrame:
    """`p` with `ua` / `ub` replaced by their ranks in `p` (each margin uniform on its own sample)."""
    out = p.copy()
    out["ua"] = rankdata(out["ua"]) / (len(out) + 1)
    out["ub"] = rankdata(out["ub"]) / (len(out) + 1)
    return out


@dataclass(frozen=True)
class Calibration:
    observed: int
    se: float  # game-bootstrap SE of the observed count
    n: int


def observed_joint(p: pd.DataFrame, q: float, *, n_boot: int = N_BOOT, seed: int = 0) -> Calibration:
    """Team-games with both players above their own `q` quantile, and its game-bootstrap SE."""
    hit = ((p["ua"] > q) & (p["ub"] > q)).to_numpy()
    rng = np.random.default_rng(seed)
    counts = [int(hit[idx].sum()) for idx in _resample_index(p["game_id"].to_numpy(), rng, n_boot)]
    return Calibration(int(hit.sum()), float(np.std(counts, ddof=1)), len(p))


def copula_loglik(za: np.ndarray, zb: np.ndarray, rho: float) -> np.ndarray:
    """Per pair, the Gaussian copula's log density at the normal scores `(za, zb)` (the margins cancel)."""
    r2 = 1 - rho**2
    return -0.5 * np.log(r2) - (rho**2 * (za**2 + zb**2) - 2 * rho * za * zb) / (2 * r2)


def candidates(train_est: dict[str, float], shipped: float | None, outside: float | None) -> dict[str, float]:
    """The copula rho each candidate would ship: every method fitted on the training seasons, the committed
    table's value (fitted on 2014-2025, so in sample for 2023-2025), the outside value (`HAND_SET`), and
    independence."""
    out = {m: as_copula_rho(m, v) for m, v in train_est.items()}
    if shipped is not None:
        out["committed table"] = float(shipped)
    if outside is not None:
        out["hand-set / cited"] = float(outside)
    out["independent"] = 0.0
    return out


def calibration_table(
    test: pd.DataFrame, cands: dict[str, float], *, n_boot: int = N_BOOT, seed: int = 0
) -> pd.DataFrame:
    """Per view (table / exact margins) and quantile: each candidate's expected count against the observed,
    the miss in SE, and the improvement over the shipped method (`surprise_shrunk`) in SE (positive =
    closer)."""
    rows = []
    for view, data in (("table margins", test), ("exact margins", exact_margins(test))):
        for q in QUANTILES:
            cal = observed_joint(data, q, n_boot=n_boot, seed=seed)
            base = cal.n * joint_tail_prob(cands["surprise_shrunk"], q)
            for name, rho in cands.items():
                expected = cal.n * joint_tail_prob(rho, q)
                rows.append(
                    {
                        "view": view,
                        "q": q,
                        "candidate": name,
                        "rho": rho,
                        "n": cal.n,
                        "observed": cal.observed,
                        "expected": expected,
                        "ratio": cal.observed / expected if expected > 0 else float("nan"),
                        "miss_se": (cal.observed - expected) / cal.se if cal.se > 0 else float("nan"),
                        "improvement_se": (abs(cal.observed - base) - abs(cal.observed - expected)) / cal.se
                        if cal.se > 0
                        else float("nan"),
                    }
                )
    return pd.DataFrame(rows)


def loglik_table(test: pd.DataFrame, cands: dict[str, float]) -> pd.DataFrame:
    """Held-out copula log-likelihood per 1,000 pairs, each candidate minus the shipped method,
    game-clustered SE."""
    za, zb = test["za"].to_numpy(), test["zb"].to_numpy()
    base = copula_loglik(za, zb, cands["surprise_shrunk"])
    rows = []
    for name, rho in cands.items():
        d = copula_loglik(za, zb, rho) - base
        per_game = pd.Series(d).groupby(test["game_id"].to_numpy()).sum()
        se = float(np.sqrt((per_game**2).sum()) / len(d))
        rows.append(
            {"candidate": name, "rho": rho, "gain_per_1000": 1000 * float(d.mean()), "se_per_1000": 1000 * se}
        )
    return pd.DataFrame(rows)


def run(frame: pd.DataFrame, *, n_boot: int = N_BOOT, seed: int = 0) -> dict[str, pd.DataFrame]:
    """Every table of the check: estimates by period, calibration and log-likelihood per pair."""
    tables = tail_check.train_tables(frame)
    scored = with_scores(frame, tables, seed=seed)
    try:
        shipped = correlation.load_correlations()
    except Exception:  # noqa: BLE001 - the committed table is a reference column only
        shipped = None
    est, cal, ll = [], [], []
    for relation, a, b in PAIRS:
        p = pairs_of(scored, relation, a, b)
        label = f"{a}-{b}" if relation == "same_team" else f"{a}-opp {b}"
        train = p[p["season"] <= TRAIN_THROUGH].reset_index(drop=True)
        test = p[p["season"].isin(TEST_SEASONS)].reset_index(drop=True)
        for period, part in (("2014-2022", train), ("2023-2026", test), ("2014-2026", p)):
            t = bootstrap(part, n_boot=n_boot, seed=seed)
            t.insert(0, "n", len(part))
            t.insert(0, "period", period)
            t.insert(0, "pair", label)
            est.append(t)
        committed = None if shipped is None else shipped.rho(relation, a, b)
        cands = candidates(estimates(train), committed, HAND_SET.get(label))
        c = calibration_table(test, cands, n_boot=n_boot, seed=seed)
        c.insert(0, "pair", label)
        cal.append(c)
        lt = loglik_table(test, cands)
        lt.insert(0, "pair", label)
        ll.append(lt)
    return {
        "estimates": pd.concat(est, ignore_index=True),
        "calibration": pd.concat(cal, ignore_index=True),
        "loglik": pd.concat(ll, ignore_index=True),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--boot", type=int, default=N_BOOT)
    ap.add_argument("--save", help="folder for the three tables as CSV")
    args = ap.parse_args()
    out = run(pd.read_parquet(args.frame), n_boot=args.boot)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 500)
    for name, t in out.items():
        print(f"\n== {name}")
        print(t.round(4).to_string(index=False))
        if args.save:
            from pathlib import Path

            Path(args.save).mkdir(parents=True, exist_ok=True)
            t.to_csv(Path(args.save) / f"{name}.csv", index=False)


if __name__ == "__main__":
    main()
