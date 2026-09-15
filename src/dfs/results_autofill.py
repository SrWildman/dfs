"""Fix 2.16/2.17: derives Results-tab updates (Week, Cash Pts, H2H
Entered, H2H Win) from DK contest-entry history, sorted into NFL weeks by
each entry's own contest date -- not by assuming a CSV export or an
EntriesRaw paste covers exactly one week, and not by asking which week
it's for. Cash Line and the three team-colour columns stay Sam's own
typed input; nothing here touches them, and neither does the caller (see
`bankroll.write_results_updates`).

A DK contest-history export accumulates the whole season, so a single
`dfs week close --csv <file>` run can (and should) backfill every past
week's Results row it has real data for, not just "this week's" -- the
per-week grouping here makes that automatic rather than something Sam
has to manage by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs import nfl_calendar
from dfs.bankroll import classify_entry
from dfs.config import ResultsConfig
from dfs.models import ContestEntry
from dfs.sheets import SheetsClient

# A straight 1v1 -- see bankroll.py's own classification docstring. This
# is a strictly narrower condition than classify_entry's "cash" bucket
# (which also includes 50/50s and double-ups at the same ~0.5 payout
# ratio), so an H2H entry is always also "cash", but not the reverse.
H2H_CONTEST_ENTRIES = 2


@dataclass
class WeekResults:
    week: int
    cash_pts: float | None
    h2h_entered: int
    h2h_win: int


def compute_week_results(entries: list[ContestEntry], season: int) -> dict[int, WeekResults]:
    """One `WeekResults` per NFL week present in `entries`, keyed by week
    number. `cash_pts` is the first cash-classified entry's `Points` found
    for that week -- entering the same lineup into several cash-style
    contests (the normal case: multiple 50/50s/double-ups on one lineup)
    produces several entries with identical `Points`, so "first" and "max"
    agree in practice; `None` if no cash entry has a `Points` value yet
    (the slate hasn't finished scoring)."""
    by_week: dict[int, list[ContestEntry]] = {}
    for e in entries:
        week = nfl_calendar.week_for_date(e.contest_date.date(), season)
        by_week.setdefault(week, []).append(e)

    results: dict[int, WeekResults] = {}
    for week, week_entries in by_week.items():
        cash_points = [e.points for e in week_entries if e.points is not None and classify_entry(e) == "cash"]
        h2h_entries = [e for e in week_entries if e.contest_entries == H2H_CONTEST_ENTRIES]
        results[week] = WeekResults(
            week=week,
            cash_pts=cash_points[0] if cash_points else None,
            h2h_entered=len(h2h_entries),
            h2h_win=sum(1 for e in h2h_entries if e.place == 1),
        )
    return results


def write_results_updates(
    client: SheetsClient, results_cfg: ResultsConfig, updates: dict[int, WeekResults]
) -> list[int]:
    """Writes Cash Pts / H2H Entered / H2H Win (columns B, E, F) into each
    week's already-existing Results row, found by matching column A
    against the week number -- Results' rows are pre-built 1-18 in the
    template, so this only ever fills an existing row, never inserts one.
    Column C (Cash Line) and columns H/I/J (team colours) are Sam's own
    typed input and are never touched here; neither are D/G, the two
    formula columns.

    Cash Pts is left alone (not blanked) for a week `compute_week_results`
    found no scored cash entry for yet -- a slate that hasn't finished
    scoring should never overwrite a real number with a blank. H2H
    Entered/Win are always written: 0 is a real, meaningful count, not a
    placeholder for "unknown".

    Returns the week numbers actually written (a week present in
    `updates` with no matching row in Results is silently skipped, not
    an error -- it just means that week's row hasn't been reached yet).
    """
    tab = results_cfg.tab
    a_values = client.read_range(tab, f"A{results_cfg.first_row}:A{results_cfg.last_row}")
    row_by_week: dict[int, int] = {}
    for i, row in enumerate(a_values):
        cell = row[0].strip() if row and row[0] else ""
        if cell.isdigit():
            row_by_week[int(cell)] = results_cfg.first_row + i

    written = []
    for week, stats in sorted(updates.items()):
        row = row_by_week.get(week)
        if row is None:
            continue
        if stats.cash_pts is not None:
            client.update_range(tab, f"B{row}", [[stats.cash_pts]])
        client.update_range(tab, f"E{row}:F{row}", [[stats.h2h_entered, stats.h2h_win]])
        written.append(week)
    return written
