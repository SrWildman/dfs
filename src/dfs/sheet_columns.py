"""Phase 3: the single designed column order for PlayerPoolRaw, Player
Pool and Lineups -- the "do it ONCE" order CONTRIBUTING.md's structural
changelog describes, so reordering these three tabs in lockstep (the
operation that silently corrupted the sheet twice before) never needs to
happen again. `derived.EDGE_COLUMNS` is EdgeRaw's own copy of the same
grammar; this module is its counterpart for the three tabs that hold real
hand-authored formulas (and so need actual `moveDimension` calls, not just
a `dfs sync` rewrite -- see `column_reorder.py`).

Phase 6, Part 2 (2026-09-17): redesigned into a **shared spine** visible
on all three tabs in identical order (Name/Pos/Team/Opp/DK Sal/Pts/Val/
Ceil/CeilVal/Own%/Avail/Flag), with everything else behind collapsed
groups in a fixed left-to-right order Sam specified: Game, Ceiling
detail, Movement, Weather. `Id` stays hidden outright, not part of any
visible group. Nothing is deleted -- every column that existed before
still exists, just renamed (`Rstr%`/`ProjOwn` -> `Own%`, `% of Rstr` ->
`% of Own`) and/or repositioned. `Leverage`'s new home (the collapsed
Ceiling detail group) is Part 7.1's decision, folded into this same
reorder rather than run as a second pass shortly after -- 7.1 is not
scheduled as its own step anywhere in `docs/HANDOFF_PHASE6.md`'s 7-step
order, it's the same category of change as this Part (column
positioning), and reordering the same three tabs twice in quick
succession is strictly more risk for no benefit. 7.1's OTHER pieces (the
Board panel change, the `docs/CALCULATIONS.md` note) are left for Step 4,
where the Board itself gets rebuilt (Part 3/7.6).

`Venue` moves out of IDENTITY into the Weather group -- it's demoted the
same as everything else not in the spine, and the spine deliberately does
not include it (see the spine list below). `ValAdj` (Part 7.2) and
`Flags` (Part 5) are NOT added here as placeholder columns -- both are
named in Part 2's own spine list as forward references to work that
hasn't shipped yet ("`Flags` is Part 5's new column; `ValAdj` is Part
7.2's"), and Sam has already rejected the reserved-placeholder pattern
once (the `SoS 1..4` removal, Phase 5 Section K: "Why still sos 1-4.
Should only be one per player"). Each will insert itself into the spine
at build time via the same `sheet_reorder.migrate_tab_to_designed_order`
mechanism that already knows how to insert and place a new column name
into a designed order (used for `Edge ↗`/`Used`/`In` in earlier phases).

Header text still matches each tab's own already-established spelling
(`Pos.`/`Opp.`/`DK Sal`/`Pts`/`Ceil`/`Own%`, not EdgeRaw's `Position`/
`Opp`/`Salary`/`ProjPts`/`Ceiling`/`Own%`) -- normalizing that text is a
structural change this codebase deliberately never makes (see
`sheet_style.py`'s FIELD_FORMATS docstring). `Own%` itself IS the one
new shared name (previously `ProjOwn` on EdgeRaw, `Rstr%` here) -- a
deliberate exception, since a shared spine cannot have a column that
changes name per tab (Part 2's own reasoning).

`LINKED_COLUMNS` is the subset of each zone that's a VLOOKUP-by-Name
against EdgeRaw (`sheet_links.link_edge_columns` fills these); everything
else in a zone is a native column PlayerPoolRaw/Player Pool/Lineups
already compute for themselves (a direct source formula, or for
`OppPosRank`, a VLOOKUP against SoSComb rather than EdgeRaw). `Venue` is
NATIVE (a VLOOKUP against TFFBOptoRaw, not EdgeRaw -- EdgeRaw has no
Venue column of its own to link from) despite sitting in the Weather
group alongside three linked columns; membership in a visual group is
about where Sam looks, not about how the value gets there. Every linked
name here is spelled exactly as it appears in `derived.EDGE_COLUMNS` --
unlike the native columns, the linked ones were introduced by
`link_edge_columns` using EdgeRaw's own names directly, so there's no
separate alias to track.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS

IDENTITY = ["Name", "Pos.", "Team", "Opp."]

# DK Sal/Pts/Val/Ceil/Own% are native (read straight off DkSalClean/
# TFFBOptoRaw, or self-computed for Val); CeilVal/Avail/Flag are linked
# (VLOOKUP against EdgeRaw). Leverage is NOT here -- Part 7.1 demotes it
# off the spine into the collapsed Ceiling detail group below, folded
# into this same reorder (see this module's own docstring).
DECISION = ["DK Sal", "Pts", "Val", "Ceil", "CeilVal", "Own%", "Avail", "Flag"]

# O/U/Spread/Team Implied/OppPosRank are native (VLOOKUP against oddsFinal/
# SoSComb); GameEnv is linked. The four `SoS 1..4` placeholders that used
# to sit here (Phase 3, reserved for strength-of-schedule before that data
# existed) are gone -- Phase 5 (2026-09-16) landed the real SoS sync
# (`sources/tffb_sos.py`) straight into `OppPosRank` itself, and Sam:
# "Why still sos 1-4. Should only be one per player" -- confirming the
# four reserved slots never had a real per-position use once the actual
# per-player value existed. See CONTRIBUTING.md's changelog for the real
# column deletion this required on PlayerPoolRaw/Player Pool/Lineups.
GAME = ["O/U", "Spread", "Team Implied", "GameEnv", "OppPosRank"]

# Phase 6, Part 2 + 7.1: CeilPct/OwnPct/LevBasis were already collapsed
# (the old INTERNAL zone below); Leverage joins them here now that it's
# off the spine. All four are linked (VLOOKUP against EdgeRaw).
CEILING_DETAIL = ["CeilPct", "OwnPct", "Leverage", "LevBasis"]

# All linked (VLOOKUP against EdgeRaw).
MOVEMENT = ["ImpliedMove", "TotMove", "SpdMove", "GameStart"]

# Venue is native (see module docstring); Stadium/Roof/Wind are linked.
WEATHER = ["Venue", "Stadium", "Roof", "Wind"]

# Id stays hidden outright, not part of any visible collapsed group
# (Part 2's own table lists it separately from the four numbered groups
# for exactly this reason) -- linked (VLOOKUP against EdgeRaw, same as
# every other column that used to share the old INTERNAL zone with it).
INTERNAL = ["Id"]

# Shared by all three tabs -- see PLAYER_POOL_RAW_COLUMN_ORDER/
# PLAYER_POOL_COLUMN_ORDER/LINEUPS_COLUMN_ORDER below for each tab's full
# header, including its own extra columns. Group order (GAME, CEILING_
# DETAIL, MOVEMENT, WEATHER) is Sam's own, left to right -- do not re-sort
# it (Part 2's explicit instruction).
BASE_COLUMN_ORDER = [*IDENTITY, *DECISION, *GAME, *CEILING_DETAIL, *MOVEMENT, *WEATHER, *INTERNAL]

# The linked (EdgeRaw-VLOOKUP) subset of BASE_COLUMN_ORDER, in header
# order -- `sheet_links.LINKED_EDGE_COLUMNS` re-exports this list; kept
# here so it can be defined once, next to the zones it's drawn from,
# instead of duplicated by hand in sheet_links.py. Venue deliberately
# excluded (native, see module docstring) despite sitting inside WEATHER.
LINKED_COLUMNS = [
    "CeilVal",
    "Avail",
    "Flag",
    "GameEnv",
    *CEILING_DETAIL,
    *MOVEMENT,
    "Stadium",
    "Roof",
    "Wind",
    *INTERNAL,
]

# PlayerPoolRaw has no tab-specific extras -- BASE_COLUMN_ORDER exactly.
PLAYER_POOL_RAW_COLUMN_ORDER = list(BASE_COLUMN_ORDER)

# Player Pool: "Source" (which of EdgeRaw/the add-a-player row a row came
# from) belongs beside the other identity-ish columns, so it's inserted
# right after Opp. (Venue no longer sits in IDENTITY -- see module
# docstring); "Edge ↗" (A3: a HYPERLINK straight to this player's row on
# EdgeRaw, so removing someone -- unchecking Pool there -- is one click
# away instead of a scroll/search through 743 rows) sits right next to
# it, since both are about managing this row rather than describing the
# player; "Pool" (Fix 2.11's surfaced Cash/GPP/Both value) and "Overflow"
# (the over-the-cap warning) are both about *this tab's own roster
# mechanics*, not a player attribute, so they stay appended at the very
# end regardless of what else moves around them. "Used"/"In" (Phase 5B:
# how many of THIS WEEK'S lineups roster this player, and which ones) are
# the newest addition and append-only past everything else, per
# `sheet_links.link_edge_columns`'s own append convention --
# `sheet_pool_usage.py` writes their formulas.
PLAYER_POOL_COLUMN_ORDER = [
    *IDENTITY,
    "Source",
    "Edge ↗",
    *DECISION,
    *GAME,
    *CEILING_DETAIL,
    *MOVEMENT,
    *WEATHER,
    *INTERNAL,
    "Overflow",
    "Pool",
    "Used",
    "In",
]

# Lineups: Part 2 moves "% of Own" (renamed from "% of Rstr") OUT of its
# old interspersed position (directly after Rstr%, inside DECISION) to
# sit with Lineups' other tab-specific columns, immediately after the
# full spine -- Part 2's own instruction: tab-specific columns sit
# "immediately after the spine, before the collapsed groups." "Issues"
# (A1: renamed from "Check") is the last thing you look at before
# trusting a lineup, so it sits right after "% of Own"; "Edge ↗" (A3,
# same HYPERLINK-to-EdgeRaw as Player Pool's own column of the same name)
# sits right after that -- Issues is exactly the moment you'd want to
# jump over and check/fix something on EdgeRaw.
LINEUPS_COLUMN_ORDER = [
    *IDENTITY,
    *DECISION,
    "% of Own",
    "Issues",
    "Edge ↗",
    *GAME,
    *CEILING_DETAIL,
    *MOVEMENT,
    *WEATHER,
    *INTERNAL,
]

assert set(LINKED_COLUMNS) <= set(EDGE_COLUMNS)  # noqa: S101 - internal self-check, not a public contract
assert len(BASE_COLUMN_ORDER) == len(set(BASE_COLUMN_ORDER))  # noqa: S101
assert len(PLAYER_POOL_COLUMN_ORDER) == len(set(PLAYER_POOL_COLUMN_ORDER))  # noqa: S101
assert len(LINEUPS_COLUMN_ORDER) == len(set(LINEUPS_COLUMN_ORDER))  # noqa: S101
