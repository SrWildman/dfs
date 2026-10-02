"""`dfs results update`: score every completed week that is not scored yet, then rebuild `Model Check`.

Order of work:

1. fetch nflverse's `stats_player`, `stats_team` and the schedule's final scores (snapshotted under
   `data/raw/`); if the stats are not published yet, say so and stop cleanly (exit 0 -- `dfs week close`
   runs this and must never fail over nflverse's one-to-two-day lag);
2. score actual DK points (`results_actual`);
3. for each completed week without a `data/results/scored_<season>_wNN.csv` (or `--week N`, or `--all`),
   rebuild that week's projections as of kickoff (`results_loop.score_week`), join actuals, and write the
   scored table plus an unmatched list;
4. read every scored table back and rebuild the `Model Check` tab from disk (`sheet_model_check`).

The sheet step is skipped with `write_sheet=False` (offline runs and tests). Nothing here is part of `dfs
sync`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from dfs import results_actual, results_loop
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheet_model_check import MODEL_CHECK_TAB, build_layout, write_model_check
from dfs.sheets import SheetsClient
from dfs.sources import nflverse_results

log = get_logger("results_update")


@dataclass
class UpdateReport:
    """What a run did, as printable lines; `published` is False when there was nothing to score yet."""

    lines: list[str] = field(default_factory=list)
    weeks_scored: list[int] = field(default_factory=list)
    published: bool = True

    def say(self, text: str) -> None:
        """Add one printable line to the report."""
        self.lines.append(text)


@dataclass
class Fetchers:
    """The three downloads, injectable so tests never touch the network."""

    stats_player: Callable[[int], pd.DataFrame] = nflverse_results.fetch_stats_player
    stats_team: Callable[[int], pd.DataFrame] = nflverse_results.fetch_stats_team
    game_scores: Callable[[int], pd.DataFrame] = nflverse_results.fetch_game_scores


def load_projection_snapshots() -> list[tuple]:
    """Every archived TFFB projection snapshot, oldest first (unreadable ones dropped)."""
    out = []
    for stamp, path in results_loop.snapshot_files("projections"):
        frame = results_loop.read_snapshot(path)
        if frame is not None:
            out.append((stamp, frame))
    return out


def load_all_scored(season: int) -> pd.DataFrame:
    """Concatenate every `scored_<season>_wNN.csv` on disk (empty frame if none)."""
    frames = []
    for path in sorted(results_loop.RESULTS_DIR.glob(f"scored_{season}_w*.csv")):
        frames.append(pd.read_csv(path))
    if not frames:
        return pd.DataFrame(columns=results_loop.SCORED_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def score_weeks(
    weeks: list[int],
    season: int,
    *,
    offense_actual: pd.DataFrame,
    dst_actual: pd.DataFrame,
    report: UpdateReport,
) -> None:
    """Score each week in `weeks`: write its scored table and unmatched list, and report coverage."""
    snapshots = load_projection_snapshots()
    results_loop.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for week in weeks:
        result = results_loop.score_week(
            week,
            season,
            projection_snapshots=snapshots,
            offense_actual=offense_actual,
            dst_actual=dst_actual,
        )
        if result is None:
            report.say(f"Week {week}: no archived projections or salary file to score against -- skipped.")
            continue
        result.scored.to_csv(results_loop.scored_path(season, week), index=False)
        missing = result.scored[
            (result.scored["Status"] == results_loop.STATUS_UNMATCHED)
            & (pd.to_numeric(result.scored["ProjPts"], errors="coerce") > 0)
        ]
        missing[["Id", "Name", "Position", "Team", "ProjPts", "RosterablePool"]].to_csv(
            results_loop.unmatched_path(season, week), index=False
        )
        report.weeks_scored.append(week)
        report.say(
            f"Week {week}: snapshot {result.reference:%Y-%m-%d %H:%M} UTC, "
            f"{result.snapshots_used} projection snapshot(s) used, sources as of then: "
            f"{', '.join(result.present_sources) or 'none'}."
        )
        for _, row in results_loop.coverage(result.scored).iterrows():
            report.say(
                f"  {row['Population']}: {row['Scored'] + row['DNP']} of {row['Players']} joined "
                f"({row['JoinedPct']}%): {row['Scored']} scored, {row['DNP']} did not play, "
                f"{row['Unmatched']} not found."
            )
        report.say(f"  unmatched list: {results_loop.unmatched_path(season, week)}")


def update_results(
    cfg: Config,
    *,
    season: int,
    week: int | None = None,
    rescore_all: bool = False,
    sheet_id: str | None = None,
    write_sheet: bool = True,
    fetchers: Fetchers | None = None,
) -> UpdateReport:
    """Run the whole update. Never raises for "stats not published yet": it returns a report with
    `published=False` instead."""
    fetchers = fetchers or Fetchers()
    report = UpdateReport()
    try:
        stats_player = fetchers.stats_player(season)
        stats_team = fetchers.stats_team(season)
        game_scores = fetchers.game_scores(season)
    except nflverse_results.ResultsFetchError as e:
        report.published = False
        label = f"Week {week}" if week else "The latest week"
        report.say(f"{label}: nflverse stats not published yet ({e}); run `dfs results update` later.")
        return report

    completed = results_loop.completed_weeks(game_scores)
    if week is not None:
        if week not in completed:
            report.published = False
            report.say(
                f"Week {week} is not complete yet (not every game has a final score); nothing to score."
            )
            return report
        weeks = [week]
    elif rescore_all:
        weeks = completed
    else:
        weeks = [w for w in completed if not results_loop.scored_path(season, w).exists()]
    offense = results_actual.score_offense_actual(stats_player)
    dst = results_actual.score_dst_actual(stats_team, stats_player, game_scores)
    if weeks:
        score_weeks(weeks, season, offense_actual=offense, dst_actual=dst, report=report)
    else:
        report.say("Every completed week is already scored (use --week N or --all to rescore).")

    scored = load_all_scored(season)
    all_weeks = sorted(int(w) for w in scored["week"].unique()) if not scored.empty else []
    if write_sheet:
        gs = cfg.google_sheets
        if sheet_id:
            gs = gs.model_copy(update={"sheet_id": sheet_id})
        client = SheetsClient(gs)
        title, _url = client.describe()
        layout = build_layout(scored, weeks=all_weeks)
        with client.batched():  # the header notes are one request each; send them together
            summary = write_model_check(client, layout, MODEL_CHECK_TAB)
        report.say(f"{summary} on {title}")
        _slot_into_tab_strip(client)
    return report


def _slot_into_tab_strip(client: SheetsClient) -> None:
    """A brand-new `Model Check` tab lands at the end of the strip; the shared chrome pass puts it where
    `sheet_style.WEEK_ORDER` says (after Season) and colours it."""
    from dfs.sheet_style import apply_tab_chrome

    apply_tab_chrome(client, hide_staging=False)
