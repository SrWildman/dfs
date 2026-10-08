"""The results loop: score every archived projection against what actually happened.

For each completed week this module rebuilds, from the raw snapshots under `data/raw/`, the
projections Sam could have seen at kickoff -- using TODAY's derived-field code, never an archived
`Flags` column -- and joins them to actual DK points (`results_actual.py`).

**Snapshot rule.** A player's projection is the last TFFB snapshot taken before HIS OWN kickoff
(`GameStart`), not before the Sunday 1 pm games: late-window players lock later. TFFB only projects
the Sunday main slate, so in practice every player in a week lands on the same snapshot; the rule
is applied anyway, and `Snapshot` records which one each player used.

**Derived fields** (`ValAdj`, `Flags`, the rosterable pool) are recomputed with `derived.
build_edge_frame` over the week's selected projections and every other source "as of" the
week's reference time (the snapshot most players used). A source with no
snapshot that early (pbp before 9/25, Sleeper/FantasyPros before 9/24, ...) is simply absent for
that week, exactly the fail-soft path a live sync takes -- so older weeks carry fewer flags.

The pure pieces (`select_projection_rows`, `reference_time`, `score_week`) take dataframes and need
no disk; `load_*` and `as_of` are the thin disk layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from dfs import nfl_calendar
from dfs.derived import _rosterable_pool_mask, build_edge_frame
from dfs.kickoff import KICKOFF_TIMEZONE, kickoff_utc
from dfs.line_movement import LineMovementError, diff_odds
from dfs.log import get_logger
from dfs.paths import DATA_DIR, RAW_DIR
from dfs.player_join import join_source_to_dk

log = get_logger("results_loop")

RESULTS_DIR = DATA_DIR / "results"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_SOS_SOURCES = {"QB": "sos_qb", "RB": "sos_rb", "WR": "sos_wr", "TE": "sos_te", "DST": "sos_dst"}

SCORED_COLUMNS = [
    "season",
    "week",
    "Id",
    "Name",
    "Position",
    "Team",
    "Salary",
    "GameStart",
    "Snapshot",
    "RefSnapshot",
    "ProjPts",
    "Ceiling",
    "SleeperPts",
    "FantasyProsPts",
    "AggPts",
    "Val",
    "ValAdj",
    "Flags",
    "Avail",
    "RosterablePool",
    "Status",
    "DkActual",
    "GsisId",
    "UmPts",  # the UM model's mean, added by `results_signals.attach_um` (blank until then)
]

# Status of a DK main-slate player's actual points: "scored" (found a stat line that week), "dnp"
# (no stat line that week but known elsewhere in the season file: inactive, injured, benched), or
# "unmatched" (never found -- a real join miss, written to the unmatched file).
STATUS_SCORED, STATUS_DNP, STATUS_UNMATCHED = "scored", "dnp", "unmatched"


def scored_path(season: int, week: int) -> Path:
    """`data/results/scored_<season>_wNN.csv`: one row per main-slate player for that week."""
    return RESULTS_DIR / f"scored_{season}_w{week:02d}.csv"


def unmatched_path(season: int, week: int) -> Path:
    """`data/results/unmatched_<season>_wNN.csv`: players with a projection that were found nowhere."""
    return RESULTS_DIR / f"unmatched_{season}_w{week:02d}.csv"


# ---------------------------------------------------------------------------------------------
# Disk layer: archived snapshots
# ---------------------------------------------------------------------------------------------


def snapshot_files(source: str) -> list[tuple[datetime, Path]]:
    """Every `data/raw/<source>/<UTC stamp>.csv`, oldest first."""
    directory = RAW_DIR / source
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.csv")):
        if re.fullmatch(r"\d{8}T\d{6}Z", path.stem):
            out.append((datetime.strptime(path.stem, _STAMP_FORMAT).replace(tzinfo=UTC), path))
    return out


def read_snapshot(path: Path) -> pd.DataFrame | None:
    """A snapshot, or None when it cannot be read (an older schema, a truncated file)."""
    try:
        return pd.read_csv(path)
    except Exception as e:  # noqa: BLE001 - one bad archive must not stop the whole loop
        log.warning("could not read %s: %s", path.name, e)
        return None


def as_of(source: str, when: datetime) -> pd.DataFrame | None:
    """The latest snapshot of `source` taken at or before `when`, or None if there is none."""
    chosen = [path for ts, path in snapshot_files(source) if ts <= when]
    return read_snapshot(chosen[-1]) if chosen else None


def odds_movement(week: int, season: int, when: datetime) -> pd.DataFrame | None:
    """The week's line movement as of `when`: earliest `nfl_odds` snapshot on/after the week's start
    date, against the latest one at or before `when` -- the same baseline a live sync uses."""
    start = nfl_calendar.week_start_date(week, season)
    snaps = [(ts, p) for ts, p in snapshot_files("nfl_odds") if ts <= when]
    if not snaps:
        return None
    baseline = next((p for ts, p in snaps if ts.date() >= start), snaps[0][1])
    previous, current = read_snapshot(baseline), read_snapshot(snaps[-1][1])
    if previous is None or current is None:
        return None
    try:
        return diff_odds(previous, current)
    except LineMovementError as e:
        log.warning("line movement for week %s failed: %s", week, e)
        return None


# ---------------------------------------------------------------------------------------------
# Pure: which snapshot each player is scored against
# ---------------------------------------------------------------------------------------------


def _with_week(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    frame = frame.copy()
    frame["_gs"] = kickoff_utc(frame["GameStart"])
    # The week comes from the Eastern calendar date: Sunday-night games stay in their own week.
    local_date = frame["_gs"].dt.tz_convert(KICKOFF_TIMEZONE).dt.date
    frame["_week"] = local_date.map(lambda d: nfl_calendar.week_for_date(d, season) if pd.notna(d) else None)
    return frame


def select_projection_rows(
    snapshots: list[tuple[datetime, pd.DataFrame]], week: int, season: int
) -> pd.DataFrame:
    """One row per player: his projection from the last snapshot taken BEFORE HIS OWN kickoff.

    `snapshots` is `[(timestamp, projections_frame)]`. A frame without `GameStart` (an older
    schema) is skipped. A player whose every snapshot post-dates his own kickoff gets no row.
    Adds `Snapshot` (the chosen snapshot's stamp)."""
    candidates = []
    for stamp, frame in snapshots:
        if frame is None or "GameStart" not in frame.columns or "Id" not in frame.columns:
            continue
        framed = _with_week(frame, season)
        framed = framed[(framed["_week"] == week) & (framed["_gs"] > pd.Timestamp(stamp))].copy()
        framed["Snapshot"] = stamp.strftime(_STAMP_FORMAT)
        framed["_stamp"] = pd.Timestamp(stamp)
        candidates.append(framed)
    if not candidates:
        return pd.DataFrame()
    pooled = pd.concat(candidates, ignore_index=True)
    best = pooled.sort_values("_stamp").groupby("Id", as_index=False).tail(1)
    return best.drop(columns=["_week", "_stamp"]).reset_index(drop=True)


def reference_time(selected: pd.DataFrame) -> datetime | None:
    """The week's reference moment: the snapshot MOST players used (the main 1 pm window). Every
    "as of" source and the derived-field recompute are anchored here. (The earliest kickoff is the
    wrong anchor: it is often a morning international game that locks hours before everyone else.)"""
    if selected.empty:
        return None
    stamp = selected["Snapshot"].value_counts().idxmax()
    return datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# Rebuild the week's derived fields with today's code
# ---------------------------------------------------------------------------------------------


@dataclass
class WeekInputs:
    """What `build_edge_frame` needs, as of the week's reference time."""

    salaries: pd.DataFrame
    games: pd.DataFrame | None
    weather: pd.DataFrame | None
    line_movement: pd.DataFrame | None
    sos_by_position: dict[str, pd.DataFrame]
    sleeper: pd.DataFrame | None
    fantasypros: pd.DataFrame | None
    snaps: pd.DataFrame | None
    team_metrics: pd.DataFrame | None
    present: list[str]


def load_week_inputs(week: int, season: int, when: datetime) -> WeekInputs | None:
    """Every source the edge build reads, as of `when`. None when no DraftKings file exists yet
    (nothing to score against)."""
    salaries = as_of("draftkings", when)
    if salaries is None:
        return None
    sos = {pos: df for pos, src in _SOS_SOURCES.items() if (df := as_of(src, when)) is not None}
    inputs = WeekInputs(
        salaries=salaries,
        games=as_of("nflverse_games", when),
        weather=as_of("weather", when),
        line_movement=odds_movement(week, season, when),
        sos_by_position=sos,
        sleeper=as_of("sleeper", when),
        fantasypros=as_of("fantasypros", when),
        snaps=as_of("snaps", when),
        team_metrics=as_of("pbp", when),
        present=[],
    )
    inputs.present = [
        name
        for name, value in (
            ("games", inputs.games),
            ("weather", inputs.weather),
            ("line_movement", inputs.line_movement),
            ("sleeper", inputs.sleeper),
            ("fantasypros", inputs.fantasypros),
            ("snaps", inputs.snaps),
            ("pbp", inputs.team_metrics),
        )
        if value is not None
    ] + [f"sos_{pos.lower()}" for pos in sos]
    return inputs


def rebuild_week_frame(selected: pd.DataFrame, inputs: WeekInputs) -> pd.DataFrame:
    """The edge frame for the week, recomputed with the CURRENT derived code from the selected
    projections and the as-of inputs. Never reads an archived edge/flag output."""
    projections = selected.drop(columns=[c for c in ("_gs", "Snapshot") if c in selected.columns])
    result = build_edge_frame(
        projections,
        inputs.salaries,
        games=inputs.games,
        weather=inputs.weather,
        line_movement=inputs.line_movement,
        sos_by_position=inputs.sos_by_position,
        sleeper=inputs.sleeper,
        fantasypros=inputs.fantasypros,
        snaps=inputs.snaps,
        team_metrics=inputs.team_metrics,
    )
    return result.frame


def source_projection_columns(frame: pd.DataFrame, inputs: WeekInputs) -> pd.DataFrame:
    """Sleeper's and FantasyPros' own DK-scored projection per DK player (NaN when a source did not
    project him), joined with the same `player_join` every other source uses."""
    dk = frame[["Id", "Name", "Team", "Position"]]
    out = pd.DataFrame({"Id": frame["Id"]})
    for column, source, df in (
        ("SleeperPts", "sleeper", inputs.sleeper),
        ("FantasyProsPts", "fantasypros", inputs.fantasypros),
    ):
        if df is None or df.empty:
            out[column] = float("nan")
            continue
        result = join_source_to_dk(
            dk,
            df,
            source_name_col="Name",
            source_team_col="Team",
            source_position_col="Position",
            source=source,
        )
        matched = (
            result.matched[["Id", "DkPts"]].drop_duplicates(subset="Id").rename(columns={"DkPts": column})
        )
        out = out.merge(matched, on="Id", how="left")
    return out


# ---------------------------------------------------------------------------------------------
# Join actual points
# ---------------------------------------------------------------------------------------------


def attach_actual(
    frame: pd.DataFrame, offense_week: pd.DataFrame, offense_season: pd.DataFrame, dst_week: pd.DataFrame
) -> pd.DataFrame:
    """Add `DkActual`, `GsisId` and `Status` to `frame` (one row per DK main-slate player).

    Offence joins on name + team + position, DST on team alone, both through `player_join`. A player
    with no stat line THIS week but present elsewhere in `offense_season` is a `dnp` (he did not
    play), not a join failure; one found nowhere is `unmatched`."""
    out = frame.copy()
    out["DkActual"] = float("nan")
    out["GsisId"] = ""
    out["Status"] = STATUS_UNMATCHED
    dk = out[["Id", "Name", "Team", "Position"]]

    def _join(source_frame: pd.DataFrame, source: str, *, expect_dst: bool) -> pd.DataFrame:
        if source_frame.empty:
            return pd.DataFrame(columns=["Id"])
        return join_source_to_dk(
            dk,
            source_frame,
            source_name_col="Name",
            source_team_col="Team",
            source_position_col="Position",
            source=source,
            expect_dst=expect_dst,
        ).matched

    week_rows = _join(offense_week, "results", expect_dst=False)
    if not week_rows.empty:
        keep = week_rows[["Id", "dk_actual", "player_id"]].drop_duplicates(subset="Id")
        mapped = out[["Id"]].merge(keep, on="Id", how="left")
        found = mapped["dk_actual"].notna().to_numpy()
        out.loc[found, "DkActual"] = mapped.loc[found, "dk_actual"].to_numpy()
        out.loc[found, "GsisId"] = mapped.loc[found, "player_id"].to_numpy()
        out.loc[found, "Status"] = STATUS_SCORED

    dst_rows = dst_week.assign(Name=dst_week["Team"]) if not dst_week.empty else dst_week
    dst_matched = _join(dst_rows, "results_dst", expect_dst=True) if not dst_week.empty else pd.DataFrame()
    if not dst_matched.empty:
        is_dst = dst_matched["Position"].astype(str).str.upper() == "DST"
        keep = dst_matched[is_dst][["Id", "dk_actual"]].drop_duplicates(subset="Id")
        mapped = out[["Id"]].merge(keep, on="Id", how="left")
        found = mapped["dk_actual"].notna().to_numpy()
        out.loc[found, "DkActual"] = mapped.loc[found, "dk_actual"].to_numpy()
        out.loc[found, "Status"] = STATUS_SCORED

    pending = out["Status"] == STATUS_UNMATCHED
    if pending.any() and not offense_season.empty:
        # One row per player (his latest week): a player-week frame would duplicate the join keys.
        season_players = offense_season.sort_values("week").groupby("player_id", as_index=False).tail(1)
        elsewhere = _join(season_players, "results_season", expect_dst=False)
        if not elsewhere.empty:
            known = set(elsewhere["Id"])
            out.loc[pending & out["Id"].isin(known), "Status"] = STATUS_DNP
    # A defense with no stat line is a real miss, never a "did not play".
    return out


# ---------------------------------------------------------------------------------------------
# One week, end to end
# ---------------------------------------------------------------------------------------------


@dataclass
class WeekResult:
    """One scored week: the table, when it was anchored, and what went into it (for the printed report)."""

    week: int
    scored: pd.DataFrame
    reference: datetime | None
    present_sources: list[str]
    snapshots_used: int
    different_from_reference: int


def score_week(
    week: int,
    season: int,
    *,
    projection_snapshots: list[tuple[datetime, pd.DataFrame]],
    offense_actual: pd.DataFrame,
    dst_actual: pd.DataFrame,
) -> WeekResult | None:
    """Everything for one week. `offense_actual`/`dst_actual` are the full-season outputs of
    `results_actual.score_offense_actual`/`score_dst_actual`. None when the week has no usable
    projection snapshot or DraftKings file."""
    selected = select_projection_rows(projection_snapshots, week, season)
    reference = reference_time(selected)
    if reference is None:
        log.warning("week %s: no projection snapshot before kickoff", week)
        return None
    inputs = load_week_inputs(week, season, reference)
    if inputs is None:
        log.warning("week %s: no DraftKings salary file as of %s", week, reference)
        return None

    frame = rebuild_week_frame(selected, inputs)
    frame = frame.merge(
        selected[["Id", "GameStart", "Snapshot"]].assign(Id=lambda d: d["Id"].astype(frame["Id"].dtype)),
        on="Id",
        how="left",
    )
    frame = frame.merge(source_projection_columns(frame, inputs), on="Id", how="left")
    frame["RosterablePool"] = _rosterable_pool_mask(frame["ProjPts"], frame["Position"])
    frame["season"] = season
    frame["week"] = week
    frame["RefSnapshot"] = reference.strftime(_STAMP_FORMAT)

    offense_week = offense_actual[offense_actual["week"] == week]
    dst_week = dst_actual[dst_actual["week"] == week]
    frame = attach_actual(frame, offense_week, offense_actual, dst_week)
    for column in SCORED_COLUMNS:
        if column not in frame.columns:
            frame[column] = float("nan")
    return WeekResult(
        week=week,
        scored=frame[SCORED_COLUMNS].sort_values(["Position", "ProjPts"], ascending=[True, False]),
        reference=reference,
        present_sources=inputs.present,
        snapshots_used=int(frame["Snapshot"].nunique()),
        different_from_reference=int((frame["Snapshot"] != frame["RefSnapshot"]).sum()),
    )


# ---------------------------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------------------------


def coverage(scored: pd.DataFrame) -> pd.DataFrame:
    """The "N of M main-slate players joined" report, for both populations, plus the DNP count.
    "Joined" is `scored` + `dnp` (a player we positively know did not play); `unmatched` are the real
    join misses."""
    rows = []
    populations = {
        "rosterable pool": scored["RosterablePool"].astype(bool),
        "everyone with ProjPts > 0": pd.to_numeric(scored["ProjPts"], errors="coerce") > 0,
    }
    for label, mask in populations.items():
        part = scored[mask]
        total = len(part)
        scored_n = int((part["Status"] == STATUS_SCORED).sum())
        dnp = int((part["Status"] == STATUS_DNP).sum())
        miss = int((part["Status"] == STATUS_UNMATCHED).sum())
        rows.append(
            {
                "Population": label,
                "Players": total,
                "Scored": scored_n,
                "DNP": dnp,
                "Unmatched": miss,
                "JoinedPct": round(100 * (scored_n + dnp) / total, 1) if total else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def completed_weeks(game_scores: pd.DataFrame) -> list[int]:
    """A week is complete when EVERY scheduled game that week has a final score."""
    scores = game_scores.copy()
    scores["week"] = pd.to_numeric(scores["week"], errors="coerce")
    done = scores.groupby("week").apply(
        lambda g: bool(g["home_score"].notna().all() and g["away_score"].notna().all()), include_groups=False
    )
    return sorted(int(w) for w, ok in done.items() if ok and len(scores[scores["week"] == w]) > 0)
