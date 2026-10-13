"""The durable contest-entry log, built from DraftKings' My Contests -> History export.

Each export holds the whole account history (2021 to now, every sport), so exports overlap heavily. The log is
the union of every export, **deduplicated by `Entry_Key`** (a later export wins -- it carries the final
Place/Points/Winnings of a contest the earlier one saw mid-flight), and keeps **every original column
verbatim as text** (`$8.00`, `2,972`) so the file round-trips exactly. The derived columns are recomputed from
those originals on every merge, so a changed rule in `series.py` re-labels the whole log, and
`merge_into` is safe to re-run on the same export.

NFL only: other sports have no NFL season/week/format, so they are dropped and counted
(`MergeResult.dropped_other_sports`).

Derived columns (`DERIVED_COLUMNS`):

- `Season` / `Week`: the NFL season (a January game belongs to the previous season) and week, from
  `nfl_calendar` -- weeks 1-18 only; a contest after week 18's Monday (playoffs, the offseason) has a blank
  `Week`, because `nfl_calendar` clamps those into week 18 and that would be a wrong number.
- `Slate`: `main` if the contest starts Sunday 12:00-17:59 ET (the repo's definition of the Sunday-afternoon
  main slate, `sources/dk_salaries._is_sunday_afternoon`), else `other` (Thursday, Monday, SNF-only, ...).
  Only `main` Classic contests are comparable with the main-slate scoring index.
- `Series`, `Format`, `Matchup`, `Opponent`, `Entry_No`, `Entry_Max`, `Bankroll_Class`, `Format_Class`,
  `Multiple` (a Multiplier contest's 3, 5, 10 ... times the fee, else blank): `series.classify`.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from dfs import nfl_calendar
from dfs.contests import series as series_mod

HISTORY_COLUMNS = [
    "Sport",
    "Game_Type",
    "Entry_Key",
    "Entry",
    "Contest_Key",
    "Contest_Date_EST",
    "Place",
    "Points",
    "Winnings_Non_Ticket",
    "Winnings_Ticket",
    "Contest_Entries",
    "Entry_Fee",
    "Prize_Pool",
    "Places_Paid",
]
DERIVED_COLUMNS = [
    "Season",
    "Week",
    "Slate",
    "Series",
    "Format",
    "Matchup",
    "Opponent",
    "Entry_No",
    "Entry_Max",
    "Bankroll_Class",
    "Format_Class",
    "Multiple",
]
LOG_COLUMNS = HISTORY_COLUMNS + DERIVED_COLUMNS

MAIN_SLATE_WEEKDAY = 6  # Sunday
MAIN_SLATE_HOURS = range(12, 18)  # kickoff 12:00-17:59 ET, as in dk_salaries._is_sunday_afternoon


class HistoryError(Exception):
    """An export that is not a DraftKings contest-history file (or is empty)."""


def money(value: object) -> Decimal:
    """`$1,234.50` -> Decimal; blank or unreadable -> 0 (the bankroll module's own rule)."""
    text = str(value).replace("$", "").replace(",", "").strip()
    try:
        return Decimal(text) if text else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def count(value: object) -> int | None:
    """`2,758` -> 2758; blank or unreadable -> None."""
    text = str(value).replace(",", "").strip()
    try:
        return int(float(text)) if text else None
    except ValueError:
        return None


def number(value: object) -> float | None:
    text = str(value).replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def season_week(day: date) -> tuple[int, int | None]:
    """`(NFL season, NFL week or None)` for a calendar date. January and February belong to the previous
    season; a date after the Monday of week 18 (playoffs, offseason) has no week."""
    season = day.year if nfl_calendar.season_week_for_date(day, day.year) is not None else day.year - 1
    week = nfl_calendar.season_week_for_date(day, season)
    if week is None:
        return season, None
    after_regular_season = nfl_calendar.week_start_date(nfl_calendar.MAX_WEEK, season) + timedelta(weeks=1)
    return (season, None) if day >= after_regular_season else (season, week)


def slate_of(moment: datetime) -> str:
    """`main` for a Sunday 12:00-17:59 start, else `other`."""
    return "main" if moment.weekday() == MAIN_SLATE_WEEKDAY and moment.hour in MAIN_SLATE_HOURS else "other"


def original_columns(df: pd.DataFrame) -> list[str]:
    """The export's own columns: the 14 known ones first, then any column DraftKings has added since (kept
    verbatim, never dropped), never a derived one."""
    extras = [c for c in df.columns if c not in HISTORY_COLUMNS and c not in DERIVED_COLUMNS]
    return [*HISTORY_COLUMNS, *extras]


def read_export(source: str | os.PathLike | io.IOBase | bytes) -> pd.DataFrame:
    """One history export as text, every column as it was written. Raises `HistoryError` for a 0-byte file or
    a file without the expected header."""
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, (str, os.PathLike)):
        raw = Path(source).read_bytes()
    else:
        raw = source.read()
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    if not raw.strip():
        raise HistoryError("the history export is empty (0 bytes)")
    try:
        df = pd.read_csv(
            io.StringIO(raw.decode("utf-8-sig")), dtype=str, keep_default_na=False, na_filter=False
        )
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise HistoryError(f"could not read the history export as a CSV: {e}") from e
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in HISTORY_COLUMNS if c not in df.columns]
    if missing:
        raise HistoryError(f"not a DraftKings contest-history export: missing column(s) {missing}")
    return df[original_columns(df)].copy()


def enrich(df: pd.DataFrame, *, me: str = series_mod.MY_USERNAME) -> pd.DataFrame:
    """The original columns plus `DERIVED_COLUMNS`, NFL rows only."""
    base = df[original_columns(df)].copy()
    base = base[base["Sport"].str.strip().str.upper() == "NFL"].reset_index(drop=True)
    rows = []
    for rec in base.itertuples(index=False):
        moment = pd.to_datetime(rec.Contest_Date_EST, errors="coerce")
        if pd.isna(moment):
            season, week, slate = "", "", ""
        else:
            s, w = season_week(moment.date())
            season, week, slate = s, ("" if w is None else w), slate_of(moment.to_pydatetime())
        info = series_mod.classify(
            rec.Game_Type,
            rec.Entry,
            fee=money(rec.Entry_Fee),
            contest_entries=count(rec.Contest_Entries),
            places_paid=count(rec.Places_Paid),
            me=me,
        )
        rows.append(
            [
                season,
                week,
                slate,
                info.series,
                info.format,
                info.matchup,
                info.opponent,
                "" if info.entry_no is None else info.entry_no,
                "" if info.entry_max is None else info.entry_max,
                info.bankroll_class,
                info.format_class,
                "" if info.multiple is None else f"{info.multiple:g}",
            ]
        )
    derived = pd.DataFrame(rows, columns=DERIVED_COLUMNS, dtype=str)
    return pd.concat([base, derived], axis=1)


def read_log(log_path: str | os.PathLike) -> pd.DataFrame:
    """The log as text (empty frame with the log's columns when there is no log yet): the original columns
    (the 14 known ones, then any extra the exports carried), then the derived ones."""
    path = Path(log_path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=LOG_COLUMNS, dtype=str)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8-sig")
    for column in LOG_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[[*original_columns(df), *DERIVED_COLUMNS]]


@dataclass(frozen=True)
class MergeResult:
    added: int
    updated: int
    unchanged: int
    total: int
    dropped_other_sports: int
    dropped_no_key: int


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, quoting=csv.QUOTE_MINIMAL, encoding="utf-8")
    os.replace(tmp, path)


def merge_into(
    log_path: str | os.PathLike, export_path: str | os.PathLike, *, me: str = series_mod.MY_USERNAME
) -> MergeResult:
    """Merges one export into the log at `log_path` (created if missing).

    Rows are matched on `Entry_Key`. The export is taken as the newer record, so its row replaces the
    log's -- except that a row without a `Place` (a contest still in play when that export was taken) never
    replaces one that has a result, and a column only the log carries (one DraftKings has since dropped) is
    kept. Safe to re-run: the same export changes nothing, and the file is rewritten only when its content
    would change -- the derived columns included, so a changed rule in `series.py` re-labels every row on
    the next merge."""
    path = Path(log_path)
    export = read_export(export_path)
    export_nfl = export[export["Sport"].str.strip().str.upper() == "NFL"]
    dropped_other = len(export) - len(export_nfl)
    export = export_nfl[export_nfl["Entry_Key"].str.strip() != ""]
    dropped_no_key = len(export_nfl) - len(export)

    existing = read_log(path)
    originals = original_columns(pd.concat([existing.iloc[:0], export.iloc[:0]]))
    compared = [c for c in original_columns(export) if c != "Entry_Key"]
    log_rows = existing.reindex(columns=originals, fill_value="").set_index("Entry_Key")
    incoming = export.reindex(columns=originals, fill_value="").drop_duplicates("Entry_Key", keep="last")

    added = updated = unchanged = 0
    for key, row in incoming.set_index("Entry_Key").iterrows():
        if key not in log_rows.index:
            log_rows.loc[key] = row
            added += 1
            continue
        previous = log_rows.loc[key]
        scored_before = bool(str(previous["Place"]).strip())
        if (scored_before and not str(row["Place"]).strip()) or (previous[compared] == row[compared]).all():
            unchanged += 1
            continue
        log_rows.loc[key, compared] = row[compared]
        updated += 1

    merged = enrich(log_rows.reset_index(), me=me)
    merged["_sort"] = pd.to_datetime(merged["Contest_Date_EST"], errors="coerce")
    merged = merged.sort_values(["_sort", "Entry_Key"], kind="stable").drop(columns="_sort")
    merged = merged.reset_index(drop=True)

    def cells(frame: pd.DataFrame) -> list:
        return frame.fillna("").astype(str).to_numpy().tolist()

    on_disk = existing.reindex(columns=merged.columns, fill_value="").reset_index(drop=True)
    if not path.exists() or list(existing.columns) != list(merged.columns) or cells(merged) != cells(on_disk):
        _write_atomic(merged, path)
    return MergeResult(added, updated, unchanged, len(merged), dropped_other, dropped_no_key)


def typed(log: pd.DataFrame) -> pd.DataFrame:
    """The log with numbers as numbers, one row per entry, for the analyses.

    Columns: `entry_key`, `contest_key`, `date`, `season`, `week` (nullable ints), `slate`, `game_type`,
    `entry` (the raw name), `series`, `format`, `opponent`, `matchup`, `place`, `points`, `entries`,
    `places_paid`, `fee`, `winnings` (non-ticket + ticket), `prize_pool`, `payout_ratio`
    (`places_paid / entries`)."""
    out = pd.DataFrame(
        {
            "entry_key": log["Entry_Key"].astype(str),
            "contest_key": log["Contest_Key"].astype(str),
            "date": pd.to_datetime(log["Contest_Date_EST"], errors="coerce"),
            "season": pd.to_numeric(log["Season"], errors="coerce").astype("Int64"),
            "week": pd.to_numeric(log["Week"], errors="coerce").astype("Int64"),
            "slate": log["Slate"],
            "game_type": log["Game_Type"],
            "entry": log["Entry"],
            "series": log["Series"],
            "format": log["Format"],
            "opponent": log["Opponent"],
            "matchup": log["Matchup"],
            "multiple": pd.to_numeric(log["Multiple"], errors="coerce"),
            "place": log["Place"].map(count).astype("Int64"),
            "points": log["Points"].map(number).astype("Float64"),
            "entries": log["Contest_Entries"].map(count).astype("Int64"),
            "places_paid": log["Places_Paid"].map(count).astype("Int64"),
            "fee": log["Entry_Fee"].map(lambda v: float(money(v))),
            "winnings": [
                float(money(a) + money(b))
                for a, b in zip(log["Winnings_Non_Ticket"], log["Winnings_Ticket"], strict=True)
            ],
            "prize_pool": log["Prize_Pool"].map(lambda v: float(money(v))),
        }
    )
    out["payout_ratio"] = (out["places_paid"].astype("Float64") / out["entries"].astype("Float64")).astype(
        "Float64"
    )
    return out.reset_index(drop=True)
