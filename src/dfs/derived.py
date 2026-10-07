"""Pure functions computing the "edge" signals from already-synced sources.

Design principle from docs/planning/ROADMAP.md Phase 1: a column that shows two
numbers is worse than one that says *look at this*. Every score computed
here ends up sortable (Leverage, CeilVal, GameEnv) or turned into a short
flag token (Avail, Flag) -- not just a raw number restated.

Everything here is offline-testable (no network, no Sheets): `edge.py`'s
EdgeSource is the thin, untested wrapper that reads already-synced CSVs from
`store.load_current()` and calls into this module, per CONTRIBUTING.md's
"split the pure logic out" rule.

Join: TFFB's projections `Id` is DraftKings' own player ID (verified 742/742
exact overlap against a live pull) -- no name matching needed for the
projections<->salaries join. A TFFB row without a DK salary match is
*reported* (via EdgeBuildResult.unmatched_names), never silently dropped --
this can legitimately happen since TFFB's optimizer isn't scoped to "DK's
main Sunday slate only" the way dk_salaries.py is, so a Thursday/Monday-only
player can show up projected with no match in that week's main-slate pull.

GameEnv is computed only from the Vegas context TFFB already attaches to
each player (OU/Spread) -- deliberately *not* cross-referenced against the
separately-synced nfl_odds source in this first pass, since nfl_odds rows
are keyed by team nickname/abbreviation (`rotowire_odds.py`'s `team`/`abbr`
columns) rather than DK's team codes, and building that name-matching layer
just to double-check numbers TFFB already provides isn't worth the join risk
yet.

Stadium/Roof/Wind, added in Phase 2, *do* use the clean team-code join
`nflverse_games` provides: each player's `Team` is looked up against
`GamesRaw`'s Away/Home columns (already normalized to DK's team codes by
`nflverse_games.py`), and `WeatherRaw` is then joined on GameId. Both
`games`/`weather` are optional -- a week without them synced still produces
the exact same EdgeRaw column set, just with those columns blank, because
a tab whose column *count* changes week to week is the precondition for
the Phase 8 formula-shift bug (see CONTRIBUTING.md) if it's ever pasted
into a sheet with hardcoded column references.

ImpliedMove/TotMove/SpdMove (ImpliedMove added in Phase 3 as "LineMove", renamed
and joined by TotMove/SpdMove in Fix 2.2) join `line_movement.diff_odds()`'s
output by team code (`Abbr`, already DK-compatible -- `rotowire_odds.py`'s
own `abbr` field) directly onto `Team`, no intermediate game lookup
needed. `diff_odds()` always computed all three deltas (team implied
points, game total, spread); only the first ever reached EdgeRaw before
this fix, under a name that didn't say which line had moved. Also
optional, same blank-not-omitted rule as everything else in this
paragraph.

This diffs against the *last sync* originally (`store.load_previous`) --
reverted in Phase 5 after real use showed that's the wrong baseline: it
depends entirely on how often you happen to run `dfs sync`, so the exact
same real move could show as a big number or nothing depending on sync
cadence, which isn't a signal, it's noise. It now diffs against the
*start of the current NFL week* instead (`store.load_since` +
`nfl_calendar.week_start_date`), a fixed baseline that means the same
thing regardless of sync frequency. `GameStart`, added alongside it in
Phase 5, needs no attach step at all: TFFB's projections.csv already
carries it (an ISO-8601 UTC kickoff time) straight through the join, it
just wasn't kept in EDGE_COLUMNS' final column selection before now.
Backs `dfs lineups late-swap`'s lock-time check.

See docs/CALCULATIONS.md for the full worked explanation of every column
here, including Val/CeilVal/CeilPct/Leverage/GameEnv/Flag -- this
docstring covers the *why* of the design, that doc covers the exact
formula for anyone who just wants to verify a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.line_movement import FLAG_IMPL_DOWN, FLAG_IMPL_UP, LINE_MOVE_FLAG_THRESHOLD
from dfs.player_join import JoinResult, join_source_to_dk, normalize_name
from dfs.team_metrics import GAME_ENV_WEIGHTS, combined_by_game, weighted_mean_skipna
from dfs.usage_metrics import USAGE_METRIC_COLUMNS

# Leverage = CeilPct - OwnPct, both percentile ranks on the same 0-100
# scale within position -- see the module docstring's "second scale/index
# bug" postmortem for why this replaced a raw-percentage subtraction.
# `OwnPct` itself is dropped from EDGE_COLUMNS (Part 7.9: its only
# consumer anywhere in this codebase was this one subtraction -- verified
# by grep before removing) but stays a real pandas column here, computed
# and used exactly as before; only the sheet-facing output changed.
# OwnStatus's one remaining job: telling you whether ProjOwn has actually
# been published yet. Until it has (every player reads 0), there is no
# real ownership signal to rank against, so Leverage is left BLANK rather
# than showing a number that looks like leverage but isn't -- a confident
# wrong number is worse than an empty cell. The frame still sorts usefully
# in that window, just by CeilPct instead (see build_edge_frame).
# Renamed from LevBasis (Part 7.9): with Leverage demoted off the spine
# (Part 7.1), this marker's real job is gating `Own%`, a spine column --
# the old name no longer said what it does.
OWN_STATUS_REAL = "real"
OWN_STATUS_UNPUBLISHED = "unpublished"

# LEVERAGE flag (2026-09-24): a fixed threshold on Leverage mixed two
# problems. First, it was never restricted to the rosterable pool -- on
# the 2026-09-20 15:51 UTC snapshot, 10 of 30 flags at threshold 30 were
# backup players outside `VAL_ADJ_ROSTERABLE_TOP_N` (nine $2,500-$2,800
# backup TEs plus one injury-limited Brock Bowers), all with CeilPct
# inflated by the same backup-pile problem ValAdj's Fix 2 already solved
# for ValAdj itself -- CeilPct/OwnPct are percentiles across every player
# DK lists, so a backup projecting 4.5 points looks like a ~75th-
# percentile ceiling. Second, even restricted to the pool, a fixed
# threshold drifted week to week: at 30, the pool fire rate was 12-15% in
# Week 1 (9/10-9/15 snapshots) and 5-8% in Week 2 (9/19-9/20 snapshots) --
# the same failure mode LINE had before it was retuned.
#
# Fix: only pool members are eligible, and the flag goes to the top
# `LEVERAGE_FLAG_TOP_SHARE` of the pool by Leverage each week (see
# `_leverage_flag_cutoff`), not a fixed number. That's self-correcting by
# construction -- roughly 17-18 of 250 pool players every week, whatever
# the raw Leverage distribution looks like that week.
LEVERAGE_FLAG_TOP_SHARE = 0.07
# Confirmed against the same slate: 5/744 players (0.7%) clear this today.
# An absolute ownership percentage, not a percentile -- correctly untouched
# by the Leverage scale fix. On the fraction scale (Phase 6, Part 2 --
# ProjOwn is now 0-1, not 0-100, to share one stored scale with
# PlayerPoolRaw's native Own%/Rstr%): 20% is 0.20, not 20.0.
CHALK_OWNERSHIP_THRESHOLD = 0.20

# SPLIT rework (2026-09-25): the original C5b rule (raw gap vs. a fixed
# floor/percentage-of-ProjPts threshold) fired almost entirely on backups,
# not on real disagreement. CORRECTION, since a prior comment here was
# wrong and stated as fact: that comment claimed "the systematic RB/WR gap
# ... Sam already accepted" as the reason for the one-sided firing -- Sam
# had accepted that the RB/WR calibration gap EXISTS and that AggPts
# should ship with it (2026-09-24, "ship it, note the caveat" -- see
# docs/CALCULATIONS.md's own AggPts section), but nobody had separately
# decided whether it was fine for that gap to dominate SPLIT specifically.
# Nobody had, until this rework.
#
# The actual finding (2026-09-25, `data/raw/edge/20260925T191419Z.csv`):
# TFFB agrees closely with Sleeper/FantasyPros on STARTERS (RB ranks 1-12
# differ by about +0.3 points) but runs systematically higher than both on
# deep RB/WR backups (ranks 41-64 differ by about -3.0) -- a real, stable
# difference between the models at the roster's bottom, not a scoring bug
# (hand-checked `dk_scoring` against stored components directly). Raw-gap
# SPLIT fired 15 times on that snapshot; 12 of the 15 were SPLIT↓ on
# $3,400-$4,800 RBs/WRs -- the same handful of backups every week, not a
# useful "look closer" signal.
#
# Fix: flag disagreement beyond the USUAL gap for a position and
# projection level, not raw points -- fit `gap ~ ProjPts` per position
# (OLS, over rosterable-pool players with a source, the same shape
# `_val_adj_residual_within_position` already uses for ValAdj), and flag
# on the RESIDUAL from that line (`_split_residual_within_position`), not
# the gap itself. `SPLIT_ABS_FLOOR`/`SPLIT_REL_THRESHOLD` are gone --
# nothing reads them any more.
#
# `SPLIT_FLAG_TOP_SHARE = 0.07`: the top 7% of pool players with a
# residual, by |residual|, slate-wide (not per position) -- same quantile
# shape `LEVERAGE_FLAG_TOP_SHARE` already uses, ties at the cutoff
# included. `SPLIT_MIN_RESIDUAL = 2.0`: an absolute floor below the
# quantile -- a week where every source agrees closely shouldn't
# manufacture flags just to fill 7%. `SPLIT_MIN_FIT_PLAYERS = 8`: a
# position with fewer than this many pool players carrying a source
# doesn't get a fitted line at all (too few points to trust a slope) --
# skipped entirely (no residual, no flag, a printed warning), rather than
# fit against a handful of players and pretend the resulting line means
# anything.
SPLIT_FLAG_TOP_SHARE = 0.07
SPLIT_MIN_RESIDUAL = 2.0
SPLIT_MIN_FIT_PLAYERS = 8
# Flag text (Sam, 2026-09-30: say what it means, keep it short). The arrow is TFFB's own
# position against the other sources, NOT the old SPLIT↑/SPLIT↓ sense -- those arrows pointed
# at the OTHER sources (↑ = others higher), so the old ↑ is now `TFFB↓` and vice versa.
SPLIT_TFFB_HIGH = "TFFB↑"  # TFFB projects this player higher than Sleeper/FantasyPros do
SPLIT_TFFB_LOW = "TFFB↓"  # TFFB projects this player lower than Sleeper/FantasyPros do
# Phase 6, Part 1.4: `has_real_ownership` used to be `.any()` -- a single
# non-zero ProjOwn (one early-published player, a data glitch, a bye-week
# artifact) flipped the WHOLE slate to "real," computing OwnPct/Leverage as
# a percentile over a column that's still ~99% zeros for everyone else.
# Live symptom, reproduced before this fix: LevBasis read "real" while
# every ProjOwn on EdgeRaw still read 0.0% and every Leverage cell was
# blank. A share threshold instead requires ownership to be genuinely
# published for a majority of the slate before trusting it.
#
# Week 3 follow-ups, Item 4 (2026-09-23): "majority of the slate" was still
# measured over every player DraftKings lists, not the VAL_ADJ_ROSTERABLE_
# TOP_N pool -- but TFFB only ever publishes ownership for players who will
# actually be rostered, so that share tops out around 38% and can never
# cross 0.5. Live symptom: OwnStatus read "unpublished" and Leverage was
# blank all season, on every snapshot, even ones where ownership had
# clearly published. Verified on the real 2026-09-20 15:51 UTC snapshot
# (`data/raw/edge/20260920T155118Z.csv`, 668 players, 255 with ProjOwn >
# 0): 38% over the whole list (reads unpublished) vs. 90% over the 250-
# player rosterable pool (clearly published). Same denominator mistake as
# ValAdj (Fix 2) and the same fix: measure the share against the pool, not
# the full DK list.
OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5
OUT_STATUSES = frozenset({"OUT", "IR"})
# Mirrors sources/weather.py's WIND_FLAG_THRESHOLD_MPH. Duplicated rather
# than imported so derived.py (pure, source-agnostic logic) never depends
# on a specific source module -- sources depend on derived.py, not the
# other way around.
WIND_FLAG_THRESHOLD_MPH = 20.0

# Phase 6, Part 3 (2026-09-22): the Board's "Slate shape" section flags a
# game as a probable shootout by its Vegas total. 48 is a standard DFS
# heuristic, not measured against real data the way `LEVERAGE_FLAG_
# THRESHOLD` was re-tuned against a real slate -- a first-pass number
# Sam should sanity-check once he's looked at a few real weeks, same as
# that one was.
SHOOTOUT_TOTAL_THRESHOLD = 48.0

# Week 3 feedback (A3): weight given to raw projected-points percentile
# vs. the price-edge residual percentile in ValAdj's blend -- see
# `_val_adj_blend`'s docstring for why a plain residual isn't enough on
# its own. A named constant, not a literal, since Sam may want to shift
# it toward projection later without a code review to find the number.
VAL_ADJ_PROJECTION_WEIGHT = 0.5

# Week 3 fixes, Fix 2 (2026-09-23): the reference population ValAdj's two
# percentiles (PtsPct, EdgePct) and its price-edge regression line are
# computed against -- roughly the starters league-wide at each position.
# Found live: both percentiles used to be computed across EVERY player
# DraftKings lists at the position, including backups projecting near
# zero -- the deeper a position's backup pile, the more its mid-tier
# players get inflated (measured on the 2026-09-23 slate: a 5.7-pt TE
# landed at the 82nd percentile at TE, where 71% of listed TEs project
# under 2 pts, vs. 66th at WR, where only 57% do -- the metric was
# measuring "better than the backup pile," not "a good play"). Sam's
# fix, decided live: rank against ROSTERABLE players only. If a position
# has fewer players than its own N (DST had 26 on that slate), the pool
# is simply all of them -- see `_rosterable_pool_mask`.
VAL_ADJ_ROSTERABLE_TOP_N = {"QB": 32, "RB": 64, "WR": 96, "TE": 32, "DST": 32}

# EdgeRaw's real sheet layout is [Pool, *EDGE_COLUMNS] -- Pool sits in
# column A (so it's beside Name once Id, immediately after it, is hidden),
# not appended after EDGE_COLUMNS the way it first shipped. Every module
# that turns an EDGE_COLUMNS index into an absolute EdgeRaw column letter
# (sheet_links.py, sheet_style.py, sheet_pool_formulas.py, doctor.py) adds
# this offset -- `sources/edge.py`'s own POOL_COLUMN is the one column
# that ISN'T offset, since it's the thing the offset makes room for.
# Purely relative math (e.g. sheet_links._vlookup_index, computed as a
# distance from Name) is unaffected by a uniform shift and doesn't need it.
EDGE_DATA_OFFSET = 1

# The EdgeRaw tab's column order (as data columns; the real sheet position
# of each is `EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET`) -- exposed so
# `sheet_style.polish_edge` can locate a column by name without an extra
# round-trip read of the sheet.
#
# Phase 6, Part 2 (2026-09-17): redesigned into a shared SPINE (Name/
# Position/Team/Opp/Salary/ProjPts/Val/Ceiling/CeilVal/Own%/Avail/Flag --
# `sheet_columns.py`'s DECISION for the other three tabs, same grammar,
# each tab's own established spelling) visible identically across
# EdgeRaw/Player Pool/Lineups, with everything else behind collapsed
# groups in Sam's own fixed left-to-right order: GAME, CEILING DETAIL,
# MOVEMENT, WEATHER. `Id` stays hidden outright, not part of any visible
# group. `Own%` is the one deliberately shared name -- this column was
# `ProjOwn` here and `Rstr%` on the other three tabs, "the same value
# under two names" a shared spine can't have; renamed to `Own%`
# EVERYWHERE. Internally this module still computes and reasons about a
# `ProjOwn` pandas column throughout (TFFB's own field name, the real
# external data source) -- the rename to `Own%` happens ONLY at the very
# end, selecting into this list (see `build_edge_frame`'s return), so
# every internal formula/threshold/test below is unaffected. `Leverage`
# moves into the collapsed CEILING DETAIL group (Part 7.1's decision,
# folded into this same reorder -- see `sheet_columns.py`'s own docstring
# for why). Nothing deleted; every name below already existed, just
# renamed/repositioned. See CONTRIBUTING.md's structural changelog for
# the full before/after.
# Zone labels (2026-09-17, post-Part-7.9 usability fix): a real, always-
# visible column immediately before each collapsed zone, naming what's
# inside -- Sam: "make sure I know what group is what somehow and I'm not
# just clicking random stuff." A label CANNOT live inside the zone it
# names (collapsing a group hides every cell in its range, the label
# included), and it can't be a blank spacer either (an unlabeled `+`
# control is exactly the "guess and click" problem being fixed) -- so
# each zone gets its own narrow, native, no-formula label column, text
# only in the header row, blank below. This also happens to be what makes
# independent per-zone collapse possible at all: verified live (a raw
# `addDimensionGroup` test on the template's Scratch tab, depth 1 wrapping
# a range and depth 2 per-zone inside it) that Sheets merges adjacent
# same-depth groups into one regardless of nesting -- there is no way to
# get 4 independently-collapsible zones without a REAL gap between them.
GAME_LABEL = "GAME"
CEILING_DETAIL_LABEL = "CEIL"
MOVEMENT_LABEL = "MOVE"
WEATHER_LABEL = "WX"
# Part C, C6 (2026-09-24): a fifth collapsed group, positioned after
# WEATHER per Sam's own instruction -- his existing Game/Ceiling detail/
# Movement/Weather left-to-right order is untouched; he can move Usage
# later if he wants. Un-abbreviated like GAME (not a 2-4 letter shorthand
# like CEIL/MOVE/WX) since "Usage" is already short.
USAGE_LABEL = "USAGE"

# Round 5 item 3: `metric -> hidden percentile column` (within-position, over the
# rosterable pool `_rosterable_pool_mask`, zeros/blanks excluded). The five
# player-performance metrics whose raw value is NOT comparable across positions, plus (usage
# work, 2026-10-02) the five usage volume columns, which are compared within position too: a
# 25% target share means one thing for a WR and another for an RB.
PLAYER_METRIC_PCT_COLUMNS = {
    "ProjPts": "ProjPts%ile",
    "AggPts": "AggPts%ile",
    "Ceiling": "Ceiling%ile",
    "Val": "Val%ile",
    "CeilVal": "CeilVal%ile",
    "Tgt%": "Tgt%ile",
    "WOPR": "WOPR%ile",
    "Rush%": "Rush%ile",
    "RZ/G": "RZ/G%ile",
    "HVT/G": "HVT/G%ile",
}

# Edge Finder columns (see `edge_finder.py`, `calibration.py`, `probabilities.py`, `xfp.py`).
# `CalPts%ile` / `xFP/G%ile` are hidden within-position helpers, like `PLAYER_METRIC_PCT_COLUMNS`' but kept
# separate so they APPEND after `NameKey` instead of shifting it.
EDGE_FINDER_PCT_COLUMNS = {"CalPts": "CalPts%ile", "xFP/G": "xFP/G%ile"}
EDGE_FINDER_VALUE_COLUMNS = ["CalPts", "Hit3x%", "Boom%", "Bust%", "Floor", "CeilM", "xFP/G", "Edge"]
EDGE_FINDER_COLUMNS = [*EDGE_FINDER_VALUE_COLUMNS, *EDGE_FINDER_PCT_COLUMNS.values()]
# Every hidden within-position percentile helper, old and new.
ALL_PCT_COLUMNS = {**PLAYER_METRIC_PCT_COLUMNS, **EDGE_FINDER_PCT_COLUMNS}

EDGE_COLUMNS = [
    # SPINE
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "ProjPts",
    # Part C, C5 (2026-09-24): equal-weight mean of every DK-scored source
    # with a real projection for this player (TFFB/`ProjPts`, Sleeper,
    # FantasyPros) -- see `_attach_agg_pts`. Placed immediately after
    # `ProjPts` per Sam's own instruction (PROMPT_PART_C.md); every column
    # from `Val` onward shifts one position right on PlayerPoolRaw/Player
    # Pool/Lineups (see CONTRIBUTING.md's structural changelog). Feeds
    # nothing else -- ValAdj/Val/CeilVal/the Board/every guardrail still
    # key off `ProjPts` alone, unchanged.
    "AggPts",
    "Val",
    # Part 7.2: `Val` is salary- and position-biased (cheap players and
    # QBs both artificially outrank better plays), so it stays but is no
    # longer the tool's primary sort -- `ValAdj` is. See
    # `_val_adj_blend` below for the current (Week 3, A3) formula.
    "ValAdj",
    "Ceiling",
    "CeilVal",
    "Own%",
    "Avail",
    # Part 7.9: "Flag" (below, hidden) turned out to ALREADY be every
    # matching condition, space-separated (Fix 2.1, an earlier session) --
    # not the first-match-only value 7.9's own text assumed. Verified with
    # Sam directly rather than guessed: split for real, not just renamed.
    # "Flags" is the one on the spine now -- everything that fired, for
    # reading.
    "Flags",
    # GAME (collapsed) -- GAME_LABEL sits immediately before it, outside
    # the collapsed range, always visible.
    GAME_LABEL,
    "OverUnder",
    "Spread",
    "GameEnv",
    # Part C, C7 (2026-09-25): this player's own TEAM's season-to-date
    # offense metrics from nflverse play-by-play, blended with last
    # season's full-season value early on -- see `sources/nflverse_pbp.py`/
    # `team_metrics.py`. Placed right after `GameEnv` since all three feed
    # it (see `_game_env_scores`); `Expl%` stays a readable column on its
    # own and is deliberately NOT one of GameEnv's inputs (C7's own
    # instruction).
    "Pace",
    "PROE",
    "Expl%",
    # `ModelImplied` (GPS, 2026-09-26) was REMOVED in Round 5 item 5c: the
    # worksheet's `Implied Total` turned out to be Vegas, not a model (see
    # `gps_check.py`), so the column only ever repeated the market.
    # Sam: "all data should be in edge raw" -- computed the same way
    # PlayerPoolRaw's own (now-fixed) `OppPosRank` is, but natively in
    # Python from the already-synced sos_qb/rb/wr/te/dst CSVs rather than
    # a live Sheets formula, matching EdgeRaw's own "computed locally, no
    # live formulas" design. See `_attach_opp_pos_rank` below.
    "OppPosRank",
    # Round 5 item 9: a steadier matchup signal than `OppPosRank` (TFFB's
    # fantasy-points-allowed rank, very noisy this early) -- the opponent's
    # EPA-per-play efficiency from our own play-by-play. One column, the input
    # depends on position; see `_attach_opp_epa` and docs/CALCULATIONS.md.
    # Higher = a softer matchup for EVERY position.
    "OppEPA",
    # Part 7.4 (2026-09-18): makes stacks visible. `GameID` was already
    # computed internally (`_attach_games`, as `GameId`) to join Stadium/
    # Roof/Wind, then dropped before this -- now kept and renamed to match
    # this column's own header text. `TmRank` is new: this player's
    # salary rank within his own team AND position (1 = highest-salaried
    # at that position on that team -- "WR1", "RB1", read alongside
    # Position) -- a crude but serviceable proxy for target hierarchy,
    # not a measured one. See `_tm_rank_within_team_position` below and
    # docs/CALCULATIONS.md for why it's labelled a proxy.
    "GameID",
    "TmRank",
    # CEILING DETAIL (collapsed) -- CeilPct/OwnStatus were already
    # collapsed together (the old INTERNAL group); Leverage joins them
    # here now that it's off the spine (Part 7.1). `OwnPct` dropped
    # entirely (Part 7.9) -- see the constant section above.
    CEILING_DETAIL_LABEL,
    "CeilPct",
    "Leverage",
    "OwnStatus",
    # MOVEMENT (collapsed)
    MOVEMENT_LABEL,
    "ImpliedMove",
    "TotMove",
    "SpdMove",
    "GameStart",
    # WEATHER (collapsed)
    WEATHER_LABEL,
    "Stadium",
    "Roof",
    "Wind",
    # USAGE (collapsed, Part C, C6, 2026-09-24) -- positioned after
    # WEATHER per Sam's own instruction. Linked (VLOOKUP against EdgeRaw),
    # like every other collapsed-group metric here: a whole-slate-and-
    # history join against nflverse's own snap-count release, not a
    # per-row native formula.
    USAGE_LABEL,
    "Snap%",
    # Usage volume (2026-10-02), over each player's last 3 games played (`usage_metrics.py`):
    # `Tgt%`/`WOPR` for pass-catchers, `Rush%` for RB/QB, red-zone and high-value touches per
    # game. Blank where a metric doesn't apply, never 0. Linked like `Snap%`.
    *USAGE_METRIC_COLUMNS,
    # Id/Flag stay hidden outright, not part of any visible group -- Pool
    # (column A, ahead of this whole list) sits directly beside Name with
    # no column between them. "Flag" (Part 7.9) is the single
    # highest-priority token only -- kept, not deleted, since other
    # formatting/filtering logic keys off it as a boolean/categorical
    # value; "Flags" (on the spine, above) is what a person reads.
    "Id",
    "Flag",
    # Round 5 item 3: each player metric's standing within his position, hidden
    # helpers the highlighting rules read (see `PLAYER_METRIC_PCT_COLUMNS`).
    *PLAYER_METRIC_PCT_COLUMNS.values(),
    # Round 5 item 6: `player_join.normalize_name(Name)` -- the key typed names
    # are matched against (see `sheet_names.py`). Hidden, appended last.
    "NameKey",
    # Edge Finder (2026-10-07), APPENDED after NameKey (EDGE_COLUMNS is append-only): the calibrated
    # projection, outcome probabilities, xFP and the Edge token chips, plus two hidden within-position
    # percentile helpers. Filled by `attach_edge_finder_columns` after the build (they need the results
    # history, UM and the free nflverse files); blank until then. Display order on Player Pool, Lineups
    # and PlayerPoolRaw comes from `sheet_columns.py`.
    *EDGE_FINDER_COLUMNS,
]

# The four zone labels, in the same left-to-right order they appear --
# used wherever code needs "all the label columns" as a group (e.g. to
# exclude them from formatting that only makes sense for real metrics).
ZONE_LABELS = (GAME_LABEL, CEILING_DETAIL_LABEL, MOVEMENT_LABEL, WEATHER_LABEL, USAGE_LABEL)


@dataclass
class EdgeBuildResult:
    frame: pd.DataFrame
    unmatched_names: list[str]
    # Part C, C1: one JoinResult per external source actually passed to
    # `build_edge_frame` (keyed "sleeper"/"fantasypros"/"snaps") -- lets
    # the caller (sources/edge.py) print C1's own required "match rate
    # per source per position" report and write unmatched rosterable
    # players to a file, without re-running the join itself. Empty when
    # no source was passed in (e.g. `dfs sync --only edge` before any
    # has ever synced).
    source_joins: dict[str, JoinResult]
    # SPLIT rework (2026-09-25): positions `_split_residual_within_position`
    # skipped for having fewer than `SPLIT_MIN_FIT_PLAYERS` rosterable-pool
    # players with a source -- lets the caller print a warning rather than
    # silently never flagging that position. Empty in the common case
    # (every position has enough data, or neither external source synced).
    split_skipped_positions: list[str]


def _percentile_within(series: pd.Series, group: pd.Series) -> pd.Series:
    """Percentile rank (0-100) of `series` within each `group` value. NaN
    inputs stay NaN in the output -- pandas' rank() already skips them,
    which is exactly what we want for Ceiling's ~40% missing rows."""
    return series.groupby(group).rank(pct=True) * 100


def _tm_rank_within_team_position(
    salary: pd.Series, name: pd.Series, team: pd.Series, position: pd.Series
) -> pd.Series:
    """Part 7.4: this player's salary rank within his own TEAM and
    POSITION -- 1 is the highest-salaried player at that position on that
    team ("WR1", "RB1", read alongside `Position`). A crude but
    serviceable proxy for target hierarchy -- salary reflects the
    market's OWN belief about usage, not a measured target share -- see
    docs/CALCULATIONS.md for why this is never to be read as the latter.

    Ranked by Salary descending, ties broken by Name ascending so the
    result is deterministic regardless of the frame's own row order
    (which itself changes every sync, since EdgeRaw is sorted by
    `ValAdj`) -- two players priced identically at the same team/position
    is rare but real (a full slate of backups at the veteran minimum),
    and an arbitrary tiebreak would make "WR1"/"WR2" flip between syncs
    for no real-world reason."""
    frame = pd.DataFrame({"Salary": salary, "Name": name, "Team": team, "Position": position})
    ordered = frame.sort_values(["Salary", "Name"], ascending=[False, True])
    ordered["TmRank"] = ordered.groupby(["Team", "Position"]).cumcount() + 1
    return ordered["TmRank"].reindex(frame.index)


def _rosterable_pool_mask(proj_pts: pd.Series, position: pd.Series) -> pd.Series:
    """True for the top `VAL_ADJ_ROSTERABLE_TOP_N[position]` players by
    `ProjPts` within each position -- the reference population ValAdj's
    percentiles and residual regression are computed against (Fix 2). A
    position with fewer players than its own N (e.g. DST) gets every row
    marked True -- the pool is simply all of them. Ties at the cutoff
    aren't specially expanded; a stable sort keeps this deterministic run
    to run for a fixed input frame."""
    proj_pts = pd.to_numeric(proj_pts, errors="coerce")
    mask = pd.Series(False, index=proj_pts.index)
    for pos_value in position.unique():
        idx = position.index[position == pos_value]
        n = VAL_ADJ_ROSTERABLE_TOP_N[pos_value]
        top_idx = proj_pts.loc[idx].sort_values(ascending=False, na_position="last").index[:n]
        mask.loc[top_idx] = True
    return mask


def _leverage_flag_eligible(leverage: pd.Series, pool_mask: pd.Series) -> pd.Series:
    """True for pool members (`pool_mask`) in the top `LEVERAGE_FLAG_
    TOP_SHARE` of the pool by `Leverage`, slate-wide (not per position --
    PROMPT_LEVERAGE_FLAG.md doesn't scope this by position the way
    ValAdj's pool is built per position). Non-pool players are never
    eligible whatever their Leverage. `k = ceil(pool_size * LEVERAGE_FLAG_TOP_SHARE)`
    (minimum 1 if the pool has any non-blank Leverage at all); the cutoff
    is the k-th largest pool Leverage value, and every pool row at or
    above that value is eligible -- so a tie at the cutoff is included,
    not arbitrarily broken, and the flagged count can run slightly above
    k when there's a tie there."""
    pool_leverage = pd.to_numeric(leverage, errors="coerce").loc[pool_mask.index[pool_mask]].dropna()
    eligible = pd.Series(False, index=leverage.index)
    if pool_leverage.empty:
        return eligible
    k = max(1, math.ceil(len(pool_leverage) * LEVERAGE_FLAG_TOP_SHARE))
    cutoff = pool_leverage.nlargest(k).min()
    eligible.loc[pool_leverage.index] = pool_leverage >= cutoff
    return eligible


def _val_adj_residual_within_position(
    proj_pts: pd.Series, salary: pd.Series, position: pd.Series, pool_mask: pd.Series
) -> pd.Series:
    """Part 7.2: `residual = ProjPts - E[ProjPts | Salary, Position]` --
    fit a plain OLS line of `ProjPts` on `Salary` *within each position, on
    this slate's own projections*, and take the residual: "is this player
    projected above what this slate's own pricing implies for his
    position." Version 2 (refit against realized points once the results
    loop exists, additionally surfacing where the market is systematically
    wrong) is a deliberate later step, not built here.

    Week 3 feedback (A3), found live: this residual used to BE `ValAdj`
    directly, and that was the bug Sam flagged -- "cheap players float too
    high." A residual is scale-free, so a $3,200 RB beating his price by
    +1.3 outranked an $8,200 RB missing his by -0.2, even though the cheap
    player can't win a lineup and the expensive one can. This function's
    output is now only an internal input to `_val_adj_blend` (via its own
    within-position percentile, `EdgePct`) -- see that function for the
    fix. Still called "residual," not "ValAdj," to make that clear at
    every call site.

    Week 3 fixes, Fix 2 (2026-09-23): the OLS line is fit on `pool_mask`
    (`VAL_ADJ_ROSTERABLE_TOP_N`, roughly the starters league-wide) ONLY,
    not the whole position -- fitting against a position's full backup
    pile pulled the line toward players who were never going to play,
    distorting the "expectation" every real play gets compared to. Every
    row still gets a residual predicted off that line, pool member or
    not -- only the fit itself is pool-scoped, not the scoring.

    A position with fewer than two usable (pool) rows, or one where every
    pool row shares the same `Salary` (can't fit a slope from a single
    price point), gets a residual of 0 for every row in that whole
    position group -- there's no "expectation" to measure against yet,
    and 0 reads as "no signal" rather than a fabricated number. A row
    with a missing `ProjPts` stays blank (NaN), consistent with this
    codebase's "blank is not zero" rule -- it is never coerced into 0.

    Deliberately NOT `groupby(...).apply(...)`: with exactly one
    position present (a real case -- position-scoped debugging, or a
    hypothetical single-position slate), pandas' own `apply` collapses
    the per-row result into a single aggregate row instead of returning
    it row-aligned, silently corrupting every value. Iterating positions
    explicitly and writing into a pre-sized result Series sidesteps that
    entirely and is easier to reason about besides."""
    proj_pts = pd.to_numeric(proj_pts, errors="coerce")
    salary = pd.to_numeric(salary, errors="coerce")
    result = pd.Series(np.nan, index=proj_pts.index, dtype=float)

    for pos_value in position.unique():
        idx = position.index[position == pos_value]
        pts = proj_pts.loc[idx].to_numpy(dtype=float)
        sal = salary.loc[idx].to_numpy(dtype=float)
        pool = pool_mask.loc[idx].to_numpy()
        usable = pool & ~(np.isnan(pts) | np.isnan(sal))
        if usable.sum() < 2 or np.ptp(sal[usable]) == 0:
            # No fittable slope for this position's pool -- 0 for every
            # row that actually has a ProjPts to compare (pool or not);
            # a genuinely missing ProjPts stays NaN rather than being
            # coerced to a fabricated 0 (see docstring).
            residual = np.where(np.isnan(pts), np.nan, 0.0)
        else:
            slope, intercept = np.polyfit(sal[usable], pts[usable], 1)
            predicted = intercept + slope * sal
            residual = pts - predicted
        result.loc[idx] = residual

    return result.round(2)


def _percentile_against_pool(series: pd.Series, group: pd.Series, pool_mask: pd.Series) -> pd.Series:
    """Percentile rank (0-100) of `series` within each `group` value,
    measured against only the `pool_mask` members of that group (Fix 2's
    `VAL_ADJ_ROSTERABLE_TOP_N` reference population) -- but every row in
    the group still gets a score, pool member or not, so a true backup
    sorts naturally toward the bottom instead of getting a blank.

    Same average-rank convention `pandas.Series.rank(pct=True)` uses for
    a value that IS a pool member (`rank = (count-below + count-at-or-
    below + 1) / 2`, `pct = rank / pool_size`), generalized to a value
    that ISN'T a pool member by the same formula -- it isn't a special
    case, just what that formula already gives for a value with zero
    exact ties in the reference set. NaN inputs stay NaN."""
    values = pd.to_numeric(series, errors="coerce")
    result = pd.Series(np.nan, index=values.index, dtype=float)
    for group_value in group.unique():
        idx = group.index[group == group_value]
        pool_idx = idx[pool_mask.loc[idx].to_numpy()]
        pool_values = np.sort(values.loc[pool_idx].dropna().to_numpy())
        n = len(pool_values)
        if n == 0:
            continue
        group_values = values.loc[idx].to_numpy()
        below = np.searchsorted(pool_values, group_values, side="left")
        at_or_below = np.searchsorted(pool_values, group_values, side="right")
        pct = (below + at_or_below + 1) / 2 / n * 100
        result.loc[idx] = np.where(np.isnan(group_values), np.nan, pct)
    return result


def _val_adj_blend(pts_pct: pd.Series, edge_pct: pd.Series) -> pd.Series:
    """Week 3 feedback (A3): `ValAdj = VAL_ADJ_PROJECTION_WEIGHT * PtsPct
    + (1 - VAL_ADJ_PROJECTION_WEIGHT) * EdgePct`, both percentile ranks
    (0-100, via `_percentile_against_pool` -- `PtsPct` of raw `ProjPts`,
    `EdgePct` of `_val_adj_residual_within_position`'s output) measured
    within each position against Fix 2's rosterable reference population
    (`VAL_ADJ_ROSTERABLE_TOP_N`), not every player DK lists at the
    position.

    Worked example that motivated the original 50/50 blend (illustrative
    percentiles, not a real slate): an $8,200 RB projected 19.5 against a
    19.7 par (residual -0.2) has PtsPct 98, EdgePct 45 -> ValAdj 71.5. A
    $3,200 RB projected 9.0 against a 7.7 par (residual +1.3) "beats his
    price" more -- PtsPct 30, EdgePct 85 -> ValAdj 57.5. The expensive
    back now wins, which is the point: a residual alone (old ValAdj)
    ranked the cheap back above the expensive one, even though the cheap
    back can't plausibly win a GPP lineup and the expensive one can.
    Blending in raw `ProjPts`' own percentile keeps scale in the picture
    without losing the price-edge signal `EdgePct` alone provides. See
    docs/CALCULATIONS.md for the full worked example and Fix 2's
    reference-population rule."""
    return (VAL_ADJ_PROJECTION_WEIGHT * pts_pct + (1 - VAL_ADJ_PROJECTION_WEIGHT) * edge_pct).round(1)


def _attach_team_metrics(merged: pd.DataFrame, team_metrics: pd.DataFrame | None) -> pd.DataFrame:
    """Part C, C7: `Pace`/`PROE`/`Expl%` joined onto each player's row by
    his own `Team` -- `team_metrics` is `sources/nflverse_pbp.py`'s own
    already-blended per-team output (`Team`/`Pace`/`PROE`/`Expl%`), a
    whole-slate Python join exactly like `_attach_snaps`'s. Missing
    entirely (pbp couldn't be fetched this run) blanks all three columns
    for every row -- same fail-soft contract as every other optional input
    here; `_game_env_scores` (below) is what makes `GameEnv` itself
    degrade gracefully from this rather than going blank in turn."""
    if team_metrics is None:
        merged["Pace"] = pd.NA
        merged["PROE"] = pd.NA
        merged["Expl%"] = pd.NA
        return merged
    by_team = team_metrics.set_index("Team")
    merged["Pace"] = merged["Team"].map(by_team["Pace"])
    merged["PROE"] = merged["Team"].map(by_team["PROE"])
    merged["Expl%"] = merged["Team"].map(by_team["Expl%"])
    return merged


def _game_env_scores(
    game: pd.Series, ou: pd.Series, spread: pd.Series, team: pd.Series, pace: pd.Series, proe: pd.Series
) -> pd.Series:
    """Part C, C7 rebuild: 0-100 per game, an equal-weight (`GAME_ENV_
    WEIGHTS`) percentile blend of four inputs -- total (higher = more
    scoring expected), spread tightness (smaller |spread| = more
    competitive), combined pace of both offenses (faster = higher --
    `Pace` itself reads lower-is-faster, so this percentile is inverted),
    and combined PROE of both offenses (pass-heavier = higher). `Expl%` is
    a readable column on its own and is deliberately NOT one of these
    four, per C7's own instruction.

    Computed once per unique game (`Game`, TFFB's own field -- see module
    docstring for why this deliberately isn't cross-joined against
    `nflverse_games`'s own `GameId`) and broadcast back to every player in
    it, same as before C7. `pace`/`proe` are combined via `team_metrics.
    combined_by_game` (the mean of the two TEAMS' own values, never
    player-count-weighted) before being folded in here.

    Fail-soft: `weighted_mean_skipna` renormalizes over whatever inputs
    aren't NaN for a given game, so a pbp outage that leaves `Pace`/`PROE`
    blank for every row doesn't blank `GameEnv` -- it silently reduces to
    the exact pre-C7 formula (a plain 50/50 of total/spread tightness),
    with no separate fallback branch needed."""
    games = pd.DataFrame({"Game": game, "OU": pd.to_numeric(ou, errors="coerce")})
    games["AbsSpread"] = pd.to_numeric(spread, errors="coerce").abs()
    games = games.drop_duplicates(subset="Game").set_index("Game")

    combined_pace = combined_by_game(game, team, pace)
    combined_proe = combined_by_game(game, team, proe)
    games["Pace"] = combined_pace.reindex(games.index)
    games["PROE"] = combined_proe.reindex(games.index)

    components = pd.DataFrame(
        {
            "total": games["OU"].rank(pct=True) * 100,
            "spread_tightness": (1 - games["AbsSpread"].rank(pct=True)) * 100,
            "pace": (1 - games["Pace"].rank(pct=True)) * 100,
            "proe": games["PROE"].rank(pct=True) * 100,
        },
        index=games.index,
    )
    game_env = weighted_mean_skipna(components, GAME_ENV_WEIGHTS)

    return game.map(game_env)


def _team_game_lookup(games: pd.DataFrame) -> pd.DataFrame:
    """GamesRaw (one row per game) -> one row per team (each team appears
    once, as either Away or Home), indexed by team code."""
    away = games[["Away", "GameId", "Stadium", "Roof"]].rename(columns={"Away": "Team"})
    home = games[["Home", "GameId", "Stadium", "Roof"]].rename(columns={"Home": "Team"})
    return pd.concat([away, home], ignore_index=True).set_index("Team")


def _attach_games(merged: pd.DataFrame, games: pd.DataFrame | None) -> pd.DataFrame:
    if games is None:
        merged["GameId"] = pd.NA
        merged["Stadium"] = pd.NA
        merged["Roof"] = pd.NA
        return merged
    by_team = _team_game_lookup(games)
    joined = merged["Team"].map(by_team["GameId"])
    merged["GameId"] = joined
    merged["Stadium"] = merged["Team"].map(by_team["Stadium"])
    merged["Roof"] = merged["Team"].map(by_team["Roof"])
    return merged


def _attach_weather(merged: pd.DataFrame, weather: pd.DataFrame | None) -> pd.DataFrame:
    if weather is None or weather.empty:
        merged["Wind"] = pd.NA
        return merged
    wind_by_game = weather.set_index("GameId")["Wind"]
    merged["Wind"] = merged["GameId"].map(wind_by_game)
    return merged


def _attach_opp_pos_rank(
    merged: pd.DataFrame, sos_by_position: dict[str, pd.DataFrame] | None
) -> pd.DataFrame:
    """This player's OPPONENT's strength-of-schedule rank at THIS
    player's own position -- the same value `PlayerPoolRaw`'s own
    `OppPosRank` computes (via a `SoSComb` VLOOKUP, keyed by `Opp.`, not
    `Team` -- see `sheet_pool_raw_sos.py`'s docstring for the bug that
    inverted that one for months), computed here natively from each
    position's own already-synced `sos_<position>` frame instead
    (`Team.1`/`Rank` columns -- see `sources/tffb_sos.py`).

    `sos_by_position` is a dict of ONLY the positions that synced
    successfully this run (`sources/edge.py` builds it the same
    graceful-degradation way `games`/`weather` are already optional) --
    a position missing from it blanks just that position's players,
    never the whole column, since one position's TFFB page failing
    shouldn't hide every other position's real data."""
    if not sos_by_position:
        merged["OppPosRank"] = pd.NA
        return merged
    rank_by_team = {position: df.set_index("Team.1")["Rank"] for position, df in sos_by_position.items()}

    def _rank_for_row(row: pd.Series) -> object:
        lookup = rank_by_team.get(row["Position"])
        if lookup is None:
            return pd.NA
        return lookup.get(row["Opp"], pd.NA)

    merged["OppPosRank"] = merged.apply(_rank_for_row, axis=1)
    return merged


def _attach_opp_epa(merged: pd.DataFrame, team_metrics: pd.DataFrame | None) -> pd.DataFrame:
    """Round 5 item 9: `OppEPA`, ONE column whose input depends on position --
    higher always means a softer matchup:

    - QB/WR/TE: the opponent defense's `DefEPA/Pass` (EPA per pass play allowed);
    - RB: the opponent defense's `DefEPA/Rush`;
    - DST: the OPPOSING OFFENSE's `OffEPA/Play` with the sign FLIPPED -- a
      bad offense (negative EPA) is a good matchup for a defense, so
      `-OffEPA/Play` keeps "higher = better" for every position.

    Keyed by the player's `Opp` (a DK team code, like `team_metrics.Team`). A
    team missing from the pbp data -- or pbp not synced at all -- leaves
    `OppEPA` blank, never 0 (blank is not zero, same rule as every other
    optional input)."""
    if team_metrics is None or "DefEPA/Pass" not in team_metrics.columns:
        merged["OppEPA"] = pd.NA
        return merged
    by_team = team_metrics.set_index("Team")
    pass_allowed = merged["Opp"].map(by_team["DefEPA/Pass"])
    rush_allowed = merged["Opp"].map(by_team["DefEPA/Rush"])
    dst_matchup = -merged["Opp"].map(by_team["OffEPA/Play"])
    position = merged["Position"].astype(str).str.upper()
    oppepa = pd.Series(pd.NA, index=merged.index, dtype="object")
    oppepa = oppepa.mask(position.isin(["QB", "WR", "TE"]), pass_allowed)
    oppepa = oppepa.mask(position == "RB", rush_allowed)
    oppepa = oppepa.mask(position == "DST", dst_matchup)
    merged["OppEPA"] = pd.to_numeric(oppepa, errors="coerce")
    return merged


def _attach_line_movement(merged: pd.DataFrame, line_movement: pd.DataFrame | None) -> pd.DataFrame:
    """Fix 2.2: `diff_odds()` already computes all three deltas
    (TeamPointsDelta, TotalDelta, SpreadDelta); only the first ever
    reached EdgeRaw, under a name that didn't say which line moved. All
    three are surfaced now: ImpliedMove (team implied points -- what LineMove
    used to be), TotMove (game total), SpdMove (spread)."""
    if line_movement is None or line_movement.empty:
        merged["ImpliedMove"] = pd.NA
        merged["TotMove"] = pd.NA
        merged["SpdMove"] = pd.NA
        return merged
    by_team = line_movement.set_index("Abbr")
    merged["ImpliedMove"] = merged["Team"].map(by_team["TeamPointsDelta"])
    merged["TotMove"] = merged["Team"].map(by_team["TotalDelta"])
    merged["SpdMove"] = merged["Team"].map(by_team["SpreadDelta"])
    return merged


def _split_residual_within_position(
    gap: pd.Series, proj_pts: pd.Series, position: pd.Series, eligible: pd.Series
) -> tuple[pd.Series, list[str]]:
    """SPLIT rework: `resid = gap - E[gap | ProjPts, Position]` -- an OLS
    line of `gap` on `ProjPts`, fit PER POSITION over only `eligible` rows
    (rosterable-pool members with at least one of Sleeper/FantasyPros
    available), the same "fit against the reference population" shape
    `_val_adj_residual_within_position` already uses for ValAdj. Unlike
    that function, only `eligible` rows are ever SCORED too, not every row
    in the position -- a non-pool player, or a pool player missing both
    other sources, never gets a residual at all (`NaN`), matching "still
    pool-only" and "no flag on missing data" directly rather than scoring
    a row this flag can never apply to anyway.

    A position with fewer than `SPLIT_MIN_FIT_PLAYERS` eligible rows (or
    where every eligible row shares the same `ProjPts`, so no slope can be
    fit) is skipped entirely -- NaN for every row in it, and its name is
    returned in the second element of the tuple so the caller can log a
    warning; too few points to trust a fitted line, and Sam's own
    instruction is "don't fit it and don't flag it" for exactly this
    case, not "fit it anyway with weaker data."""
    gap = pd.to_numeric(gap, errors="coerce")
    proj_pts = pd.to_numeric(proj_pts, errors="coerce")
    result = pd.Series(np.nan, index=gap.index, dtype=float)
    skipped_positions: list[str] = []

    for pos_value in position.unique():
        idx = position.index[position == pos_value]
        elig = eligible.loc[idx].to_numpy()
        g = gap.loc[idx].to_numpy(dtype=float)
        p = proj_pts.loc[idx].to_numpy(dtype=float)
        usable = elig & ~(np.isnan(g) | np.isnan(p))
        if usable.sum() < SPLIT_MIN_FIT_PLAYERS or np.ptp(p[usable]) == 0:
            skipped_positions.append(pos_value)
            continue
        slope, intercept = np.polyfit(p[usable], g[usable], 1)
        predicted = intercept + slope * p
        residual = np.where(usable, g - predicted, np.nan)
        result.loc[idx] = residual

    return result.round(2), skipped_positions


def _split_flag_eligible(residual: pd.Series) -> pd.Series:
    """True for rows in the top `SPLIT_FLAG_TOP_SHARE` of every row WITH a
    residual, slate-wide (not per position -- same shape `_leverage_flag_
    eligible` uses), by `|residual|`, AND clearing the absolute
    `SPLIT_MIN_RESIDUAL` floor -- both conditions required, per Sam's own
    "flag the top 7%... [and] add an absolute floor" instruction (the
    floor keeps a low-disagreement week from manufacturing flags just to
    fill 7%; the quantile keeps a high-disagreement week from flagging way
    more than intended). Ties at the cutoff are included, same as
    LEVERAGE's own quantile."""
    abs_resid = residual.abs().dropna()
    eligible = pd.Series(False, index=residual.index)
    if abs_resid.empty:
        return eligible
    k = max(1, math.ceil(len(abs_resid) * SPLIT_FLAG_TOP_SHARE))
    cutoff = abs_resid.nlargest(k).min()
    in_top_share = abs_resid >= cutoff
    clears_floor = abs_resid >= SPLIT_MIN_RESIDUAL
    eligible.loc[abs_resid.index] = in_top_share & clears_floor
    return eligible


def _split_flag_for_residual(resid: float, eligible: bool) -> str:
    if not eligible or pd.isna(resid):
        return ""
    # resid > 0: the OTHER sources sit above their usual gap to TFFB, i.e. TFFB is the low one.
    return SPLIT_TFFB_LOW if resid > 0 else SPLIT_TFFB_HIGH


def _attach_agg_pts(
    merged: pd.DataFrame,
    sleeper: pd.DataFrame | None,
    fantasypros: pd.DataFrame | None,
    pool_mask: pd.Series,
) -> tuple[pd.Series, pd.Series, dict[str, JoinResult], list[str]]:
    """Part C, C5/SPLIT rework: `AggPts` is the equal-weight mean of every
    DK-scored source with a real projection for this player -- TFFB's own
    `ProjPts` (always present, this frame's own column), Sleeper's
    `DkPts`, FantasyPros' `DkPts`. A player missing one source (not synced
    this run, or a real "no projection this week" NaN from that source --
    see sources/sleeper_projections.py's/fantasypros_projections.py's own
    docstrings for why that's NaN, never a fabricated 0) is averaged over
    whatever's left; with neither external source available, `AggPts`
    equals `ProjPts` exactly. Feeds nothing else -- `ValAdj`/`Val`/
    `CeilVal`/the Board/every guardrail still key off `ProjPts` alone.

    Also computes the SPLIT flag (`TFFB↑`/`TFFB↓`) eligibility (reworked 2026-09-25 -- see
    `SPLIT_FLAG_TOP_SHARE`'s own comment for why): `gap = mean(Sleeper,
    FantasyPros) - ProjPts` -- deliberately NOT `AggPts`, which already
    includes `ProjPts` and would hide a third of the real disagreement --
    then `_split_residual_within_position` fits `gap ~ ProjPts` per
    position over the rosterable pool, and `_split_flag_eligible` flags
    the top share of |residual|, above an absolute floor. Restricted to
    `pool_mask` (a $2,500 backup's disagreement is noise, same restriction
    the LEVERAGE flag got) and to players with at least one of those two
    sources available (no source -> no flag, never flag on missing data).

    Joins each source independently by (name, team, position) via
    `player_join.join_source_to_dk`, keyed on `merged`'s own Id/Name/
    Team/Position (DST names already rewritten to DK's nickname by this
    point in `build_edge_frame`) -- never against each other, so a name
    one source can't match doesn't cost the other's own independent
    match. Returns `(agg_pts, split_flags, joins, split_skipped_positions)`:
    the `AggPts` series, the `TFFB↑`/`TFFB↓`/`""` SPLIT series (both aligned to
    `merged`'s index), one `JoinResult` per source actually passed in (so
    the caller, `sources/edge.py`, can report C1's own required per-source,
    per-position match rate without re-running the join itself), and the
    list of positions `_split_residual_within_position` skipped for having
    too few eligible players to fit (so the caller can print a warning).
    `pool_mask` (the same `VAL_ADJ_ROSTERABLE_TOP_N` mask ValAdj uses)
    scopes every `JoinResult`'s match-rate counts to the rosterable pool
    -- it does NOT restrict what gets averaged into `AggPts` itself; a
    non-pool player still gets every source's real number blended in,
    same as `ProjPts` itself is never pool-restricted."""
    dk_frame = merged[["Id", "Name", "Team", "Position"]]
    proj_pts = pd.to_numeric(merged["ProjPts"], errors="coerce")
    scores = [proj_pts]
    other_scores = []
    joins: dict[str, JoinResult] = {}

    for source_name, source_df in (("sleeper", sleeper), ("fantasypros", fantasypros)):
        if source_df is None:
            continue
        result = join_source_to_dk(
            dk_frame,
            source_df,
            source_name_col="Name",
            source_team_col="Team",
            source_position_col="Position",
            source=source_name,
            pool_mask=pool_mask,
        )
        joins[source_name] = result
        matched = result.matched[["Id", "DkPts"]].drop_duplicates(subset="Id", keep="first")
        score = dk_frame.merge(matched, on="Id", how="left")["DkPts"]
        score.index = merged.index
        score = pd.to_numeric(score, errors="coerce")
        scores.append(score)
        other_scores.append(score)

    agg_pts = pd.concat(scores, axis=1).mean(axis=1, skipna=True).round(2)

    if other_scores:
        other_mean = pd.concat(other_scores, axis=1).mean(axis=1, skipna=True)
        gap = other_mean - proj_pts
        has_other_source = other_mean.notna()
        pool = pool_mask.reindex(merged.index, fill_value=False)
        eligible_for_fit = pool & has_other_source
        residual, split_skipped_positions = _split_residual_within_position(
            gap, proj_pts, merged["Position"], eligible_for_fit
        )
        split_eligible = _split_flag_eligible(residual)
        split_flags = pd.Series(
            [_split_flag_for_residual(r, elig) for r, elig in zip(residual, split_eligible, strict=True)],
            index=merged.index,
        )
    else:
        split_flags = pd.Series("", index=merged.index)
        split_skipped_positions = []

    return agg_pts, split_flags, joins, split_skipped_positions


def _attach_snaps(
    merged: pd.DataFrame, snaps: pd.DataFrame | None, pool_mask: pd.Series
) -> tuple[pd.Series, JoinResult | None]:
    """Part C, C6: `Snap%` via the same (name, team, position) join every
    other external source in this module uses (`player_join.
    join_source_to_dk`). `snaps` is already resolved to each player's own
    most recently completed week by `sources/nflverse_snaps.py` -- this
    function only joins it onto DK's own Id, same as `_attach_agg_pts`
    does for Sleeper/FantasyPros. Missing entirely (not synced this run)
    blanks `Snap%` for every row and returns `None` for the join result,
    same fail-soft contract as every other optional input here."""
    if snaps is None:
        return pd.Series(float("nan"), index=merged.index, dtype="float64"), None
    dk_frame = merged[["Id", "Name", "Team", "Position"]]
    result = join_source_to_dk(
        dk_frame,
        snaps,
        source_name_col="Name",
        source_team_col="Team",
        source_position_col="Position",
        source="snaps",
        pool_mask=pool_mask,
        expect_dst=False,  # defenses have no snap counts by design
    )
    matched = result.matched[["Id", "Snap%"]].drop_duplicates(subset="Id", keep="first")
    snap_pct = dk_frame.merge(matched, on="Id", how="left")["Snap%"]
    snap_pct.index = merged.index
    return pd.to_numeric(snap_pct, errors="coerce"), result


def _attach_usage(
    merged: pd.DataFrame, usage: pd.DataFrame | None, pool_mask: pd.Series
) -> tuple[pd.DataFrame, JoinResult | None]:
    """Usage metrics (`Tgt%`/`WOPR`/`Rush%`/`RZ/G`/`HVT/G`) via the same (name, team, position)
    join every other external source uses. `usage` is `sources/nflverse_usage.py`'s output, one
    row per gsis id already reduced to each player's last 3 games played. The join's matched
    rows keep `GsisId`, which is the gsis -> DK crosswalk the results loop reuses. Missing or
    empty (not synced, fetch failed, Week 1) blanks every usage column -- never 0 -- and returns
    `None` for the join result."""
    blank = pd.DataFrame(float("nan"), index=merged.index, columns=USAGE_METRIC_COLUMNS)
    if usage is None or usage.empty:
        return blank, None
    dk_frame = merged[["Id", "Name", "Team", "Position"]]
    result = join_source_to_dk(
        dk_frame,
        usage,
        source_name_col="Name",
        source_team_col="Team",
        source_position_col="Position",
        source="usage",
        pool_mask=pool_mask,
        expect_dst=False,  # defenses have no usage
    )
    matched = result.matched[["Id", *USAGE_METRIC_COLUMNS]].drop_duplicates(subset="Id", keep="first")
    values = dk_frame.merge(matched, on="Id", how="left")[USAGE_METRIC_COLUMNS]
    values.index = merged.index
    return values.apply(pd.to_numeric, errors="coerce"), result


def _dst_nickname(full_team_name: str) -> str:
    """Mirrors sources/tffb_projections.py's `_dst_nickname` -- duplicated
    rather than imported for the same reason as WIND_FLAG_THRESHOLD_MPH
    above (derived.py stays source-module-agnostic). "Los Angeles
    Chargers" -> "Chargers": every NFL nickname is one word, so the last
    token is always right."""
    return full_team_name.strip().rsplit(" ", 1)[-1]


def _flags_for_row(row: pd.Series) -> list[str]:
    """Every matching flag, in priority order (most urgent first) -- a
    player who is both WIND and LEVERAGE showed only WIND under the old
    first-match-wins rule, silently hiding the second condition. All of
    these can be simultaneously true of the same player, so all of them
    are computed here; `build_edge_frame` derives both sheet columns from
    this one list (`Flags` = every token space-separated, for reading;
    `Flag` = just the first / highest-priority one, Part 7.9)."""
    flags = []
    if row["Avail"] in OUT_STATUSES:
        flags.append("OUT")
    if pd.notna(row["Wind"]) and row["Wind"] >= WIND_FLAG_THRESHOLD_MPH:
        flags.append("WIND")
    # IMPL↑/↓ keys off ImpliedMove specifically (Fix 2.2) -- TotMove/SpdMove
    # are shown for context but don't drive this flag.
    if pd.notna(row["ImpliedMove"]) and row["ImpliedMove"] >= LINE_MOVE_FLAG_THRESHOLD:
        flags.append(FLAG_IMPL_UP)
    if pd.notna(row["ImpliedMove"]) and row["ImpliedMove"] <= -LINE_MOVE_FLAG_THRESHOLD:
        flags.append(FLAG_IMPL_DOWN)
    if row["_LeverageFlagEligible"]:
        flags.append("LEVERAGE")
    # No ownership-published guard needed: ProjOwn reads 0 for everyone
    # until TFFB publishes it, so this can't fire before then regardless.
    if row["ProjOwn"] >= CHALK_OWNERSHIP_THRESHOLD:
        flags.append("CHALK")
    # Part C, C5b: lowest priority, below CHALK -- "look closer," not
    # "danger," and per the spec's own explicit instruction, a new flag
    # must never mask an existing one in the singular `Flag` column
    # (exactly the bug the LINE flag caused by sitting too high).
    if row["_SplitFlag"]:
        flags.append(row["_SplitFlag"])
    return flags


def build_edge_frame(
    projections: pd.DataFrame,
    salaries: pd.DataFrame,
    games: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    line_movement: pd.DataFrame | None = None,
    sos_by_position: dict[str, pd.DataFrame] | None = None,
    sleeper: pd.DataFrame | None = None,
    fantasypros: pd.DataFrame | None = None,
    snaps: pd.DataFrame | None = None,
    team_metrics: pd.DataFrame | None = None,
    usage: pd.DataFrame | None = None,
) -> EdgeBuildResult:
    """Join TFFB projections to DK salaries on player ID and compute every
    derived column for the EdgeRaw tab. Rows are returned pre-sorted by
    ValAdj descending, so the top of the tab is the answer.

    `salaries` is the raw draftkings.csv shape (columns include `ID`,
    `Salary`, `Status`); `projections` is the raw projections.csv shape
    (columns include `Id`, `ProjPts`, `ProjOwn`, `Ceiling`, `OU`, `Spread`,
    `Game`, `GameStart`). `games`/`weather`/`line_movement` are the
    GamesRaw/WeatherRaw shapes from `nflverse_games.py`/`weather.py`, and
    `line_movement.diff_odds()`'s output diffed against the start of the
    current NFL week (see sources/edge.py) -- all optional; see module
    docstring for why a missing one blanks columns rather than omitting
    them. `sos_by_position` is `{"QB": sos_qb_frame, ...}` -- each
    position's own already-synced `sources/tffb_sos.py` shape -- for
    however many positions synced successfully this run; see
    `_attach_opp_pos_rank`. `sleeper`/`fantasypros` are those sources' own
    already-DK-scored shapes (`sources/sleeper_projections.py`/
    `fantasypros_projections.py`, columns include `Name`/`Team`/
    `Position`/`DkPts`) -- optional, feed only `AggPts` (see
    `_attach_agg_pts`); missing either (or both) degrades gracefully, same
    as every other optional input here. `snaps` is `sources/
    nflverse_snaps.py`'s own shape (`Name`/`Team`/`Position`/`Snap%`,
    each player's own most recently completed week already resolved) --
    optional, feeds only `Snap%` (see `_attach_snaps`); missing it blanks
    `Snap%` for every row, same as everything else optional here.
    `team_metrics` is `sources/nflverse_pbp.py`'s own already-blended shape
    (`Team`/`Pace`/`PROE`/`Expl%` -- see `team_metrics.py`) -- optional,
    feeds `Pace`/`PROE`/`Expl%` directly (see `_attach_team_metrics`) and
    `GameEnv` indirectly (see `_game_env_scores`); missing it blanks the
    three team-metric columns and `GameEnv` silently reduces to its
    pre-C7, Vegas-only formula, same fail-soft contract as everything else
    optional here. `usage` is `sources/nflverse_usage.py`'s own shape (`GsisId`/`Name`/`Team`/
    `Position` plus the five usage metrics -- see `usage_metrics.py`) -- optional, feeds the five
    usage columns only (see `_attach_usage`); missing or empty blanks them.
    """
    proj = projections.copy()
    sal = salaries[["ID", "Salary", "Status"]].rename(
        columns={"ID": "Id", "Salary": "DkSalary", "Status": "Status"}
    )

    merged = proj.merge(sal, on="Id", how="left", indicator=True)
    unmatched_names = merged.loc[merged["_merge"] == "left_only", "Name"].tolist()
    merged = merged.drop(columns="_merge")

    # DK's own salary feed is the authoritative number (it's what's actually
    # charged); fall back to TFFB's own Salary field for the rare unmatched
    # row rather than dropping it.
    merged["Salary"] = merged["DkSalary"].fillna(merged["Salary"])
    merged = merged.drop(columns="DkSalary")

    # TFFB's own Name for a DST is the full team name ("Jacksonville
    # Jaguars"); DraftKings' -- and therefore DkSalClean/PlayerPoolRaw/
    # whatever a human types into Player Pool/Lineups -- is just the
    # nickname ("Jaguars"). Rewriting here (not only at TFFBOptoRaw's own
    # to_sheet_rows, which only reformats *that* tab) is what makes every
    # Name-keyed join against EdgeRaw actually match for DST rows.
    is_dst = merged["Position"] == "DST"
    merged.loc[is_dst, "Name"] = merged.loc[is_dst, "Name"].apply(_dst_nickname)

    # Phase 6, Part 2: TFFB's own ProjOwn is a raw percentage-as-number
    # (14.6 meaning 14.6%) -- converted to a true fraction (0.146) here so
    # it shares one stored scale with PlayerPoolRaw's native Rstr% (which
    # divides by 100 in its own formula, `=(...)/100`), now that both are
    # renamed to the same "Own%" name and need to share one PERCENT-type
    # sheet format. CHALK_OWNERSHIP_THRESHOLD is on this same fraction
    # scale as a result (0.20, not 20.0) -- see its own comment.
    merged["ProjOwn"] = merged["ProjOwn"] / 100

    val_adj_pool = _rosterable_pool_mask(merged["ProjPts"], merged["Position"])
    merged["AggPts"], merged["_SplitFlag"], source_joins, split_skipped_positions = _attach_agg_pts(
        merged, sleeper, fantasypros, val_adj_pool
    )
    merged["Val"] = (merged["ProjPts"] / (merged["Salary"] / 1000)).round(2)
    val_adj_residual = _val_adj_residual_within_position(
        merged["ProjPts"], merged["Salary"], merged["Position"], val_adj_pool
    )
    val_adj_pts_pct = _percentile_against_pool(merged["ProjPts"], merged["Position"], val_adj_pool)
    val_adj_edge_pct = _percentile_against_pool(val_adj_residual, merged["Position"], val_adj_pool)
    merged["ValAdj"] = _val_adj_blend(val_adj_pts_pct, val_adj_edge_pct)
    merged["CeilVal"] = (merged["Ceiling"] / (merged["Salary"] / 1000)).round(2)
    merged["CeilPct"] = _percentile_within(merged["Ceiling"], merged["Position"]).round(1)

    # Week 3 follow-ups, Item 4: measured over `val_adj_pool` (the same
    # VAL_ADJ_ROSTERABLE_TOP_N reference population ValAdj uses), not every
    # player DK lists -- see OWNERSHIP_PUBLISHED_SHARE_THRESHOLD's own
    # comment for why the full-list denominator can never cross 0.5.
    pool_projown = merged.loc[val_adj_pool, "ProjOwn"]
    has_real_ownership = pool_projown.fillna(0).gt(0).mean() > OWNERSHIP_PUBLISHED_SHARE_THRESHOLD
    merged["OwnStatus"] = OWN_STATUS_REAL if has_real_ownership else OWN_STATUS_UNPUBLISHED
    if has_real_ownership:
        merged["OwnPct"] = _percentile_within(merged["ProjOwn"], merged["Position"]).round(1)
        merged["Leverage"] = (merged["CeilPct"] - merged["OwnPct"]).round(1)
    else:
        merged["OwnPct"] = pd.NA
        merged["Leverage"] = pd.NA

    # LEVERAGE flag (2026-09-24): eligibility-and-cutoff computed here,
    # slate-wide, before `_flags_for_row` runs -- that function only ever
    # sees one row, so it can't compute a quantile itself. When ownership
    # is unpublished, Leverage is entirely blank and this is all False,
    # same as before.
    merged["_LeverageFlagEligible"] = _leverage_flag_eligible(merged["Leverage"], val_adj_pool)

    merged = _attach_team_metrics(merged, team_metrics)
    merged["GameEnv"] = _game_env_scores(
        merged["Game"], merged["OU"], merged["Spread"], merged["Team"], merged["Pace"], merged["PROE"]
    )
    # Fix 2.3: O/U and Spread were computed into GameEnv but never
    # surfaced on EdgeRaw itself -- renamed OverUnder here (not OU) so its
    # header doesn't collide with Player Pool/Lineups' own "O/U" header
    # text sourced from a different tab (oddsFinal via PlayerPoolRaw).
    # Spread needs no rename; TFFB's own field is already called that.
    merged["OverUnder"] = merged["OU"]

    merged = _attach_games(merged, games)
    merged = _attach_weather(merged, weather)
    # Part 7.4: GameId used to be dropped right after Stadium/Roof/Wind
    # were joined off it -- an internal join key, never surfaced. Now
    # kept and renamed to GameID (this column's own header text) so
    # stacks (players sharing a game) are visible on EdgeRaw itself.
    merged = merged.rename(columns={"GameId": "GameID"})
    merged = _attach_line_movement(merged, line_movement)
    merged = _attach_opp_pos_rank(merged, sos_by_position)
    merged = _attach_opp_epa(merged, team_metrics)
    merged["Snap%"], snaps_join = _attach_snaps(merged, snaps, val_adj_pool)
    if snaps_join is not None:
        source_joins["snaps"] = snaps_join
    usage_values, usage_join = _attach_usage(merged, usage, val_adj_pool)
    for column in USAGE_METRIC_COLUMNS:
        merged[column] = usage_values[column]
    if usage_join is not None:
        source_joins["usage"] = usage_join
    merged["TmRank"] = _tm_rank_within_team_position(
        merged["Salary"], merged["Name"], merged["Team"], merged["Position"]
    )

    merged["Avail"] = merged["Status"].fillna("")
    merged = merged.drop(columns="Status")

    flag_lists = merged.apply(_flags_for_row, axis=1)
    merged["Flags"] = flag_lists.apply(" ".join)
    # Part 7.9: "Flag" is just the single highest-priority token (empty
    # string, not NaN, when nothing fired -- consistent with "Flags").
    merged["Flag"] = flag_lists.apply(lambda flags: flags[0] if flags else "")
    merged["NameKey"] = merged["Name"].map(normalize_name)
    # Round 5 item 3: within-position percentile of each player metric over the
    # rosterable pool, zeros and blanks excluded (they are neither in the
    # reference distribution nor given a percentile), computed ONCE so the same
    # player reads the same colour on every tab. Highlighting rules key off these.
    for metric, pct_column in PLAYER_METRIC_PCT_COLUMNS.items():
        values = pd.to_numeric(merged[metric], errors="coerce")
        merged[pct_column] = _percentile_against_pool(
            values.where(values != 0), merged["Position"], val_adj_pool
        ).round(1)

    # Part 7.2: `ValAdj` is EdgeRaw's default sort now -- Part 7.1 demoted
    # `Leverage` off the spine specifically because it's no longer a
    # primary sort anywhere (TFFB's ownership projection is large-field,
    # wrong-shaped for Sam's small-field contests; see docs/CALCULATIONS.
    # md). Unlike the old Leverage/CeilPct fallback, `ValAdj` never
    # depends on ownership having published, so no fallback branch is
    # needed here.
    merged = merged.sort_values("ValAdj", ascending=False, na_position="last").reset_index(drop=True)

    # Phase 6, Part 2: renamed to Own% ONLY here, at the very last step --
    # every computation above (has_real_ownership, OwnPct, Leverage,
    # CHALK's ProjOwn check) still reasons in terms of ProjOwn, TFFB's own
    # field name for this value. EDGE_COLUMNS lists the OUTPUT name
    # (Own%), which is why the rename has to land after every internal use
    # of "ProjOwn" and right before this final column selection.
    merged = merged.rename(columns={"ProjOwn": "Own%"})

    # Zone labels carry no per-row data -- text lives in the header only
    # (see ZONE_LABELS' own comment for why they exist at all).
    for label in ZONE_LABELS:
        merged[label] = ""
    for column in EDGE_FINDER_COLUMNS:
        merged[column] = "" if column == "Edge" else float("nan")

    return EdgeBuildResult(
        frame=merged[EDGE_COLUMNS],
        unmatched_names=unmatched_names,
        source_joins=source_joins,
        split_skipped_positions=split_skipped_positions,
    )


def attach_edge_finder_columns(frame: pd.DataFrame, extras: pd.DataFrame) -> pd.DataFrame:
    """Fill the Edge Finder columns of an edge frame from `extras` (indexed by DraftKings `Id`, any of
    `EDGE_FINDER_VALUE_COLUMNS`). A player with no row in `extras` stays blank, never 0. The two hidden
    percentile helpers are recomputed within position over the rosterable pool, like the other helpers."""
    out = frame.copy()
    extras = extras[~extras.index.duplicated(keep="first")]
    for column in EDGE_FINDER_VALUE_COLUMNS:
        if column not in extras.columns:
            continue
        mapped = out["Id"].map(extras[column])
        out[column] = mapped.fillna("") if column == "Edge" else pd.to_numeric(mapped, errors="coerce")
    pool = _rosterable_pool_mask(pd.to_numeric(out["ProjPts"], errors="coerce"), out["Position"])
    for metric, pct_column in EDGE_FINDER_PCT_COLUMNS.items():
        values = pd.to_numeric(out[metric], errors="coerce")
        out[pct_column] = _percentile_against_pool(values.where(values != 0), out["Position"], pool).round(1)
    return out
