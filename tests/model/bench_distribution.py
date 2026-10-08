"""Test bench for the distribution engine's top (and bottom) end -- NOT a pytest module (no `test_` prefix, it
needs the cached nflverse history): `python tests/model/bench_distribution.py --help`.

It scores candidate table builders on the out-of-fold 2014-2025 rows the shipped tables are built on:

  (a) actual / engine-mean ratio per projection band (acceptance: every band with n >= 50 within 0.95-1.05);
  (b) q20 / q85 coverage per position (within +/-3 points);
  (c) reliability of P(actual >= T) at the p25 / p50 / p75 of actual points (every decile within +/-4);
  (d) pinball loss over the stored levels, in points;
  (e) `--sim`: the real `dfs sim backtest` (reliability deciles for the cash line and the GPP target).

Three ways to read the same rows: `insample` (tables built on 2014-2025, scored on the same rows: the
acceptance rule's reading), `oot` (built on 2014-2022, scored on 2023-2025: the era-drift reading) and `cv`
(four season blocks of three; every row scored by tables that never saw its block: whether an in-sample pass
is overfit). A candidate is `build(oof_for_one_position, position) -> table` in the shipped CSV's shape, so it
runs through the engine's unchanged `ratio_matrix` / `prob_at_least_many` and through the simulator.

`--rebuild` refreshes the cached out-of-fold frame (data/model_cache/bench_oof.parquet) after a retrain.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

import numpy as np
import pandas as pd

from dfs.model import data
from dfs.model import distribution as D
from dfs.model import evaluate as E
from dfs.model.artifacts import read_metadata
from dfs.model.history import POSITIONS, load_history
from dfs.model.predict import available_seasons
from dfs.model.train import build_dataset, oof_predictions

INF = np.inf
POS = list(POSITIONS)
# The band grid, fixed before any candidate was scored: the PR #3 table's own bands (QB <14, 25-28, 28+; RB
# 22-26, 26+; WR 22+; TE 14-17, 17+; DST 11-13, 13+) extended downward so every projection is in one band.
BANDS = {
    "QB": [(-INF, 14), (14, 18), (18, 22), (22, 25), (25, 28), (28, INF)],
    "RB": [(-INF, 6), (6, 10), (10, 14), (14, 18), (18, 22), (22, 26), (26, INF)],
    "WR": [(-INF, 6), (6, 10), (10, 14), (14, 18), (18, 22), (22, INF)],
    "TE": [(-INF, 5), (5, 8), (8, 11), (11, 14), (14, 17), (17, INF)],
    "DST": [(-INF, 5), (5, 7), (7, 9), (9, 11), (11, 13), (13, INF)],
}
MIN_BAND_N = 50
BAND_TOL = 0.05
OOT_SPLIT = 2022
FOLDS = [(2014, 2016), (2017, 2019), (2020, 2022), (2023, 2025)]
TAIL_MULTS = (1.0, 1.25, 1.5)
MIN_BUCKET_ROWS = 300  # the finer-bucket remedy: a new bucket needs this many out-of-fold rows
CACHE = data.CACHE_DIR / "bench_oof.parquet"

Builder = Callable[[pd.DataFrame, str], pd.DataFrame]


# --- the out-of-fold rows ---------------------------------------------------------------------------------


def load_oof(rebuild: bool = False) -> pd.DataFrame:
    """`position, season, week, gsis_id, pred, dk` for every in-population player-game (and team-game) of the
    seasons the shipped model was trained on, predicted out of fold by the shipped method."""
    if CACHE.exists() and not rebuild:
        return pd.read_parquet(CACHE)
    meta = read_metadata()
    seasons = available_seasons(int(meta["train_seasons"][1]))
    history, _ = load_history(seasons)
    parts = []
    for position in POS:
        method = meta["positions"][position]["method"]
        part = oof_predictions(build_dataset(history, position), position, method, seasons)
        parts.append(part.assign(position=position))
    oof = pd.concat(parts, ignore_index=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    oof.to_parquet(CACHE)
    return oof


# --- candidate builders -----------------------------------------------------------------------------------


def bucket_table(oof: pd.DataFrame, position: str, edges: tuple[float, ...]) -> pd.DataFrame:
    """The "buckets-v1" algorithm (`D.build_bucket_tables`) with arbitrary inner edges."""
    rows = oof[oof["pred"] > 0]
    ratio = rows["dk"] / rows["pred"]
    idx = np.digitize(rows["pred"].to_numpy(), edges)
    out = []
    for i in range(len(edges) + 1):
        sel = idx == i
        if not sel.any():
            raise D.DistributionError(f"no rows in {position} bucket {i}")
        rec = {
            "position": position,
            "bucket": f"<{edges[0]:g}"
            if i == 0
            else f"{edges[-1]:g}+"
            if i == len(edges)
            else f"{edges[i - 1]:g}-{edges[i]:g}",
            "lo": -np.inf if i == 0 else edges[i - 1],
            "hi": np.inf if i == len(edges) else edges[i],
            "n": int(sel.sum()),
            "center": float(rows.loc[sel, "pred"].mean()),
            "p_zero": float((rows.loc[sel, "dk"] == 0).mean()),
        }
        rec.update(zip(D.LEVEL_COLUMNS, np.quantile(ratio[sel], D.LEVELS), strict=True))
        out.append(rec)
    return pd.DataFrame(out)


def split_edges(preds: np.ndarray, edges: tuple[float, ...], *, bottom: bool) -> tuple[float, ...]:
    """Add whole-number edges above the top edge (and below the bottom one) wherever BOTH new buckets keep
    at least MIN_BUCKET_ROWS rows; otherwise the bucket stays merged."""
    edges = list(edges)
    while True:  # top
        e = edges[-1]
        cut = next(
            (
                c
                for c in np.arange(e + 1, preds.max())
                if ((preds >= e) & (preds < c)).sum() >= MIN_BUCKET_ROWS
                and (preds >= c).sum() >= MIN_BUCKET_ROWS
            ),
            None,
        )
        if cut is None:
            break
        edges.append(float(cut))
    while bottom:
        e = edges[0]
        cut = next(
            (
                c
                for c in np.arange(e - 1, preds.min(), -1)
                if ((preds >= c) & (preds < e)).sum() >= MIN_BUCKET_ROWS
                and (preds < c).sum() >= MIN_BUCKET_ROWS
            ),
            None,
        )
        if cut is None:
            break
        edges.insert(0, float(cut))
    return tuple(edges)


def fine_buckets(bottom_for: tuple[str, ...]) -> Builder:
    """Remedy 1: split each position's top bucket (and the bottom bucket of `bottom_for`) where >= 300 rows
    support the new bucket."""

    def build(oof: pd.DataFrame, position: str) -> pd.DataFrame:
        preds = oof.loc[oof["pred"] > 0, "pred"].to_numpy()
        edges = split_edges(preds, D.BUCKET_EDGES[position], bottom=position in bottom_for)
        return bucket_table(oof, position, edges)

    return build


def smooth(knot_quantiles: tuple[float, ...], *, basis: str = "log") -> Builder:
    """Remedy 2: quantile regression of the ratio on log(projection), a linear spline with knots at
    `knot_quantiles` of the projections (none = one straight line). `D.build_tables` is the 5-knot version."""

    def build(oof: pd.DataFrame, position: str) -> pd.DataFrame:
        base = D.build_bucket_tables(oof, position)
        rows = oof[oof["pred"] > 0]
        pred = rows["pred"].to_numpy()
        ratio = (rows["dk"] / rows["pred"]).to_numpy()
        inner = np.quantile(np.log(pred), knot_quantiles)
        X = D._spline_basis(np.log(pred), inner)
        centers = np.geomspace(*np.percentile(pred, D.EDGE_PERCENTILES), D.N_KNOTS)
        grid = D._spline_basis(np.log(centers), inner)
        ratios = np.column_stack([grid @ D._quantile_fit(X, ratio, tau) for tau in D.LEVELS])
        floor = np.minimum(base[D.LEVEL_COLUMNS].min().to_numpy(dtype=float), 0.0)
        out = pd.DataFrame(np.sort(np.maximum(ratios, floor), axis=1), columns=D.LEVEL_COLUMNS)
        out.insert(0, "p_zero", np.interp(centers, base["center"], base["p_zero"]))
        out.insert(0, "center", centers)
        out.insert(0, "n", len(rows))
        out.insert(0, "hi", np.inf)
        out.insert(0, "lo", -np.inf)
        out.insert(0, "bucket", [f"{c:.2f}" for c in centers])
        out.insert(0, "position", position)
        return out

    return build


def together(n_extra: int = 8) -> Builder:
    """Both: fine buckets for the interior; beyond the outermost bucket centres the spline's shape,
    re-anchored to the bucket value at that centre, out to the data edge (flat after)."""
    fine, spline = fine_buckets(tuple(POS)), smooth((0.2, 0.4, 0.6, 0.8, 0.95))
    cols = D.LEVEL_COLUMNS

    def build(oof: pd.DataFrame, position: str) -> pd.DataFrame:
        b = fine(oof, position).sort_values("center").reset_index(drop=True)
        s = spline(oof, position).sort_values("center").reset_index(drop=True)

        def at(c: float) -> np.ndarray:
            return np.array([np.interp(c, s["center"], s[col]) for col in cols])

        ends = []  # (bucket row at that end, the projections to add beyond it)
        if s["center"].iloc[-1] > b["center"].iloc[-1]:
            ends.append(
                (b.iloc[-1], np.linspace(b["center"].iloc[-1], s["center"].iloc[-1], n_extra + 1)[1:])
            )
        if s["center"].iloc[0] < b["center"].iloc[0]:
            ends.append((b.iloc[0], np.linspace(s["center"].iloc[0], b["center"].iloc[0], n_extra + 1)[:-1]))
        rows = []
        for end, centres in ends:
            shift = end[cols].to_numpy(dtype=float) - at(end["center"])
            for c in centres:
                rec = {"position": position, "bucket": "ext", "lo": -np.inf, "hi": np.inf, "n": 0}
                rec |= {"center": float(c), "p_zero": float(end["p_zero"])}
                rec |= dict(zip(cols, np.sort(at(c) + shift), strict=True))
                rows.append(rec)
        return pd.concat([b, pd.DataFrame(rows)], ignore_index=True).sort_values("center", ignore_index=True)

    return build


CANDIDATES: dict[str, Builder] = {
    "current (buckets-v1)": lambda oof, p: D.build_bucket_tables(oof, p),
    "fine buckets (top + QB bottom)": fine_buckets(("QB",)),
    "fine buckets (top + all bottoms)": fine_buckets(tuple(POS)),
    "smooth: one line in log(p)": smooth(()),
    "smooth: 3-knot spline": smooth((0.25, 0.5, 0.75)),
    "smooth: 5-knot spline (shipped)": D.build_tables,
    "together: fine buckets + spline tails": together(),
}


# --- the metrics ------------------------------------------------------------------------------------------


def positive(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["pred"] > 0].reset_index(drop=True)


def engine_means(position: str, preds: np.ndarray, tables: pd.DataFrame, n_grid: int = 800) -> np.ndarray:
    """Mean of each projection's distribution as the SIMULATOR sees it (the zero atom explicit)."""
    from dfs.sim import marginal as M

    m = M.marginals(position, preds, tables=tables)
    u = (np.arange(n_grid) + 0.5) / n_grid
    return np.array([M.ppf(m, i, u).mean() for i in range(len(m))])


def band_label(lo: float, hi: float) -> str:
    return f"<{hi:g}" if lo == -INF else f"{lo:g}+" if hi == INF else f"{lo:g}-{hi:g}"


def band_table(rows: pd.DataFrame) -> pd.DataFrame:
    """`rows`: position, pred, dk, engine. Per band: n, actual / engine and its standard error."""
    out = []
    for pos in POS:
        t = rows[rows["position"] == pos]
        for lo, hi in BANDS[pos]:
            b = t[(t["pred"] >= lo) & (t["pred"] < hi)]
            if len(b) > 1:
                ratio = b["dk"].mean() / b["engine"].mean()
                se = b["dk"].std(ddof=1) / np.sqrt(len(b)) / b["engine"].mean()
                out.append(dict(position=pos, band=band_label(lo, hi), n=len(b), ratio=ratio, se=se))
    df = pd.DataFrame(out)
    df["fail"] = (df["n"] >= MIN_BAND_N) & ((df["ratio"] - 1).abs() > BAND_TOL)
    return df


def thresholds(oof: pd.DataFrame) -> dict[tuple[str, int], float]:
    """The reliability thresholds: the p25 / p50 / p75 of ALL actual points of each position."""
    return {
        (pos, pct): float(np.percentile(oof.loc[oof["position"] == pos, "dk"], pct))
        for pos in POS
        for pct in E.RELIABILITY_PCTS
    }


def scored_rows(tables: pd.DataFrame, test: pd.DataFrame, thr: dict[tuple[str, int], float]) -> pd.DataFrame:
    """Per row: engine mean, q20 / q85 in points, mean pinball, and predicted / realized for the reliability
    thresholds and the top-end tail check."""
    out = []
    lv = np.asarray(D.LEVELS)
    for pos in POS:
        t = positive(test[test["position"] == pos])
        pred, dk = t["pred"].to_numpy(), t["dk"].to_numpy()
        q = D.ratio_matrix(pos, pred, tables=tables) * pred[:, None]
        err = dk[:, None] - q
        rec = pd.DataFrame(
            dict(
                position=pos,
                season=t["season"].to_numpy(),
                pred=pred,
                dk=dk,
                engine=engine_means(pos, pred, tables),
                q20=q[:, D.LEVELS.index(D.FLOOR_LEVEL)],
                q85=q[:, D.LEVELS.index(D.CEILING_LEVEL)],
                pin=np.maximum(lv * err, (lv - 1) * err).mean(axis=1),
            )
        )
        for pct in E.RELIABILITY_PCTS:
            rec[f"p{pct}"] = D.prob_at_least_many(pos, pred, thr[pos, pct], tables=tables)
            rec[f"h{pct}"] = (dk >= thr[pos, pct]).astype(float)
        for m in TAIL_MULTS:
            rec[f"tp{m}"] = D.prob_at_least_many(pos, pred, m * pred, tables=tables)
            rec[f"th{m}"] = (dk >= m * pred).astype(float)
        out.append(rec)
    return pd.concat(out, ignore_index=True)


def summarize(rows: pd.DataFrame) -> dict:
    bands = band_table(rows)
    cov, rel, tail = [], [], []
    for pos in POS:
        t = rows[rows["position"] == pos]
        below = (float((t["dk"] < t["q20"]).mean()), float((t["dk"] <= t["q20"]).mean()))
        above = (float((t["dk"] > t["q85"]).mean()), float((t["dk"] >= t["q85"]).mean()))
        gap = max(
            max(
                0.0, below[0] - 0.20, 0.20 - below[1]
            ),  # distance from the target to the strict/inclusive interval
            max(0.0, above[0] - 0.15, 0.15 - above[1]),
        )
        cov.append(dict(position=pos, gap=100 * gap))
        gaps = []
        for pct in E.RELIABILITY_PCTS:
            bins = pd.qcut(t[f"p{pct}"].rank(method="first"), E.N_BINS, labels=False)
            g = t.groupby(bins.to_numpy()).agg(p=(f"p{pct}", "mean"), h=(f"h{pct}", "mean"))
            gaps += list(100 * (g["h"] - g["p"]))
        gaps = np.abs(gaps)
        rel.append(
            dict(position=pos, max_gap=gaps.max(), beyond4=int((gaps > 100 * E.RELIABILITY_TOL).sum()))
        )
        top = t[t["pred"] >= t["pred"].quantile(0.95)]
        for m in TAIL_MULTS:
            tail.append(100 * (top[f"th{m}"].mean() - top[f"tp{m}"].mean()))
    n = rows.groupby("position").size()
    pin = rows.groupby("position")["pin"].mean()
    fails = bands[bands["fail"]]
    return dict(
        bands=bands,
        band_fails=len(fails),
        fail_text="; ".join(f"{r.position} {r.band} {r.ratio:.3f}" for r in fails.itertuples()) or "none",
        coverage=pd.DataFrame(cov),
        reliability=pd.DataFrame(rel),
        pinball=pin,
        pinball_all=float((pin * n).sum() / n.sum()),
        tail_mean_abs=float(np.abs(tail).mean()),
    )


def evaluate(build: Builder, oof: pd.DataFrame, mode: str) -> dict:
    thr = thresholds(oof)

    def tables_from(train: pd.DataFrame) -> pd.DataFrame:
        return pd.concat([build(train[train["position"] == p], p) for p in POS], ignore_index=True)

    if mode == "insample":
        rows = scored_rows(tables_from(oof), oof, thr)
    elif mode == "oot":
        early = oof["season"] <= OOT_SPLIT
        rows = scored_rows(tables_from(oof[early]), oof[~early], thr)
    else:  # cv
        parts = []
        for lo, hi in FOLDS:
            held = oof["season"].between(lo, hi)
            parts.append(scored_rows(tables_from(oof[~held]), oof[held], thr))
        rows = pd.concat(parts, ignore_index=True)
    return summarize(rows)


def line(name: str, s: dict) -> dict:
    return {
        "candidate": name,
        "band fails (n>=50)": s["band_fails"],
        "max cov gap": round(float(s["coverage"]["gap"].max()), 1),
        "rel bins >4 (of 150)": int(s["reliability"]["beyond4"].sum()),
        "rel max gap": round(float(s["reliability"]["max_gap"].max()), 1),
        "pinball": round(s["pinball_all"], 4),
        "top-5% tail err": round(s["tail_mean_abs"], 2),
    }


# --- (e) the simulator back-test ---------------------------------------------------------------------------


def sim_backtest(name: str, build: Builder, oof: pd.DataFrame, jobs: int) -> pd.DataFrame:
    """The real `run_backtest` with the candidate's tables as both marginal sets: `shipped` (built on all the
    out-of-fold rows) and `train-only` (built on the role frame's 2014-2022 rows), correlations refitted with
    the candidate's own tables -- exactly what `make_variants` does for the committed tables."""
    from dfs.sim import backtest as BT
    from dfs.sim.correlation import fit_correlations
    from dfs.sim.pipeline import load_frame

    frame = load_frame()
    train = frame[frame["season"] <= BT.TRAIN_THROUGH]
    shipped = pd.concat([build(oof[oof["position"] == p], p) for p in POS], ignore_index=True)
    early = pd.concat(
        [build(train.loc[train["position"] == p, ["pred", "dk"]], p) for p in POS], ignore_index=True
    )
    variants = [
        BT.Variant("shipped", shipped, fit_correlations(train, tables=shipped, seed=0, n_boot=200).lookup()),
        BT.Variant("train-only", early, fit_correlations(train, tables=early, seed=0, n_boot=200).lookup()),
    ]
    return BT.run_backtest(frame, variants=variants, n_jobs=jobs)


def sim_line(name: str, res: pd.DataFrame) -> list[dict]:
    from dfs.sim import backtest as BT
    from dfs.sim import report as R

    out = []
    for variant in R.VARIANTS:
        for group in ("all", "stacked", "random"):
            mask = R._mask(res, group)
            cov = BT.coverage(res, variant, "corr", mask)
            cash = BT.reliability(res, variant, "corr", "p_cash", BT.DEFAULT_CASH_LINE, mask)
            gpp = BT.reliability(res, variant, "corr", "p_gpp", BT.DEFAULT_GPP_TARGET, mask)
            out.append(
                {
                    "candidate": name,
                    "marginals": variant,
                    "lineups": group,
                    "max cov gap": round(float(cov["gap"].abs().max()), 1),
                    "max cash-bin gap": round(float(cash["gap"].abs().max()), 1),
                    "max GPP-bin gap": round(float(gpp["gap"].abs().max()), 1),
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="candidate names (substring match); default: all")
    ap.add_argument("--modes", default="insample,oot,cv", help="comma list of insample, oot, cv")
    ap.add_argument(
        "--sim", action="store_true", help="also run the real sim back-test per candidate (~7 min each)"
    )
    ap.add_argument("--jobs", type=int, default=4, help="worker processes for --sim")
    ap.add_argument("--rebuild", action="store_true", help="refresh the cached out-of-fold frame")
    args = ap.parse_args(argv)
    oof = load_oof(args.rebuild)
    chosen = {k: v for k, v in CANDIDATES.items() if not args.names or any(n in k for n in args.names)}
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    for mode in args.modes.split(","):
        results = {name: evaluate(build, oof, mode) for name, build in chosen.items()}
        print(f"\n=== {mode} ===")
        print(pd.DataFrame([line(n, s) for n, s in results.items()]).to_string(index=False))
        for name, s in results.items():
            print(f"  band failures [{name}]: {s['fail_text']}")
    if args.sim:
        rows = []
        for name, build in chosen.items():
            rows += sim_line(name, sim_backtest(name, build, oof, args.jobs))
        print("\n=== dfs sim backtest (correlated) ===")
        print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
