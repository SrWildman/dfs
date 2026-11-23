"""The money chain: every slate's sheet in date order, each one's bankroll starting where the one before it
ended (Sam, Nov 23: "Thanksgiving slate is its own slate, so its bankroll should be its own thing, just like
any other main slate").

    Week 11 -> Week 12 Thanksgiving -> Week 12 main -> Week 13 ...

**A link** is one sheet with a ledger: the main slate's weekly sheet ("Week 12") or a secondary slate's
("Week 12 Thanksgiving", `slate.py`). Its place is `(week, day)`, the day counted from the NFL week's Tuesday
(`DAY_ORDER`): a Thursday, Friday or Saturday slate comes before that week's Sunday main, a Monday slate
after it.

**Which sheets.** The ones config.toml names: `sheet_id` and `previous_sheet_id` (main) and every
`[slates.<key>] sheet_id`. Each one's week is its own title (`week.parse_week_from_title` for a main sheet,
`slate.title_week` for a secondary one), or `[slates.<key>] week` when config.toml has it (`dfs week new
--slate` writes it). A sheet that cannot be read or titled is left out, and the caller says so.

**Its Season row** is labelled by `season_label`: "12" for a main week (as every Season row is today),
"12 Thu" for a secondary slate.

**Which ledger an entry goes in** (`entry_slate`): a Classic contest whose start falls on a secondary
slate's day of the week that slate's sheet is for (`slate.contest_slate`). Everything else in the week is the
main slate's. With no `[slates.*]` nothing is a secondary slate's, so every ledger is exactly what it was.

Pure: nothing here reads a sheet or a file; `links` takes a `describe(sheet_id) -> title` the caller owns.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from dfs import nfl_calendar, slate

# Days after the NFL week's Tuesday: the order of a week's slates (and of their Season rows).
DAY_ORDER: dict[str, int] = {"tue": 0, "wed": 1, "thu": 2, "fri": 3, "sat": 4, slate.MAIN: 5, "mon": 6}

_LABEL_RE = re.compile(r"^\s*(\d+)(?:\s+([A-Za-z]{3}))?\s*$")


def season_label(week: int, key: str = slate.MAIN) -> str:
    """The Season tab's column-A label for a slate: "12" for the main slate (unchanged), "12 Thu" else."""
    k = slate.validate(key)
    return str(int(week)) if k == slate.MAIN else f"{int(week)} {k.title()}"


def parse_label(text: object) -> tuple[int, str] | None:
    """`(week, key)` for a Season label ("12" -> (12, "main"), "12 Thu" -> (12, "thu")); None for anything
    else (a blank, "Year to date", a typo)."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return (int(text), slate.MAIN) if float(text).is_integer() and text > 0 else None
    match = _LABEL_RE.match(str(text or ""))
    if not match:
        return None
    week, day = int(match.group(1)), (match.group(2) or "").lower()
    if not day:
        return week, slate.MAIN
    return (week, day) if day in slate.DAYS else None


def position(week: int, key: str = slate.MAIN) -> tuple[int, int]:
    """Where a slate sits in the season, for sorting: `(week, day after Tuesday)`."""
    return int(week), DAY_ORDER[slate.validate(key)]


def label_position(text: object) -> tuple[int, int] | None:
    parsed = parse_label(text)
    return None if parsed is None else position(*parsed)


@dataclass(frozen=True)
class Link:
    """One sheet of the chain."""

    key: str  # "main" or a secondary slate's key
    week: int
    sheet_id: str
    title: str = ""

    @property
    def position(self) -> tuple[int, int]:
        return position(self.week, self.key)

    @property
    def label(self) -> str:
        return season_label(self.week, self.key)

    @property
    def is_main(self) -> bool:
        return self.key == slate.MAIN


_MAIN_TITLE_RE = re.compile(r"^Week (\d+)$")  # `week.parse_week_from_title`'s, whatever slate is active


def _week_from_title(title: str, key: str) -> int | None:
    if key == slate.MAIN:
        match = _MAIN_TITLE_RE.match(title.strip())
        return int(match.group(1)) if match else None
    return slate.title_week(title)


def candidates(cfg) -> list[tuple[str, str, int | None]]:  # noqa: ANN001 - dfs.config.Config (the base file)
    """`(key, sheet_id, week from config or None)` for every sheet config.toml names, main first, each id
    once."""
    gs = cfg.google_sheets
    out: list[tuple[str, str, int | None]] = []
    seen: set[str] = set()
    for sid in (gs.sheet_id, gs.previous_sheet_id):
        if sid and sid not in seen:
            seen.add(sid)
            out.append((slate.MAIN, sid, None))
    for key, entry in sorted((getattr(cfg, "slates", {}) or {}).items()):
        if entry.sheet_id and entry.sheet_id not in seen:
            seen.add(entry.sheet_id)
            out.append((key, entry.sheet_id, getattr(entry, "week", None)))
    return out


def links(
    cfg,  # noqa: ANN001 - dfs.config.Config (the base file, whatever slate is active)
    describe: Callable[[str], str | None],
    *,
    skipped: list[str] | None = None,
) -> list[Link]:
    """Every sheet config.toml names, as a link, in date order. `describe(sheet_id)` gives a sheet's title
    (None when it cannot be read). A sheet whose week is unknown is left out (and named in `skipped`)."""
    out: list[Link] = []
    for key, sid, week in candidates(cfg):
        title = describe(sid) or ""
        found = _week_from_title(title, key) if title else None
        resolved = found if found is not None else week
        if resolved is None:
            if skipped is not None:
                skipped.append(f"{sid} ({title or 'could not be read'})")
            continue
        out.append(Link(key, resolved, sid, title))
    out.sort(key=lambda link: link.position)
    return out


def previous(chain: Sequence[Link], at: tuple[int, int]) -> Link | None:
    """The link right before the place `at`, by date: the sheet whose Ending a sheet at `at` starts from."""
    before = [link for link in chain if link.position < at]
    return before[-1] if before else None


def since_last_main(chain: Sequence[Link], at: tuple[int, int]) -> list[Link]:
    """The links before `at` back to (and including) the last main sheet before it: the sheets whose Season
    rows a close at `at` re-reads (Week 12 main: Week 11 and Week 12 Thanksgiving; Week 12 Thanksgiving: Week
    11)."""
    before = [link for link in chain if link.position < at]
    mains = [i for i, link in enumerate(before) if link.is_main]
    return before[mains[-1] :] if mains else before[-1:]


def find(chain: Sequence[Link], sheet_id: str) -> Link | None:
    return next((link for link in chain if link.sheet_id == sheet_id), None)


def slate_weeks(chain: Iterable[Link]) -> dict[str, int]:
    """`{secondary slate key: the week its configured sheet is for}`."""
    return {link.key: link.week for link in chain if not link.is_main}


# -- which ledger an entry goes in ---------------------------------------------------------------------------


def entry_slate(
    moment: datetime | None, game_type: str, season: int, weeks: dict[str, int], week: int | None = None
) -> str:
    """The slate whose ledger an entry belongs to: a secondary slate's key when its contest is a Classic one
    starting on that slate's day of the week the slate's sheet is for (`weeks`, from `slate_weeks`), else
    "main". `week` is the entry's NFL week when the caller has it (else it is worked out from the date)."""
    if moment is None or not weeks:
        return slate.MAIN
    if week is None:
        week = nfl_calendar.season_week_for_date(moment.date(), season)
    keys = [k for k, wk in weeks.items() if wk == week]
    found = slate.contest_slate(moment, game_type, keys)
    return found or slate.MAIN


def for_slate(entries: Sequence, key: str, season: int, weeks: dict[str, int]) -> list:
    """`entries` (`models.ContestEntry`) that belong to slate `key`'s ledger. With no secondary slates and
    `key` main, every entry (the list as it was)."""
    k = slate.validate(key)
    if not weeks and k == slate.MAIN:
        return list(entries)
    return [
        e for e in entries if entry_slate(getattr(e, "contest_date", None), e.game_type, season, weeks) == k
    ]


def log_for_slate(frame, key: str, season: int, weeks: dict[str, int]):  # noqa: ANN001, ANN201 - DataFrames
    """The contest log (`history.read_log`, or `history.typed`) narrowed to slate `key`'s rows: by the log's
    own `SlateKey` tag when it has one (`slate_close.tag_log`), else by `entry_slate`. The frame is returned
    as it is for the main slate when no secondary slate is configured."""
    import pandas as pd

    k = slate.validate(key)
    if frame is None or len(frame) == 0:
        return frame
    if not weeks and k == slate.MAIN and slate.SLATE_COLUMN not in frame.columns and "slate_key" not in frame:
        return frame
    raw = "Contest_Date_EST" in frame.columns
    tag_col = slate.SLATE_COLUMN if raw else "slate_key"
    if tag_col in frame.columns:
        tagged = frame[tag_col].astype(str).str.strip()
        if tagged.ne("").any():  # the log's own record of each entry's slate (`slate_close.tag_log`)
            return frame[tagged.where(tagged.ne(""), slate.MAIN) == k]
    dates = pd.to_datetime(frame["Contest_Date_EST" if raw else "date"], errors="coerce")
    games = frame["Game_Type" if raw else "game_type"]
    week_col = pd.to_numeric(frame["Week" if raw else "week"], errors="coerce")
    season_col = pd.to_numeric(frame["Season" if raw else "season"], errors="coerce")
    keep = []
    for moment, game, wk, ssn in zip(dates, games, week_col, season_col, strict=True):
        if pd.isna(wk) or pd.isna(ssn) or int(ssn) != season:
            keep.append(k == slate.MAIN)  # another season's row, or no week: never a secondary slate's
            continue
        when = None if pd.isna(moment) else moment.to_pydatetime()
        keep.append(entry_slate(when, str(game), season, weeks, int(wk)) == k)
    return frame[pd.Series(keep, index=frame.index, dtype=bool)]
