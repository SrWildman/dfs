"""Part 7.5 (2026-09-18): the lineup-level metrics block Sam's own spec
called "the highest impact-per-effort item available" -- every published
DFS target (ownership, stacking, uniqueness) is a LINEUP property, and
the tool had been entirely player-level until now.

Pure spreadsheet formula generation, same "split the pure logic out"
split as `sheet_pool_formulas.py`/`sheet_style.py`'s guardrail functions
-- no new data source, no new EdgeRaw column. Every input here (Team,
Position, GameID, Own%, Opp.) is already linked onto `Lineups` by
`sheet_links.link_edge_columns` (Part 7.4 added GameID); this module
only combines what's already there.

Six new per-lineup columns, each written once per lineup block onto its
own TOTALS row (blank on every slot row, same convention `Issues`/`% of
Cap` already use for a whole-lineup property):

- `Stack` -- e.g. "QB+2 (KC) + 1 bring-back", "no QB", "no stack".
- `Games` -- distinct GameIDs represented across the 9 picks.
- `Bring-back` -- Yes/No, a plain-language mirror of `Stack`'s own
  bring-back count, for a reader scanning the column rather than parsing
  the signature string.
- `Own% Used` -- summed projected ownership across the 9 picks. Blank
  (not a confidently-wrong 0%) until `OwnStatus` says ownership is real
  for the slate -- same "don't show a number that looks real but isn't"
  policy `Leverage` already established.
- `Sub-10%` -- count of picks under 10% projected ownership. Same
  ownership-published guard as `Own% Used`, for the same reason: every
  player reads exactly 0% pre-publish, which would make this column
  read "9/9" every single week before Tuesday, a meaningless number
  dressed as a real one.
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
Stack SHAPE (QB+1 vs QB+2 vs QB+3) is reported via `Stack` above, never
warned about in `Issues` -- there's no cash/GPP tag per lineup (Part
7.3), so a shape warning would fire wrongly on a lineup that should have
no stack at all.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient, column_letter

# Part 7.5's own explicit threshold -- "Count of players under 10%
# owned," not a value to invent.
SUB_10_OWNERSHIP_THRESHOLD = 0.10

_STACK_HEADER = "Stack"
_GAMES_HEADER = "Games"
_BRING_BACK_HEADER = "Bring-back"
_OWN_USED_HEADER = "Own% Used"
_SUB_10_HEADER = "Sub-10%"
_MIN_UNIQUE_HEADER = "Min Unique"
LINEUP_METRIC_HEADERS = [
    _STACK_HEADER,
    _GAMES_HEADER,
    _BRING_BACK_HEADER,
    _OWN_USED_HEADER,
    _SUB_10_HEADER,
    _MIN_UNIQUE_HEADER,
]


def _qb_team_formula(start: int, end: int, *, position_col: str, team_col: str) -> str:
    return (
        f"IFERROR(INDEX(${team_col}${start}:${team_col}${end},"
        f'MATCH("QB",${position_col}${start}:${position_col}${end},0)),"")'
    )


def _qb_game_formula(start: int, end: int, *, position_col: str, gameid_col: str) -> str:
    return (
        f"IFERROR(INDEX(${gameid_col}${start}:${gameid_col}${end},"
        f'MATCH("QB",${position_col}${start}:${position_col}${end},0)),"")'
    )


def stack_signature_formula(
    start: int, end: int, *, position_col: str, team_col: str, gameid_col: str
) -> str:
    """ "QB+2 (KC) + 1 bring-back" / "QB+0 (KC)" / "no QB". Stack count is
    every OTHER rostered player (any position) on the QB's own team;
    bring-back count is every rostered player in the QB's own GAME but on
    the OPPONENT's team -- neither excludes a bring-back from also being
    counted if he happens to be... there's no double-count risk here,
    since a bring-back is on the OPPOSING team by definition, disjoint
    from the QB's own team's stack count."""
    qb_team = _qb_team_formula(start, end, position_col=position_col, team_col=team_col)
    qb_game = _qb_game_formula(start, end, position_col=position_col, gameid_col=gameid_col)
    stack_count = (
        f"COUNTIFS(${team_col}${start}:${team_col}${end},{qb_team},"
        f'${position_col}${start}:${position_col}${end},"<>QB")'
    )
    bring_back_count = (
        f"COUNTIFS(${gameid_col}${start}:${gameid_col}${end},{qb_game},"
        f'${team_col}${start}:${team_col}${end},"<>"&{qb_team})'
    )
    return (
        f'=IF({qb_team}="","no QB",'
        f'"QB+"&{stack_count}&" ("&{qb_team}&")"'
        f'&IF({bring_back_count}>0," + "&{bring_back_count}&" bring-back",""))'
    )


def distinct_games_formula(start: int, end: int, *, gameid_col: str) -> str:
    rng = f"${gameid_col}${start}:${gameid_col}${end}"
    return f'=IFERROR(COUNTA(UNIQUE(FILTER({rng},{rng}<>""))),0)'


def bring_back_present_formula(
    start: int, end: int, *, position_col: str, team_col: str, gameid_col: str
) -> str:
    qb_team = _qb_team_formula(start, end, position_col=position_col, team_col=team_col)
    qb_game = _qb_game_formula(start, end, position_col=position_col, gameid_col=gameid_col)
    bring_back_count = (
        f"COUNTIFS(${gameid_col}${start}:${gameid_col}${end},{qb_game},"
        f'${team_col}${start}:${team_col}${end},"<>"&{qb_team})'
    )
    return f'=IF({qb_team}="","",IF({bring_back_count}>0,"Yes","No"))'


def own_used_formula(start: int, end: int, totals_row: int, *, own_col: str, own_status_col: str) -> str:
    """Blank (not 0%) until `OwnStatus` says ownership is real for the
    slate -- summing an all-zero pre-publish `Own%` column would read as
    a confident "0% owned," which is not a real claim yet."""
    own_status = f"${own_status_col}{totals_row}"
    return f'=IF({own_status}<>"real","",SUM(${own_col}${start}:${own_col}${end}))'


def sub_10_percent_formula(
    start: int, end: int, totals_row: int, *, own_col: str, own_status_col: str
) -> str:
    own_status = f"${own_status_col}{totals_row}"
    count = f'COUNTIFS(${own_col}${start}:${own_col}${end},"<{SUB_10_OWNERSHIP_THRESHOLD}")'
    return f'=IF({own_status}<>"real","",{count})'


def min_unique_formula(
    this_start: int, this_end: int, other_blocks: list[tuple[int, int]], *, name_col: str = "A"
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
    lineup" to report."""
    if not other_blocks:
        return '=""'
    this_rng = f"$A${this_start}:$A${this_end}"
    terms = []
    for other_start, other_end in other_blocks:
        other_rng = f"${name_col}${other_start}:${name_col}${other_end}"
        picks = this_end - this_start + 1
        terms.append(f"({picks}-SUMPRODUCT(COUNTIF({other_rng},{this_rng})>0))")
    return f"=MIN({','.join(terms)})"


def write_lineup_metrics(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """Writes all six metrics onto every lineup block's own totals row.
    Every column found by header name (never a hardcoded letter) --
    `dfs setup reorder-columns` must have run first so
    `LINEUP_METRIC_HEADERS`/`Position`/`Team`/`GameID`/`Own%`/`OwnStatus`
    all already exist; skips cleanly (naming which name is missing) if
    not, rather than writing formulas against a column that isn't there
    yet."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    required = ["Pos.", "Team", "Opp.", "GameID", "Own%", "OwnStatus", *LINEUP_METRIC_HEADERS]
    missing = [name for name in required if name not in header]
    if missing:
        return f"{tab}: column(s) {missing} not found (run `dfs setup reorder-columns` first) -- skipped"

    position_col = column_letter(header.index("Pos."))
    team_col = column_letter(header.index("Team"))
    gameid_col = column_letter(header.index("GameID"))
    own_col = column_letter(header.index("Own%"))
    own_status_col = column_letter(header.index("OwnStatus"))
    stack_col = column_letter(header.index(_STACK_HEADER))
    games_col = column_letter(header.index(_GAMES_HEADER))
    bring_back_col = column_letter(header.index(_BRING_BACK_HEADER))
    own_used_col = column_letter(header.index(_OWN_USED_HEADER))
    sub_10_col = column_letter(header.index(_SUB_10_HEADER))
    min_unique_col = column_letter(header.index(_MIN_UNIQUE_HEADER))

    for start, end in name_blocks:
        totals_row = end + 1
        other_blocks = [(s, e) for s, e in name_blocks if (s, e) != (start, end)]
        client.update_range(
            tab,
            f"{stack_col}{totals_row}",
            [
                [
                    stack_signature_formula(
                        start, end, position_col=position_col, team_col=team_col, gameid_col=gameid_col
                    )
                ]
            ],
        )
        client.update_range(
            tab, f"{games_col}{totals_row}", [[distinct_games_formula(start, end, gameid_col=gameid_col)]]
        )
        client.update_range(
            tab,
            f"{bring_back_col}{totals_row}",
            [
                [
                    bring_back_present_formula(
                        start, end, position_col=position_col, team_col=team_col, gameid_col=gameid_col
                    )
                ]
            ],
        )
        client.update_range(
            tab,
            f"{own_used_col}{totals_row}",
            [[own_used_formula(start, end, totals_row, own_col=own_col, own_status_col=own_status_col)]],
        )
        client.update_range(
            tab,
            f"{sub_10_col}{totals_row}",
            [
                [
                    sub_10_percent_formula(
                        start, end, totals_row, own_col=own_col, own_status_col=own_status_col
                    )
                ]
            ],
        )
        client.update_range(
            tab, f"{min_unique_col}{totals_row}", [[min_unique_formula(start, end, other_blocks)]]
        )

    return (
        f"{tab}: lineup metrics ({', '.join(LINEUP_METRIC_HEADERS)}) written for {len(name_blocks)} block(s)"
    )
