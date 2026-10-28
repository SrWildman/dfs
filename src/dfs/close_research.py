"""`dfs week close --research`: Monday on autopilot -- the weekly offline studies after a close.

The close runs as it always did; then (each stage fail-soft, in its own process with its own log, bounded by
one overall deadline so an unattended run always ends):

| stage | what | module |
|---|---|---|
| `refresh` | `dfs research refresh --through <week>`: the weekly studies | `research.refresh` |
| `stacks` | `dfs stacks --retro`: the field refit and the stack ranking's track record | `sim.stacks_retro` |
| `backtest` | Flashback: `ownership_uncertainty` and `_gpp` against today, all weeks | `backtest` |
| `own_error` | `dfs.sim.own_error_sim_retro`: the sim's ownership draws | `sim.own_error_sim_retro` |

The settings the backtest compares live in the repo (`research/settings/*.toml`), not in a scratchpad.

The stages run as `python -m dfs.close_research stage <name>` children (a hung or crashed one cannot take the
others down; the parent kills its own process group at the deadline), write `<stage>.json` and `<stage>.log`
under `data/research/close/<season>-wk<NN>/`, and the parent folds them into ONE report,
`docs/research/weekly/<season>-wk<NN>-close.md` (gitignored): the close summary, each study's headline and
ship-bar verdict, and "changed since last week" against the previous close report. It prints a short summary
to paste to planning. Nothing here writes a sheet (the close itself did that before).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from dfs.paths import DATA_DIR, REPO_ROOT

REPORT_DIR = REPO_ROOT / "docs" / "research" / "weekly"
RUN_DIR = DATA_DIR / "research" / "close"
SETTINGS_DIR = REPO_ROOT / "research" / "settings"
METRICS_TAG = "dfs-close-research-metrics"
DEADLINE_MINUTES = 45.0
SUMMARY_LINES = 15
# One thread per numpy call in every stage: they share the cores and the backtest runs six workers itself.
THREAD_CAPS = {
    k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
}
OWN_ERROR_WORKERS = 4  # the four settings of the contest retro side by side, then the curse weeks
STAGE_NAMES = ("refresh", "stacks", "backtest", "own_error")
# The backtest compares each of these against `BASELINE` (files in `SETTINGS_DIR`).
BASELINE = "today"
CANDIDATES = ("ownership_uncertainty", "ownership_uncertainty_gpp")
# Longest first: the order they start in. All of them run at once (`run`): the machine has the cores, and
# the 45-minute budget is shorter than the stages run one after another.
STAGE_TITLES = {
    "refresh": "Weekly research reruns",
    "stacks": "Stacks: field refit and retro",
    "backtest": "Flashback: ownership uncertainty against today",
    "own_error": "Ownership uncertainty in the sim (held out by week)",
}


@dataclass
class StageResult:
    """One study's outcome as it goes into the report. `status` is ok, failed, skipped or timeout;
    `changed` is the study's own "changed since last week" lines (None: the parent compares `metrics` with
    the previous close report)."""

    key: str
    title: str
    status: str = "ok"
    verdict: str = ""
    ship_bar: str = ""
    headline: list[str] = field(default_factory=list)
    metrics: dict[str, float | bool] = field(default_factory=dict)
    changed: list[str] | None = None
    seconds: float = 0.0


# ---------------------------------------------------------------------------------------------------------
# The stages (each runs in its own process: `python -m dfs.close_research stage <name>`)
# ---------------------------------------------------------------------------------------------------------


_BEGAN = time.monotonic()


def _stamped(message: str) -> None:
    """A log line with the minutes since this stage began (the logs show where the time went)."""
    print(f"[{(time.monotonic() - _BEGAN) / 60:5.1f}m] {message}", flush=True)


def stage_refresh(season: int, week: int) -> list[StageResult]:
    """`dfs research refresh --through <week>`: every study of the weekly refresh as its own result, with
    the refresh's own comparison to its previous report."""
    from dfs.research import refresh

    path, studies, _load = refresh.refresh(season, week, log=_stamped)
    previous = refresh.previous_report(season, week)
    out = []
    for s in studies:
        result = StageResult(
            key=f"refresh.{s.key}",
            title=s.title,
            status=s.status,
            verdict=s.verdict,
            ship_bar=s.ship_bar,
            headline=list(s.headline),
            metrics=dict(s.metrics),
            seconds=s.seconds,
        )
        if s.status == "ok" and s.metrics:
            prior = previous[1].get(s.key) if previous else None
            result.changed = (
                (refresh.changes(prior, s.metrics) or ["nothing moved by more than 5%."])
                if prior
                else ["no earlier refresh report to compare."]
            )
        out.append(result)
    print(f"refresh report: {path}", flush=True)
    return out


def stage_stacks(season: int, week: int) -> list[StageResult]:
    """`dfs stacks --retro` without the sheet: refit the field model, rerun the retro, save its record."""
    from dfs.sim import stacks_retro

    payload = stacks_retro.refit_and_save(log=_stamped)
    z = payload.get("combined_z")
    verdict = str(payload.get("verdict", "?"))
    result = StageResult(
        key="stacks",
        title=STAGE_TITLES["stacks"],
        verdict=f"{verdict}" + (f" (combined z {z:.2f})" if isinstance(z, float) and z == z else ""),
        ship_bar=(
            "tested = the flagged stacks beat the same count of random candidate stacks (one-sided p < 0.05 "
            "combined over weeks) and in most weeks; weak = above the baseline in most weeks without that; "
            "an information view either way (`sim/stacks_retro.py`)"
        ),
        headline=[str(payload.get("line", ""))],
        metrics={"combined_z": round(float(z), 3) if isinstance(z, float) and z == z else 0.0}
        | {"tested": bool(payload.get("tested"))},
    )
    return [result]


def _baseline_pair(results, name_a: str, name_b: str):  # noqa: ANN001, ANN202 - backtest.Result
    from dfs import backtest as bt
    from dfs import backtest_report as br

    a, b = results[name_a], results[name_b]
    rows = pd.concat(
        [
            bt.rename_side(a.rows[a.rows["side"] == "backtest"], "A"),
            bt.rename_side(b.rows[b.rows["side"] == "backtest"], "B"),
        ]
    )
    table = bt.contest_table(rows)
    scope = "entry" if br.enters_differently(a) or br.enters_differently(b) else "any"
    paired = bt.paired_vs(table, "A", "B", scope, None)
    return paired, br.compare_lines(a, b), scope


def stage_backtest(season: int, week: int) -> list[StageResult]:
    """Flashback: each candidate settings file against `today`, on every week with standings up to `week`."""
    from dfs import backtest as bt
    from dfs import backtest_report as br
    from dfs import backtest_settings as bs

    settings = {n: bs.load(SETTINGS_DIR / f"{n}.toml") for n in (BASELINE, *CANDIDATES)}
    weeks = list(range(1, week + 1))
    results = bt.backtest(weeks, list(settings.values()), log=_stamped)
    out = []
    for name in CANDIDATES:
        paired, lines, scope = _baseline_pair(results, settings[name].name, settings[BASELINE].name)
        out.append(
            StageResult(
                key=f"backtest.{name}",
                title=f"Flashback: {settings[name].name} against today",
                verdict=lines[-1].split("Verdict on the ship bar", 1)[-1].lstrip(" :").strip()
                if lines and "Verdict on the ship bar" in lines[-1]
                else f"{paired['diff_week']:+.1%} ROI points by week",
                ship_bar=br.SHIP_BAR,
                headline=[f"Weeks {', '.join(map(str, results[settings[name].name].weeks))}.", "", *lines],
                metrics={
                    "weeks": paired["weeks"],
                    "contests": paired["contests"],
                    f"{scope} ROI diff by week": round(paired["diff_week"], 4),
                    f"{scope} ROI diff SE": round(paired["se_week"], 4),
                    "weeks won": paired["weeks_won"],
                },
            )
        )
    return out


def stage_own_error(season: int, week: int) -> list[StageResult]:
    """`python -m dfs.sim.own_error_sim_retro`: the sim's ownership uncertainty held out by week."""
    from dfs.research import refresh
    from dfs.sim import own_error_sim_retro as oesr

    tables = oesr.run(log=_stamped, workers=OWN_ERROR_WORKERS)
    cal, curse = tables["contest_calibration"], tables["curse_calibration"]
    metrics: dict[str, float | bool] = {}
    for _, r in cal.iterrows():
        metrics[f"{r['measure']} brier [{r['setting']}]"] = round(float(r["brier"]), 5)
    for _, r in curse.iterrows():
        metrics[f"{r['set']} cash gap [{r['setting']}]"] = round(float(r["gap"]), 4)
        metrics[f"{r['set']} brier [{r['setting']}]"] = round(float(r["brier_cash"]), 5)
    headline = ["Contest retro (his and the field's lineups in every real contest):", ""]
    headline += refresh._md_table(cal.round(4), 4)
    headline += ["", "Coverage of the lines (inside the 10-90% band):", ""]
    headline += refresh._md_table(tables["coverage"].round(3), 3)
    headline += ["", "Ranking inside a contest, setting minus today:", ""]
    headline += refresh._md_table(tables["ranking"].round(4), 4)
    headline += ["", "The curse study's builds rescored (cash shown minus cashed, Brier):", ""]
    headline += refresh._md_table(curse.round(4), 4)
    today = {k.split(" [")[0]: v for k, v in metrics.items() if k.endswith("[today]") and "brier" in k}
    draws = {k.split(" [")[0]: v for k, v in metrics.items() if k.endswith("[draws]") and "brier" in k}
    better = sum(1 for k in today if k in draws and draws[k] < today[k])
    return [
        StageResult(
            key="own_error",
            title=STAGE_TITLES["own_error"],
            verdict=f"draws beat today's Brier on {better} of {len(today)} measures (SEs: see the tables)",
            ship_bar=(
                "ships only if it beats today by more than 2 SE held out by week on Cash % Brier and does "
                "not worsen the GPP build ranking (docs/SIM.md, 'Ownership uncertainty in the sim'); "
                "until then default off"
            ),
            headline=headline,
            metrics=metrics,
        )
    ]


STAGE_FUNCTIONS: dict[str, Callable[[int, int], list[StageResult]]] = {
    "refresh": stage_refresh,
    "stacks": stage_stacks,
    "backtest": stage_backtest,
    "own_error": stage_own_error,
}


def run_stage(name: str, season: int, week: int, out: Path) -> int:
    """The child process: run one stage, write its results as JSON. Exit 0 when it wrote them (a result may
    still say `failed`), 1 when the stage itself raised."""
    start = time.perf_counter()
    try:
        results = STAGE_FUNCTIONS[name](season, week)
    except Exception as e:  # noqa: BLE001 - one stage must not take the others down
        traceback.print_exc()
        results = [
            StageResult(
                key=name,
                title=STAGE_TITLES[name],
                status="failed",
                verdict=f"{type(e).__name__}: {e}",
                headline=[f"FAILED: {type(e).__name__}: {e}"],
            )
        ]
    elapsed = time.perf_counter() - start
    for r in results:
        if not r.seconds:
            r.seconds = elapsed
    out.write_text(json.dumps([asdict(r) for r in results]))
    return 0 if all(r.status != "failed" for r in results) else 1


# ---------------------------------------------------------------------------------------------------------
# The parent: start the stages, enforce the deadline, fold the results into one report
# ---------------------------------------------------------------------------------------------------------


def run_dir(season: int, week: int) -> Path:
    return RUN_DIR / f"{season}-wk{week:02d}"


def close_text_path(season: int, week: int) -> Path:
    return run_dir(season, week) / "close.txt"


def save_close_text(season: int, week: int, text: str) -> Path:
    """What `dfs week close` printed, kept so the report (and a later standalone run) can quote it."""
    path = close_text_path(season, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _tail(path: Path, lines: int = 6) -> str:
    try:
        return " | ".join(path.read_text().strip().splitlines()[-lines:])[:400]
    except OSError:
        return ""


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop a stage and the workers it started: its own process group only (a new session each)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _stage_command(name: str, season: int, week: int, out: Path) -> list[str]:
    return [sys.executable, "-m", "dfs.close_research", "stage", name, "--season", str(season)] + [
        "--week",
        str(week),
        "--out",
        str(out),
    ]


def run_stages(
    season: int,
    week: int,
    stages: tuple[str, ...] = STAGE_NAMES,
    *,
    deadline_minutes: float = DEADLINE_MINUTES,
    log: Callable[[str], None] = print,
) -> list[StageResult]:
    """Start every stage at once, wait until they finish or the deadline passes (a stage still running is
    stopped and reported as a timeout), and return their results in `STAGE_NAMES` order."""
    directory = run_dir(season, week)
    directory.mkdir(parents=True, exist_ok=True)
    started: dict[str, tuple[subprocess.Popen, float, Path, Path]] = {}
    for name in stages:
        out, logfile = directory / f"{name}.json", directory / f"{name}.log"
        out.unlink(missing_ok=True)
        with logfile.open("w") as fh:
            proc = subprocess.Popen(  # noqa: S603 - our own interpreter and module
                _stage_command(name, season, week, out),
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=REPO_ROOT,
                env={**os.environ, **THREAD_CAPS},
                start_new_session=True,
            )
        started[name] = (proc, time.monotonic(), out, logfile)
        log(f"  started {name} (pid {proc.pid}, log {logfile})")
    deadline = time.monotonic() + deadline_minutes * 60
    finished: dict[str, float] = {}
    timed_out: set[str] = set()
    while len(finished) < len(started):
        for name, (proc, began, _out, _log) in started.items():
            if name not in finished and proc.poll() is not None:
                finished[name] = time.monotonic() - began
                log(f"  {name} finished in {finished[name] / 60:.1f} min (exit {proc.returncode})")
        if time.monotonic() > deadline:
            for name, (proc, began, _out, _log) in started.items():
                if name not in finished:
                    _kill_group(proc)
                    finished[name] = time.monotonic() - began
                    timed_out.add(name)
                    log(f"  {name} stopped at the {deadline_minutes:g}-minute deadline")
            break
        time.sleep(2)
    results: list[StageResult] = []
    for name in stages:
        _proc, _began, out, logfile = started[name]
        seconds = finished.get(name, 0.0)
        if name in timed_out:
            results.append(
                StageResult(
                    key=name,
                    title=STAGE_TITLES[name],
                    status="timeout",
                    verdict=f"stopped at the {deadline_minutes:g}-minute deadline",
                    headline=[f"Stopped at the deadline. Last log lines: {_tail(logfile)}"],
                    seconds=seconds,
                )
            )
            continue
        try:
            loaded = [StageResult(**r) for r in json.loads(out.read_text())]
        except (OSError, ValueError, TypeError):
            loaded = [
                StageResult(
                    key=name,
                    title=STAGE_TITLES[name],
                    status="failed",
                    verdict="no result written",
                    headline=[f"The stage ended without a result. Last log lines: {_tail(logfile)}"],
                    seconds=seconds,
                )
            ]
        results += loaded
    return results


# ---------------------------------------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------------------------------------


def report_path(season: int, week: int, directory: Path | None = None) -> Path:
    return (directory or REPORT_DIR) / f"{season}-wk{week:02d}-close.md"


def previous_report(season: int, week: int, directory: Path | None = None) -> tuple[Path, dict] | None:
    """The newest close report of the season for a week before `week`, with the metrics it embeds."""
    directory = directory or REPORT_DIR
    found = []
    for path in directory.glob(f"{season}-wk*-close.md") if directory.exists() else []:
        match = re.search(r"-wk(\d+)-close\.md$", path.name)
        if match and int(match.group(1)) < week:
            found.append((int(match.group(1)), path))
    for _w, path in sorted(found, reverse=True):
        metrics = read_metrics(path)
        if metrics is not None:
            return path, metrics
    return None


def read_metrics(path: Path) -> dict | None:
    match = re.search(rf"<!-- {METRICS_TAG} (.*?) -->", path.read_text(), flags=re.S)
    return json.loads(match.group(1)) if match else None


def fill_changes(results: list[StageResult], previous: tuple[Path, dict] | None) -> None:
    """Give every ok result without its own comparison the lines that moved since the previous report."""
    from dfs.research import refresh

    for r in results:
        if r.changed is not None or r.status != "ok" or not r.metrics:
            continue
        if previous is None:
            r.changed = ["no earlier close report."]
            continue
        prior = previous[1].get(r.key)
        if not prior:
            r.changed = ["new in this report."]
            continue
        r.changed = refresh.changes(prior, r.metrics) or ["nothing moved by more than 5%."]


def decisions_from_tab(client) -> list[str] | None:  # noqa: ANN001 - a SheetsClient
    """The Contests tab's THIS WEEK, YOUR DECISIONS lines (the five after the heading), or None when the
    tab or the block is not there. Read only."""
    from dfs import sheet_contests

    try:
        rows = client.read_tab(sheet_contests.CONTESTS_TAB)
    except Exception:  # noqa: BLE001 - a missing tab or a failed read just leaves the block out
        return None
    for i, row in enumerate(rows):
        if any(str(c).startswith("THIS WEEK, YOUR DECISIONS") for c in row):
            found = []
            for later in rows[i + 1 :]:
                text = " ".join(str(c).strip() for c in later if str(c).strip())
                if not text:
                    if found:
                        break
                    continue
                found.append(text)
                if len(found) == 5:
                    break
            return found or None
    return None


def _decisions_block(lines: list[str] | None) -> list[str]:
    if not lines:
        return ["", "*The Contests tab's decisions block was not read (no sheet in this run).*"]
    return ["", "**This week, your decisions** (the Contests tab):", "", *[f"- {line}" for line in lines]]


def render(
    results: list[StageResult],
    season: int,
    week: int,
    *,
    close_text: str | None,
    decisions: list[str] | None,
    previous: tuple[Path, dict] | None,
    wall_seconds: float,
) -> str:
    lines = [
        f"# Week {week} close: {season}",
        "",
        f"Generated {date.today().isoformat()}; {len(results)} studies in {wall_seconds / 60:.0f} min "
        "(the stages ran side by side). Nothing was written to a sheet by the studies. Compared against "
        + (f"`{previous[0].name}`." if previous else "no earlier close report (this is the first)."),
        "",
        "## The close",
        "",
    ]
    if close_text:
        lines += ["```", *close_text.strip().splitlines()[-120:], "```"]
    else:
        lines += ["*`dfs week close` was not run in this session; the studies were run on their own.*"]
    lines += _decisions_block(decisions)
    lines += ["", "## The studies", "", "| Study | Status | Time | Verdict |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.title} | {r.status} | {r.seconds / 60:.1f} min | {r.verdict} |")
    for i, r in enumerate(results, start=1):
        lines += ["", f"### {i}. {r.title}", ""]
        if r.ship_bar:
            lines += [f"**Ship bar:** {r.ship_bar}.", ""]
        lines += [*r.headline, ""]
        if r.changed:
            lines += (
                ["**Changed since last week:** " + r.changed[0]]
                if len(r.changed) == 1
                else ["**Changed since last week:**", *[f"- {c}" for c in r.changed]]
            )
        elif r.status != "ok":
            lines.append(f"**Changed since last week:** not compared ({r.status}).")
    metrics = {r.key: r.metrics for r in results if r.status == "ok"}
    lines += ["", f"<!-- {METRICS_TAG} {json.dumps(metrics, default=float)} -->", ""]
    return "\n".join(lines)


def summary_lines(
    results: list[StageResult],
    season: int,
    week: int,
    path: Path,
    wall_seconds: float,
    decisions: list[str] | None = None,
) -> list[str]:
    """The (at most) 15 lines Sam pastes to planning: one per study, then the decisions block, the report."""
    notes = [f"decision: {d}"[:200] for d in (decisions or [])[:5]]
    out = [f"Week {week} close research ({season}): {len(results)} studies, {wall_seconds / 60:.0f} min"]
    room = SUMMARY_LINES - 2 - len(notes)  # the header and the report line are always there
    if len(results) > room:
        room -= 1  # one line says how many more are in the report
    shown = results[: max(room, 0)]
    for r in shown:
        out.append(f"{r.key:<34} {r.status:<7} {r.verdict}"[:200])
    if len(results) > len(shown):
        out.append(f"... and {len(results) - len(shown)} more in the report")
    out += notes
    out.append(f"report: {path}")
    return out[:SUMMARY_LINES]


def run(
    season: int,
    week: int,
    *,
    close_text: str | None = None,
    decisions: list[str] | None = None,
    stages: tuple[str, ...] = STAGE_NAMES,
    deadline_minutes: float = DEADLINE_MINUTES,
    directory: Path | None = None,
    log: Callable[[str], None] = print,
) -> tuple[Path, list[StageResult], float]:
    """Run the stages, write the close report. Returns (the report's path, the results, wall seconds)."""
    start = time.monotonic()
    if close_text is None and close_text_path(season, week).exists():
        close_text = close_text_path(season, week).read_text()
    results = run_stages(season, week, stages, deadline_minutes=deadline_minutes, log=log)
    wall = time.monotonic() - start
    previous = previous_report(season, week, directory)
    fill_changes(results, previous)
    path = report_path(season, week, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render(
            results,
            season,
            week,
            close_text=close_text,
            decisions=decisions,
            previous=previous,
            wall_seconds=wall,
        )
    )
    return path, results, wall


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the child entry point
    parser = argparse.ArgumentParser(prog="dfs.close_research")
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("name", choices=STAGE_NAMES)
    stage.add_argument("--season", type=int, required=True)
    stage.add_argument("--week", type=int, required=True)
    stage.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_stage(args.name, args.season, args.week, args.out)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
