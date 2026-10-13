"""`dfs contests analyze`, as a function: history exports + standings files in, CSVs and a summary out.

Writes only files under the output directory. No sheet, no credentials, no DraftKings network call; the only
network access is nflverse (public) for the slate index of a season not already in the committed backfill,
and that fails soft: the index is context (and one of the three week treatments compared), so a season
without it is left out of that comparison only, and the summary says so.

Output files (all in `out_dir`):

- `contest_log.csv`      the durable log (every history column + the derived ones), merged across runs
- `series.csv`           one row per (format, series): entries, weeks, typical field and payout
- `class_disagreements.csv`  rows where the payout-ratio band and the format disagree about cash vs GPP
- `profiles.csv`         the score profile of every standings contest (entrants, cash line, top 10%, ...)
- `field_stats.csv`, `field_stacks.csv`, `field_unmatched.csv`   how the fields build (see `field.py`)
- `lineup_errors.csv`    lineups that did not parse, with the reason
- `toughness_series.csv`, `toughness_formats.csv`, `toughness_weeks.csv`   the curves and the week effects
- `toughness_models.csv`, `toughness_k.csv`   the leave-one-week-out comparison of the three week
  treatments, and the penalty grid of the one used (see `toughness.py`)
- `h2h_bands.csv`, `h2h_opponents.csv`
- `slate_index.csv`      the per-week slate index: context beside the week effects, and the `index` comparator
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pandas as pd

from dfs.contests import field as field_mod
from dfs.contests import history, slate_index, standings, toughness
from dfs.contests import series as series_mod
from dfs.sources.nflverse_results import ResultsFetchError

LOG_NAME = "contest_log.csv"
NO_INDEX = "none"
ENTRANT_TOLERANCE = 0.01  # a standings file this far off the history's entry count is a partial export


@dataclass
class Analysis:
    out_dir: Path
    merges: list[tuple[str, history.MergeResult]] = field(default_factory=list)
    log: pd.DataFrame = field(default_factory=pd.DataFrame)
    typed: pd.DataFrame = field(default_factory=pd.DataFrame)
    series_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    disagreements: pd.DataFrame = field(default_factory=pd.DataFrame)
    scan: standings.Scan | None = None
    profiles: pd.DataFrame = field(default_factory=pd.DataFrame)
    lineup_errors: pd.DataFrame = field(default_factory=pd.DataFrame)
    field_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    field_stacks: pd.DataFrame = field(default_factory=pd.DataFrame)
    field_unmatched: pd.DataFrame = field(default_factory=pd.DataFrame)
    index: pd.DataFrame = field(default_factory=pd.DataFrame)
    index_candidate: str = ""
    toughness: toughness.ToughnessResult | None = None
    h2h_bands: pd.DataFrame = field(default_factory=pd.DataFrame)
    h2h_opponents: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    frame_sources: dict[tuple[int, int], str] = field(default_factory=dict)  # (season, week) -> snapshot used


def _median(values: pd.Series) -> float:
    """The median of a nullable column; NaN (not a crash) when every value is missing."""
    valid = values.dropna()
    return float(valid.astype("Float64").median()) if len(valid) else float("nan")


def series_summary(typed: pd.DataFrame) -> pd.DataFrame:
    """One row per (format, series) of the typed log."""
    rows = []
    for (fmt, name), sub in typed.groupby(["format", "series"], sort=False):
        rows.append(
            {
                "format": fmt,
                "series": name,
                "entries": len(sub),
                "weeks": int(sub[["season", "week"]].drop_duplicates().shape[0]),
                "first": sub["date"].min(),
                "last": sub["date"].max(),
                "median_fee": _median(sub["fee"]),
                "median_field": _median(sub["entries"]),
                "median_payout_ratio": _median(sub["payout_ratio"]),
                "net": float((sub["winnings"] - sub["fee"]).sum()),
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["format", "entries"], ascending=[True, False]).reset_index(drop=True)


def resolve_index(
    typed: pd.DataFrame,
    *,
    candidate: str,
    supplied: pd.DataFrame | None,
    fetch_season: Callable[[int], pd.DataFrame] | None,
    previous: pd.DataFrame | None,
    warnings: list[str],
) -> pd.DataFrame:
    """The `season, week, index` frame for every (season, week) in the log.

    `supplied` (a file the caller passed) wins; else the committed backfill covers `BACKFILL_SEASONS` and any
    other season is computed from nflverse through `fetch_season`. A season that cannot be fetched falls
    back to `previous` (the last run's `slate_index.csv`) and is reported as missing."""
    weeks = typed[["season", "week"]].dropna().drop_duplicates()
    if candidate == NO_INDEX:
        return pd.DataFrame(columns=["season", "week", "index"])
    if supplied is not None:
        return supplied[["season", "week", "index"]]
    seasons = sorted({int(s) for s in weeks["season"]})
    backfill = slate_index.load_backfill()
    have = set(backfill["season"].astype(int)) if len(backfill) else set()
    parts = []
    if len(backfill):
        parts.append(slate_index.slate_index(backfill[backfill["season"].isin(seasons)], candidate))
    for season in seasons:
        if season in have:
            continue
        try:
            if fetch_season is None:
                raise OSError("not fetched")
            parts.append(slate_index.slate_index(slate_index.with_relative(fetch_season(season)), candidate))
        except (OSError, httpx.HTTPError, ResultsFetchError, ValueError, KeyError) as e:
            detail = str(e).splitlines()[0] if str(e) else type(e).__name__
            warnings.append(
                f"slate index for {season} unavailable ({detail}); those weeks are left out of the index "
                "comparison only"
            )
            if previous is not None and len(previous) and set(previous.get("candidate", [])) == {candidate}:
                kept = previous[previous["season"] == season][["season", "week", "index"]]
                if len(kept):
                    warnings.append(
                        f"slate index for {season}: using the last run's values from slate_index.csv"
                    )
                    parts.append(kept)
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=["season", "week", "index"])
    return pd.concat(parts, ignore_index=True)


def _read_players(path: Path | None) -> pd.DataFrame | None:
    return None if path is None else pd.read_csv(path, dtype={"Name": str, "Team": str, "Position": str})


def run(
    history_files: Iterable[Path],
    out_dir: Path,
    *,
    standings_dir: Path | None = None,
    players: Path | pd.DataFrame | None = None,
    me: str = series_mod.MY_USERNAME,
    index_candidate: str = slate_index.SELECTED,
    index_file: Path | None = None,
    fetch_season: Callable[[int], pd.DataFrame] | None = slate_index.compute_season,
    frames: Callable[[int, int], tuple[pd.DataFrame | None, str]] | None = None,
) -> Analysis:
    out_dir = Path(out_dir)
    result = Analysis(out_dir=out_dir)
    log_path = out_dir / LOG_NAME

    for path in history_files:
        result.merges.append((str(path), history.merge_into(log_path, path, me=me)))
    result.log = history.read_log(log_path)
    result.typed = history.typed(result.log)
    result.series_table = series_summary(result.typed) if len(result.typed) else pd.DataFrame()
    result.disagreements = series_mod.crosscheck(result.log)

    frame = _read_players(players) if isinstance(players, Path) else players
    week_cache: dict[tuple[int, int], pd.DataFrame | None] = {}

    def players_for(season: int | None, week: int | None) -> pd.DataFrame | None:
        """The players frame of one contest's week: a supplied frame with `Season`/`Week` columns is cut to
        the contest's week; one without them is used as is; with none, `frames` builds the week's."""
        if frame is not None:
            if season is None or week is None or "Week" not in frame.columns:
                return frame
            keep = frame["Week"] == week
            if "Season" in frame.columns:
                keep &= frame["Season"] == season
            return frame[keep]
        if frames is None or season is None or week is None:
            return None
        if (season, week) not in week_cache:
            built, note = frames(season, week)
            week_cache[(season, week)] = built
            if built is None:
                result.warnings.append(f"no players frame for Week {week} {season}: {note}")
            else:
                result.frame_sources[(season, week)] = note
        return week_cache[(season, week)]

    log_by_contest = (
        result.typed.drop_duplicates("contest_key").set_index("contest_key") if len(result.typed) else None
    )
    profile_rows, error_rows, field_rows, stack_rows, miss_rows = [], [], [], [], []
    profile_inputs: list[tuple[standings.ScoreProfile, str, str, int, int]] = []

    if standings_dir is not None:
        result.scan = standings.scan_standings(standings_dir)
        for source in result.scan.sources:
            info = None
            if log_by_contest is not None and source.contest_id in log_by_contest.index:
                info = log_by_contest.loc[source.contest_id]
            else:
                result.warnings.append(
                    f"contest {source.contest_id}: no history row (cash line and week unknown)"
                )
            places_paid = None if info is None or pd.isna(info["places_paid"]) else int(info["places_paid"])
            try:
                parsed = standings.parse_standings(
                    source.raw, contest_id=source.contest_id, places_paid=places_paid
                )
            except standings.StandingsError as e:
                result.warnings.append(f"{source.origin}: {e}")
                continue
            for message in parsed.warnings:
                result.warnings.append(f"contest {source.contest_id}: {message}")
            prof = parsed.profile
            season = week = None
            fmt = name = ""
            if info is not None:
                fmt, name = str(info["format"]), str(info["series"])
                season = None if pd.isna(info["season"]) else int(info["season"])
                week = None if pd.isna(info["week"]) else int(info["week"])
            profile_rows.append(
                {
                    "contest_id": prof.contest_id,
                    "season": season,
                    "week": week,
                    "format": fmt,
                    "series": name,
                    "kind": parsed.kind,
                    "entrants": prof.entrants,
                    "places_paid": prof.places_paid,
                    "cash_line": prof.cash_line,
                    "top10": prof.top10,
                    "top1": prof.top1,
                    "win": prof.win,
                    "median": prof.median,
                    "complete": prof.complete,
                    "source": source.origin,
                }
            )
            usable = (
                season is not None
                and week is not None
                and fmt
                and info is not None
                and (parsed.kind == "Showdown" or info["slate"] == "main")
            )
            if usable and not prof.complete:
                result.warnings.append(
                    f"contest {source.contest_id}: still in play (time remaining on some entries), "
                    "its score profile is not used in the fit"
                )
                usable = False
            if usable and not pd.isna(info["entries"]):
                gap = abs(prof.entrants - int(info["entries"])) / max(int(info["entries"]), 1)
                if gap > ENTRANT_TOLERANCE:
                    result.warnings.append(
                        f"contest {source.contest_id}: the file has {prof.entrants} entries but the "
                        f"history says {int(info['entries'])}; a partial export, so its profile is not "
                        "used in the fit"
                    )
                    usable = False
            if usable:
                profile_inputs.append((prof, fmt, name, season, week))
            if len(parsed.lineup_errors):
                error_rows.append(parsed.lineup_errors.assign(contest_id=parsed.contest_id))
            stats = field_mod.field_stats(parsed, players_for(season, week), me=me)
            if stats.skipped:
                result.warnings.append(
                    f"contest {source.contest_id}: field measures skipped ({stats.skipped})"
                )
            field_rows.append(stats.rows)
            stack_rows.append(stats.stacks)
            miss_rows.append(stats.unmatched)

    def stack(parts: list[pd.DataFrame], columns=None) -> pd.DataFrame:
        parts = [p for p in parts if len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns)

    result.profiles = pd.DataFrame(profile_rows)
    result.lineup_errors = stack(error_rows)
    result.field_rows = stack(field_rows, field_mod.FIELD_COLUMNS)
    result.field_stacks = stack(stack_rows, ["contest_id", "group", "team", "n", "share"])
    result.field_unmatched = stack(miss_rows, ["contest_id", "player", "slot", "reason"])

    supplied = slate_index.read_index_file(index_file) if index_file is not None else None
    previous_path = out_dir / "slate_index.csv"
    previous = pd.read_csv(previous_path) if previous_path.exists() else None
    result.index_candidate = index_candidate
    result.index = resolve_index(
        result.typed,
        candidate=index_candidate,
        supplied=supplied,
        fetch_season=fetch_season,
        previous=previous,
        warnings=result.warnings,
    )
    result.toughness = toughness.fit_toughness(result.typed, result.index, profile_inputs)
    result.h2h_bands, result.h2h_opponents = toughness.h2h_report(result.typed)
    return result


def write(result: Analysis) -> list[Path]:
    """Writes every CSV (creating the directory) and returns the paths. The merge already wrote the log."""
    out = result.out_dir
    out.mkdir(parents=True, exist_ok=True)
    tough = result.toughness
    files = {
        "series.csv": result.series_table,
        "class_disagreements.csv": result.disagreements,
        "profiles.csv": result.profiles,
        "field_stats.csv": result.field_rows,
        "field_stacks.csv": result.field_stacks,
        "field_unmatched.csv": result.field_unmatched,
        "lineup_errors.csv": result.lineup_errors,
        "toughness_series.csv": tough.series_table if tough else pd.DataFrame(),
        "toughness_formats.csv": tough.format_table if tough else pd.DataFrame(),
        "toughness_weeks.csv": tough.weeks_table if tough else pd.DataFrame(),
        "toughness_models.csv": tough.comparison if tough else pd.DataFrame(),
        "toughness_k.csv": tough.k_errors if tough else pd.DataFrame(),
        "h2h_bands.csv": result.h2h_bands,
        "h2h_opponents.csv": result.h2h_opponents,
        "slate_index.csv": result.index.assign(candidate=result.index_candidate),
    }
    written = [out / LOG_NAME]
    for name, frame in files.items():
        frame.to_csv(out / name, index=False)
        written.append(out / name)
    return written


def _pct(x: float) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.0f}%"


def summary_lines(result: Analysis) -> list[str]:
    """The short human summary `dfs contests analyze` prints."""
    lines: list[str] = []
    for name, merged in result.merges:
        lines.append(
            f"history {Path(name).name}: {merged.added} new, {merged.updated} updated, "
            f"{merged.unchanged} unchanged, {merged.dropped_other_sports} other-sport row(s) skipped "
            f"-> {merged.total} NFL entries in the log"
        )
    typed = result.typed
    if len(typed):
        counts = typed["format"].value_counts()
        lines.append("formats: " + ", ".join(f"{fmt} {n}" for fmt, n in counts.items()))
        other = typed[typed["format"] == series_mod.OTHER]["series"].value_counts().head(5)
        if len(other):
            lines.append("not recognised (Other), top: " + "; ".join(f"{s} x{n}" for s, n in other.items()))
    if len(result.disagreements):
        lines.append(
            f"{len(result.disagreements)} entr(ies) where the payout-ratio band and the format disagree "
            "(class_disagreements.csv)"
        )
    if result.scan is not None:
        scan = result.scan
        lines.append(
            f"standings: {len(scan.sources)} read, {len(scan.empty)} empty (0-byte, failed download), "
            f"{len(scan.unidentified)} without a contest id, {len(scan.duplicates)} duplicate"
        )
        for origin in scan.empty:
            lines.append(f"  empty: {origin}")
    if len(result.field_rows):
        for group, sub in result.field_rows.groupby("group"):
            lines.append(
                f"field [{group}] {len(sub)} contest(s): QB+1 {_pct(sub['qb_stack1'].mean())}, "
                f"QB+2 {_pct(sub['qb_stack2'].mean())}, bring-back {_pct(sub['bringback'].mean())}, "
                f"avg unspent {sub['avg_unspent'].mean():.0f}"
                if sub["avg_unspent"].notna().any()
                else f"field [{group}] {len(sub)} contest(s): QB+1 {_pct(sub['qb_stack1'].mean())}"
            )
    for (season, week), snapshot in sorted(result.frame_sources.items()):
        lines.append(f"players for Week {week} {season}: {snapshot}")
    if len(result.field_unmatched):
        lines.append(f"{len(result.field_unmatched)} lineup player name(s) unmatched (field_unmatched.csv)")
    if len(result.lineup_errors):
        lines.append(f"{len(result.lineup_errors)} lineup(s) did not parse (lineup_errors.csv)")
    tough = result.toughness
    if tough is not None:
        lines.append(
            f"toughness: week treatment '{tough.mode}', k = {tough.k:g}, k_w = {tough.k_w:g} "
            f"({tough.penalty_source}); left out: {tough.skipped}"
        )
        for rec in tough.comparison.itertuples(index=False):
            lines.append(
                f"  leave-one-week-out error, {rec.mode}: {rec.mae:.4f} "
                f"(k={rec.k:g}, k_w={rec.k_w:g}; {rec.n_scored} entries)"
            )
        if len(tough.weeks_table):
            weeks = tough.weeks_table[tough.weeks_table["group"] == "classic"]
            if len(weeks) > 1:
                line = (
                    f"  week effects (Classic): sd {weeks['w'].std():.2f} logit "
                    f"= {weeks['w_points'].std():.0f} points"
                )
                paired = weeks[["w", "slate_index"]].dropna()
                if len(paired) > 2:
                    line += (
                        f"; correlation with the slate index {paired['w'].corr(paired['slate_index']):+.2f}"
                    )
                lines.append(line)
        for rec in tough.format_table.itertuples(index=False):
            lines.append(
                f"  {rec.format}: {rec.n_entries} entries, expected cash line in an average week "
                f"{rec.expected_cash_line:.1f}" + (f" ({rec.note})" if rec.note else "")
            )
        ranked = tough.series_table.dropna(subset=["toughness"])
        ranked = ranked[ranked["n"] >= 5].sort_values("toughness", ascending=False)
        for label, sub in (("toughest", ranked.head(3)), ("softest", ranked.tail(3)[::-1])):
            for rec in sub.itertuples(index=False):
                lines.append(f"  {label}: {rec.series} ({rec.format}) {rec.toughness:+.1f} pts, n={rec.n}")
    if len(result.h2h_bands):
        lines.append(
            "H2H by score band: "
            + ", ".join(f"{r.band} {_pct(r.win_rate)} (n={r.n})" for r in result.h2h_bands.itertuples())
        )
    if len(result.h2h_opponents):
        lines.append(
            "H2H repeat opponents: "
            + ", ".join(f"{r.opponent} {r.record}" for r in result.h2h_opponents.head(5).itertuples())
        )
    lines.extend(f"warning: {w}" for w in result.warnings)
    return lines
