"""The game-script study, end to end: `dfs sim script` runs `analyse` and prints `render`.

Fit on 2014-2022, judged on 2023-2025, on the same normal scores as `docs/SIM.md`. The question is whether a
pair's correlation depends on the closing spread or the team's implied total, whether the projections already
price the game script, and whether any conditional table beats the pooled one out of sample. See `script.py`
(the tests), `script_pbp.py` (the mechanism) and `script_backtest.py` (the lineup-level bar).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.model import data
from dfs.model.history import team_game_context
from dfs.sim import script, script_bringback, script_pbp, script_shape
from dfs.sim.correlation import normal_scores
from dfs.sim.report import md_table, signed

NAMED = [(r, a, b) for r, a, b, _ in script.NAMED_PAIRS]
LABELS = {(r, a, b): label for r, a, b, label in script.NAMED_PAIRS}
TAIL_PAIRS = [
    ("same_team", "QB1", "WR1"),
    ("same_team", "QB1", "WR2"),
    ("same_team", "QB1", "TE1"),
    ("opp", "QB1", "QB1"),
    ("same_team", "QB1", "RB1"),
    ("same_team", "RB1", "DST"),
    ("opp", "QB1", "WR1"),
]


@dataclass
class Analysis:
    """Every table the report prints, as frames."""

    scan: script.Scan
    chance: pd.DataFrame
    oos: dict = field(default_factory=dict)  # variable -> {"all": ..., "named": ..., "flagged": ...}
    null: dict = field(default_factory=dict)  # variable -> {"all": array, "named": array}
    marginal: pd.DataFrame | None = None
    pass_rate: pd.DataFrame | None = None
    pass_rate_first_half: pd.DataFrame | None = None
    team_slopes: dict = field(default_factory=dict)
    volume: pd.DataFrame | None = None
    mechanism: pd.DataFrame | None = None
    tails: pd.DataFrame | None = None
    pooled_vs_zero: pd.DataFrame | None = None
    lineups: pd.DataFrame | None = None
    counts: dict = field(default_factory=dict)
    shape_scan: script_shape.ShapeScan | None = None
    shape_oos: dict = field(default_factory=dict)  # "all" / "receiver" -> held-out gain; "per_test"
    shape_null: dict = field(default_factory=dict)  # "all" / "receiver" -> gains of shuffled-feature tables
    bringback: script_bringback.Lift | None = None


def analyse(
    frame: pd.DataFrame,
    history,
    *,
    n_shuffle: int = 200,
    n_null: int = 100,
    seed: int = 0,
    lineup_results: pd.DataFrame | None = None,
    log=lambda msg: None,
) -> Analysis:
    """Run the whole study on `frame` (`roles.build_frame`'s output for 2014-2025)."""
    games = data.read_games()
    f = script.with_context(frame, games)
    f = f.assign(z=normal_scores(f, seed=seed))
    log("play-by-play")
    seasons = list(range(data.FIRST_SEASON, int(f["season"].max()) + 1))
    pbp = script_pbp.load_pbp(seasons, log=log)
    f = script_shape.shape_features(f, history, script_pbp.team_game_proe(pbp))
    train, test = f[f["season"] <= script.TRAIN_THROUGH], f[f["season"] > script.TRAIN_THROUGH]
    pairs = script.family_pairs()
    log("scanning the training seasons")
    sc = script.scan(train, n_shuffle=n_shuffle, seed=seed)
    out = Analysis(scan=sc, chance=script.chance_summary(sc.table, sc))
    out.counts = {"train_rows": len(train), "test_rows": len(test), "pairs": len(sc.table)}

    base = script.lookup_from_rhos(script.pooled_rhos(train, pairs))
    named = [p for p in NAMED if p in set(pairs)]
    table = sc.table
    for variable in script.VARIABLES:
        lookup = script.ContextLookup(base, sc.buckets, sc.cutoffs, use=(variable,))
        long = script.loglik_gain(test, lookup, pairs)
        flagged = [
            (r, a, b)
            for r, a, b, t in zip(
                table.relation, table.role_a, table.role_b, table[f"t_{variable}"], strict=True
            )
            if abs(t) > 1.96
        ]
        out.oos[variable] = {
            "all": script.summarize_gain(long),
            "named": script.summarize_gain(long, named),
            "flagged": script.summarize_gain(long, flagged) | {"pairs": len(flagged)},
            "per_pair": script.per_pair_gain(long),
        }
    log(f"shuffled-context control ({n_null} draws)")
    rng = np.random.default_rng(seed + 5)
    null = {v: {"all": [], "named": []} for v in script.VARIABLES}
    for _ in range(n_null):
        sh = script.shuffled_frame(train, rng)
        scn = script.scan(sh, n_shuffle=0, n_boot=5, seed=seed)
        b = script.lookup_from_rhos(script.pooled_rhos(sh, pairs))
        for v in script.VARIABLES:
            long = script.loglik_gain(
                test, script.ContextLookup(b, scn.buckets, scn.cutoffs, use=(v,)), pairs
            )
            null[v]["all"].append(script.summarize_gain(long)["per_1000"])
            null[v]["named"].append(script.summarize_gain(long, named)["per_1000"])
    out.null = {v: {k: np.array(x) for k, x in d.items()} for v, d in null.items()}

    log("team shape")
    out.shape_scan = shape_sc = script_shape.scan(train, n_shuffle=n_shuffle, seed=seed)
    lookups = {
        "all": script_shape.ShapeLookup(base, shape_sc.buckets, shape_sc.cuts),
        "receiver": script_shape.ShapeLookup(
            base, shape_sc.buckets, shape_sc.cuts, tests=set(script_shape.RECEIVER_TESTS)
        ),
    }
    long = script_shape.shape_gain(test, lookups["all"])
    out.shape_oos = {
        "all": script_shape.summarize(long),
        "receiver": script_shape.summarize(long, tests=set(script_shape.RECEIVER_TESTS)),
        "per_test": long.groupby(["relation", "role_a", "role_b", "side", "feature"])
        .apply(lambda g: pd.Series(script.summarize_gain(g)), include_groups=False)
        .reset_index(),
    }
    log(f"shuffled-feature control ({n_null} draws)")
    rng = np.random.default_rng(seed + 9)
    shape_null = {"all": [], "receiver": []}
    for _ in range(n_null):
        scn = script_shape.scan(script_shape.shuffled_features(train, rng), n_shuffle=0)
        gain = script_shape.shape_gain(test, script_shape.ShapeLookup(base, scn.buckets, scn.cuts))
        shape_null["all"].append(script_shape.summarize(gain)["per_1000"])
        shape_null["receiver"].append(
            script_shape.summarize(gain, tests=set(script_shape.RECEIVER_TESTS))["per_1000"]
        )
    out.shape_null = {k: np.array(v) for k, v in shape_null.items()}
    log("bring-back lift")
    out.bringback = script_bringback.lift_table(f, seed=seed)

    log("marginal effects")
    out.marginal = script.marginal_effects(f, script.naive_baseline(history))
    ctx = team_game_context(games)
    out.pass_rate = script_pbp.pass_rate_by_state(pbp, ctx)
    out.pass_rate_first_half = script_pbp.pass_rate_by_state(pbp, ctx, first_half=True)
    tg = script_pbp.team_game_script(pbp, ctx, games)
    for y, x in (("pass_rate", "spread"), ("pass_rate", "avg_lead"), ("pass_rate", "margin")):
        b, se = script_pbp.slope(tg[y], tg[x], tg["game_id"])
        out.team_slopes[x] = (b, b / se)
    out.volume = script_pbp.volume_table(script_pbp.team_volume(history.player_games, ctx, games))
    out.mechanism = script.margin_mechanism(
        f, games, NAMED + [("same_team", "QB1", "DST"), ("opp", "QB1", "DST")]
    )
    out.tails = script.tail_dependence(f, TAIL_PAIRS, seed=seed)
    out.pooled_vs_zero = pooled_vs_zero(train, test, NAMED)
    out.lineups = lineup_results
    return out


def pooled_vs_zero(train: pd.DataFrame, test: pd.DataFrame, pairs) -> pd.DataFrame:
    """Does the pooled correlation (fitted on the training seasons) beat independence on the test seasons?"""
    rows = []
    for relation, a, b in pairs:
        tr, te = script.oriented_rows(train, relation, a, b), script.oriented_rows(test, relation, a, b)
        rho = float(script.shrink(np.corrcoef(tr["z_a"], tr["z_b"])[0, 1], len(tr)))
        za, zb = te["z_a"].to_numpy(), te["z_b"].to_numpy()
        gain = script.bivariate_loglik(za, zb, rho) - script.bivariate_loglik(za, zb, 0.0)
        by_game = pd.Series(gain).groupby(te["game_id"].to_numpy()).sum()
        rows.append(
            {
                "pair": f"{relation} {a}-{b}",
                "train_rho": rho,
                "test_r": float(np.corrcoef(za, zb)[0, 1]),
                "n": len(te),
                "gain": float(gain.sum()),
                "t": float(gain.sum() / np.sqrt((by_game**2).sum())),
            }
        )
    return pd.DataFrame(rows)


# --- text -------------------------------------------------------------------------------------------------


def _pair(relation: str, a: str, b: str) -> str:
    return f"{a} - {b}" + ("" if relation == "same_team" else " (opp.)")


def chance_text(a: Analysis) -> str:
    c = a.chance.copy()
    c["variable"] = c["variable"].map({"spread": "closing spread", "implied": "team implied total"})
    c = c.rename(
        columns={
            "variable": "Conditioned on",
            "pairs": "Pairs",
            "flagged_real": "|t| > 1.96, real contexts",
            "flagged_shuffled_mean": "shuffled: mean",
            "flagged_shuffled_p95": "shuffled: 95th pct",
            "max_abs_t_real": "largest |t|, real",
            "max_abs_t_shuffled_p95": "largest |t|, shuffled 95th pct",
        }
    )
    return md_table(
        c,
        {
            "shuffled: mean": ".1f",
            "shuffled: 95th pct": ".0f",
            "largest |t|, real": ".2f",
            "largest |t|, shuffled 95th pct": ".2f",
        },
    )


def named_text(a: Analysis) -> str:
    t, b = a.scan.table.set_index(["relation", "role_a", "role_b"]), a.scan.buckets
    rows = []
    for key in NAMED:
        if key not in t.index:
            continue
        r = t.loc[key]
        row = {"Pair": LABELS[key], "n": int(r["n"]), "pooled r": f"{r['r_pooled']:+.3f}"}
        for variable, names in (("spread", script.SPREAD_BUCKETS), ("implied", script.IMPLIED_BUCKETS)):
            sel = b[
                (b.relation == key[0])
                & (b.role_a == key[1])
                & (b.role_b == key[2])
                & (b.variable == variable)
            ]
            by = sel.set_index("bucket")["r"]
            for name in names:
                row[f"{variable[:3]}:{name}"] = f"{by.get(name, np.nan):+.3f}"
        row["t (spread)"] = f"{r['t_spread']:+.1f}"
        row["t (implied)"] = f"{r['t_implied']:+.1f}"
        row["p adj. (spread)"] = f"{r['p_adj_spread']:.2f}"
        row["p adj. (implied)"] = f"{r['p_adj_implied']:.2f}"
        rows.append(row)
    return md_table(pd.DataFrame(rows))


def oos_text(a: Analysis) -> str:
    rows = []
    for variable in script.VARIABLES:
        o, n = a.oos[variable], a.null[variable]
        for scope in ("all", "named"):
            g = o[scope]
            nul = n[scope]
            rows.append(
                {
                    "Conditioned on": {"spread": "closing spread", "implied": "implied total"}[variable],
                    "Pairs": "all 110" if scope == "all" else f"{len(NAMED)} named",
                    "Gain per 1,000 pair-obs": f"{g['per_1000']:+.3f}",
                    "SE": f"{g['se_per_1000']:.3f}",
                    "Shuffled contexts: mean": f"{nul.mean():+.3f}",
                    "95th pct": f"{np.quantile(nul, 0.95):+.3f}",
                    "Share of shuffles >= real": f"{(nul >= g['per_1000']).mean():.2f}",
                }
            )
        f = o["flagged"]
        rows.append(
            {
                "Conditioned on": {"spread": "closing spread", "implied": "implied total"}[variable],
                "Pairs": f"{f['pairs']} flagged in training",
                "Gain per 1,000 pair-obs": f"{f['per_1000']:+.3f}",
                "SE": f"{f['se_per_1000']:.3f}",
                "Shuffled contexts: mean": "",
                "95th pct": "",
                "Share of shuffles >= real": "",
            }
        )
    return md_table(pd.DataFrame(rows))


def lineup_text(results: pd.DataFrame) -> str:
    """The lineup-level comparison: each conditional table against the pooled one on the same draws -- the
    three measures over all lineups, then the pinball loss by lineup family (the families that contain the
    pairs a table changes are `rb_dst`, `qb_te`, `qb_rb`, `game` and `stack`)."""
    from dfs.sim import script_backtest as sb

    names = [
        n
        for n in ("spread", "spread_named", "implied", "shape_tshare", "shape_all")
        if f"shipped.{n}.p50" in results
    ]
    overall, by_family = [], []
    for marginals in ("shipped", "train-only"):
        for name in names:
            c = sb.comparison(results, marginals, name)
            row = {"Marginals": marginals, "Conditional table": name}
            for measure, label in (
                ("Pinball loss (p10-p99)", "Pinball"),
                ("Brier, P(>= cash line)", "Brier cash"),
                ("Brier, P(>= top end)", "Brier top end"),
            ):
                r = c[(c.family == "all") & (c.measure == measure)].iloc[0]
                row[label] = f"{r['relative']:+.4f}% (t {r['improvement'] / r['se']:+.1f})"
            overall.append(row)
            fam = {"Marginals": marginals, "Conditional table": name}
            for family in sb.FAMILIES:
                r = c[(c.family == family) & (c.measure == "Pinball loss (p10-p99)")].iloc[0]
                fam[family] = f"{r['relative']:+.4f}% (t {r['improvement'] / r['se']:+.1f})"
            by_family.append(fam)
    return (
        "Better than the pooled table by (percent of its loss; positive = better; t over weeks), "
        "all lineups:\n\n"
        + md_table(pd.DataFrame(overall))
        + "\n\nPinball loss by lineup family:\n\n"
        + md_table(pd.DataFrame(by_family))
    )


def marginal_text(a: Analysis) -> str:
    m = a.marginal
    rows = []
    for role in script.MARGINAL_ROLES:
        tr = m[(m.role == role) & (m.era == "train")].iloc[0]
        te = m[(m.role == role) & (m.era == "test")].iloc[0]
        rows.append(
            {
                "Role": role,
                "Mean projection": f"{tr['mean_pred']:.1f}",
                "Projection moves per +10 spread": f"{tr['mean_pred_per10']:+.2f}",
                "Naive baseline misses (2014-22)": f"{tr['naive_per10']:+.2f} (t {tr['naive_t']:+.1f})",
                "Projection misses (2014-22)": f"{tr['um_per10']:+.2f} (t {tr['um_t']:+.1f})",
                "Projection misses (2023-25)": f"{te['um_per10']:+.2f} (t {te['um_t']:+.1f})",
                "Position in its own distribution, z per +10 (2014-22 / 2023-25)": (
                    f"{tr['z_per10']:+.2f} (t {tr['z_t']:+.1f}) / {te['z_per10']:+.2f} (t {te['z_t']:+.1f})"
                ),
            }
        )
    return md_table(pd.DataFrame(rows))


def mechanism_text(a: Analysis) -> str:
    m = a.mechanism.copy()
    m["Pair"] = [_pair(r, x, y) for r, x, y in zip(m.relation, m.role_a, m.role_b, strict=True)]
    m = m[["Pair", "n", "r", "r_given_margin", "r_a_margin", "r_b_margin"]]
    m.columns = ["Pair", "n", "r", "r after removing the margin", "A vs margin", "B vs margin"]
    return md_table(
        m, {"r": "+.3f", "r after removing the margin": "+.3f", "A vs margin": "+.3f", "B vs margin": "+.3f"}
    )


def tails_text(a: Analysis) -> str:
    t = a.tails.copy()
    t["Pair"] = [_pair(r, x, y) for r, x, y in zip(t.relation, t.role_a, t.role_b, strict=True)]
    t["Both above"] = [f"+{c:g} sd" for c in t["cut"]]
    t["90% interval"] = [f"[{lo:.2f}, {hi:.2f}]" for lo, hi in zip(t.ratio_lo, t.ratio_hi, strict=True)]
    t = t[["Pair", "Both above", "n", "rho", "observed", "gaussian", "ratio", "90% interval"]]
    t.columns = [
        "Pair",
        "Both above",
        "n",
        "r",
        "Observed",
        "Gaussian, same r",
        "Ratio",
        "90% interval of ratio",
    ]
    return md_table(t, {"r": "+.3f", "Observed": ".4f", "Gaussian, same r": ".4f", "Ratio": ".2f"})


def pass_rate_text(a: Analysis) -> str:
    p = a.pass_rate[["all", "BD", "SD", "SF", "BF"]].copy()
    p.insert(0, "Offense's score at the snap", p.index)
    p["Share of snaps"] = a.pass_rate["snaps_all"].map("{:.0%}".format).to_numpy()
    for c in ("all", "BD", "SD", "SF", "BF"):
        p[c] = p[c].map("{:.1%}".format)
    p = p.rename(
        columns={"all": "All teams", "BD": "big dog", "SD": "small dog", "SF": "small fav.", "BF": "big fav."}
    )
    return md_table(p.reset_index(drop=True))


def volume_text(a: Analysis) -> str:
    v = a.volume.rename(
        index={
            "attempts": "pass attempts",
            "rb_carries": "RB carries",
            "wr_te_targets": "WR + TE targets",
            "rb_targets": "RB targets",
        }
    )
    v = v.reset_index(names="Per team-game")
    v.columns = [
        "Per team-game",
        "big dog",
        "small dog",
        "small fav.",
        "big fav.",
        "per +10 spread",
        "t",
        "per +10 final margin",
        "t ",
    ]
    return md_table(
        v,
        {c: ".1f" for c in ("big dog", "small dog", "small fav.", "big fav.")}
        | {"per +10 spread": "+.2f", "t": "+.1f", "per +10 final margin": "+.2f", "t ": "+.1f"},
    )


def pooled_text(a: Analysis) -> str:
    p = a.pooled_vs_zero
    return md_table(p, {"train_rho": "+.3f", "test_r": "+.3f", "gain": "+.1f", "t": "+.1f"})


def bringback_text(a: Analysis) -> str:
    """The bring-back's lift by spread bucket of the stacked team, for the main stacks."""
    t = a.bringback.table
    rows = []
    for stack, back in (
        ("QB+WR1+WR2", "WR1"),
        ("QB+WR1+WR2", "TE1"),
        ("QB+WR1", "WR1"),
        ("QB+RB1", "WR1"),
    ):
        for tail in ("top 10%", "top 5%", "top 1%"):
            sel = t[(t["stack"] == stack) & (t["back"] == back) & (t["tail"] == tail)].set_index("bucket")
            if sel.empty:
                continue
            row = {"Stack": stack, "Plus the other side's": back, "Tail of S + B": tail}
            for bucket in (*script.SPREAD_BUCKETS, "all"):
                if bucket in sel.index:
                    r = sel.loc[bucket]
                    row[bucket] = f"{r['lift']:.2f} [{r['lo']:.2f}, {r['hi']:.2f}] ({int(r['hits'])})"
            rows.append(row)
    corr = t[(t["tail"] == "top 10%") & (t["back"] == "WR1")].drop_duplicates(["stack", "bucket"])
    corr_rows = (
        corr.pivot(index="stack", columns="bucket", values="corr")
        .reindex(columns=[*script.SPREAD_BUCKETS, "all"])
        .reset_index()
        .rename(columns={"stack": "Stack"})
    )
    return (
        md_table(pd.DataFrame(rows))
        + "\n\nPearson r of the stack's points with the opposing WR1's, by the stacked team's "
        "spread bucket:\n\n" + md_table(corr_rows, {c: "+.3f" for c in corr_rows.columns[1:]})
    )


def shape_text(a: Analysis) -> str:
    """The named team-shape tests, tercile by tercile, then the out-of-sample gains."""
    t = a.shape_scan.table.set_index(["relation", "role_a", "role_b", "side", "feature"])
    rows = []
    for rel, x, y, side, feat, label in script_shape.NAMED:
        key = (rel, x, y, side, feat)
        if key not in t.index:
            continue
        r = t.loc[key]
        rows.append(
            {
                "Pair, by modifier": label,
                "n": int(r["n"]),
                "pooled": f"{r['r_pooled']:+.3f}",
                "low": f"{r['r_low']:+.3f}",
                "mid": f"{r['r_mid']:+.3f}",
                "high": f"{r['r_high']:+.3f}",
                "t": f"{r['t']:+.1f}",
                "p adj.": f"{r['p_adj']:.2f}",
            }
        )
    named = md_table(pd.DataFrame(rows))
    per = a.shape_oos["per_test"].copy()
    per = per[
        [
            (r, x, y, s_, f) in {n[:5] for n in script_shape.NAMED}
            for r, x, y, s_, f in zip(
                per.relation, per.role_a, per.role_b, per.side, per.feature, strict=True
            )
        ]
    ]
    per["Pair, by modifier"] = [
        {n[:5]: n[5] for n in script_shape.NAMED}[(r, x, y, s_, f)]
        for r, x, y, s_, f in zip(per.relation, per.role_a, per.role_b, per.side, per.feature, strict=True)
    ]
    per = per[["Pair, by modifier", "n", "per_1000", "se_per_1000"]]
    per.columns = ["Pair, by modifier", "n", "Held-out gain per 1,000", "SE"]
    totals = []
    for label, key in (("all 22 tests", "all"), ("receiver target share (6 tests)", "receiver")):
        g, null = a.shape_oos[key], a.shape_null[key]
        totals.append(
            {
                "Tests": label,
                "Gain per 1,000": f"{g['per_1000']:+.3f}",
                "SE": f"{g['se_per_1000']:.3f}",
                "Shuffled features: mean": f"{null.mean():+.3f}",
                "95th pct": f"{np.quantile(null, 0.95):+.3f}",
                "Share of shuffles >= real": f"{(null >= g['per_1000']).mean():.3f}",
            }
        )
    return (
        named + "\n\nHeld-out gain of each named test (2023-2025), then the totals against the "
        "shuffled-feature control:\n\n"
        + md_table(per, {"Held-out gain per 1,000": "+.3f", "SE": ".3f"})
        + "\n\n"
        + md_table(pd.DataFrame(totals))
    )


FAMILY_ALPHA = 0.05
TABLES_EXAMINED = (
    6  # spread and implied total, each over all pairs and the named pairs, and the two shape tables
)


def ruling(a: Analysis) -> pd.DataFrame:
    """The shipping bar, table by table (see `script_backtest`): the pinball loss better by more than two
    paired standard errors under both marginal sets, neither Brier score worse by a standard error, and an
    out-of-sample gain over the pooled value that beats the shuffled-context control at `FAMILY_ALPHA` shared
    across the tables examined. A table ships only if all three hold."""
    from dfs.sim import script_backtest as sb

    rows = []
    for name, gain, null in (
        ("spread", a.oos["spread"]["all"]["per_1000"], a.null["spread"]["all"]),
        ("spread_named", a.oos["spread"]["named"]["per_1000"], a.null["spread"]["named"]),
        ("implied", a.oos["implied"]["all"]["per_1000"], a.null["implied"]["all"]),
        ("shape_tshare", a.shape_oos["receiver"]["per_1000"], a.shape_null["receiver"]),
        ("shape_all", a.shape_oos["all"]["per_1000"], a.shape_null["all"]),
    ):
        share = float((null >= gain).mean())
        seen = a.lineups is not None and f"shipped.{name}.p50" in a.lineups
        pinball, brier_ok = [], True
        if seen:
            for marginals in ("shipped", "train-only"):
                c = sb.comparison(a.lineups, marginals, name)
                c = c[c.family == "all"].set_index("measure")
                pinball.append(
                    float(
                        c.loc["Pinball loss (p10-p99)", "improvement"] / c.loc["Pinball loss (p10-p99)", "se"]
                    )
                )
                for measure in ("Brier, P(>= cash line)", "Brier, P(>= top end)"):
                    brier_ok = brier_ok and bool(c.loc[measure, "improvement"] >= -c.loc[measure, "se"])
        checks = {
            "pinball > 2 SE, both marginal sets": seen and min(pinball) > 2,
            "no Brier loss beyond 1 SE": seen and brier_ok,
            f"gain > 0 and control share <= {FAMILY_ALPHA / TABLES_EXAMINED:.4f}": gain > 0
            and share <= FAMILY_ALPHA / TABLES_EXAMINED,
        }
        rows.append(
            {
                "Conditional table": name,
                "Pinball t (shipped / train-only)": " / ".join(f"{t:+.1f}" for t in pinball)
                if seen
                else "n/a",
                "Control share": f"{share:.3f}",
                **{k: "yes" if v else "NO" for k, v in checks.items()},
                "Ships": "yes" if all(checks.values()) else "NO",
            }
        )
    return pd.DataFrame(rows)


def render(a: Analysis) -> str:
    """The whole report as markdown."""
    sp, im = a.oos["spread"], a.oos["implied"]
    ns, ni = a.null["spread"]["named"], a.null["implied"]["named"]
    parts = [
        "# Game script: spread and implied total, tested\n",
        f"Fit on 2014-2022 ({a.counts['train_rows']:,} player-games), judged on 2023-2025 "
        f"({a.counts['test_rows']:,}). Normal scores as in `docs/SIM.md`; spread is team-relative (positive "
        f"= this team "
        f"favoured); buckets BD/SD/SF/BF are big dog (+7 or more), small dog, small favourite, big "
        f"favourite; the "
        f"implied total is cut at the training terciles ({a.scan.cutoffs[0]:.1f}, "
        f"{a.scan.cutoffs[1]:.1f}).\n",
        "## 1. Does a pair's correlation move with the spread or the implied total?\n",
        "Slope of the cross-product of the two z-scores on the covariate, game-clustered t. With "
        f"{a.counts['pairs']} pairs a few exceed 1.96 by chance, so the same scan is repeated with the "
        f"games' contexts "
        "shuffled among games of the same season.\n",
        chance_text(a),
        "\n## 2. The named pairs, in the training seasons\n",
        "Correlation of the surprises (unshrunk) by bucket of role A's team; `p adj.` is the share of the "
        "shuffled scans "
        "whose LARGEST |t| over all pairs is at least this pair's.\n",
        named_text(a),
        "\n## 3. Out of sample: does conditioning beat the pooled value?\n",
        "Log-likelihood gain of the bucket correlation (shrunk toward the pooled value, prior 200) over the "
        "pooled value, on the "
        "2023-2025 pairs; the control fits the same table on shuffled training contexts.\n",
        oos_text(a),
        "\n## 4. Lineup level (the shipping bar)\n",
        lineup_text(a.lineups) if a.lineups is not None else "(not run: pass `--lineups N`)",
        "\n## 4b. Team shape: who the players are, as a modifier on the pair\n",
        "Terciles (cut on the training seasons) of each modifier, three previous games only: target share "
        "of the "
        "receiver or back, the QB's carry share, the team's pass rate over expected, target concentration. "
        "Route "
        "participation is not in the weekly data this repo loads; target share stands in for it. "
        "Correlations are "
        "unshrunk; `p adj.` is the share of shuffled scans (the feature shuffled among rows of the same "
        "role and "
        "season) whose largest |t| over all 22 tests is at least this test's.\n",
        shape_text(a),
        "\n### The ruling\n",
        md_table(ruling(a)),
        "\n## 5. What a bring-back adds, by the stacked team's spread\n",
        "Lift = P(stack + the other side's player reaches the tail) / P(stack + an unrelated player of the "
        "same role "
        "and spread bucket does), 2014-2025 actual DK points; above 1 the bring-back helps, at 1 it adds "
        "nothing, "
        "below 1 it hurts. 90% interval from resampling games; the top-1% hit counts are in brackets, and "
        "are "
        "small.\n",
        bringback_text(a),
        "\n## 6. Do the projections already price the spread?\n",
        "Change in (actual - projection) DK points per +10 points of spread; a projection that priced the "
        "script fully "
        "would miss by 0. The naive baseline is each player's mean over his previous three games.\n",
        marginal_text(a),
        "\n## 7. The mechanism\n",
        f"Pass rate by the offense's score at the snap and its pre-game spread (2014-2025 designed plays). "
        f"Per +1 point "
        f"of pre-game spread the team-game pass rate moves {a.team_slopes['spread'][0] * 100:+.2f} points "
        f"(t {a.team_slopes['spread'][1]:+.0f}); per +1 point of average lead on offense "
        f"{a.team_slopes['avg_lead'][0] * 100:+.2f}; per +1 point of final margin "
        f"{a.team_slopes['margin'][0] * 100:+.2f}.\n",
        pass_rate_text(a),
        "\nVolume by pre-game spread bucket and by final margin:\n",
        volume_text(a),
        "\nThe final margin and the surprises (quadratic in the margin of role A's team removed from "
        "both):\n",
        mechanism_text(a),
        "\n## 8. Tails: do the booms of a pair cluster more than a Gaussian copula says?\n",
        tails_text(a),
        "\n## 9. Pooled correlation against independence, out of sample\n",
        pooled_text(a),
        f"\n(Shuffled-context control for the named pairs: spread mean {ns.mean():+.3f}, 95th pct "
        f"{np.quantile(ns, 0.95):+.3f}; implied mean {ni.mean():+.3f}, 95th pct "
        f"{np.quantile(ni, 0.95):+.3f}. "
        f"Real: spread {sp['named']['per_1000']:+.3f}, implied {im['named']['per_1000']:+.3f}.)\n",
    ]
    return "\n".join(parts)


def verdict_line(a: Analysis) -> str:
    """The one-line ruling the console prints under the report."""
    sp = a.oos["spread"]["named"]["per_1000"]
    share = float((a.null["spread"]["named"] >= sp).mean())
    return f"named-pairs spread gain {sp:+.3f}/1000, {share:.0%} of shuffled-context tables do as well"


def signed_text(x: float) -> str:
    return signed(x, 2)
