"""The news change log: what changed between two EdgeRaw snapshots, one row per changed field.

Nothing recorded what changed on a Sunday or when (blueprint 2026-11, finding 7). Every `dfs sync` already
keeps its EdgeRaw frame under `data/raw/edge/<UTC stamp>Z.csv`; this module diffs one snapshot against the
one before it and returns rows for `data/news/changes.csv` (`news_store`, append-only). Pure: no file, no
clock, no network.

**What is logged** (`FIELDS`): a player's `status` (DK's `Avail`), each source's projection (`proj_tffb`,
`proj_sleeper`, `proj_fantasypros`, `proj_espn`, `proj_props`, and `proj_agg`, the mean of the first three),
`calpts`, the ownership numbers (`own_tffb`, `cash_own`, `gpp_own`), and per TEAM (one row, no player) the
`spread` and `total`. `source` names the feed the value came from.

**Rules.**

- A row is written when the value differs at the precision the field is stored at (`Field.decimals`); that
  is the representation, not a threshold. Blank to a value (ownership publishing) and a value to blank are
  changes; blank to blank is not.
- A column missing from EITHER snapshot logs nothing for that field: the code grew a column, no news broke.
  So a field's history starts with the second snapshot that carries it.
- Only players in BOTH snapshots are compared (by `Id`). A player who joins or leaves the slate is not a
  field change.
- **One slate at a time** (`same_slate`): when most teams' opponents differ, the feed moved on to the next
  week and the newer snapshot is that week's baseline: no rows. Every projection "changes" there, and none
  of it is news.
- The week is the slate's own (`GameID`, "2026_09_LV_SF"); a snapshot from before that column existed
  falls back to the calendar (`week_of`), and `replay` keeps the previous snapshot's week while the slate
  is unchanged (a Tuesday-morning sync still showing last week's games).

The late-swap refresh (`dfs lineups late-swap --refresh-injuries`) saves no snapshot: ESPN's game-day feed is
laid over `Avail` in memory. `espn_rows` turns those escalations into `status` rows (source
`espn_injuries`, `snapshot` = "espn:<the entry's own stamp>"), once each.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from zoneinfo import ZoneInfo

import pandas as pd

from dfs import nfl_calendar

COLUMNS = [
    "at",  # when the newer value was seen: the snapshot's UTC stamp (ISO, "2026-11-08T16:57:02Z")
    "season",
    "week",
    "player_id",  # EdgeRaw's Id (DK's); blank on a team row
    "name",
    "position",
    "team",
    "field",
    "before",
    "after",
    "source",
    "snapshot",  # the newer snapshot's file stem, or "espn:<stamp>" for a late-swap refresh row
]

STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
EASTERN = ZoneInfo("America/New_York")
STATUS = "status"
ESPN_SOURCE = "espn_injuries"
ESPN_SNAPSHOT = "espn"


@dataclass(frozen=True)
class Field:
    """One logged field. `columns`: the EdgeRaw header(s) it has had, newest first. `decimals` None = text.
    `scales`, one per column, multiplies the stored number (TFFB's ownership was 0-100 under its first
    header)."""

    name: str
    columns: tuple[str, ...]
    source: str
    decimals: int | None = 2
    scales: tuple[float, ...] = ()


PLAYER_FIELDS: tuple[Field, ...] = (
    Field(STATUS, ("Avail",), "dk", decimals=None),
    Field("proj_tffb", ("ProjPts",), "tffb"),
    Field("proj_sleeper", ("SleeperPts",), "sleeper"),
    Field("proj_fantasypros", ("FantasyProsPts",), "fantasypros"),
    Field("proj_espn", ("EspnPts",), "espn"),
    Field("proj_props", ("PropPts",), "props"),
    Field("proj_agg", ("AggPts",), "model"),
    Field("calpts", ("CalPts",), "model"),
    # `Own%` is a 0-1 fraction; the same number was `ProjOwn`, 0-100, until 2026-09-17
    Field("own_tffb", ("Own%", "ProjOwn"), "tffb", decimals=4, scales=(1.0, 0.01)),
    Field("cash_own", ("CashOwn",), "model", decimals=4),
    Field("gpp_own", ("GppOwn",), "model", decimals=4),
)
TEAM_FIELDS: tuple[Field, ...] = (
    Field("spread", ("Spread",), "odds", decimals=1),
    Field("total", ("OverUnder",), "odds", decimals=1),
)
FIELDS: tuple[Field, ...] = PLAYER_FIELDS + TEAM_FIELDS
SLATE_SHARE = 0.5  # same slate = more than this share of the teams in both snapshots kept their opponent


def parse_stamp(stem: str) -> datetime:
    """A snapshot's file stem ("20261108T165702Z") as its UTC instant."""
    return datetime.strptime(stem, STAMP_FORMAT).replace(tzinfo=UTC)


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_id(value: object) -> str:
    """An Id as a clean string (read back from a CSV it can be 44557804, 4.4557804e7 or "44557804.0")."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _number(value: object, decimals: int, scale: float) -> str:
    """The stored number at the field's precision, as text ("" when blank or not a number)."""
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(value) * scale
    except (TypeError, ValueError):
        return ""
    if math.isnan(number) or math.isinf(number):
        return ""
    return f"{round(number, decimals) + 0.0:.{decimals}f}"  # + 0.0: never "-0.00"


def _values(frame: pd.DataFrame, spec: Field) -> pd.Series | None:
    """The field's values as comparable text, indexed like `frame`; None when the frame has no such column."""
    for i, column in enumerate(spec.columns):
        if column in frame.columns:
            if spec.decimals is None:
                return frame[column].map(_text)
            scale = spec.scales[i] if spec.scales else 1.0
            return frame[column].map(partial(_number, decimals=spec.decimals, scale=scale))
    return None


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` indexed by clean Id, rows without one dropped, the first row kept for a repeated Id."""
    if "Id" not in frame.columns:
        return pd.DataFrame(index=pd.Index([], name="Id"))
    out = frame.copy()
    out["Id"] = out["Id"].map(clean_id)
    out = out[out["Id"] != ""].drop_duplicates(subset="Id", keep="first")
    return out.set_index("Id")


def _opponents(frame: pd.DataFrame) -> dict[str, str]:
    if "Team" not in frame.columns or "Opp" not in frame.columns:
        return {}
    pairs = frame[["Team", "Opp"]].dropna()
    return {_text(t): _text(o) for t, o in zip(pairs["Team"], pairs["Opp"], strict=True) if _text(t)}


def same_slate(previous: pd.DataFrame, current: pd.DataFrame) -> bool:
    """True when both snapshots show the same week's games: most teams in both kept their opponent. Two
    frames with no team in common (or no `Opp` column) are not the same slate."""
    before, after = _opponents(previous), _opponents(current)
    shared = before.keys() & after.keys()
    if not shared:
        return False
    kept = sum(1 for team in shared if before[team] == after[team])
    return kept / len(shared) > SLATE_SHARE


def week_of(frame: pd.DataFrame, at: datetime) -> tuple[int, int]:
    """(season, week) of the slate a snapshot shows: its `GameID`s' own ("2026_09_LV_SF", the commonest),
    else the calendar week of the day it was saved (Eastern)."""
    if "GameID" in frame.columns:
        parts = frame["GameID"].dropna().astype(str).str.extract(r"^(\d{4})_(\d{1,2})_")
        parts = parts.dropna()
        if not parts.empty:
            season, week = parts.value_counts().idxmax()
            return int(season), int(week)
    day = at.astimezone(EASTERN).date()
    season = nfl_calendar.current_season(day)
    return season, nfl_calendar.week_for_date(day, season)


def _row(at: str, season: int, week: int, snapshot: str, spec: Field, before: str, after: str, **who) -> dict:
    return {
        "at": at,
        "season": season,
        "week": week,
        "player_id": who.get("player_id", ""),
        "name": who.get("name", ""),
        "position": who.get("position", ""),
        "team": who.get("team", ""),
        "field": spec.name,
        "before": before,
        "after": after,
        "source": spec.source,
        "snapshot": snapshot,
    }


def _team_values(frame: pd.DataFrame, spec: Field) -> dict[str, str] | None:
    """{team: value}: the first non-blank value among the team's rows (they all carry the game's number)."""
    values = _values(frame, spec)
    if values is None or "Team" not in frame.columns:
        return None
    out: dict[str, str] = {}
    for team, value in zip(frame["Team"].map(_text), values, strict=True):
        if team and (team not in out or (not out[team] and value)):
            out[team] = value
    return out


def diff(
    previous: pd.DataFrame | None,
    current: pd.DataFrame,
    *,
    at: datetime,
    snapshot: str,
    season: int | None = None,
    week: int | None = None,
) -> list[dict]:
    """The rows (`COLUMNS`) for everything that changed from `previous` to `current`: player fields in
    `current`'s row order, then team fields by team. Empty when there is no previous snapshot or it shows
    another slate (see the module docstring's rules)."""
    if previous is None or previous.empty or current.empty or not same_slate(previous, current):
        return []
    if season is None or week is None:
        season, week = week_of(current, at)
    stamp = iso(at)
    rows: list[dict] = []

    old, new = _keyed(previous), _keyed(current)
    old_at = {key: i for i, key in enumerate(old.index)}
    shared = [(i, old_at[key], key) for i, key in enumerate(new.index) if key in old_at]
    if shared:
        changed: dict[str, list[tuple[Field, str, str]]] = {}
        for spec in PLAYER_FIELDS:
            before, after = _values(old, spec), _values(new, spec)
            if before is None or after is None:
                continue
            before_list, after_list = before.tolist(), after.tolist()
            for i, j, key in shared:
                if before_list[j] != after_list[i]:
                    changed.setdefault(key, []).append((spec, before_list[j], after_list[i]))
        who = {
            column: new[column].map(_text).tolist() if column in new.columns else [""] * len(new)
            for column in ("Name", "Position", "Team")
        }
        for i, _j, key in shared:  # the newer snapshot's order, a player's fields together
            for spec, before_value, after_value in changed.get(key, ()):
                rows.append(
                    _row(
                        stamp,
                        season,
                        week,
                        snapshot,
                        spec,
                        before_value,
                        after_value,
                        player_id=key,
                        name=who["Name"][i],
                        position=who["Position"][i],
                        team=who["Team"][i],
                    )
                )

    for spec in TEAM_FIELDS:
        before_by_team, after_by_team = _team_values(previous, spec), _team_values(current, spec)
        if before_by_team is None or after_by_team is None:
            continue
        for team in sorted(before_by_team.keys() & after_by_team.keys()):
            if before_by_team[team] != after_by_team[team]:
                rows.append(
                    _row(
                        stamp,
                        season,
                        week,
                        snapshot,
                        spec,
                        before_by_team[team],
                        after_by_team[team],
                        team=team,
                    )
                )
    return rows


def replay(snapshots: Iterable[tuple[str, pd.DataFrame]]) -> Iterator[tuple[str, list[dict]]]:
    """Walk snapshots oldest first (`(file stem, frame)`) and yield `(stem, rows)` for each against the one
    before it. The first, and the first of each new slate, yield no rows. A snapshot without `GameID` keeps
    the previous one's week while the slate is unchanged."""
    previous: pd.DataFrame | None = None
    previous_week: tuple[int, int] | None = None
    for stem, frame in snapshots:
        at = parse_stamp(stem)
        season, week = week_of(frame, at)
        carried = (
            previous is not None
            and previous_week is not None
            and "GameID" not in frame.columns
            and same_slate(previous, frame)
        )
        if carried:
            season, week = previous_week
        yield stem, diff(previous, frame, at=at, snapshot=stem, season=season, week=week)
        previous, previous_week = frame, (season, week)


def espn_rows(
    edge: pd.DataFrame,
    changes: Iterable,
    *,
    at: datetime,
    logged: Iterable[tuple[str, str]] = (),
) -> list[dict]:
    """`status` rows for a late-swap refresh's escalations (`late_swap_news.Change`: name, before, after,
    updated). `edge` is the frame they were laid over (for the Id, team and week); `logged` holds the
    (player_id, after) pairs this week's log already has from ESPN, so a second run within the hour adds
    nothing. A name the frame does not hold exactly once is skipped (`apply_news` never changes one)."""
    if edge.empty or "Name" not in edge.columns:
        return []
    season, week = week_of(edge, at)
    spec = Field(STATUS, ("Avail",), ESPN_SOURCE, decimals=None)
    seen = set(logged)
    by_name: dict[str, list[int]] = {}
    for position, name in enumerate(edge["Name"].map(_text)):
        by_name.setdefault(name, []).append(position)
    rows = []
    for change in changes:
        holders = by_name.get(_text(change.name), [])
        if len(holders) != 1:
            continue
        who = edge.iloc[holders[0]]
        player_id = clean_id(who.get("Id"))
        after = _text(change.after)
        if (player_id, after) in seen:
            continue
        seen.add((player_id, after))
        updated = getattr(change, "updated", None)
        snapshot = f"{ESPN_SNAPSHOT}:{updated.astimezone(UTC):{STAMP_FORMAT}}" if updated else ESPN_SNAPSHOT
        rows.append(
            _row(
                iso(at),
                season,
                week,
                snapshot,
                spec,
                _text(change.before),
                after,
                player_id=player_id,
                name=_text(who.get("Name")),
                position=_text(who.get("Position")),
                team=_text(who.get("Team")),
            )
        )
    return rows
