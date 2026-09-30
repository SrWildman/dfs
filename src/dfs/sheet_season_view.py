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

CHART_ANCHOR_CELL = f"A{YTD_TOTAL_ROW + 2}"

# Chart data (S:W): a Week 0 baseline row of zeros, then one row per week
# that has data. Feeds the chart only -- N:Q alone would start every line at
# Week 1's value, and would flat-line through the weeks not yet played.
CHART_COLS = ("S", "T", "U", "V", "W")
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
]

_HEADER_FMT = {
    "backgroundColor": {"red": 0.1254902, "green": 0.14901961, "blue": 0.18431373},
    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "fontSize": 10, "bold": True},
}
_CURRENCY = {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
_PERCENT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}


def _week_row(week: int) -> list:
    r = FIRST_ROW + week - 1
    return [
        week,
        "",
        "",
        "",
        f"=B{r}+C{r}+D{r}",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        f"=SUM($B${FIRST_ROW}:B{r})",
        f"=SUM($C${FIRST_ROW}:C{r})",
        f"=SUM($D${FIRST_ROW}:D{r})",
        f"=N{r}+O{r}+P{r}",
    ]


def build_season_grid() -> list[list]:
    rows: list[list] = [HEADER]
    rows.extend(_week_row(w) for w in range(1, MAX_WEEK + 1))
    rows.append([])  # row 20, blank spacer
    rows.append(["Year to date"])  # row 21
    rows.append(["", "Net", "Risked", "ROI", "Record (W-L-P)", "Exp. Wins"])  # row 22

    b, g, i = f"B{FIRST_ROW}:B{LAST_ROW}", f"G{FIRST_ROW}:G{LAST_ROW}", f"I{FIRST_ROW}:I{LAST_ROW}"
    c, h = f"C{FIRST_ROW}:C{LAST_ROW}", f"H{FIRST_ROW}:H{LAST_ROW}"
    d = f"D{FIRST_ROW}:D{LAST_ROW}"
    e = f"E{FIRST_ROW}:E{LAST_ROW}"
    wins, losses, pushes, exp_wins = (
        f"J{FIRST_ROW}:J{LAST_ROW}",
        f"K{FIRST_ROW}:K{LAST_ROW}",
        f"L{FIRST_ROW}:L{LAST_ROW}",
        f"M{FIRST_ROW}:M{LAST_ROW}",
    )

    rows.append(
        ["Cash", f"=SUM({b})", f"=SUM({g})", guard_formula(f"=B{YTD_CASH_ROW}/C{YTD_CASH_ROW}"), "", ""]
    )  # 23
    rows.append(
        ["GPP", f"=SUM({c})", f"=SUM({h})", guard_formula(f"=B{YTD_GPP_ROW}/C{YTD_GPP_ROW}"), "", ""]
    )  # 24
    rows.append(
        [
            "Betting",
            f"=SUM({d})",
            f"=SUM({i})",
            guard_formula(f"=B{YTD_BETTING_ROW}/C{YTD_BETTING_ROW}"),
            f'=SUM({wins})&"-"&SUM({losses})&"-"&SUM({pushes})',
            f'=ROUND(SUM({exp_wins}),1)&" vs "&SUM({wins})',
        ]
    )  # 25
    rows.append(
        [
            "Total",
            f"=SUM({e})",
            f"=SUM({g})+SUM({h})+SUM({i})",
            guard_formula(f"=B{YTD_TOTAL_ROW}/C{YTD_TOTAL_ROW}"),
            "",
            "",
        ]
    )  # 26
    return rows


def chart_data_grid() -> list[list]:
    """S1:W(LAST_ROW+1): header, Week 0 = $0 baseline, then week w mirrors
    Season row FIRST_ROW+w-1's cumulative N:Q. A week with no Cash/GPP/
    Betting net yet gives #N/A, which a line chart leaves as a gap (the
    #N/A is muted in the sheet itself, see `add_season_chart`)."""
    rows: list[list] = [CHART_HEADER, [0, 0, 0, 0, 0]]
    for week in range(1, MAX_WEEK + 1):
        r = FIRST_ROW + week - 1
        has_data = f"COUNT($B{r}:$D{r})=0"
        rows.append([f"=A{r}"] + [f"=IF({has_data},NA(),{col}{r})" for col in ("N", "O", "P", "Q")])
    return rows


def add_season_chart(client: SheetsClient, tab: str) -> str:
    """(Re)builds the chart-data block and the cumulative-net chart from it.
    Safe to re-run: existing charts on the tab are deleted first."""
    client.update_range(tab, f"S{HEADER_ROW}:W{CHART_LAST_ROW}", chart_data_grid())
    client.format_range(tab, f"S{HEADER_ROW}:W{HEADER_ROW}", _HEADER_FMT)
    client.format_range(
        tab,
        f"S{FIRST_ROW}:W{CHART_LAST_ROW}",
        {"textFormat": {"foregroundColor": {"red": 0.55, "green": 0.58, "blue": 0.62}, "fontSize": 9}},
    )
    for col in ("T", "U", "V", "W"):
        client.format_range(tab, f"{col}{FIRST_ROW}:{col}{CHART_LAST_ROW}", _CURRENCY)
    client.delete_charts(tab)
    client.add_line_chart(
        tab,
        title="Cumulative net by week",
        domain_a1=f"S{HEADER_ROW}:S{CHART_LAST_ROW}",
        series_a1=[f"{col}{HEADER_ROW}:{col}{CHART_LAST_ROW}" for col in ("T", "U", "V", "W")],
        anchor_cell_a1=CHART_ANCHOR_CELL,
        plot_hidden_data=True,
    )
    # The #N/A gaps that make the line stop at the last played week are chart plumbing, not
    # something to read: hide the helper block. The chart keeps plotting it because of
    # `plot_hidden_data` above.
    client.hide_columns(tab, CHART_COLS[0], CHART_COLS[-1])
    return f"{tab}: chart data S:W written (hidden), chart rebuilt"


def build_season_tab(client: SheetsClient, tab: str) -> str:
    """One-time build: writes the whole grid, styles the header/YTD rows,
    and adds the cumulative-net-by-week line chart. Not safe to re-run
    against a tab already holding real weekly data -- see module
    docstring."""
    grid = build_season_grid()
    client.write_tab(tab, grid)
    client.freeze(tab, rows=1)

    client.format_range(tab, f"A{HEADER_ROW}:Q{HEADER_ROW}", _HEADER_FMT)
    client.format_range(tab, f"A{YTD_LABEL_ROW}", {"textFormat": {"bold": True}})
    client.format_range(tab, f"A{YTD_HEADER_ROW}:F{YTD_HEADER_ROW}", {"textFormat": {"bold": True}})

    for col in ("B", "C", "D", "E", "F", "G", "H", "I", "N", "O", "P", "Q"):
        client.format_range(tab, f"{col}{FIRST_ROW}:{col}{LAST_ROW}", _CURRENCY)
    for row in (YTD_CASH_ROW, YTD_GPP_ROW, YTD_BETTING_ROW, YTD_TOTAL_ROW):
        client.format_range(tab, f"B{row}:C{row}", _CURRENCY)
        client.format_range(tab, f"D{row}", _PERCENT)

    add_season_chart(client, tab)

    return f"{tab}: {len(grid)} row(s) written, chart added"
