"""Is our rest-of-season view better than the market's? Scored held out, week by week, from here on.

Every `dfs season sync` keeps a dated copy of FantasyPros' rest-of-season ranks (`consensus.archive`) and of
our own rest-of-season points per game in each league (`archive_ours`). Once weeks have been played after a
snapshot, both are scored against what happened in those weeks (`score`): per position, the Spearman rank
correlation between each view's order and the players' actual points per game in the league's scoring, over
the players either view ranks in its top `TOP_N` at that position who played. The same players for both, so
the comparison is of views, not of who is in the sample. Higher is better; the headline is the mean over
positions and snapshots, with the number of snapshots and player-weeks it rests on.

There is no backfill: the Internet Archive holds only off-season copies of the FantasyPros page, so the first
scores arrive after the first week played since 2026-10-28.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd

from dfs.nfl_calendar import week_start_date
from dfs.paths import DATA_DIR
from dfs.season_long import consensus as cs
from dfs.season_long import ros

OURS_DIR = DATA_DIR / "season" / "ros_history"
TOP_N = {"QB": 24, "RB": 40, "WR": 50, "TE": 20}
MIN_PLAYERS = 8


def archive_ours(league_name: str, summary: pd.DataFrame, day: date | None = None) -> None:
    """Our per-game rest-of-season rate per player, one file per league per day (first sync of the day)."""
    day = day or datetime.now(UTC).date()
    path = OURS_DIR / league_name / f"{day.isoformat()}.csv"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = (summary["ros_pts"] / summary["games_left"].where(summary["games_left"] > 0)).round(2)
    out = summary[["name", "position", "team"]].assign(rate=rate).dropna(subset=["rate"])
    out.rename_axis("player_id").reset_index().to_csv(path, index=False)


def _ours(league_name: str) -> dict[date, pd.DataFrame]:
    folder = OURS_DIR / league_name
    out = {}
    for path in sorted(folder.glob("*.csv")) if folder.exists() else []:
        try:
            out[date.fromisoformat(path.stem)] = pd.read_csv(path, dtype={"player_id": str})
        except (ValueError, OSError):
            continue
    return out


def weeks_after(day: date, season: int, through: int) -> list[int]:
    """The finished weeks whose first game (Thursday) came after the snapshot day."""
    out = []
    for w in range(1, through + 1):
        thursday = datetime.combine(week_start_date(w, season) + timedelta(days=2), time(23), tzinfo=UTC)
        if thursday.date() > day:
            out.append(w)
    return out


@dataclass
class CheckResult:
    snapshots: int = 0
    player_weeks: int = 0
    ours: float = float("nan")
    market: float = float("nan")
    by_position: dict[str, tuple[float, float, int]] = field(default_factory=dict)
    first_snapshot: date | None = None

    def line(self) -> str:
        if not self.snapshots:
            since = f" (first snapshot {self.first_snapshot})" if self.first_snapshot else ""
            return f"Ours vs market: no week played since the first snapshot yet{since}."
        lead = "ours" if self.ours > self.market else "the market" if self.market > self.ours else "neither"
        return (
            f"Ours vs market, held out: rank correlation {self.ours:.2f} vs {self.market:.2f} ({lead} ahead) "
            f"over {self.snapshots} snapshot(s), {self.player_weeks} player-weeks."
        )


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


def score(league_name: str, scoring: dict, season: int, through: int, table: pd.DataFrame) -> CheckResult:
    page = cs.page_for(scoring)
    market = cs.history(page)
    ours = _ours(league_name)
    result = CheckResult(first_snapshot=min(ours) if ours else None)
    days = sorted(set(market) & set(ours))
    per_pos: dict[str, list[tuple[float, float, int]]] = {}
    for day in days:
        weeks = weeks_after(day, season, through)
        if not weeks:
            continue
        act = ros.actuals(scoring, season, weeks)
        actual = act.groupby("player_id")["pts"].agg(["mean", "size"])
        ranks = cs.match_to_sleeper(market[day], table)
        mine = ours[day].set_index("player_id")
        used = False
        for pos, n in TOP_N.items():
            at = mine[mine["position"] == pos]
            our_top = set(at["rate"].sort_values(ascending=False).index[:n])
            pos_ranks = ranks[ranks.index.isin(at.index)].sort_values()
            mkt_top = set(pos_ranks.index[:n])
            known = set(actual.index) & set(pos_ranks.index) & set(at.index)
            pool = sorted((our_top | mkt_top) & known)
            if len(pool) < MIN_PLAYERS:
                continue
            truth = actual.loc[pool, "mean"]
            s_ours = _spearman(at.loc[pool, "rate"], truth)
            s_mkt = _spearman(-pos_ranks.loc[pool].astype(float), truth)
            per_pos.setdefault(pos, []).append((s_ours, s_mkt, int(actual.loc[pool, "size"].sum())))
            used = True
        result.snapshots += int(used)
    if not per_pos:
        return result
    rows = [r for v in per_pos.values() for r in v]
    result.player_weeks = sum(r[2] for r in rows)
    result.ours = sum(r[0] for r in rows) / len(rows)
    result.market = sum(r[1] for r in rows) / len(rows)
    result.by_position = {
        pos: (sum(r[0] for r in v) / len(v), sum(r[1] for r in v) / len(v), sum(r[2] for r in v))
        for pos, v in per_pos.items()
    }
    return result
