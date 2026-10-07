"""Render a `TrainingRun` as markdown: the content of `models/um/backtest.md` and what `dfs model backtest`
prints. Numbers only come from the run; nothing here recomputes a metric."""

from __future__ import annotations

import pandas as pd

from dfs.model import evaluate
from dfs.model.history import POSITIONS
from dfs.model.pipeline import OUT_OF_TIME_SEASONS, TrainingRun


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def band(pair: tuple[float, float]) -> str:
    lo, hi = pair
    return _pct(lo) if abs(hi - lo) < 0.0005 else f"{_pct(lo)}-{_pct(hi)}"


def data_section(report: dict) -> str:
    if not report:
        return ""
    rows = [
        ["offense player-games", f"{report['player_games']:,}"],
        ["ffopportunity xFP join, all player-games", _pct(report["ep_join_rate"])],
        [
            "ffopportunity xFP join, player-games with any target/carry/attempt",
            _pct(report["ep_join_rate_with_touches"]),
        ],
        [
            "unmatched rows (xFP set to 0): with touches / mean DK points",
            f"{report['no_ep_rows_with_touches']} / {report['no_ep_mean_dk']:.2f}",
        ],
        ["Vegas context (implied total, spread) joined", _pct(report["games_join_rate"])],
        [
            "DK identity breaks, raw (DK - PPR vs bonuses + INT + fumbles lost)",
            str(report["identity_breaks_raw"]),
        ],
        ["DK identity breaks after the fumble-recovery-TD adjustment", str(report["identity_breaks"])],
        ["team-games (DST)", f"{report['team_games']:,}"],
        ["DST Vegas context joined", _pct(report["team_games_context_rate"])],
    ]
    return "## 1. Data\n\n" + _table(["Check", "Value"], rows)


def holdout_section(run: TrainingRun) -> str:
    rows = []
    for position in POSITIONS:
        r = run.results[position]
        m = r.metrics
        rows.append(
            [
                position,
                f"{r.n_train:,} / {r.n_holdout:,}",
                f"{m['mae_l8']:.3f}",
                f"{m['mae_blend']:.3f}",
                f"{m['mae_gbm']:.3f}",
                f"{m['rho_l8']:.3f}",
                f"{m['rho_blend']:.3f}",
                f"{m['rho_gbm']:.3f}",
                f"**{r.method}**",
            ]
        )
    note = (
        "MAE in DK points; rho is the mean Spearman rank correlation within position-week. DST has no blend: "
        "its baseline is trailing-8 DST points, so the two baseline columns are identical and that baseline "
        "plays the blend's role in the shipping rule. The GBM ships only if MAE_gbm <= MAE_blend AND "
        "rho_gbm >= rho_blend."
    )
    return (
        f"## 2. Holdout {run.holdout[0]}-{run.holdout[-1]} "
        f"(fit on {run.first_season}-{run.holdout[0] - 1})\n\n"
        + _table(
            [
                "Pos",
                "Train / holdout rows",
                "MAE L8",
                "MAE blend",
                "MAE GBM",
                "rho L8",
                "rho blend",
                "rho GBM",
                "Shipped",
            ],
            rows,
        )
        + "\n\n"
        + note
    )


def coverage_section(run: TrainingRun) -> str:
    rows = []
    for position in POSITIONS:
        c = run.coverage[position]
        rows.append(
            [
                position,
                f"{c['n']:,}",
                band(c["below_q20"]),
                band(c["above_q85"]),
                "yes" if c["ok"] else "**NO**",
            ]
        )
    return (
        "## 3. Distribution calibration (out-of-fold, tables built from the same games)\n\n"
        "### Coverage (target: below q20 = 20%, above q85 = 15%; accept within +/-3 points)\n\n"
        + _table(["Pos", "N", "Realized below q20", "Realized above q85", "Within +/-3"], rows)
        + "\n\nA range means the quantile sits on an atom (e.g. a cheap WR's q20 is exactly 0 points): the "
        "strict and inclusive shares bracket the target."
    )


def _reliability_block(position: str, rel: pd.DataFrame) -> str:
    out = []
    for label, g in rel.groupby("threshold", sort=False):
        rows = []
        for r in g.itertuples():
            flag = "*" if abs(r.gap_pts) > evaluate.RELIABILITY_TOL * 100 else ""
            rows.append(
                [
                    str(int(r.bin)),
                    f"{r.n:,}",
                    _pct(r.mean_p),
                    _pct(r.realized),
                    f"{r.gap_pts:+.1f}{flag}",
                    f"{r.se_pts:.1f}",
                ]
            )
        out.append(
            f"**{position}, P(actual >= {g['T'].iloc[0]:.1f} pts)** ({label} of actual points)\n\n"
            + _table(["Bin", "N", "Predicted", "Realized", "Gap (pts)", "SE (pts)"], rows)
        )
    return "\n\n".join(out)


def reliability_section(run: TrainingRun) -> str:
    summary = []
    for position in POSITIONS:
        s = evaluate.summarize(position, run.coverage[position], run.reliability[position])
        summary.append(
            [
                position,
                f"{s['max_abs_gap_pts']:.1f}",
                f"{s['bins_over_tol']}/{s['bins']}",
                str(s["bins_over_2se"]),
                "yes" if s["reliability_ok"] else "**NO**",
            ]
        )
    head = (
        "### Reliability of P(actual >= T), T at the 25th/50th/75th percentile of actual points\n\n"
        "Ten equal-count bins of predicted probability per threshold. `*` marks a gap beyond +/-4 points. "
        "A bin's SE is the binomial standard error of its realized rate.\n\n"
        + _table(
            ["Pos", "Max abs gap", "Bins beyond +/-4 (of 30)", "Bins beyond 2 SE", "All within +/-4"], summary
        )
    )
    blocks = "\n\n".join(_reliability_block(p, run.reliability[p]) for p in POSITIONS)
    return head + "\n\n" + blocks


def out_of_time_section(run: TrainingRun) -> str:
    rows = []
    for position in POSITIONS:
        o = run.out_of_time[position]
        s = evaluate.summarize(position, o["coverage"], o["reliability"])
        rows.append(
            [
                position,
                f"{o['coverage']['n']:,}",
                band(o["coverage"]["below_q20"]),
                band(o["coverage"]["above_q85"]),
                f"{s['max_abs_gap_pts']:.1f}",
                f"{s['bins_over_tol']}/{s['bins']}",
                str(s["bins_over_2se"]),
            ]
        )
    return (
        f"### Supplementary: tables built on {run.first_season}-{run.through - OUT_OF_TIME_SEASONS}, "
        f"scored on {run.through - OUT_OF_TIME_SEASONS + 1}-{run.through}\n\n"
        "The check above measures the tables on the games that built them. This one measures them on "
        "seasons they never saw (fewer games per bin, so more sampling noise).\n\n"
        + _table(
            [
                "Pos",
                "N",
                "Below q20",
                "Above q85",
                "Max abs gap (pts)",
                "Bins beyond +/-4 (of 30)",
                "Bins beyond 2 SE",
            ],
            rows,
        )
    )


def table_section(run: TrainingRun) -> str:
    rows = []
    for _, r in run.tables.iterrows():
        rows.append(
            [
                r["position"],
                r["bucket"],
                f"{int(r['n']):,}",
                f"{r['center']:.2f}",
                _pct(r["p_zero"]),
                f"{r['q0.2']:.2f}",
                f"{r['q0.5']:.2f}",
                f"{r['q0.85']:.2f}",
                f"{r['q0.95']:.2f}",
            ]
        )
    return (
        "## 4. Outcome-ratio tables (actual / predicted)\n\n"
        + _table(["Pos", "Bucket", "N", "Centre", "P(0)", "q20", "q50", "q85", "q95"], rows)
        + "\n\nThe full set of quantile levels is in `distribution.csv`."
    )


def render_backtest(run: TrainingRun) -> str:
    parts = [
        f"# UM backtest -- trained through {run.through}\n",
        "Generated by `dfs model train`. Public nflverse and ffopportunity history only.",
        data_section(run.join_report),
        holdout_section(run),
        coverage_section(run),
        reliability_section(run),
        out_of_time_section(run),
        table_section(run),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"
