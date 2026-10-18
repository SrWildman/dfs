"""Retro-test of the GPP builds on Sam's real contests.

For a past week, rebuild the Suggested GPP builds from what was known at the lock (the last EdgeRaw snapshot
before Sunday 1 pm ET: projections, salaries, ownership, kickoffs), then score every build against the real
standings of that week's contests: the build's actual DraftKings points (from the standings' own `FPTS`
column) placed in the field's final scores. Local, offline, read-only: the snapshots and standings are Sam's
files in `data/raw` (never in git) and nothing here writes a sheet.

**The pool is an assumption.** Sam's real pool for a past week is not stored, so a pool is *constructed*: the
main-slate players with the highest `CalPts` at each position, in the counts of his Week 6 pool (QB 9, RB 13,
WR 17, TE 10, DST 8). `POOL_RULES` names the rules; a second one tilts toward value (`Val`), because he picks
by value more than by projection. Every build is judged against the same field either way, so the comparison
between rankings is fair; the absolute finishes are for a plausible pool, not his.

Pure functions where possible; the loaders read files under `data/raw`.
"""

from __future__ import annotations

import collections
import glob
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import calibration, nfl_calendar, results_loop
from dfs.contests import standings as st
from dfs.contests import week_frames
from dfs.player_join import normalize_name

SEASON = 2026
EDGE_DIR = Path("data/raw/edge")
DK_DIR = Path("data/raw/draftkings")
STANDINGS_DIR = Path("data/raw/dk_standings")
POOL_COUNTS = {"QB": 9, "RB": 13, "WR": 17, "TE": 10, "DST": 8}  # Sam's Week 6 pool (57 players)
POOL_RULES = ("calpts", "value")
TOP_FRACTION = 0.01
_STAMP = re.compile(r"^(\d{8}T\d{6})Z\.csv$")


# ---------------------------------------------------------------------------------------------------------
# The week as it stood at the lock
# ---------------------------------------------------------------------------------------------------------


def edge_snapshot_before(week: int, season: int = SEASON, folder: Path = EDGE_DIR) -> tuple[Path, datetime]:
    """The last EdgeRaw snapshot taken before the week's Sunday lock, and its UTC time."""
    lock = week_frames.lock_utc(season, week)
    best = None
    for path in sorted(folder.glob("*.csv")):
        found = _STAMP.match(path.name)
        if not found:
            continue
        stamp = datetime.strptime(found.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        if stamp <= lock:
            best = (path, stamp)
    if best is None:
        raise FileNotFoundError(f"no edge snapshot before the Week {week} lock in {folder}")
    return best


def _id_key(values: pd.Series) -> pd.Series:
    return results_loop._id_key(values)  # noqa: SLF001 - the one DK-id normaliser


def with_calpts(edge: pd.DataFrame, scored: pd.DataFrame, week: int, season: int = SEASON) -> pd.DataFrame:
    """`edge` with a `CalPts` for every row that has one: the snapshot's own where it was written, else the
    calibration fit on the weeks BEFORE `week` (no lookahead) applied to the week's scored rows, by DK Id."""
    out = edge.copy()
    if "CalPts" not in out.columns:
        out["CalPts"] = np.nan
    rows, _ = calibration.calpts_for_week(scored, week, season=season)
    by_id = rows.assign(_k=_id_key(rows["Id"])).drop_duplicates("_k").set_index("_k")["CalPts"]
    fill = _id_key(out["Id"]).map(by_id)
    out["CalPts"] = pd.to_numeric(out["CalPts"], errors="coerce").fillna(fill)
    return out


def main_slate_names(week: int, season: int = SEASON) -> set[tuple[str, str]] | None:
    """`{(normalised name, team)}` of the week's main slate (the DraftKings salary snapshot the sync kept)."""
    frame, _ = week_frames.week_frame(DK_DIR, season, week)
    if frame is None:
        return None
    return {(normalize_name(n), str(t).upper()) for n, t in zip(frame["Name"], frame["Team"], strict=True)}


def pool_tags(
    edge: pd.DataFrame,
    *,
    rule: str = "calpts",
    counts: dict[str, int] = POOL_COUNTS,
    slate: set[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """`{name: "Both"}` for a constructed pool: per position the top `counts[position]` main-slate players by
    `CalPts` (rule "calpts") or by `Val` among those with a `CalPts` of at least the median of the position's
    top-`counts` (rule "value"). Nobody OUT or IR."""
    if rule not in POOL_RULES:
        raise ValueError(f"pool rule {rule!r} is not one of {POOL_RULES}")
    frame = edge.copy()
    frame["_cal"] = pd.to_numeric(frame["CalPts"], errors="coerce")
    frame = frame[frame["_cal"].notna() & (frame["_cal"] > 0)]
    if slate is not None:
        keep = [
            (normalize_name(n), str(t).upper()) in slate
            for n, t in zip(frame["Name"], frame["Team"], strict=True)
        ]
        frame = frame[keep]
    if "Avail" in frame.columns:
        frame = frame[~frame["Avail"].astype(str).str.upper().isin({"OUT", "IR"})]
    tags: dict[str, str] = {}
    for position, n in counts.items():
        part = frame[frame["Position"] == position].sort_values("_cal", ascending=False)
        if rule == "value":
            wide = part.head(n * 2)
            wide = wide.assign(_v=pd.to_numeric(wide["Val"], errors="coerce")).sort_values(
                "_v", ascending=False
            )
            part = wide.head(n)
        else:
            part = part.head(n)
        tags.update({str(name): "Both" for name in part["Name"]})
    return tags


# ---------------------------------------------------------------------------------------------------------
# The real contests
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContestField:
    contest_id: str
    week: int
    series: str
    fmt: str  # "gpp" or "cash"
    entries: int
    places_paid: int
    points: np.ndarray  # every entry's final score, sorted descending
    fpts: dict[str, float]  # normalised player name -> DK points
    copies: collections.Counter  # sorted tuple of the nine normalised names -> entries holding exactly them
    own: dict[str, float]  # normalised player name -> %Drafted as a fraction

    def finish(self, points: float) -> dict[str, float]:
        """Where `points` lands: its place (1 + the entries that scored strictly more), the percentile
        (place / entries), and whether it would have cashed, made the top 10% and the top 1%."""
        place = 1 + int((self.points > points + 1e-9).sum())
        n = len(self.points)
        return {
            "place": place,
            "pct": place / n,
            "cash": place <= self.places_paid,
            "top10": place <= max(1, round(0.10 * n)),
            "top1": place <= max(1, round(TOP_FRACTION * n)),
        }


def load_contests(week: int | None = None, folder: Path = STANDINGS_DIR) -> list[ContestField]:
    """Every Classic contest in the standings folder (optionally one week), with the history log's size,
    series and places paid."""
    log = pd.read_csv("data/contests/contest_log.csv", low_memory=False)
    log = log[log["Season"] == SEASON]
    out = []
    for path in sorted(glob.glob(str(folder / "contest-standings-*.csv"))):
        cid = re.search(r"(\d+)\.csv$", path).group(1)
        meta = log[log["Contest_Key"] == int(cid)]
        if meta.empty:
            continue
        m = meta.iloc[0]
        if week is not None and int(m["Week"]) != week:
            continue
        s = st.parse_standings(Path(path).read_bytes(), contest_id=cid, places_paid=int(m["Places_Paid"]))
        if s.kind != "Classic" or s.lineups.empty:
            continue
        pts = np.sort(s.lineups["points"].to_numpy(dtype=float))[::-1]
        own = s.ownership
        fpts = {
            normalize_name(p): float(v) for p, v in zip(own["player"], own["fpts"], strict=True) if v == v
        }
        pct = own.groupby("player")["pct_drafted"].max()
        scale = 100.0 if float(pct.max()) > 1.5 else 1.0
        keys = collections.Counter(
            tuple(sorted(normalize_name(n) for _, n in sl)) for sl in s.lineups["slots"]
        )
        out.append(
            ContestField(
                contest_id=cid,
                week=int(m["Week"]),
                series=str(m["Series"]),
                fmt=str(m["Format_Class"]),
                entries=int(m["Contest_Entries"]),
                places_paid=int(m["Places_Paid"]),
                points=pts,
                fpts=fpts,
                copies=keys,
                own={normalize_name(p): float(v) / scale for p, v in pct.items()},
            )
        )
    return out


def build_points(names: list[str], field: ContestField) -> float | None:
    """The nine players' actual DK points in this contest's own table; None if any is not in it."""
    keys = [normalize_name(n) for n in names]
    if any(k not in field.fpts for k in keys):
        return None
    return float(sum(field.fpts[k] for k in keys))


def build_copies(names: list[str], field: ContestField) -> int:
    """How many entries in the field held exactly these nine players."""
    return int(field.copies.get(tuple(sorted(normalize_name(n) for n in names)), 0))


def score_build(names: list[str], field: ContestField) -> dict | None:
    """One build against one contest: its actual points, finish, and copies in the field (None when a player
    is missing from the contest's own player table, e.g. a player the contest's slate excluded)."""
    points = build_points(names, field)
    if points is None:
        return None
    return {"points": points, "copies": build_copies(names, field), **field.finish(points)}


def stamp(week: int) -> str:
    return f"Week {week}  ({nfl_calendar.week_start_date(week, SEASON):%b %d})"
