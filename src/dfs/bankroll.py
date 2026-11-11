"""Sync DK contest-entry history into the bankroll tab's existing Cash
Entry / GPP Entry ledgers.

These ledgers already exist in the live sheet (header row + a fixed range
of data rows, each row carrying its own "% Paid"/"Place %" formulas) --
this module only ever appends into the entry-name-through-Places-Paid
columns (A-H) and a dedicated dedupe-key column, via SheetsClient's
range-scoped read/update, so existing formulas are never touched and nothing
outside the configured row range is written.

Classification rule (confirmed with you directly, not guessed): a "cash"
entry is a Classic Double-Up, 50/50 or straight 2-person head-to-head, by the
contest's series format (`contests.series.classify`); everything else
(tiered/top-heavy payouts, freerolls, Discord-Only and Private contests) is
GPP. The old rule, a Places_Paid/Contest_Entries ratio in the 0.40-0.60 band
(real Double-Ups ~0.44-0.52, H2H exactly 0.5, the large GPP cluster ~0.2-0.3),
filed two Week 7 Discord-Only contests as cash; it survives as
`payout_band_class`, the cross-check.
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
    return [e for e in entries if nfl_calendar.season_week_for_date(e.contest_date.date(), season) == week]


CLASSIC_GAME_TYPE = "Classic"


def payout_band_class(entry: ContestEntry) -> str:
    """The word "cash" or "gpp" from `Game_Type` and the payout ratio alone (a Classic contest paying 40-60%
    of the field is cash). No longer what the ledgers use: `classify_entry` reads the contest's format. Kept
    as the cross-check `contests.series.crosscheck` reports against."""
    if entry.game_type.strip().lower() != CLASSIC_GAME_TYPE.lower():
        return "gpp"
    if not entry.contest_entries:
        return "gpp"
    ratio = (entry.places_paid or 0) / entry.contest_entries
    return "cash" if CASH_PAYOUT_RATIO_LOW <= ratio <= CASH_PAYOUT_RATIO_HIGH else "gpp"


def classify_entry(entry: ContestEntry) -> str:
    """Returns "cash" or "gpp". "cash" is a Classic contest whose series format is Double Up, 50/50 or H2H
    (`contests.series.classify`, which reads the name, the fee and the field); everything else is GPP, so a
    $0-fee freeroll, a Discord-Only contest and a Private contest are never cash however they pay (found live
    on Week 7: two Discord-Only contests, UFC and NHL, paid 3 of 5 and landed in the Cash ledger from the
    payout-ratio band). Single Stat, Showdown and Best Ball are GPP too (Week 4: a $1 Single Stat contest,
    3 of 7 paid, fed Cash Pts and Cash Results a score that was not the cash lineup's). Their money still
    lands in the GPP ledger, so the bankroll stays whole."""
    # series imports this module for `payout_band_class`
    from dfs.contests import series

    if entry.game_type.strip().lower() != CLASSIC_GAME_TYPE.lower():
        return "gpp"
    info = series.classify(
        entry.game_type,
        entry.entry,
        fee=entry.entry_fee,
        contest_entries=entry.contest_entries,
        places_paid=entry.places_paid,
    )
    return "cash" if info.format in series.CASH_FORMATS else "gpp"


NFL_SPORT = "NFL"


def is_nfl(entry: ContestEntry) -> bool:
    return entry.sport.strip().upper() == NFL_SPORT


def ledger_bucket(entry: ContestEntry) -> str:
    """Which Bankroll ledger an entry goes in: "other" for any sport but NFL (Plan D item 2: MLB/NHL contests
    used to land in GPP, so Week 4's GPP read +$201 when NFL GPP was +$77), else `classify_entry`'s "cash"
    or "gpp"."""
    return classify_entry(entry) if is_nfl(entry) else "other"


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


def _key_text(value) -> str:
    """A dedupe key as the string `ContestEntry.entry_key` holds. Keys are
    read UNFORMATTED: a stray cell format (a `$` currency or comma-grouped
    number format on a key cell -- seen live on Bankroll's L39/L89) changes
    a formatted read to "$5,269,229,435.00", which never matches the key and
    made `week close` append the entry a second time."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _read_entry_keys(client: SheetsClient, tab: str, key_range: str) -> set[str]:
    return {
        _key_text(row[0])
        for row in client.read_range_unformatted(tab, key_range)
        if row and _key_text(row[0])
    }


def sync_bucket(
    client: SheetsClient,
    tab: str,
    table_cfg: EntryTableConfig,
    entries: list[ContestEntry],
    bucket: str,
) -> BucketSyncResult:
    key_col = table_cfg.entry_key_column
    key_range = f"{key_col}{table_cfg.first_row}:{key_col}{table_cfg.last_row}"
    existing_keys = _read_entry_keys(client, tab, key_range)

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


def other_sports_by_week(entries: list[ContestEntry], season: int) -> dict[int, tuple[float, float]]:
    """{NFL week: (net, entry fees)} of the non-NFL entries in a contest-history export, by the NFL week each
    contest's date falls in (`nfl_calendar.season_week_for_date`, the same rule the ledgers use)."""
    out: dict[int, tuple[float, float]] = {}
    for e in entries:
        if is_nfl(e):
            continue
        week = nfl_calendar.season_week_for_date(e.contest_date.date(), season)
        if week is None:
            continue
        net, fees = out.get(week, (0.0, 0.0))
        out[week] = (net + float(e.total_winnings - e.entry_fee), fees + float(e.entry_fee))
    return out
