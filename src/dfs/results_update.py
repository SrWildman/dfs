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
from datetime import UTC, datetime

import pandas as pd

from dfs import (
    calibration,
    field_own,
    props_edge,
    results_actual,
    results_loop,
    results_signals,
    signals_data,
    teammate_bump,
)
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheet_model_check import MODEL_CHECK_TAB, build_layout, write_model_check
from dfs.sheets import SheetsClient
from dfs.sim import dups
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
    write_signals: bool | None = None,
    signal_fetchers: signals_data.Fetchers | None = None,
) -> UpdateReport:
    """Run the whole update. Never raises for "stats not published yet": it returns a report with
    `published=False` instead."""
    fetchers = fetchers or Fetchers()
    if write_signals is None:
        write_signals = write_sheet  # an offline run (write_sheet=False) must not touch the network either
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
    if write_signals and all_weeks:
        _backfill_signals(
            scored,
            season,
            all_weeks,
            report,
            rebuild=set(weeks),
            offense=offense,
            dst=dst,
            schedule=game_scores,
            signal_fetchers=signal_fetchers,
        )
    if write_sheet:
        gs = cfg.google_sheets
        if sheet_id:
            gs = gs.model_copy(update={"sheet_id": sheet_id})
        client = SheetsClient(gs)
        title, _url = client.describe()
        signal_table = reliability = opportunity = None
        if write_signals:
            evaluation = results_signals.evaluation_frame(scored, season, _r6_history(season))
            signal_table = results_signals.signal_report(evaluation)
            _save_signal_table(signal_table, season)
            reliability = results_signals.reliability_report(evaluation)
            opportunity = _opportunity_report(season, evaluation)
            _save_opportunity_table(opportunity, season)
        ownership, ownership_gaps = _refit_field_own(season, scored, report)
        layout = build_layout(
            scored,
            weeks=all_weeks,
            signal_table=signal_table,
            reliability=reliability,
            ownership=ownership,
            ownership_gaps=ownership_gaps,
            props_shadow=_props_shadow(season, stats_player, report),
            teammate_race=_teammate_bump_race(season, scored),
            opportunity=opportunity,
        )
        with client.batched():  # the header notes are one request each; send them together
            summary = write_model_check(client, layout, MODEL_CHECK_TAB)
        report.say(f"{summary} on {title}")
        _slot_into_tab_strip(client)
    return report


def signal_table_path(season: int):
    """Where the Model Check signal records are kept for the Player card (`player_card.record_line`)."""
    return results_loop.RESULTS_DIR / f"signal_table_{season}.csv"


def _save_signal_table(table: pd.DataFrame, season: int) -> None:
    """Keep the signal records so the Player card can quote them at every sync without rebuilding them
    (the R6 history takes minutes). A failed save never fails the update."""
    try:
        results_loop.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        table.assign(AsOf=datetime.now(UTC).strftime("%Y-%m-%d")).to_csv(
            signal_table_path(season), index=False
        )
    except OSError as e:
        log.warning("signal records not saved for the Player card: %s", e)


def opportunity_table_path(season: int):
    """Where the opportunity-per-dollar record is kept for the Edge Finder's PRICE GAP line."""
    return results_loop.RESULTS_DIR / f"opportunity_table_{season}.csv"


def _save_opportunity_table(table: pd.DataFrame | None, season: int) -> None:
    """Keep the opportunity record so the Edge Finder can quote it at every sync. A failed save never fails
    the update; a report that could not be built leaves the last saved one."""
    if table is None or table.empty:
        return
    try:
        results_loop.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        table.assign(AsOf=datetime.now(UTC).strftime("%Y-%m-%d")).to_csv(
            opportunity_table_path(season), index=False
        )
    except OSError as e:
        log.warning("opportunity record not saved for the Edge Finder: %s", e)


def _props_shadow(season: int, stats_player: pd.DataFrame, report: UpdateReport) -> props_edge.Shadow | None:
    """Model Check's PROPS SHADOW from the saved pulls and ESPN snapshots (no network, no Odds API credits).
    Fail soft: None leaves the section out."""
    try:
        shadow = props_edge.build_shadow(stats_player, season=season)
    except Exception as e:  # noqa: BLE001 - never fail a results update over a shadow
        log.warning("props shadow skipped: %s", e)
        report.say(f"Props shadow skipped ({e}); Model Check's PROPS SHADOW will be missing.")
        return None
    report.say(f"Props shadow: {props_edge.headline(shadow)}")
    return shadow


def _refit_field_own(
    season: int, scored: pd.DataFrame, report: UpdateReport
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """The weekly refit of the ownership shadow (`field_own`): fit on every week with standings, save the
    model for the next sync, and return Model Check's two tables. Fail soft: (None, None) leaves them out."""
    try:
        frame, counts = field_own.training_frame(season)
        if frame.empty:
            return pd.DataFrame(), pd.DataFrame()
        model = field_own.fit(frame, counts)
        if model is not None:
            field_own.save(model)
            report.say(
                f"Ownership shadow refitted on weeks {', '.join(str(w) for w in model.weeks)} "
                f"(GPP k {model.exponent['gpp']:.2f}, cash k {model.exponent['cash']:.2f})."
            )
        scores = field_own.score_weeks(frame, curve=dups.double_up_ownership)
        return field_own.score_summary(scores), field_own.disagreement(frame, scored)
    except Exception as e:  # noqa: BLE001 - never fail a results update over a shadow
        log.warning("ownership shadow skipped: %s", e)
        report.say(f"Ownership shadow skipped ({e}); Model Check's ownership tables will be missing.")
        return None, None


def _teammate_bump_race(season: int, scored: pd.DataFrame) -> pd.DataFrame | None:
    """The teammate-bump shadow's Model Check rows (`calibration.teammate_bump_race`). Fail soft: None (no
    row) when the nflverse cache is missing or anything in the join breaks."""
    try:
        return calibration.teammate_bump_race(scored, teammate_bump.season_roles(season))
    except Exception as e:  # noqa: BLE001 - never fail a results update over a shadow
        log.warning("teammate bump shadow skipped: %s", e)
        return None


def _opportunity_report(season: int, evaluation: pd.DataFrame) -> pd.DataFrame | None:
    """Opportunity per dollar scored against the scored weeks (`results_signals.opportunity_report`), every
    feature built from games before its own week. None when the saved ffopportunity weeks cannot be read, so
    Model Check simply has no such section yet."""
    from dfs import opportunity

    try:
        data, _notes = signals_data.load_current_data(season, refresh=False)
        if data.ffo_weeks is None or data.ffo_weeks.empty:
            return None
        snaps, pbp, players = opportunity.read_research_frames(season, [])
        games = opportunity.player_games(data.ffo_weeks, snaps, pbp, players)
        frame = results_signals.attach_opportunity(evaluation, games, season)
        return results_signals.opportunity_report(frame)
    except (OSError, ValueError, KeyError) as e:
        log.warning("Opportunity per dollar skipped in Model Check: %s", e)
        return None


def _r6_history(season: int):
    """The R6 signals each scored player-game would have shown (cached inputs only; None when they are not
    there, so Model Check simply has no R6 rows yet)."""
    from dfs import usage_r6

    try:
        return usage_r6.history_flags([season - 1, season])
    except (usage_r6.R6Error, usage_r6.ResearchConstantsError, OSError, ValueError, KeyError) as e:
        log.warning("R6 rows skipped in Model Check: %s", e)
        return None


def _backfill_signals(
    scored: pd.DataFrame,
    season: int,
    all_weeks: list[int],
    report: UpdateReport,
    *,
    rebuild: set[int],
    offense: pd.DataFrame,
    dst: pd.DataFrame,
    schedule: pd.DataFrame,
    signal_fetchers: signals_data.Fetchers | None,
) -> None:
    """Fill in `UmPts` on every scored week that lacks it, then write the signals archive for every scored
    week that lacks one, plus any week just (re)scored. If UM was newly attached anywhere, every week is
    rebuilt: a later week's `CalPts` is fitted on the earlier weeks, so its archive changes with them.

    Fail soft: whatever cannot be built is reported and the rest of the update carries on, because the
    signals are context and `dfs week close` must never fail over them."""
    try:
        data, notes = signals_data.load_season_data(
            season, fetchers=signal_fetchers, offense_actual=offense, dst_actual=dst, schedule=schedule
        )
        for note in notes:
            report.say(f"Signals input unavailable: {note}")
        scored, attached = _attach_um_to_scored_files(scored, season, all_weeks, rebuild, data, report)
        have = {w for w in all_weeks if signals_data.archive_files(season, w)}
        todo = all_weeks if attached else [w for w in all_weeks if w not in have or w in rebuild]
        if not todo:
            report.say("Signals: every scored week already has an archive.")
            return
        written = results_signals.backfill(scored, data, season=season, weeks=todo)
    except Exception as e:  # noqa: BLE001 - never fail a results update over the signals
        log.warning("signals backfill failed: %s", e)
        report.say(f"Signals backfill failed ({e}); Model Check's signal tables will be missing.")
        return
    for week, name in written.items():
        report.say(f"Signals: week {week} archived as {name}.")


def _attach_um_to_scored_files(
    scored: pd.DataFrame,
    season: int,
    all_weeks: list[int],
    rebuild: set[int],
    data: signals_data.SeasonData,
    report: UpdateReport,
) -> tuple[pd.DataFrame, list[int]]:
    """Compute `UmPts` for each scored week that has none (or was just rescored) and rewrite its scored
    file with it. Returns (the scored table, the weeks that gained UM). A missing model cache (`dfs model
    fetch` not run) or any model error leaves UmPts blank and says so; CalPts then runs without UM."""
    attached = []
    todo = [
        w
        for w in all_weeks
        if w in rebuild
        or "UmPts" not in scored.columns
        or scored.loc[scored["week"] == w, "UmPts"].isna().all()
    ]
    if not todo:
        return scored, attached
    try:
        from dfs.model import data as model_data
        from dfs.model.predict import slate_history

        games = model_data.read_games()
        history = slate_history(season, fetch=False)
    except Exception as e:  # noqa: BLE001 - ModelDataError, a missing cache, a missing sklearn...
        report.say(f"UM unavailable ({e}); CalPts is built without it. Run `dfs model fetch`.")
        return scored, attached
    for week in todo:
        path = results_loop.scored_path(season, week)
        if not path.exists():
            continue
        rows = pd.read_csv(path)
        try:
            filled = results_signals.attach_um(
                rows, data, season=season, week=week, games=games, history=history
            )
        except Exception as e:  # noqa: BLE001
            report.say(f"UM for week {week} failed ({e}); left blank.")
            continue
        filled.to_csv(path, index=False)
        attached.append(week)
        report.say(f"Week {week}: UM projection attached to {int(filled['UmPts'].notna().sum())} players.")
    return (load_all_scored(season) if attached else scored), attached


def _slot_into_tab_strip(client: SheetsClient) -> None:
    """A brand-new `Model Check` tab lands at the end of the strip; the shared chrome pass puts it where
    `sheet_style.WEEK_ORDER` says (after Season) and colours it."""
    from dfs.sheet_style import apply_tab_chrome

    apply_tab_chrome(client, hide_staging=False)
