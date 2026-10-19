"""Generates the Instructions tab from code (Week 3 fixes, Fix 4), rewritten as Sam's playbook in the
usability round (slice 7).

The tab is for the person using the sheet, not for a developer: the week step by step (which tab, what to look
at, what to do, which command), one plain line per number or chip, one line per tab. No backticks, no source
names, no function names; the developer reference stays in `docs/`.

**What this fixes, and what it does not.** Every fact below that already has a named constant elsewhere is
derived from it, not retyped: Player Pool's per-position caps (`weekly_reset.PLAYER_POOL_NAME_BLOCKS`),
Lineups' roster slots and block count (`models.ROSTER_SLOTS`, `weekly_reset.LINEUPS_NAME_BLOCKS`), the pool
tag-group order (`sources.edge.POOL_TYPE_SORT_ORDER`), the Pool dropdown's options
(`sheet_pool_cells.POOL_OPTIONS`) and the simulator's Top 1% label and green lines
(`sheet_lineup_sim`). The surrounding English is still hand-written
and can go stale the way any comment can; a generated row does not generate the facts inside it.

The tab's layout is `_LAYOUT`, a flat list of title / section-band / text rows. `render_instructions_grid`
turns it into row -> (column A, column B), the one source of truth `build_instructions_tab` (the writer) and
`dfs doctor`'s drift check both use. The writer clears the tab's old content first (the row count is no
longer fixed), then writes every row and styles the section bands, the wrapped text and the column widths.
Idempotent.
"""

from __future__ import annotations

from dfs.models import ROSTER_SLOTS
from dfs.sheet_lineup_sim import P_CASH_GREEN, P_GPP_GREEN, TOP1_HEADER
from dfs.sheet_pool_cells import POOL_OPTIONS
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


def _pool_options_text() -> str:
    return ", ".join(POOL_OPTIONS[:-1]) + f" or {POOL_OPTIONS[-1]}"


_TITLE = "How to use this sheet"

_ONE_THING_ROWS: list[tuple[str, str]] = [
    (
        "What you type",
        "Only a few cells are ever typed, and the pale yellow ones are the cue: the Pool dropdown at the "
        f"left of every player on the Edge Finder, the Board and Player Pool ({_pool_options_text()}; clear "
        "it to take him out), the Pool dropdown on EdgeRaw, the add-a-player box in Player Pool's top row, "
        "the names in Lineups column A (plain white, so the stack colours show), the Cash or GPP marker on "
        "each lineup's Total row, and a percentage in Exposure's Target column. Everything else is a formula "
        "or written by the sync: if a cell is not one of those, do not type in it. Most tabs warn you "
        "before you can; the warning is a reminder, not a lock.",
    ),
    (
        "The one thing to run first",
        "Nothing fills in until you have run dfs sync once this week. The sheet is fed by a small program on "
        "your computer called dfs; it is not a Google add-on and nothing updates on its own. Typing just dfs "
        "with nothing after it shows where you are in the week and a menu of the standard week, and asks for "
        "anything it needs.",
    ),
    (
        "The Pool dropdown",
        "The Pool cell at the left of every player on the Edge Finder, the Board and Player Pool shows "
        "whether he is in your pool and is the control: pick Cash, GPP or Both to add him, or press Delete "
        "to take him out. It needs the one-time Lineup Tools script pasted into the sheet (Extensions, then "
        "Apps Script; the dfs doctor check says when it is missing). Until then, use the Pool dropdown on "
        "EdgeRaw, which does the same thing.",
    ),
]

_MODEL_ROWS: list[tuple[str, str]] = [
    (
        "CalPts",
        "Our projection: TFFB corrected for its known biases and blended with Sleeper and FantasyPros. "
        "Better than TFFB at RB and WR so far.",
    ),
    (
        "Cash",
        f"Pool from Top plays, then Cash. Build lineups with a Cash % of {P_CASH_GREEN:.0%} or more.",
    ),
    (
        "GPP",
        "Pool from Top plays, then GPP. Look for a Lev of +25 or more. Build with a stack and check the "
        f"{TOP1_HEADER}.",
    ),
    (
        "Proj ▼ / Proj ▲",
        "The projection is probably a bit high (▼) or a bit low (▲). Use it as a tiebreaker only.",
    ),
    ("Suggested", "Complete builds from your pool. Copy one in with Lineup Tools and tweak it."),
    (
        "Gameday",
        "Final sync after the inactives, then Lineup Tools, Fix FLEX order, then late swap before the 4 pm "
        "games.",
    ),
]

_WEEK_ROWS: list[tuple[str, str]] = [
    (
        "Start of week",
        'File, then Make a copy of the template, and run: dfs week new "<link to the copy>". It checks the '
        "copy, titles it Week <n>, carries your bankroll and Results log forward, clears last week's "
        "lineups, rebuilds the STAKES block on Bankroll and runs the first full sync. It asks you to "
        "confirm before it writes anything.",
    ),
    (
        "Tue / Wed: judge, then build the pool",
        "1) Model Check: read the bold sentence that opens each block. The projection race says which "
        "projection to trust (it only says clearly best at a real gap); the signals line says whether the "
        "chips are earning their place. 2) Edge Finder: the first pass. Cash core and GPP upside list "
        "the best plays by position, with Why and Do saying what to make of each; add players with the Pool "
        "dropdown. Command to refresh any time: dfs sync.",
    ),
    (
        "Thu - Sat: injuries and pool changes",
        "Re-run dfs sync as news arrives. The Board is where to look: the Queue lists what changed since the "
        "last sync for players in your pool (a status move, wind, a line move, a salary change), Pool check "
        "lists pool players with a problem (out or doubtful, a high Bust%, a CalPts well under the "
        "projection, "
        "a FADE chip), and Pool summary shows how many you have per position and where you are short of "
        "what a lineup needs. Fix them with the Pool dropdown (clear it to remove). Movement shows how each "
        "team's line has moved.",
    ),
    (
        "Sunday: final sync, then lineups",
        "About 90 minutes before the first kickoff, after inactives, run: dfs sync --live. Then build "
        "lineups "
        "on the Lineups tab: type names in column A, set Cash or GPP on the Total row, and read Cash % and "
        f"{TOP1_HEADER} there (see Reading the numbers). Exposure shows how concentrated "
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
        "Export your contest history from DraftKings, and download each contest's standings (DraftKings only "
        "keeps them about two weeks). Then run: dfs week close. It finds the newest history export in "
        "Downloads (or pass --csv <file>), reconciles Cash and GPP results into Bankroll and Season, keeps "
        "your downloads, fills a blank Cash Line in Results, rebuilds Contests and scores the week's "
        "projections into Model Check. If the stats are not posted yet it says so; run dfs results update a "
        "day later.",
    ),
]

_NUMBER_ROWS: list[tuple[str, str]] = [
    (
        "CalPts",
        "Our corrected projection: the sources blended after removing the bias each is known to have "
        "for that "
        "position and salary tier. It fixes the level (TFFB runs high) and ranks players about the same as "
        "TFFB. It is the projection late swap, the lineup odds and Suggested use; TFFB stays visible.",
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
        "Cash %",
        "On a lineup's Total row: the chance the lineup beats the double-up cash line, which moves with the "
        f"slate (the field's average score plus a little). Green at {P_CASH_GREEN:.0%} or more. Typical is "
        "its usual score and Big night is what it scores on a great slate (1 slate in 10 does better).",
    ),
    (
        TOP1_HEADER,
        "On a lineup's Total row: the chance the lineup finishes in the top 1% of a large-field GPP, a line "
        "that moves with the slate. An average entry has about 1%, so "
        f"{P_GPP_GREEN:.0%} or more (twice that) is green. The Cash or GPP marker highlights the number "
        "that matters for that lineup. Until ownership publishes it is the chance of reaching a fixed score "
        "instead.",
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
        "Your landing tab, nothing typed except the Pool dropdowns. Slate shape ranks the games; the "
        "Queue is what changed for your pool since the last sync; Pool check flags pool players with a "
        "problem; Pool summary counts your pool by position; Chalk map lists the highest-owned players at "
        "each position once ownership is out, with a Pool dropdown beside each; Stack candidates lists "
        "the best QB with his pass catchers in the best games.",
    ),
    (
        "Edge Finder",
        "Written by the sync. Per position, the best cash plays and best GPP plays, where CalPts disagrees "
        "with TFFB, who benefits from an injury, matchups and the usage trends and signals, each with a Why "
        "and a Do. Pool, at the left, shows your pick and changes it. The Why is the one most useful fact; "
        "hover it for the full reason. Overflow lists are folded under a plus sign.",
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
        "a position has room for. Pick a name in the top row (with Cash, GPP or Both beside it) to add one "
        "directly; clear a player's Pool cell to take him out. The rest is computed.",
    ),
    (
        "Suggested",
        "Complete lineups built from your pool by dfs lineups suggest: 3 cash builds, ranked by Cash %, "
        "and GPP builds, a stack for every QB and two each when they fit, ranked by Top 1% "
        "(GPP builds may use anyone in your pool, never all Cash-marked). Every "
        "build shows salary, CalPts, kickoff and total ownership, with the FLEX on the latest kickoff. "
        "Copy some or all "
        "into the empty lineups with Lineup Tools, Copy builds to Lineups (type 1,4,6 or all); nothing "
        "here changes Lineups on its own.",
    ),
    (
        "Lineups",
        f"Type names in column A, starting at row {LINEUPS_NAME_BLOCKS[0][0]}. Each lineup is one "
        f"{len(ROSTER_SLOTS)}-player roster block ({_roster_slots_text()}); there are "
        f"{len(LINEUPS_NAME_BLOCKS)} blocks total. The Issues column flags a duplicate, an unavailable "
        "player, a salary problem, an incomplete lineup, a Cash lineup with a weak Cash %, or a FLEX that "
        "should hold a later kickoff. The Total row holds Typical, Big night, Cash % and "
        f"{TOP1_HEADER} and the Cash or GPP marker you set; the portfolio line is on the "
        "Board.",
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
        "and GPP rows are added by dfs week close. STAKES, to the right of the ledgers, is rebuilt by "
        "dfs bankroll stakes: your ROI by format and this week's plan from your own rules.",
    ),
    (
        "Season",
        "The year at a glance: one row per week, year-to-date by bucket and a chart. Carried forward "
        "each week.",
    ),
    (
        "Results",
        "One row per week: cash points and cash line, head-to-head entered and won. You type it; it is "
        "never reset. The cash line you type here feeds Cash %.",
    ),
    (
        "Model Check",
        "Every projection scored against what happened, rebuilt by dfs results update. Each block opens with "
        "one bold sentence saying what the numbers show. Muted italic rows have too few players to say "
        "anything yet.",
    ),
    (
        "Contests",
        "Your contests by format with ROI and cash rate, the real cash line of each one from your standings "
        "files, how the fields build their lineups and your head-to-head record. Rebuilt by dfs week "
        "close; nothing is typed.",
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
    ("section", "HOW TO USE THE MODEL", "Six lines"),
    *(("row", a, b) for a, b in _MODEL_ROWS),
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
    # Column C too: an older layout kept a link per row there, and a link left beside a row it no longer
    # belongs to (CalPts, Suggested) reads as part of that row. The tab is two columns now.
    client.clear_ranges(tab, [f"A1:C{_CLEAR_TO_ROW}"])
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
