"""Generates the Instructions tab from code (Week 3 fixes, Fix 4), rewritten as Sam's playbook in the
usability round (slice 7).

The tab is for the person using the sheet, not for a developer: the week step by step (which tab, what to look
at, what to do, which command), one plain line per number or chip, one line per tab. No backticks, no source
names, no function names; the developer reference stays in `docs/`.

**What this fixes, and what it does not.** Every fact below that already has a named constant elsewhere is
derived from it, not retyped: Player Pool's per-position caps (`weekly_reset.PLAYER_POOL_NAME_BLOCKS`),
Lineups' roster slots and block count (`models.ROSTER_SLOTS`, `weekly_reset.LINEUPS_NAME_BLOCKS`), the pool
tag-group order (`sources.edge.POOL_TYPE_SORT_ORDER`), the Set dropdown's options
(`sheet_pool_cells.SET_OPTIONS`) and the GPP target default. The surrounding English is still hand-written
and can go stale the way any comment can; a generated row does not generate the facts inside it.

The tab's layout is `_LAYOUT`, a flat list of title / section-band / text rows. `render_instructions_grid`
turns it into row -> (column A, column B), the one source of truth `build_instructions_tab` (the writer) and
`dfs doctor`'s drift check both use. The writer clears the tab's old content first (the row count is no
longer fixed), then writes every row and styles the section bands, the wrapped text and the column widths.
Idempotent.
"""

from __future__ import annotations

from dfs.config import SIM_GPP_TARGET_DEFAULT
from dfs.models import ROSTER_SLOTS
from dfs.sheet_pool_cells import SET_OPTIONS
from dfs.sheets import SheetsClient
from dfs.sources.edge import POOL_TYPE_SORT_ORDER
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS

INSTRUCTIONS_TAB = "Instructions"

# Per-position pool caps (Player Pool's own block sizes), in PLAYER_POOL_NAME_BLOCKS' own QB/RB/WR/TE/DST
# order.
_POOL_POSITIONS = ("QB", "RB", "WR", "TE", "DST")
# Rows to clear below the content, so a longer previous version leaves nothing behind.
_CLEAR_TO_ROW = 120
COLUMN_WIDTHS = {"A": 230, "B": 980}
# Row heights are set from the text length because a wrapped row does not reliably auto-fit when written
# through the API: about this many characters fit on one line of column B at 10 pt, and one line is this
# many pixels.
_CHARS_PER_LINE = 150
_LINE_PX = 16
_ROW_PAD_PX = 8


def _pool_caps_text() -> str:
    caps = [
        f"{pos} {end - start + 1}"
        for pos, (start, end) in zip(_POOL_POSITIONS, PLAYER_POOL_NAME_BLOCKS, strict=True)
    ]
    return ", ".join(caps)


def _roster_slots_text() -> str:
    return ", ".join(ROSTER_SLOTS)


def _tag_order_text() -> str:
    return ", then ".join(POOL_TYPE_SORT_ORDER[:-1]) + f", then {POOL_TYPE_SORT_ORDER[-1]}"


def _set_options_text() -> str:
    return ", ".join(SET_OPTIONS[:-1]) + f" or {SET_OPTIONS[-1]}"


_TITLE = "How to use this sheet"

_ONE_THING_ROWS: list[tuple[str, str]] = [
    (
        "What you type",
        "Only a few cells are ever typed, and the pale yellow ones are the cue: the Set dropdown on the Edge "
        f"Finder and the Board ({_set_options_text()}), the Pool dropdown on EdgeRaw, a name in Player "
        f"Pool's "
        "top row, the names in Lineups column A (plain white, so the stack colours show), the Cash or GPP "
        "marker on each lineup's Total row, and a percentage in Exposure's Target column. Everything else is "
        "a formula or written by the sync: if a cell is not one of those, do not type in it. Most tabs warn "
        "you before you can; the warning is a reminder, not a lock.",
    ),
    (
        "The one thing to run first",
        "Nothing fills in until you have run dfs sync once this week. The sheet is fed by a small program on "
        "your computer called dfs; it is not a Google add-on and nothing updates on its own. Typing just dfs "
        "with nothing after it shows where you are in the week and a menu of the standard week, and asks for "
        "anything it needs.",
    ),
    (
        "The Set dropdown",
        "Set on the Edge Finder and the Board adds a player to your pool as Cash, GPP or Both, and "
        "Remove takes "
        "him out. It needs the one-time Lineup Tools script pasted into the sheet (Extensions, then Apps "
        "Script; the dfs doctor check says when it is missing). Until then, use the Pool dropdown on "
        "EdgeRaw, "
        "which does the same thing.",
    ),
]

_WEEK_ROWS: list[tuple[str, str]] = [
    (
        "Start of week",
        'File, then Make a copy of the template, and run: dfs week new "<link to the copy>". It checks the '
        "copy, titles it Week <n>, carries your bankroll and Results log forward, clears last week's lineups "
        "and runs the first full sync. It asks you to confirm before it writes anything.",
    ),
    (
        "Tue / Wed: judge, then build the pool",
        "1) Model Check: read the bold sentence that opens each block. The projection race says which "
        "projection to trust (it only says clearly best at a real gap); the signals line says whether the "
        "chips are earning their place. 2) Edge Finder: the first pass. Cash core and GPP upside list "
        "the best "
        "plays by position, with Why and Do saying what to make of each; add players with Set. Command to "
        "refresh any time: dfs sync.",
    ),
    (
        "Thu - Sat: injuries and pool changes",
        "Re-run dfs sync as news arrives. The Board is where to look: the Queue lists what changed since the "
        "last sync for players in your pool (a status move, wind, a line move, a salary change), Pool check "
        "lists pool players with a problem (out or doubtful, a high Bust%, a CalPts well under the "
        "projection, "
        "a FADE chip), and Pool summary shows how many you have per position and where you are short of "
        "what a "
        "lineup needs. Fix them with Set, or Remove. Movement shows how each team's line has moved.",
    ),
    (
        "Sunday: final sync, then lineups",
        "About 90 minutes before the first kickoff, after inactives, run: dfs sync --live. Then build "
        "lineups "
        "on the Lineups tab: type names in column A, set Cash or GPP on the Total row, and read P(cash) and "
        f"P({SIM_GPP_TARGET_DEFAULT:.0f}+) there (see Reading the numbers). Exposure shows how concentrated "
        "your lineups are. When they are final, pair them to your contest entries in DK Upload, run: dfs "
        "export, and upload the file to DraftKings.",
    ),
    (
        "Sunday: late swap",
        "Between kickoffs run: dfs sync --live, then: dfs lineups late-swap. It checks every lineup against "
        "real kickoff times, shows who is still swappable and suggests the best swaps with the change in "
        "P(cash) and P(GPP) for each. It aims at what the Total row's Cash or GPP marker says.",
    ),
    (
        "Mon / Tue: close the week",
        "Export your contest history from DraftKings and run: dfs week close --csv <file>. It "
        "reconciles Cash "
        "and GPP results into Bankroll and Season and scores the week's projections into Model Check. If "
        "the stats are not posted yet it says so; run dfs results update a day later.",
    ),
]

_NUMBER_ROWS: list[tuple[str, str]] = [
    (
        "CalPts",
        "Our corrected projection: the sources blended after removing the bias each is known to have "
        "for that "
        "position and salary tier. It fixes the level (TFFB runs high) and ranks players about the same as "
        "TFFB. It is not the default projection: Model Check's race says whether it is earning that.",
    ),
    (
        "Hit3x%",
        "The chance he scores at least 3 points per $1,000 of salary (a $6,000 player: 18 points). That is "
        "the usual cash-game line. Higher is better for cash.",
    ),
    (
        "Boom%",
        "The chance he scores at least 4 points per $1,000 (a $6,000 player: 24 points). The tournament "
        "number. Higher is better for GPP.",
    ),
    (
        "Bust%",
        "The chance he scores under 2 points per $1,000 (a $6,000 player: 12 points). Lower is safer. It is "
        "the one number where a low value is the good one.",
    ),
    (
        "CeilM",
        "The model's ceiling for him: the score he reaches or beats about one time in seven. It sits beside "
        "the projection source's own Ceil.",
    ),
    (
        "ValAdj",
        "A 0 to 100 value score: how high he projects among his position, blended with how many points he "
        "projects beyond what his salary normally buys. Compared only with players at the same position, "
        "so a quarterback is only ever measured against quarterbacks.",
    ),
    (
        "P(cash)",
        "On a lineup's Total row: the chance the lineup reaches the cash line. The line is the median of "
        "your last three typed Cash Lines in Results. Green at 50% or more. Median is the typical score and "
        "p90 is the great-night score.",
    ),
    (
        f"P({SIM_GPP_TARGET_DEFAULT:.0f}+)",
        f"On a lineup's Total row: the chance the lineup reaches the GPP target "
        f"({SIM_GPP_TARGET_DEFAULT:.0f} "
        "points unless you change it in the config). Green at 5% or more. The Cash or GPP marker highlights "
        "the number that matters for that lineup.",
    ),
    (
        "The chips",
        "A coloured tag marks a state, never a number. OUT and IR are red, Q is amber. INJ+ means a "
        "teammate is out and more work should come his way. USAGE up or down means his carry share has "
        "moved. "
        "FADE is a tight end the numbers lean against. Proj down means that, in past seasons, players in "
        "this exact usage spot scored under their projection; Proj up means over. A grey Proj with a "
        "question "
        "mark rests on weaker evidence. The Why on the Edge Finder names each signal and how big the "
        "measured "
        "effect was. All of them are context, not proof: Model Check scores every one.",
    ),
    (
        "The arrows (trends)",
        "On the Edge Finder's usage trends: the last three games against the six before them. An up or down "
        "arrow shows only when the move is bigger than what has been measured as meaningful for his "
        "position, "
        "and only for a player with at least nine earlier games. No arrow means a normal wobble or too "
        "little "
        "history, not that nothing happened.",
    ),
    (
        "The colours",
        "A dark band is a table header. Pale yellow is where you type. Green or red on a number means better "
        "or worse compared with others at the same position, never a fixed cut-off. Ownership shades amber "
        "and red because high ownership is a caution. Grey text means no data yet (ownership shows "
        "blank until "
        "it is published). A bold player name means at least one flag; bold name = at least one flag.",
    ),
]

_TAB_ROWS: list[tuple[str, str]] = [
    ("Instructions", "This tab."),
    (
        "Slate Grid",
        "One row per game with kickoff, total, spread, wind and roof, then every team by implied total with "
        "pace and offensive and defensive strength. Read-only; use it to find the games worth stacking.",
    ),
    (
        "Board",
        "Your landing tab, nothing typed except Set. Slate shape ranks the games; the Queue is what changed "
        "for your pool since the last sync; Pool check flags pool players with a problem; Pool summary "
        "counts "
        "your pool by position; Stack candidates lists the best QB with his pass catchers in the best games.",
    ),
    (
        "Edge Finder",
        "Written by the sync. Per position, the best cash plays and best GPP plays, where CalPts disagrees "
        "with TFFB, who benefits from an injury, matchups and the usage trends and signals, each with a Why "
        "and a Do. Pool shows your pick, Set changes it and the arrow jumps to the player on EdgeRaw. "
        "Overflow lists are folded under a plus sign.",
    ),
    (
        "EdgeRaw",
        "Every player of the week sorted by ValAdj, with the Pool dropdown to add him. Sort or search from "
        "the column headers; saved filter views under Data, then Filter views, give Cash and GPP orders.",
    ),
    (
        "Player Pool",
        "Everyone you added, grouped by position, tagged by Cash, GPP or Both and sorted by salary within a "
        f"tag ({_tag_order_text()}). Caps: {_pool_caps_text()}; a warning appears if you add more than "
        "a position has room for. Type a name in the top row to add one directly. The rest is computed.",
    ),
    (
        "Lineups",
        f"Type names in column A, starting at row {LINEUPS_NAME_BLOCKS[0][0]}. Each lineup is one "
        f"{len(ROSTER_SLOTS)}-player roster block ({_roster_slots_text()}); there are "
        f"{len(LINEUPS_NAME_BLOCKS)} blocks total. The Issues column flags a duplicate, an unavailable "
        "player, a salary problem or an incomplete lineup. The Total row holds Median, p90, P(cash) and "
        "P(GPP) and the Cash or GPP marker you set; the portfolio line is on the Board.",
    ),
    (
        "DK Upload",
        "Pair a finished lineup to a real contest entry (Entry ID, contest, fee), then run dfs export and "
        "upload the file it writes to DraftKings.",
    ),
    (
        "Exposure",
        "How many of your lineups use each player. Type a target percentage in Target and the next column "
        "shows how far over or under you are.",
    ),
    (
        "Movement",
        "One row per team, largest move first: the implied total now, how it moved, total and spread moves, "
        "kickoff in Eastern time, your team's top players and a sentence on what the move means. Read-only.",
    ),
    (
        "Bankroll",
        "Cash, GPP and betting ledgers with starting and ending bankroll. Betting is typed by hand; Cash "
        "and GPP rows are added by dfs week close.",
    ),
    (
        "Season",
        "The year at a glance: one row per week, year-to-date by bucket and a chart. Carried forward "
        "each week.",
    ),
    (
        "Results",
        "One row per week: cash points and cash line, head-to-head entered and won. You type it; it is "
        "never reset. The cash line you type here feeds P(cash).",
    ),
    (
        "Model Check",
        "Every projection scored against what happened, rebuilt by dfs results update. Each block opens with "
        "one bold sentence saying what the numbers show. Muted italic rows have too few players to say "
        "anything yet.",
    ),
    (
        "SoSComb",
        "Strength of schedule by team and position in one lookup. It feeds each player's opponent rank. "
        "The five tabs behind it are hidden; unhide one from the tab bar if you want the raw numbers.",
    ),
    (
        "PlayerPoolRaw",
        "The hub the other tabs read from, one row per player. Left visible so a broken lookup is easy to "
        "find. Nothing to do here.",
    ),
    (
        "Hidden tabs",
        "The raw feeds the sync fills (projections, DraftKings salaries, odds, games, weather, pace) and the "
        "team-level tables behind them. They are hidden on purpose; you never need to open them.",
    ),
]

_MORE_ROWS: list[tuple[str, str]] = [
    (
        "On your computer",
        "In the dfs folder, the docs folder has the detail: WORKFLOW (the weekly grind with exact commands), "
        "TROUBLESHOOTING (symptom, cause, fix), SHEET_REFERENCE (every column), CALCULATIONS (every formula) "
        "and COMMANDS (every command).",
    ),
]

# (kind, column A, column B): "title", "section" (a dark band) or "row".
_LAYOUT: list[tuple[str, str, str]] = [
    ("title", _TITLE, ""),
    ("section", "READ THIS FIRST", ""),
    *(("row", a, b) for a, b in _ONE_THING_ROWS),
    ("section", "THE WEEK, STEP BY STEP", "Tab, what to look at, what to do, and the command"),
    *(("row", a, b) for a, b in _WEEK_ROWS),
    ("section", "READING THE NUMBERS", "One plain line each"),
    *(("row", a, b) for a, b in _NUMBER_ROWS),
    ("section", "THE TABS", "What each one is for"),
    *(("row", a, b) for a, b in _TAB_ROWS),
    ("section", "MORE DETAIL", ""),
    *(("row", a, b) for a, b in _MORE_ROWS),
]

INSTRUCTIONS_LAST_ROW = len(_LAYOUT)


def render_instructions_grid() -> dict[int, tuple[str, str]]:
    """Row number -> (column A, column B) for every row this tab generates -- the one shared source of truth
    `build_instructions_tab` (the writer) and `dfs doctor`'s drift check both build on, so the two can never
    disagree about what "correct" looks like."""
    return {number: (a, b) for number, (_kind, a, b) in enumerate(_LAYOUT, start=1)}


def _section_rows() -> list[int]:
    return [number for number, (kind, _a, _b) in enumerate(_LAYOUT, start=1) if kind == "section"]


def build_instructions_tab(client: SheetsClient, tab: str = INSTRUCTIONS_TAB) -> str:
    """Clears the tab's old content, writes every row of `render_instructions_grid` and styles it (section
    bands, wrapped top-aligned text, column widths). Never touches another tab."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    from dfs.sheet_style import _HEADER_FMT, _TITLE_FMT, WHITE

    grid = render_instructions_grid()
    client.clear_ranges(tab, [f"A1:B{_CLEAR_TO_ROW}"])
    client.update_range(tab, f"A1:B{len(grid)}", [list(grid[number]) for number in range(1, len(grid) + 1)])
    client.set_column_widths(tab, COLUMN_WIDTHS)
    client.format_range(
        tab,
        f"A1:B{_CLEAR_TO_ROW}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "fontSize": 10},
            "wrapStrategy": "WRAP",
            "verticalAlignment": "TOP",
        },
    )
    client.format_range(tab, "A1", {**_TITLE_FMT, "wrapStrategy": "OVERFLOW_CELL"})
    for number in _section_rows():
        client.format_range(tab, f"A{number}:B{number}", _HEADER_FMT)
    for number, (kind, label, text) in enumerate(_LAYOUT, start=1):
        if kind == "row":
            client.format_range(tab, f"A{number}", {"textFormat": {"bold": True, "fontSize": 10}})
        lines = max(-(-len(text) // _CHARS_PER_LINE), -(-len(label) // 30), 1)
        client.set_row_heights(
            tab, start_row=number, end_row=number, pixel_size=lines * _LINE_PX + _ROW_PAD_PX
        )
    return f"{tab}: {len(grid)} row(s) written"
