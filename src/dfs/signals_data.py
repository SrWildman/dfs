"""The disk and network layer for `signals.py`: load every free input a slate's signals need, and read and
write the per-sync signals archive.

Kept apart from `signals.py` (pure) the same way `results_loop` keeps its disk layer apart from its maths.
Every download is fail-soft: a file that cannot be fetched is left out of `SeasonData` and named in the
returned notes, and only the signals that depend on it go blank. Nothing here snapshots to `data/raw/`
except the archive itself, so a backfill never litters the raw history.

**The archive** (`data/signals/signals_<season>_w<NN>_<UTC stamp>.csv`): one file per sync and per
backfilled week, the `signals.SIGNAL_COLUMNS` player table. Scoring picks, per player, the LAST archive
taken before his own kickoff (`select_archive_rows`), the same rule the results loop applies to TFFB
snapshots, so a late `sync --live` after a game starts can never leak into that player's score.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from dfs import results_actual, results_loop, xfp
from dfs.kickoff import kickoff_utc
from dfs.log import get_logger
from dfs.paths import DATA_DIR
from dfs.signals import SeasonData
from dfs.sources import ffopportunity, nflverse_depth, nflverse_injuries
from dfs.sources import nflverse_files as nf
from dfs.sources.nflverse_games import GAMES_CSV_URL
from dfs.usage_metrics import PBP_USAGE_COLUMNS, red_zone_counts

log = get_logger("signals_data")

SIGNALS_DIR = DATA_DIR / "signals"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_NFLVERSE = nf.NFLVERSE_RELEASES
STATS_PLAYER_URL = _NFLVERSE + "/stats_player/stats_player_week_{season}.parquet"
STATS_TEAM_URL = _NFLVERSE + "/stats_team/stats_team_week_{season}.parquet"
PBP_URL = _NFLVERSE + "/pbp/play_by_play_{season}.parquet"


def archive_path(season: int, week: int, stamp: str) -> Path:
    """`data/signals/signals_<season>_wNN_<stamp>.csv`."""
    return SIGNALS_DIR / f"signals_{season}_w{week:02d}_{stamp}.csv"


def stamp_of(when: datetime) -> str:
    """A UTC datetime as the archive/snapshot stamp (`20261004T162719Z`)."""
    return when.astimezone(UTC).strftime(_STAMP_FORMAT)


def iso_of(when: datetime) -> str:
    """A UTC datetime as the depth chart's `dt` format (`2026-10-04T16:27:19Z`)."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_archive(players: pd.DataFrame, season: int, week: int, when: datetime) -> Path:
    """Write one archive for the slate as of `when`; returns its path."""
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    path = archive_path(season, week, stamp_of(when))
    players.assign(season=season, week=week).to_csv(path, index=False)
    return path


def archive_files(season: int, week: int | None = None) -> list[tuple[datetime, Path]]:
    """Every archive for the season (or one week), oldest first."""
    if not SIGNALS_DIR.exists():
        return []
    pattern = f"signals_{season}_w{week:02d}_*.csv" if week is not None else f"signals_{season}_w*_*.csv"
    out = []
    for path in sorted(SIGNALS_DIR.glob(pattern)):
        stamp = path.stem.rsplit("_", 1)[-1]
        try:
            out.append((datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=UTC), path))
        except ValueError:
            continue
    return out


def load_archives(season: int, week: int) -> list[tuple[datetime, pd.DataFrame]]:
    """Every readable archive for one week, oldest first."""
    out = []
    for stamp, path in archive_files(season, week):
        try:
            out.append((stamp, pd.read_csv(path)))
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the loop
            log.warning("could not read %s: %s", path.name, e)
    return out


def select_archive_rows(
    archives: list[tuple[datetime, pd.DataFrame]], fallback_cutoff: datetime | None
) -> pd.DataFrame:
    """One row per player (`Id`): the one from the LAST archive taken before HIS OWN kickoff.

    A player's kickoff is the archive row's `GameStart` (Eastern wall-clock labelled Z, read through
    `kickoff.kickoff_utc`); when it is missing the archive must be at or before `fallback_cutoff`
    (the week's reference snapshot), and with no cutoff either, the row is excluded: an archive we cannot
    prove predates the game never reaches a score. Adds `ArchiveStamp`."""
    pieces = []
    for stamp, frame in archives:
        if "Id" not in frame.columns:
            continue
        framed = frame.copy()
        kickoff = (
            kickoff_utc(framed["GameStart"])
            if "GameStart" in framed.columns
            else pd.Series(pd.NaT, index=framed.index)
        )
        known = kickoff.notna()
        before_kickoff = known & (kickoff > pd.Timestamp(stamp))
        fallback_ok = (~known) & (fallback_cutoff is not None and stamp <= fallback_cutoff)
        framed = framed[before_kickoff | fallback_ok].copy()
        framed["ArchiveStamp"] = stamp_of(stamp)
        pieces.append(framed)
    if not pieces:
        return pd.DataFrame()
    pooled = pd.concat(pieces, ignore_index=True)
    return pooled.sort_values("ArchiveStamp").groupby("Id", as_index=False).tail(1).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# Loading the free inputs
# ---------------------------------------------------------------------------------------------


@dataclass
class Fetchers:
    """Every download, injectable so tests never touch the network."""

    ffo: Callable[[int], pd.DataFrame] = ffopportunity.fetch_ffo
    injuries: Callable[[int], pd.DataFrame] = nflverse_injuries.fetch_injuries
    depth: Callable[[int], pd.DataFrame] = lambda season: nflverse_depth.fetch_depth(season, keep_days=None)  # noqa: E731
    stats_player: Callable[[int], pd.DataFrame] | None = None
    stats_team: Callable[[int], pd.DataFrame] | None = None
    schedule: Callable[[int], pd.DataFrame] | None = None
    pbp_rz: Callable[[int], pd.DataFrame] | None = None


def _fetch_stats_player(season: int) -> pd.DataFrame:
    content = nf.download(STATS_PLAYER_URL.format(season=season), what=f"{season} stats_player")
    return nf.read_parquet(content, results_actual.STATS_PLAYER_COLUMNS, what=f"{season} stats_player")


def _fetch_stats_team(season: int) -> pd.DataFrame:
    content = nf.download(STATS_TEAM_URL.format(season=season), what=f"{season} stats_team")
    return nf.read_parquet(content, results_actual.STATS_TEAM_COLUMNS, what=f"{season} stats_team")


def _fetch_schedule(season: int) -> pd.DataFrame:
    """The season's regular-season schedule with final scores (nflverse's `games.csv`; no raw snapshot)."""
    content = nf.download(GAMES_CSV_URL, what="games.csv")
    games = pd.read_csv(io.BytesIO(content))
    games = games[(games["season"] == season) & (games["game_type"] == "REG")]
    return games[["week", "away_team", "home_team", "away_score", "home_score"]].reset_index(drop=True)


def _fetch_pbp_rz(season: int) -> pd.DataFrame:
    """Red-zone opportunities per player-week (`GsisId`, `week`, `rz`) from the season's play-by-play."""
    content = nf.download(PBP_URL.format(season=season), what=f"{season} play-by-play")
    pbp = nf.read_parquet(content, PBP_USAGE_COLUMNS, what=f"{season} play-by-play")
    return red_zone_counts(pbp)[["GsisId", "week", "rz"]]


def load_season_data(
    season: int,
    *,
    fetchers: Fetchers | None = None,
    offense_actual: pd.DataFrame | None = None,
    dst_actual: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
) -> tuple[SeasonData, list[str]]:
    """Everything `signals.build_signals` reads. Pass a season's already-scored actuals / schedule to skip
    re-fetching them (the results update has them). Returns (data, notes); each failed file adds a note
    and leaves its field None."""
    f = fetchers or Fetchers()
    notes: list[str] = []
    data = SeasonData(season=season, offense_actual=offense_actual, dst_actual=dst_actual, schedule=schedule)

    def attempt(label: str, call: Callable[[], object]) -> object | None:
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - every failure is reported and survivable
            notes.append(f"{label}: {e}")
            log.warning("%s: %s", label, e)
            return None

    weeks = []
    for year in (season - 1, season):
        raw = attempt(f"ffopportunity {year}", lambda y=year: f.ffo(y))
        if raw is not None and not raw.empty:
            weeks.append(xfp.player_weeks(raw, season=year))
    if weeks:
        data.ffo_weeks = pd.concat(weeks, ignore_index=True)

    fetch_player = f.stats_player or _fetch_stats_player
    fetch_team = f.stats_team or _fetch_stats_team
    fetch_schedule = f.schedule or _fetch_schedule
    cache: dict[tuple[str, int], pd.DataFrame | None] = {}

    def cached(kind: str, year: int, call: Callable[[int], pd.DataFrame]) -> pd.DataFrame | None:
        """One download per (file, season), shared by the offense and defense scoring."""
        if (kind, year) not in cache:
            cache[(kind, year)] = attempt(f"{year} {kind}", lambda: call(year))
        return cache[(kind, year)]

    if data.offense_actual is None:
        sp = cached("stats_player", season, fetch_player)
        if sp is not None:
            data.offense_actual = results_actual.score_offense_actual(sp)
    if data.schedule is None:
        data.schedule = cached("schedule", season, fetch_schedule)
    if data.dst_actual is None and data.schedule is not None:
        sp, st = cached("stats_player", season, fetch_player), cached("stats_team", season, fetch_team)
        if sp is not None and st is not None:
            data.dst_actual = results_actual.score_dst_actual(st, sp, data.schedule)
    # Last season: only the matchup blend and with-or-without use it, so a failure costs only those.
    prior = season - 1
    sp_prior = cached("stats_player", prior, fetch_player)
    st_prior = cached("stats_team", prior, fetch_team)
    data.schedule_prior = cached("schedule", prior, fetch_schedule)
    if sp_prior is not None:
        data.offense_actual_prior = results_actual.score_offense_actual(sp_prior)
        if st_prior is not None and data.schedule_prior is not None:
            data.dst_actual_prior = results_actual.score_dst_actual(st_prior, sp_prior, data.schedule_prior)
    if data.ffo_weeks is not None and data.offense_actual is not None:
        current = data.ffo_weeks["season"] == season
        joined = xfp.attach_actual_points(
            data.ffo_weeks[current].drop(columns="dk_actual", errors="ignore"), data.offense_actual
        )
        data.ffo_weeks = pd.concat([data.ffo_weeks[~current], joined], ignore_index=True)

    data.injuries = attempt(f"{season} injuries", lambda: f.injuries(season))
    data.depth = attempt(f"{season} depth charts", lambda: f.depth(season))
    data.rz_by_week = attempt(f"{season} play-by-play", lambda: (f.pbp_rz or _fetch_pbp_rz)(season))
    return data, notes


def week_cutoff(selected: pd.DataFrame) -> datetime | None:
    """The week's as-of moment for the depth chart and archive: the reference snapshot (`results_loop`)."""
    return results_loop.reference_time(selected)
