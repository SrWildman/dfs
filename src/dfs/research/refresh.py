"""`dfs research refresh`: the weekly reruns of the offline studies, in one command and one report.

Offline only: it reads the caches and `data/results`, `data/signals`, `data/contests`, writes nothing but
`docs/research/weekly/<season>-wk<NN>.md` (gitignored) and touches no sheet. Each study fails soft (one broken
study is reported and the rest still run), and the report says how long each took.

Studies, each with the bar it must clear to matter:

| key | study | module |
|---|---|---|
| `bump` | the teammate-bump shadow's Model Check race | `dfs.teammate_bump` |
| `ownership` | the field-ownership shadow's scores, where CalPts and TFFB disagree | `dfs.field_own` |
| `shadows` | ESPN and props shadow projections against CalPts (the blend gate) | `calibration.shadow_race` |
| `props` | the PROPS SHADOW: does ESPN beat the prop market's price | `dfs.props_edge.build_shadow` |
| `contest_retro` | the contest retro (hook below) | `dfs.sim.contest_retro` |
| `curse` | the optimizer's curse and its held-out fixes (hook below) | `dfs.research.curse` |
| `backtest` | Flashback: build settings scored in his real contests (optional: `--only`) | `dfs.backtest` |
| `du_line` | the calibrated double-up Cash % offset (saved for its switch) | `dfs.sim.du_line_retro` |

**The hook.** A study kept in its own module (`HOOK_MODULES`: the contest retro, the curse study, the
double-up line) plugs in by exposing

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
    """What the studies share, built on first use: the scored files. `through` is the last closed week to
    include."""

    def __init__(self, season: int, through: int):
        self.season, self.through = season, through
        self.load_seconds = 0.0
        self._scored = None

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
DU_LINE_MODULE = "dfs.sim.du_line_retro"
HOOK_MODULES = {
    "contest_retro": CONTEST_RETRO_MODULE,
    "curse": CURSE_MODULE,
    "backtest": BACKTEST_MODULE,
    "du_line": DU_LINE_MODULE,
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


def study_du_line(ctx: Context) -> Study:
    return hook_study(ctx, "du_line", "The double-up cash line: calibrated Cash % (session O)")


STUDIES: dict[str, Callable[[Context], Study]] = {
    "bump": study_bump,
    "ownership": study_ownership,
    "shadows": study_shadows,
    "props": study_props,
    "contest_retro": study_contest_retro,
    "curse": study_curse,
    "backtest": study_backtest,
    "du_line": study_du_line,
}
OPTIONAL = frozenset({"backtest"})  # in STUDIES, but run only when asked for (`--only backtest`)
TITLES = {
    "bump": "Teammate bump shadow",
    "ownership": "Field-ownership shadow",
    "shadows": "ESPN / props shadows",
    "props": "PROPS SHADOW",
    "contest_retro": "Contest retro",
    "curse": "Optimizer's curse",
    "backtest": "Flashback backtest",
    "du_line": "Double-up Cash % offset",
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
    only: list[str] | None = None,
    directory: Path | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> tuple[Path, list[Study], float]:
    """Run the studies and write the report. Returns (path, studies, seconds spent loading shared inputs)."""
    ctx = Context(season, through)
    studies = run_studies(ctx, only, log)
    path = report_path(season, through, directory, partial=only is not None)
    text = render(studies, season, through, ctx.load_seconds, previous_report(season, through, directory))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, studies, ctx.load_seconds
