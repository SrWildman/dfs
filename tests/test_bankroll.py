from decimal import Decimal

import pandas as pd
import pytest

from dfs.bankroll import (
    CASH_PAYOUT_RATIO_HIGH,
    CASH_PAYOUT_RATIO_LOW,
    classify_entry,
    parse_contest_history,
    sync_bucket,
)
from dfs.config import EntryTableConfig
from dfs.models import ContestEntry


def _entry(entries=100, places_paid=50, **kw) -> ContestEntry:
    defaults = dict(
        sport="NFL",
        game_type="Classic",
        entry_key="E1",
        entry="Some Contest",
        contest_key="C1",
        contest_date="2026-09-13T13:00:00",
        contest_entries=entries,
        places_paid=places_paid,
    )
    defaults.update(kw)
    return ContestEntry(**defaults)


def test_classify_double_up_is_cash():
    # real ratio from the user's own history: 1000/2298 = 0.435
    e = _entry(entries=2298, places_paid=1000)
    assert classify_entry(e) == "cash"


def test_classify_h2h_is_cash():
    e = _entry(entries=2, places_paid=1)
    assert classify_entry(e) == "cash"


def test_classify_large_gpp_is_gpp():
    # real ratio from the user's own history: 752/3676 = 0.205
    e = _entry(entries=3676, places_paid=752)
    assert classify_entry(e) == "gpp"


def test_classify_zero_entries_defaults_gpp():
    e = _entry(entries=None, places_paid=None)
    assert classify_entry(e) == "gpp"


def test_classify_boundary_values():
    e_low = _entry(entries=100, places_paid=int(CASH_PAYOUT_RATIO_LOW * 100))
    assert classify_entry(e_low) == "cash"
    e_high = _entry(entries=100, places_paid=int(CASH_PAYOUT_RATIO_HIGH * 100))
    assert classify_entry(e_high) == "cash"
    e_just_under = _entry(entries=100, places_paid=int(CASH_PAYOUT_RATIO_LOW * 100) - 1)
    assert classify_entry(e_just_under) == "gpp"


def test_parse_contest_history_maps_dk_columns():
    df = pd.DataFrame(
        [
            {
                "Sport": "NFL", "Game_Type": "Classic", "Entry_Key": "5050021727",
                "Entry": "NFL $25K Engage Eight [5 Entry Max]", "Contest_Key": "187479226",
                "Contest_Date_EST": "2026-09-13 13:00:00", "Place": 420, "Points": 159.46,
                "Winnings_Non_Ticket": "$16.00", "Winnings_Ticket": "$0.00",
                "Contest_Entries": 3676, "Entry_Fee": "$8.00", "Prize_Pool": "$25,000.00",
                "Places_Paid": 752,
            }
        ]
    )
    entries = parse_contest_history(df)
    assert len(entries) == 1
    e = entries[0]
    assert e.entry_key == "5050021727"
    assert e.winnings_non_ticket == Decimal("16.00")
    assert e.prize_pool == Decimal("25000.00")
    assert e.total_winnings == Decimal("16.00")


class FakeSheetsClient:
    """In-memory stand-in for SheetsClient's range read/update, keyed by tab."""

    def __init__(self):
        self.cells: dict[str, dict[int, dict[str, str]]] = {}  # tab -> row -> col letter -> value

    def _row_col(self, a1_range: str):
        # supports "A17:A59" or "A17:H20" single-column or rectangular ranges
        start, end = a1_range.split(":")
        return start, end

    def read_range(self, tab, a1_range):
        import re

        m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1_range)
        col_start, row_start, col_end, row_end = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        rows = self.cells.get(tab, {})
        result = []
        for r in range(row_start, row_end + 1):
            row_data = rows.get(r, {})
            if col_start == col_end:
                val = row_data.get(col_start, "")
                if val:
                    result.append([val])
                # gspread trims trailing empty rows; stop appending once we hit
                # a blank row only if nothing after it either -- simplify: skip blanks
            else:
                result.append([row_data.get(chr(c), "") for c in range(ord(col_start), ord(col_end) + 1)])
        # trim trailing fully-blank rows (mimics gspread's get())
        while result and (not result[-1] or not any(str(v).strip() for v in result[-1])):
            result.pop()
        return result

    def update_range(self, tab, a1_range, rows):
        import re

        m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1_range)
        col_start, row_start, col_end, row_end = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        self.cells.setdefault(tab, {})
        for i, row_values in enumerate(rows):
            r = row_start + i
            self.cells[tab].setdefault(r, {})
            if col_start == col_end:
                self.cells[tab][r][col_start] = str(row_values[0])
            else:
                for j, v in enumerate(row_values):
                    self.cells[tab][r][chr(ord(col_start) + j)] = str(v)


@pytest.fixture
def table_cfg():
    return EntryTableConfig(header_row=16, first_row=17, last_row=20, entry_key_column="L")


def test_sync_bucket_writes_new_entries_to_first_free_row(table_cfg):
    client = FakeSheetsClient()
    entries = [_entry(entry_key="E1", entry="Contest A"), _entry(entry_key="E2", entry="Contest B")]
    result = sync_bucket(client, "Bankroll", table_cfg, entries, "cash")

    assert len(result.written_entries) == 2
    assert result.already_synced == 0
    assert result.skipped_full == 0
    assert client.cells["Bankroll"][17]["A"] == "Contest A"
    assert client.cells["Bankroll"][17]["L"] == "E1"
    assert client.cells["Bankroll"][18]["A"] == "Contest B"


def test_sync_bucket_skips_already_synced_entries(table_cfg):
    client = FakeSheetsClient()
    entries = [_entry(entry_key="E1", entry="Contest A")]
    sync_bucket(client, "Bankroll", table_cfg, entries, "cash")

    # re-run with the same entry plus a new one
    entries2 = [_entry(entry_key="E1", entry="Contest A"), _entry(entry_key="E2", entry="Contest B")]
    result = sync_bucket(client, "Bankroll", table_cfg, entries2, "cash")

    assert result.already_synced == 1
    assert len(result.written_entries) == 1
    assert result.written_entries[0].entry_key == "E2"
    # Contest A's row must be untouched, not duplicated
    assert client.cells["Bankroll"][17]["A"] == "Contest A"
    assert client.cells["Bankroll"][18]["A"] == "Contest B"


def test_sync_bucket_dedupes_within_same_batch(table_cfg):
    client = FakeSheetsClient()
    entries = [_entry(entry_key="E1", entry="Contest A"), _entry(entry_key="E1", entry="Contest A dup")]
    result = sync_bucket(client, "Bankroll", table_cfg, entries, "cash")
    assert len(result.written_entries) == 1


def test_sync_bucket_reports_skipped_when_table_is_full(table_cfg):
    client = FakeSheetsClient()
    # table has rows 17-20 (4 rows); fill it, then try to add 2 more
    first_batch = [_entry(entry_key=f"E{i}", entry=f"Contest {i}") for i in range(4)]
    sync_bucket(client, "Bankroll", table_cfg, first_batch, "cash")

    second_batch = [_entry(entry_key=f"E{i}", entry=f"Contest {i}") for i in range(4, 6)]
    result = sync_bucket(client, "Bankroll", table_cfg, second_batch, "cash")

    assert len(result.written_entries) == 0
    assert result.skipped_full == 2


def test_sync_bucket_never_writes_beyond_column_h(table_cfg):
    client = FakeSheetsClient()
    entries = [_entry(entry_key="E1", entry="Contest A")]
    sync_bucket(client, "Bankroll", table_cfg, entries, "cash")
    row = client.cells["Bankroll"][17]
    assert set(row.keys()) <= {"A", "B", "C", "D", "E", "F", "G", "H", "L"}
