"""Backfill the signals for every scored week, and join them to what happened so Model Check can score them.

For a completed week this rebuilds, from the raw snapshots and the free files, exactly what
`signals.build_signals` would have said at that week's reference moment (`results_loop.reference_time`):
`CalPts` fitted on earlier weeks only, the context tokens, injury beneficiaries and matchups. The result is
written as one archive (`signals_data.write_archive`) stamped at the reference moment, so scoring treats a
backfilled week and a live one the same way.

**No lookahead** is the whole point. `calibration.calpts_for_week` fits on weeks before the week;
`signals.build_signals` cuts every file at the week; the depth chart is the latest snapshot at or before
the reference moment; "priced in" reads only snapshots before it. `tests/test_results_signals.py` adds the
week's own actuals to every input and asserts nothing in that week's signals moves.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from dfs import calibration, nfl_calendar, probabilities, results_loop, signals, signals_data
from dfs.log import get_logger
from dfs.results_analysis import is_thin, usable

log = get_logger("results_signals")

SIGNAL_GROUPS = [
    ("BUY↑", "up"),
    ("FADE↓", "down"),
    ("USAGE↑", "up"),
    ("USAGE↓", "down"),
    ("INJ+ confirmed", "up"),
    ("INJ+ questionable", "up"),
    ("Matchup top 8", "up"),
    ("Matchup bottom 4", "down"),
]
SIGNAL_COLUMNS = ["Signal", "n", "VsProj", "VsCal", "HitProj", "HitCal", "Thin"]


def _stamped(snapshots: list[tuple[datetime, pd.DataFrame]]) -> list[tuple[str, pd.DataFrame]]:
    return [(signals_data.stamp_of(ts), frame) for ts, frame in snapshots]


def _week_snapshots(source: str, week: int, season: int, until: datetime) -> list[tuple[str, pd.DataFrame]]:
    """The raw snapshots of `source` taken since the week's start and at or before `until`, oldest first."""
    start = nfl_calendar.week_start_date(week, season)
    out = []
    for ts, path in results_loop.snapshot_files(source):
        if ts.date() >= start and ts <= until:
            frame = results_loop.read_snapshot(path)
            if frame is not None:
                out.append((ts, frame))
    return _stamped(out)


def week_signals(
    scored: pd.DataFrame,
    data: signals.SeasonData,
    *,
    week: int,
    season: int,
    sources: tuple[str, ...] = calibration.CAL_SOURCES,
) -> tuple[signals.SignalOutput, datetime] | None:
    """The signals for one scored week as of its reference moment, or None when the week has no rows or no
    reference. `scored` is every scored table (the calibration needs the earlier weeks)."""
    rows, _fit = calibration.calpts_for_week(scored, week, season=season, sources=sources)
    if rows.empty:
        return None
    reference = datetime.strptime(str(rows["RefSnapshot"].iloc[0]), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    projections = results_loop.as_of("projections", reference)
    frame = rows.copy()
    if projections is not None and "GameStart" in projections.columns:
        starts = projections.drop_duplicates("Id").set_index("Id")["GameStart"]
        frame["GameStart"] = frame["Id"].map(starts)
    frame["GsisId"] = frame["GsisId"].replace("", np.nan)
    output = signals.build_signals(
        frame,
        data,
        week=week,
        depth_dt=signals_data.iso_of(reference),
        team_metrics=results_loop.as_of("pbp", reference),
        implied=signals.implied_by_team(projections) if projections is not None else None,
        dk_snapshots=_week_snapshots("draftkings", week, season, reference),
        projection_snapshots=_week_snapshots("projections", week, season, reference),
    )
    return output, reference


def backfill(
    scored: pd.DataFrame,
    data: signals.SeasonData,
    *,
    season: int,
    weeks: list[int],
    sources: tuple[str, ...] = calibration.CAL_SOURCES,
) -> dict[int, str]:
    """Write one archive per week in `weeks` (replacing an earlier backfill archive of the same stamp).
    Returns week -> archive file name. A week that cannot be built is skipped with a warning."""
    written = {}
    for week in weeks:
        built = week_signals(scored, data, week=week, season=season, sources=sources)
        if built is None:
            log.warning("week %s: no scored rows to build signals from -- skipped", week)
            continue
        output, reference = built
        path = signals_data.write_archive(output.players, season, week, reference)
        written[week] = path.name
    return written


# ---------------------------------------------------------------------------------------------
# Joining signals to actuals
# ---------------------------------------------------------------------------------------------


def evaluation_frame(scored: pd.DataFrame, season: int) -> pd.DataFrame:
    """Scored rows (rosterable pool, real stat line, ProjPts > 0) joined to the signals that would have
    been seen: per player the LAST archive taken before his own kickoff. Columns added: `CalPts`, `Edge`,
    `InjFrom`, `MatchupGroup`, `MatchupRank`, `MatchupTop`, `ArchiveStamp`. Weeks with no archive are
    absent."""
    pieces = []
    base = usable(scored, "pool")
    for week in sorted(int(w) for w in base["week"].unique()):
        archives = signals_data.load_archives(season, week)
        if not archives:
            continue
        week_rows = base[base["week"] == week]
        cutoff = None
        if "RefSnapshot" in week_rows.columns and week_rows["RefSnapshot"].notna().any():
            cutoff = datetime.strptime(
                str(week_rows["RefSnapshot"].dropna().iloc[0]), "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=UTC)
        chosen = signals_data.select_archive_rows(archives, cutoff)
        if chosen.empty:
            continue
        keep = [
            "Id",
            "CalPts",
            "UmPts",
            "Hit3x%",
            "Boom%",
            "Bust%",
            "LowConf",
            "Edge",
            "InjFrom",
            "MatchupGroup",
            "MatchupRank",
            "ArchiveStamp",
        ]
        merged = week_rows.drop(columns=[c for c in keep if c != "Id" and c in week_rows.columns]).merge(
            chosen[[c for c in keep if c in chosen.columns]], on="Id", how="inner"
        )
        pieces.append(merged)
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True)
    out["Edge"] = out["Edge"].fillna("")
    out["InjFrom"] = out["InjFrom"].fillna("")
    out["MatchupGroup"] = out["MatchupGroup"].fillna("")
    score = pd.to_numeric(out.get("CalPts"), errors="coerce").fillna(
        pd.to_numeric(out["ProjPts"], errors="coerce")
    )
    rank = (
        score.groupby([out["week"], out["Team"], out["Position"]]).rank(ascending=False, method="first")
        if not out.empty
        else score
    )
    out["MatchupTop"] = rank <= 2
    return out


def _tokens(edge: pd.Series) -> pd.Series:
    return edge.fillna("").astype(str).str.split()


def signal_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Which rows belong to each signal group (`SIGNAL_GROUPS`)."""
    has = _tokens(frame["Edge"])

    def token(t: str) -> pd.Series:
        return has.map(lambda parts, t=t: t in parts)

    return {
        "BUY↑": token("BUY↑"),
        "FADE↓": token("FADE↓"),
        "USAGE↑": token("USAGE↑"),
        "USAGE↓": token("USAGE↓"),
        "INJ+ confirmed": token("INJ+"),
        "INJ+ questionable": frame["InjFrom"] == "questionable",
        "Matchup top 8": (frame["MatchupGroup"] == "top") & frame["MatchupTop"],
        "Matchup bottom 4": (frame["MatchupGroup"] == "bottom") & frame["MatchupTop"],
    }


def signal_report(frame: pd.DataFrame) -> pd.DataFrame:
    """Per signal group: n, mean (actual - ProjPts), mean (actual - CalPts), and the hit rate against each
    (share on the right side of the projection: above it for an "up" signal, below it for a "down" one).
    A group with no rows is listed with n = 0, never dropped, so the reader sees it has not fired."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    masks = signal_masks(frame)
    proj = pd.to_numeric(frame["ProjPts"], errors="coerce")
    cal = pd.to_numeric(frame["CalPts"], errors="coerce")
    actual = pd.to_numeric(frame["DkActual"], errors="coerce")
    rows = []
    for name, direction in SIGNAL_GROUPS:
        mask = masks[name]
        n = int(mask.sum())
        if n == 0:
            rows.append(
                {
                    "Signal": name,
                    "n": 0,
                    "VsProj": np.nan,
                    "VsCal": np.nan,
                    "HitProj": np.nan,
                    "HitCal": np.nan,
                    "Thin": True,
                }
            )
            continue
        vs_proj = (actual - proj)[mask]
        vs_cal = (actual - cal)[mask]
        sign = 1.0 if direction == "up" else -1.0
        rows.append(
            {
                "Signal": name,
                "n": n,
                "VsProj": round(float(vs_proj.mean()), 2),
                "VsCal": round(float(vs_cal.dropna().mean()), 2) if vs_cal.notna().any() else np.nan,
                "HitProj": round(float((sign * vs_proj > 0).mean()), 3),
                "HitCal": round(float((sign * vs_cal.dropna() > 0).mean()), 3)
                if vs_cal.notna().any()
                else np.nan,
                "Thin": is_thin(n),
            }
        )
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


# ---------------------------------------------------------------------------------------------
# UM for scored weeks
# ---------------------------------------------------------------------------------------------


def attach_um(
    scored_week: pd.DataFrame,
    data: signals.SeasonData,
    *,
    season: int,
    week: int,
    games: pd.DataFrame | None = None,
    history=None,  # noqa: ANN001 - dfs.model.history.History
    artifacts=None,  # noqa: ANN001 - dfs.model.artifacts.Artifacts
) -> pd.DataFrame:
    """`scored_week` with `UmPts` filled in: the UM model's mean for each player, from games before `week`
    only. The gsis id is attached with the project's one join when the row has none."""
    identity = signals.identity_frame(signals.season_weeks(data), data.injuries)
    frame, _ = signals.attach_gsis(
        scored_week.assign(GsisId=scored_week["GsisId"].replace("", np.nan)), identity
    )
    um = probabilities.um_projections(
        frame, season=season, week=week, games=games, history=history, artifacts=artifacts
    )
    out = scored_week.copy()
    out["UmPts"] = um.reindex(out.index).to_numpy()
    return out


# ---------------------------------------------------------------------------------------------
# Reliability of the probability columns
# ---------------------------------------------------------------------------------------------

RELIABILITY_BINS = 10
RELIABILITY_FLAG_POINTS = 8.0
RELIABILITY_MIN_N = 30
RELIABILITY_MEASURES = [
    ("Hit3x%", "Hit 3x", probabilities.HIT_MULTIPLE, "at_least"),
    ("Boom%", "Boom 4x", probabilities.BOOM_MULTIPLE, "at_least"),
    ("Bust%", "Bust under 2x", probabilities.BUST_MULTIPLE, "below"),
]
RELIABILITY_COLUMNS = ["Measure", "Decile", "n", "Predicted", "Realized", "Gap", "Flag", "Thin"]


def reliability_report(frame: pd.DataFrame) -> pd.DataFrame:
    """Predicted decile against realized, for each probability column: players with a value are ranked by
    it and cut into ten equal-count bins; per bin the mean predicted probability, the realized share (did
    the actual reach 3x / 4x salary per $1,000, or fall under 2x) and the gap in points. `Flag` is "off"
    when the gap exceeds `RELIABILITY_FLAG_POINTS` with n >= `RELIABILITY_MIN_N` (reported, never
    adjusted); with few weeks the bins are thin and n is shown."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=RELIABILITY_COLUMNS)
    actual = pd.to_numeric(frame["DkActual"], errors="coerce")
    per_thousand = pd.to_numeric(frame["Salary"], errors="coerce") / 1000.0
    rows = []
    for column, label, multiple, kind in RELIABILITY_MEASURES:
        if column not in frame.columns:
            continue
        predicted = pd.to_numeric(frame[column], errors="coerce")
        ok = predicted.notna() & actual.notna() & (per_thousand > 0)
        if ok.sum() < RELIABILITY_BINS:
            continue
        threshold = multiple * per_thousand[ok]
        happened = (actual[ok] >= threshold) if kind == "at_least" else (actual[ok] < threshold)
        bins = pd.qcut(predicted[ok].rank(method="first"), RELIABILITY_BINS, labels=False)
        for decile in range(RELIABILITY_BINS):
            mask = bins == decile
            n = int(mask.sum())
            if n == 0:
                continue
            mean_pred = float(predicted[ok][mask].mean())
            realized = 100.0 * float(happened[mask].mean())
            gap = realized - mean_pred
            rows.append(
                {
                    "Measure": label,
                    "Decile": decile + 1,
                    "n": n,
                    "Predicted": round(mean_pred, 1),
                    "Realized": round(realized, 1),
                    "Gap": round(gap, 1),
                    "Flag": "off" if abs(gap) > RELIABILITY_FLAG_POINTS and n >= RELIABILITY_MIN_N else "",
                    "Thin": is_thin(n),
                }
            )
    return pd.DataFrame(rows, columns=RELIABILITY_COLUMNS)
