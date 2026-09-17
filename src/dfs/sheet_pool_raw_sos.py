"""Fixes `PlayerPoolRaw`'s own `OppPosRank` formula -- a straight
`VLOOKUP`+`HLOOKUP` against `SoSComb`, hand-typed once into the template
long before any Python code in this repo tracked it (nothing else here
regenerates it; `sheet_native_links.NATIVE_LOOKUP_COLUMNS` only covers
the DOWNSTREAM copies this formula's OWN result feeds into on Player
Pool/Lineups).

**The bug, found live 2026-09-16, the first time real data ever flowed
through it:** the hand-typed formula keyed its `SoSComb` lookup on this
row's own `Team` (column C), not its `Opp.` (column D) -- so every
`OppPosRank` value on the sheet measured how tough a player's OWN
defense is against their OWN position, never their actual opponent's.
Invisible for as long as `SoSQB`/`SoSRB`/etc. were blank (`#N/A` either
way regardless of which column the lookup used); confirmed by
cross-checking 29 real players against `SoSComb`'s own per-team ranks by
hand -- every single one matched "team's own rank," never "opponent's
rank." Never caught by any test, since nothing tracked the formula's own
correctness, only that some formula (any formula) occupied the column.

Fixed here as a real, regenerable Python-generated formula rather than a
second hand-typed patch -- the exact gap that let this bug hide in the
first place.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient, column_letter

OPP_POS_RANK_COLUMN = "OppPosRank"
_OPP_COLUMN = "Opp."
_POSITION_COLUMN = "Pos."
_SOS_COMB_TAB = "SoSComb"


def opp_pos_rank_formula(row: int, *, opp_column: str, position_column: str) -> str:
    """This row's OPPONENT's schedule rank at THIS row's own position --
    `HLOOKUP` picks the right of `SoSComb`'s five position columns (its
    own row 1/2 header-and-index pair), `VLOOKUP` then finds the
    opponent's own row by team abbreviation."""
    return (
        f"=VLOOKUP(${opp_column}{row},{_SOS_COMB_TAB}!$B:$G,"
        f"HLOOKUP(${position_column}{row},{_SOS_COMB_TAB}!$C$1:$G$2,2,false),false)"
    )


def rewrite_opp_pos_rank(client: SheetsClient, tab: str, *, last_row: int, header_row: int = 1) -> str:
    """Overwrites `tab`'s `OppPosRank` column (rows `header_row+1..last_row`)
    with the corrected formula -- every position found by header NAME
    (never a hardcoded column letter), matching this codebase's own
    "derive positions, never hardcode them" rule. No-ops if `tab` doesn't
    have an `OppPosRank` column at all (nothing to fix) or is missing the
    `Opp.`/`Pos.` columns the corrected formula depends on."""
    header_row_values = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(header_row_values[0]) if header_row_values else []
    if OPP_POS_RANK_COLUMN not in header:
        return f"{tab}: no {OPP_POS_RANK_COLUMN!r} column -- skipped"
    missing = [name for name in (_OPP_COLUMN, _POSITION_COLUMN) if name not in header]
    if missing:
        return f"{tab}: missing {missing} -- can't rebuild {OPP_POS_RANK_COLUMN!r}, skipped"

    rank_col = column_letter(header.index(OPP_POS_RANK_COLUMN))
    opp_col = column_letter(header.index(_OPP_COLUMN))
    pos_col = column_letter(header.index(_POSITION_COLUMN))

    first_data_row = header_row + 1
    rows = [
        [opp_pos_rank_formula(row, opp_column=opp_col, position_column=pos_col)]
        for row in range(first_data_row, last_row + 1)
    ]
    client.update_range(tab, f"{rank_col}{first_data_row}:{rank_col}{last_row}", rows)
    return (
        f"{tab}: rewrote {len(rows)} {OPP_POS_RANK_COLUMN!r} formula(s) (keyed by {_OPP_COLUMN!r}, not Team)"
    )
