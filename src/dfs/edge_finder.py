"""Edge Finder: everything the sync adds to EdgeRaw beyond the base edge frame, and the saved outputs the
`Edge Finder` tab and the Board panel read.

`enrich(frame, ctx)` takes the edge frame (`derived.build_edge_frame`'s output) and returns it with the
Edge Finder columns filled: `CalPts`, `Hit3x%`, `Boom%`, `Bust%`, `Floor`, `CeilM`, `xFP/G`, `Edge` and the
two hidden percentile helpers. It also writes the signals archive (`data/signals/`) and the tab's source
tables (`data/current/edge_finder/`).

**Fail soft, column by column.** Each input is optional: no results history means `CalPts` is AggPts (Week
1) or blank; no ffopportunity blanks `xFP/G` and
the context tokens; no depth chart falls back to with-or-without; no injuries file blanks the injury
list. An exception anywhere returns the frame unchanged (blank Edge Finder columns) and logs a warning:
the base EdgeRaw sync never fails over this.

**Full sync vs `--live`.** A full sync (`ctx.live` False) refreshes the cached season inputs. `--live`
reuses the cached inputs, never re-downloading history. (UM is no longer inferred here: CalPts is the TFFB /
Sleeper / FantasyPros blend, and UM is scored only in Model Check.)

**No lookahead.** Everything is cut at the slate's week (`signals.build_signals`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from dfs import calibration, perf, results_loop, results_signals, signals, signals_data, store
from dfs.derived import (
    EDGE_FINDER_VALUE_COLUMNS,
    _rosterable_pool_mask,
    attach_edge_finder_columns,
)
from dfs.kickoff import kickoff_utc
from dfs.log import get_logger
from dfs.paths import CURRENT_DIR
from dfs.sources.base import SyncContext

log = get_logger("edge_finder")

OUTPUT_DIR = CURRENT_DIR / "edge_finder"
PLAYERS_FILE = "players.csv"
BENEFICIARIES_FILE = "beneficiaries.csv"
ABSENCES_FILE = "absences.csv"
MATCHUPS_FILE = "matchups.csv"
OUTS_FILE = "outs.csv"
STATUS_FILE = "status.json"


def _try_current(name: str) -> pd.DataFrame | None:
    try:
        return store.load_current(name)
    except FileNotFoundError:
        return None


def dk_level_frame(frame: pd.DataFrame, pool: pd.Series) -> pd.DataFrame:
    """The edge frame reshaped for `signals.build_signals` / the calibration: `Id`, `Name`, `Team`, `Opp`,
    `Position`, `Salary`, `ProjPts`, `AggPts`, `Avail`, `GameStart`, `RosterablePool`, plus Sleeper's and
    FantasyPros' own projections (the ensemble needs them separately; the edge frame keeps only AggPts)."""
    df = pd.DataFrame(
        {
            "Id": frame["Id"],
            "Name": frame["Name"],
            "Team": frame["Team"],
            "Opp": frame["Opp"],
            "Position": frame["Position"],
            "Salary": frame["Salary"],
            "ProjPts": frame["ProjPts"],
            "AggPts": frame["AggPts"],
            "Avail": frame["Avail"],
            "GameStart": frame["GameStart"],
            "RosterablePool": pool.to_numpy(),
        }
    )
    inputs = SimpleNamespace(sleeper=_try_current("sleeper"), fantasypros=_try_current("fantasypros"))
    extra = results_loop.source_projection_columns(df, inputs)
    return df.merge(extra, on="Id", how="left")


def depth_cutoff(frame: pd.DataFrame, now: datetime) -> str:
    """The depth chart's as-of stamp: now, or the slate's first kickoff if that has already passed (a late
    `sync --live` must not read a chart published after the games started)."""
    starts = (
        kickoff_utc(frame["GameStart"]).dropna()
        if "GameStart" in frame.columns
        else pd.Series(dtype="object")
    )
    first = starts.min() if len(starts) else None
    moment = now if first is None or pd.isna(first) or now < first.to_pydatetime() else first.to_pydatetime()
    return signals_data.iso_of(moment)


def enrich(
    frame: pd.DataFrame,
    ctx: SyncContext,
    *,
    now: datetime | None = None,
    scored: pd.DataFrame | None = None,
    data: signals.SeasonData | None = None,
    write: bool = True,
) -> pd.DataFrame:
    """The edge frame with the Edge Finder columns filled; the frame unchanged (blank columns) on any
    failure. `scored`/`data` are injectable for tests; `write` False skips every file write."""
    now = now or datetime.now(UTC)
    try:
        return _enrich(frame, ctx, now=now, scored=scored, data=data, write=write)
    except Exception as e:  # noqa: BLE001 - the base EdgeRaw sync must never fail over this
        log.warning("Edge Finder columns skipped: %s", e)
        return frame


def _enrich(
    frame: pd.DataFrame,
    ctx: SyncContext,
    *,
    now: datetime,
    scored: pd.DataFrame | None,
    data: signals.SeasonData | None,
    write: bool,
) -> pd.DataFrame:
    from dfs import results_update

    notes: list[str] = []
    if data is None:
        with perf.phase("edge finder: season inputs"):
            data, data_notes = signals_data.load_current_data(ctx.season, refresh=not ctx.live)
        notes += data_notes
    if scored is None:
        scored = results_update.load_all_scored(ctx.season)

    pool = _rosterable_pool_mask(pd.to_numeric(frame["ProjPts"], errors="coerce"), frame["Position"])
    df = dk_level_frame(frame, pool)
    df, join = signals.attach_gsis(
        df, signals.identity_for(data, depth_cutoff(df, now)), crosswalk=read_gsis_crosswalk()
    )

    with perf.phase("edge finder: CalPts"):
        fitted = calibration.fit(
            scored, before_week=ctx.week, season=ctx.season, sources=calibration.CAL_SOURCES
        )
        df["CalPts"] = calibration.predict(df, fitted)

    projections = _try_current("projections")
    dk_snaps = results_signals.week_snapshots("draftkings", ctx.week, ctx.season, now)
    proj_snaps = results_signals.week_snapshots("projections", ctx.week, ctx.season, now)
    with perf.phase("edge finder: signals and probabilities"):
        output = signals.build_signals(
            df,
            data,
            week=ctx.week,
            depth_dt=depth_cutoff(df, now),
            team_metrics=_try_current("pbp"),
            implied=signals.implied_by_team(projections) if projections is not None else None,
            dk_snapshots=dk_snaps,
            projection_snapshots=proj_snaps,
        )
    extras = output.players.set_index("Id")[
        [c for c in EDGE_FINDER_VALUE_COLUMNS if c in output.players.columns]
    ]
    enriched = attach_edge_finder_columns(frame, extras)

    if write:
        _write_outputs(output, ctx, now, fitted, data, df, notes, join, enriched, proj_snaps)
    return enriched


def _write_outputs(
    output: signals.SignalOutput,
    ctx: SyncContext,
    now: datetime,
    fitted: calibration.Calibration,
    data: signals.SeasonData,
    df: pd.DataFrame,
    notes: list[str],
    join,  # noqa: ANN001 - JoinResult | None
    enriched: pd.DataFrame,
    proj_snaps: list[tuple[str, pd.DataFrame]],
) -> None:
    """The signals archive (`data/signals/`) and the tab's source tables (`data/current/edge_finder/`)."""
    signals_data.write_archive(output.players, ctx.season, ctx.week, now)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output.players.to_csv(OUTPUT_DIR / PLAYERS_FILE, index=False)
    output.beneficiaries.to_csv(OUTPUT_DIR / BENEFICIARIES_FILE, index=False)
    output.absences.to_csv(OUTPUT_DIR / ABSENCES_FILE, index=False)
    output.matchups.to_csv(OUTPUT_DIR / MATCHUPS_FILE, index=False)
    output.outs.to_csv(OUTPUT_DIR / OUTS_FILE, index=False)
    weeks = data.ffo_weeks
    through = None
    if weeks is not None and not weeks.empty:
        current = weeks[pd.to_numeric(weeks["season"], errors="coerce") == ctx.season]
        through = int(current["week"].max()) if not current.empty else None
    injuries_week = None
    if data.injuries is not None and not data.injuries.empty:
        inj = data.injuries[pd.to_numeric(data.injuries["season"], errors="coerce") == ctx.season]
        injuries_week = int(inj["week"].max()) if not inj.empty else None
    depth_dt = None
    if data.depth is not None and not data.depth.empty:
        depth_dt = str(depth_cutoff_latest(data.depth, depth_cutoff(df, now)))
    status = {
        "season": ctx.season,
        "week": ctx.week,
        "generated": signals_data.iso_of(now),
        "stats_through_week": through,
        "injury_report_week": injuries_week,
        "injury_report": injury_report_info(data, ctx),
        "depth_chart_dt": depth_dt,
        "projection_snapshot": proj_snaps[-1][0] if proj_snaps else None,
        "calpts_weeks": list(fitted.weeks),
        "calpts_sources": list(fitted.sources),
        "gsis_coverage": list(join_coverage(join)),
        "gsis_unmatched": list(join.unmatched_pool_names) if join is not None else [],
        "live": bool(ctx.live),
        "notes": notes,
    }
    (OUTPUT_DIR / STATUS_FILE).write_text(json.dumps(status, indent=2))


def injury_report_info(data: signals.SeasonData, ctx: SyncContext) -> dict:
    """What the nflverse injury report held for this slate's week: the source, how many rows, how many carry a
    final status (blank until teams publish Friday's report; before that the rows are practice reports only),
    and when this copy was fetched (the saved file's time). Shown on the Edge Finder tab so a thin report
    reads as a timing fact, not a bug."""
    from dfs.paths import CURRENT_DIR

    rows = with_status = 0
    if data.injuries is not None and not data.injuries.empty:
        inj = data.injuries
        mine = inj[
            (pd.to_numeric(inj["season"], errors="coerce") == ctx.season)
            & (pd.to_numeric(inj["week"], errors="coerce") == ctx.week)
        ]
        rows, with_status = len(mine), int(mine["report_status"].notna().sum())
    path = CURRENT_DIR / "nflverse_injuries.csv"
    fetched = (
        datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if path.exists()
        else None
    )
    return {
        "source": "nflverse-data release `injuries` (injuries_<season>.parquet)",
        "week": ctx.week,
        "rows": rows,
        "with_status": with_status,
        "fetched": fetched,
    }


def read_gsis_crosswalk() -> pd.DataFrame | None:
    """The usage join's DraftKings-id -> gsis map from this sync (`data/current/gsis_crosswalk.csv`), the
    second identity source after the depth chart. None when it does not exist."""
    from dfs.paths import CURRENT_DIR

    path = CURRENT_DIR / "gsis_crosswalk.csv"
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"Id": str})
    except (OSError, ValueError, pd.errors.ParserError) as e:
        log.warning("gsis crosswalk unreadable (%s) -- identity from the depth chart and stats only", e)
        return None


def join_coverage(join) -> tuple[int, int]:  # noqa: ANN001
    """(matched, rosterable pool) of the gsis join, (0, 0) when there was none."""
    return (join.pool_matched, join.pool_total) if join is not None else (0, 0)


def depth_cutoff_latest(depth: pd.DataFrame, cutoff: str) -> str:
    """The `dt` of the depth-chart snapshot actually used at `cutoff`."""
    from dfs.injury_beneficiaries import latest_depth

    rows = latest_depth(depth, cutoff)
    return rows["dt"].iloc[0] if not rows.empty else ""
