"""Round 5, item 7d: the Season tab -- one pre-built row per NFL week
(never inserted/deleted, same convention as the Results tab), a
year-to-date rollup per bucket, and a cumulative-net line chart.

Brand-new tab (nothing here existed before this change), so unlike
`sheet_bankroll_view.py`'s surgical insert-into-an-existing-tab approach,
this module rewrites the whole thing with `SheetsClient.write_tab` --
safe here specifically because there is no hand-built content to
preserve yet. Once built, ONLY `season.py`'s `write_season_cash_gpp`/
`write_season_betting_and_ending` should ever touch it again (narrow
range writes into the already-existing grid) -- re-running
`build_season_tab` wipes and rebuilds the whole tab, including whatever
weekly data had accumulated, so it is a one-time setup step, not a
per-week command.

Column layout (see `season.py`'s own column-letter constants, which this
module's header row must stay in sync with):

    A Week | B Cash Net | C GPP Net | D Betting Net | E Total Net (=B+C+D)
    F Ending Bankroll | G Cash Risked | H GPP Risked | I Betting Risked
    J Betting Wins | K Betting Losses | L Betting Pushes | M Betting Exp. Wins
    N-Q running cumulative Cash/GPP/Betting/Total Net, for the chart only.
    R Other Net | S Other Risked -- DK contests in any sport but NFL (Plan D item 2, 2026-11-03: Sam, "own
    category, but it's the same total bankroll"), so B/C are NFL only and E/Q still count every dollar.
    T-X the chart's data, hidden (`CHART_COLS`).

B/C/G/H are written by `season.write_season_cash_gpp` (DK CSV backfill,
season-long, at `week close`/`bankroll sync` time). D/F/I/J/K/L/M are
written by `season.write_season_betting_and_ending` plus the week-close
wiring that reads `compute_weekly_betting_stats` off the CLOSING week's
own Bankroll tab (`cli.py`) -- there is no DK Sportsbook export to bucket
by week. E and N-Q are formulas, never written by Python.
"""

from __future__ import annotations

from dfs.nfl_calendar import MAX_WEEK
from dfs.sheet_empty_guards import guard_formula
from dfs.sheets import SheetsClient

HEADER_ROW = 1
FIRST_ROW = 2
LAST_ROW = FIRST_ROW + MAX_WEEK - 1  # 19, one row per week 1..MAX_WEEK

YTD_LABEL_ROW = LAST_ROW + 2  # 21
YTD_HEADER_ROW = YTD_LABEL_ROW + 1  # 22
YTD_CASH_ROW = YTD_HEADER_ROW + 1  # 23
YTD_GPP_ROW = YTD_CASH_ROW + 1  # 24
YTD_BETTING_ROW = YTD_GPP_ROW + 1  # 25
YTD_TOTAL_ROW = YTD_BETTING_ROW + 1  # 26
YTD_OTHER_ROW = YTD_TOTAL_ROW + 1  # 27: other sports, under Total (Total counts it; nothing above moved)

CHART_ANCHOR_CELL = f"A{YTD_OTHER_ROW + 2}"

OTHER_NET_HEADER = "Other Net"
OTHER_RISKED_HEADER = "Other Risked"
OTHER_YTD_LABEL = "Other sports"

# Chart data (T:X since Plan D item 2 inserted the Other Risked column at S; S:W before): a Week 0
# baseline row of zeros, then one row per week that has data. Feeds the chart only -- N:Q alone would
# start every line at Week 1's value, and would flat-line through the weeks not yet played.
CHART_COLS = ("T", "U", "V", "W", "X")
CHART_HEADER = ["Week", "Cash", "GPP", "Betting", "Total"]
CHART_LAST_ROW = LAST_ROW + 1  # Week 0 takes row 2, so week w sits on row w + 2

HEADER = [
    "Week",
    "Cash Net",
    "GPP Net",
    "Betting Net",
    "Total Net",
    "Ending Bankroll",
    "Cash Risked",
    "GPP Risked",
    "Betting Risked",
    "Betting Wins",
    "Betting Losses",
    "Betting Pushes",
    "Betting Exp. Wins",
    "Cum. Cash",
    "Cum. GPP",
    "Cum. Betting",
    "Cum. Total",
    OTHER_NET_HEADER,
    OTHER_RISKED_HEADER,
]

_HEADER_FMT = {
    "backgroundColor": {"red": 0.1254902, "green": 0.14901961, "blue": 0.18431373},
    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "fontSize": 10, "bold": True},
}
_CURRENCY = {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
_PERCENT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}


def _week_row(week: int) -> list:
    return slate_row(week, FIRST_ROW + week - 1)


def slate_row(label: int | str, r: int, first_row: int = FIRST_ROW) -> list:
    """One Season row A:S for the label in A (a week number, or "12 Thu" for a secondary slate) on sheet row
    `r`: the formula columns E and N:Q, every money column blank."""
    return [
        label,
        "",
        "",
        "",
        total_net_formula(r),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        f"=SUM($B${first_row}:B{r})",
        f"=SUM($C${first_row}:C{r})",
        f"=SUM($D${first_row}:D{r})",
        cum_total_formula(r, first_row),
        "",
        "",
    ]


def total_net_formula(r: int) -> str:
    """Column E: every bucket's net, other sports included (Ending Bankroll counts them too)."""
    return f"=B{r}+C{r}+D{r}+R{r}"


def cum_total_formula(r: int, first_row: int = FIRST_ROW) -> str:
    """Column Q: the running Total Net (E), so the chart's Total line includes other sports."""
    return f"=SUM($E${first_row}:E{r})"


def total_risked_formula(first_row: int = FIRST_ROW, last_row: int = LAST_ROW) -> str:
    g, h, i, s_ = (f"{c}{first_row}:{c}{last_row}" for c in ("G", "H", "I", "S"))
    return f"=SUM({g})+SUM({h})+SUM({i})+SUM({s_})"


def other_ytd_row(first_row: int = FIRST_ROW, last_row: int = LAST_ROW, r: int = YTD_OTHER_ROW) -> list:
    return [
        OTHER_YTD_LABEL,
        f"=SUM(R{first_row}:R{last_row})",
        f"=SUM(S{first_row}:S{last_row})",
        guard_formula(f"=B{r}/C{r}"),
        "",
        "",
    ]


YTD_LABEL = "Year to date"
YTD_ROW_LABELS = ("Cash", "GPP", "Betting", "Total", OTHER_YTD_LABEL)  # in order, under the header row


def ytd_block(first_row: int = FIRST_ROW, last_row: int = LAST_ROW, label_row: int = YTD_LABEL_ROW) -> list:
    """The "Year to date" block A:F, from its label row down to the Other sports row, summing Season rows
    `first_row`..`last_row` (every slate row, a secondary slate's included)."""
    cash_r, gpp_r, bet_r, total_r, other_r = (label_row + 2 + i for i in range(5))
    b, g, i = f"B{first_row}:B{last_row}", f"G{first_row}:G{last_row}", f"I{first_row}:I{last_row}"
    c, h = f"C{first_row}:C{last_row}", f"H{first_row}:H{last_row}"
    d = f"D{first_row}:D{last_row}"
    e = f"E{first_row}:E{last_row}"
    wins, losses, pushes, exp_wins = (f"{col}{first_row}:{col}{last_row}" for col in ("J", "K", "L", "M"))
    return [
        [YTD_LABEL],
        ["", "Net", "Risked", "ROI", "Record (W-L-P)", "Exp. Wins"],
        ["Cash", f"=SUM({b})", f"=SUM({g})", guard_formula(f"=B{cash_r}/C{cash_r}"), "", ""],
        ["GPP", f"=SUM({c})", f"=SUM({h})", guard_formula(f"=B{gpp_r}/C{gpp_r}"), "", ""],
        [
            "Betting",
            f"=SUM({d})",
            f"=SUM({i})",
            guard_formula(f"=B{bet_r}/C{bet_r}"),
            f'=SUM({wins})&"-"&SUM({losses})&"-"&SUM({pushes})',
            f'=ROUND(SUM({exp_wins}),1)&" vs "&SUM({wins})',
        ],
        [
            "Total",
            f"=SUM({e})",
            total_risked_formula(first_row, last_row),
            guard_formula(f"=B{total_r}/C{total_r}"),
            "",
            "",
        ],
        other_ytd_row(first_row, last_row, other_r),
    ]


def build_season_grid() -> list[list]:
    rows: list[list] = [HEADER]
    rows.extend(_week_row(w) for w in range(1, MAX_WEEK + 1))
    rows.append([])  # row 20, blank spacer
    rows.extend(ytd_block())  # rows 21-27
    return rows


def chart_data_grid(rows: list[int] | None = None) -> list[list]:
    """T1:X(n+2): header, Week 0 = $0 baseline, then one row per Season row (`rows`, the sheet rows of the
    slate labels, in order; the 18 week rows when left out) mirroring its cumulative N:Q. A row with no
    Cash/GPP/Betting net yet gives #N/A, which a line chart leaves as a gap (the #N/A is muted in the sheet
    itself, see `add_season_chart`)."""
    grid: list[list] = [CHART_HEADER, [0, 0, 0, 0, 0]]
    for r in rows if rows is not None else range(FIRST_ROW, LAST_ROW + 1):
        has_data = f"COUNT($B{r}:$D{r})=0"
        grid.append([f"=A{r}"] + [f"=IF({has_data},NA(),{col}{r})" for col in ("N", "O", "P", "Q")])
    return grid


# -- a secondary slate's row (slate_chain.py) ----------------------------------------------------------------
#
# A secondary slate ("12 Thu") gets a Season row of its own, between Week 11's and Week 12's, by a real row
# insert (`SheetsClient.insert_rows`), so Sheets moves every formula range on the tab with it. Nothing in
# Python holds a Season row number after that: `label_rows` finds every slate row by its label in column A,
# the "Year to date" block by its label, and the chart block is rebuilt from the rows found.

LABEL_SCAN_ROWS = 60  # column A is read this far down for the slate labels (18 weeks and a few slates)


def label_rows(client: SheetsClient, tab: str, first_row: int = FIRST_ROW) -> list[tuple[int, str]]:
    """`(sheet row, label)` for every Season row, from `first_row` down to the first cell that is not a slate
    label ("12", "12 Thu"; `slate_chain.parse_label`): the blank spacer above "Year to date"."""
    from dfs import slate_chain

    values = client.read_range(tab, f"A{first_row}:A{first_row + LABEL_SCAN_ROWS - 1}")
    out: list[tuple[int, str]] = []
    for i, row in enumerate(values):
        cell = str(row[0]).strip() if row and row[0] not in (None, "") else ""
        if slate_chain.parse_label(cell) is None:
            break
        out.append((first_row + i, cell))
    return out


def insert_at(labels: list[tuple[int, str]], label: str) -> int:
    """The sheet row a new slate label goes in, by date: before the first row that comes after it, else
    right under the last one."""
    from dfs import slate_chain

    new = slate_chain.label_position(label)
    for r, text in labels:
        pos = slate_chain.label_position(text)
        if pos is not None and new is not None and pos > new:
            return r
    return labels[-1][0] + 1 if labels else FIRST_ROW


def insert_slate_row(
    client: SheetsClient, tab: str, label: str, first_row: int = FIRST_ROW
) -> tuple[int, list[str]]:
    """Insert the Season row for a secondary slate (`label`, "12 Thu") in date order, with its own formulas
    (E, N:Q) and money formats; rewrite the "Year to date" block over every slate row (a row added under the
    last one is outside the old sums) and the chart block from the rows found. Returns the new row and what
    was done. Raises SeasonSlateRowError, before any write, when the tab is not the layout this expects."""
    labels = label_rows(client, tab, first_row)
    if not labels:
        raise SeasonSlateRowError(f"{tab}: no week rows found from A{first_row} -- nothing written")
    if any(text.casefold() == label.casefold() for _, text in labels):
        raise SeasonSlateRowError(f"{tab}: {label!r} already has a row -- nothing written")
    ytd_before = _ytd_label_row(client, tab, labels[-1][0])
    if ytd_before is None:
        raise SeasonSlateRowError(
            f"{tab}: no {YTD_LABEL!r} block (Cash, GPP, Betting, Total, Other sports) under the week rows -- "
            "nothing written"
        )
    header = client.read_range(tab, f"A{HEADER_ROW}:Z{HEADER_ROW}")
    chart_here = other_columns_state(header[0] if header else []) == "done"

    at = insert_at(labels, label)
    client.insert_rows(tab, at_row=at, count=1)
    rows = sorted([r + 1 if r >= at else r for r, _ in labels] + [at])
    last = rows[-1]
    ytd = ytd_before + 1
    updates = {
        f"A{at}:S{at}": [slate_row(label, at, first_row)],
        f"A{ytd}:F{ytd + len(YTD_ROW_LABELS) + 1}": ytd_block(first_row, last, ytd),
    }
    if chart_here:
        first_col, last_col = CHART_COLS[0], CHART_COLS[-1]
        updates[f"{first_col}{HEADER_ROW}:{last_col}{HEADER_ROW + 1 + len(rows)}"] = chart_data_grid(rows)
    client.update_ranges(tab, updates)
    for cols in (f"B{at}:I{at}", f"N{at}:S{at}"):  # money; J:M are counts
        client.format_range(tab, cols, _CURRENCY)
    done = [
        f"{tab}: inserted row {at} for {label!r} (date order); {YTD_LABEL!r} sums rows {first_row}-{last}",
    ]
    if chart_here:
        done.append(f"{tab}: chart block rebuilt over {len(rows)} rows")
    return at, done


def _ytd_label_row(client: SheetsClient, tab: str, after_row: int) -> int | None:
    """The "Year to date" label's row, a few rows under the last slate row (None when it is not there)."""
    values = client.read_range(tab, f"A{after_row + 1}:A{after_row + 12}")
    cells = [str(row[0]).strip() if row else "" for row in values]
    expected = [YTD_LABEL, "", *YTD_ROW_LABELS]
    for i, cell in enumerate(cells):
        if cell == YTD_LABEL:
            found = (cells[i : i + len(expected)] + [""] * len(expected))[: len(expected)]
            return after_row + 1 + i if found == expected else None
    return None


class SeasonSlateRowError(Exception):
    pass


def add_season_chart(client: SheetsClient, tab: str) -> str:
    """(Re)builds the chart-data block and the cumulative-net chart from it.
    Safe to re-run: existing charts on the tab are deleted first. On a Season tab from before Plan D item 2
    (chart block at S:W, no Other columns) it writes nothing and says to run `dfs setup other-sports`."""
    header = client.read_range(tab, f"A{HEADER_ROW}:Z{HEADER_ROW}")
    if other_columns_state(header[0] if header else []) == "ready":
        return f"{tab}: chart not rebuilt -- the tab predates the Other columns; run `dfs setup other-sports`"
    first, last = CHART_COLS[0], CHART_COLS[-1]
    # One chart row per Season row: a secondary slate's row ("12 Thu") is a point of its own, in date order.
    rows = [r for r, _ in label_rows(client, tab)] or None
    chart_last = CHART_LAST_ROW if rows is None else HEADER_ROW + 1 + len(rows)
    client.update_range(tab, f"{first}{HEADER_ROW}:{last}{chart_last}", chart_data_grid(rows))
    client.format_range(tab, f"{first}{HEADER_ROW}:{last}{HEADER_ROW}", _HEADER_FMT)
    client.format_range(
        tab,
        f"{first}{FIRST_ROW}:{last}{chart_last}",
        {"textFormat": {"foregroundColor": {"red": 0.55, "green": 0.58, "blue": 0.62}, "fontSize": 9}},
    )
    for col in CHART_COLS[1:]:
        client.format_range(tab, f"{col}{FIRST_ROW}:{col}{chart_last}", _CURRENCY)
    client.delete_charts(tab)
    client.add_line_chart(
        tab,
        title="Cumulative net by week",
        domain_a1=f"{first}{HEADER_ROW}:{first}{chart_last}",
        series_a1=[f"{col}{HEADER_ROW}:{col}{chart_last}" for col in CHART_COLS[1:]],
        anchor_cell_a1=CHART_ANCHOR_CELL,
        plot_hidden_data=True,
    )
    # The #N/A gaps that make the line stop at the last played week are chart plumbing, not
    # something to read: hide the helper block. The chart keeps plotting it because of
    # `plot_hidden_data` above.
    client.hide_columns(tab, CHART_COLS[0], CHART_COLS[-1])
    return f"{tab}: chart data {first}:{last} written (hidden), chart rebuilt"


def build_season_tab(client: SheetsClient, tab: str) -> str:
    """One-time build: writes the whole grid, styles the header/YTD rows,
    and adds the cumulative-net-by-week line chart. Not safe to re-run
    against a tab already holding real weekly data -- see module
    docstring."""
    grid = build_season_grid()
    client.write_tab(tab, grid)
    client.freeze(tab, rows=1)

    client.format_range(tab, f"A{HEADER_ROW}:S{HEADER_ROW}", _HEADER_FMT)
    client.format_range(tab, f"A{YTD_LABEL_ROW}", {"textFormat": {"bold": True}})
    client.format_range(tab, f"A{YTD_HEADER_ROW}:F{YTD_HEADER_ROW}", {"textFormat": {"bold": True}})

    for col in ("B", "C", "D", "E", "F", "G", "H", "I", "N", "O", "P", "Q", "R", "S"):
        client.format_range(tab, f"{col}{FIRST_ROW}:{col}{LAST_ROW}", _CURRENCY)
    for row in (YTD_CASH_ROW, YTD_GPP_ROW, YTD_BETTING_ROW, YTD_TOTAL_ROW, YTD_OTHER_ROW):
        client.format_range(tab, f"B{row}:C{row}", _CURRENCY)
        client.format_range(tab, f"D{row}", _PERCENT)

    add_season_chart(client, tab)

    return f"{tab}: {len(grid)} row(s) written, chart added"


class SeasonOtherError(Exception):
    pass


def other_columns_state(header: list[str]) -> str:
    """ "done" when R/S already hold the Other columns, "ready" when R is blank and S is the old chart block's
    first header (the layout before Plan D item 2), else "unknown" (stop: something else is there)."""
    padded = [str(v).strip() for v in header] + [""] * 26
    r, s_ = padded[17], padded[18]
    if (r, s_) == (OTHER_NET_HEADER, OTHER_RISKED_HEADER):
        return "done"
    if r == "" and s_ == CHART_HEADER[0]:
        return "ready"
    return "unknown"


def add_other_columns(client: SheetsClient, tab: str) -> list[str]:
    """Upgrades an existing Season tab (data kept): inserts one column at S (a real `insertDimension`, so the
    hidden chart block and the chart's own ranges move S:W -> T:X), titles R/S, makes Total Net (E) and
    Cum. Total (Q) count R, adds S to the Total row's Risked and writes the "Other sports" YTD row under
    Total. Then    rebuilds the chart from `CHART_COLS`. Re-running on an upgraded tab rewrites the same
    formulas."""
    header = client.read_range(tab, f"A{HEADER_ROW}:Z{HEADER_ROW}")
    state = other_columns_state(header[0] if header else [])
    if state == "unknown":
        found = (header[0] + [""] * 26)[17:19] if header else ["", ""]
        raise SeasonOtherError(
            f"{tab}!R1:S1 read {found!r}: not the layout this upgrade expects -- nothing written"
        )
    out = []
    if state == "ready":
        ytd = client.read_range(tab, f"A{YTD_OTHER_ROW}:F{YTD_OTHER_ROW}")
        if ytd and any(str(v).strip() for v in ytd[0]):
            raise SeasonOtherError(f"{tab}!A{YTD_OTHER_ROW}:F{YTD_OTHER_ROW} is not blank -- nothing written")
        client.insert_columns(tab, at_column="S")
        out.append(
            f"{tab}: inserted a column at S (the chart block moved to {CHART_COLS[0]}:{CHART_COLS[-1]})"
        )
    client.update_ranges(
        tab,
        {
            f"R{HEADER_ROW}:S{HEADER_ROW}": [[OTHER_NET_HEADER, OTHER_RISKED_HEADER]],
            f"E{FIRST_ROW}:E{LAST_ROW}": [[total_net_formula(r)] for r in range(FIRST_ROW, LAST_ROW + 1)],
            f"Q{FIRST_ROW}:Q{LAST_ROW}": [[cum_total_formula(r)] for r in range(FIRST_ROW, LAST_ROW + 1)],
            f"C{YTD_TOTAL_ROW}": [[total_risked_formula()]],
            f"A{YTD_OTHER_ROW}:F{YTD_OTHER_ROW}": [other_ytd_row()],
        },
    )
    client.format_range(tab, f"R{HEADER_ROW}:S{HEADER_ROW}", _HEADER_FMT)
    client.format_range(tab, f"R{FIRST_ROW}:S{LAST_ROW}", _CURRENCY)
    client.format_range(tab, f"B{YTD_OTHER_ROW}:C{YTD_OTHER_ROW}", _CURRENCY)
    client.format_range(tab, f"D{YTD_OTHER_ROW}", _PERCENT)
    out.append(
        f"{tab}: R/S are {OTHER_NET_HEADER} / {OTHER_RISKED_HEADER}; E and Q count R; YTD row {YTD_OTHER_ROW}"
    )
    out.append(add_season_chart(client, tab))
    return out
