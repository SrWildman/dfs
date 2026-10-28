"""`dfs research refresh`: the weekly reruns of the offline studies, in one command and one report.

Offline only: it reads the caches and `data/results`, `data/signals`, `data/contests`, writes nothing but
`docs/research/weekly/<season>-wk<NN>.md` (gitignored) and touches no sheet. Each study fails soft (one broken
study is reported and the rest still run), and the report says how long each took.

Studies, each with the bar it must clear to matter:

| key | study | module |
|---|---|---|
| `shape` | the five QB1-receiver pairs re-tested by target share | `dfs.sim.shape_retest` |
| `bump` | the teammate-bump shadow's Model Check race | `dfs.teammate_bump` |
| `recal` | recalibrating Hit3x% / Boom% / Bust% | `dfs.prob_recal` |
| `tails` | the top of the marginal tables (coverage, per-role stretch) | `dfs.tail_check` |
| `ownership` | the field-ownership shadow's scores, where CalPts and TFFB disagree | `dfs.field_own` |
| `shadows` | ESPN and props shadow projections against CalPts (the blend gate) | `calibration.shadow_race` |
| `props` | the PROPS SHADOW: does ESPN beat the prop market's price | `dfs.props_edge.build_shadow` |
| `contest_retro` | the contest retro (hook below) | `dfs.sim.contest_retro` |
| `curse` | the optimizer's curse and its held-out fixes (hook below) | `dfs.research.curse` |
| `backtest` | Flashback: build settings scored in his real contests (optional: `--only`) | `dfs.backtest` |
| `dst` | OurDST, our own DST projection, against TFFB and CalPts (hook below) | `dfs.model.dst_study` |

**The hook.** A study kept in its own module (`HOOK_MODULES`: the contest retro, the curse study, OurDST)
plugs in by exposing

    refresh_summary(season, through) -> {"headline": [str, ...], "metrics": {str: float | bool},
                                         "ship_bar": str}

and the study appears in the report; a module that cannot be imported (or has no `refresh_summary`) is
reported as skipped, with that line.

**Changed since last week** compares the previous report of the season (the highest week below this one) by
the numbers each study puts in `metrics`, which every report embeds as a JSON comment at its end.
"""

from __future__ import annotations

import importlib
import json
import re
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.paths import REPO_ROOT

REPORT_DIR = REPO_ROOT / "docs" / "research" / "weekly"
METRICS_TAG = "dfs-research-refresh-metrics"
CHANGE_MIN_ABS = 1e-3  # a number counts as changed when it moved by this much ...
CHANGE_MIN_REL = 0.05  # ... and by this share of itself
CHANGE_LIMIT = 12


@dataclass
class Study:
    """One study's outcome. `status` is ok, skipped or failed; `verdict` one line for the summary."""

    key: str
    title: str
    ship_bar: str
    status: str = "ok"
    verdict: str = ""
    headline: list[str] = field(default_factory=list)
    metrics: dict[str, float | bool] = field(default_factory=dict)
    seconds: float = 0.0
    note: str = ""


class Context:
    """What the studies share, built on first use: the scored files, the UM out-of-fold frame (about a
    minute),
    the nflverse history. `through` is the last closed week to include."""

    def __init__(self, season: int, through: int, shuffles: int):
        self.season, self.through, self.shuffles = season, through, shuffles
        self.load_seconds = 0.0
        self._scored = self._frame = self._history = None

    def _timed(self, build: Callable):
        start = time.perf_counter()
        try:
            return build()
        finally:
            self.load_seconds += time.perf_counter() - start

    @property
    def scored(self) -> pd.DataFrame:
        if self._scored is None:
            from dfs import results_update

            def build():
                s = results_update.load_all_scored(self.season)
                return s[pd.to_numeric(s["week"], errors="coerce") <= self.through].reset_index(drop=True)

            self._scored = self._timed(build)
        return self._scored

    @property
    def frame(self) -> pd.DataFrame:
        """Every out-of-fold player-game 2014 through `season`, with this season cut after `through`."""
        if self._frame is None:
            from dfs.sim.pipeline import load_frame

            def build():
                f = load_frame(self.season)
                return f[(f["season"] < self.season) | (f["week"] <= self.through)].reset_index(drop=True)

            self._frame = self._timed(build)
        return self._frame

    @property
    def history(self):
        if self._history is None:
            from dfs.model.history import load_history
            from dfs.model.predict import available_seasons

            self._history = self._timed(lambda: load_history(available_seasons(self.season))[0])
        return self._history


def latest_scored_week(season: int) -> int | None:
    """The newest week with a scored file for `season`, or None."""
    from dfs import results_loop

    weeks = []
    for path in results_loop.RESULTS_DIR.glob(f"scored_{season}_w*.csv"):
        match = re.search(r"_w(\d+)\.csv$", path.name)
        if match:
            weeks.append(int(match.group(1)))
    return max(weeks) if weeks else None


def _fmt(x: float, places: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{places}f}"


def _num(x: float) -> str:
    """A count (an int in the metrics) prints whole, anything else to three places."""
    return str(x) if isinstance(x, int) else _fmt(float(x), 3)


def _md_table(frame: pd.DataFrame, places: int = 3) -> list[str]:
    cols = [str(c) for c in frame.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in frame.itertuples(index=False):
        out.append("| " + " | ".join(_fmt(v, places) if isinstance(v, float) else str(v) for v in row) + " |")
    return out


# --- the studies -------------------------------------------------------------------------------------------


def study_shape(ctx: Context) -> Study:
    from dfs.sim import shape_retest

    s = Study(
        "shape",
        "Five QB1-receiver pairs by target share (and the QB1-TE1 control)",
        f"held-out gain per 1,000 pairs above {shape_retest.BAR_SE:g} SE "
        f"(fit 2014-{shape_retest.TRAIN_THROUGH}, judge the seasons after); "
        "a pair that clears it and beats its shuffled-feature tables is a candidate "
        "for `shape_table.SHIPPED_TESTS`, and shipping a refit is Sam's call",
    )
    f = shape_retest.prepare(ctx.frame, ctx.history)
    result = shape_retest.run(f, n_shuffle=ctx.shuffles)
    t = result["table"]
    s.headline = _md_table(
        t[["pair", "n_test", "gain_per_1000", "se", "z", "shuffled_as_good", "ships"]].rename(
            columns={"gain_per_1000": "gain/1000", "shuffled_as_good": "shuffles as good"}
        ),
        2,
    ) + ["", f"Held out: {result['test_seasons']}; {ctx.shuffles} shuffled-feature tables per pair."]
    for r in t.itertuples():
        s.metrics[f"{r.pair} gain/1000"] = round(r.gain_per_1000, 3)
        s.metrics[f"{r.pair} z"] = round(r.z, 2)
        s.metrics[f"{r.pair} ships"] = bool(r.ships)
    shipping = [r.pair for r in t.itertuples() if r.ships]
    s.verdict = f"{len(shipping)} of {len(t)} clear the bar" + (
        f": {', '.join(shipping)}" if shipping else ""
    )
    return s


def study_bump(ctx: Context) -> Study:
    from dfs import calibration, teammate_bump

    s = Study(
        "bump",
        "Teammate bump shadow (Model Check race)",
        "joins CalPts only after 3 scored weeks of teammates of a regular listed out, "
        "and only if it beats CalPts "
        "on MAE and rank correlation (`calibration.ShadowPolicy`)",
    )
    roles = teammate_bump.season_roles(ctx.season)
    roles = roles[roles["week"] <= ctx.through]
    race = calibration.teammate_bump_race(ctx.scored, roles)
    absent = teammate_bump.absent_by_team_week(ctx.scored, roles)
    if race.empty:
        s.headline = [
            f"No teammate of a lone regular listed OUT or IR has been scored yet ({len(absent)} team-weeks)."
        ]
        s.verdict = "no rows yet"
        s.metrics["team_weeks"] = len(absent)
        return s
    allrows = race[race["Position"] == "All"].set_index("Source")
    s.headline = _md_table(race[["Source", "Position", "n", "Rho", "MAE", "Bias"]], 3) + [
        "",
        f"{len(absent)} team-weeks with exactly one regular out "
        "(roles need three prior games, so Week 4 on).",
    ]
    cal, bump = allrows.loc["CalPts"], allrows.loc[teammate_bump.LABEL]
    s.metrics.update(
        {
            "team_weeks": len(absent),
            "n": int(bump["n"]),
            "MAE CalPts": round(float(cal["MAE"]), 3),
            "MAE bump": round(float(bump["MAE"]), 3),
            "MAE gain": round(float(cal["MAE"] - bump["MAE"]), 3),
            "Rho gain": round(float(bump["Rho"] - cal["Rho"]), 3),
        }
    )
    s.verdict = f"n {int(bump['n'])}: MAE {_fmt(cal['MAE'])} CalPts, {_fmt(bump['MAE'])} with the bump"
    return s


def study_recal(ctx: Context) -> Study:
    from dfs import prob_recal

    s = Study(
        "recal",
        "Recalibrating Hit3x% / Boom% / Bust%",
        "held-out Brier gain over the shown probabilities above 2 SE on a measure with none worse by 1 SE "
        "(`prob_recal.evaluate`); then behind a default-off config switch until Sam's OK",
    )
    frame = prob_recal.load_frame(ctx.season)
    frame = frame[frame["week"] <= ctx.through]
    top = int(frame["week"].max())
    plans = {
        f"split (fit 1-{top - 2}, test {top - 1}-{top})": [(list(range(1, top - 1)), [top - 1, top])],
        f"rolling (weeks 3-{top})": [p for p in prob_recal.ROLLING_PLAN if p[1][0] <= top],
    }
    lines, ship = [], []
    for label, plan in plans.items():
        if top < 4 or not plan:
            continue
        res = prob_recal.evaluate(frame, plan)
        lines += [
            "",
            f"**{label}:** {res['n']} held-out rows; Brier as shown "
            + ", ".join(f"{m} {v:.4f}" for m, v in res["old"].items()),
        ]
        rows = []
        for kind, byc in res["kinds"].items():
            row = {"correction": kind}
            for m, v in byc.items():
                row[m] = f"{v['better']:+.4f} ({v['se']:.4f})"
                s.metrics[f"{label.split(' ')[0]} {kind} {m}"] = round(v["better"], 5)
            rows.append(row)
            clears = any(v["better"] > 2 * v["se"] for v in byc.values()) and all(
                v["better"] > -v["se"] for v in byc.values()
            )
            s.metrics[f"{label.split(' ')[0]} {kind} clears"] = bool(clears)
            if clears:
                ship.append(f"{kind} ({label.split(' ')[0]})")
        lines += _md_table(pd.DataFrame(rows), 3)
    s.headline = ["Old minus new Brier per measure, (SE); positive = the correction is better.", *lines]
    s.verdict = "clears: " + ", ".join(ship) if ship else "no correction beats the shown probabilities"
    return s


def tail_metrics(cov: pd.DataFrame, judged: pd.DataFrame) -> dict[str, float | bool]:
    """The numbers the tails report compares week to week: each position's share above the 90th percentile,
    and each role's pinball gain and whether it clears 2 SE."""
    out: dict[str, float | bool] = {}
    for _, r in cov.iterrows():
        out[f"{r['position']} >90"] = round(float(r[">90"]), 2)
    for _, r in judged.iterrows():
        out[f"{r['group']} pinball_better"] = round(float(r["pinball_better"]), 4)
        out[f"{r['group']} clears_2se"] = bool(r["clears_2se"])
    return out


def study_tails(ctx: Context) -> Study:
    from dfs import tail_check

    s = Study(
        "tails",
        "Top of the marginal tables (coverage; per-role stretch)",
        "a stretch of the upper quantiles that clears 2 SE on BOTH pinball loss and tail log-likelihood "
        f"held out (fit 2014-{tail_check.TRAIN_THROUGH}, judge {tail_check.TRAIN_THROUGH + 1}+); "
        "at position level none did, RB2 and WR4 (lower tops) did by role "
        "and are parked as value-column only",
    )
    frame = ctx.frame
    tables = tail_check.train_tables(frame)
    scored = tail_check.with_pit(frame, tables)
    held = scored[scored["season"] > tail_check.TRAIN_THROUGH]
    cov = tail_check.coverage(held, ("position",))
    fitted = tail_check.fit_adjustment(frame, tables, group="role")
    seasons = range(tail_check.TRAIN_THROUGH + 1, ctx.season + 1)
    judged = tail_check.judge(frame, tables, fitted, seasons, group="role")
    s.headline = (
        [
            "Share of actuals above the predicted percentile, "
            f"tables fitted 2014-{tail_check.TRAIN_THROUGH}, "
            f"rows {tail_check.TRAIN_THROUGH + 1}-{ctx.season} (nominal 10 / 5 / 1):"
        ]
        + _md_table(cov[["position", "n", ">90", "z90", ">95", ">99"]], 2)
        + ["", "Per-role stretch of the top (positive `better` = adjusted is better):"]
        + _md_table(
            judged[
                [
                    "group",
                    "n",
                    "delta",
                    "pinball_better",
                    "pinball_se",
                    "tail_ll_better",
                    "tail_ll_se",
                    "clears_2se",
                ]
            ],
            3,
        )
    )
    s.metrics.update(tail_metrics(cov, judged))
    clears = [r.group for r in judged.itertuples() if r.clears_2se and r.group != "All"]
    s.verdict = "clears 2 SE: " + ", ".join(clears) if clears else "no stretch clears 2 SE"
    return s


def ownership_metrics(summary: pd.DataFrame, gaps: pd.DataFrame) -> dict[str, float | bool]:
    """MAE and chalk bias per format and model, whether `Ours` beats TFFB's Own% on MAE per format, and the
    share of the CalPts-TFFB gap that came true."""
    out: dict[str, float | bool] = {}
    for _, r in summary.iterrows():
        key = f"{r['Format']} {r['Model']}"
        out[f"{key} MAE"] = round(float(r["MAE"]), 4)
        if np.isfinite(r["Chalk bias"]):
            out[f"{key} chalk bias"] = round(float(r["Chalk bias"]), 4)
    for fmt in summary["Format"].unique():
        pick = summary[summary["Format"] == fmt].set_index("Model")["MAE"]
        if {"Ours", "TFFB Own%"} <= set(pick.index):
            out[f"{fmt} ours beats TFFB"] = bool(pick["Ours"] < pick["TFFB Own%"])
    if (
        not gaps.empty
        and (gaps["Week"] == "All").any()
        and np.isfinite(gaps.loc[gaps["Week"] == "All", "Gap share"].iloc[0])
    ):
        out["gap share"] = round(float(gaps.loc[gaps["Week"] == "All", "Gap share"].iloc[0]), 3)
    return out


def study_ownership(ctx: Context) -> Study:
    from dfs import field_own
    from dfs.sim import dups

    s = Study(
        "ownership",
        "Field-ownership shadow (GppOwn / CashOwn / OurOwn)",
        "a shadow: used by nothing until Sam says so; the question each week is whether `Ours` beats TFFB's "
        "Own% (and the double-up curve for cash) on MAE and on the chalk it runs light on, out of sample",
    )
    frame, _counts = field_own.training_frame(ctx.season, through=ctx.through)
    if frame.empty:
        s.status, s.verdict = "skipped", "no contest standings yet"
        s.headline = ["No weeks with standings in the contest log."]
        return s
    scores = field_own.score_weeks(frame, curve=dups.double_up_ownership)
    summary = field_own.score_summary(scores)
    gaps = field_own.disagreement(frame, ctx.scored)
    s.headline = ["Mean over the scored weeks, ownership points (0.05 = 5 points):"] + _md_table(summary, 4)
    if not gaps.empty:
        s.headline += [
            "",
            "When CalPts and TFFB disagreed, the share of the gap that came true:",
        ] + _md_table(gaps[gaps["Week"] == "All"] if (gaps["Week"] == "All").any() else gaps, 3)
    s.metrics.update(ownership_metrics(summary, gaps))
    beats = [f for f in field_own.FORMATS if s.metrics.get(f"{f} ours beats TFFB")]
    s.verdict = (
        f"Ours beats TFFB on MAE in: {', '.join(beats) or 'neither format'} ({summary['Weeks'].max()} weeks)"
    )
    return s


def study_shadows(ctx: Context) -> Study:
    from dfs import calibration

    s = Study(
        "shadows",
        "ESPN and props shadow projections (Model Check SHADOW SOURCES)",
        "joins the CalPts blend after `shadow_min_weeks` (3) scored weeks of 20+ players, through the same "
        "weighting; the question is whether it beats CalPts on MAE and rank correlation "
        "on the players it rated",
    )
    policy = calibration.configured_shadow()
    progress = calibration.shadow_progress(ctx.scored, policy)
    race = calibration.shadow_race(ctx.scored, shadow=policy)
    lines = [
        f"{label}: {weeks} of {needed} scored weeks" + (" (in the blend)" if joined else "")
        for label, weeks, needed, joined in progress
    ]
    for label, weeks, _needed, _joined in progress:
        s.metrics[f"{label} weeks"] = weeks
    if race.empty:
        s.headline = [*lines, "", "No shadow source has rated a scored player yet."]
        s.verdict = "; ".join(lines)
        return s
    allrows = race[race["Position"] == "All"]
    s.headline = [*lines, "", *_md_table(allrows[["Rated by", "Source", "n", "Rho", "MAE", "Bias"]], 3)]
    for rated, g in allrows.groupby("Rated by"):
        g = g.set_index("Source")
        cal = g.loc["CalPts"]
        shadow = g[g.index.str.contains("shadow")].iloc[0]
        s.metrics[f"{rated} n"] = int(shadow["n"])
        s.metrics[f"{rated} MAE gain"] = round(float(cal["MAE"] - shadow["MAE"]), 3)
        s.metrics[f"{rated} Rho gain"] = round(float(shadow["Rho"] - cal["Rho"]), 3)
    better = [
        r
        for r in allrows["Rated by"].unique()
        if s.metrics.get(f"{r} MAE gain", 0) > 0 and s.metrics.get(f"{r} Rho gain", 0) > 0
    ]
    s.verdict = "; ".join(lines) + (
        f". Ahead of CalPts: {', '.join(better)}" if better else ". None ahead of CalPts"
    )
    return s


def props_metrics(table: pd.DataFrame) -> dict[str, float | bool]:
    """Per band of the "All markets" rows: scored player-markets, how often ESPN's favoured side won, the win
    rate the prices needed, the return per unit staked, and whether the verdict reads "beat the price"."""
    out: dict[str, float | bool] = {}
    allm = table[table["Market"] == "All markets"] if not table.empty else table
    for _, r in allm.iterrows():
        band = str(r["Band"])
        out[f"{band} n"] = int(r["n"])
        if r["n"]:
            out[f"{band} won"] = round(float(r["Won"]), 4)
            out[f"{band} needed"] = round(float(r["Needed"]), 4)
            out[f"{band} return"] = round(float(r["Return"]), 4)
        out[f"{band} beat the price"] = r["Verdict"] == "beat the price"
    return out


def study_props(ctx: Context) -> Study:
    from dfs import props_edge

    s = Study(
        "props",
        "PROPS SHADOW (ESPN against the prop market)",
        f"a verdict word only at {props_edge.MIN_N_VERDICT}+ scored player-markets in a cell; "
        '"beat the price" means ESPN\'s favoured side won more often than the price needed by more than '
        "2 SE. "
        "Analysis only: nothing here is a bet, and nothing in the sheet uses it",
    )
    shadow = props_edge.build_shadow(props_edge.latest_stats_player(), season=ctx.season)
    rows = shadow.rows[shadow.rows["Week"] <= ctx.through] if not shadow.rows.empty else shadow.rows
    table = props_edge.band_table(rows)
    shadow = props_edge.Shadow(
        rows,
        table,
        shadow.fit,
        shadow.counts,
        sorted(int(w) for w in rows["Week"].unique()),
        shadow.fit_weeks,
    )
    s.headline = [props_edge.headline(shadow), "", *props_edge.summary_lines(shadow)[:1]]
    allm = table[table["Market"] == "All markets"] if not table.empty else table
    if not allm.empty:
        s.headline += [""] + _md_table(allm[["Band", "n", "Won", "Needed", "Return", "Verdict"]], 3)
    s.metrics = props_metrics(table)
    s.verdict = props_edge.headline(shadow)
    return s


CONTEST_RETRO_MODULE = "dfs.sim.contest_retro"
CURSE_MODULE = "dfs.research.curse"
BACKTEST_MODULE = "dfs.backtest"
DST_MODULE = "dfs.model.dst_study"
HOOK_MODULES = {
    "contest_retro": CONTEST_RETRO_MODULE,
    "curse": CURSE_MODULE,
    "backtest": BACKTEST_MODULE,
    "dst": DST_MODULE,
}


def hook_study(ctx: Context, key: str, title: str) -> Study:
    """A study whose module exposes `refresh_summary(season, through)` (module docstring)."""
    name = HOOK_MODULES[key]
    s = Study(key, title, "defined by the module (`refresh_summary`'s `ship_bar`)")
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError:
        s.status = "skipped"
        s.verdict = f"{name} could not be imported"
        s.headline = [
            f"`{name}` could not be imported, so this study is skipped. It plugs in by exposing "
            '`refresh_summary(season, through) -> {"headline": [...], "metrics": {...}, "ship_bar": str}`.'
        ]
        return s
    hook = getattr(module, "refresh_summary", None)
    if hook is None:
        s.status = "skipped"
        s.verdict = f"{name} has no refresh_summary(season, through)"
        s.headline = [s.verdict + "; see `dfs.research.refresh` for the contract."]
        return s
    out = hook(ctx.season, ctx.through)
    s.headline, s.metrics = list(out.get("headline", [])), dict(out.get("metrics", {}))
    s.ship_bar = out.get("ship_bar", s.ship_bar)
    s.verdict = s.headline[0] if s.headline else "ran"
    return s


def study_contest_retro(ctx: Context) -> Study:
    return hook_study(ctx, "contest_retro", "Contest retro (session A)")


def study_curse(ctx: Context) -> Study:
    return hook_study(ctx, "curse", "Optimizer's curse (session F)")


def study_backtest(ctx: Context) -> Study:
    return hook_study(ctx, "backtest", "Flashback backtest (session J)")


def study_dst(ctx: Context) -> Study:
    return hook_study(ctx, "dst", "OurDST, our own DST projection (session O)")


STUDIES: dict[str, Callable[[Context], Study]] = {
    "shape": study_shape,
    "bump": study_bump,
    "recal": study_recal,
    "tails": study_tails,
    "ownership": study_ownership,
    "shadows": study_shadows,
    "props": study_props,
    "contest_retro": study_contest_retro,
    "curse": study_curse,
    "backtest": study_backtest,
    "dst": study_dst,
}
OPTIONAL = frozenset({"backtest"})  # in STUDIES, but run only when asked for (`--only backtest`)
TITLES = {
    "shape": "Five QB1-receiver pairs",
    "bump": "Teammate bump shadow",
    "recal": "Probability recalibration",
    "tails": "Top of the marginal tables",
    "ownership": "Field-ownership shadow",
    "shadows": "ESPN / props shadows",
    "props": "PROPS SHADOW",
    "contest_retro": "Contest retro",
    "curse": "Optimizer's curse",
    "backtest": "Flashback backtest",
    "dst": "OurDST",
}


def run_studies(
    ctx: Context, only: list[str] | None = None, log: Callable[[str], None] = lambda m: None
) -> list[Study]:
    """Run each study, timing it; a study that raises is reported as failed and the rest still run."""
    keys = only or [k for k in STUDIES if k not in OPTIONAL]
    unknown = [k for k in keys if k not in STUDIES]
    if unknown:
        raise ValueError(f"unknown studies {unknown}; choose from {', '.join(STUDIES)}")
    out = []
    for key in keys:
        log(f"{key} ...")
        start, before = time.perf_counter(), ctx.load_seconds
        try:
            study = STUDIES[key](ctx)
        except Exception as e:  # noqa: BLE001 - one broken study must not stop the others
            study = Study(key, TITLES[key], "n/a", status="failed", verdict=f"{type(e).__name__}: {e}")
            study.note = traceback.format_exc(limit=4)
            study.headline = [f"FAILED: {type(e).__name__}: {e}"]
        # time spent building the shared inputs is reported once, under the shared line, not per study
        study.seconds = time.perf_counter() - start - (ctx.load_seconds - before)
        out.append(study)
    return out


# --- the report --------------------------------------------------------------------------------------------


def _json_number(o):
    return bool(o) if isinstance(o, np.bool_) else float(o)


def report_path(season: int, through: int, directory: Path | None = None, partial: bool = False) -> Path:
    """The week's report; a run of only some studies (`--only`) writes `<season>-wk<NN>-partial.md` so it
    never replaces a full report (and is never taken as "last week's" by the comparison)."""
    return (directory or REPORT_DIR) / f"{season}-wk{through:02d}{'-partial' if partial else ''}.md"


def previous_report(season: int, through: int, directory: Path | None = None) -> tuple[Path, dict] | None:
    """The newest report of the season for a week before `through`, with the metrics it embeds."""
    directory = directory or REPORT_DIR
    found = []
    for path in directory.glob(f"{season}-wk*.md") if directory.exists() else []:
        match = re.search(r"-wk(\d+)\.md$", path.name)
        if match and int(match.group(1)) < through:
            found.append((int(match.group(1)), path))
    for _week, path in sorted(found, reverse=True):
        metrics = read_metrics(path)
        if metrics is not None:
            return path, metrics
    return None


def read_metrics(path: Path) -> dict | None:
    match = re.search(rf"<!-- {METRICS_TAG} (.*?) -->", path.read_text(), flags=re.S)
    return json.loads(match.group(1)) if match else None


def changes(old: dict[str, float | bool], new: dict[str, float | bool]) -> list[str]:
    """What moved between two metric dicts: flipped flags first, then the numbers that moved by both
    `CHANGE_MIN_ABS` and `CHANGE_MIN_REL`, biggest relative move first."""
    flips, moves = [], []
    for key, value in new.items():
        before = old.get(key)
        if before is None or value is None:
            if key not in old and value is not None:
                moves.append(
                    (
                        np.inf,
                        f"{key}: new ({_fmt(float(value), 3) if not isinstance(value, bool) else value})",
                    )
                )
            continue
        if isinstance(value, bool) or isinstance(before, bool):
            if bool(value) != bool(before):
                flips.append(f"**{key}: {before} -> {value}**")
            continue
        delta = float(value) - float(before)
        rel = abs(delta) / max(abs(float(before)), 1e-9)
        if abs(delta) >= CHANGE_MIN_ABS and rel >= CHANGE_MIN_REL:
            moves.append((rel, f"{key}: {_num(before)} -> {_num(value)}"))
    moves.sort(key=lambda m: -m[0])
    return flips + [text for _rel, text in moves][: max(CHANGE_LIMIT - len(flips), 0)]


def render(
    studies: list[Study], season: int, through: int, load_seconds: float, previous: tuple[Path, dict] | None
) -> str:
    total = sum(s.seconds for s in studies) + load_seconds
    lines = [
        f"# Research refresh: {season} through Week {through}",
        "",
        f"Generated {date.today().isoformat()}; {len(studies)} studies in {total:.0f} s "
        f"({load_seconds:.0f} s of it loading the shared history and out-of-fold frame). "
        "Offline: nothing was "
        "written to a sheet. Compared against "
        + (f"`{previous[0].name}`." if previous else "no earlier report (this is the first)."),
        "",
        "| Study | Status | Time | Verdict |",
        "|---|---|---|---|",
    ]
    for s in studies:
        lines.append(f"| {s.title} | {s.status} | {s.seconds:.0f} s | {s.verdict} |")
    for i, s in enumerate(studies, start=1):
        lines += ["", f"## {i}. {s.title}", "", f"**Ship bar:** {s.ship_bar}.", "", *s.headline, ""]
        if s.status == "ok" and s.metrics:
            if previous:
                prior = previous[1].get(s.key)
                moved = changes(prior, s.metrics) if prior else None
                if moved is None:
                    lines.append("**Changed since last week:** this study is new in this report.")
                elif moved:
                    lines += ["**Changed since last week:**", *[f"- {m}" for m in moved]]
                else:
                    lines.append("**Changed since last week:** nothing moved by more than 5%.")
            else:
                lines.append("**Changed since last week:** no earlier report.")
        elif s.status != "ok":
            lines.append(f"**Changed since last week:** not compared ({s.status}).")
    metrics = {s.key: s.metrics for s in studies if s.status == "ok"}
    lines += [
        "",
        f"<!-- {METRICS_TAG} {json.dumps(metrics, default=_json_number)} -->",
        "",
    ]
    return "\n".join(lines)


def summary_lines(
    studies: list[Study], load_seconds: float, path: Path, season: int, through: int
) -> list[str]:
    """The lines the command prints: a header (with the shared load), one line per study, the report."""
    total = sum(s.seconds for s in studies) + load_seconds
    out = [
        f"research refresh {season} through Week {through}: {total:.0f} s in all "
        f"({load_seconds:.0f} s loading the shared history and out-of-fold frame)"
    ]
    for s in studies:
        out.append(f"  {s.key:<13} {s.status:<7} {s.seconds:>4.0f} s  {s.verdict}")
    out.append(f"  report: {path}")
    return out


def refresh(
    season: int,
    through: int,
    *,
    shuffles: int = 40,
    only: list[str] | None = None,
    directory: Path | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> tuple[Path, list[Study], float]:
    """Run the studies and write the report. Returns (path, studies, seconds spent loading shared inputs)."""
    ctx = Context(season, through, shuffles)
    studies = run_studies(ctx, only, log)
    path = report_path(season, through, directory, partial=only is not None)
    text = render(studies, season, through, ctx.load_seconds, previous_report(season, through, directory))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, studies, ctx.load_seconds
