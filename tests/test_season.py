from datetime import datetime
from decimal import Decimal

from dfs.config import SeasonConfig
from dfs.models import ContestEntry
from dfs.season import (
    WeekBankrollNet,
    extract_season_value_columns,
    read_ledger_week_totals,
    write_season_betting_and_ending,
    write_season_cash_gpp,
)
from dfs.sheet_bankroll_view import WeeklyBettingStats

_SEASON = 2026
_WEEK1_DATE = datetime(2026, 9, 13, 13, 0)
_WEEK2_DATE = datetime(2026, 9, 20, 13, 0)


def _entry(**overrides) -> ContestEntry:
    base = dict(
        sport="NFL",
        game_type="Classic",
        entry_key="k1",
        entry="e1",
        contest_key="c1",
        contest_date=_WEEK1_DATE,
        place=None,
        points=None,
        winnings_non_ticket=Decimal("0"),
        winnings_ticket=Decimal("0"),
        contest_entries=None,
        entry_fee=Decimal("0"),
        prize_pool=Decimal("0"),
        places_paid=None,
    )
    base.update(overrides)
    return ContestEntry(**base)


class LedgerClient:
    """Fakes `read_range_unformatted` for the Bankroll summary cells."""

    def __init__(self, cells: dict[str, object]):
        self.cells = cells
        self.reads: list[str] = []

    def read_range_unformatted(self, tab, a1):
        self.reads.append(a1)
        v = self.cells.get(a1)
        return [[v]] if v is not None else []


def test_read_ledger_week_totals_uses_the_sheets_own_summary_cells():
    """Season follows the ledger (a promo entry Sam zeroed stays zero), so
    Cash/GPP cost and net are the Bankroll summary's D/H on rows 12/13."""
    client = LedgerClient({"D12": 55.0, "H12": 56.0, "D13": 88.0, "H13": -18.0})
    got = read_ledger_week_totals(client, "Bankroll", 3)
    assert got == WeekBankrollNet(week=3, cash_net=56.0, cash_risked=55.0, gpp_net=-18.0, gpp_risked=88.0)
    assert sorted(client.reads) == ["D12", "D13", "H12", "H13"]


def test_read_ledger_week_totals_treats_blank_or_text_as_zero():
    client = LedgerClient({"D12": "n/a", "H12": None})
    got = read_ledger_week_totals(client, "Bankroll", 1)
    assert (got.cash_net, got.cash_risked, got.gpp_net, got.gpp_risked) == (0.0, 0.0, 0.0, 0.0)


class SpySeasonClient:
    def __init__(self, week_column: list[str]):
        self._week_column = week_column
        self.update_calls: list[tuple[str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        return [[v] if v else [] for v in self._week_column]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))


_SEASON_CFG = SeasonConfig(tab="Season", header_row=1, first_row=2, last_row=5)


def test_write_season_cash_gpp_finds_row_by_week_number():
    client = SpySeasonClient(["1", "2", "3", "4"])
    updates = {2: WeekBankrollNet(week=2, cash_net=8.0, cash_risked=10.0, gpp_net=-20.0, gpp_risked=20.0)}

    written = write_season_cash_gpp(client, _SEASON_CFG, updates)

    assert written == [2]
    calls = dict(client.update_calls)
    assert calls["B3:C3"] == [[8.0, -20.0]]  # week 2 -> row 3
    assert calls["G3:H3"] == [[10.0, 20.0]]


def test_write_season_cash_gpp_skips_unmatched_week():
    client = SpySeasonClient(["1", "2", "3", "4"])
    updates = {99: WeekBankrollNet(week=99, cash_net=1.0, cash_risked=1.0, gpp_net=1.0, gpp_risked=1.0)}

    written = write_season_cash_gpp(client, _SEASON_CFG, updates)

    assert written == []
    assert client.update_calls == []


_STATS = WeeklyBettingStats(settled=3, wins=1, losses=1, pushes=1, entered=30.0, net=-1.23, expected_wins=1.5)


def test_write_season_betting_and_ending_writes_stats_and_ending():
    client = SpySeasonClient(["1", "2", "3", "4"])

    ok = write_season_betting_and_ending(client, _SEASON_CFG, 3, betting_stats=_STATS, ending_bankroll=195.22)

    assert ok is True
    calls = dict(client.update_calls)
    assert calls["D4"] == [[-1.23]]  # week 3 -> row 4
    assert calls["I4"] == [[30.0]]
    assert calls["J4:M4"] == [[1, 1, 1, 1.5]]
    assert calls["F4"] == [[195.22]]


def test_write_season_betting_and_ending_leaves_unknown_fields_untouched():
    client = SpySeasonClient(["1", "2", "3", "4"])

    write_season_betting_and_ending(client, _SEASON_CFG, 1, betting_stats=None, ending_bankroll=100.0)

    touched = {a1 for a1, _rows in client.update_calls}
    assert touched == {"F2"}


def test_write_season_betting_and_ending_returns_false_for_unmatched_week():
    client = SpySeasonClient(["1", "2", "3", "4"])

    ok = write_season_betting_and_ending(client, _SEASON_CFG, 99, betting_stats=_STATS, ending_bankroll=1.0)

    assert ok is False
    assert client.update_calls == []


def test_extract_season_value_columns_skips_total_net_formula_column():
    rows = [["1", "10", "-20", "2.34", "SHOULD_NOT_CARRY", "191.34", "50", "60", "30", "1", "1", "1", "1.5"]]
    extracted = extract_season_value_columns(rows)

    assert extracted["A"] == [["1"]]
    assert extracted["B:D"] == [["10", "-20", "2.34"]]
    assert extracted["F"] == [["191.34"]]
    assert extracted["G:M"] == [["50", "60", "30", "1", "1", "1", "1.5"]]
    assert "E" not in extracted
    for group in extracted.values():
        assert "SHOULD_NOT_CARRY" not in group[0]
