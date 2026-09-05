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

from dfs.config import EntryTableConfig
from dfs.log import get_logger
from dfs.models import ContestEntry
from dfs.sheets import SheetsClient

log = get_logger("bankroll")

CASH_PAYOUT_RATIO_LOW = 0.40
CASH_PAYOUT_RATIO_HIGH = 0.60


class BankrollError(Exception):
    pass


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
    existing_keys = {
        row[0] for row in client.read_range(tab, key_range) if row and row[0].strip()
    }

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
        client.update_range(tab, f"{key_col}{first_free_row}:{key_col}{last_row}", [[e.entry_key] for e in to_write])
        log.info("wrote %d %s entries to %s!A%d:H%d", len(to_write), bucket, tab, first_free_row, last_row)

    return BucketSyncResult(
        bucket=bucket, written_entries=to_write, already_synced=already_synced, skipped_full=skipped_full
    )
