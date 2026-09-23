"""Generates the Instructions tab from code (Week 3 fixes, proposal
approved 2026-09-23 -- see CONTRIBUTING.md's Fix 4 entry for the incident
that prompted this).

Before this module existed, Instructions was hand-typed prose on the
template, and nothing in this codebase read or corrected it except
`sheet_tab_removal.clean_instructions_tab` (which only ever deletes rows
for the five tabs explicitly retired in Part 4b). An independent review
found it badly stale: two rows described tabs that no longer exist at
all (`Pool Picks`, `PoolSort`), plus `LevBasis` -> `OwnStatus`, "sorted by
Leverage" -> `ValAdj`, `DEF` -> `DST`, and hardcoded column letters that
had drifted. All fixed by hand at the time; this module is what stops the
NEXT one of those from needing another manual audit.

**What this actually fixes, and what it doesn't.** Every fact below that
already has a named constant elsewhere in the codebase is derived from
it, not retyped: Player Pool's per-position caps
(`weekly_reset.PLAYER_POOL_NAME_BLOCKS`), Lineups' roster slot order and
block count (`models.ROSTER_SLOTS`, `weekly_reset.LINEUPS_NAME_BLOCKS`),
Player Pool's Overflow/Pool column letters
(`sheet_columns.PLAYER_POOL_COLUMN_ORDER`), and the pool tag-group order
(`sources.edge.POOL_TYPE_SORT_ORDER`). A future change to any of THOSE
constants updates this tab automatically the next time this module runs
-- that specific class of drift becomes structurally impossible. The
surrounding prose is still hand-written English describing behavior
(e.g. "seven collapsible sections", "one row per game") and can still
go stale the same way any comment or docstring can if the underlying
feature changes and nobody updates the words -- generating the ROWS from
code doesn't generate the FACTS inside them. `sheet_style.TAB_NOTES` hit
this same limit (found live, same review): being code-generated didn't
stop its own strings from describing a Board that no longer existed.

**Which tabs get a row, and in what order, is an editorial choice** --
the same shape as `TAB_NOTES`' own explicit dict, not derived from
`sheet_style.WEEK_ORDER`/`HIDE_TABS`. Instructions documents several
tabs that are hidden from the visible strip (the raw sync tabs --
TFFBOptoRaw, DKSalRaw, oddsraw, GamesRaw, WeatherRaw, the five SoS
tabs), because a reader trying to understand what feeds the sheet needs
them explained even though they're not in the tab strip day to day; it
also skips a couple of pure-plumbing tabs (DkSalClean, oddsFinal)
nobody needs to understand to use the sheet. If that curation ever needs
to change, change `_TAB_ROWS` below -- there's no mechanical way to
derive "which tabs are worth explaining to a person" from tab metadata.

Idempotent: every call rewrites every row's content, the same "just
overwrite it" stance `TAB_NOTES`/`apply_tab_notes` already takes. Uses
targeted `update_range` calls per row rather than a full-tab `write_tab`
clear+rewrite -- Instructions' bold header row, wrapped text, and column
widths are hand-styled on the template and never touched by any other
`dfs` command; a blanket clear risks stripping formatting `write_tab`
was never asked to preserve. Never inserts or deletes a row -- the row
layout below is fixed to match the tab's own current, already-cleaned-up
structure (Pool Picks/PoolSort's old rows are gone for good, not
something this needs to keep re-deleting).
"""

from __future__ import annotations

from dfs.models import ROSTER_SLOTS
from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_TYPE_SORT_ORDER
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS

INSTRUCTIONS_TAB = "Instructions"

_TITLE_ROW = 1
_GENERAL_FIRST_ROW = 2
_DIVIDER_ROW = 6
_TAB_HEADER_ROW = 7
_TAB_FIRST_ROW = 8
_DOC_LINKS_HEADER_ROW = 26

# Per-position pool caps (Player Pool's own block sizes), in the same
# QB/RB/WR/TE/DST order PLAYER_POOL_NAME_BLOCKS itself is written in --
# confirmed to match by `dfs doctor`'s own structural checks elsewhere,
# never re-verified here (this module trusts the same order every other
# consumer of PLAYER_POOL_NAME_BLOCKS already trusts).
_POOL_POSITIONS = ("QB", "RB", "WR", "TE", "DST")


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


def _pp_col(name: str) -> str:
    return column_letter(PLAYER_POOL_COLUMN_ORDER.index(name))


_TITLE = "How this sheet works"

# The 5 general-info rows (label, body). Rows 2-5 hold real content;
# `_DIVIDER_ROW` (6) is a deliberate blank spacer before the tab-by-tab
# reference section and is never written to.
_GENERAL_ROWS: list[tuple[str, str]] = [
    (
        "THE ONE THING TO KNOW",
        "Exactly four things are typed in this entire workbook, all pale yellow: "
        "(1) the Pool dropdown in EdgeRaw column A (leftmost, blank/Cash/GPP/Both), "
        "(2) a player name in Player Pool's own row 1 search box (the add-a-player "
        "control cell), (3) player names in Lineups column A, (4) a percentage in "
        "Exposure column F (Target). Everything else is a formula -- if a cell isn't "
        "pale yellow, don't type into it. Most other tabs will warn you before you "
        "can (Data > Protection), but the warning is dismissible, not a hard lock, "
        "so it won't stop a genuine fix. Player Pool's Name column is COMPUTED from "
        "EdgeRaw ticks + that add-a-player control cell (every name you type there "
        "this week accumulates in a hidden column, so typing a second name doesn't "
        "erase the first) -- do not type in it. Nothing works until you've run "
        "`dfs sync` at least once this week (see below), or the lookups will come "
        "back blank.",
    ),
    (
        "What is dfs?",
        "This sheet is fed by a Python command-line tool called `dfs`, installed and "
        "run separately on your own computer -- it is not a Google Sheets add-on, and "
        "nothing here syncs on its own. Setup instructions, full command reference, "
        "and the source code ->",
    ),
    (
        "Weekly workflow",
        "0) Not sure what to run? Just run `dfs` with no arguments -- it shows where "
        "you are in the week and the commands that make sense right now.  1) File > "
        "Make a copy of this template for the new week, then run `dfs week new "
        '"<url-of-the-copy>"` -- checks the copy\'s structure first (`dfs doctor`), '
        "points config.toml at it, carries your bankroll and Results log forward, "
        "clears last week's lineups, and runs a full sync, all in one step (asks for "
        "confirmation first).  2) Build the pool, any combination of three ways: "
        'tick players in EdgeRaw\'s Pool column (use the "Pool picking" filter '
        "view's Name search to find one fast); type a name into Player Pool's own "
        'row 1 search box; or run `dfs pool add "name"` from the terminal. All '
        "three land in Player Pool automatically, sorted by tag group then salary, "
        "capped by position, with a blank row between each tag group."
        "  3) Build lineups directly in Lineups: type names into column A of each "
        f"{len(ROSTER_SLOTS)}-player roster block, starting at row "
        f"{LINEUPS_NAME_BLOCKS[0][0]} -- the dropdown there also catches typos "
        "before they become a silent #N/A.  4) Pair a finished lineup to a real DK "
        "contest entry in the DK Upload tab, then run `dfs export` and upload that "
        "file to DraftKings.  5) Through the week, re-run `dfs sync` anytime as "
        "lines/injuries/weather move -- safe to re-run, never loses data (your "
        "EdgeRaw ticks survive it).  6) Gameday: `dfs sync --live` re-pulls odds/DK "
        "status/weather and shows what changed; `dfs lineups late-swap` checks your "
        "built lineups against real kickoff times and shows who's still swappable.  "
        "7) End of week: export your DK contest history and run `dfs week close "
        "--csv <file>` to reconcile Cash/GPP results (pass `--week N` to reconcile "
        "a specific week by hand instead of the connected sheet's own week).",
    ),
    (
        "Reading the colours",
        'One system, used everywhere: a dark header row always means "this is a '
        'table header", nothing else does. Pale yellow always means "you type '
        'here" (see above) -- the only cue for that. Red-to-green shading on a '
        "number means more is better (reversed on rank-style columns (SoS Rank, "
        "Player Pool/Lineups' own OppPosRank), where a LOW rank is the good "
        "matchup); a signed number like ImpMove/TotMove/SpdMove or Spread shades "
        "from red through white at zero to green, since zero -- not the middle of "
        'the range -- is what "no change" means. Ownership (ProjOwn on EdgeRaw, '
        'Rstr% on Player Pool/Lineups) is the one exception to "more is better": '
        "it shades white-to-amber-to-red instead, since high ownership is chalk -- "
        "a caution, not a quality. A flat grey cell on ownership means no real "
        "number yet (TFFB hasn't published ownership this week), not \"the lowest "
        'value". Chips (solid colour, bold text) mark a state, never a number -- '
        'Flag (can show more than one at once, e.g. "WIND LEVERAGE"), Avail, '
        "Venue (H/R), a lineup's Issues column. Grey text on OwnStatus means "
        "ownership hasn't published yet this week, in which case Leverage reads "
        "blank rather than a number that looks real but isn't. If you see colour "
        "that doesn't fit one of these, ask -- it's a bug, not a feature.",
    ),
]

_TAB_HEADER = ("Tab-by-tab reference", "Columns / what to do")

# The 18 tab-by-tab rows, in display order -- see this module's own
# docstring for why this set/order is an editorial choice, not derived
# from WEEK_ORDER/HIDE_TABS.
_TAB_ROWS: list[tuple[str, str]] = [
    (
        "Board",
        "Read-only landing tab (`dfs setup build-views`): seven collapsible sections "
        "-- Queue (what changed since the last sync, pooled players only; open by "
        "default), Slate shape (games ranked by total, open by default), "
        "Per-position leaders, Punt finder, Stack candidates, Pool diagnostics "
        "(reads your pool, not the slate), and a deferred Chalk map placeholder, "
        "plus the same games/highest-total/max-wind/injuries summary as before. "
        "Click a section's +/- to expand it. Nothing to type here -- check it "
        "first each week.",
    ),
    (
        "Slate Grid",
        "Read-only (`dfs setup build-views`): one row per game instead of one row "
        "per player -- kickoff, total, spread, roof, wind/gust, rest days, "
        "division game, stadium, plus per-game line movement (total move/spread "
        'move). A sortable filter view ("All") is available without touching the '
        "underlying rows. Nothing to type here.",
    ),
    (
        "TFFBOptoRaw",
        "Synced by `dfs sync` (`projections` source) from The Fantasy Footballers. "
        "Columns: Id, Name, Position, Team, ProjPts, ProjOwn, Opp, Salary, Ceiling, "
        "ImpPts, OU, Spread, Game, GameStart, Venue. You shouldn't need to edit this.",
    ),
    (
        "DKSalRaw",
        "Synced by `dfs sync` (`draftkings` source) from DraftKings, unauthenticated. "
        "Columns: Position, Name + ID, Name, ID, Roster Position, Salary, Game Info, "
        "TeamAbbrev, AvgPointsPerGame, Status (Q/OUT/IR).",
    ),
    (
        "oddsraw",
        "Synced by `dfs sync` (`nfl_odds` source) from Rotowire's DK odds feed. Two "
        "rows per game: Team, Date, Moneyline, Spread, Over-Under, Team Points.",
    ),
    (
        "GamesRaw",
        "Synced by `dfs sync` (`nflverse_games` source), free NFL schedule data. "
        "Columns: GameId, Away, Home, Date, Time, Stadium, Roof, Surface, AwayRest, "
        "HomeRest, DivGame, Spread, Total.",
    ),
    (
        "WeatherRaw",
        "Synced by `dfs sync` (`weather` source), free forecast for outdoor games "
        "only (dome games are skipped). Columns: GameId, Away, Home, Stadium, Temp, "
        "Wind, Gust, Precip, Flag (WIND if windy).",
    ),
    (
        "EdgeRaw",
        "Synced by `dfs sync` (`edge` source) -- computed locally from the tabs "
        'above, no network call. The "which players are actually worth a look" tab, '
        "sorted by ValAdj descending. Column A is Pool: a blank/Cash/GPP/Both "
        "dropdown -- any non-blank value adds that player to Player Pool. Preserved "
        "across `dfs sync`, keyed on the DraftKings player Id. Id (B) is hidden -- "
        "meaningless to look at. Row already tinted when that player is pooled, and "
        "its Name bolded when Flag is set. Click any header arrow to sort or search "
        "directly (Data > Create a filter). Four saved presets also live under "
        'Data > Filter views: "Pool picking", "Leverage plays", "Available only", '
        '"In my pool". What each column means ->',
    ),
    (
        "PlayerPoolRaw",
        "The hub tab everything below reads from -- one row per player, pulling "
        "together every raw tab above plus EdgeRaw. Columns: Name, Pos., Team, DK "
        "Sal, O/U, Spread, Team Implied, Opp., Venue, OppPosRank, Pts, Ceil, Val, "
        "Rstr%, then (linked from EdgeRaw) CeilVal, CeilPct, Leverage, OwnStatus, "
        "GameEnv, Stadium, Roof, Wind, Avail, Flag. You shouldn't need to edit this "
        "directly -- fully protected (warning-only).",
    ),
    (
        "Player Pool",
        "DO NOT TYPE HERE -- fully protected (warning-only), except row 1's own "
        "search box (type a few letters of a name, pick from the list -- the "
        "add-a-player control cell). Column A is computed from the UNION of "
        "EdgeRaw's Pool ticks and that control cell -- each position block pulls "
        "every player of that position from either source, deduped, sorted by tag "
        f"group ({_tag_order_text()} -- one blank row between adjacent non-empty "
        f"groups) then salary descending within a group, capped at {_pool_caps_text()}. "
        f"Column {_pp_col('Pool')} (Pool) mirrors that player's actual EdgeRaw Pool "
        "value (Cash/GPP/Both). Tick/type more than a cap and the Overflow column "
        f"({_pp_col('Overflow')}) warns you -- the extras are hidden, not silently "
        "dropped. Everything else VLOOKUPs off that computed name against "
        "PlayerPoolRaw: Team, DK Sal, O/U, Spread, Team Implied, Opp., Venue, "
        "OppPosRank, Pts, Ceil, Val, Rstr%, then the same linked Edge columns as "
        'PlayerPoolRaw at the far right -- grouped, click the "-" above the column '
        "letters to collapse them if it's too much at once.",
    ),
    (
        "Lineups",
        f"TYPE NAMES IN COLUMN A ONLY, starting at row {LINEUPS_NAME_BLOCKS[0][0]}: a "
        "dropdown search box against PlayerPoolRaw catches typos before they turn "
        f"into a silent #N/A across the row. Repeats one {len(ROSTER_SLOTS)}-player "
        f"roster block ({_roster_slots_text()}) per lineup you're building "
        f"({len(LINEUPS_NAME_BLOCKS)} blocks total), each with its own "
        "salary/Pts/Ceil-totals and % of Rstr row underneath. Column O (Issues) "
        "shows per-lineup guardrails (duplicate player, OUT/IR/Q, over cap, "
        "incomplete). Same linked Edge columns at the right. Everywhere except "
        "column A is protected (warning-only) -- it's a formula. On gameday, "
        "`dfs lineups late-swap` checks every lineup here against real kickoff "
        "times and shows who's still swappable.",
    ),
    (
        "DK Upload",
        "Pair a finished lineup to a real DK contest entry here: Entry ID, Contest "
        "Name, Contest ID, Entry Fee, then the roster-slot columns (DraftKings' own "
        "bulk-upload format). Run `dfs export -o lineups.csv` once filled in, then "
        "upload that file on DraftKings.",
    ),
    (
        "Movement",
        "Read-only, gameday (`dfs setup build-views`): players ranked by how far "
        "their team's implied total has moved since the week started "
        "(colour-scaled, signed -- zero is the midpoint), with kickoff alongside. A "
        'sortable filter view ("All") is available. Blank until at least one '
        "`dfs sync`/`dfs sync --live` has run this week. Fully protected "
        "(warning-only).",
    ),
    (
        "Exposure",
        "Counts how many of your Lineups use each player -- how concentrated this "
        "week's lineups are on a given player. Target (column F, pale yellow) is "
        "the one typed cell here -- set a target exposure percentage and vs Target "
        "shows how far off you are, colour-coded over/under. A sortable filter view "
        '("All") is available. Everywhere except Target is protected (warning-only).',
    ),
    (
        "SoSQB / SoSRB / SoSWR / SoSTE / SoSDef",
        "Synced automatically by `dfs sync` (`sos_qb`/`sos_rb`/`sos_wr`/`sos_te`/"
        "`sos_dst` sources) from The Fantasy Footballers' Foot Clan Premium access, "
        "current week only. One position per tab; Rank is colour-scaled REVERSED (a "
        "low rank is the good matchup here, same as Player Pool/Lineups' own "
        'OppPosRank). A sortable filter view ("All") is available on each. Hidden '
        "from the visible tab strip (Phase 6, Part 4) since SoSComb -- the combined "
        "lookup everything else actually reads -- is the one you'd normally check "
        "directly; unhide one via the sheet's tab-bar menu if you want to look at a "
        "single position's raw numbers.",
    ),
    (
        "SosComb",
        "Combines the 5 SoS tabs into one lookup, keyed by team and position. A "
        'sortable filter view ("All") is available.',
    ),
    (
        "Bankroll",
        "Cash/GPP results ledgers plus starting/ending bankroll totals. `dfs week "
        "close --csv <file>` (or `dfs bankroll sync --csv <file>` directly) appends "
        "new results here after each week -- never touches the summary figures or "
        "anything outside its configured rows.",
    ),
    (
        "Results",
        "Season-level results log (Week, Cash Pts/Line, H2H Entered/Win, "
        "Red/Blue/Black) -- Cash Results chipped green/red, H2H % colour-scaled. "
        "NOT reset each week -- `dfs week new` carries it forward from the outgoing "
        'sheet automatically. A sortable filter view ("All") is available.',
    ),
]

_DOC_LINKS_HEADER = "Full documentation"
_DOC_LINK_ROWS: list[str] = [
    "The weekly grind, phase by phase (research, build the pool, build lineups, "
    "enter, monitor, reconcile), with exact commands ->",
    "Symptom -> cause -> fix, for when something looks wrong ->",
    "Complete column-by-column reference ->",
    "Every calculation, formula, and threshold, explained ->",
    "Setup, all commands, source code ->",
]


def build_instructions_tab(client: SheetsClient, tab: str = INSTRUCTIONS_TAB) -> str:
    """Rewrites every content row of the Instructions tab. Never inserts
    or deletes a row -- see this module's docstring for why the row
    layout is fixed rather than computed, and why this uses per-row
    `update_range` calls instead of a full-tab clear+rewrite."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    client.update_range(tab, f"A{_TITLE_ROW}", [[_TITLE]])

    for offset, (label, body) in enumerate(_GENERAL_ROWS):
        row = _GENERAL_FIRST_ROW + offset
        client.update_range(tab, f"A{row}:B{row}", [[label, body]])

    client.update_range(tab, f"A{_TAB_HEADER_ROW}:B{_TAB_HEADER_ROW}", [list(_TAB_HEADER)])

    for offset, (tab_name, body) in enumerate(_TAB_ROWS):
        row = _TAB_FIRST_ROW + offset
        client.update_range(tab, f"A{row}:B{row}", [[tab_name, body]])

    client.update_range(tab, f"A{_DOC_LINKS_HEADER_ROW}", [[_DOC_LINKS_HEADER]])
    doc_links_first_row = _DOC_LINKS_HEADER_ROW
    for offset, body in enumerate(_DOC_LINK_ROWS):
        row = doc_links_first_row + offset
        client.update_range(tab, f"B{row}", [[body]])

    total_rows = 1 + len(_GENERAL_ROWS) + 1 + len(_TAB_ROWS) + len(_DOC_LINK_ROWS)
    return f"{tab}: {total_rows} row(s) written"
