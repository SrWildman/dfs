"""The `Stacks` tab as rows plus formatting instructions (`build_layout` is pure; `sheet_stacks` reads the
inputs and writes it).

Read-only and rewritten whole, like the Cockpit: nothing on it is typed and it holds no formula. Column A is
the cue (`BUILD`: the stack takes a bigger share of the simulated top 1% than of the field, so it is worth
typing into Suggested's rules box); the last column holds the line to type there.

Two things change what the tab shows, and it says which:

- **Ownership not published yet** (TFFB's Own% is blank until midweek): the field is generated from a stand-in
  (`stacks_inputs.stand_in_ownership`), so the field rate and leverage would be the stand-in's, not the
  field's. Those columns read `-`, there are no cues, and the list is sorted by the simulated Top 1%.
- **The leverage signal is untested** (the retro finds it no better than stacks picked at random, or it has
  not been run): the header says "untested" and the list is sorted by boom share (the stack's share of the
  simulated top 1%) instead of leverage. A weak signal (above the baseline in most weeks, not yet beyond
  chance) keeps the leverage order and says "weak signal" in the header.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from dfs import stacks
from dfs.cockpit import clock

STACKS_TAB = "Stacks"
COLUMN_COUNT = 13
CUE_BUILD = "BUILD"
DASH = "-"

STACK_HEADERS = [
    "QB",
    "Teammates",
    "Game",
    "Salary",
    "Proj",
    "Top 1%",
    "Lift",
    "Field %",
    "Boom %",
    "Lev",
    "BB edge",
    "Type in the rules box",
]
GAME_HEADERS = ["Game", "Total", "Pace", "Spread", "Stack %", "Game stack %", "Best Lev"]

TESTED, WEAK, UNTESTED = "tested", "weak", "untested"  # = sim.stacks_retro's VERDICT_*
PCT, LIFT, MONEY, POINTS, SIGNED = "0.0%", '0.0"x"', "$#,##0", "0.0", "+0.0%;-0.0%;0.0%"


@dataclass
class StackTabInputs:
    result: stacks.StackResult
    sheet_title: str
    now: datetime
    week: int | None = None
    ownership_published: bool = True
    field_weeks: tuple[int, ...] = ()
    retro_line: str = ""  # the track-record sentence (`sim.stacks_retro.track_record_line`)
    verdict: str | None = None  # the retro's verdict (`sim.stacks_retro.verdict`); None: not run
    unavailable: str = ""  # why the tab could not be built, when it could not


@dataclass
class Layout:
    rows: list[list]
    title_row: int = 1
    status_row: int = 2
    section_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    cue_rows: dict[int, str] = field(default_factory=dict)
    formats: list[tuple[str, str, str]] = field(default_factory=list)  # (range, pattern, type)
    sort_label: str = ""


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])

    @property
    def next_row(self) -> int:
        return len(self.layout.rows) + 1

    def add(self, values: list) -> int:
        row = [("" if isinstance(v, float) and v != v else v) for v in values]
        self.layout.rows.append(row + [""] * (COLUMN_COUNT - len(row)))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def section(self, title: str) -> None:
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def header(self, *labels: str) -> None:
        self.layout.header_rows.append(self.add(["", *labels]))

    def note(self, text: str) -> None:
        self.layout.note_rows.append(self.add(["", text]))


def rule_text(qb: str, mates: str) -> str:
    """What to type on Suggested's `Always use` line to build the stack (full names resolve on their own)."""
    return f"Always use: {qb}, {mates}"


def sorted_table(table: pd.DataFrame, *, published: bool, verdict: str | None) -> tuple[pd.DataFrame, str]:
    """The stacks in the order the tab shows them, and the sentence naming it."""
    if table.empty:
        return table, ""
    if not published:
        return table.sort_values(
            "top1", ascending=False, kind="stable"
        ), "simulated Top 1% (no ownership yet)"
    if verdict in (TESTED, WEAK):
        return table.sort_values(["lev", "top1"], ascending=False, kind="stable"), "leverage"
    return table.sort_values(["boom_share", "top1"], ascending=False, kind="stable"), "boom share"


def _cue(row, *, published: bool, verdict: str | None) -> str:
    if published and verdict in (TESTED, WEAK) and row["lev"] > 0 and row["lift"] > 1.0:
        return CUE_BUILD
    return ""


def build_layout(inputs: StackTabInputs) -> Layout:
    """The whole tab. Pure."""
    b = _Builder()
    res = inputs.result
    week = f"  Week {inputs.week}" if inputs.week else ""
    b.add([f"STACKS{week}  —  QB stacks by simulated upside against how much of the field plays them"])
    meta = res.meta
    own = (
        "ownership: published"
        if inputs.ownership_published
        else "ownership NOT published yet: field % and leverage wait for it"
    )
    weeks = (
        f"  ·  field fitted on weeks {', '.join(map(str, inputs.field_weeks))}" if inputs.field_weeks else ""
    )
    b.add(
        [
            f"{inputs.sheet_title}  ·  built {clock(inputs.now)} ET  ·  {own}{weeks}  ·  "
            f"{meta.get('n_sims', 0):,} simulated draws  ·  read-only"
        ]
    )
    if inputs.unavailable:
        b.blank()
        b.note(f"Not built: {inputs.unavailable}")
        return b.layout
    if inputs.retro_line:
        b.note(inputs.retro_line)
    else:
        b.note(
            "Track record: the retro has not been run (`dfs stacks --retro`); leverage is an untested signal."
        )
    published = inputs.ownership_published
    table, sort_label = sorted_table(res.table, published=published, verdict=inputs.verdict)
    b.layout.sort_label = sort_label
    lev_label = {TESTED: "Lev", WEAK: "Lev (weak signal)"}.get(inputs.verdict or "", "Lev (untested)")

    b.section(f"TOP {stacks.STACK_ROWS} QB STACKS  —  sorted by {sort_label}")
    b.header(*[lev_label if h == "Lev" else h for h in STACK_HEADERS])
    first = b.next_row
    for _, r in table.head(stacks.STACK_ROWS).iterrows():
        cue = _cue(r, published=published, verdict=inputs.verdict)
        game = f"{r['team']} v {r['opp']}"
        row = b.add(
            [
                cue,
                r["qb"],
                r["mates"],
                game,
                int(r["salary"]),
                float(r["proj"]),
                float(r["top1"]),
                float(r["lift"]),
                float(r["field"]) if published else DASH,
                float(r["boom_share"]) if published else DASH,
                float(r["lev"]) if published else DASH,
                float(r["bb_edge"]),
                rule_text(r["qb"], r["mates"]),
            ]
        )
        if cue:
            b.layout.cue_rows[row] = cue
    last = b.next_row - 1
    if last >= first:
        for col, pattern, kind in (
            ("E", MONEY, "CURRENCY"),
            ("F", POINTS, "NUMBER"),
            ("G", PCT, "PERCENT"),
            ("H", LIFT, "NUMBER"),
            ("I", PCT, "PERCENT"),
            ("J", PCT, "PERCENT"),
            ("K", SIGNED, "PERCENT"),
            ("L", SIGNED, "PERCENT"),
        ):
            b.layout.formats.append((f"{col}{first}:{col}{last}", pattern, kind))
    b.note(
        "Top 1% = chance a lineup built on the stack, the rest filled the way the field fills it, finishes "
        "in the field's top 1% (1% is an average lineup; Lift is Top 1% over 1%). Field % = share of the "
        "generated field playing exactly this stack. Boom % = share of the simulated top 1% that plays it. "
        "Lev = Boom % - Field %, in points of the field. BB edge = Top 1% with a bring-back minus without."
    )
    if not published:
        b.note("Field %, Boom % and Lev appear once TFFB publishes ownership (the next sync fills them).")

    b.section(f"TOP {stacks.GAME_ROWS} GAMES  —  by combined total")
    b.header(*GAME_HEADERS)
    games = res.games
    first = b.next_row
    if not games.empty:
        for _, g in games.head(stacks.GAME_ROWS).iterrows():
            b.add(
                [
                    "",
                    g["game"],
                    g.get("total", np.nan),
                    g.get("pace", np.nan),
                    g.get("spread", np.nan),
                    float(g["stack_rate"]) if published else DASH,
                    float(g["game_stack_rate"]) if published else DASH,
                    float(g["best_lev"]) if published else DASH,
                ]
            )
        last = b.next_row - 1
        for col, pattern, kind in (
            ("C", POINTS, "NUMBER"),
            ("D", POINTS, "NUMBER"),
            ("E", POINTS, "NUMBER"),
            ("F", PCT, "PERCENT"),
            ("G", PCT, "PERCENT"),
            ("H", SIGNED, "PERCENT"),
        ):
            b.layout.formats.append((f"{col}{first}:{col}{last}", pattern, kind))
    b.note(
        "Stack % = share of the field that stacks either QB of the game; Game stack % = a stack plus a "
        "bring-back from the same game. Best Lev = the game's best stack above."
    )
    b.blank()
    b.note(
        "Rebuilt by `dfs stacks` and `dfs sync` (where this tab exists); nothing here is typed. To build "
        "one: type its last cell into Suggested's rules box (names after `Always use`), then run "
        "`dfs lineups suggest --iterate`."
    )
    return b.layout


def _cell_text(value, pattern: str | None) -> str:
    if value == "" or value is None:
        return ""
    if isinstance(value, str):
        return value
    v = float(value)
    if pattern == PCT:
        return f"{v:.1%}"
    if pattern == SIGNED:
        return f"{v * 100:+.1f}%"
    if pattern == LIFT:
        return f"{v:.1f}x"
    if pattern == MONEY:
        return f"${v:,.0f}"
    return f"{v:.1f}"


def render_text(layout: Layout, width: int = 130) -> str:
    """The tab as plain text for the terminal (one line per row; table rows column-aligned, long note rows
    left as they are)."""
    pattern_at: dict[tuple[int, int], str] = {}
    for rng, pattern, _kind in layout.formats:
        first, last = rng.split(":")
        col = ord(first[0]) - ord("A")
        for row in range(int(first[1:]), int(last[1:]) + 1):
            pattern_at[(row, col)] = pattern
    out = []
    table_rows = {
        r for r in range(1, len(layout.rows) + 1) if any(p for (row, _), p in pattern_at.items() if row == r)
    } | set(layout.header_rows)
    widths = [0] * COLUMN_COUNT
    cells: dict[int, list[str]] = {}
    for i, row in enumerate(layout.rows, start=1):
        if i in table_rows:
            cells[i] = [_cell_text(v, pattern_at.get((i, c))) for c, v in enumerate(row)]
            for c, t in enumerate(cells[i]):
                if c != COLUMN_COUNT - 1:
                    widths[c] = max(widths[c], len(t))
    for i, row in enumerate(layout.rows, start=1):
        if i in cells:
            out.append("  ".join(t.ljust(widths[c]) for c, t in enumerate(cells[i])).rstrip())
        else:
            text = " ".join(str(v) for v in row if v != "")
            out.append(text if i not in layout.section_rows else f"\n{text}")
    return "\n".join(out)
