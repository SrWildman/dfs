"""Sync DK contest-entry history into the bankroll tab's existing Cash
Entry / GPP Entry ledgers.

These ledgers already exist in the live sheet (header row + a fixed range
of data rows, each row carrying its own "% Paid"/"Place %" formulas) --
this module only ever appends into the entry-name-through-Places-Paid
columns (A-H) and a dedicated dedupe-key column, via SheetsClient's
range-scoped read/update, so existing formulas are never touched and nothing
outside the configured row range is written.

Classification rule (confirmed with you directly, not guessed): a "cash"
entry pays out roughly half the field -- 50/50s and Double-Ups included --
or is a straight 2-person head-to-head; everything else (tiered/top-heavy
payouts) is GPP. Validated against your own contest history: real
Double-Ups sit at a Places_Paid/Contest_Entries ratio of ~0.44-0.52, H2H at
exactly 0.5, and the large GPP cluster sits at ~0.2-0.3 with a clean gap in
between -- see the 0.40-0.60 band below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd

from dfs import nfl_calendar
from dfs.config import EntryTableConfig
from dfs.log import get_logger
from dfs.models import ContestEntry
from dfs.sheets import SheetsClient

log = get_logger("bankroll")

CASH_PAYOUT_RATIO_LOW = 0.40
CASH_PAYOUT_RATIO_HIGH = 0.60


class BankrollError(Exception):
    pass


def entries_for_week(entries: list[ContestEntry], week: int, season: int) -> list[ContestEntry]:
    """Narrow a (typically season-long) DK contest-history export down to
    just the entries whose own contest date falls in `week`.

    Found live 2026-09-22: this filter didn't exist anywhere, so
    `sync_bucket` (below) was appending EVERY not-yet-synced entry in the
    export into whatever week's sheet `bankroll sync`/`week close` was run
    against -- including entries from earlier weeks. That's the "bankroll
    sync took entries from the wrong week" bug, and it's a separate
    mechanism from the `nfl_calendar` anchor bug (see
    `nfl_calendar.WEEK_ROLLOVER_LEAD_DAYS`), even though both are rooted in
    "how do we know what week it is": the Bankroll cash/GPP ledger ranges
    are cleared fresh every week by `weekly_reset.clear_previous_week`
    (called from `dfs week new`), each week gets its own spreadsheet, so
    an earlier week's entries are never in the new sheet's dedupe-key
    column and always look "new" to `sync_bucket`'s dedupe -- there was
    nothing stopping them from being appended into a week's ledger they
    don't belong to.

    Deliberately NOT used for `results_autofill.compute_week_results`,
    whose season-long backfill across every past week's own Results ROW
    is the intended behaviour -- this filter only applies to the ledger
    append, which is scoped to "this week" by design."""
    return [e for e in entries if nfl_calendar.week_for_date(e.contest_date.date(), season) == week]


def classify_entry(entry: ContestEntry) -> str:
    """Returns "cash" or "gpp"."""
    if not entry.contest_entries:
        return "gpp"
    ratio = (entry.places_paid or 0) / entry.contest_entries
    return "cash" if CASH_PAYOUT_RATIO_LOW <= ratio <= CASH_PAYOUT_RATIO_HIGH else "gpp"


def _money(value) -> Decimal:
    if pd.isna(value):
        return Decimal("0")
    s = str(value).replace("$", "").replace(",", "").strip()
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _int_or_none(value) -> int | None:
    return int(value) if pd.notna(value) else None


def _float_or_none(value) -> float | None:
    return float(value) if pd.notna(value) else None


def parse_contest_history(df: pd.DataFrame) -> list[ContestEntry]:
    """df columns match DK's contest-history export as-is: Sport, Game_Type,
    Entry_Key, Entry, Contest_Key, Contest_Date_EST, Place, Points,
    Winnings_Non_Ticket, Winnings_Ticket, Contest_Entries, Entry_Fee,
    Prize_Pool, Places_Paid."""
    entries = []
    for _, row in df.iterrows():
        entries.append(
            ContestEntry(
                sport=str(row["Sport"]),
                game_type=str(row["Game_Type"]),
                entry_key=str(row["Entry_Key"]),
                entry=str(row["Entry"]),
                contest_key=str(row["Contest_Key"]),
                contest_date=row["Contest_Date_EST"],
                place=_int_or_none(row["Place"]),
                points=_float_or_none(row["Points"]),
                winnings_non_ticket=_money(row["Winnings_Non_Ticket"]),
                winnings_ticket=_money(row["Winnings_Ticket"]),
                contest_entries=_int_or_none(row["Contest_Entries"]),
                entry_fee=_money(row["Entry_Fee"]),
                prize_pool=_money(row["Prize_Pool"]),
                places_paid=_int_or_none(row["Places_Paid"]),
            )
        )
    return entries


def _entry_row(e: ContestEntry) -> list:
    return [
        e.entry,
        e.place if e.place is not None else "",
        e.points if e.points is not None else "",
        float(e.total_winnings),
        e.contest_entries if e.contest_entries is not None else "",
        float(e.entry_fee),
        float(e.prize_pool),
        e.places_paid if e.places_paid is not None else "",
    ]


def _first_free_row(existing_col_a: list[list[str]], first_row: int) -> int:
    for i, row in enumerate(existing_col_a):
        if not row or not row[0].strip():
            return first_row + i
    return first_row + len(existing_col_a)


@dataclass
class BucketSyncResult:
    bucket: str
    written_entries: list[ContestEntry] = field(default_factory=list)
    already_synced: int = 0
    skipped_full: int = 0


def sync_bucket(
    client: SheetsClient,
    tab: str,
    table_cfg: EntryTableConfig,
    entries: list[ContestEntry],
    bucket: str,
) -> BucketSyncResult:
    key_col = table_cfg.entry_key_column
    key_range = f"{key_col}{table_cfg.first_row}:{key_col}{table_cfg.last_row}"
    existing_keys = {row[0] for row in client.read_range(tab, key_range) if row and row[0].strip()}

    already_synced = sum(1 for e in entries if e.entry_key in existing_keys)

    seen: set[str] = set()
    new_entries = []
    for e in entries:
        if e.entry_key in existing_keys or e.entry_key in seen:
            continue
        seen.add(e.entry_key)
        new_entries.append(e)

    a_values = client.read_range(tab, f"A{table_cfg.first_row}:A{table_cfg.last_row}")
    first_free_row = _first_free_row(a_values, table_cfg.first_row)
    available = max(0, table_cfg.last_row - first_free_row + 1)

    to_write = new_entries[:available]
    skipped_full = len(new_entries) - len(to_write)

    if to_write:
        last_row = first_free_row + len(to_write) - 1
        client.update_range(tab, f"A{first_free_row}:H{last_row}", [_entry_row(e) for e in to_write])
        client.update_range(
            tab, f"{key_col}{first_free_row}:{key_col}{last_row}", [[e.entry_key] for e in to_write]
        )
        log.info("wrote %d %s entries to %s!A%d:H%d", len(to_write), bucket, tab, first_free_row, last_row)

    return BucketSyncResult(
        bucket=bucket, written_entries=to_write, already_synced=already_synced, skipped_full=skipped_full
    )


def _money_value(s: str) -> Decimal:
    s = s.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(s) if s else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def _int_value(s: str) -> int | None:
    s = s.replace(",", "").strip()
    return int(s) if s else None


def _row_signature(row: list[str]) -> tuple:
    """A sheet row's identity, from fields that survive Sheets' own
    DISPLAY formatting (comma-grouped integers, `$`-prefixed currency)
    round-tripping through `Decimal`/`int` cleanly -- unlike `Points`
    (one decimal place shown, more precision stored) or `Winnings` (a
    sum of two source fields), neither used here since a formatted
    round-trip can't be trusted to reproduce the original value exactly.
    """
    padded = row + [""] * (8 - len(row))
    return (
        padded[0].strip(),
        _int_value(padded[1]),
        _int_value(padded[4]),
        _money_value(padded[5]),
        _money_value(padded[6]),
        _int_value(padded[7]),
    )


def _entry_signature(e: ContestEntry) -> tuple:
    return (e.entry.strip(), e.place, e.contest_entries, e.entry_fee, e.prize_pool, e.places_paid)


@dataclass
class BackfillResult:
    bucket: str
    backfilled: list[tuple[int, str]] = field(default_factory=list)  # (row, entry_key)
    ambiguous_rows: list[int] = field(default_factory=list)
    unmatched_rows: list[int] = field(default_factory=list)


def backfill_entry_keys(
    client: SheetsClient, tab: str, table_cfg: EntryTableConfig, entries: list[ContestEntry], bucket: str
) -> BackfillResult:
    """One-time repair for rows written before the dedupe-key column
    existed (or by some path that skipped it) -- confirmed live: a real
    synced sheet had zero entries in its `entry_key_column` despite
    `sync_bucket` supposedly writing one per row, and a later sync of
    overlapping DK contest history re-appended those same entries as if
    they were new, since `sync_bucket`'s dedupe can only ever check the
    key column. This never rewrites A-H (the row's own data) or an
    already-populated key -- only fills a genuinely blank key cell, and
    only when exactly one CSV entry's `_entry_signature` matches that
    row's `_row_signature`. A row matching zero or multiple entries is
    left alone and reported, never guessed.
    """
    key_col = table_cfg.entry_key_column
    rows = client.read_range(tab, f"A{table_cfg.first_row}:H{table_cfg.last_row}")
    keys = client.read_range(tab, f"{key_col}{table_cfg.first_row}:{key_col}{table_cfg.last_row}")

    by_signature: dict[tuple, list[ContestEntry]] = {}
    for e in entries:
        by_signature.setdefault(_entry_signature(e), []).append(e)

    result = BackfillResult(bucket=bucket)
    to_write: list[tuple[int, str]] = []
    for i, row in enumerate(rows):
        if not row or not row[0].strip():
            continue
        row_num = table_cfg.first_row + i
        existing_key = keys[i][0] if i < len(keys) and keys[i] else ""
        if existing_key.strip():
            continue
        candidates = by_signature.get(_row_signature(row), [])
        if len(candidates) == 1:
            to_write.append((row_num, candidates[0].entry_key))
        elif len(candidates) > 1:
            result.ambiguous_rows.append(row_num)
        else:
            result.unmatched_rows.append(row_num)

    for row_num, key in to_write:
        client.update_range(tab, f"{key_col}{row_num}:{key_col}{row_num}", [[key]])
        result.backfilled.append((row_num, key))

    return result
