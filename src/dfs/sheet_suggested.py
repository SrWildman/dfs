"""The `Suggested` tab: complete builds from Sam's pool (`lineup_suggest`), written by `dfs lineups suggest`.

Nothing on it is typed and nothing on any other tab reads it except the Apps Script's Lineup Tools > Copy
build to Lineups. Each build is a block in the Lineups layout (Sam approved it, Week 6):

    Build 4 | GPP | NO stack + NYG bring-back                             (a dark bar)
    Slot | Name | Team | Opp | Salary | CalPts | Kickoff | Own% | Typical | Big night | Cash % | Top 1%
    QB   | ...                                                    (nine player rows, FLEX = the latest
    ...                                                           kickoff)
    Total |     |     |    | $49,900 | 132.9 |        |      | 126.1 | 163.4 | 25% | 1.4%

The block's first row names it (`Build N`, its kind Cash or GPP, and for a GPP build its stack), the second
row is the column header, then nine player rows in `ROSTER_SLOTS` order, then the Total row: salary and
projection, and the simulator's four numbers under their own labels. A blank row separates blocks. Three
lines above the first build say what the tab is, what it was built from and how to use a build.

`parse_builds` reads the same layout back (the Apps Script's `planCopyBuild` does the same in JavaScript,
and the tests run both over one grid), so the layout is stated once, here."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from dfs.kickoff import kickoff_label
from dfs.lineup_suggest import Build, SuggestReport
from dfs.models import ROSTER_SLOTS
from dfs.sheet_lineup_sim import P_CASH_GREEN, P_GPP_GREEN, TOP1_HEADER
from dfs.sheet_lineup_tints import apply_lineup_tints
from dfs.sheets import SheetsClient, column_letter

TAB = "Suggested"
BUILD_LABEL = "Build"  # column A of a block's first row reads "Build 4"
COLUMNS = [
    "Slot",
    "Name",
    "Team",
    "Opp",
    "Salary",
    "CalPts",
    "Kickoff",
    "Own%",
    "Typical",
    "Big night",
    "Cash %",
    TOP1_HEADER,
]
LAST_COLUMN = column_letter(len(COLUMNS) - 1)  # the last visible column
GAME_COLUMN = "GameID"  # a hidden helper after the visible columns: the correlation tints group by game
COLUMN_COUNT = len(COLUMNS) + 1
GAME_COLUMN_LETTER = column_letter(len(COLUMNS))
TOTAL_LABEL = "Total"
TITLE = "SUGGESTED LINEUPS  —  complete builds from your pool, scored by the simulator"
HOW_TO = (
    "To use one: Lineup Tools > Copy build to Lineups (it goes into the first empty block and never "
    "overwrites your names). Nothing on this tab changes Lineups on its own. Name and Team tints, as on "
    "Lineups: blue = the QB's stack, amber = his bring-back, lavender = other correlated players."
)
EMPTY = "No builds yet. Put players in your pool (Cash, GPP or Both) and run `dfs lineups suggest`."
WIDTHS = [56, 170, 48, 48, 70, 62, 92, 56, 66, 74, 62, 62]
FIRST_BUILD_ROW = 5  # rows 1-3 are the title, the status and the how-to; row 4 is blank


@dataclass
class Layout:
    rows: list[list]
    bar_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    player_rows: list[int] = field(default_factory=list)
    total_rows: list[int] = field(default_factory=list)
    status_rows: list[int] = field(default_factory=list)


def projection_label(metric: str) -> str:
    """The header over the projection column: the projection the builds used."""
    return metric


def status_line(
    report: SuggestReport,
    *,
    metric: str,
    cash_line: float,
    gpp_target: float,
    lines_text: str | None = None,
) -> str:
    """ "CalPts · pool 47 Cash / 51 GPP · cash line 143.9 · GPP target 190 · 8 builds in 3 s". `lines_text`
    (what the Cash % and Top 1% were measured against, `SimLines.status`) replaces the two fixed numbers."""
    pool = report.candidates
    if not pool and not report.builds:  # nothing has been run yet (a fresh template or week)
        return "Not run yet: `dfs lineups suggest` fills this tab from your pool."
    return (
        f"{metric}  ·  pool {pool.get('cash', 0)} for Cash / {pool.get('gpp', 0)} for GPP  ·  "
        + (lines_text or f"cash line {cash_line:g}  ·  GPP target {gpp_target:g}")
        + f"  ·  {len(report.builds)} build"
        f"{'' if len(report.builds) == 1 else 's'} in {report.seconds:.0f} s"
    )


def _details(edge: pd.DataFrame) -> dict[str, tuple[str, float | str]]:
    """Name -> (kickoff text, ownership) from the edge frame."""
    own = pd.to_numeric(edge.get("Own%", pd.Series(dtype=float)), errors="coerce")
    out: dict[str, tuple[str, float | str]] = {}
    for name, start, share in zip(
        edge["Name"],
        edge.get("GameStart", pd.Series("", index=edge.index)),
        own.reindex(edge.index),
        strict=True,
    ):
        out.setdefault(
            str(name), (kickoff_label(start), "" if pd.isna(share) or share <= 0 else float(share))
        )
    return out


def _game_ids(edge: pd.DataFrame) -> dict[str, str]:
    """Name -> the player's game (EdgeRaw's `GameID`), for the stack tints."""
    if "GameID" not in edge.columns:
        return {}
    out: dict[str, str] = {}
    for name, game in zip(edge["Name"], edge["GameID"], strict=True):
        if pd.notna(game) and str(game).strip():
            out.setdefault(str(name), str(game))
    return out


def _total_own(build: Build, details: dict[str, tuple[str, float | str]]) -> float | str:
    """The build's total ownership (the nine players' `Own%` added up, like Lineups' Total row), blank when no
    ownership is out yet."""
    shares = [details.get(p.name, ("", ""))[1] for p in build.players]
    numbers = [x for x in shares if isinstance(x, float)]
    return round(sum(numbers), 4) if numbers else ""


def build_layout(
    report: SuggestReport,
    edge: pd.DataFrame,
    *,
    metric: str,
    cash_line: float,
    gpp_target: float,
    lines_text: str | None = None,
) -> Layout:
    """The tab's rows (1-based numbers resolved) and which rows are bars, headers, players and totals."""
    layout = Layout(rows=[])
    header = [*COLUMNS, GAME_COLUMN]
    header[COLUMNS.index("CalPts")] = projection_label(metric)

    def add(values: list) -> int:
        layout.rows.append([*values, *[""] * (COLUMN_COUNT - len(values))])
        return len(layout.rows)

    add([TITLE])
    layout.status_rows.append(
        add(
            [
                status_line(
                    report, metric=metric, cash_line=cash_line, gpp_target=gpp_target, lines_text=lines_text
                )
            ]
        )
    )
    layout.status_rows.append(add([HOW_TO]))
    add([])
    if not report.builds:
        layout.status_rows.append(add([EMPTY]))
    details = _details(edge)
    games = _game_ids(edge)
    for build in report.builds:
        layout.bar_rows.append(
            add([f"{BUILD_LABEL} {build.number}", "Cash" if build.kind == "cash" else "GPP", build.title])
        )
        layout.header_rows.append(add(header))
        for slot, player in zip(ROSTER_SLOTS, build.players, strict=True):
            kickoff, own = details.get(player.name, ("", ""))
            layout.player_rows.append(
                add(
                    [slot, player.name, player.team, player.opp, player.salary, player.value, kickoff, own]
                    + [""] * 4
                    + [games.get(player.name, "")]
                )
            )
        layout.total_rows.append(
            add(
                [TOTAL_LABEL, "", "", "", build.salary, build.projection, "", _total_own(build, details)]
                + [
                    round(build.typical, 1),
                    round(build.big_night, 1),
                    round(build.p_cash, 4),
                    round(build.p_gpp, 4),
                ]
            )
        )
        add([])
    for note in report.notes:
        layout.status_rows.append(add([note]))
    return layout


def parse_builds(grid: list[list]) -> list[dict]:
    """The builds on a Suggested grid: `{"number", "kind", "title", "names": [nine names in slot order]}` for
    every block whose bar row reads `Build N` and which has nine named player rows under its column header.
    The tests run the Apps Script's `planCopyBuild` over the same grid and compare."""
    builds = []
    for i, row in enumerate(grid):
        label = str(row[0]).strip() if row else ""
        head, _, number = label.partition(" ")
        if head != BUILD_LABEL or not number.isdigit():
            continue
        players = grid[i + 2 : i + 2 + len(ROSTER_SLOTS)]
        names = [str(p[1]).strip() if len(p) > 1 else "" for p in players]
        if len(names) != len(ROSTER_SLOTS) or not all(names):
            continue
        builds.append(
            {
                "number": int(number),
                "kind": str(row[1]).strip() if len(row) > 1 else "",
                "title": str(row[2]).strip() if len(row) > 2 else "",
                "names": names,
            }
        )
    return builds


def write_suggested(
    client: SheetsClient,
    report: SuggestReport,
    edge: pd.DataFrame,
    *,
    metric: str,
    cash_line: float,
    gpp_target: float,
    lines_text: str | None = None,
) -> str:
    """Write and style the whole tab (it is rebuilt from scratch every run)."""
    from dfs.sheet_style import _HEADER_FMT, _SUBHEAD_FMT, _TITLE_FMT, INK_MUTED, WHITE, _chip, _num

    layout = build_layout(
        report, edge, metric=metric, cash_line=cash_line, gpp_target=gpp_target, lines_text=lines_text
    )
    last_row = max(len(layout.rows), 40)
    client.write_tab(TAB, layout.rows)
    client.clear_conditional_formats(TAB)
    client.format_range(
        TAB,
        f"A1:{LAST_COLUMN}{last_row + 20}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": None,
            "numberFormat": None,
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    client.set_column_widths(TAB, {column_letter(i): px for i, px in enumerate(WIDTHS)})
    client.format_range(TAB, f"A1:{LAST_COLUMN}1", _TITLE_FMT)
    for row in layout.status_rows:
        client.format_range(
            TAB, f"A{row}:{LAST_COLUMN}{row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row in layout.bar_rows:
        client.format_range(TAB, f"A{row}:{LAST_COLUMN}{row}", _HEADER_FMT)
    for row in layout.header_rows:
        client.format_range(TAB, f"A{row}:{LAST_COLUMN}{row}", _SUBHEAD_FMT)
    number = {c: column_letter(COLUMNS.index(c)) for c in COLUMNS}
    for first, last in _runs(layout.player_rows):
        client.format_range(
            TAB,
            f"{number['Salary']}{first}:{number['Salary']}{last}",
            {**_num("$#,##0", "CURRENCY"), "horizontalAlignment": "RIGHT"},
        )
        client.format_range(
            TAB,
            f"{number['CalPts']}{first}:{number['CalPts']}{last}",
            {**_num("0.0"), "horizontalAlignment": "RIGHT"},
        )
        client.format_range(
            TAB,
            f"{number['Own%']}{first}:{number['Own%']}{last}",
            {**_num("0%"), "horizontalAlignment": "RIGHT"},
        )
    for row in layout.total_rows:
        client.format_range(
            TAB,
            f"A{row}:{LAST_COLUMN}{row}",
            {"textFormat": {"bold": True}, "borders": {"top": {"style": "SOLID"}}},
        )
        client.format_range(
            TAB,
            f"{number['Salary']}{row}",
            {**_num("$#,##0", "CURRENCY"), "horizontalAlignment": "RIGHT"},
        )
        client.format_range(TAB, f"{number['CalPts']}{row}", {**_num("0.0"), "horizontalAlignment": "RIGHT"})
        client.format_range(TAB, f"{number['Own%']}{row}", {**_num("0%"), "horizontalAlignment": "RIGHT"})
        client.format_range(
            TAB,
            f"{number['Typical']}{row}:{number['Big night']}{row}",
            {**_num("0.0"), "horizontalAlignment": "RIGHT"},
        )
        client.format_range(TAB, f"{number['Cash %']}{row}", {**_num("0%"), "horizontalAlignment": "RIGHT"})
        client.format_range(
            TAB, f"{number[TOP1_HEADER]}{row}", {**_num("0.0%"), "horizontalAlignment": "RIGHT"}
        )
    for row in layout.total_rows:  # green at the same lines Lineups uses (Cash % 50%, Top 1% 2%)
        for column, line in (("Cash %", P_CASH_GREEN), (TOP1_HEADER, P_GPP_GREEN)):
            cell = f"{number[column]}{row}"
            client.add_boolean_rule(
                TAB,
                cell,
                condition_type="NUMBER_GREATER_THAN_EQ",
                values=[str(line)],
                fmt=_chip(
                    {"red": 0.85, "green": 0.94, "blue": 0.85}, {"red": 0.10, "green": 0.45, "blue": 0.25}
                ),
            )
    if report.builds:
        client.hide_columns(TAB, GAME_COLUMN_LETTER, GAME_COLUMN_LETTER)
        # The same stack / bring-back / other tints as Lineups, over each build's Name and Team cells. The
        # Slot stands in for the position (only QB and DST matter to the rules; FLEX is never either).
        apply_lineup_tints(
            client,
            TAB,
            header_row=layout.header_rows[0],
            name_blocks=_runs(layout.player_rows),
            header_names={"Pos.": "Slot", "Opp.": "Opp"},
        )
    return (
        f"{TAB}: {len(report.builds)} build(s) written ({len(layout.rows)} rows)"
        if report.builds
        else f"{TAB}: cleared (no builds)"
    )


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for row in sorted(rows):
        if out and row == out[-1][1] + 1:
            out[-1] = (out[-1][0], row)
        else:
            out.append((row, row))
    return out


def build_summary(build: Build) -> str:
    """One terminal line for a build: number, kind, title, salary, projection and the odds."""
    return (
        f"Build {build.number}  {build.kind.upper():<4} ${build.salary:,}  {build.projection:.1f} pts  "
        f"Cash {build.p_cash:.0%}  Top 1% {build.p_gpp:.1%}  {build.title}"
    )


def build_lines(build: Build, edge: pd.DataFrame) -> list[str]:
    """Nine terminal lines for a build (slot, name, team v opponent, salary, projection, kickoff)."""
    details = _details(edge)
    return [
        f"{slot:<5}{player.name:<24}{player.team:>4} v {player.opp:<4} ${player.salary:>6,}  "
        f"{player.value:>5.1f}  {details.get(player.name, ('', ''))[0]}"
        for slot, player in zip(ROSTER_SLOTS, build.players, strict=True)
    ]


def ensure_suggested_tab(client: SheetsClient) -> str:
    """`dfs setup build-views`: create the tab empty (title, how-to and the "no builds yet" line) when it does
    not exist; never touch one that does (it holds the last run's builds)."""
    if client.tab_exists(TAB):
        return f"{TAB}: already present -- left as the last run wrote it"
    layout = build_layout(
        SuggestReport(), pd.DataFrame({"Name": []}), metric="CalPts", cash_line=0.0, gpp_target=0.0
    )
    client.write_tab(TAB, layout.rows)
    return f"{TAB}: created (empty)"
