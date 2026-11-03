"""Plan D item 7: the hand-built VLOOKUPs whose column NUMBER is typed into the formula.

Google Sheets shifts a range reference when a column moves upstream, but never the integer index inside
`VLOOKUP(key, range, n, false)` (CONTRIBUTING.md's "central hazard"; the Phase 8 incident was exactly this,
on PlayerPoolRaw). These formulas were built by hand on the sheet, not by this repo, so they are written out
here as the sheet holds them (row 2), and:

- `tests/test_sheet_literal_lookups.py` checks every index lands on the column it means in the writer's own
  order (`tffb_projections.SHEET_COLUMNS`, `rotowire_odds.ODDSRAW_COLUMNS`, oddsFinal's layout below);
- `dfs doctor` (check `literal-lookups`) checks the sheet still holds these exact formulas.

Change a writer's column order and the test fails; change a formula on the sheet and doctor says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dfs.sheets import column_index

PLAYER_POOL_RAW = "PlayerPoolRaw"
TFFB_TAB = "TFFBOptoRaw"
ODDS_FINAL = "oddsFinal"
ODDSRAW = "oddsraw"
FORMULA_ROW = 2

# oddsFinal (hand-built): A the team nickname, B its abbreviation, then three VLOOKUPs into oddsraw.
ODDS_FINAL_COLUMNS = ("team", "abbr", "Spread", "O/U", "Implied")


@dataclass(frozen=True)
class LiteralLookup:
    tab: str  # where the formula lives
    header: str  # the column it sits under there (row 1)
    formula: str  # row 2's formula, exactly as the sheet holds it
    returns: tuple[str, ...]  # what each VLOOKUP in it must return, in the formula's order


PLAYER_POOL_RAW_LOOKUPS = (
    LiteralLookup(
        PLAYER_POOL_RAW,
        "Pts",
        '=IF($B2="DST",VLOOKUP($A2,TFFBOptoRaw!$D:F,2,false),VLOOKUP($A2,TFFBOptoRaw!$B:F,4,false))',
        ("ProjPts", "ProjPts"),
    ),
    LiteralLookup(
        PLAYER_POOL_RAW,
        "Ceil",
        '=IF($B2="DST",VLOOKUP($A2,TFFBOptoRaw!$D:I,6,false),VLOOKUP($A2,TFFBOptoRaw!$B:I,8,false))',
        ("Ceiling", "Ceiling"),
    ),
    LiteralLookup(
        PLAYER_POOL_RAW,
        "Own%",
        '=(IF($B2="DST",VLOOKUP($A2,TFFBOptoRaw!$D:F,3,false),VLOOKUP($A2,TFFBOptoRaw!$B:F,5,false))/100)',
        ("ProjOwn", "ProjOwn"),
    ),
    LiteralLookup(
        PLAYER_POOL_RAW,
        "Venue",
        '=IF($B2="DST",VLOOKUP($A2,TFFBOptoRaw!$D:O,12,false),VLOOKUP($A2,TFFBOptoRaw!$B:O,14,false))',
        ("Venue", "Venue"),
    ),
    LiteralLookup(PLAYER_POOL_RAW, "O/U", "=VLOOKUP($C2,oddsFinal!$B:$E,3,false)", ("O/U",)),
    LiteralLookup(PLAYER_POOL_RAW, "Spread", "=VLOOKUP($C2,oddsFinal!$B:$E,2,false)", ("Spread",)),
    LiteralLookup(PLAYER_POOL_RAW, "Team Implied", "=VLOOKUP($C2,oddsFinal!$B:$E,4,false)", ("Implied",)),
)

ODDS_FINAL_LOOKUPS = (
    LiteralLookup(ODDS_FINAL, "Spread", "=VLOOKUP($A2,oddsraw!$A:$J,4,false)", ("spread",)),
    LiteralLookup(ODDS_FINAL, "O/U", "=VLOOKUP($A2,oddsraw!$A:$J,5,false)", ("total",)),
    LiteralLookup(ODDS_FINAL, "Implied", "=VLOOKUP($A2,oddsraw!$A:$J,7,false)", ("team_points",)),
)

ALL_LOOKUPS = PLAYER_POOL_RAW_LOOKUPS + ODDS_FINAL_LOOKUPS

_VLOOKUP = re.compile(
    r"VLOOKUP\(\s*[^,]+,\s*'?([A-Za-z0-9 _%]+?)'?!\$?([A-Z]+):\$?([A-Z]+)\s*,\s*(\d+)\s*,", re.IGNORECASE
)


@dataclass(frozen=True)
class Hit:
    target: str  # the tab looked up
    first: str  # the range's first column letter
    index: int  # the typed column number

    @property
    def column(self) -> int:
        """0-based column of the target tab the lookup returns."""
        return column_index(self.first) + self.index - 1


def vlookups(formula: str) -> list[Hit]:
    return [Hit(m.group(1), m.group(2), int(m.group(4))) for m in _VLOOKUP.finditer(formula)]
