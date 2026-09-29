"""Part 7.5 (2026-09-18): the lineup-level metrics block Sam's own spec
called "the highest impact-per-effort item available" -- every published
DFS target (ownership, stacking, uniqueness) is a LINEUP property, and
the tool had been entirely player-level until now.

Pure spreadsheet formula generation, same "split the pure logic out"
split as `sheet_pool_formulas.py`/`sheet_style.py`'s guardrail functions
-- no new data source, no new EdgeRaw column. Every input here (GameID,
the name blocks) is already linked onto `Lineups` by
`sheet_links.link_edge_columns`; this module only combines what's
already there.

Round 5 item 1c (2026-09-29): `Stack`, `Bring-back`, `Own% Used` and
`Sub-10%` were REMOVED (Sam doesn't use them; `Sub-10%` never worked to
his eye). Stack shape is shown by item 4's subtle correlation tints on
the pick rows instead. Two per-lineup columns remain, each written once
per lineup block onto its own TOTALS row (blank on every slot row, same
convention `Issues`/`% of Cap` already use for a whole-lineup property):

- `Games` -- distinct GameIDs represented across the 9 picks.
- `Min Unique` -- the smallest number of picks THIS lineup has that
  DON'T appear in some OTHER lineup, minimized over every other lineup
  in the build. Answers "how different is my most similar other lineup"
  -- the real portfolio-diversification question a same-shirt-numbers
  comparison of "how many total players am I using" can't answer.
  O(lineups^2) in the number of lineup blocks (each lineup's formula
  names every OTHER block's own range once) -- fine at the 20-lineup
  scale this sheet is built for, not something to scale past without
  reconsidering.

Deliberately NOT built here (Part 7.4's own text, restated): a
player-level exposure cap -- "at 4-8 lineups they are actively harmful,"
exposure stays a REPORT (`Exposure`, already built), never a constraint.
"""

from __future__ import annotations

from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheets import SheetsClient, column_letter

_GAMES_HEADER = "Games"
_MIN_UNIQUE_HEADER = "Min Unique"
LINEUP_METRIC_HEADERS = [_GAMES_HEADER, _MIN_UNIQUE_HEADER]


def distinct_games_formula(start: int, end: int, *, gameid_col: str) -> str:
    # Found live (2026-09-19), auditing the portfolio-level version of this
    # exact idiom in `sheet_views.build_exposure`: a lineup with NO real
    # GameID typed anywhere makes FILTER's own result set genuinely empty,
    # which FILTER errors on (`#N/A`) -- but `COUNTA` absorbs that error
    # into a valid count of 1 (an error value still "counts" as present)
    # *before* IFERROR ever sees an error to catch, so this block's own
    # "Games" column was silently reading 1, not 0, for every still-empty
    # lineup since Part 7.5 shipped. `ROWS` does NOT absorb the error --
    # it propagates it, so `IFERROR(ROWS(...),0)` genuinely degrades to 0
    # -- confirmed empirically on the template's Scratch tab, both for a
    # truly-empty range (0) and a real multi-game range (correct count).
    rng = f"${gameid_col}${start}:${gameid_col}${end}"
    return f'=IFERROR(ROWS(UNIQUE(FILTER({rng},{rng}<>""))),0)'


def min_unique_formula(
    this_start: int,
    this_end: int,
    other_blocks: list[tuple[int, int]],
    *,
    name_col: str = "A",
    key_col: str | None = None,
) -> str:
    """The smallest count of THIS lineup's own picks absent from some
    OTHER lineup, minimized over every other lineup. `9 - overlap_count`
    per other lineup, where `overlap_count` is how many of this lineup's
    names also appear anywhere in that other lineup's own name range
    (`SUMPRODUCT(COUNTIF(other_range, this_range) > 0)` -- COUNTIF's own
    criteria argument broadcasts fine here since it's a plain value
    lookup, not the MATCH-inside-a-bare-array-literal case that needed
    FILTER wrapping elsewhere in this codebase).

    Blank with no other lineups to compare against (the build's first
    lineup, or a one-lineup week) -- there's no "most similar other
    lineup" to report.

    Week 3 fixes, Fix 6.4 (2026-09-23): also blank while THIS block is
    entirely empty. Found while checking every lineup-metric cell for
    Stack's own "warning on an empty block" bug: `COUNTIF(other_rng,
    this_rng)` treats a blank cell in `this_rng` as matching any blank
    cell in `other_rng` (both are "no value"), so an unbuilt lineup
    compared against any OTHER still-unbuilt lineup (near-universal
    early in the week) read `Min Unique: 0` -- indistinguishable from
    "these two lineups are complete duplicates," when neither is built
    at all."""
    if not other_blocks:
        return '=""'
    # Round 5 follow-up item 3: overlap is judged on the hidden `Player Key` (DK's
    # canonical name) when there is one, so "kenneth walker" in one lineup and
    # "Kenneth Walker III" in another are the same player. Emptiness is still read off
    # the typed column (a formula-blank key cell would count as non-empty in COUNTA).
    compare_col = key_col or name_col
    block_empty = f"COUNTA($A${this_start}:$A${this_end})=0"
    this_rng = f"${compare_col}${this_start}:${compare_col}${this_end}"
    terms = []
    for other_start, other_end in other_blocks:
        other_rng = f"${compare_col}${other_start}:${compare_col}${other_end}"
        picks = this_end - this_start + 1
        terms.append(f"({picks}-SUMPRODUCT(COUNTIF({other_rng},{this_rng})>0))")
    return f'=IF({block_empty},"",MIN({",".join(terms)}))'


def write_lineup_metrics(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """Writes both metrics onto every lineup block's own totals row. Every
    column found by header name (never a hardcoded letter) --
    `dfs setup reorder-columns` must have run first so
    `LINEUP_METRIC_HEADERS`/`GameID` all already exist; skips cleanly
    (naming which name is missing) if not, rather than writing formulas
    against a column that isn't there yet."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    required = ["GameID", *LINEUP_METRIC_HEADERS]
    missing = [name for name in required if name not in header]
    if missing:
        return f"{tab}: column(s) {missing} not found (run `dfs setup reorder-columns` first) -- skipped"

    gameid_col = column_letter(header.index("GameID"))
    games_col = column_letter(header.index(_GAMES_HEADER))
    min_unique_col = column_letter(header.index(_MIN_UNIQUE_HEADER))
    key_col = column_letter(header.index(LINEUP_KEY_HEADER)) if LINEUP_KEY_HEADER in header else None

    for start, end in name_blocks:
        totals_row = end + 1
        other_blocks = [(s, e) for s, e in name_blocks if (s, e) != (start, end)]
        client.update_range(
            tab, f"{games_col}{totals_row}", [[distinct_games_formula(start, end, gameid_col=gameid_col)]]
        )
        client.update_range(
            tab,
            f"{min_unique_col}{totals_row}",
            [[min_unique_formula(start, end, other_blocks, key_col=key_col)]],
        )

    return (
        f"{tab}: lineup metrics ({', '.join(LINEUP_METRIC_HEADERS)}) written for {len(name_blocks)} block(s)"
    )
