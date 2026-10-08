"""Part 3: validate the simulator on history.

For every regular-season week of 2023-2025 it builds about 200 random LEGAL DraftKings classic lineups out of
that week's out-of-fold player-games (half stacked, half random), predicts each lineup's distribution with the
simulator from the UM projections, and compares it with the lineup's realized score (the sum of its players'
actual DK points).

No leakage in the correlations: they are fitted on 2014-2022 only, and the games being predicted are never in
the fit. Two sets of marginals are run, because the shipped distribution tables were built on 2014-2025:

- **shipped** tables (the engine as the simulator uses it; in-sample for the test seasons);
- **train-only** tables, rebuilt from 2014-2022 out-of-fold rows alone (out-of-time; the stricter test).

Each set is run with the fitted correlations and with every correlation forced to 0.

Limits, all repeated in docs/SIM.md: the lineups ignore the salary cap (there is no salary in this data), the
pool is every player the model projects (so lineups skew toward starters), roles are usage proxies from prior
games, and the out-of-fold GBM projections for WR and DST were fit with other seasons (later ones too),
held out only for the season itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.model import distribution as dist
from dfs.sim.correlation import CorrelationLookup, fit_correlations
from dfs.sim.simulate import DEFAULT_CASH_LINE, DEFAULT_GPP_TARGET, PlayerSpec, simulate_lineups

TRAIN_THROUGH = 2022
TEST_SEASONS = (2023, 2024, 2025)
LINEUPS_PER_WEEK = 200
STACKED_SHARE = 0.5
# Each lineup draws players with probability proportional to projection ** selectivity: 1 is close to a
# random pool, 6 is close to "the chalk". Mixing them gives lineups across the whole range of strength.
SELECTIVITY = (1.0, 3.0, 6.0)
COVERAGE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)
COVERAGE_TOL = 3.0  # points
RELIABILITY_TOL = 4.0  # points
MAX_PER_TEAM = 8
SLOT_COUNTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
FLEX_POSITIONS = ("RB", "WR", "TE")
CATCHERS = ("WR", "TE")


def is_legal(rows: pd.DataFrame) -> bool:
    """Is this set of player-games a legal DraftKings classic lineup (ignoring the salary cap)? Nine distinct
    players: 1 QB, 1 DST, and RB / WR / TE counts that are the base 2 / 3 / 1 plus exactly one FLEX from
    RB, WR or TE; from at least 2 different games; no more than 8 from one team."""
    if len(rows) != 9 or rows["gsis_id"].duplicated().any():
        return False
    n = rows["position"].value_counts()
    if n.get("QB", 0) != 1 or n.get("DST", 0) != 1:
        return False
    extra = {p: n.get(p, 0) - SLOT_COUNTS[p] for p in FLEX_POSITIONS}
    if any(v < 0 for v in extra.values()) or sum(extra.values()) != 1:
        return False
    if rows["game_id"].nunique() < 2:
        return False
    return int(rows["team"].value_counts().max()) <= MAX_PER_TEAM


@dataclass
class Lineup:
    season: int
    week: int
    rows: pd.DataFrame
    stacked: bool
    selectivity: float


def _pick(rng: np.random.Generator, pool: pd.DataFrame, k: int, power: float) -> pd.DataFrame:
    if len(pool) < k:
        raise ValueError("pool too small")
    w = pool["pred"].to_numpy() ** power
    take = rng.choice(len(pool), size=k, replace=False, p=w / w.sum())
    return pool.iloc[take]


def _fill(rng: np.random.Generator, week: pd.DataFrame, chosen: pd.DataFrame, power: float) -> pd.DataFrame:
    """Complete `chosen` into a nine-player lineup, with at most one FLEX."""
    have = chosen["position"].value_counts().to_dict()
    parts = [chosen]
    taken = set(chosen["gsis_id"])
    flex_used = sum(max(have.get(p, 0) - SLOT_COUNTS[p], 0) for p in FLEX_POSITIONS)
    if flex_used > 1:
        raise ValueError("more than one FLEX")
    for position, need in SLOT_COUNTS.items():
        short = need - have.get(position, 0)
        if short > 0:
            pool = week[(week["position"] == position) & ~week["gsis_id"].isin(taken)]
            got = _pick(rng, pool, short, power)
            parts.append(got)
            taken |= set(got["gsis_id"])
    if flex_used == 0:
        pool = week[week["position"].isin(FLEX_POSITIONS) & ~week["gsis_id"].isin(taken)]
        parts.append(_pick(rng, pool, 1, power))
    return pd.concat(parts)


def _stack(rng: np.random.Generator, week: pd.DataFrame, power: float) -> pd.DataFrame:
    """QB + 2 pass catchers from his team + 1 pass catcher from the other team of his game."""
    qb = _pick(rng, week[week["position"] == "QB"], 1, power)
    team, game = qb["team"].iloc[0], qb["game_id"].iloc[0]
    catchers = week[week["position"].isin(CATCHERS) & (week["game_id"] == game)]
    mates = _pick(rng, catchers[catchers["team"] == team], 2, power)
    bring_back = _pick(rng, catchers[catchers["team"] != team], 1, power)
    return pd.concat([qb, mates, bring_back])


def generate_lineups(
    week: pd.DataFrame, n: int, rng: np.random.Generator, *, stacked_share: float = STACKED_SHARE
) -> list[Lineup]:
    """`n` legal lineups from one week's player-games (`week`: rows of `roles.build_frame` for that week);
    `stacked_share` of them are stacks. A draw that comes out illegal or impossible is redrawn."""
    season, wk = int(week["season"].iloc[0]), int(week["week"].iloc[0])
    out: list[Lineup] = []
    n_stacked = int(round(n * stacked_share))
    kinds = [True] * n_stacked + [False] * (n - n_stacked)
    for stacked in kinds:
        for _ in range(1000):
            power = float(rng.choice(SELECTIVITY))
            try:
                seed_rows = _stack(rng, week, power) if stacked else week.iloc[0:0]
                rows = _fill(rng, week, seed_rows, power)
            except ValueError:
                continue
            if is_legal(rows):
                out.append(Lineup(season, wk, rows.reset_index(drop=True), stacked, power))
                break
        else:
            raise RuntimeError(f"could not build a legal lineup for {season} week {wk}")
    return out


def to_specs(rows: pd.DataFrame, tag: str) -> list[PlayerSpec]:
    return [
        PlayerSpec(
            id=f"{tag}:{r.gsis_id}",
            position=r.position,
            team=r.team,
            opp=r.opp,
            game_id=r.game_id,
            role=r.role,
            projection=float(r.pred),
        )
        for r in rows.itertuples()
    ]


@dataclass
class Variant:
    name: str
    tables: pd.DataFrame | None
    lookup: CorrelationLookup


def make_variants(frame: pd.DataFrame, *, seed: int = 0, n_boot: int = 200) -> list[Variant]:
    """The two marginal sets, each with correlations fitted on `frame` through `TRAIN_THROUGH` only."""
    train = frame[frame["season"] <= TRAIN_THROUGH]
    train_tables = pd.concat(
        [dist.build_tables(train.loc[train["position"] == p, ["pred", "dk"]], p) for p in dist.BUCKET_EDGES],
        ignore_index=True,
    )
    return [
        Variant("shipped", None, fit_correlations(train, seed=seed, n_boot=n_boot).lookup()),
        Variant(
            "train-only",
            train_tables,
            fit_correlations(train, tables=train_tables, seed=seed, n_boot=n_boot).lookup(),
        ),
    ]


def top_centres() -> dict[str, float]:
    """Per position, the mean projection of the shipped tables' highest bucket: past it the engine has no
    bucket of its own and reuses the top bucket's outcome ratios."""
    tables = dist.load_tables()
    return {p: float(g["center"].max()) for p, g in tables.groupby("position")}


def _week_records(
    week: pd.DataFrame,
    variants: list[Variant],
    *,
    lineups_per_week: int,
    n_sims: int,
    seed: int,
    cash_line: float,
    gpp_target: float,
    centres: dict[str, float],
) -> list[dict]:
    """The back-test rows for one week. Everything random comes from a generator keyed by (seed, season,
    week), so a week's rows do not depend on which other weeks run, or in what order or process."""
    season, wk = int(week["season"].iloc[0]), int(week["week"].iloc[0])
    rng = np.random.default_rng([seed + 7, season, wk])
    lineups = generate_lineups(week, lineups_per_week, rng)
    sim_seeds = rng.integers(0, 2**31 - 1, size=len(lineups))
    rows = []
    for lu, sim_seed in zip(lineups, sim_seeds, strict=True):
        rec = {
            "season": lu.season,
            "week": lu.week,
            "stacked": lu.stacked,
            "selectivity": lu.selectivity,
            "realized": float(lu.rows["dk"].sum()),
            "projected": float(lu.rows["pred"].sum()),
            "beyond_top_centre": bool((lu.rows["pred"] > lu.rows["position"].map(centres)).any()),
        }
        specs = to_specs(lu.rows, f"{lu.season}-{lu.week}")
        for v in variants:
            for mode in ("corr", "indep"):
                res = simulate_lineups(
                    [specs],
                    n_sims=n_sims,
                    seed=int(sim_seed),
                    cash_line=cash_line,
                    gpp_target=gpp_target,
                    correlations=v.lookup,
                    tables=v.tables,
                    independent=mode == "indep",
                )
                st = res.lineups[0]
                pre = f"{v.name}.{mode}."
                rec[pre + "mean"] = st.mean
                rec[pre + "sd"] = st.sd
                for q, val in st.quantiles.items():
                    rec[pre + f"p{round(q * 100)}"] = val
                rec[pre + "p_cash"] = st.p_cash
                rec[pre + "p_gpp"] = st.p_gpp
                if mode == "corr":
                    rec[f"{v.name}.repaired"] = res.repair.needed
                    rec[f"{v.name}.repair_change"] = res.repair.max_change
        rows.append(rec)
    return rows


def run_backtest(
    frame: pd.DataFrame,
    *,
    variants: list[Variant] | None = None,
    lineups_per_week: int = LINEUPS_PER_WEEK,
    n_sims: int = 10000,
    seed: int = 0,
    cash_line: float = DEFAULT_CASH_LINE,
    gpp_target: float = DEFAULT_GPP_TARGET,
    seasons: tuple[int, ...] = TEST_SEASONS,
    n_jobs: int = 1,
    log=lambda msg: None,
) -> pd.DataFrame:
    """One row per lineup: `season, week, stacked, selectivity, realized, projected, beyond_top_centre` and,
    for every `variant x {corr, indep}`, the predicted mean, sd, quantiles, p_cash and p_gpp (columns
    `<variant>.<mode>.<stat>`), plus `repaired` / `repair_change` for the correlated runs.

    Deterministic in `seed` whatever `n_jobs` is (every week is a task with its own generator)."""
    variants = variants or make_variants(frame, seed=seed)
    centres = top_centres()
    weeks = [
        frame[(frame["season"] == season) & (frame["week"] == wk)].reset_index(drop=True)
        for season in seasons
        for wk in sorted(frame.loc[frame["season"] == season, "week"].unique())
    ]
    log(f"{len(weeks)} weeks x {lineups_per_week} lineups, {n_jobs} worker(s)")
    kwargs = dict(
        lineups_per_week=lineups_per_week,
        n_sims=n_sims,
        seed=seed,
        cash_line=cash_line,
        gpp_target=gpp_target,
        centres=centres,
    )
    if n_jobs == 1:
        done = []
        for week in weeks:
            log(f"{int(week['season'].iloc[0])} week {int(week['week'].iloc[0])}")
            done.append(_week_records(week, variants, **kwargs))
    else:
        from joblib import Parallel, delayed

        done = Parallel(n_jobs=n_jobs)(delayed(_week_records)(week, variants, **kwargs) for week in weeks)
    return pd.DataFrame([rec for rows in done for rec in rows])


# --- reading the results ----------------------------------------------------------------------------------


def coverage(results: pd.DataFrame, variant: str, mode: str, mask: pd.Series | None = None) -> pd.DataFrame:
    """Share of realized scores below each predicted quantile, against the target level."""
    r = results if mask is None else results[mask]
    rows = []
    for level in COVERAGE_LEVELS:
        below = (r["realized"] < r[f"{variant}.{mode}.p{round(level * 100)}"]).to_numpy()
        share = float(below.mean())
        rows.append(
            {
                "level": level,
                "target": 100 * level,
                "realized": 100 * share,
                "gap": 100 * (share - level),
                "se": 100 * float(np.sqrt(level * (1 - level) / len(r))),
                "n": len(r),
            }
        )
    return pd.DataFrame(rows)


def reliability(
    results: pd.DataFrame,
    variant: str,
    mode: str,
    stat: str,
    threshold: float,
    mask: pd.Series | None = None,
    bins: int = 10,
) -> pd.DataFrame:
    """Realized rate of `realized >= threshold` against the predicted probability (`stat`: 'p_cash' or
    'p_gpp'), in `bins` equal-count bins of the prediction."""
    r = results if mask is None else results[mask]
    pred = r[f"{variant}.{mode}.{stat}"].to_numpy()
    hit = (r["realized"] >= threshold).to_numpy()
    order = np.argsort(pred, kind="stable")
    rows = []
    for i, chunk in enumerate(np.array_split(order, bins), start=1):
        if len(chunk) == 0:
            continue
        p, h = float(pred[chunk].mean()), float(hit[chunk].mean())
        rows.append(
            {
                "bin": i,
                "n": len(chunk),
                "predicted": 100 * p,
                "realized": 100 * h,
                "gap": 100 * (h - p),
                "se": 100 * float(np.sqrt(max(p * (1 - p), 1e-12) / len(chunk))),
                "hits": int(hit[chunk].sum()),
            }
        )
    return pd.DataFrame(rows)


def verdict(cov: pd.DataFrame, rel: dict[str, pd.DataFrame]) -> dict:
    """Is it accepted? Every coverage figure within +/-3 points and every reliability bin within +/-4."""
    cov_gap = float(cov["gap"].abs().max())
    rel_gap = {k: float(v["gap"].abs().max()) for k, v in rel.items()}
    return {
        "max_coverage_gap": cov_gap,
        "max_reliability_gap": rel_gap,
        "coverage_ok": cov_gap <= COVERAGE_TOL,
        "reliability_ok": all(g <= RELIABILITY_TOL for g in rel_gap.values()),
    }


def pinball(results: pd.DataFrame, variant: str, mode: str) -> pd.Series:
    """Per-lineup pinball loss averaged over `COVERAGE_LEVELS` (lower is better): the standard score for
    predicted quantiles, and unlike coverage it rewards sharpness as well as calibration."""
    loss = 0.0
    for level in COVERAGE_LEVELS:
        err = results["realized"] - results[f"{variant}.{mode}.p{round(level * 100)}"]
        loss = loss + np.maximum(level * err, (level - 1) * err)
    return loss / len(COVERAGE_LEVELS)


def brier(results: pd.DataFrame, variant: str, mode: str, stat: str, threshold: float) -> pd.Series:
    """Per-lineup squared error of P(realized >= threshold) (lower is better)."""
    return (results[f"{variant}.{mode}.{stat}"] - (results["realized"] >= threshold)) ** 2


def paired_difference(
    results: pd.DataFrame, independent: pd.Series, correlated: pd.Series
) -> tuple[float, float]:
    """Mean of (independent - correlated) loss, positive when correlation helps, and its standard error with
    each (season, week) as one observation -- lineups in a week share players and games, so they are not
    independent draws."""
    per_week = (independent - correlated).groupby([results["season"], results["week"]]).mean()
    return float(per_week.mean()), float(per_week.std(ddof=1) / np.sqrt(len(per_week)))
