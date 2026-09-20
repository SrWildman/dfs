"""Clear last week's typed-in lineup data from the working sheet, leaving
every formula and all formatting (conditional formatting included) intact.

The sheet gets duplicated fresh from a template every week (see the
weekly-template link in README.md), so most tabs start clean automatically.
Four tabs don't, because they hold typed values a human enters while
building lineups, not formulas: `Lineups` and `Player Pool` (a name column
per row, everything else VLOOKUPs off it -- though see Task K below,
`Player Pool`'s Name column isn't actually typed anymore), and
`Scratch`/`DK Upload` (full grids of typed player picks / contest entries
with no formulas at all).

Task K (4.3) made Player Pool's Name column a SORT/FILTER formula off
EdgeRaw's Pool tick column instead of a typed value -- `clear_previous_week`
detects this (a formula in the first block's first cell) and skips
clearing Player Pool entirely rather than destroying the formula, since
the actual per-week state (which players are ticked) lives in EdgeRaw,
which `dfs sync` already rewrites every week regardless. A follow-up to
4.3 (same session) then resized the RB/TE/DST blocks larger (Sam wanted
headroom rather than a manual-override escape hatch) via a real
`insertDimension`-based row insert -- see `sheet_pool_resize.py` -- which
is why PLAYER_POOL_NAME_BLOCKS' row counts aren't uniform per position.

The row blocks below are specific to this sheet's template layout -- they
were measured directly off the live sheet (non-blank formula rows in each
tab's Pts column), not derived from any general rule, because the blocks
aren't even uniformly sized (Lineups' first block is 10 rows, every other
one is 11). If the template's row layout ever changes, these need
re-measuring the same way.

Lineups specifically: every block from the 2nd one on opens with a
repeated sub-header row (Pos./Team/... re-printed so you don't have to
scroll back up to read the column labels for e.g. lineup 5) whose own
column A holds the literal text "Name", not a player slot -- clearing
that row's column A would wipe the label, not a stale pick. The first
block has no such row (the tab's own header, immediately above it,
already covers it), so its start stays as measured; every later block's
start is offset by 1 to skip it. Confirmed once already: an earlier
version of this file didn't do this and clearing wiped those 19 labels,
restored by hand.

Lineups' header sits at row 1. It didn't always: between 2026-09-06 and
2026-09-16 it moved to row 11, then back, as the "pool deck" (frozen rows
above the header holding a browsable window into Player Pool) was built,
resized twice, and finally removed outright after a week of real use
showed Player Pool's own colour scales/chips/`Used`/`In` columns already
covered the job -- see CONTRIBUTING.md's structural changelog for the
full history (four shifts total: +7, +14 net (-4 from the first), -4
more, and this -10 back to the original). Only the CURRENT position
matters for this constant; `sheet_pool_deck.py`'s module docstring has
the removal's own details.
"""

from __future__ import annotations

from dfs.config import EntryTableConfig
from dfs.sheets import SheetsClient

# Fix 2.4: each tuple is now the NINE REAL ROSTER ROWS ONLY (QB, RB, RB,
# WR, WR, WR, TE, FLEX, DST) -- `end` used to also be that block's totals
# row (the SUM/remaining-salary row directly below the 9th slot), which
# meant `dfs setup link-edge`/`polish_guardrails` treated it as a tenth
# roster slot: a permanent #N/A tenth-player VLOOKUP that could never
# resolve, since the totals row's own Name cell (column A) is always
# blank. The totals row for a given block is now `LINEUPS_TOTALS_ROWS`'
# corresponding entry (always `end + 1`) -- every consumer that used to
# read `end` as "the totals row" and back out the last real slot via
# `end - 1` now reads `end` directly as the last real slot, and reaches
# for `LINEUPS_TOTALS_ROWS` (or an inline `end + 1`) for the totals row
# itself. See CONTRIBUTING.md's structural changelog for the full list of
# symbols this touched.
LINEUPS_NAME_BLOCKS = [
    (2, 10),
    (15, 23),
    (28, 36),
    (41, 49),
    (54, 62),
    (67, 75),
    (80, 88),
    (93, 101),
    (106, 114),
    (119, 127),
    (132, 140),
    (145, 153),
    (158, 166),
    (171, 179),
    (184, 192),
    (197, 205),
    (210, 218),
    (223, 231),
    (236, 244),
    (249, 257),
]
LINEUPS_TOTALS_ROWS = [end + 1 for _, end in LINEUPS_NAME_BLOCKS]

# A3: row 1 became a real "add a player" control (a name search box, see
# `sheet_pool_control.py`) via a real `insertDimension` -- every row below
# shifted down by 1 from the pre-A3 layout `[(2, 11), (13, 32), (34, 58),
# (60, 69), (71, 80)]`. `PLAYER_POOL_CONTROL_ROW`/`PLAYER_POOL_HEADER_ROW`
# are the two new fixed positions this shift created; every consumer that
# used to assume Player Pool's header sits at row 1 (`sheet_pool_deck.py`,
# `sheet_reorder.py`'s callers, `sheet_links.link_edge_columns`, `doctor.py`)
# now derives it from `PLAYER_POOL_HEADER_ROW` instead. See
# CONTRIBUTING.md's structural changelog for the full list this touched.
PLAYER_POOL_CONTROL_ROW = 1
PLAYER_POOL_NAME_BLOCKS = [(3, 12), (14, 33), (35, 59), (61, 70), (72, 81)]
PLAYER_POOL_HEADER_ROW = PLAYER_POOL_NAME_BLOCKS[0][0] - 1

# Full-grid tabs: clear everything below the header, generously past any
# row/column count actually seen so far.
SCRATCH_RANGE = "A2:I1000"
DK_UPLOAD_RANGE = "A2:M1000"

# Phase 5G: `EntriesRaw` (hand-pasted DK contest-history export -- see
# docs/SHEET_REFERENCE.md's "EntriesRaw / GPPin / DKLineupsRaw /
# DKLineupsFinal") is the one other genuinely TYPED tab `dfs week new`
# never cleared -- found auditing every tab for the same "survives into a
# new week looking current" risk Bankroll's contest rows had (see
# CONTRIBUTING.md's changelog). `GPPin`/`DKLineupsRaw`/`DKLineupsFinal`
# are confirmed entirely formula-driven off it (verified via
# `value_render_option="FORMULA"`, see docs/ROADMAP.md's Phase 4
# postmortem) -- clearing EntriesRaw's data rows only ever makes their
# formulas resolve to blank/#N/A, the same "blank is better than bad"
# outcome Fix 2.14 already established for every synced source tab.
# Column count (A:M, 13) is not a guess -- SHEET_REFERENCE.md documents
# EntriesRaw's real shape as "same roster-slot shape as Lineups/DK
# Upload": Entry ID/Contest Name/Contest ID/Entry Fee (4) plus the 9
# roster slots, and `DK_UPLOAD_RANGE` above is that same shape's own
# already-verified range.
ENTRIES_RAW_TAB = "EntriesRaw"
ENTRIES_RAW_RANGE = "A2:M1000"


def _clear_bankroll_bucket(
    client: SheetsClient, bankroll_tab: str, label: str, table_cfg: EntryTableConfig
) -> str:
    # Typed columns only (Fix 5H): A-H are `bankroll.sync_bucket`'s own
    # `_entry_row` (Entry/Place/Points/Winnings/Entries/Entry Fee/Prize
    # Pool/Places Paid), plus the dedupe-key column -- verified live
    # against a real synced sheet (read_formula, not just read_range) that
    # I ("% Paid", `=H{r}/E{r}`) and J ("Place %", `=B{r}/E{r}`) are
    # formulas, never touched here. Clearing the whole row would wipe
    # those on the very first `dfs week new` after this shipped.
    ranges = [
        f"A{table_cfg.first_row}:H{table_cfg.last_row}",
        f"{table_cfg.entry_key_column}{table_cfg.first_row}:{table_cfg.entry_key_column}{table_cfg.last_row}",
    ]
    client.clear_ranges(bankroll_tab, ranges)
    return f"{bankroll_tab}: cleared {label} contest rows {table_cfg.first_row}-{table_cfg.last_row}"


def clear_previous_week(
    client: SheetsClient,
    lineups_tab: str,
    player_pool_tab: str,
    scratch_tab: str,
    dk_upload_tab: str,
    *,
    bankroll_tab: str | None = None,
    bankroll_cash: EntryTableConfig | None = None,
    bankroll_gpp: EntryTableConfig | None = None,
) -> list[str]:
    """Clear last week's lineup data. Returns a human-readable line per tab
    describing what was cleared, for CLI display.

    `bankroll_tab`/`bankroll_cash`/`bankroll_gpp` are optional (a caller
    with no `[bankroll.cash]`/`[bankroll.gpp]` configured passes nothing,
    same as `doctor._check_bankroll_headers`' own None-check) and, when
    given, must be cleared AFTER `dfs week new`'s own bankroll-carryover
    step has already read the CURRENT sheet's Ending balance -- this
    function only ever runs against the NEW sheet, so that ordering is
    naturally satisfied by `week_new`'s own call sequence (carry forward,
    THEN `clear_previous_week`) rather than anything enforced here; see
    `cli.py`'s `week_new` docstring. Sam: "New week should clear out
    everything" -- `clear_previous_week` cleared Lineups/Player Pool/
    Scratch/DK Upload already but never Bankroll, so a fresh weekly copy
    opened showing last week's contests as if they were this week's.
    """
    summary = []

    lineups_ranges = [f"A{s}:A{e}" for s, e in LINEUPS_NAME_BLOCKS]
    client.clear_ranges(lineups_tab, lineups_ranges)
    summary.append(f"{lineups_tab}: cleared Name column across {len(LINEUPS_NAME_BLOCKS)} lineup slot(s)")

    # A3: the add-a-player control cell (row PLAYER_POOL_CONTROL_ROW) is a
    # plain typed value, not a formula -- unlike the rest of Player Pool,
    # `is_formula_driven` below doesn't cover it, and a name typed there
    # last week (for a player who may not even be on this week's slate)
    # must not survive into a new week any more than a stale Lineups pick
    # would (Fix 2.14's "blank is better than bad").
    client.clear_ranges(player_pool_tab, [f"B{PLAYER_POOL_CONTROL_ROW}"])
    summary.append(f"{player_pool_tab}: cleared the add-a-player control (B{PLAYER_POOL_CONTROL_ROW})")

    first_start, _ = PLAYER_POOL_NAME_BLOCKS[0]
    first_cell = client.read_formula(player_pool_tab, f"A{first_start}")
    is_formula_driven = bool(first_cell and first_cell[0] and str(first_cell[0][0]).startswith("="))
    if is_formula_driven:
        # Task K 4.3: Player Pool's Name column is a SORT/FILTER formula off
        # EdgeRaw's Pool tick column, not a typed value -- clearing it would
        # destroy the feature on the very first `dfs week new` after it
        # ships. Nothing here needs clearing: the ticks live in EdgeRaw,
        # which `dfs sync` already rewrites (and restores, see
        # sources/edge.py) every week regardless.
        summary.append(f"{player_pool_tab}: formula-driven (Task K), Name column left alone")
    else:
        pool_ranges = [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
        client.clear_ranges(player_pool_tab, pool_ranges)
        n = len(PLAYER_POOL_NAME_BLOCKS)
        summary.append(f"{player_pool_tab}: cleared Name column across {n} block(s)")

    client.clear_ranges(scratch_tab, [SCRATCH_RANGE])
    summary.append(f"{scratch_tab}: cleared {SCRATCH_RANGE}")

    client.clear_ranges(dk_upload_tab, [DK_UPLOAD_RANGE])
    summary.append(f"{dk_upload_tab}: cleared {DK_UPLOAD_RANGE}")

    if client.tab_exists(ENTRIES_RAW_TAB):
        client.clear_ranges(ENTRIES_RAW_TAB, [ENTRIES_RAW_RANGE])
        summary.append(f"{ENTRIES_RAW_TAB}: cleared {ENTRIES_RAW_RANGE}")

    if bankroll_tab and bankroll_cash:
        summary.append(_clear_bankroll_bucket(client, bankroll_tab, "cash", bankroll_cash))
    if bankroll_tab and bankroll_gpp:
        summary.append(_clear_bankroll_bucket(client, bankroll_tab, "GPP", bankroll_gpp))

    return summary


def clear_synced_tabs(
    client: SheetsClient, tab_mappings: dict[str, str], source_names: list[str]
) -> list[str]:
    """Fix 2.14 -- Sam: "If it's not ready when we do our new week, blank
    is better than bad." A synced source's own upload step already
    clears its tab right before writing fresh data, but only on success
    (`run_sync` continues past a failed source rather than stopping, so a
    source that fails on `dfs week new`'s first-ever sync against a
    brand-new sheet copy never touches its tab at all). That tab is then
    left holding whatever the TEMPLATE happened to carry -- which can be
    real-looking, wrong data, not an obvious blank: the canonical
    template is periodically rebuilt from a real past week's live sheet
    (see CONTRIBUTING.md), so its raw source tabs can still hold that
    week's actual numbers.

    Called unconditionally before the first sync a new week runs, this
    blanks every tab a source in `source_names` is mapped to, so a
    failure during that sync leaves a genuinely empty tab instead of a
    stale-but-plausible one. `client.write_tab(tab, [])` is `ws.clear()`
    with nothing written back -- the same clear every source's own
    upload already does, just run for all of them up front rather than
    one at a time on success.
    """
    summary = []
    for name in source_names:
        tab = tab_mappings.get(name)
        if not tab:
            continue
        client.write_tab(tab, [], clear_first=True)
        summary.append(f"{tab}: cleared before first sync ({name})")
    return summary
