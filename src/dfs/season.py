"""Round 5, item 7d: the Season tab's per-week Cash/GPP/Betting numbers.

Every figure comes from that week's OWN Bankroll tab -- the ledger Sam
actually reconciles (Round 5, 2026-09-29: "Season should follow the ledger;
I sometimes zero things out because of promos"). Cash/GPP net and cost are
read from the weekly summary rows; Betting net/risked/record and the Ending
bankroll are written at `dfs week close` (see `cli.py`'s wiring). The DK
export is no longer used to backfill Season -- it only fills the ledger
itself and Results.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.config import SeasonConfig
from dfs.sheet_bankroll_view import (
    CASH_SUMMARY_ROW,
    GPP_SUMMARY_ROW,
    SUMMARY_COST_COLUMN,
    SUMMARY_NET_COLUMN,
    WeeklyBettingStats,
)
from dfs.sheets import SheetsClient

# Season tab's fixed column layout (see sheet_season_view.py for the full
# tab build) -- never hardcoded elsewhere, this module and that one are
# the only two places these letters appear.
WEEK_COLUMN = "A"
CASH_NET_COLUMN = "B"
GPP_NET_COLUMN = "C"
BETTING_NET_COLUMN = "D"
TOTAL_NET_COLUMN = "E"  # formula, never written here
ENDING_BANKROLL_COLUMN = "F"
CASH_RISKED_COLUMN = "G"
GPP_RISKED_COLUMN = "H"
BETTING_RISKED_COLUMN = "I"
BETTING_WINS_COLUMN = "J"
BETTING_LOSSES_COLUMN = "K"
BETTING_PUSHES_COLUMN = "L"
BETTING_EXPECTED_WINS_COLUMN = "M"

# `dfs week new` carry-forward (see `extract_season_value_columns`) --
# skips E (Total Net) and N-Q (cumulative), all formulas that recompute
# correctly on the new sheet once the columns below are carried.
SEASON_VALUE_COLUMN_RANGES = ["A", "B:D", "F", "G:M"]


@dataclass
class WeekBankrollNet:
    week: int
    cash_net: float
    cash_risked: float
    gpp_net: float
    gpp_risked: float


def read_ledger_week_totals(client: SheetsClient, bankroll_tab: str, week: int) -> WeekBankrollNet:
    """This week's Cash/GPP net and cost as the sheet's OWN weekly summary shows
    them (`Bankroll!D12/H12` and `D13/H13`), not re-derived from the DK export.

    Round 5 (Sam, 2026-09-29): "Season should follow the ledger. I sometimes
    zero things out because of promos." A DK export knows what DraftKings
    charged; the ledger knows what Sam actually paid. Read UNFORMATTED so a
    currency display format can't turn a number into a string."""

    def _num(cell: str) -> float:
        rows = client.read_range_unformatted(bankroll_tab, cell)
        v = rows[0][0] if rows and rows[0] else 0
        return float(v) if isinstance(v, (int, float)) else 0.0

    cost, net = SUMMARY_COST_COLUMN, SUMMARY_NET_COLUMN
    return WeekBankrollNet(
        week=week,
        cash_net=_num(f"{net}{CASH_SUMMARY_ROW}"),
        cash_risked=_num(f"{cost}{CASH_SUMMARY_ROW}"),
        gpp_net=_num(f"{net}{GPP_SUMMARY_ROW}"),
        gpp_risked=_num(f"{cost}{GPP_SUMMARY_ROW}"),
    )


def _find_week_row(client: SheetsClient, tab: str, cfg: SeasonConfig, week: int) -> int | None:
    a_values = client.read_range(tab, f"{WEEK_COLUMN}{cfg.first_row}:{WEEK_COLUMN}{cfg.last_row}")
    for i, row in enumerate(a_values):
        cell = row[0].strip() if row and row[0] else ""
        if cell.isdigit() and int(cell) == week:
            return cfg.first_row + i
    return None


def write_season_cash_gpp(
    client: SheetsClient, cfg: SeasonConfig, updates: dict[int, WeekBankrollNet]
) -> list[int]:
    """Writes Cash/GPP net + risked (columns B/C/G/H) into each week's
    already-existing Season row, found by matching column A -- rows are
    pre-built 1-`nfl_calendar.MAX_WEEK` (see `sheet_season_view.py`), never
    inserted. A week present in `updates` with no matching row is silently
    skipped, same convention as `results_autofill.write_results_updates`."""
    tab = cfg.tab
    a_values = client.read_range(tab, f"{WEEK_COLUMN}{cfg.first_row}:{WEEK_COLUMN}{cfg.last_row}")
    row_by_week: dict[int, int] = {}
    for i, row in enumerate(a_values):
        cell = row[0].strip() if row and row[0] else ""
        if cell.isdigit():
            row_by_week[int(cell)] = cfg.first_row + i

    written = []
    for week, stats in sorted(updates.items()):
        row = row_by_week.get(week)
        if row is None:
            continue
        client.update_range(
            tab, f"{CASH_NET_COLUMN}{row}:{GPP_NET_COLUMN}{row}", [[stats.cash_net, stats.gpp_net]]
        )
        client.update_range(
            tab,
            f"{CASH_RISKED_COLUMN}{row}:{GPP_RISKED_COLUMN}{row}",
            [[stats.cash_risked, stats.gpp_risked]],
        )
        written.append(week)
    return written


def write_season_betting_and_ending(
    client: SheetsClient,
    cfg: SeasonConfig,
    week: int,
    *,
    betting_stats: WeeklyBettingStats | None = None,
    ending_bankroll: float | None = None,
) -> bool:
    """Writes this week's Betting net/risked/wins/losses/pushes/expected
    wins (columns D, I, J, K, L, M) and Ending bankroll (column F) --
    called at `dfs week close` time with `betting_stats` computed by
    `sheet_bankroll_view.compute_weekly_betting_stats` off the closing
    week's own Bankroll tab, and `ending_bankroll` read straight from that
    tab's B2. Never computed here -- there is no DK Sportsbook export to
    derive either from. `None` leaves those cells untouched rather than
    blanking them (a week with no bets yet, or an Ending balance not yet
    known, must not overwrite a real prior value with blank -- same
    "don't invent it, don't erase it" convention
    `results_autofill.write_results_updates` uses for Cash Pts). Returns
    False if `week` has no matching row (nothing written)."""
    tab = cfg.tab
    row = _find_week_row(client, tab, cfg, week)
    if row is None:
        return False
    if betting_stats is not None:
        client.update_range(
            tab,
            f"{BETTING_NET_COLUMN}{row}",
            [[betting_stats.net]],
        )
        client.update_range(tab, f"{BETTING_RISKED_COLUMN}{row}", [[betting_stats.entered]])
        client.update_range(
            tab,
            f"{BETTING_WINS_COLUMN}{row}:{BETTING_EXPECTED_WINS_COLUMN}{row}",
            [[betting_stats.wins, betting_stats.losses, betting_stats.pushes, betting_stats.expected_wins]],
        )
    if ending_bankroll is not None:
        client.update_range(tab, f"{ENDING_BANKROLL_COLUMN}{row}", [[ending_bankroll]])
    return True


def extract_season_value_columns(rows: list[list[str]]) -> dict[str, list[list[str]]]:
    """`rows` is the Season tab's A:M data range (one inner list per row,
    0-indexed A=0 .. M=12). Returns the same rows split into the
    column-groups in SEASON_VALUE_COLUMN_RANGES for `dfs week new`'s
    carry-forward -- skipping column E (Total Net, a formula) entirely, so
    a carryover write never overwrites a formula with a stale literal
    value. Mirrors `week.extract_results_value_columns` exactly."""

    def cell(row: list[str], idx: int) -> str:
        return row[idx] if idx < len(row) else ""

    return {
        "A": [[cell(r, 0)] for r in rows],
        "B:D": [[cell(r, 1), cell(r, 2), cell(r, 3)] for r in rows],
        "F": [[cell(r, 5)] for r in rows],
        "G:M": [[cell(r, i) for i in range(6, 13)] for r in rows],
    }
