"""What a role-aware tail adjustment does to a lineup's chance of a big score, stacked against unstacked.

`dfs.tail_check` found the engine's tables too generous to depth players' upside (RB2, RB3, WR4, TE2 boom less
often than their projection's table says). This puts that correction into the simulator (`tail_delta`, default
off) and replays the Week 8 back-test (`backtest.py`'s random legal lineups, half stacked) on held-out
seasons:
tables and correlations fitted on 2014-2022 only, the role deltas fitted on 2014-2022 only
(`tail_check.fit_adjustment`), lineups from 2023-2026. For each score line it compares the simulator's
P(lineup >= line), with and without the adjustment, to the share of lineups that really got there, split into
stacked and unstacked, with a week-clustered paired Brier difference.

    python -m dfs.sim.tail_backtest --frame frame.parquet [--lineups 100] [--jobs 4]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from dfs import tail_check
from dfs.sim import backtest
from dfs.sim.correlation import fit_correlations
from dfs.sim.simulate import simulate_lineups

LINES = (150.0, 170.0, 190.0, 210.0)
DEPTH_ROLES = ("RB2", "RB3", "WR4", "TE2")  # the roles whose tops the engine over-states (tail_check)
VARIANTS = ("p", "q", "d")  # as shipped, every role's delta, the depth roles' deltas only
SEASONS = (2023, 2024, 2025, 2026)


def run(
    frame: pd.DataFrame, *, lineups_per_week: int = 100, n_sims: int = 5000, seed: int = 0, jobs: int = 1
) -> pd.DataFrame:
    """One row per lineup: season, week, stacked, realized, and `p<line>` / `q<line>` / `d<line>` (the
    simulator's P(score >= line) as shipped, with every role's delta, with the depth roles' only)."""
    train = frame[frame["season"] <= tail_check.TRAIN_THROUGH]
    tables = tail_check.train_tables(frame)
    lookup = fit_correlations(train, tables=tables, seed=seed, n_boot=20).lookup()
    delta = {k: d for k, (d, _) in tail_check.fit_adjustment(frame, tables, group="role").items()}
    deltas = {"p": None, "q": delta, "d": {k: v for k, v in delta.items() if k in DEPTH_ROLES}}
    weeks = [
        frame[(frame["season"] == s) & (frame["week"] == w)].reset_index(drop=True)
        for s in SEASONS
        for w in sorted(frame.loc[frame["season"] == s, "week"].unique())
    ]

    def one(week: pd.DataFrame) -> list[dict]:
        season, wk = int(week["season"].iloc[0]), int(week["week"].iloc[0])
        rng = np.random.default_rng([seed + 11, season, wk])
        lineups = backtest.generate_lineups(week, lineups_per_week, rng)
        out = []
        for lu, sim_seed in zip(lineups, rng.integers(0, 2**31 - 1, size=len(lineups)), strict=True):
            specs = backtest.to_specs(lu.rows, f"{season}-{wk}")
            rec = {
                "season": season,
                "week": wk,
                "stacked": lu.stacked,
                "realized": float(lu.rows["dk"].sum()),
                "projected": float(lu.rows["pred"].sum()),
            }
            for tag, td in deltas.items():
                res = simulate_lineups(
                    [specs],
                    n_sims=n_sims,
                    seed=int(sim_seed),
                    correlations=lookup,
                    tables=tables,
                    tail_delta=td,
                    keep_scores=True,
                )
                for line in LINES:
                    rec[f"{tag}{line:g}"] = float((res.scores[:, 0] >= line).mean())
                rec[f"{tag}mean"] = res.lineups[0].mean
            out.append(rec)
        return out

    if jobs == 1:
        done = [one(w) for w in weeks]
    else:
        from joblib import Parallel, delayed

        done = Parallel(n_jobs=jobs)(delayed(one)(w) for w in weeks)
    out = pd.DataFrame([r for rows in done for r in rows])
    out.attrs["delta"] = delta
    return out


def summarize(res: pd.DataFrame) -> pd.DataFrame:
    """Per line and stacked/unstacked: predicted (as shipped, every-role delta, depth-roles delta) and
    realized rates in percent, and each adjustment's paired Brier gain over the shipped one (positive =
    better)
    with a week-clustered SE."""
    rows = []
    week = res["season"].astype(str) + "-" + res["week"].astype(str)
    for line in LINES:
        hit = (res["realized"] >= line).astype(float)
        for name, sel in (
            ("stacked", res["stacked"]),
            ("unstacked", ~res["stacked"]),
            ("all", res["stacked"] | ~res["stacked"]),
        ):
            s, h = res[sel], hit[sel]
            row = {"line": line, "lineups": name, "n": len(s), "realized%": 100 * h.mean()}
            for tag, label in (("p", "shipped"), ("q", "all roles"), ("d", "depth roles")):
                row[f"{label}%"] = 100 * s[f"{tag}{line:g}"].mean()
            for tag, label in (("q", "all roles"), ("d", "depth roles")):
                d = (s[f"p{line:g}"] - h) ** 2 - (s[f"{tag}{line:g}"] - h) ** 2
                per = d.groupby(week[sel]).sum()
                row[f"{label} Brier gain x1e4"] = 1e4 * float(d.mean())
                row[f"{label} se x1e4"] = 1e4 * float(np.sqrt((per**2).sum()) / len(d))
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--lineups", type=int, default=100)
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--save", help="parquet for the per-lineup results")
    args = ap.parse_args()
    frame = pd.read_parquet(args.frame)
    res = run(frame, lineups_per_week=args.lineups, n_sims=args.sims, jobs=args.jobs)
    if args.save:
        res.to_parquet(args.save)
    pd.set_option("display.width", 200)
    print("role deltas (fitted 2014-2022):", {k: round(v, 3) for k, v in res.attrs["delta"].items()})
    print(summarize(res).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
