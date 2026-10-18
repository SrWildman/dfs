"""The shipping test for a conditional (spread / implied-total) correlation table: the existing back-test's
lineups and measures, run with the pooled table and with each conditional table on the SAME draws.

`backtest.py` builds random legal lineups for 2023-2025 and compares the simulator's predicted lineup
quantiles with what the lineup scored. This module reuses its lineup generator and measures and changes two
things only:

- the lookups: every lineup is simulated once per lookup (the pooled table fitted on 2014-2022, and the
  conditional tables fitted on the same seasons), with the same seed, so the draws are identical and the only
  difference is the correlation matrix;
- two extra lineup families that contain the pairs under test -- **rb_dst** (a RB1 and his own DST) and
  **game** (a QB, one of his pass catchers and two players of the opposing team) -- so a pair the
  conditional table changes is actually in some lineups. `random` and `stack` are the existing two.

The bar (docs/SIM.md, "Game script"): a conditional table ships only if, out of sample, (1) it beats the
pooled one on the pinball loss of the lineup quantiles by more than two paired standard errors (weeks are the
unit) under BOTH marginal sets, (2) it does not lose on the Brier scores of P(cash) and P(top end) by more
than one, and (3) its gain in the held-out pair likelihood is positive and beats the shuffled-context
control (the same table fitted on contexts with no link to the surprises, `script.shuffled_frame`) with the
5% error shared across the four tables examined (spread and implied total, each over all pairs and
over the named pairs). Anything less is chance and ships nothing (`script_report.ruling`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model import distribution as dist
from dfs.sim import backtest as bt
from dfs.sim import script
from dfs.sim.correlation import fit_correlations
from dfs.sim.simulate import DEFAULT_CASH_LINE, DEFAULT_GPP_TARGET, PlayerSpec, simulate_lineups

FAMILIES = ("random", "stack", "rb_dst", "game")
LINEUPS_PER_FAMILY = 50
SKILL = ("RB", "WR", "TE")


def _rb_dst(rng: np.random.Generator, week: pd.DataFrame, power: float) -> pd.DataFrame:
    """A RB1 and the DST of his own team."""
    rb = bt._pick(rng, week[week["role"] == "RB1"], 1, power)
    dst = week[(week["position"] == "DST") & (week["team"] == rb["team"].iloc[0])]
    if dst.empty:
        raise ValueError("no DST for the RB1's team")
    return pd.concat([rb, dst.iloc[:1]])


def _game(rng: np.random.Generator, week: pd.DataFrame, power: float) -> pd.DataFrame:
    """Four players of one game: a QB, one of his pass catchers and two skill players of the other team."""
    qb = bt._pick(rng, week[week["position"] == "QB"], 1, power)
    team, game = qb["team"].iloc[0], qb["game_id"].iloc[0]
    in_game = week[week["game_id"] == game]
    mate = bt._pick(rng, in_game[(in_game["team"] == team) & in_game["position"].isin(bt.CATCHERS)], 1, power)
    others = in_game[(in_game["team"] != team) & in_game["position"].isin(SKILL)]
    return pd.concat([qb, mate, bt._pick(rng, others, 2, power)])


def generate(week: pd.DataFrame, n_per_family: int, rng: np.random.Generator) -> list[tuple[str, bt.Lineup]]:
    """`n_per_family` legal lineups in each family, as (family, Lineup). A draw that is illegal is redrawn."""
    season, wk = int(week["season"].iloc[0]), int(week["week"].iloc[0])
    seeds = {"random": lambda r, p: week.iloc[0:0], "stack": lambda r, p: bt._stack(r, week, p)}
    seeds["rb_dst"] = lambda r, p: _rb_dst(r, week, p)
    seeds["game"] = lambda r, p: _game(r, week, p)
    out = []
    for family in FAMILIES:
        for _ in range(n_per_family):
            for _try in range(1000):
                power = float(rng.choice(bt.SELECTIVITY))
                try:
                    rows = bt._fill(rng, week, seeds[family](rng, power), power)
                except ValueError:
                    continue
                if bt.is_legal(rows):
                    out.append(
                        (family, bt.Lineup(season, wk, rows.reset_index(drop=True), family == "stack", power))
                    )
                    break
            else:
                raise RuntimeError(f"could not build a {family} lineup for {season} week {wk}")
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
            spread=float(r.spread),
            implied=float(r.implied),
        )
        for r in rows.itertuples()
    ]


def make_lookups(
    frame: pd.DataFrame, *, seed: int = 0, n_boot: int = 200, prior_n: float = script.SHRINK_PRIOR_N
):
    """Per marginal set ('shipped', 'train-only'), the pooled lookup and the conditional ones, all fitted on
    the seasons through `TRAIN_THROUGH`: `{marginals: (tables, {lookup name: lookup})}`."""
    train = frame[frame["season"] <= bt.TRAIN_THROUGH]
    train_tables = pd.concat(
        [dist.build_tables(train.loc[train["position"] == p, ["pred", "dk"]], p) for p in dist.BUCKET_EDGES],
        ignore_index=True,
    )
    named = {(r, a, b) for r, a, b, _ in script.NAMED_PAIRS}
    family = script.family_pairs()
    named = {p for p in named if p in set(family)} | {
        (r, b, a) for r, a, b, _ in script.NAMED_PAIRS if (r, b, a) in set(family)
    }
    out = {}
    for name, tables in (("shipped", None), ("train-only", train_tables)):
        fit = fit_correlations(train, tables=tables, seed=seed, n_boot=n_boot)
        sc = script.scan(fit.scored, n_shuffle=0, n_boot=10, seed=seed)
        base = fit.lookup()
        out[name] = (
            tables,
            {
                "pooled": base,
                "spread": script.ContextLookup(
                    base, sc.buckets, sc.cutoffs, prior_n=prior_n, use=("spread",)
                ),
                "spread_named": script.ContextLookup(
                    base, sc.buckets, sc.cutoffs, prior_n=prior_n, use=("spread",), pairs=named
                ),
                "implied": script.ContextLookup(
                    base, sc.buckets, sc.cutoffs, prior_n=prior_n, use=("implied",)
                ),
            },
        )
    return out


def _week_records(
    week: pd.DataFrame, lookups, *, n_per_family: int, n_sims: int, seed: int, cash_line, gpp_target
):
    season, wk = int(week["season"].iloc[0]), int(week["week"].iloc[0])
    rng = np.random.default_rng([seed + 11, season, wk])
    lineups = generate(week, n_per_family, rng)
    sim_seeds = rng.integers(0, 2**31 - 1, size=len(lineups))
    rows = []
    for (family, lu), sim_seed in zip(lineups, sim_seeds, strict=True):
        rec = {
            "season": lu.season,
            "week": lu.week,
            "family": family,
            "stacked": lu.stacked,
            "realized": float(lu.rows["dk"].sum()),
            "projected": float(lu.rows["pred"].sum()),
        }
        specs = to_specs(lu.rows, f"{lu.season}-{lu.week}")
        for marginals, (tables, by_name) in lookups.items():
            for name, lookup in by_name.items():
                res = simulate_lineups(
                    [specs],
                    n_sims=n_sims,
                    seed=int(sim_seed),
                    cash_line=cash_line,
                    gpp_target=gpp_target,
                    correlations=lookup,
                    tables=tables,
                )
                st = res.lineups[0]
                pre = f"{marginals}.{name}."
                rec[pre + "mean"] = st.mean
                rec[pre + "sd"] = st.sd
                for q, val in st.quantiles.items():
                    rec[pre + f"p{round(q * 100)}"] = val
                rec[pre + "p_cash"] = st.p_cash
                rec[pre + "p_gpp"] = st.p_gpp
        rows.append(rec)
    return rows


def run(
    frame: pd.DataFrame,
    *,
    lookups=None,
    n_per_family: int = LINEUPS_PER_FAMILY,
    n_sims: int = 10000,
    seed: int = 0,
    cash_line: float = DEFAULT_CASH_LINE,
    gpp_target: float = DEFAULT_GPP_TARGET,
    seasons: tuple[int, ...] = bt.TEST_SEASONS,
    n_jobs: int = 1,
    log=lambda msg: None,
) -> pd.DataFrame:
    """One row per lineup with the predicted mean / sd / quantiles / p_cash / p_gpp under every
    `<marginals>.<lookup>` (columns `<marginals>.<lookup>.<stat>`). Deterministic in `seed` whatever
    `n_jobs`."""
    frame = script.with_context(frame)
    lookups = lookups or make_lookups(frame, seed=seed)
    weeks = [
        frame[(frame["season"] == s) & (frame["week"] == w)].reset_index(drop=True)
        for s in seasons
        for w in sorted(frame.loc[frame["season"] == s, "week"].unique())
    ]
    log(f"{len(weeks)} weeks x {len(FAMILIES) * n_per_family} lineups, {n_jobs} worker(s)")
    kwargs = dict(
        n_per_family=n_per_family, n_sims=n_sims, seed=seed, cash_line=cash_line, gpp_target=gpp_target
    )
    if n_jobs == 1:
        done = [_week_records(w, lookups, **kwargs) for w in weeks]
    else:
        from joblib import Parallel, delayed

        done = Parallel(n_jobs=n_jobs)(delayed(_week_records)(w, lookups, **kwargs) for w in weeks)
    return pd.DataFrame([rec for rows in done for rec in rows])


# --- reading the results --------------------------------------------------------------------------------


def comparison(results: pd.DataFrame, marginals: str, name: str, baseline: str = "pooled") -> pd.DataFrame:
    """Paired differences (baseline minus `name`: positive = the conditional table is better) per lineup
    family and measure, with the standard error taken over (season, week) -- lineups in a week share players
    and games. Measures: pinball loss over the quantiles, Brier of P(>= cash line), Brier of P(>= top-end)."""
    rows = []
    for label, mask in [("all", pd.Series(True, index=results.index))] + [
        (f, results["family"] == f) for f in FAMILIES
    ]:
        r = results[mask]
        for measure, fn in (
            ("Pinball loss (p10-p99)", lambda x, mode: bt.pinball(x, marginals, mode)),
            (
                "Brier, P(>= cash line)",
                lambda x, mode: bt.brier(x, marginals, mode, "p_cash", DEFAULT_CASH_LINE),
            ),
            (
                "Brier, P(>= top end)",
                lambda x, mode: bt.brier(x, marginals, mode, "p_gpp", DEFAULT_GPP_TARGET),
            ),
        ):
            base, cond = fn(r, baseline), fn(r, name)
            diff, se = bt.paired_difference(r, base, cond)
            rows.append(
                {
                    "lookup": name,
                    "family": label,
                    "measure": measure,
                    "n": len(r),
                    "baseline": float(base.mean()),
                    "conditional": float(cond.mean()),
                    "improvement": diff,
                    "se": se,
                    "relative": 100 * diff / float(base.mean()),
                }
            )
    return pd.DataFrame(rows)
