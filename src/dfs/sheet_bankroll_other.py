"""Plan D item 2: the Bankroll tab's Other Sports ledger and its weekly line, so Cash and GPP are NFL only.

DK's contest-history export holds every sport; MLB, NHL, UFC and CFB entries used to land in the GPP
ledger (`bankroll.classify_entry` files anything non-Classic as GPP), so Week 4's GPP read +$201 when NFL
GPP was +$77. Sam (2026-11-03): "own category, but it's the same total bankroll." So:

- the ledger: an "Other Sports Entry" block below GPP (`BankrollConfig.other_table()`: by default one
  blank row under GPP's last row), the same columns and per-row `% Paid` / `Place %` formulas as Cash and
  GPP;
- the weekly line on row `OTHER_SUMMARY_ROW` (11, blank before this, right above Cash's summary row):
  Weekly Other % / Cost / Winnings / Net, the same shape as rows 12-14;
- Weekly Cost (B7) and Weekly Net (B9) widen from rows 12-14 to rows 11-14, so Ending Bankroll (= B1 + B9)
  still counts every dollar;
- GPP's own summary sums (D13 / F13) narrow from the hand-built `86:171` to GPP's configured rows, which
  is what lets the Other ledger sit right below GPP without being counted twice.

Nothing moves: every cell written was blank (checked first; anything else stops the build). Re-running on
a sheet that has the block rewrites the same cells."""

from __future__ import annotations

from dataclasses import dataclass

from dfs.config import EntryTableConfig
from dfs.sheet_bankroll_view import (
    CASH_SUMMARY_ROW,
    GPP_SUMMARY_ROW,
    SUMMARY_COST_COLUMN,
    SUMMARY_NET_COLUMN,
    SUMMARY_ROW,
)
from dfs.sheet_empty_guards import guard_formula
from dfs.sheet_style import CRIT_FG, OK_FG
from dfs.sheets import SheetsClient

OTHER_SUMMARY_ROW = CASH_SUMMARY_ROW - 1  # 11: the blank row above the Cash/GPP/Betting summary rows
SUMMARY_WINNINGS_COLUMN = "F"
WEEKLY_COST_CELL = "B7"
WEEKLY_NET_CELL = "B9"

OTHER_LEDGER_TITLE = "Other Sports Entry"
OTHER_HEADER = [
    OTHER_LEDGER_TITLE,
    "Place",
    "Points",
    "Winnings",
    "Entries",
    "Entry Fee",
    "Prize Pool",
    "Places Paid",
    "% Paid",
    "Place %",
]
OTHER_SUMMARY_LABELS = ("Weekly Other %", "Weekly Other Cost", "Weekly Other Winnings", "Weekly Other Net")

_HEADER_FMT = {
    "backgroundColor": {"red": 0.1254902, "green": 0.14901961, "blue": 0.18431373},
    "horizontalAlignment": "LEFT",
    "verticalAlignment": "MIDDLE",
    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "fontSize": 10, "bold": True},
}
_LABEL_FMT = {
    "horizontalAlignment": "LEFT",
    "textFormat": {
        "foregroundColor": {"red": 0.4745098, "green": 0.5176471, "blue": 0.5803922},
        "fontSize": 9,
        "bold": False,
    },
}
_CURRENCY = {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}, "horizontalAlignment": "RIGHT"}
_PERCENT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}, "horizontalAlignment": "RIGHT"}


def paid_formula(row: int) -> str:
    return f'=IF(E{row}="","",IFERROR(H{row}/E{row},""))'


def place_formula(row: int) -> str:
    return f'=IF(E{row}="","",IFERROR(B{row}/E{row},""))'


def ledger_sum(column: str, table: EntryTableConfig) -> str:
    return f"=SUM({column}{table.first_row}:{column}{table.last_row})"


def other_summary_row(other: EntryTableConfig) -> list[str]:
    """Row 11, A:H -- the same shape as Cash's row 12 (Cost = the ledger's Entry Fee, Winnings = its
    Winnings)."""
    r = OTHER_SUMMARY_ROW
    return [
        OTHER_SUMMARY_LABELS[0],
        guard_formula(f"=D{r}/{WEEKLY_COST_CELL}"),
        OTHER_SUMMARY_LABELS[1],
        ledger_sum("F", other),
        OTHER_SUMMARY_LABELS[2],
        ledger_sum("D", other),
        OTHER_SUMMARY_LABELS[3],
        f"={SUMMARY_WINNINGS_COLUMN}{r}-{SUMMARY_COST_COLUMN}{r}",
    ]


def weekly_cost_formula() -> str:
    return f"=SUM({SUMMARY_COST_COLUMN}{OTHER_SUMMARY_ROW}:{SUMMARY_COST_COLUMN}{SUMMARY_ROW})"


def weekly_net_formula() -> str:
    return f"=SUM({SUMMARY_NET_COLUMN}{OTHER_SUMMARY_ROW}:{SUMMARY_NET_COLUMN}{SUMMARY_ROW})"


def gpp_cost_formula(gpp: EntryTableConfig) -> str:
    return ledger_sum("F", gpp)


def gpp_winnings_formula(gpp: EntryTableConfig) -> str:
    return ledger_sum("D", gpp)


@dataclass(frozen=True)
class OtherBlockState:
    """What is on a sheet where the block goes. `ok` = the block is there as built."""

    header: list[str]
    summary_label: str
    weekly_cost: str
    weekly_net: str
    gpp_cost: str
    gpp_winnings: str

    def has_block(self) -> bool:
        return bool(self.header) and self.header[0].strip() == OTHER_LEDGER_TITLE


def read_state(client: SheetsClient, tab: str, other: EntryTableConfig) -> OtherBlockState:
    header = client.read_range(tab, f"A{other.header_row}:J{other.header_row}")
    label = client.read_range(tab, f"A{OTHER_SUMMARY_ROW}")
    cost, net = (client.read_formula(tab, c) for c in (WEEKLY_COST_CELL, WEEKLY_NET_CELL))
    gpp = client.read_formula(tab, f"{SUMMARY_COST_COLUMN}{GPP_SUMMARY_ROW}:F{GPP_SUMMARY_ROW}")

    def first(rows) -> str:
        return str(rows[0][0]).strip() if rows and rows[0] else ""

    gpp_row = gpp[0] if gpp else []
    return OtherBlockState(
        header=[str(v) for v in (header[0] if header else [])],
        summary_label=first(label),
        weekly_cost=first(cost),
        weekly_net=first(net),
        gpp_cost=str(gpp_row[0]).strip() if gpp_row else "",
        gpp_winnings=str(gpp_row[2]).strip() if len(gpp_row) > 2 else "",
    )


def has_other_ledger(client: SheetsClient, tab: str, other: EntryTableConfig) -> bool:
    header = client.read_range(tab, f"A{other.header_row}")
    return bool(header and header[0]) and str(header[0][0]).strip() == OTHER_LEDGER_TITLE


def problems(state: OtherBlockState, other: EntryTableConfig, gpp: EntryTableConfig) -> list[str]:
    """What `dfs doctor` reports: empty when the block is there and every rollup counts it exactly once."""
    out = []
    if not state.has_block():
        found = state.header[0] if state.header else "(blank)"
        out.append(f"no Other Sports ledger header at A{other.header_row} (reads {found!r})")
    if state.summary_label != OTHER_SUMMARY_LABELS[0]:
        out.append(f"A{OTHER_SUMMARY_ROW} is not the {OTHER_SUMMARY_LABELS[0]!r} line")
    if state.weekly_cost != weekly_cost_formula():
        out.append(f"{WEEKLY_COST_CELL} is {state.weekly_cost!r}, expected {weekly_cost_formula()!r}")
    if state.weekly_net != weekly_net_formula():
        out.append(f"{WEEKLY_NET_CELL} is {state.weekly_net!r}, expected {weekly_net_formula()!r}")
    if state.gpp_cost != gpp_cost_formula(gpp) or state.gpp_winnings != gpp_winnings_formula(gpp):
        out.append(
            f"GPP's summary sums ({SUMMARY_COST_COLUMN}{GPP_SUMMARY_ROW}/F{GPP_SUMMARY_ROW}) are not GPP's "
            f"own rows {gpp.first_row}-{gpp.last_row}, so they may count the Other ledger too"
        )
    return out


class OtherBlockError(Exception):
    pass


def _blank(rows: list[list]) -> bool:
    return not any(str(v).strip() for row in rows for v in row)


def build_other_sports_block(
    client: SheetsClient, tab: str, *, other: EntryTableConfig, gpp: EntryTableConfig
) -> list[str]:
    """Writes the block (see the module docstring). Refuses, writing nothing, if a target cell holds
    something that is not this block's own content. Returns one line per thing written."""
    if other.header_row <= gpp.last_row:
        raise OtherBlockError(f"the Other ledger (header row {other.header_row}) would overlap GPP's rows")
    state = read_state(client, tab, other)
    if not state.has_block():
        ledger = client.read_range(tab, f"A{other.header_row}:{other.entry_key_column}{other.last_row}")
        if not _blank(ledger):
            raise OtherBlockError(
                f"{tab}!A{other.header_row}:{other.entry_key_column}{other.last_row} is not blank -- "
                "nothing written"
            )
    if state.summary_label not in ("", OTHER_SUMMARY_LABELS[0]):
        raise OtherBlockError(f"{tab}!A{OTHER_SUMMARY_ROW} holds {state.summary_label!r} -- nothing written")
    if state.summary_label == "" and not _blank(
        client.read_range(tab, f"A{OTHER_SUMMARY_ROW}:H{OTHER_SUMMARY_ROW}")
    ):
        raise OtherBlockError(
            f"{tab}!A{OTHER_SUMMARY_ROW}:H{OTHER_SUMMARY_ROW} is not blank -- nothing written"
        )

    first, last = other.first_row, other.last_row
    client.update_ranges(
        tab,
        {
            f"A{other.header_row}:J{other.header_row}": [OTHER_HEADER],
            f"I{first}:J{last}": [[paid_formula(r), place_formula(r)] for r in range(first, last + 1)],
            f"A{OTHER_SUMMARY_ROW}:H{OTHER_SUMMARY_ROW}": [other_summary_row(other)],
            WEEKLY_COST_CELL: [[weekly_cost_formula()]],
            WEEKLY_NET_CELL: [[weekly_net_formula()]],
            f"{SUMMARY_COST_COLUMN}{GPP_SUMMARY_ROW}": [[gpp_cost_formula(gpp)]],
            f"F{GPP_SUMMARY_ROW}": [[gpp_winnings_formula(gpp)]],
        },
    )

    client.format_range(tab, f"A{other.header_row}:J{other.header_row}", _HEADER_FMT)
    client.format_range(tab, f"A{first}:A{last}", {"horizontalAlignment": "LEFT"})
    client.format_range(tab, f"B{first}:J{last}", {"horizontalAlignment": "RIGHT"})
    for col in ("D", "F", "G"):
        client.format_range(tab, f"{col}{first}:{col}{last}", _CURRENCY)
    client.format_range(tab, f"I{first}:J{last}", _PERCENT)
    r = OTHER_SUMMARY_ROW
    for col in ("A", "C", "E", "G"):
        client.format_range(tab, f"{col}{r}", _LABEL_FMT)
    client.format_range(tab, f"B{r}", _PERCENT)
    for col in ("D", "F", "H"):
        client.format_range(tab, f"{col}{r}", _CURRENCY)
    for condition, color in (("NUMBER_GREATER", OK_FG), ("NUMBER_LESS", CRIT_FG)):
        client.add_boolean_rule(
            tab,
            f"H{r}",
            condition_type=condition,
            values=["0"],
            fmt={"textFormat": {"foregroundColor": color, "bold": True}},
        )
    client.hide_columns(tab, other.entry_key_column, other.entry_key_column)

    return [
        f"{tab}: Other Sports ledger at rows {other.header_row}-{last} (header + {last - first + 1} rows)",
        f"{tab}: row {r} is the weekly Other line; {WEEKLY_COST_CELL} / {WEEKLY_NET_CELL} now count rows "
        f"{r}-{SUMMARY_ROW}",
        f"{tab}: GPP's summary sums now read GPP's own rows {gpp.first_row}-{gpp.last_row}",
    ]
