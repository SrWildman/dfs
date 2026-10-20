"""The `Suggested` tab: complete builds from Sam's pool (`lineup_suggest`), written by `dfs lineups suggest`,
and the working state he iterates on (`lineup_iterate`).

Mostly output; what Sam types is the lock ticks, the decision cell on each bar, a name typed over a player and
the rules box (typed cells: pale yellow, carved out of the warning-only protection, `typed_ranges`). Nothing
on any other tab reads it except the Apps Script's Lineup Tools > Copy builds to Lineups. Each build is a
block in the Lineups layout (Sam approved it, Week 6):

    Build 4 | GPP | Rebuild | NO stack + NYG bring-back                   (a dark bar)
    Slot | 🔒 | Name | Team | Opp | Salary | CalPts | Kickoff | Own% | Typical | Big night | Cash % | Top 1%
    QB   | [x] | ...                                          (nine player rows, FLEX = the latest
    ...                                                        kickoff)
    Total |    |     |    | $49,900 | 132.9 |        |      | 126.1 | 163.4 | 25% | 1.4%
         bring-back → McBride (yours, was ...); Cash % 61% → 64%                (the note, when there is one)

The bar's first cell names it (`Build N`), the second its kind (Cash or GPP, over the lock column), the third
is the DECISION (Keep / Rebuild / Drop / Vary N; blank is Keep) and the rest its title. The second row is
the column header, then nine player rows in `ROSTER_SLOTS` order, then the Total row: salary and projection,
and the simulator's four numbers under their own labels. A blank row separates blocks. Above the first build:
the title, a status line ("Round 3  ·  ..."), how to use it, and the RULES box (one typed line each for Always
use, Never use, Max exposure, Stack rules and Builds wanted; `suggest_state.py` has the syntax). Below the
last: the run's notes and a History block, one line per round.

`parse_builds` reads the builds back for Copy (the Apps Script's `planCopyBuild` does the same in JavaScript,
and the tests run both over one grid) and `parse_state` reads everything the rerun needs, so the layout is
stated once, here. Every position comes from `COLUMNS` (`NAME_INDEX`, `LOCK_INDEX`, `TITLE_INDEX`): the Apps
Script has its own copies, which `tests/test_apps_script.py` checks against these."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from dfs import perf
from dfs.kickoff import kickoff_label
from dfs.lineup_suggest import Build, SuggestReport
from dfs.models import ROSTER_SLOTS
from dfs.sheet_lineup_sim import P_CASH_GREEN, P_GPP_GREEN, TOP1_HEADER
from dfs.sheet_lineup_tints import apply_lineup_tints
from dfs.sheets import SheetsClient, column_letter
from dfs.suggest_state import DECISION_OPTIONS, RULE_LABELS, RULE_SYNTAX, RulesText, TabBuild, TabState

TAB = "Suggested"
BUILD_LABEL = "Build"  # column A of a block's first row reads "Build 4"
LOCK_HEADER = "🔒"  # the lock column, left of Name: a checkbox per player
COLUMNS = [
    "Slot",
    LOCK_HEADER,
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
# Where things sit, all derived from COLUMNS (the Apps Script copies these and a test compares them).
LOCK_INDEX = COLUMNS.index(LOCK_HEADER)
NAME_INDEX = COLUMNS.index("Name")
KIND_INDEX = LOCK_INDEX  # a bar's second cell (Cash / GPP) sits over the lock column
DECISION_INDEX = NAME_INDEX  # ... its third, the decision, over Name
TITLE_INDEX = DECISION_INDEX + 1  # ... and the title after it
GAME_COLUMN = "GameID"  # a hidden helper after the visible columns: the correlation tints group by game
YOURS_COLUMN = "Yours"  # a second hidden helper: TRUE on a name Sam typed in (the "your pick" tint)
COLUMN_COUNT = len(COLUMNS) + 2
GAME_COLUMN_LETTER = column_letter(len(COLUMNS))
YOURS_COLUMN_LETTER = column_letter(len(COLUMNS) + 1)
TOTAL_LABEL = "Total"
HISTORY_LABEL = "History"
TITLE = "SUGGESTED LINEUPS  —  complete builds from your pool, scored by the simulator"
HOW_TO = (
    "To use some: Lineup Tools > Copy builds to Lineups (type 1,4,6 or all; they go into the empty blocks "
    "in order and never overwrite your names). A fresh run lists Cash first, then GPP, each by QB and best "
    "CalPts total; a rerun lists kept builds, then rebuilt, then new ones. Name and Team tints, as on "
    "Lineups: blue = the QB's stack, amber = his bring-back, lavender = other correlated players. To "
    "iterate: set a build's decision (Keep, Rebuild, Drop, Vary N), tick 🔒 on the players who stay, or type "
    "another name over one (it counts as a lock, in green), fill the rules box, then run `dfs lineups "
    "suggest --iterate`."
)
TITLE_KEY = (
    "A GPP bar reads: shape, copies expected in a GPP of the size the run assumed, the nine players' "
    "ownership sum and product, and the Top 1% share (Top 1% divided by 1 + the copies)."
)
RANK_NOTES = {
    "top1": "GPP builds are ranked by Top 1%.",
    "share": "GPP builds are ranked by the Top 1% share.",
}
RULES_TITLE = "RULES  ·  typed here; they apply to every rebuilt and new build"
ANY_DST_NOTE = "plus any DST on the slate (pooled or not)"
EMPTY = "No builds yet. Put players in your pool (Cash, GPP or Both) and run `dfs lineups suggest`."
WIDTHS = [56, 34, 170, 48, 48, 70, 62, 92, 56, 66, 74, 62, 62]
# Rows 1-3 are the title, the status and the how-to; row 4 is blank; then the rules box (a title, one row per
# label, the syntax line) and a blank row, and the builds start.
FIRST_BUILD_ROW = 8 + len(RULE_LABELS)
# "Your pick": a name Sam typed over another player. A mint green, not near the blue / amber / lavender of the
# correlation tints (a rule of its own, above them).
YOUR_PICK_TINT = {"red": 0.74, "green": 0.90, "blue": 0.78}


def cells(values: dict[str, object]) -> list:
    """A full-width row with `values` under their column headers (anything else blank)."""
    row: list = [""] * COLUMN_COUNT
    for header, value in values.items():
        row[COLUMNS.index(header)] = value
    return row


def typed_ranges() -> list[str]:
    """What Sam types into on this tab, as whole columns (the rows move with every run): the lock ticks and
    the Name column (which holds the decision cells, the rules box and the names he types over). These are
    what `dfs setup protect` carves out of the tab's warning-only protection and `dfs doctor` checks."""
    lock, name = column_letter(LOCK_INDEX), column_letter(NAME_INDEX)
    return [f"{lock}:{lock}", f"{name}:{name}"]


@dataclass
class Layout:
    rows: list[list]
    bar_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    player_rows: list[int] = field(default_factory=list)
    total_rows: list[int] = field(default_factory=list)
    status_rows: list[int] = field(default_factory=list)
    rules_title_row: int = 0
    rules_rows: list[int] = field(default_factory=list)
    note_rows: list[tuple[int, bool]] = field(default_factory=list)  # (row, a refusal: shown red)


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
    """ "Round 3  ·  CalPts · pool 47 Cash / 51 GPP + any DST · cash line 143.9 · GPP target 190 · 8 builds".
    `lines_text` (what the Cash % and Top 1% were measured against, `SimLines.status`) replaces the two fixed
    numbers. The pool counts include every defense on the slate (any DST may be used, pooled or not). The
    round (`parse_state` reads it back) leads the line once the state file has one."""
    pool = report.candidates
    if not pool and not report.builds:  # nothing has been run yet (a fresh template or week)
        return "Not run yet: `dfs lineups suggest` fills this tab from your pool."
    lead = f"Round {report.round}  ·  " if report.round else ""
    return (
        f"{lead}{metric}  ·  pool {pool.get('cash', 0)} for Cash / {pool.get('gpp', 0)} for GPP, "
        f"{ANY_DST_NOTE}  ·  "
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


def bar_text(build: Build) -> str:
    """The text on a build's bar: its title, and for a build with ownership the nine players' ownership sum
    and product and (GPP) the Top 1% share. The title already names the shape and the copies expected."""
    if build.own_sum is None or build.own_product is None:
        return build.title
    text = f"{build.title}  ·  own {build.own_sum:.0%}, product {build.own_product:.1e}"
    return text + (f"  ·  Top 1% share {build.objective:.2%}" if build.objective is not None else "")


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
    header = [*COLUMNS, GAME_COLUMN, YOURS_COLUMN]
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
    how_to = f"{HOW_TO} {RANK_NOTES.get(report.rank, '')} {TITLE_KEY}".strip() if report.builds else HOW_TO
    layout.status_rows.append(add([how_to]))
    add([])
    layout.rules_title_row = add([RULES_TITLE])
    for label in RULE_LABELS:
        layout.rules_rows.append(add(cells({"Slot": label, "Name": report.rules.get(label, "")})))
    layout.status_rows.append(add([RULE_SYNTAX]))
    add([])
    if not report.builds:
        layout.status_rows.append(add([EMPTY]))
    details = _details(edge)
    games = _game_ids(edge)
    for build in report.builds:
        layout.bar_rows.append(
            add(
                cells(
                    {
                        "Slot": f"{BUILD_LABEL} {build.number}",
                        LOCK_HEADER: "Cash" if build.kind == "cash" else "GPP",
                        "Name": build.decision,
                        "Team": bar_text(build),
                    }
                )
            )
        )
        layout.header_rows.append(add(header))
        locked, yours = set(build.locked), set(build.yours)
        for slot, player in zip(ROSTER_SLOTS, build.players, strict=True):
            kickoff, own = details.get(player.name, ("", ""))
            layout.player_rows.append(
                add(
                    [
                        *cells(
                            {
                                "Slot": slot,
                                LOCK_HEADER: player.name in locked,
                                "Name": player.name,
                                "Team": player.team,
                                "Opp": player.opp,
                                "Salary": player.salary,
                                "CalPts": player.value,
                                "Kickoff": kickoff,
                                "Own%": own,
                            }
                        )[: len(COLUMNS)],
                        games.get(player.name, ""),
                        True if player.name in yours else "",
                    ]
                )
            )
        layout.total_rows.append(
            add(
                cells(
                    {
                        "Slot": TOTAL_LABEL,
                        "Salary": build.salary,
                        "CalPts": build.projection,
                        "Own%": _total_own(build, details),
                        "Typical": round(build.typical, 1),
                        "Big night": round(build.big_night, 1),
                        "Cash %": round(build.p_cash, 4),
                        TOP1_HEADER: round(build.p_gpp, 4),
                    }
                )
            )
        )
        if build.note:
            layout.note_rows.append((add(cells({"Name": build.note})), build.problem))
        add([])
    for note in report.notes:
        layout.status_rows.append(add([note]))
    if report.history:
        add([])
        layout.status_rows.append(add([HISTORY_LABEL]))
        layout.status_rows.extend(add([line]) for line in report.history)
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
        names = [str(p[NAME_INDEX]).strip() if len(p) > NAME_INDEX else "" for p in players]
        if len(names) != len(ROSTER_SLOTS) or not all(names):
            continue
        builds.append(
            {
                "number": int(number),
                "kind": str(row[KIND_INDEX]).strip() if len(row) > KIND_INDEX else "",
                "title": str(row[TITLE_INDEX]).strip() if len(row) > TITLE_INDEX else "",
                "names": names,
            }
        )
    return builds


def _ticked(value: object) -> bool:
    return value is True or str(value).strip().upper() in ("TRUE", "1", "YES")


def parse_state(grid: list[list]) -> TabState:
    """What the rerun reads off the tab: the round the status line names, the rules box (found by its labels
    in column A, the text beside them) and every build with its decision, its nine names AS TYPED (a blank
    cell stays blank: the rerun reports it) and its nine lock ticks. A block missing player rows is
    skipped."""
    state = TabState()
    for row in grid[:3]:
        match = re.match(r"Round (\d+)\b", str(row[0]).strip()) if row else None
        if match:
            state.round = int(match.group(1))
            break
    first_bar = next(
        (i for i, row in enumerate(grid) if row and str(row[0]).strip().partition(" ")[0] == BUILD_LABEL),
        len(grid),
    )
    typed: dict[str, str] = {}
    for row in grid[:first_bar]:
        label = str(row[0]).strip() if row else ""
        if label in RULE_LABELS and label not in typed:
            typed[label] = str(row[NAME_INDEX]) if len(row) > NAME_INDEX else ""
    state.rules = RulesText.from_labels(typed)
    for i, row in enumerate(grid):
        label = str(row[0]).strip() if row else ""
        head, _, number = label.partition(" ")
        if head != BUILD_LABEL or not number.isdigit():
            continue
        players = grid[i + 2 : i + 2 + len(ROSTER_SLOTS)]
        if len(players) != len(ROSTER_SLOTS):
            continue

        def cell(r: list, index: int) -> object:
            return r[index] if len(r) > index else ""

        state.builds.append(
            TabBuild(
                number=int(number),
                kind=str(cell(row, KIND_INDEX)).strip().casefold(),
                title=str(cell(row, TITLE_INDEX)).strip(),
                decision=str(cell(row, DECISION_INDEX)).strip(),
                names=[str(cell(p, NAME_INDEX)).strip() for p in players],
                locked=[_ticked(cell(p, LOCK_INDEX)) for p in players],
            )
        )
    return state


def read_state(client: SheetsClient) -> TabState:
    """The Suggested tab as `TabState` (an empty one when the tab does not exist)."""
    if not client.tab_exists(TAB):
        return TabState()
    return parse_state(client.read_tab(TAB))


def write_suggested(
    client: SheetsClient,
    report: SuggestReport,
    edge: pd.DataFrame,
    *,
    metric: str,
    cash_line: float,
    gpp_target: float,
    lines_text: str | None = None,
    timings: dict[str, tuple[float, int]] | None = None,
) -> str:
    """Write and style the whole tab (it is rebuilt from scratch every run). The styling is one batch (the
    formatting requests are queued and sent together: it used to be one round trip per cell range, several
    hundred of them for a full set of builds, which is what made the run sit idle after printing).
    `timings`, when given, gets `"write tab"` and `"format tab"` -> (seconds, API requests)."""
    layout = build_layout(
        report, edge, metric=metric, cash_line=cash_line, gpp_target=gpp_target, lines_text=lines_text
    )
    with perf.measure(timings, "write tab"):
        client.write_tab(TAB, layout.rows)
    with perf.measure(timings, "format tab"), client.batched():
        _style(client, layout, report)
    return (
        f"{TAB}: {len(report.builds)} build(s) written ({len(layout.rows)} rows)"
        if report.builds
        else f"{TAB}: cleared (no builds)"
    )


def _style(client: SheetsClient, layout: Layout, report: SuggestReport) -> None:
    """Every format, rule, width and tint of the tab, for the rows `layout` describes."""
    from dfs.sheet_style import (
        _HEADER_FMT,
        _SUBHEAD_FMT,
        _TITLE_FMT,
        CRIT_FG,
        INK,
        INK_MUTED,
        INPUT_BG,
        WHITE,
        _chip,
        _num,
    )

    last_row = max(len(layout.rows), 40)
    area = f"A1:{LAST_COLUMN}{last_row + 20}"
    client.clear_conditional_formats(TAB)
    # The checkboxes and the decision dropdowns are re-set below for this run's rows: whatever the last run's
    # rows carried (they move) goes first.
    client.clear_data_validation(TAB, f"A1:{YOURS_COLUMN_LETTER}{last_row + 20}")
    client.format_range(
        TAB,
        area,
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
    number = {c: column_letter(COLUMNS.index(c)) for c in COLUMNS}
    name, lock = number["Name"], number[LOCK_HEADER]
    if layout.rules_title_row:
        client.format_range(
            TAB, f"A{layout.rules_title_row}:{LAST_COLUMN}{layout.rules_title_row}", _HEADER_FMT
        )
    for row in layout.rules_rows:  # the typed lines: a pale-yellow cue across the box
        client.format_range(TAB, f"A{row}", {"textFormat": {"bold": True}})
        client.format_range(TAB, f"{name}{row}:{LAST_COLUMN}{row}", {"backgroundColor": INPUT_BG})
    for row in layout.bar_rows:
        client.format_range(TAB, f"A{row}:{LAST_COLUMN}{row}", _HEADER_FMT)
        # the decision cell is typed: pale yellow with dark text on the dark bar, a dropdown (Vary N is typed)
        client.format_range(
            TAB,
            f"{name}{row}",
            {"backgroundColor": INPUT_BG, "textFormat": {"bold": True, "foregroundColor": INK}},
        )
        client.set_dropdown_validation(TAB, f"{name}{row}", DECISION_OPTIONS, strict=False)
    for row in layout.header_rows:
        client.format_range(TAB, f"A{row}:{LAST_COLUMN}{row}", _SUBHEAD_FMT)
    for first, last in _runs(layout.player_rows):
        client.set_checkbox_validation(TAB, f"{lock}{first}:{lock}{last}")
        client.format_range(
            TAB, f"{lock}{first}:{lock}{last}", {"backgroundColor": INPUT_BG, "horizontalAlignment": "CENTER"}
        )
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
    for row, refused in layout.note_rows:
        client.format_range(
            TAB,
            f"{name}{row}:{LAST_COLUMN}{row}",
            {
                "textFormat": {
                    "fontSize": 9,
                    "italic": True,
                    "foregroundColor": CRIT_FG if refused else INK_MUTED,
                }
            },
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
        client.hide_columns(TAB, GAME_COLUMN_LETTER, YOURS_COLUMN_LETTER)
        # The same stack / bring-back / other tints as Lineups, over each build's Name and Team cells. The
        # Slot stands in for the position (only QB and DST matter to the rules; FLEX is never either).
        apply_lineup_tints(
            client,
            TAB,
            header_row=layout.header_rows[0],
            name_blocks=_runs(layout.player_rows),
            header_names={"Pos.": "Slot", "Opp.": "Opp"},
        )
        # "Your pick": added after the tints, so it is the rule that wins on a name Sam typed in
        for first, last in _runs(layout.player_rows):
            client.add_boolean_rule(
                TAB,
                f"{name}{first}:{name}{last}",
                condition_type="CUSTOM_FORMULA",
                values=[f"=${YOURS_COLUMN_LETTER}{first}=TRUE"],
                fmt={"backgroundColor": YOUR_PICK_TINT},
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
    line = (
        f"Build {build.number}  {build.kind.upper():<4} ${build.salary:,}  {build.projection:.1f} pts  "
        f"Cash {build.p_cash:.0%}  Top 1% {build.p_gpp:.1%}  {build.title}"
    )
    if build.e_dups is None or build.own_sum is None or build.own_product is None:
        return line
    own = (
        f"own sum {build.own_sum:.0%} product {build.own_product:.1e}  "
        f"~{build.e_dups:.2g} copies in {build.entries:,}"
    )
    share = f"  Top 1% share {build.objective:.2%}" if build.objective is not None else ""
    return f"{line}\n    {own}{share}"


def build_lines(build: Build, edge: pd.DataFrame) -> list[str]:
    """Nine terminal lines for a build (slot, name, team v opponent, salary, projection, kickoff); a locked
    player is marked with a lock, a typed-in one with a star."""
    details = _details(edge)
    return [
        f"{slot:<5}{player.name:<24}{player.team:>4} v {player.opp:<4} ${player.salary:>6,}  "
        f"{player.value:>5.1f}  {details.get(player.name, ('', ''))[0]}"
        + ("  [locked]" if player.name in build.locked else "")
        + (" [your pick]" if player.name in build.yours else "")
        for slot, player in zip(ROSTER_SLOTS, build.players, strict=True)
    ]


def ensure_suggested_tab(client: SheetsClient) -> str:
    """`dfs setup build-views`: create the tab empty (title, how-to, the rules box and the "no builds yet"
    line) when it does not exist; never touch one that does (it holds the last run's builds)."""
    if client.tab_exists(TAB):
        return f"{TAB}: already present -- left as the last run wrote it"
    layout = build_layout(
        SuggestReport(), pd.DataFrame({"Name": []}), metric="CalPts", cash_line=0.0, gpp_target=0.0
    )
    client.write_tab(TAB, layout.rows)
    return f"{TAB}: created (empty)"


def setup_controls(client: SheetsClient) -> list[str]:
    """`dfs setup suggested-controls` (idempotent): the Suggested tab's typed controls and its protection.

    - A tab with no builds (a fresh template, or a new week's copy) is rewritten empty with the rules box,
      styled (the pale-yellow typed cells), keeping whatever rules were typed in it. A tab that holds builds
      is left as the last run wrote it: the lock ticks, decision cells and "your pick" tint arrive with the
      next `dfs lineups suggest` run (the rows move with every run, so they are written then, never here).
    - Its warning-only protection is replaced by one that carves out the typed columns (`typed_ranges`: the
      lock ticks and the Name column with the decision cells, the rules box and the names Sam types over)."""
    results = []
    if client.tab_exists(TAB):
        state = read_state(client)
        if state.builds:
            results.append(
                f"{TAB}: holds {len(state.builds)} build(s): left as the last run wrote it (the next "
                "`dfs lineups suggest` writes the controls beside them)"
            )
        else:
            write_suggested(
                client,
                SuggestReport(rules=state.rules.by_label()),
                pd.DataFrame({"Name": []}),
                metric="CalPts",
                cash_line=0.0,
                gpp_target=0.0,
            )
            results.append(f"{TAB}: empty tab rewritten with the rules box (rules typed there kept)")
    else:
        write_suggested(
            client,
            SuggestReport(),
            pd.DataFrame({"Name": []}),
            metric="CalPts",
            cash_line=0.0,
            gpp_target=0.0,
        )
        results.append(f"{TAB}: created with the rules box")
    client.clear_protected_ranges(TAB)
    client.protect_sheet(TAB, unprotected_ranges=typed_ranges(), description=PROTECTION_DESCRIPTION)
    results.append(f"{TAB}: protected (warning-only) except {' and '.join(typed_ranges())}")
    return results


PROTECTION_DESCRIPTION = (
    "Written by dfs lineups suggest -- check before typing here (except the lock ticks, the Name column with "
    "the decision cells and rules box)"
)
