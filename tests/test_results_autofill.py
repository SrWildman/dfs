from datetime import datetime
from decimal import Decimal

from dfs.config import ResultsConfig
from dfs.models import ContestEntry
from dfs.results_autofill import WeekResults, compute_week_results, write_results_updates

_SEASON = 2026
_WEEK1_DATE = datetime(2026, 9, 13, 13, 0)  # a Sunday in Week 1
_WEEK2_DATE = datetime(2026, 9, 20, 13, 0)  # a Sunday in Week 2


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


def test_entries_grouped_by_the_nfl_week_their_own_date_falls_in():
    entries = [
        _entry(entry_key="w1", contest_date=_WEEK1_DATE),
        _entry(entry_key="w2", contest_date=_WEEK2_DATE),
    ]
    results = compute_week_results(entries, _SEASON)
    assert set(results) == {1, 2}


def test_cash_pts_is_the_points_of_a_cash_classified_entry():
    # 50/50, entries=100, paid=50 -> ratio 0.5 -> cash (bankroll.classify_entry).
    entries = [
        _entry(entry_key="cash1", points=131.5, contest_entries=100, places_paid=50),
        _entry(entry_key="gpp1", points=999.0, contest_entries=1000, places_paid=100),
    ]
    results = compute_week_results(entries, _SEASON)
    assert results[1].cash_pts == 131.5


def test_cash_pts_is_none_when_no_cash_entry_has_scored_yet():
    entries = [_entry(entry_key="gpp1", points=None, contest_entries=1000, places_paid=100)]
    results = compute_week_results(entries, _SEASON)
    assert results[1].cash_pts is None


def test_h2h_entered_counts_only_two_entrant_contests():
    entries = [
        _entry(entry_key="h2h1", contest_entries=2, place=1),
        _entry(entry_key="h2h2", contest_entries=2, place=2),
        _entry(entry_key="fifty50", contest_entries=100, places_paid=50, place=10),
        _entry(entry_key="gpp1", contest_entries=1000, places_paid=100, place=500),
    ]
    results = compute_week_results(entries, _SEASON)
    assert results[1].h2h_entered == 2


def test_h2h_win_counts_first_place_h2h_entries_only():
    entries = [
        _entry(entry_key="h2h_won", contest_entries=2, place=1),
        _entry(entry_key="h2h_lost", contest_entries=2, place=2),
    ]
    results = compute_week_results(entries, _SEASON)
    assert results[1].h2h_win == 1


def test_real_week_one_numbers_match_the_live_results_row():
    # Matches the actual live Results!A2:F2 row: H2H Entered=20, Win=7.
    entries = [_entry(entry_key=f"h2h{i}", contest_entries=2, place=1) for i in range(7)]
    entries += [_entry(entry_key=f"h2h_lost{i}", contest_entries=2, place=2) for i in range(13)]
    entries.append(_entry(entry_key="cash", points=131.5, contest_entries=100, places_paid=50))

    results = compute_week_results(entries, _SEASON)
    assert results[1].h2h_entered == 20
    assert results[1].h2h_win == 7
    assert results[1].cash_pts == 131.5


class SpyResultsClient:
    def __init__(self, week_column: list[str]):
        self._week_column = week_column
        self.update_calls: list[tuple[str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        return [[v] if v else [] for v in self._week_column]


class RecordingResultsClient(SpyResultsClient):
    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))


_RESULTS_CFG = ResultsConfig(tab="Results", header_row=1, first_row=2, last_row=5)


def test_write_results_updates_finds_the_row_by_week_number():
    # Week column: rows 2-5 hold weeks 1,2,3,4 (row 2 = week 1, etc).
    client = RecordingResultsClient(["1", "2", "3", "4"])
    updates = {2: WeekResults(week=2, cash_pts=140.0, h2h_entered=5, h2h_win=3)}

    written = write_results_updates(client, _RESULTS_CFG, updates)

    assert written == [2]
    calls = dict(client.update_calls)
    assert calls["B3"] == [[140.0]]  # week 2 -> row 3
    assert calls["E3:F3"] == [[5, 3]]


def test_write_results_updates_skips_a_week_with_no_matching_row():
    client = RecordingResultsClient(["1", "2", "3", "4"])
    updates = {99: WeekResults(week=99, cash_pts=1.0, h2h_entered=1, h2h_win=1)}

    written = write_results_updates(client, _RESULTS_CFG, updates)

    assert written == []
    assert client.update_calls == []


def test_write_results_updates_never_blanks_cash_pts_when_none():
    # A week with no scored cash entry yet must not overwrite whatever
    # Cash Pts already holds -- only H2H Entered/Win are always written.
    client = RecordingResultsClient(["1", "2", "3", "4"])
    updates = {1: WeekResults(week=1, cash_pts=None, h2h_entered=0, h2h_win=0)}

    write_results_updates(client, _RESULTS_CFG, updates)

    touched = {a1 for a1, _rows in client.update_calls}
    assert "B2" not in touched
    assert "E2:F2" in touched


def test_write_results_updates_writes_zero_h2h_counts_not_blank():
    client = RecordingResultsClient(["1", "2", "3", "4"])
    updates = {1: WeekResults(week=1, cash_pts=None, h2h_entered=0, h2h_win=0)}

    write_results_updates(client, _RESULTS_CFG, updates)

    calls = dict(client.update_calls)
    assert calls["E2:F2"] == [[0, 0]]
