"""`Bankroll` > STAKES: your own numbers by format, this week's plan from your own rules, and how the default
split was worked out. A block to the RIGHT of the Bankroll tab's ledgers (columns `STAKES_FIRST_COLUMN` on,
from row 1), so no row is inserted and nothing the tab already holds moves: the Betting/Cash/GPP ledgers and
the KPI block sit in A:J (L is the hidden dedupe key). The block is rebuilt on EVERY run (`dfs bankroll
stakes`, and at the end of `dfs week new`) from `data/contests/contest_log.csv`, the tab's own Starting
Bankroll and Weekly Budget cells and `[stakes]` in config, so nothing in it is typed and no hand-edited cell
survives; nothing outside it is ever written.

The math is `stakes.py`; this file only lays it out (`build_layout`, pure, no sheet) and writes it
(`write_stakes`). Every row is placed by the order things are added, never by a number: the table's rows come
from the formats the log holds, and each style target is recorded as the row the builder returned. The one
position that is a constant is the block's first column, `STAKES_FIRST_COLUMN`.

Writing refuses (`StakesError`) when the block's area holds anything that is not a previous STAKES block, so a
cell Sam typed there is never overwritten.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from dfs import stakes
from dfs.config import BankrollConfig, StakesConfig
from dfs.sheet_column_notes import STAKES_NOTES
from dfs.sheets import SheetsClient, column_index, column_letter
from dfs.stakes import NOT_ADVICE, Plan, Stat, StatRow, money
from dfs.week import BANKROLL_STARTING_CELL, BANKROLL_WEEKLY_BUDGET_CELL, BANKROLL_WEEKLY_COST_CELL

STAKES_FIRST_COLUMN = "N"  # M is the spacer after the hidden L; the ledgers end at J
COLUMN_COUNT = 9
STAKES_LAST_COLUMN = column_letter(column_index(STAKES_FIRST_COLUMN) + COLUMN_COUNT - 1)
STAKES_TITLE = "STAKES  -  your own history and your own rules"
STAKES_MAX_ROWS = 80  # how far down a previous block is looked for (and cleared)
FIRST_COLUMN_PX = 270
OTHER_COLUMN_PX = 130  # "Weight on own ROI" and "Cash 60% / GPP 40%" both fit
NOTE_WRAP_CHARS = 150
MISSING = "–"

TABLE_HEADER = [
    "Format",
    "Entries",
    "ROI",
    "Cash rate",
    "Weekly SD",
    "Entries",
    "ROI",
    "Cash rate",
    "Weekly SD",
]
SPLIT_HEADER = ["Bucket", "Entries", "ROI", "Weight on own ROI", "ROI share", "Even share", "Share", "Note"]


class StakesError(Exception):
    pass


@dataclass
class Layout:
    """`rows` (each `COLUMN_COUNT` wide) and the rows/cells that get a style, all 1-based and relative to the
    block (the first row of `rows` is row 1 of the tab)."""

    rows: list[list]
    title_rows: list[int] = field(default_factory=list)
    section_rows: list[int] = field(default_factory=list)
    group_rows: list[int] = field(default_factory=list)  # the "THIS SEASON" / "ALL SEASONS" band
    header_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    verdict_rows: list[int] = field(default_factory=list)
    warning_rows: list[int] = field(default_factory=list)
    value_rows: list[int] = field(default_factory=list)  # the plan's label / value rows: value right-aligned
    total_rows: list[int] = field(default_factory=list)  # Cash / GPP / All formats rows, bold
    percent_cells: list[tuple[int, int]] = field(default_factory=list)  # (row, column index in the block)
    roi_cells: list[tuple[int, int]] = field(default_factory=list)  # percent, red when negative
    money_cells: list[tuple[int, int]] = field(default_factory=list)
    count_cells: list[tuple[int, int]] = field(default_factory=list)


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])

    def add(self, values: list) -> int:
        self.layout.rows.append(list(values) + [""] * (COLUMN_COUNT - len(values)))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def section(self, title: str) -> None:
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def lines(self, text: str, into: list[int]) -> None:
        for line in textwrap.wrap(text, NOTE_WRAP_CHARS) or [""]:
            into.append(self.add([line]))


def _fmt_stat(stat: Stat) -> list:
    """Entries, ROI, cash rate, weekly SD; "–" where the data cannot say."""
    if stat.entries == 0:
        return [0, MISSING, MISSING, MISSING]
    return [
        stat.entries,
        MISSING if stat.roi is None else stat.roi,
        MISSING if stat.cash_rate is None else stat.cash_rate,
        MISSING if stat.weekly_sd is None else stat.weekly_sd,
    ]


def _cells(layout: Layout, row: int, first_col: int, kinds: tuple[list, ...]) -> None:
    for offset, target in enumerate(kinds):
        target.append((row, first_col + offset))


def build_layout(
    rows: list[StatRow],
    plan: Plan,
    cfg: StakesConfig,
    *,
    season: int,
    first_season: int,
    last_season: int,
    log_entries: int,
) -> Layout:
    b = _Builder()
    lay = b.layout
    lay.title_rows.append(b.add([STAKES_TITLE]))
    lay.note_rows.append(
        b.add(
            [
                f"From your contest log ({log_entries:,} entries, {first_season}-{last_season}). Rebuilt by "
                "`dfs bankroll stakes`; do not type here."
            ]
        )
    )

    b.section("YOUR NUMBERS BY FORMAT")
    lay.group_rows.append(
        b.add(["", f"THIS SEASON ({season})", "", "", "", f"ALL SEASONS ({first_season}-{last_season})"])
    )
    lay.header_rows.append(b.add(TABLE_HEADER))
    for r in rows:
        row = b.add([r.label, *_fmt_stat(r.season), *_fmt_stat(r.overall)])
        if r.kind != "format":
            lay.total_rows.append(row)
        for first in (1, 5):
            lay.count_cells.append((row, first))
            lay.roi_cells.append((row, first + 1))
            lay.percent_cells.append((row, first + 2))
            lay.money_cells.append((row, first + 3))
    b.blank()
    b.lines(
        "ROI = (winnings - fees) / fees. Cash rate = entries that finished in a paid place. Weekly SD = the "
        "standard deviation of the weekly net (needs 2+ weeks; playoff and off-season contests count in ROI "
        'and cash rate but not in a week). "–" = the data cannot say (no fees paid, under 2 weeks). '
        "Cash = H2H, 50/50 and Double Up; GPP = every other format.",
        lay.note_rows,
    )

    b.section("THIS WEEK'S PLAN  -  your rules, as arithmetic")

    def val(values: list) -> int:
        row = b.add(values)
        lay.value_rows.append(row)
        return row

    val(
        [
            f"Weekly budget ({BANKROLL_WEEKLY_BUDGET_CELL})",
            "" if plan.budget is None else plan.budget,
            "typed on this tab; carried week to week" if plan.budget is not None else "blank: type it first",
        ]
    )
    lay.money_cells.append((len(lay.rows), 1))
    val(
        [
            f"Starting bankroll ({BANKROLL_STARTING_CELL})",
            "" if plan.bankroll is None else plan.bankroll,
            "last week's Ending Bankroll",
        ]
    )
    lay.money_cells.append((len(lay.rows), 1))
    for label, key, pct, cap in (
        ("Max % of bankroll per week", "week_cap", cfg.max_pct_bankroll_per_week, plan.week_cap),
        ("Max entry fee, % of bankroll", "entry_cap", cfg.max_entry_fee_pct, plan.entry_cap),
    ):
        if pct is None:
            val(
                [
                    label,
                    "not set",
                    "off: set it in config.toml [stakes], or see `dfs bankroll stakes --propose`",
                ]
            )
        else:
            how = (
                f"{plan.arithmetic[key]} = {money(cap)}" if cap is not None else "needs the Starting Bankroll"
            )
            val([label, "" if cap is None else cap, how])
            lay.money_cells.append((len(lay.rows), 1))
    val(["Planned spend", "" if plan.spend is None else plan.spend, _spend_arithmetic(plan)])
    lay.money_cells.append((len(lay.rows), 1))
    cash_share, gpp_share = plan.shares[stakes.CASH], plan.shares[stakes.GPP]
    val(
        [
            "Split",
            f"Cash {cash_share:.0%} / GPP {gpp_share:.0%}",
            "your [stakes] split" if plan.split_source == "your split" else "default: ROI-based, see below",
        ]
    )
    for bucket in stakes.PLAN_BUCKETS:
        amount = plan.amounts[bucket]
        row = val(
            [
                f"{stakes.BUCKET_LABEL[bucket]} amount",
                "" if amount is None else amount,
                f"{money(plan.spend)} x {plan.shares[bucket]:.0%}" if plan.spend is not None else "",
            ]
        )
        lay.money_cells.append((row, 1))
    row = val(["Largest single entry", "no limit set" if plan.entry_cap is None else plan.entry_cap])
    if plan.entry_cap is not None:
        lay.money_cells.append((row, 1))
    b.blank()
    b.lines(stakes.plan_line(plan), lay.verdict_rows)
    for warning in plan.warnings:
        b.lines(f"WARNING: {warning}", lay.warning_rows)
    unset = stakes.unset_rules(cfg)
    if unset:
        b.lines(
            f"Not set: {', '.join(unset)}. The plan above is the arithmetic with "
            f"{'it' if len(unset) == 1 else 'them'} off. `dfs bankroll stakes --propose` prints values "
            "worked out from your own bankroll and weekly volatility (nothing is written).",
            lay.note_rows,
        )

    b.section("HOW THE DEFAULT SPLIT IS WORKED OUT")
    lay.header_rows.append(b.add(SPLIT_HEADER))
    for d in plan.details:
        row = b.add(
            [
                stakes.BUCKET_LABEL[d.bucket],
                d.entries,
                MISSING if d.roi is None else d.roi,
                d.weight if d.eligible else MISSING,
                d.roi_share if d.eligible else MISSING,
                d.even_share if d.eligible else MISSING,
                d.share,
                "" if d.eligible else f"$0: {d.reason}",
            ]
        )
        lay.count_cells.append((row, 1))
        lay.roi_cells.append((row, 2))
        lay.percent_cells.extend((row, c) for c in (3, 4, 5, 6))
    b.blank()
    b.lines(
        f"share = w x (ROI share) + (1 - w) x (even share). w = min(1, entries / {stakes.SHRINK_ENTRIES}): a "
        f"bucket under {stakes.SHRINK_ENTRIES} entries is pulled toward an even split by how short of "
        f"{stakes.SHRINK_ENTRIES} it is. ROI share = its ROI / the eligible buckets' ROI total. Floor: a "
        f"bucket whose money format ({stakes.MONEY_FORMATS[stakes.CASH]} for Cash, "
        f"{stakes.MONEY_FORMATS[stakes.GPP]} for GPP) has an ROI over every season "
        f"in the log below {stakes.ROI_FLOOR:.0%} (or no history) gets $0. "
        "Only those two formats get money: H2H, 50/50, multi-entry GPP and Showdown get $0. Cash and GPP are "
        "split because those are the two bankrolls this tab keeps; your [stakes] split overrides all of it.",
        lay.note_rows,
    )
    b.lines(NOT_ADVICE, lay.note_rows)
    return lay


def _spend_arithmetic(plan: Plan) -> str:
    if plan.spend is None:
        return ""
    if plan.week_cap is not None and plan.budget is not None and plan.week_cap < plan.budget:
        return f"min({money(plan.budget)}, {money(plan.week_cap)}): the weekly rule is lower"
    if plan.week_cap is not None:
        return f"min({money(plan.budget)}, {money(plan.week_cap)}): the budget is lower"
    return "the weekly budget (no weekly % rule set)"


# ---- reading the sheet's own inputs ----------------------------------------------------------------


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _cell(client: SheetsClient, tab: str, a1: str) -> float | None:
    rows = client.read_range_unformatted(tab, f"{a1}:{a1}")
    return _number(rows[0][0]) if rows and rows[0] else None


def _largest_fee(client: SheetsClient, tab: str, table, label: str) -> float | None:
    """The biggest value in the ledger column whose header reads `label` (found by name, never by letter)."""
    header = client.read_range(tab, f"A{table.header_row}:{table.header_row}")
    names = [str(c).strip() for c in (header[0] if header else [])]
    if label not in names:
        return None
    letter = column_letter(names.index(label))
    values = client.read_range_unformatted(tab, f"{letter}{table.first_row}:{letter}{table.last_row}")
    fees = [n for row in values if row and (n := _number(row[0])) is not None]
    return max(fees) if fees else None


@dataclass
class SheetInputs:
    budget: float | None
    bankroll: float | None
    spent: float | None
    largest_entry: float | None


ENTRY_FEE_HEADER = "Entry Fee"  # Cash/GPP ledgers (`bankroll._entry_row`'s column F)
BET_ENTERED_HEADER = "Entered"  # the Betting ledger (`sheet_bankroll_view.BETTING_LEDGER_HEADER`)


def read_inputs(client: SheetsClient, cfg: BankrollConfig) -> SheetInputs:
    tab = cfg.tab
    fees = []
    for table, label in (
        (cfg.cash, ENTRY_FEE_HEADER),
        (cfg.gpp, ENTRY_FEE_HEADER),
        (cfg.bets, BET_ENTERED_HEADER),
    ):
        if table is not None and (fee := _largest_fee(client, tab, table, label)) is not None:
            fees.append(fee)
    return SheetInputs(
        budget=_cell(client, tab, BANKROLL_WEEKLY_BUDGET_CELL),
        bankroll=_cell(client, tab, BANKROLL_STARTING_CELL),
        spent=_cell(client, tab, BANKROLL_WEEKLY_COST_CELL),
        largest_entry=max(fees) if fees else None,
    )


# ---- writing ---------------------------------------------------------------------------------------


def _a1(first_row: int, last_row: int, first_col: int, last_col: int) -> str:
    base = column_index(STAKES_FIRST_COLUMN)
    return f"{column_letter(base + first_col)}{first_row}:{column_letter(base + last_col)}{last_row}"


def _block(row: int) -> str:
    return _a1(row, row, 0, COLUMN_COUNT - 1)


def _cell_a1(row: int, col: int) -> str:
    return _a1(row, row, col, col)


def write_stakes(client: SheetsClient, layout: Layout, tab: str) -> str:
    """Write `layout` to the block's area of `tab`, after clearing the previous block. Raises `StakesError`
    (writing nothing) when that area holds anything but a previous STAKES block."""
    from dfs.sheet_style import (
        _HEADER_FMT,
        _PANEL_FMT,
        _SUBHEAD_FMT,
        _TITLE_FMT,
        CRIT_FG,
        INK_MUTED,
        WHITE,
        _num,
    )

    area = _a1(1, STAKES_MAX_ROWS, 0, COLUMN_COUNT - 1)
    existing = client.read_range(tab, area)
    cells = [str(c).strip() for row in existing for c in row if str(c).strip()]
    first = str(existing[0][0]).strip() if existing and existing[0] else ""
    if cells and not first.startswith("STAKES"):
        raise StakesError(
            f"{tab}!{area} already holds something that is not a STAKES block (starting {cells[0]!r}); "
            "nothing was written. Clear it or move it first."
        )
    if len(layout.rows) > STAKES_MAX_ROWS:
        raise StakesError(f"The STAKES block is {len(layout.rows)} rows; the limit is {STAKES_MAX_ROWS}.")

    client.ensure_column_capacity(tab, column_index(STAKES_LAST_COLUMN) + 1)
    with client.batched():
        client.clear_ranges(tab, [area])
        client.clear_notes(tab, area)
        client.update_range(tab, _a1(1, len(layout.rows), 0, COLUMN_COUNT - 1), layout.rows)
        client.format_range(
            tab,
            area,
            {
                "backgroundColor": WHITE,
                "textFormat": {"bold": False, "italic": False, "fontSize": 10},
                "horizontalAlignment": "LEFT",
                "numberFormat": None,
                "wrapStrategy": "OVERFLOW_CELL",
            },
        )
        widths = {STAKES_FIRST_COLUMN: FIRST_COLUMN_PX}
        base = column_index(STAKES_FIRST_COLUMN)
        widths.update({column_letter(base + i): OTHER_COLUMN_PX for i in range(1, COLUMN_COUNT)})
        client.set_column_widths(tab, widths)
        for row in layout.title_rows:
            client.format_range(tab, _cell_a1(row, 0), _TITLE_FMT)
        for row in layout.section_rows:
            client.format_range(tab, _block(row), _PANEL_FMT)
        for row in layout.group_rows:
            client.format_range(tab, _block(row), {**_SUBHEAD_FMT, "horizontalAlignment": "LEFT"})
        for row in layout.header_rows:
            client.format_range(tab, _block(row), {**_HEADER_FMT, "horizontalAlignment": "RIGHT"})
            client.format_range(tab, _cell_a1(row, 0), {"horizontalAlignment": "LEFT"})
        for row in layout.note_rows:
            client.format_range(
                tab,
                _cell_a1(row, 0),
                {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}},
            )
        for row in layout.verdict_rows:
            client.format_range(tab, _cell_a1(row, 0), {"textFormat": {"bold": True, "fontSize": 10}})
        for row in layout.warning_rows:
            client.format_range(
                tab,
                _cell_a1(row, 0),
                {"textFormat": {"bold": True, "fontSize": 10, "foregroundColor": CRIT_FG}},
            )
        for row in layout.total_rows:
            client.format_range(tab, _block(row), {"textFormat": {"bold": True}})
        right = {"horizontalAlignment": "RIGHT"}
        for cells_, fmt in (
            (layout.count_cells, _num("#,##0")),
            (layout.percent_cells, _num("0%", "PERCENT")),
            (layout.roi_cells, _num("0.0%;[Red]-0.0%", "PERCENT")),
            (layout.money_cells, _num('"$"#,##0.00;[Red]-"$"#,##0.00')),
        ):
            for row, col in cells_:
                client.format_range(tab, _cell_a1(row, col), {**fmt, **right})
        for row in layout.value_rows:
            client.format_range(tab, _cell_a1(row, 1), right)
        for header in layout.header_rows:
            client.set_row_notes(
                tab,
                header,
                [STAKES_NOTES.get(str(name).strip(), "") for name in layout.rows[header - 1]],
                first_col=base,
            )
    return (
        f"{tab}: STAKES block written ({len(layout.rows)} rows, {STAKES_FIRST_COLUMN}:{STAKES_LAST_COLUMN})"
    )


def has_block(client: SheetsClient, tab: str) -> bool:
    """True when the block's title is where `write_stakes` puts it (the doctor's check)."""
    rows = client.read_range(tab, f"{STAKES_FIRST_COLUMN}1:{STAKES_FIRST_COLUMN}1")
    return bool(rows and rows[0] and str(rows[0][0]).strip().startswith("STAKES"))


# ---- the whole step --------------------------------------------------------------------------------


def load_typed():  # noqa: ANN201
    """The contest log, typed (empty when there is no log yet)."""
    from dfs.contests import history
    from dfs.paths import CONTESTS_DIR

    return history.typed(history.read_log(CONTESTS_DIR / "contest_log.csv"))


def compute(cfg: StakesConfig, typed, inputs: SheetInputs, season: int) -> tuple[list[StatRow], Plan, Layout]:
    """The table, the plan and the block's layout from the log, the sheet's own inputs and the rules."""
    rows = stakes.build_stats(typed, season)
    plan = stakes.make_plan(
        cfg,
        budget=inputs.budget,
        bankroll=inputs.bankroll,
        stats=stakes.bucket_stats(rows),
        spent_so_far=inputs.spent,
        largest_entry_so_far=inputs.largest_entry,
    )
    seasons = typed["season"].dropna()
    layout = build_layout(
        rows,
        plan,
        cfg,
        season=season,
        first_season=int(seasons.min()) if len(seasons) else season,
        last_season=int(seasons.max()) if len(seasons) else season,
        log_entries=len(typed),
    )
    return rows, plan, layout


def refresh_block(client: SheetsClient, cfg, season: int) -> tuple[str, Plan]:
    """Rebuild the STAKES block on `client`'s sheet from the log (`cfg` is the whole `Config`). Returns the
    write summary and the plan."""
    inputs = read_inputs(client, cfg.bankroll)
    _, plan, layout = compute(cfg.stakes, load_typed(), inputs, season)
    return write_stakes(client, layout, cfg.bankroll.tab), plan
