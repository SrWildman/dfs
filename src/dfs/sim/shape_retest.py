"""The Week 8 re-test of the five QB1-receiver pairs that did not ship (QB1 with RB1, RB2, WR1, WR2, WR3, by
the receiver's target share), with 2026 added.

The bar is the one QB1-TE1 cleared: fit on 2014-2023 (cut points, pooled correlation, tercile table, all from
those seasons only), judge on 2024-2026 held out. A pair ships only if its held-out log-likelihood gain per
1,000 pairs, over the pooled value, is more than 2 game-clustered standard errors. QB1-TE1 is run beside them
as the control (it must still clear the bar), and every pair gets a shuffled-feature control: the receiver's
`tshare` permuted among the rows of his role and season, the table refitted, the same held-out gain.

The feature is the live one (`shape_table.live_tshare`): what EdgeRaw's `Tgt%` carries, so the table is fitted
on the quantity the simulator is handed. Nothing here writes `models/sim/`; the verdict is a report.

    python -m dfs.sim.shape_retest [--through 2026] [--frame frame.parquet] [--shuffles 200]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from dfs.sim import script, script_shape, shape_table
from dfs.sim.correlation import normal_scores

TRAIN_THROUGH = 2023
BAR_SE = 2.0
PAIRS = ("RB1", "RB2", "WR1", "WR2", "WR3", "TE1")
TESTS = tuple(("same_team", "QB1", b, "b", "tshare") for b in PAIRS)


def prepare(frame: pd.DataFrame, history, seed: int = 0) -> pd.DataFrame:
    """`frame` (`pipeline.load_frame`'s output) with the normal scores `z` and the live `tshare`."""
    f = script.with_context(frame)
    f = f.assign(z=normal_scores(f, seed=seed))
    return shape_table.with_live_tshare(f, history)


def _split(f: pd.DataFrame, train_through: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    return f[f["season"] <= train_through], f[f["season"] > train_through]


def _lookup(train: pd.DataFrame, tests=TESTS) -> script_shape.ShapeLookup:
    pairs = [(t[0], t[1], t[2]) for t in tests]
    base = script.lookup_from_rhos(script.pooled_rhos(train, pairs))
    sc = script_shape.scan(train, tests=tests, n_shuffle=0)
    return script_shape.ShapeLookup(base, sc.buckets, sc.cuts, tests=set(tests))


def shuffled_tshare(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """`frame` with `tshare` permuted among the rows of the same role and season: who a receiver is no longer
    goes with how his game went; the distribution of the feature is kept."""
    out = frame.copy()
    for _, idx in out.groupby(["role", "season"]).groups.items():
        idx = np.asarray(idx)
        out.loc[idx, "tshare"] = out.loc[rng.permutation(idx), "tshare"].to_numpy()
    return out


def run(f: pd.DataFrame, *, train_through: int = TRAIN_THROUGH, n_shuffle: int = 200, seed: int = 0) -> dict:
    """The table: one row per pair with training n, pooled r, tercile r, slope t, and the held-out gain per
    1,000 pairs (SE), overall and by held-out season, plus the share of shuffled-feature tables that do as
    well."""
    train, test = _split(f, train_through)
    lookup = _lookup(train)
    long = script_shape.shape_gain(test, lookup, TESTS)
    rng = np.random.default_rng(seed)
    nulls = {t: [] for t in TESTS}
    for _ in range(n_shuffle):
        lk = _lookup(shuffled_tshare(train, rng))
        g = script_shape.shape_gain(test, lk, TESTS)
        for t in TESTS:
            sel = g[(g["role_b"] == t[2])]
            nulls[t].append(script.summarize_gain(sel)["per_1000"] if len(sel) else float("nan"))
    sc = script_shape.scan(train, tests=TESTS, n_shuffle=0)
    seasons = test["season"].unique()
    game_season = test.drop_duplicates("game_id").set_index("game_id")["season"]
    rows = []
    for t in TESTS:
        b = t[2]
        sel = long[long["role_b"] == b]
        if sel.empty or sc.table[sc.table["role_b"] == b].empty:
            continue
        s = script.summarize_gain(sel)
        meta = sc.table[sc.table["role_b"] == b].iloc[0]
        by_season = {}
        for season in sorted(seasons):
            ss = sel[sel["game_id"].map(game_season) == season]
            by_season[int(season)] = script.summarize_gain(ss)
        null = np.array(nulls[t], dtype=float)
        rows.append(
            {
                "pair": f"QB1-{b}",
                "n_train": int(meta["n"]),
                "r_pooled": float(meta["r_pooled"]),
                "r_low": float(meta["r_low"]),
                "r_mid": float(meta["r_mid"]),
                "r_high": float(meta["r_high"]),
                "t_train": float(meta["t"]),
                "n_test": s["n"],
                "gain_per_1000": s["per_1000"],
                "se": s["se_per_1000"],
                "z": s["per_1000"] / s["se_per_1000"] if s["se_per_1000"] else float("nan"),
                "shuffled_as_good": float((null >= s["per_1000"]).mean()) if len(null) else float("nan"),
                "ships": bool(s["se_per_1000"] and s["per_1000"] > BAR_SE * s["se_per_1000"]),
                "by_season": by_season,
            }
        )
    return {
        "table": pd.DataFrame(rows),
        "train_through": train_through,
        "test_seasons": sorted(int(x) for x in seasons),
    }


def render(result: dict) -> str:
    t = result["table"]
    lines = [
        f"fit 2014-{result['train_through']}, held out {result['test_seasons']}; bar: gain > {BAR_SE:g} SE",
        f"{'pair':9} {'n_tr':>5} {'pooled':>7} {'low':>6} {'mid':>6} {'high':>6} {'t':>5} "
        f"{'n_test':>6} {'gain/1000':>9} {'SE':>5} {'z':>5} {'shuf>=':>6}  ships",
    ]
    for r in t.itertuples():
        lines.append(
            f"{r.pair:9} {r.n_train:5d} {r.r_pooled:+7.3f} {r.r_low:+6.3f} {r.r_mid:+6.3f} {r.r_high:+6.3f} "
            f"{r.t_train:+5.1f} {r.n_test:6d} {r.gain_per_1000:+9.2f} {r.se:5.2f} {r.z:+5.1f} "
            f"{r.shuffled_as_good:6.3f}  {'YES' if r.ships else 'no'}"
        )
    lines.append("by held-out season (gain per 1,000, SE):")
    for r in t.itertuples():
        lines.append(
            f"  {r.pair:9} "
            + "  ".join(
                f"{s}: {d['per_1000']:+.2f} ({d['se_per_1000']:.2f}, n={d['n']})"
                for s, d in r.by_season.items()
            )
        )
    return "\n".join(lines)


def main() -> None:
    from dfs.model.history import load_history
    from dfs.model.predict import available_seasons

    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True, help="parquet of pipeline.load_frame(through)")
    ap.add_argument("--train-through", type=int, default=TRAIN_THROUGH)
    ap.add_argument("--shuffles", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    frame = pd.read_parquet(args.frame)
    history, _ = load_history(available_seasons(int(frame["season"].max())))
    f = prepare(frame, history, args.seed)
    print(render(run(f, train_through=args.train_through, n_shuffle=args.shuffles, seed=args.seed)))


if __name__ == "__main__":
    main()
