"""Text for the `dfs sim` commands and docs/SIM.md: plain markdown, built from the fit and the back-test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.sim import backtest as bt
from dfs.sim.correlation import (
    CROSS_GAME_LIMIT,
    POOLED,
    TOTAL_BUCKETS,
    Fit,
    cross_game_check,
    literature_comparison,
)
from dfs.sim.simulate import PlayerSpec, SimResult, simulate_lineups, swap_impact


def md_table(df: pd.DataFrame, formats: dict[str, str] | None = None) -> str:
    """A GitHub-flavoured markdown table; `formats` maps a column to a format spec (default: str)."""
    formats = formats or {}
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "|".join("---" for _ in df.columns) + "|"]
    for row in df.itertuples(index=False):
        cells = []
        for col, value in zip(df.columns, row, strict=True):
            spec = formats.get(col)
            cells.append(format(value, spec) if spec and not pd.isna(value) else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def signed(x: float, digits: int = 2) -> str:
    return f"{x:+.{digits}f}"


# --- fit --------------------------------------------------------------------------------------------------


def fit_checks(fit: Fit, seed: int) -> dict:
    """The numbers `dfs sim fit` records beside the table (`correlations_meta.json`)."""
    cg = cross_game_check(fit.scored, seed=seed)
    flagged = fit.detail[fit.detail["conditional"]].drop_duplicates(["relation", "role_a", "role_b"])
    return {
        "cross_game": {
            "mean_abs_r": float(cg["r"].abs().mean()),
            "mean_r": float(cg["r"].mean()),
            "noise_floor": float(cg["noise_floor"].mean()),
            "max_abs_r": float(cg["r"].abs().max()),
            "role_pairs": int(len(cg)),
            "limit": CROSS_GAME_LIMIT,
        },
        "conditional_pairs": int(len(flagged)),
        "pairs": int(fit.detail.drop_duplicates(["relation", "role_a", "role_b"]).shape[0]),
    }


def literature_table(fit: Fit) -> str:
    lit = literature_comparison(fit)
    out = pd.DataFrame(
        {
            "Pair": [
                ("opp. " if r == "opp" else "") + p for r, p in zip(lit["relation"], lit["pair"], strict=True)
            ],
            "Cited": lit["cited"],
            "Ours (z, shrunk)": [
                f"{signed(r)} [{signed(lo)}, {signed(hi)}]"
                for r, lo, hi in zip(lit["rho"], lit["ci_lo"], lit["ci_hi"], strict=True)
            ],
            "n": [f"{n:,}" for n in lit["n"]],
            "Raw DK points r": [signed(x) for x in lit["r_points"]],
        }
    )
    return md_table(out)


def pooled_table(fit: Fit, relation: str) -> str:
    d = fit.detail
    d = d[(d["relation"] == relation) & (d["total_bucket"] == POOLED)]
    d = d.assign(Pair=d["role_a"] + " - " + d["role_b"]).sort_values("rho", ascending=False)
    out = pd.DataFrame(
        {
            "Pair": d["Pair"],
            "n": [f"{n:,}" for n in d["n"]],
            "raw r": [signed(x, 3) for x in d["r_raw"]],
            "rho (shrunk)": [signed(x, 3) for x in d["rho"]],
            "90% interval": [
                f"[{signed(lo, 3)}, {signed(hi, 3)}]" for lo, hi in zip(d["ci_lo"], d["ci_hi"], strict=True)
            ],
        }
    )
    return md_table(out)


def conditional_table(fit: Fit) -> str:
    d = fit.detail[fit.detail["conditional"]]
    if d.empty:
        return "(no pair differs significantly across game-total terciles)"
    wide = d.pivot_table(
        index=["relation", "role_a", "role_b"], columns="total_bucket", values="r_raw", aggfunc="first"
    ).reset_index()
    out = pd.DataFrame(
        {
            "Relation": wide["relation"],
            "Pair": wide["role_a"] + " - " + wide["role_b"],
            **{b: [signed(x, 3) for x in wide[b]] for b in TOTAL_BUCKETS},
        }
    )
    return md_table(out)


def fit_summary(fit: Fit, checks: dict) -> str:
    cg = checks
    lines = [
        f"Fitted on {fit.meta['rows']:,} out-of-fold player-games in {fit.meta['games']:,} games "
        f"({fit.meta['seasons'][0]}-{fit.meta['seasons'][1]}); game-total terciles at "
        f"{fit.cutoffs[0]:.1f} / {fit.cutoffs[1]:.1f}.",
        "",
        "Against the literature:",
        literature_table(fit),
        "",
        f"Cross-game check ({cg['cross_game']['role_pairs']} role pairs, same week, different games): "
        f"mean |r| = {cg['cross_game']['mean_abs_r']:.4f} "
        f"(independence noise floor {cg['cross_game']['noise_floor']:.4f}; limit {CROSS_GAME_LIMIT}).",
        f"Total-conditional: {cg['conditional_pairs']} of {cg['pairs']} role pairs differ significantly "
        f"across game-total terciles; report only -- every pair ships its pooled value.",
    ]
    return "\n".join(lines)


# --- back-test --------------------------------------------------------------------------------------------

GROUPS = ("all", "stacked", "random", "in-range")
VARIANTS = ("shipped", "train-only")


def _mask(results: pd.DataFrame, group: str) -> pd.Series:
    if group == "stacked":
        return results["stacked"]
    if group == "random":
        return ~results["stacked"]
    if group == "in-range":  # no player projected past the engine's top bucket centre
        return ~results["beyond_top_centre"]
    return pd.Series(True, index=results.index)


def coverage_table(results: pd.DataFrame, variant: str, group: str) -> str:
    mask = _mask(results, group)
    c = bt.coverage(results, variant, "corr", mask)
    i = bt.coverage(results, variant, "indep", mask)
    out = pd.DataFrame(
        {
            "Predicted quantile": [f"p{round(100 * q)}" for q in c["level"]],
            "Target": [f"{t:.0f}%" for t in c["target"]],
            "Correlated": [
                f"{r:.1f}% ({signed(g, 1)})" for r, g in zip(c["realized"], c["gap"], strict=True)
            ],
            "Independent": [
                f"{r:.1f}% ({signed(g, 1)})" for r, g in zip(i["realized"], i["gap"], strict=True)
            ],
            "SE": [f"{s:.1f}" for s in c["se"]],
        }
    )
    return md_table(out)


def reliability_table(results: pd.DataFrame, variant: str, stat: str, threshold: float, group: str) -> str:
    mask = _mask(results, group)
    c = bt.reliability(results, variant, "corr", stat, threshold, mask)
    i = bt.reliability(results, variant, "indep", stat, threshold, mask)
    out = pd.DataFrame(
        {
            "Bin": c["bin"],
            "n": [f"{n:,}" for n in c["n"]],
            "Corr. predicted": [f"{x:.1f}%" for x in c["predicted"]],
            "Corr. realized": [f"{x:.1f}%" for x in c["realized"]],
            "Gap": [signed(x, 1) + ("*" if abs(x) > bt.RELIABILITY_TOL else "") for x in c["gap"]],
            "Indep. predicted": [f"{x:.1f}%" for x in i["predicted"]],
            "Indep. realized": [f"{x:.1f}%" for x in i["realized"]],
            "Gap ": [signed(x, 1) + ("*" if abs(x) > bt.RELIABILITY_TOL else "") for x in i["gap"]],
            "Hits (corr.)": c["hits"],
        }
    )
    return md_table(out)


def composition_table(results: pd.DataFrame, variant: str, cash_line: float) -> str:
    """What the back-test lineups look like and how the simulated means and cash rates compare with what
    happened, by how selective the draw was and by whether any player is past the engine's top bucket."""
    groups = {
        f"selectivity {s:g}": results["selectivity"] == s for s in sorted(results["selectivity"].unique())
    }
    groups |= {
        "all players in range": ~results["beyond_top_centre"],
        "a player past the top bucket": results["beyond_top_centre"],
    }
    rows = []
    for name, mask in groups.items():
        r = results[mask]
        rows.append(
            {
                "Lineups": name,
                "n": f"{len(r):,}",
                "Sum of projections": f"{r['projected'].mean():.1f}",
                "Simulated mean": f"{r[f'{variant}.corr.mean'].mean():.1f}",
                "Realized mean": f"{r['realized'].mean():.1f}",
                f"P(>= {cash_line:g}) predicted": f"{100 * r[f'{variant}.corr.p_cash'].mean():.1f}%",
                "realized": f"{100 * (r['realized'] >= cash_line).mean():.1f}%",
            }
        )
    return md_table(pd.DataFrame(rows))


def dispersion_table(results: pd.DataFrame, variant: str) -> str:
    """The mean squared standardized residual, ((realized - mean) / sd) ** 2: 1 for a calibrated spread,
    above 1 when the predicted spread is too narrow."""
    rows = []
    for group in GROUPS:
        r = results[_mask(results, group)]
        row = {"Lineups": group, "n": f"{len(r):,}"}
        for mode, label in (("indep", "Independent"), ("corr", "Correlated")):
            sd = r[f"{variant}.{mode}.sd"]
            row[f"{label} mean sd"] = f"{sd.mean():.2f}"
            row[f"{label} E[z^2]"] = (
                f"{(((r['realized'] - r[f'{variant}.{mode}.mean']) / sd) ** 2).mean():.3f}"
            )
        rows.append(row)
    return md_table(pd.DataFrame(rows))


def skill_table(results: pd.DataFrame, variant: str, cash_line: float, gpp_target: float) -> str:
    """Does the correlation help? Pinball loss over the predicted quantiles and Brier score of the two
    probabilities, correlated against independent, with the paired difference and its standard error (weeks as
    the unit). Positive = correlation better."""
    rows = []
    for group in GROUPS:
        mask = _mask(results, group)
        r = results[mask]
        measures = {
            "Pinball loss (p10-p99)": (bt.pinball(r, variant, "indep"), bt.pinball(r, variant, "corr"), 3),
            f"Brier, P(>= {cash_line:g})": (
                bt.brier(r, variant, "indep", "p_cash", cash_line),
                bt.brier(r, variant, "corr", "p_cash", cash_line),
                5,
            ),
            f"Brier, P(>= {gpp_target:g})": (
                bt.brier(r, variant, "indep", "p_gpp", gpp_target),
                bt.brier(r, variant, "corr", "p_gpp", gpp_target),
                5,
            ),
        }
        for name, (ind, cor, digits) in measures.items():
            diff, se = bt.paired_difference(r, ind, cor)
            rows.append(
                {
                    "Lineups": group,
                    "Measure": name,
                    "Independent": f"{ind.mean():.{digits}f}",
                    "Correlated": f"{cor.mean():.{digits}f}",
                    "Improvement": f"{diff:+.{digits}f} (SE {se:.{digits}f})",
                    "Better by": f"{diff / ind.mean():+.2%}",
                }
            )
    return md_table(pd.DataFrame(rows))


def verdict_table(results: pd.DataFrame, variant: str, cash_line: float, gpp_target: float) -> str:
    """The acceptance rule per group and simulation: coverage within +/-3, reliability bins within +/-4."""
    rows = []
    for group in GROUPS:
        mask = _mask(results, group)
        for mode, label in (("corr", "Correlated"), ("indep", "Independent")):
            cov = bt.coverage(results, variant, mode, mask)
            rel = {
                "cash": bt.reliability(results, variant, mode, "p_cash", cash_line, mask),
                "gpp": bt.reliability(results, variant, mode, "p_gpp", gpp_target, mask),
            }
            v = bt.verdict(cov, rel)
            rows.append(
                {
                    "Lineups": group,
                    "Simulation": label,
                    "Max coverage gap": f"{v['max_coverage_gap']:.1f}",
                    "Coverage within 3": "yes" if v["coverage_ok"] else "NO",
                    "Max cash-bin gap": f"{v['max_reliability_gap']['cash']:.1f}",
                    "Max GPP-bin gap": f"{v['max_reliability_gap']['gpp']:.1f}",
                    "Reliability within 4": "yes" if v["reliability_ok"] else "NO",
                }
            )
    return md_table(pd.DataFrame(rows))


def backtest_report(
    results: pd.DataFrame, cash_line: float | None = None, gpp_target: float | None = None
) -> str:
    from dfs.sim.simulate import DEFAULT_CASH_LINE, DEFAULT_GPP_TARGET

    cash_line = DEFAULT_CASH_LINE if cash_line is None else cash_line
    gpp_target = DEFAULT_GPP_TARGET if gpp_target is None else gpp_target
    n = len(results)
    lines = [
        "# Simulator back-test",
        "",
        f"{n:,} random legal DK classic lineups, {results['season'].min()}-{results['season'].max()} "
        f"({results.groupby(['season', 'week']).ngroups} weeks); {int(results['stacked'].sum()):,} stacked, "
        f"{int((~results['stacked']).sum()):,} random. Correlations fitted on 2014-2022 only. "
        f"Realized score: mean {results['realized'].mean():.1f}, "
        f"{(results['realized'] >= cash_line).mean():.1%} at or above {cash_line:g}, "
        f"{(results['realized'] >= gpp_target).mean():.2%} at or above {gpp_target:g}.",
        "",
    ]
    for variant in VARIANTS:
        lines += [f"## Marginals: {variant}", ""]
        repaired = results[f"{variant}.repaired"]
        lines += [
            f"PSD repair needed on {repaired.mean():.2%} of lineups; largest change to any entry "
            f"{results[f'{variant}.repair_change'].max():.4f}.",
            "",
        ]
        lines += ["### The lineups", "", composition_table(results, variant, cash_line), ""]
        for group in GROUPS:
            lines += [f"### Coverage, {group} lineups (gap in points; SE of the target in points)", ""]
            lines += [coverage_table(results, variant, group), ""]
        for stat, threshold, name in (("p_cash", cash_line, "cash"), ("p_gpp", gpp_target, "GPP")):
            for group in ("all", "stacked", "in-range"):
                lines += [f"### Reliability: P(score >= {threshold:g}) ({name} line), {group} lineups", ""]
                lines += [reliability_table(results, variant, stat, threshold, group), ""]
        lines += ["### Dispersion: mean of ((realized - mean) / sd)^2, 1 = calibrated", ""]
        lines += [dispersion_table(results, variant), ""]
        lines += ["### Does correlation help? (positive improvement = correlated is better)", ""]
        lines += [skill_table(results, variant, cash_line, gpp_target), ""]
        lines += ["### Verdict", "", verdict_table(results, variant, cash_line, gpp_target), ""]
    return "\n".join(lines)


# --- demo -------------------------------------------------------------------------------------------------

DEMO_SEASON, DEMO_WEEK = 2025, 12
# Two lineups from week 12 of 2025, by name. The first is stacked around Patrick Mahomes (QB + WR1 + TE1 of
# KC, with Michael Pittman of IND as the bring-back); the second takes nine players from nine different games,
# and shares Puka Nacua with the first (one variable in the simulation, not two).
DEMO_STACKED = [
    "Patrick Mahomes",
    "Christian McCaffrey",
    "Derrick Henry",
    "Rashee Rice",
    "Michael Pittman",
    "Jaxon Smith-Njigba",
    "Travis Kelce",
    "Puka Nacua",
    "CLE DST",
]
DEMO_PLAIN = [
    "Dak Prescott",
    "Bijan Robinson",
    "Jahmyr Gibbs",
    "Puka Nacua",
    "Justin Jefferson",
    "Tetairoa McMillan",
    "Brock Bowers",
    "Chase Brown",
    "BAL DST",
]
DEMO_SWAP = ("Michael Pittman", "A.J. Brown")  # out of the stacked lineup, in from another game


def _spec(row: pd.Series) -> PlayerSpec:
    return PlayerSpec(
        id=row["gsis_id"],
        position=row["position"],
        team=row["team"],
        opp=row["opp"],
        game_id=row["game_id"],
        role=row["role"],
        projection=float(row["pred"]),
    )


def _demo_row(week: pd.DataFrame, name: str) -> pd.Series:
    hit = week[week["name"] == name]
    if len(hit) != 1:
        raise ValueError(f"demo player {name!r} not found exactly once in {DEMO_SEASON} week {DEMO_WEEK}")
    return hit.iloc[0]


def demo_lineups(frame: pd.DataFrame) -> tuple[list[list[PlayerSpec]], pd.DataFrame]:
    """The two demo lineups as specs, and the week's rows for every player in them."""
    week = frame[(frame["season"] == DEMO_SEASON) & (frame["week"] == DEMO_WEEK)]
    rows = [[_demo_row(week, name) for name in names] for names in (DEMO_STACKED, DEMO_PLAIN)]
    specs = [[_spec(r) for r in lineup] for lineup in rows]
    return specs, pd.DataFrame([r for lineup in rows for r in lineup]).drop_duplicates("gsis_id")


def sim_text(result: SimResult) -> str:
    t = result.table().round(1)
    t["p_cash"] = [f"{s.p_cash:.1%}" for s in result.lineups]
    t["p_gpp"] = [f"{s.p_gpp:.1%}" for s in result.lineups]
    lines = [md_table(t)]
    lines += [
        "",
        f"P(at least one lineup >= {result.gpp_target:g}): {result.p_any_gpp:.1%}",
        f"P(at least one lineup >= {result.cash_line:g}): {result.p_any_cash:.1%}",
        f"Expected cashing lineups: {result.expected_cashing:.2f} of {len(result.lineups)}",
        "Lineup score correlation: "
        + ", ".join(f"{x:+.2f}" for x in result.score_correlation[np.triu_indices(len(result.lineups), 1)]),
        f"PSD repair: {'needed' if result.repair.needed else 'not needed'} "
        f"({result.repair.n_players} players, smallest eigenvalue {result.repair.min_eigenvalue:+.4f}, "
        f"largest change {result.repair.max_change:.4f})",
    ]
    return "\n".join(lines)


def demo_report(frame: pd.DataFrame, *, cash_line: float, gpp_target: float) -> str:
    specs, rows = demo_lineups(frame)
    result = simulate_lineups(specs, cash_line=cash_line, gpp_target=gpp_target)
    actual = rows.set_index("gsis_id")["dk"]
    realized = [sum(actual[p.id] for p in lineup) for lineup in specs]
    out = [
        f"Week {DEMO_WEEK} of {DEMO_SEASON}, 20,000 simulations. Lineup 1 is stacked around Patrick Mahomes "
        f"(QB, WR1, TE1 + Michael Pittman as the bring-back); lineup 2 is not.",
        "",
    ]
    for k, lineup in enumerate(specs, start=1):
        out.append(
            f"Lineup {k}: projected {sum(p.projection for p in lineup):.1f}; realized {realized[k - 1]:.1f}"
        )
    out += ["", sim_text(result), ""]
    week = frame[(frame["season"] == DEMO_SEASON) & (frame["week"] == DEMO_WEEK)]
    out_row, in_row = _demo_row(week, DEMO_SWAP[0]), _demo_row(week, DEMO_SWAP[1])
    swap = swap_impact(
        specs[0], out_row["gsis_id"], _spec(in_row), cash_line=cash_line, gpp_target=gpp_target
    )
    out.append(
        f"Swap {DEMO_SWAP[0]} for {DEMO_SWAP[1]} in lineup 1: change in P(cash) {swap.delta_p_cash:+.1%}, "
        f"P(GPP) {swap.delta_p_gpp:+.1%}, mean {swap.delta_mean:+.1f}"
    )
    return "\n".join(out)
