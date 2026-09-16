"""Phase 3: the single designed column order for PlayerPoolRaw, Player
Pool and Lineups -- the "do it ONCE" order CONTRIBUTING.md's structural
changelog describes, so reordering these three tabs in lockstep (the
operation that silently corrupted the sheet twice before) never needs to
happen again. `derived.EDGE_COLUMNS` is EdgeRaw's own copy of the same
grammar; this module is its counterpart for the three tabs that hold real
hand-authored formulas (and so need actual `moveDimension` calls, not just
a `dfs sync` rewrite -- see `column_reorder.py`).

Same six zones everywhere: IDENTITY, DECISION, GAME (with four blank `SoS
n` columns reserved for the strength-of-schedule work landing in a few
weeks -- adding them now means that future column-order change never has
to happen either), WEATHER (collapsed), MOVEMENT (collapsed), INTERNAL
(collapsed). Header text matches each tab's own already-established
spelling (`Pos.`/`Opp.`/`DK Sal`/`Pts`/`Ceil`/`Rstr%`, not EdgeRaw's
`Position`/`Opp`/`Salary`/`ProjPts`/`Ceiling`/`ProjOwn`) -- normalizing
that text is a structural change this codebase deliberately never makes
(see `sheet_style.py`'s FIELD_FORMATS docstring).

`LINKED_COLUMNS` is the subset of each zone that's a VLOOKUP-by-Name
against EdgeRaw (`sheet_links.link_edge_columns` fills these); everything
else in a zone is a native column PlayerPoolRaw/Player Pool/Lineups
already compute for themselves (a direct source formula, or in the two
GAME-zone/blank-`SoS n` case, not wired to anything yet). Every linked
name here is spelled exactly as it appears in `derived.EDGE_COLUMNS` --
unlike the native columns, the linked ones were introduced by
`link_edge_columns` using EdgeRaw's own names directly, so there's no
separate alias to track.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS

IDENTITY = ["Name", "Pos.", "Team", "Opp.", "Venue"]

# DK Sal/Pts/Val/Ceil/Rstr% are native (read straight off DkSalClean/
# TFFBOptoRaw, or self-computed for Val); CeilVal/Leverage/Avail/Flag are
# linked (VLOOKUP against EdgeRaw).
DECISION = ["DK Sal", "Pts", "Val", "Ceil", "CeilVal", "Rstr%", "Leverage", "Avail", "Flag"]

# O/U/Spread/Team Implied/OppPosRank are native (VLOOKUP against oddsFinal/
# SoSComb); GameEnv is linked; the four `SoS n` columns are blank
# placeholders reserved for strength-of-schedule, not wired to anything
# yet -- see the module docstring.
GAME = ["O/U", "Spread", "Team Implied", "GameEnv", "OppPosRank", "SoS 1", "SoS 2", "SoS 3", "SoS 4"]

# All three collapsed groups are entirely linked.
WEATHER = ["Stadium", "Roof", "Wind"]
MOVEMENT = ["ImpMove", "TotMove", "SpdMove", "GameStart"]
INTERNAL = ["Id", "CeilPct", "OwnPct", "LevBasis"]

# Shared by all three tabs -- see PLAYER_POOL_RAW_COLUMN_ORDER/
# PLAYER_POOL_COLUMN_ORDER/LINEUPS_COLUMN_ORDER below for each tab's full
# header, including its own extra columns.
BASE_COLUMN_ORDER = [*IDENTITY, *DECISION, *GAME, *WEATHER, *MOVEMENT, *INTERNAL]

# The linked (EdgeRaw-VLOOKUP) subset of BASE_COLUMN_ORDER, in header
# order -- `sheet_links.LINKED_EDGE_COLUMNS` re-exports this list; kept
# here so it can be defined once, next to the zones it's drawn from,
# instead of duplicated by hand in sheet_links.py.
LINKED_COLUMNS = ["CeilVal", "Leverage", "Avail", "Flag", "GameEnv", *WEATHER, *MOVEMENT, *INTERNAL]

# PlayerPoolRaw has no tab-specific extras -- BASE_COLUMN_ORDER exactly.
PLAYER_POOL_RAW_COLUMN_ORDER = list(BASE_COLUMN_ORDER)

# Player Pool: "Source" (which of EdgeRaw/Pool Picks a row came from)
# belongs beside the other identity-ish columns, so it's inserted right
# after Venue; "Pool" (Fix 2.11's surfaced Cash/GPP/Both value) and
# "Overflow" (the over-the-cap warning) are both about *this tab's own
# roster mechanics*, not a player attribute, so they stay appended at the
# very end regardless of what else moves around them.
PLAYER_POOL_COLUMN_ORDER = [
    *IDENTITY,
    "Source",
    *DECISION,
    *GAME,
    *WEATHER,
    *MOVEMENT,
    *INTERNAL,
    "Overflow",
    "Pool",
]

# Lineups: "% of Rstr" is a direct derivative of Rstr% (this lineup's
# share of that player's rostership), so it sits immediately after it;
# "Issues" (A1: renamed from "Check") is the last thing you look at before
# trusting a lineup, so it sits immediately after Flag -- the last member
# of DECISION -- rather than at the tab's far right past three collapsed
# groups nobody's about to expand just to see it.
LINEUPS_COLUMN_ORDER = [
    *IDENTITY,
    "DK Sal",
    "Pts",
    "Val",
    "Ceil",
    "CeilVal",
    "Rstr%",
    "% of Rstr",
    "Leverage",
    "Avail",
    "Flag",
    "Issues",
    *GAME,
    *WEATHER,
    *MOVEMENT,
    *INTERNAL,
]

assert set(LINKED_COLUMNS) <= set(EDGE_COLUMNS)  # noqa: S101 - internal self-check, not a public contract
assert len(BASE_COLUMN_ORDER) == len(set(BASE_COLUMN_ORDER))  # noqa: S101
assert len(PLAYER_POOL_COLUMN_ORDER) == len(set(PLAYER_POOL_COLUMN_ORDER))  # noqa: S101
assert len(LINEUPS_COLUMN_ORDER) == len(set(LINEUPS_COLUMN_ORDER))  # noqa: S101
