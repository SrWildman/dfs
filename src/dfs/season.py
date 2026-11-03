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

from dfs.config import BankrollConfig, SeasonConfig
from dfs.sheet_bankroll_view import (
    CASH_SUMMARY_ROW,
    GPP_SUMMARY_ROW,
    SUMMARY_COST_COLUMN,
    SUMMARY_NET_COLUMN,
    WeeklyBettingStats,
    compute_weekly_betting_stats,
)
from dfs.sheets import SheetsClient

ENDING_BANKROLL_CELL = "B2"  # Bankroll's Ending Bankroll (= B1 Starting + B9 Weekly Net)

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
# Plan D item 2: DK contests in any sport but NFL, from the Bankroll tab's Other Sports line (row 11).
OTHER_NET_COLUMN = "R"
OTHER_RISKED_COLUMN = "S"
LAST_VALUE_COLUMN = OTHER_RISKED_COLUMN

# `dfs week new` carry-forward (see `extract_season_value_columns`) --
# skips E (Total Net) and N-Q (cumulative), all formulas that recompute
# correctly on the new sheet once the columns below are carried.
SEASON_VALUE_COLUMN_RANGES = ["A", "B:D", "F", "G:M", "R:S"]


@dataclass
class WeekBankrollNet:
    week: int
    cash_net: float
    cash_risked: float
    gpp_net: float
    gpp_risked: float
    other_net: float = 0.0
    other_risked: float = 0.0


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
    # Plan D item 2: the Other Sports line (row 11); blank (0) on a sheet from before it existed.
    from dfs.sheet_bankroll_other import OTHER_SUMMARY_ROW

    return WeekBankrollNet(
        week=week,
        cash_net=_num(f"{net}{CASH_SUMMARY_ROW}"),
        cash_risked=_num(f"{cost}{CASH_SUMMARY_ROW}"),
        gpp_net=_num(f"{net}{GPP_SUMMARY_ROW}"),
        gpp_risked=_num(f"{cost}{GPP_SUMMARY_ROW}"),
        other_net=_num(f"{net}{OTHER_SUMMARY_ROW}"),
        other_risked=_num(f"{cost}{OTHER_SUMMARY_ROW}"),
    )


def _find_week_row(client: SheetsClient, tab: str, cfg: SeasonConfig, week: int) -> int | None:
    a_values = client.read_range(tab, f"{WEEK_COLUMN}{cfg.first_row}:{WEEK_COLUMN}{cfg.last_row}")
    for i, row in enumerate(a_values):
        cell = row[0].strip() if row and row[0] else ""
        if cell.isdigit() and int(cell) == week:
            return cfg.first_row + i
    return None


def write_season_cash_gpp(
    client: SheetsClient,
    cfg: SeasonConfig,
    updates: dict[int, WeekBankrollNet],
    *,
    other_columns: bool = True,
) -> list[int]:
    """Writes Cash/GPP net + risked (columns B/C/G/H) into each week's
    already-existing Season row, found by matching column A -- rows are
    pre-built 1-`nfl_calendar.MAX_WEEK` (see `sheet_season_view.py`), never
    inserted. A week present in `updates` with no matching row is silently
    skipped, same convention as `results_autofill.write_results_updates`.
    Also writes Other Net/Risked (R/S) unless `other_columns` is False (a
    Season tab from before Plan D item 2: see `has_other_columns`)."""
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
        if other_columns:
            client.update_range(
                tab,
                f"{OTHER_NET_COLUMN}{row}:{OTHER_RISKED_COLUMN}{row}",
                [[stats.other_net, stats.other_risked]],
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


def has_other_columns(client: SheetsClient, cfg: SeasonConfig) -> bool:
    """True when the Season tab has the Other Net / Other Risked columns (R/S, Plan D item 2)."""
    from dfs.sheet_season_view import OTHER_NET_HEADER, OTHER_RISKED_HEADER

    rows = client.read_range(
        cfg.tab, f"{OTHER_NET_COLUMN}{cfg.header_row}:{OTHER_RISKED_COLUMN}{cfg.header_row}"
    )
    found = [str(v).strip() for v in (rows[0] if rows else [])]
    return found == [OTHER_NET_HEADER, OTHER_RISKED_HEADER]


def extract_season_value_columns(rows: list[list[str]]) -> dict[str, list[list[str]]]:
    """`rows` is the Season tab's A:S data range (one inner list per row,
    0-indexed A=0 .. S=18; R:S are Other Net/Risked, Plan D item 2). Returns the same rows split into the
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
        "R:S": [[cell(r, 17), cell(r, 18)] for r in rows],
    }


# --- a closed week's whole Season row, read from its own Bankroll tab
# -------------------------------------------


@dataclass
class WeekClose:
    """Everything a week's Season row holds, as that week's own Bankroll tab shows it now."""

    totals: WeekBankrollNet
    betting: WeeklyBettingStats | None
    ending_bankroll: float | None


def read_week_close(client: SheetsClient, bankroll: BankrollConfig, week: int) -> WeekClose:
    """Reads the ledger's weekly summary rows (Cash/GPP/Other), the Betting ledger (as
    `compute_weekly_betting_stats` numbers) and Ending Bankroll (B2), all unformatted. Used by `dfs week
    close` for the week it closes, and by `dfs week new` / the next `week close` to rewrite the PREVIOUS
    week's row: Sam edits a closed week's ledger after the close (Week 8: a $3 promo fee zeroed, a $10 win
    corrected to $12.50, the betting ledger settled), and the Season row taken at the close then disagrees
    with the next week's Starting Bankroll (the $16.33 gap, Plan D item 2)."""
    totals = read_ledger_week_totals(client, bankroll.tab, week)
    betting = None
    if bankroll.bets is not None:
        rows = client.read_range_unformatted(
            bankroll.tab, f"B{bankroll.bets.first_row}:E{bankroll.bets.last_row}"
        )

        def _cell(row: list, idx: int) -> float | None:
            v = row[idx] if idx < len(row) else None
            return v if isinstance(v, (int, float)) else None

        betting = compute_weekly_betting_stats([(_cell(r, 0), _cell(r, 2), _cell(r, 3)) for r in rows])
    ending_rows = client.read_range_unformatted(bankroll.tab, ENDING_BANKROLL_CELL)
    ending = ending_rows[0][0] if ending_rows and ending_rows[0] else None
    return WeekClose(totals, betting, ending if isinstance(ending, (int, float)) else None)


def write_week_close(
    client: SheetsClient, cfg: SeasonConfig, close: WeekClose, *, other_columns: bool
) -> bool:
    """Writes the whole row for `close.totals.week` (B:D, F:M, and R:S when the tab has them). False when the
    tab has no row for that week."""
    written = write_season_cash_gpp(
        client, cfg, {close.totals.week: close.totals}, other_columns=other_columns
    )
    if not written:
        return False
    write_season_betting_and_ending(
        client, cfg, close.totals.week, betting_stats=close.betting, ending_bankroll=close.ending_bankroll
    )
    return True


def read_season_row(client: SheetsClient, cfg: SeasonConfig, week: int) -> list:
    """The week's Season row A:S, unformatted (empty when the tab has no row for it)."""
    row = _find_week_row(client, cfg.tab, cfg, week)
    if row is None:
        return []
    rows = client.read_range_unformatted(cfg.tab, f"{WEEK_COLUMN}{row}:{LAST_VALUE_COLUMN}{row}")
    return list(rows[0]) if rows else []


SEASON_ROW_LABELS = {
    1: "Cash Net",
    2: "GPP Net",
    3: "Betting Net",
    5: "Ending Bankroll",
    6: "Cash Risked",
    7: "GPP Risked",
    8: "Betting Risked",
    17: "Other Net",
    18: "Other Risked",
}


def season_row_changes(before: list, after: list) -> list[str]:
    """ "GPP Net -53.50 -> -48.00"-style lines for the money cells that differ between two reads of a row."""

    def num(row: list, i: int) -> float | None:
        v = row[i] if i < len(row) else None
        return float(v) if isinstance(v, (int, float)) else None

    out = []
    for i, label in SEASON_ROW_LABELS.items():
        a, b = num(before, i), num(after, i)
        if (a or 0.0) != (b or 0.0) and not (a is None and b is None):
            fa = "blank" if a is None else f"{a:.2f}"
            fb = "blank" if b is None else f"{b:.2f}"
            out.append(f"{label} {fa} -> {fb}")
    return out


@dataclass(frozen=True)
class OtherSplit:
    week: int
    gpp_net: tuple[float, float]  # before, after
    gpp_risked: tuple[float, float]
    other_net: float
    other_risked: float


def plan_other_split(rows: list[list], by_week: dict[int, tuple[float, float]]) -> list[OtherSplit]:
    """The one-time history split (Plan D item 2): for each Season row (A:S, unformatted) whose week had
    non-NFL entries and whose Other Net (R) is still blank, move those entries' net and fees out of GPP
    (C/H) into Other (R/S). Every past ledger filed them as GPP (`bankroll.classify_entry`), so that is
    where they are. A row with R already written is skipped, so a re-run moves nothing twice."""

    def num(row: list, i: int) -> float | None:
        v = row[i] if i < len(row) else None
        return float(v) if isinstance(v, (int, float)) else None

    plan = []
    for row in rows:
        week = num(row, 0)
        if week is None or int(week) not in by_week or num(row, 17) is not None:
            continue
        gpp_net, gpp_risked = num(row, 2), num(row, 7)
        if gpp_net is None:
            continue
        net, fees = by_week[int(week)]
        plan.append(
            OtherSplit(
                week=int(week),
                gpp_net=(gpp_net, round(gpp_net - net, 2)),
                gpp_risked=(gpp_risked or 0.0, round((gpp_risked or 0.0) - fees, 2)),
                other_net=round(net, 2),
                other_risked=round(fees, 2),
            )
        )
    return plan


def apply_other_split(client: SheetsClient, cfg: SeasonConfig, plan: list[OtherSplit]) -> None:
    for item in plan:
        row = _find_week_row(client, cfg.tab, cfg, item.week)
        if row is None:
            continue
        client.update_ranges(
            cfg.tab,
            {
                f"{GPP_NET_COLUMN}{row}": [[item.gpp_net[1]]],
                f"{GPP_RISKED_COLUMN}{row}": [[item.gpp_risked[1]]],
                f"{OTHER_NET_COLUMN}{row}:{OTHER_RISKED_COLUMN}{row}": [[item.other_net, item.other_risked]],
            },
        )


def read_season_rows(client: SheetsClient, cfg: SeasonConfig) -> list[list]:
    return client.read_range_unformatted(
        cfg.tab, f"{WEEK_COLUMN}{cfg.first_row}:{LAST_VALUE_COLUMN}{cfg.last_row}"
    )
