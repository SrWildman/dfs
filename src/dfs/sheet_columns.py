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
scheduled as its own step anywhere in `docs/planning/archive/HANDOFF_PHASE6.md`'s 7-step
order, it's the same category of change as this Part (column
positioning), and reordering the same three tabs twice in quick
succession is strictly more risk for no benefit. 7.1's OTHER pieces (the
Board panel change, the `docs/CALCULATIONS.md` note) are left for Step 4,
where the Board itself gets rebuilt (Part 3/7.6).

`Venue` moves out of IDENTITY into the Weather group -- it's demoted the
same as everything else not in the spine, and the spine deliberately does
not include it (see the spine list below). `ValAdj` was originally left
out of this module's spine list entirely (named only in Part 2's own
prose as a forward reference to work that hadn't shipped yet -- Sam had
already rejected the reserved-placeholder pattern once, the `SoS 1..4`
removal, Phase 5 Section K: "Why still sos 1-4. Should only be one per
player"). Part 7.2 (2026-09-18) built it for real: it's now a genuine
`DECISION` member, right after `Val`, linked (VLOOKUP against EdgeRaw,
`LINKED_COLUMNS` below) since it's a whole-slate regression residual, not
a per-row native formula -- inserted into an already-designed order via
the same `sheet_reorder.migrate_tab_to_designed_order` mechanism used for
`Edge ↗`/`Used`/`In` in earlier phases. `Flags` is no longer a
forward reference either -- Part 7.9 built it for real (see `DECISION`
below): `Flag` (singular) turned out, when checked, to already carry every
matching condition rather than the single first-match value Part 7.9's
own spec assumed, so the split is real, not just a rename.

Phase 6, Part 7.9 (2026-09-17), three more changes on top of Part 2's own
reorder: `OwnPct` dropped entirely (its only consumer anywhere in this
codebase was the Leverage formula in `derived.py`, verified by grep);
`LevBasis` renamed to `OwnStatus` (Leverage's demotion left it gating
`Own%`, a spine column, not describing Leverage); and `Flag`/`Flags`
split for real -- `Flags` (every matching condition, space-separated)
takes `Flag`'s old spine slot, while `Flag` (the single highest-priority
token) moves to the hidden zone alongside `Id`, kept rather than deleted
since other formatting/filtering logic keys off it as a boolean value.

Zone labels (2026-09-17, a usability fix raised mid-session, not in the
original spec): each of the four collapsed groups gets a real, always-
visible label column immediately before it (`derived.GAME_LABEL` = "GAME",
`CEILING_DETAIL_LABEL` = "CEIL", `MOVEMENT_LABEL` = "MOVE",
`WEATHER_LABEL` = "WX") -- Sam: "make sure I know what group is what
somehow and I'm not just clicking random stuff." Deliberately NOT members
of `GAME`/`CEILING_DETAIL`/`MOVEMENT`/`WEATHER` themselves (see those
lists' own definitions below) so `link_edge_columns`'s grouping never
folds a label into the range it collapses -- a label has to sit OUTSIDE
its own zone's range, since collapsing hides every cell inside it,
label included. This is also what makes the four zones independently
collapsible at all: verified live (a raw `addDimensionGroup` test on the
template's Scratch tab) that Sheets merges any adjacent same-depth column
groups into one, nesting included -- a real gap column is the only way to
get four separate `+`/`-` controls instead of one.

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

from dfs.derived import (
    ALL_PCT_COLUMNS,
    CEILING_DETAIL_LABEL,
    EDGE_COLUMNS,
    GAME_LABEL,
    MOVEMENT_LABEL,
    USAGE_LABEL,
    WEATHER_LABEL,
)
from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheet_lineup_metrics import LINEUP_METRIC_HEADERS
from dfs.usage_metrics import USAGE_METRIC_COLUMNS

IDENTITY = ["Name", "Pos.", "Team", "Opp."]

# DK Sal/Pts/Val/Ceil/Own% are native (read straight off DkSalClean/
# TFFBOptoRaw, or self-computed for Val); ValAdj/CeilVal/Avail/Flags are
# linked (VLOOKUP against EdgeRaw). Leverage is NOT here -- Part 7.1
# demotes it off the spine into the collapsed Ceiling detail group below,
# folded into this same reorder (see this module's own docstring). "Flags"
# (not "Flag" -- Part 7.9) is every matching condition, space-separated;
# the single highest-priority token lives on hidden "Flag" instead, in
# INTERNAL below. "ValAdj" (Part 7.2) sits right after "Val" -- `Val`'s
# replacement as the tool's primary sort, since `Val` is both salary- and
# position-biased. Unlike `Val` it can't be a native per-row formula: it's
# a per-position regression residual over the WHOLE slate, computed once
# in Python (Week 3, A3: a blend of within-position ProjPts/price-edge
# percentiles, `derived._val_adj_blend`) and linked here like every other
# EdgeRaw-computed signal.
# Part C, C5 (2026-09-24): `AggPts` inserted right after `Pts`, per Sam's
# own instruction (PROMPT_PART_C.md) -- linked (VLOOKUP against EdgeRaw,
# `LINKED_COLUMNS` below), since it's a whole-slate join/average over
# multiple sources, not a per-row native formula. Same insertion pattern
# `ValAdj` used right after `Val` (Part 7.2).
# Edge Finder (2026-10-07), per `PROMPT_EDGE_FINDER.md` F1: `CalPts` right after `AggPts`, `Hit3x%` and
# `Boom%` right after `ValAdj`, `Edge` (the token chips) right after `Flags` -- not merged into `Flags`, so
# bold names and flag priority are unchanged. All four are linked (VLOOKUP against EdgeRaw, where they
# are APPENDED after `NameKey`; display order here is independent of EdgeRaw's).
DECISION = [
    "DK Sal",
    "Pts",
    "AggPts",
    "CalPts",
    "Val",
    "ValAdj",
    "Hit3x%",
    "Boom%",
    "Ceil",
    "CeilVal",
    "Own%",
    "Avail",
    "Flags",
    "Edge",
]

# O/U/Spread/Team Implied/OppPosRank are native (VLOOKUP against oddsFinal/
# SoSComb); GameEnv is linked. The four `SoS 1..4` placeholders that used
# to sit here (Phase 3, reserved for strength-of-schedule before that data
# existed) are gone -- Phase 5 (2026-09-16) landed the real SoS sync
# (`sources/tffb_sos.py`) straight into `OppPosRank` itself, and Sam:
# "Why still sos 1-4. Should only be one per player" -- confirming the
# four reserved slots never had a real per-position use once the actual
# per-player value existed. See CONTRIBUTING.md's changelog for the real
# column deletion this required on PlayerPoolRaw/Player Pool/Lineups.
# `GameID`/`TmRank` (Part 7.4, 2026-09-18) are both linked -- whole-slate
# Python computations (a team-code join, a per-team-and-position salary
# rank), not per-row native formulas.
# `Pace`/`PROE`/`Expl%` (Part C, C7, 2026-09-25) sit right after `GameEnv`,
# which all three feed -- linked (VLOOKUP against EdgeRaw), a whole-slate
# join against nflverse's own play-by-play release, same as `GameID`/
# `TmRank`. Placed in GAME (not the spine, not USAGE) per Sam's own
# instruction: "all three go into the collapsed Game group."
GAME = [
    "O/U",
    "Spread",
    "Team Implied",
    "GameEnv",
    "Pace",
    "PROE",
    "Expl%",
    "OppPosRank",
    # Round 5 item 9: the opponent's EPA-per-play efficiency, beside OppPosRank.
    "OppEPA",
    "GameID",
    "TmRank",
]

# Phase 6, Part 2 + 7.1: CeilPct/LevBasis were already collapsed (the old
# INTERNAL zone below); Leverage joins them here now that it's off the
# spine. All three are linked (VLOOKUP against EdgeRaw). `OwnPct` used to
# sit here too -- dropped entirely in Part 7.9 (its only consumer
# anywhere in this codebase was the Leverage formula, verified by grep);
# `LevBasis` renamed to `OwnStatus` in that same pass (Leverage's own
# demotion left it gating `Own%`, a spine column, not describing
# Leverage -- the old name no longer said what it does).
# Edge Finder: the model's own `Floor`, `CeilM` and `Bust%` join the group (beside TFFB's `Ceil`/`Leverage`).
CEILING_DETAIL = ["CeilPct", "Leverage", "OwnStatus", "Floor", "CeilM", "Bust%"]

# All linked (VLOOKUP against EdgeRaw).
MOVEMENT = ["ImpliedMove", "TotMove", "SpdMove", "GameStart"]

# Venue is native (see module docstring); Stadium/Roof/Wind are linked.
WEATHER = ["Venue", "Stadium", "Roof", "Wind"]

# Part C, C6 (2026-09-24): positioned after WEATHER, per Sam's own
# instruction -- linked (VLOOKUP against EdgeRaw, a join against
# nflverse's own snap-count release, not a per-row native formula).
# Usage volume (2026-10-02): `Tgt%`/`WOPR`/`Rush%`/`RZ/G`/`HVT/G` sit beside `Snap%`, same group,
# same linking -- see `usage_metrics.py`.
USAGE = ["Snap%", *USAGE_METRIC_COLUMNS, "xFP/G"]  # xFP/G: Edge Finder (2026-10-07)

# Id/Flag stay hidden outright, not part of any visible collapsed group
# (Part 2's own table lists Id separately from the four numbered groups
# for exactly this reason) -- both linked (VLOOKUP against EdgeRaw). Flag
# joined Id here in Part 7.9, once "Flags" (in DECISION above) took over
# its old visible spine slot -- kept, not deleted, since other
# formatting/filtering logic keys off Flag's single-highest-priority
# value as a boolean/categorical key.
# Round 5 item 3: the five hidden percentile helpers join them -- each player
# metric's within-position standing, linked so the highlighting rules on every
# tab read the SAME number for the same player.
INTERNAL = ["Id", "Flag", *ALL_PCT_COLUMNS.values()]

# Shared by all three tabs -- see PLAYER_POOL_RAW_COLUMN_ORDER/
# PLAYER_POOL_COLUMN_ORDER/LINEUPS_COLUMN_ORDER below for each tab's full
# header, including its own extra columns. Group order (GAME, CEILING_
# DETAIL, MOVEMENT, WEATHER) is Sam's own, left to right -- do not re-sort
# it (Part 2's explicit instruction). Each zone's own label constant
# (GAME_LABEL, ...) sits immediately before it, OUTSIDE the zone's own
# list -- deliberately not a member of GAME/CEILING_DETAIL/MOVEMENT/
# WEATHER, so `sheet_links.link_edge_columns`'s grouping (which reads
# those lists directly) never includes a label in the collapsed range it
# names. The label's real job -- see `derived.ZONE_LABELS`' own comment --
# is to be the thing that's NOT collapsed, so it has to stay outside.
BASE_COLUMN_ORDER = [
    *IDENTITY,
    *DECISION,
    GAME_LABEL,
    *GAME,
    CEILING_DETAIL_LABEL,
    *CEILING_DETAIL,
    MOVEMENT_LABEL,
    *MOVEMENT,
    WEATHER_LABEL,
    *WEATHER,
    USAGE_LABEL,
    *USAGE,
    *INTERNAL,
]

# The linked (EdgeRaw-VLOOKUP) subset of BASE_COLUMN_ORDER, in header
# order -- `sheet_links.LINKED_EDGE_COLUMNS` re-exports this list; kept
# here so it can be defined once, next to the zones it's drawn from,
# instead of duplicated by hand in sheet_links.py. Venue deliberately
# excluded (native, see module docstring) despite sitting inside WEATHER.
LINKED_COLUMNS = [
    "AggPts",
    "CalPts",
    "ValAdj",
    "Hit3x%",
    "Boom%",
    "CeilVal",
    "Avail",
    "Flags",
    "Edge",
    "GameEnv",
    "Pace",
    "PROE",
    "Expl%",
    "OppEPA",
    "GameID",
    "TmRank",
    *CEILING_DETAIL,
    *MOVEMENT,
    "Stadium",
    "Roof",
    "Wind",
    *USAGE,
    *INTERNAL,
]

# PlayerPoolRaw has no tab-specific extras -- BASE_COLUMN_ORDER exactly.
PLAYER_POOL_RAW_COLUMN_ORDER = list(BASE_COLUMN_ORDER)

# Player Pool: "Source" (which of EdgeRaw/the add-a-player row a row came
# from) used to sit right after IDENTITY here -- removed entirely (Week 3
# feedback, A4, 2026-09-22): Sam had no use for it. "Edge ↗" (A3: a
# HYPERLINK straight to this player's row on EdgeRaw, so removing someone
# -- unchecking Pool there -- is one click away instead of a scroll/search
# through 743 rows) sits right after IDENTITY now, since it's about
# managing this row rather than describing the player. "Pool" (the live
# Cash/GPP/Both control, actions slice 1, 2026-10-09) is the FIRST column,
# left of Name like EdgeRaw's own Pool: it shows the player's EdgeRaw state
# and is the dropdown the bound Apps Script reads (blank it to remove him
# from the pool). It used to be appended after "Overflow" (the over-the-cap
# warning, which stays near the end: a roster-mechanics note, not a player
# attribute). "Used"/"In" (Phase 5B: how many of THIS WEEK'S lineups roster
# this player, and which ones) are append-only past everything else, per
# `sheet_links.link_edge_columns`'s own append convention --
# `sheet_pool_usage.py` writes their formulas. The hidden "Added" list
# (Week 3 feedback, A6) is retired: the add-a-player box sets EdgeRaw's
# Pool directly now (`sheet_pool_control.py`).
PLAYER_POOL_COLUMN_ORDER = [
    "Pool",
    *IDENTITY,
    "Edge ↗",
    *DECISION,
    GAME_LABEL,
    *GAME,
    CEILING_DETAIL_LABEL,
    *CEILING_DETAIL,
    MOVEMENT_LABEL,
    *MOVEMENT,
    WEATHER_LABEL,
    *WEATHER,
    USAGE_LABEL,
    *USAGE,
    *INTERNAL,
    "Overflow",
    "Used",
    "In",
]

# Lineups: Part 2 moved "% of Rstr" OUT of its old interspersed position
# (directly after Rstr%, inside DECISION) to sit with Lineups' other
# tab-specific columns, immediately after the full spine -- Part 2's own
# instruction: tab-specific columns sit "immediately after the spine,
# before the collapsed groups." Part 7.9 renamed it again, "% of Rstr" ->
# "% of Own" (Part 2) -> "% of Cap" (Part 7.9, once the real cap-
# allocation meaning was confirmed) -- position unchanged both times.
# "Issues" (A1: renamed from "Check") is the last thing you look at
# before trusting a lineup, so it sits right after "% of Cap". Part 7.5's
# lineup-metrics columns (Games, Min Unique; `sheet_lineup_metrics.
# LINEUP_METRIC_HEADERS`) sit right after Issues. (The simulator's four numbers no longer have columns here:
# since the usability round they sit on each Total row, `sheet_lineup_sim`.) "Edge ↗" (A3, same
# HYPERLINK-to-EdgeRaw as Player Pool's own column of the same name)
# still last in this run, since it's about managing THIS row, not
# describing the lineup.
LINEUPS_COLUMN_ORDER = [
    *IDENTITY,
    *DECISION,
    "% of Cap",
    "Issues",
    *LINEUP_METRIC_HEADERS,
    "Edge ↗",
    GAME_LABEL,
    *GAME,
    CEILING_DETAIL_LABEL,
    *CEILING_DETAIL,
    MOVEMENT_LABEL,
    *MOVEMENT,
    WEATHER_LABEL,
    *WEATHER,
    USAGE_LABEL,
    *USAGE,
    *INTERNAL,
    # Round 5 follow-up item 3: hidden canonical-name key the duplicate/exposure counts
    # compare (`sheet_lineup_keys.py`). Appended last so nothing to its left moves.
    LINEUP_KEY_HEADER,
]

assert set(LINKED_COLUMNS) <= set(EDGE_COLUMNS)  # noqa: S101 - internal self-check, not a public contract
assert len(BASE_COLUMN_ORDER) == len(set(BASE_COLUMN_ORDER))  # noqa: S101
assert len(PLAYER_POOL_COLUMN_ORDER) == len(set(PLAYER_POOL_COLUMN_ORDER))  # noqa: S101
assert len(LINEUPS_COLUMN_ORDER) == len(set(LINEUPS_COLUMN_ORDER))  # noqa: S101
