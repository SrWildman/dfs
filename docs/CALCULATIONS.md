# Calculations reference

Every formula, threshold, and piece of decision logic this project
computes, in one place. `docs/SHEET_REFERENCE.md` tells you what each tab
and column *is*; this doc tells you exactly *how* each derived number is
calculated, so you can verify a number instead of taking its description
on faith. Source of truth is the code -- every section below names the
function it documents, so if this doc and the code ever disagree, the
code is right and this doc is stale (please fix it, or flag it -- see
`CONTRIBUTING.md`'s doc-sync checklist).

Everything here is pure, offline-testable Python in `src/dfs/derived.py`,
`src/dfs/line_movement.py`, and `src/dfs/late_swap.py`, with corresponding
tests in `tests/test_derived.py`, `tests/test_line_movement.py`, and
`tests/test_late_swap.py` -- if you want to see the exact behavior at an
edge case (a missing value, a tie, a blank slot), the tests are the most
precise spec available.

## The projections↔salaries join

TFFB's projections and DraftKings' salaries are joined on `Id`, which is
DraftKings' own player ID -- TFFB's optimizer already reports it directly,
so this is an **exact ID join, not name-matching** (verified 742/742 exact
overlap on a real slate). A TFFB row with no DK salary match isn't
dropped; it's reported back as `EdgeBuildResult.unmatched_names` and
logged as a warning by `dfs sync` -- this can legitimately happen because
TFFB's optimizer isn't scoped to "DK's main Sunday slate only" the way the
salary source is, so a Thursday/Monday-only player can appear projected
with nothing to join against that week.

One correction happens as part of this join: TFFB's own `Name` field for
a DST is the full team name ("Jacksonville Jaguars"), but DraftKings' (and
therefore every hand-typed lineup, and `DkSalClean`/`PlayerPoolRaw`) uses
just the nickname ("Jaguars"). `EdgeRaw`'s `Name` column is rewritten to
the nickname for DST rows specifically (`derived._dst_nickname`, which
just takes the last space-separated word -- every NFL nickname is one
word) so every downstream Name-keyed lookup actually matches.

## Val

`Val = ProjPts / (Salary / 1000)`

Points per $1,000 of salary -- the standard median-value stat. Uses DK's
own salary, which is authoritative here: DK's number replaces TFFB's own
`Salary` figure wherever both exist, falling back to TFFB's only for the
rare player TFFB projects who isn't on DK's main-slate salary list.

## CeilVal

`CeilVal = Ceiling / (Salary / 1000)`

Same idea as `Val`, using TFFB's ceiling (high-outcome) projection instead
of the median. **Blank wherever `Ceiling` is blank** -- TFFB doesn't
populate `Ceiling` for every player (roughly 40-60% coverage depending on
the week), and a blank is left blank rather than treated as zero, since a
missing ceiling isn't the same claim as "this player has no ceiling."

**What `Ceiling` actually is (measured, PROVISIONAL -- Weeks 1-3, rosterable pool, n = 677).** 14.3% of
players scored more than their published `Ceiling` (90% interval 12.3%-16.7%), so it behaves like roughly the
**86th percentile** -- close to FantasyLabs' own definition (top 15% of outcomes), which was the hypothesis,
not an assumption. By position (n in brackets): QB 14.1% (78), RB 9.2% (174), WR 13.1% (259), TE 25.6% (90),
DST 17.1% (76) -- so RB ceilings look too high and TE ceilings too low, but each is a few dozen to a few hundred
players; read it as "not enough data yet" for any single position. Scored as a quantile with pinball loss
(never RMSE) the overall fit is best at tau = 0.85. Re-measured by `dfs results update`; the live numbers are on
the `Model Check` tab, and this paragraph should be refreshed from it as weeks accumulate.

## ValAdj (Part 7.2, 2026-09-18; reworked Week 3 feedback A3, 2026-09-22; reference population fixed Week 3 fixes Fix 2, 2026-09-23) -- EdgeRaw's default sort

```
pool          = top VAL_ADJ_ROSTERABLE_TOP_N[position] players by ProjPts,
                within position (or everyone, if the position has fewer)   (derived._rosterable_pool_mask)
residual      = ProjPts - E[ProjPts | Salary, Position],
                the line fit on POOL rows only, scored for every row       (derived._val_adj_residual_within_position)
PtsPct_pos    = percentile rank of ProjPts against the POOL, within
                position -- every row scored, pool member or not          (derived._percentile_against_pool)
EdgePct_pos   = percentile rank of residual against the POOL, within
                position -- every row scored, pool member or not          (derived._percentile_against_pool)
ValAdj        = VAL_ADJ_PROJECTION_WEIGHT * PtsPct_pos
              + (1 - VAL_ADJ_PROJECTION_WEIGHT) * EdgePct_pos              (derived._val_adj_blend)
```

`VAL_ADJ_PROJECTION_WEIGHT = 0.5`, a named module constant in
`derived.py` rather than a literal -- Sam may want to shift it toward
projection later without a code review to find the number.

**The reference population (Fix 2, 2026-09-23).** `VAL_ADJ_ROSTERABLE_TOP_N
= {"QB": 32, "RB": 64, "WR": 96, "TE": 32, "DST": 32}` -- roughly the
starters league-wide at each position. Both percentiles above, and the
residual's own regression line, used to be computed across **every**
player DraftKings lists at the position, including backups projecting
near zero. Found live: the deeper a position's backup pile, the more its
mid-tier players got inflated, because the percentile was really
measuring "better than the backup pile," not "a good play." Measured on
the 2026-09-23 snapshot:

| Pos | Players | Projecting < 2 pts | A 5.7-pt player's percentile | A 13-pt player's percentile |
|---|---|---|---|---|
| QB | 85 | 61% | 68th | 69th |
| RB | 153 | 46% | 64th | 88th |
| WR | 247 | 57% | 66th | 90th |
| TE | 147 | 71% | 82nd | 98th |
| DST | 26 | 0% | 35th | 100th |

TE's deep backup pile (71% projecting under 2 points) is why a $2,500 TE
projecting 5.7 points sat 14th on EdgeRaw's default sort, ahead of nearly
every real play on the slate. Restricting the reference population to
`VAL_ADJ_ROSTERABLE_TOP_N` fixes this without changing the 50/50 blend
weight -- a true backup outside the pool still gets a `ValAdj` (never
blank), scored against the pool's distribution, so it sorts naturally to
the bottom instead of inflating anyone above it. If a position has fewer
players than its own N (DST had 26 that week), the pool is simply all of
them.

**Why this changed.** `Val` (points per $1,000) is salary- and
position-biased -- a cheap player outranks a better, pricier one just for
being cheap, and QBs dominate any points-per-dollar leaderboard
regardless of slate. Part 7.2's `ValAdj` (the `residual` line above, used
alone, unblended) fixed the position bias by regressing within position,
but introduced a subtler version of the same salary problem: a residual
is scale-free, so a $3,200 RB projected 9.0 against a 7.7 par (residual
+1.3) outranked an $8,200 RB projected 19.5 against a 19.7 par (residual
-0.2) -- the cheap player "beats his price" by more, but can't plausibly
win a lineup the way the expensive one can. Found live 2026-09-22 (Sam:
"cheap players float too high").

**The fix blends in scale.** `PtsPct_pos` (a straight percentile rank of
raw `ProjPts` within position) restores the "bigger number is better"
signal `EdgePct_pos` alone was missing, at a 50/50 weight by default.
Worked example (illustrative, not a real slate):

| Player | ProjPts | Residual | PtsPct | EdgePct | ValAdj |
|---|---|---|---|---|---|
| RB $8,200 | 19.5 | -0.2 | 98 | 45 | **71.5** |
| RB $3,200 | 9.0 | +1.3 | 30 | 85 | **57.5** |

The expensive back now wins, which is the intended behaviour.

**Version 2** of the residual itself, refitting against realized points
once the results-tracking loop exists (additionally surfacing where the
market is systematically wrong), is still a deliberate later step, not
built yet -- unaffected by this rework, since it only changes the input
to `EdgePct_pos`.

**`ValAdj` is EdgeRaw's default sort** (`build_edge_frame` sorts
descending by it, unconditionally). This replaces the old Leverage-
descending sort (with a CeilPct fallback while ownership was unpublished)
-- see "Leverage and OwnStatus" below for why Leverage was demoted off
every primary sort in the first place. Unlike that old sort, `ValAdj`
never depends on whether TFFB has published real ownership yet, so there
is no fallback branch any more.

**Keep `Val`.** It didn't go away -- `Val >= 3.0` (3 points per $1,000,
roughly 150 points, which wins DK cash lineups about 90% of the time) is
a real, useful cash threshold. Bad sort key, good filter line.

**Within-position, not cross-position.** Both percentiles feeding the
blend are ranked within each player's own position (same convention as
`CeilPct`) -- so `ValAdj` is already comparable across positions and is
stepped on its own value (see "Highlighting: within-position steps and smooth gradients"
below). This also means a thin
position's best player can post a very high `ValAdj` on a raw production
level an average player at a deeper position would beat easily -- the
fix targeted comparing players WITHIN the same position fairly, and does
not promise `ValAdj` is a fair cross-position ranking. (A previous, more
extreme version of this -- several $3,200-3,900 TEs projected ~10-11
points outranking a $6,400 QB projected 26.0 -- was actually the Fix 2
backup-pile-inflation bug above, not this structural point on its own;
that specific case is fixed. This paragraph's weaker claim, that a thin
position's TOP player can still rank ahead of a deeper position's
average one on raw production, remains true by design.)

## Highlighting: within-position steps and smooth gradients (Sam, 2026-10-01)

Every scaled column is coloured, and **zeros and blanks are never part of it**. A
real zero gets only the flat grey chip (`ZERO_GREY_BG`, added last so it wins); Sam
zeroes promo/bonus rows by hand and they must not skew the real numbers.

It replaces Round 5 item 3's five wide percentile bands (2026-09-29). Sam: "visually
it's hard to understand why 3 is white and 3.14 is light green", "I like the position
stuff", "I don't want anything fixed". Two shapes, set per column by
`sheet_color_scales.FIELD_COLOR_SCALES`:

**Steps -- the player metrics, compared within position.** `ProjPts`, `AggPts`, `Pts`,
`Ceiling`/`Ceil`, `Val`, `CeilVal` read a hidden within-position percentile helper
(`derived.PLAYER_METRIC_PCT_COLUMNS`: `ProjPts%ile`, ..., computed once in `derived.py`
over the rosterable pool, zeros/blanks excluded, then linked onto Player Pool, Lineups and
PlayerPoolRaw through the INTERNAL group), so a QB is compared with QBs only and a player is
the same colour on every tab. `ValAdj`, `CeilPct` and `GameEnv` are already 0-100 percentile
scores and step on their own value. A percentile `p` is shaded like this:

| p | shade |
|---|---|
| 40 to 60 | plain |
| 60-65, 65-70, 70-75 | 3 light greens, one per 5 points |
| 75 to 97.5 | 9 greens, one per 2.5 points (finest where the top players live) |
| >= 97.5 | full green (`GRAD_MAX`) |
| 25-40 and below | the same ladder in reds, down to full red (`GRAD_MIN`) at <= 2.5 |

(`STEP_EDGES`: 13 shades per side, the distance from the middle `d = |p - 50|` growing by 5 out to
25 and by 2.5 from there to 47.5, then one open-ended shade; `STEP_PLAIN_HALF` = 10.) Each shade is a
straight blend from white to the strong colour, `amount` rising linearly with `d` from 12% to 100%,
so neighbouring steps differ by a soft tint -- no 3.00-vs-3.14 cliff -- and the half-size steps
spread a tight group of near-top players over distinguishable shades (Sam, 2026-10-02: the top 10
WRs "all projected within 5 points of each other" were hard to tell apart). The palette is the old
pastel gradient nudged about 15% of the way toward Sheets' default scale (`GRAD_MIN` ~#F2C0BF,
`GRAD_MAX` ~#A8DBB0): Sam found the default "way too much to stare at a whole sheet of" and a first
try at 40% too strong, but wanted a very slightly more prominent colour than the old pastels. The rules are CUSTOM_FORMULA rules with a *relative* row
reference (`=AND(ISNUMBER($AS2),$AS2>=60,$AS2<65)`), so a colour follows its row through any sort
or filter. (A true colour-scale gradient cannot do that per position: it only sees its own
range, so a per-position gradient is tied to fixed row runs and breaks as soon as the sheet is
sorted -- which is why the within-position colouring goes through the helpers.)
`dfs doctor`'s `pct-helpers` check fails if a tab has metric values but a completely blank
helper column, since the steps would then silently show no colour.

**Gradient -- everything without a position.** Game and team metrics (`PROE`, `Expl%`,
`Team Implied`, `O/U`/`OverUnder`/`Total`, `GPS`, `OppEPA`), `Own%`, `Used`, `Pace`, `Spread`,
`OppPosRank`, `Exposure` and the zero-centred columns are ONE smooth gradient over the column:

| Point | Value | Colour |
|---|---|---|
| low end | the 5th percentile of the column's NON-ZERO values | strong red |
| midpoint | the MEDIAN of the column's non-zero values | white |
| high end | the 95th percentile of the column's non-zero values | strong green |

All three are live formulas over the column itself (`_zero_exclude_formula`:
`PERCENTILE(FILTER(range,range<>0),q)` and `MEDIAN(FILTER(...))`, absolute references; a
column in several row blocks combines every block into one computation). Values beyond the
5th/95th percentile stay at the end colour. `Pace`, `Spread`, `OppPosRank` and `Exposure` are
reversed (LOW is good: green at the low end); `Spread` keeps zero as a real value (a pick'em),
so it has no grey chip. `ImpliedMove`, `TotMove`, `SpdMove` and `Leverage` (CeilPct - OwnPct,
-100..+100) are *diverging*: white AT ZERO with symmetric ends (`diverging_anchor_kwargs`), no
grey zero. `Own%` is the one column NOT anchored to its own median: white -> amber -> red (high
ownership is a caution) with the midpoint at the 20% CHALK line, the low end the lowest non-zero
value and the high end the real maximum. Ownership is right-skewed, so a median midpoint (~5%)
crushes nearly every value into one pale amber -- Week 3 feedback A2, and again 2026-10-02 ("really
flat in terms of colour spread") when I had moved it to the median under "nothing fixed".

On Player Pool and Lineups a gradient column's blocks (one per position / lineup) are ONE rule
over the union of the blocks, so totals and header rows between them stay out of it
(`grouped_column_rule_specs`); Lineups carries about 21 gradient rules plus the steps instead of
hundreds of per-block rules. The five-band machinery (`band_rule_specs`, `LEVERAGE_BANDS`,
`RANK_BANDS`, `PERCENTRANK` rules for the game metrics) is deleted.

**Known limitation.** A Slate Grid `Spread` column is signed
home-perspective and is not banded.

`Salary`/`DK Sal` are never colour-scaled anywhere on any tab -- a
constraint on a lineup, not a quality worth ranking; colouring it would
imply cheap is inherently good.

## CeilPct

This player's `Ceiling` percentile rank **within their position** (QB vs
QB, RB vs RB, etc.), 0-100. Implementation
(`derived._percentile_within`): `series.groupby(position).rank(pct=True) *
100`. Reads as "how often would this player realistically be the optimal
play at his position" -- a proxy for upside independent of ownership.
`NaN` (missing `Ceiling`) stays `NaN` here too; pandas' `rank()` already
excludes them from the ranking rather than treating them as the lowest
value. `OwnPct` (below) is the same computation applied to `ProjOwn`.

## Leverage and OwnStatus

**A note on names, Phase 6, Parts 2 and 7.9 (2026-09-17):** this section
(and `CeilPct`'s own note above) still says `ProjOwn`/`OwnPct` throughout,
because those are `build_edge_frame`'s own internal pandas column names
for TFFB's raw projected-ownership figure and its percentile rank --
`OwnPct` is still computed exactly this way internally, right up until
the function's very last line, and used for nothing except the Leverage
subtraction below. **`OwnPct` itself is not written to `EdgeRaw` any
more** -- Part 7.9 dropped it from the sheet-facing output entirely,
since this Leverage formula was its only consumer anywhere in the
codebase (verified by grep before removing). `LevBasis` (further below)
was renamed to `OwnStatus` in that same pass, once Leverage's own
demotion (Part 7.1) left it gating `Own%`, a spine column, rather than
describing Leverage.

The column actually written to `EdgeRaw` for ownership itself is called
`Own%`, rescaled from `ProjOwn`'s original 0-100 number to a 0-1 fraction
(`merged["ProjOwn"] = merged["ProjOwn"] / 100`, then renamed) so it
matches the already-0-1 `Own%` on `PlayerPoolRaw`/`Player Pool`/`Lineups`
-- one shared name, one shared scale, across every tab. `_percentile_
within` (what computes `OwnPct`, just below) is scale-invariant by
construction, so this rescale changed nothing about `OwnPct`/`Leverage`'s
own math -- only `CHALK_OWNERSHIP_THRESHOLD` (see the `Flags` section
below) needed a matching unit change.

`OwnPct` is `ProjOwn`'s percentile rank **within position**, computed the
same way `CeilPct` is (`derived._percentile_within`). `Leverage = CeilPct
− OwnPct` -- both sides are now the same kind of number (a 0-100
percentile), so the subtraction is a real gap, roughly **-100..100**,
centered near 0. A positive number means "this player's ceiling rank
outpaces how much he'll be owned" -- genuine leverage.

**This replaced a scale bug.** The original formula was `CeilPct −
ProjOwn` -- subtracting `ProjOwn` *unranked*, as a raw ownership
percentage. `CeilPct` is uniform 0-100 with mean 50; raw `ProjOwn` is
heavily right-skewed, with most of a 743-player slate under 5% and a
handful of chalk plays at 25-40%. Subtracting a raw percentage from a
percentile doesn't cancel scales the way it looks like it should -- on a
real slate the result centered near 45, not 0, so the `Flag` column's
threshold of 15 was effectively flagging anyone above roughly the 15th
percentile of ceiling: "just about every cell gets marked as leverage,"
as reported against a real week with ownership published. The fix is to
rank-normalize `ProjOwn` onto the same percentile scale before
subtracting, exactly as `CeilPct` already does for `Ceiling`.

TFFB's `ProjOwn` reads **0 for every player** until TFFB computes real
ownership, which usually happens midweek. In that window there is no real
ownership signal to rank against -- `OwnPct` and `Leverage` are left
**BLANK** rather than showing a number that looks like leverage but
isn't; a confident wrong number is worse than an empty cell. `EdgeRaw`'s
row order still ranks usefully in that window (see below), it's only the
`Leverage`/`OwnPct` *columns* that go blank.

`OwnStatus` (`LevBasis` before Part 7.9's rename) names which case is in
effect, computed once for the whole frame (not per player): `"real"` once
ownership is published for **more than half of the rosterable pool**
(`OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5`, measured over
`VAL_ADJ_ROSTERABLE_TOP_N`, the same reference population `ValAdj` uses),
else `"unpublished"`. It has exactly one job now: a data-freshness marker
telling you whether ownership has been published yet, not a second
formula to reason about.

**Phase 6, Part 1.4 (2026-09-17):** this used to be `.any()` -- a single
non-zero `ProjOwn` (one early-published player, a data glitch, a bye-week
artifact) flipped the WHOLE slate to `"real"`, computing `OwnPct`/
`Leverage` as a percentile over a column that was still ~99% zeros for
everyone else. Reproduced live before the fix: this marker (`LevBasis` at
the time) read `"real"` while every `ProjOwn` on `EdgeRaw` still read
`0.0%` and every `Leverage` cell was blank. A share threshold requires
ownership to be genuinely published for a majority of the slate, not just
present for one player.

**Week 3 follow-ups, Item 4 (2026-09-23):** "majority of the slate" was
still measured over every player DraftKings lists, not the rosterable
pool -- but TFFB only ever publishes ownership for players who'll
actually be rostered, so that share tops out around 38% and can never
cross 0.5. Live symptom, all season: `OwnStatus` read `"unpublished"` and
`Leverage` was blank even on a Sunday with ownership clearly out.
Verified on the real 2026-09-20 snapshot: 38% over the whole 668-player
list (reads unpublished) vs. 90% over the 250-player rosterable pool
(clearly published). Same denominator mistake as `ValAdj` (Fix 2), same
fix: measure the share against the pool.

**Sort order.** `Leverage` is no longer a primary sort anywhere (Part
7.1) -- `build_edge_frame` sorts the frame by `ValAdj` descending instead
(Part 7.2, see "ValAdj" above), unconditionally, regardless of whether
`OwnStatus` is `"real"` or `"unpublished"`. Its demotion is indefinite,
not "revisit after a full season of ownership logs" -- that revisit
condition depended on hand-logged DK ownership CSVs that aren't coming
(Week 3 follow-ups, Item 3; see `docs/planning/ROADMAP.md`'s "Deliberately
not doing" section). Treat `Leverage` as directional at best.

## GameEnv (rebuilt Part C, C7, 2026-09-25)

Computed once per unique game (`derived._game_env_scores`), then broadcast
to every player in it. Originally pure Vegas (total + spread tightness);
C7 adds two real game-environment inputs now that they're computed
(`Pace`/`PROE`, below), an equal-weight percentile blend of four inputs:

```
ou_pct        = this game's Over/Under, percentile rank across every game on the slate
tightness_pct = (1 − |Spread| percentile rank across every game on the slate)
pace_pct      = (1 − combined Pace percentile rank across every game on the slate)  -- faster = higher
proe_pct      = combined PROE percentile rank across every game on the slate         -- pass-heavier = higher
GameEnv       = weighted mean of the four above, GAME_ENV_WEIGHTS = 0.25 each, renormalized over
                whichever inputs aren't blank for a given game
```

"Combined" Pace/PROE is the mean of both teams' own value in that game
(`team_metrics.combined_by_game`) -- never player-count-weighted (a team
with 20 rostered players and a team with 15 both count once). Equal
weighting is the starting point Sam asked for, not a tuned result.

**Fail-soft, by construction, not a special case:** `derived.
_game_env_scores` renormalizes the weighted mean over whatever inputs
aren't `NaN` for a given game (`team_metrics.weighted_mean_skipna`). If
`pbp` didn't sync this run, `Pace`/`PROE` are blank for every game, and
the renormalized mean of just `total`/`spread_tightness` **is** the exact
pre-C7 formula -- no separate fallback branch needed, and no printed
warning needed beyond the one `sources/edge.py` already prints when `pbp`
itself fails to load.

Higher total (more expected scoring), a tighter spread (more competitive,
more reason for the trailing team to keep throwing), faster pace and a
higher pass rate over expected all push `GameEnv` up. `OU`/`Spread` are
still the Vegas context TFFB already attaches to each player
(`projections.csv`) -- deliberately *not* cross-referenced against the
separately-synced `nfl_odds` source, for the reason already given before
C7: that source is keyed by team nickname/abbreviation rather than DK's
team codes, and a name-matching layer just to double-check numbers TFFB
already provides isn't worth the join risk.

`Expl%` (below) is deliberately **not** one of GameEnv's four inputs --
Sam's own instruction: it stays a readable column on its own.

## Pace, PROE, Expl% (Part C, C7, 2026-09-25)

Season-to-date per-TEAM offense metrics from nflverse's free play-by-play
release (`sources/nflverse_pbp.py`, pure math in `team_metrics.py`), one
value per team broadcast to every player on it (`derived.
_attach_team_metrics`). Placed in the collapsed **Game** group,
immediately after `GameEnv` (all three feed it except `Expl%`, which
doesn't feed anything -- see above).

**Neutral-script filter**, for `Pace`/`PROE` only: `0.2 ≤ wp ≤ 0.8` (the
*possession* team's own win probability -- verified live that a road
team's own early-game snap reads close to 0.5, not the home team's
complement), `qtr ≤ 3`, and `half_seconds_remaining > 120` (excludes the
hurry-up/clock-killing final two minutes of a half). Blowouts and
two-minute drills distort both pace and play-calling independent of a
team's real identity.

```
Pace  = mean seconds between consecutive real offensive snaps (play_type in {pass, run})
        within the same drive, neutral script only. Lower is faster, so its bands are reversed
        (the fastest teams are green -- see "Highlighting: within-position steps and smooth gradients").
PROE  = mean pass_oe (nflverse's own pass-rate-over-expected model) over the same
        neutral-script scrimmage plays. Higher = pass-heavier than the situation implies.
Expl% = share of ALL scrimmage plays (every game state) gaining ≥20 yards on a pass
        (nflverse's own `pass` indicator, not a play_type string match) or ≥10 on a rush
        (`rush` indicator). Naturally excludes kneels/spikes/no-play penalties, since
        none of those are `play_type in {pass, run}`.
```

**Early-season blend**, all three metrics, per team:

```
weight_current = games_played / (games_played + PBP_PRIOR_WEIGHT_GAMES)
value          = weight_current * this_season + (1 − weight_current) * last_season_full_season_value
```

`PBP_PRIOR_WEIGHT_GAMES = 4.0` -- a defensible starting value, not fit to
anything (only one week of this data exists so far): at 2 games played
(most teams, week 3), `weight_current` = 2/6 = 33%, mostly last season's
shape; at 8 games (roughly mid-season) it's already 67%; it never fully
reaches 100% (81% at a full 17-game season), which is the right shape --
a small-sample current season should never fully drown out the prior one,
even late. A team missing one side of the blend (no current-season
neutral-script sample yet; or, not expected for an existing franchise but
handled anyway, no prior-season row) falls back to whichever side it has,
never a fabricated average against a 0. **Known limitation, stated rather
than hidden:** a team with major coaching/scheme turnover makes last
season a poor prior for *this* team specifically -- not detectable from
the data itself, so this blend can't correct for it. Re-tune
`PBP_PRIOR_WEIGHT_GAMES` once a full season of real week-over-week data
exists, the same way `LEVERAGE_FLAG_TOP_SHARE`/`LINE_MOVE_FLAG_THRESHOLD`
were.

**Fail-soft:** `pbp` failing to fetch this run blanks all three columns
for every row (never a fabricated value), and `sources/edge.py` prints a
warning; `GameEnv` degrades as described above rather than going blank in
turn.

Data source: nflverse's play-by-play parquet release (`https://github.com/
nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet`),
current season plus the prior season for the blend above -- release-asset
URLs only (a `.../raw/...` URL 403s in some environments). Verified live
(2026-09-25): the 2026 file, three weeks in, is 2.5MB; the completed 2025
season is 19MB. Refetched in full on every `dfs sync`, same as every other
Part C source -- `pbp` is deliberately excluded from `dfs sync --live`'s
source list (`sources/__init__.py`), same treatment `sleeper`/
`fantasypros`/`snaps` already get, since a season-to-date team aggregate
barely changes between two live-sync passes on the same day and isn't
worth the extra bandwidth/time on the "fast pass."

## GPS (2026-09-26; corrected Round 5 item 5c, 2026-09-29)

Kyle Borgognoni's (@kyle_borg) weekly "Pace of Play: Matchups & Stacks for
Week N" article on TFFB (Sam's existing subscription -- not a new paid
source). Each week he publishes a CSV alongside the article, one row per
team (`sources/tffb_gps.py`). Two numbers matter here:

```
GPS          = 1-5, this game's overall pace/scoring-environment score.
ImpliedTotal = this team's "Implied Total" column.
```

**`Implied Total` is Vegas, not a model.** The original build treated it as
a pace/EPA model's own team score and surfaced `ModelImplied` (EdgeRaw) and
`Model Tot`/`Tot Δ`/`Model Spd`/`Spd Δ` (Slate Grid/Board) as a "model vs
market" comparison. Checked against the Week 3 worksheet: it matches the
Vegas total and spread exactly for 12 of 16 games, and the other four are
two PAIRS OF ROWS SWAPPED in the source (NE@JAX ↔ KC@MIA, TEN@NYG ↔
SEA@WAS) -- our own odds snapshots had those lines set since Monday,
before the article went up. So there was never a model signal in it, and
the deltas only measured how out of date Vegas was. **All five columns
were removed.** `GPS` itself stays.

**GPS is not purely mechanical.** It blends team implied totals, neutral
pace, EPA per dropback and per rush, and PROE, but the published 1-5 score
also folds in the author's own judgement -- treat it the way you'd treat
any expert's rating, not a formula you could reproduce from the raw inputs
alone.

**Where it shows.** Slate Grid's `GPS` and the Board's Slate shape `GPS`
read `GPSRaw` directly, off the HOME team's row (`GPS` is a per-GAME score,
never an EdgeRaw column). Both are banded like `Total` (the `game` bands: a live
`PERCENTRANK` against the column's own range -- see "Highlighting: within-position steps and smooth gradients").

**`ImpliedTotal` stays in the snapshot as a sanity check only**
(`gps_check.py`). A row swap in the source means a game's `GPS` describes
the wrong game, and it shows up as a big miss against our own odds. Vegas
implied points come from GamesRaw's `Total`/`Spread` (positive `Spread` =
home favoured):

```
home implied = (Total + Spread) / 2        away implied = (Total - Spread) / 2
```

A game is flagged when EITHER team's `ImpliedTotal` is off its Vegas
implied total by more than **`GPS_IMPLIED_MISMATCH_PTS = 1.5`** points:

- `dfs sync` logs a warning naming the game (`sources/edge.py`,
  `gps_check.find_gps_mismatches`);
- the game's `GPS` cell gets a muted amber chip. The chip reads a hidden
  helper (`GPS off Vegas` on Slate Grid, a hidden column past the Board's
  join keys) because a conditional-format rule can't reference `GPSRaw`
  directly; the helper is the sheet-side twin of the Python check
  (`sheet_views._gps_mismatch_formula`). It is blank -- never a fabricated
  0 -- when GPS or the line is missing.

**Fail-soft, timing.** The article publishes Wednesday; if this week's
isn't up yet, `tffb_gps.py` raises (never guesses a slug, never falls back
to last week's file -- `dfs week new` already clears `data/current/`),
`sync.py`'s normal failure path logs it and moves on, and `GPS` simply reads
blank until the next sync after publication. Not in `LIVE_SYNC_SOURCES` --
like `sos_*`/`snaps`/`pbp`, a weekly-cadence source has nothing new to gain
from a fast live-sync pass.

## OppEPA (Round 5 item 9, 2026-09-29)

`OppPosRank` is TFFB's fantasy-points-allowed rank, and after three weeks
it is very noisy. `OppEPA` is a steadier matchup signal from the SAME
nflverse play-by-play the offensive metrics already use -- no new source.
One column, in the collapsed **Game** group right beside `OppPosRank`;
`OppPosRank` stays.

**Per-defense inputs** (`team_metrics.py`, per `defteam`, season to date):

```
DefEPA/Pass = mean epa on pass plays allowed   (pass == 1)
DefEPA/Rush = mean epa on rush plays allowed   (rush == 1)
DefSucc%    = mean success allowed, as 0-100
OffEPA/Play = mean epa per scrimmage play an OFFENSE produced (posteam)
```

Same real-scrimmage-snap filter as the offensive metrics (`play_type` in
{pass, run}: no kneels, spikes or no-plays). Unlike `Pace`/`PROE` these use
**all game states** -- no neutral-script filter -- because a defense's
efficiency allowed is what it is regardless of the score. Each is blended
with last season's full-season value exactly as `Pace`/`PROE` are
(`blend_with_prior`, `PBP_PRIOR_WEIGHT_GAMES = 4`); EPA keeps three decimals
(`EPA_DECIMALS`), success/pace two.

**Each player's row takes his opponent's value -- higher always means a
softer matchup:**

| Position | `OppEPA` is |
|---|---|
| QB, WR, TE | the opponent defense's `DefEPA/Pass` |
| RB | the opponent defense's `DefEPA/Rush` |
| DST | **minus** the opposing OFFENSE's `OffEPA/Play` |

A bad offense (negative EPA/play) is a good matchup for a defense, so the
DST's sign is flipped to keep "higher = better matchup" for every position.
A team missing from the pbp data -- or pbp not synced at all -- leaves
`OppEPA` blank, never 0. Coloured with the `game` bands, like `PROE`/`Expl%`
(`FIELD_COLOR_SCALES["OppEPA"]`; higher = greener -- see "Highlighting: within-position steps and
smooth gradients"), formatted to three decimals.

**One-time cross-check against a public team-stats site** (2026-09-29, current season
through Week 3, unblended, all plays): our EPA/play tracks theirs closely
in rank (r = 0.99 for offense and defense, 0.96 for success %) but sits a
near-constant **+0.04 EPA/play higher** for every team, almost all of it on
pass plays (+0.065 EPA/pass; EPA/rush is -0.01). After removing that offset
the largest miss is 0.046. Our league mean is +0.007 (as expected for
scrimmage plays); theirs is -0.033. Including penalty (`no_play`) snaps,
kneels and spikes did NOT reproduce their per-team totals, so the offset is
not explained by those filters alone (likely a different EPA model/
population on their side). The filters were deliberately not tuned to match.
Because `OppEPA` is only ever compared across the slate, a uniform offset
doesn't change what it says.

## OverUnder, Spread

Straight passthrough from the same Vegas context `GameEnv` already
computes from (TFFB's `OU`/`Spread` fields on `projections.csv`) --
computed into `GameEnv` since the start, but never surfaced as their own
columns until Fix 2.3. Named `OverUnder`, not `OU`, so its header doesn't
collide with Player Pool/Lineups' own `O/U` column, which is sourced from
a different tab (`oddsFinal` via `PlayerPoolRaw`) and isn't guaranteed to
agree number-for-number with TFFB's figure.

## Board's "Shootout?" flag (Slate shape, Part 3, 2026-09-22)

`derived.SHOOTOUT_TOTAL_THRESHOLD = 48.0` -- a game's Vegas total (the
same `Total`/`OverUnder` figure documented above, read off `GamesRaw` for
this panel) at or above this reads "Shootout" on the Board's Slate shape
section. A standard DFS heuristic, **not** empirically tuned against a
real slate the way `LEVERAGE_FLAG_THRESHOLD` below was -- a first-pass
number Sam should sanity-check once he's looked at a few real weeks and
compared "flagged as a shootout" against how those games actually played.

## Board's Punt finder / Stack candidates / per-position row counts (Board Fixes, 2026-09-25)

**Punt finder** used to flag `Salary < PUNT_SALARY_CEILING = 4000` flat --
a ceiling DK's own QB/RB pricing floor sits above, so it could never fire
for those two positions. `sheet_views.PUNT_SALARY_WINDOW = 1000` replaces
it: a play is a punt if its `Salary` is within `PUNT_SALARY_WINDOW` of
**that position's own** live per-slate minimum salary (`MINIFS` over
EdgeRaw's pooled rows for the position), so the window moves with
whatever the slate actually costs at each position instead of a single
number tuned for RB/WR/TE/DST. Pool diagnostics' `Gap` column ("No <POS>
within $1,000 of the slate min") uses the same threshold, same reasoning.

**Stack candidates** covers `sheet_views._STACK_GAMES = 8` games (the
highest-`OverUnder` games on the slate, same source as Slate shape's own
sort), two rows per game (one per team): QB, WR1/WR2/WR3 (by `TmRank`
within the team's own WRs), TE1, RB1, and a `Total` column repeating that
game's OverUnder for at-a-glance sorting -- 14 columns (`BOARD_STACK_COLHEADER`),
up from the original QB+WR1+TE1 3-slot version. The hidden Slate-shape
join-key columns (`BOARD_SLATE_GAMEID_COL_INDEX`/`AWAY_COL_INDEX`/
`HOME_COL_INDEX`) sit past this block's width, derived from
`BOARD_MAX_VISIBLE_COL_INDEX` rather than a hardcoded column letter, so a
future width change can't silently collide with them again.

**Per-position leaders / Punt finder row counts** used to be a flat 5 rows
per position for Leaders (Punt finder inherited whatever Leaders used).
`sheet_views.BOARD_ROWS_PER_POSITION = {"QB": 5, "RB": 8, "WR": 10, "TE":
5, "DST": 5}` replaces the flat count for both panels -- deeper for
RB/WR since those positions have more real rostering options per slate
than QB/TE/DST -- with a thin top border between each position's block so
the boundary reads at a glance without needing the sub-label row (below)
for it.

A sub-label row above each Per-position leaders block now names its own
sort ("Best ValAdj" / "Highest ProjPts") -- Leaders stacks two
differently-sorted blocks side by side and there was previously no way to
tell which was which without checking column headers across the tab.

## Stadium, Roof, Wind

`Stadium`/`Roof` are looked up from `GamesRaw` by team code (each team's
row appears once whether it played home or away that week) -- blank if
`nflverse_games` hasn't been synced this run. `Wind` is then looked up
from `WeatherRaw` by that game's `GameId` -- blank for dome games (weather
is only fetched for `Roof == outdoors` games in the first place) or if
`weather` hasn't synced.

## ImpliedMove, TotMove, SpdMove

`ImpliedMove`/`TotMove`/`SpdMove` = `line_movement.diff_odds()`'s
`TeamPointsDelta`/`TotalDelta`/`SpreadDelta` for this player's team,
joined onto `EdgeRaw` by team code. All three were always computed by
`diff_odds()`; before Fix 2.2 only `TeamPointsDelta` reached `EdgeRaw`,
under the name `LineMove` -- a name that didn't say *which* line had
moved once two more were added alongside it. `ImpliedMove` is the direct
rename (team implied points, same number `LineMove` always was);
`TotMove` (game total) and `SpdMove` (spread) are newly surfaced. The
`Flags` column's `IMPL↑`/`IMPL↓` keys off `ImpliedMove` specifically --
`TotMove`/`SpdMove` are shown for context but don't drive that flag.

**Baseline**: the diff is `(current nfl_odds sync) − (the first nfl_odds
snapshot of the current NFL week)`. This was originally diffed against
just *the previous sync* -- reverted because that baseline depends
entirely on how often `dfs sync` happens to run: the exact same real
Vegas move could show as a big number or nothing at all depending on sync
cadence, which is noise, not signal. Diffing against a fixed week-start
baseline (`nfl_calendar.week_start_date` + `store.load_since`) means the
same real-world move always produces the same number regardless of how
many times you've synced since. `dfs odds movement` is a separate,
terminal-only report that still answers the different question "what
moved since I last ran a sync" -- useful before deciding whether to
re-sync, but not the same numbers as `EdgeRaw`'s `ImpliedMove`/`TotMove`/
`SpdMove`.

**`diff_odds()` internals** (`line_movement.py`): joins two `nfl_odds`
snapshots on `abbr` (Rotowire's own DK-compatible team code); computes
`SpreadDelta`, `TotalDelta`, `TeamPointsDelta` as `current − baseline` for
each; sorts by `|TeamPointsDelta|` descending. A team present in only one
of the two snapshots (a bye week resolving, a rare mid-week schedule
change) is dropped from the diff rather than guessed at.

`TeamPointsDelta` (`ImpliedMove`) specifically is the delta in this team's
Vegas-implied point total (`total/2 ± spread/2`, computed upstream by
Rotowire, not by this project) -- a team's *own* expected points
changing, not just the game's total or spread moving in the abstract.

## GameStart

No calculation -- a straight passthrough. TFFB's `projections.csv`
already carries each player's game kickoff time as an ISO-8601 UTC
timestamp; it's just included in `EdgeRaw`'s final column selection so
`dfs lineups late-swap` (below) can read it without reaching back into a
different CSV.

## Avail

DraftKings' own `Status` field, verbatim (`Q`/`OUT`/`IR`/blank). No
transformation.

## GameID and TmRank (Part 7.4, 2026-09-18) -- making stacks visible

`GameID` is `nflverse_games`' own game identifier (`"2026_02_DET_BUF"`),
already computed internally (as the join key that attaches `Stadium`/
`Roof`/`Wind`) but never surfaced before this -- kept now, renamed to
this column's own header text. Two players sharing this value are in the
same game, which is what makes a stack (or a same-game guardrail
violation) computable at all.

`TmRank = ` this player's rank by `Salary` descending within his own
`Team` AND `Position`, ties broken by `Name` ascending for a
deterministic result regardless of the frame's own row order (which
changes every sync, since `EdgeRaw` sorts by `ValAdj`). 1 is the
highest-salaried player at that position on that team -- read alongside
`Position`, a `TmRank` of 1 at `WR` means "this team's WR1."

**This is a proxy, not a measurement.** Salary reflects the market's own
belief about a player's usage, not his actual target share -- two
different things that happen to correlate loosely. Never read `TmRank`
as "this player gets N% of targets"; it answers "how does the market
price this player relative to his own teammates at the position," which
is a usable stand-in for target hierarchy but not the same claim. This is
also why `TmRank` gets no colour scale (`sheet_style.FIELD_COLOR_SCALES`)
-- scaling it would visually imply it's a ranked quality worth optimizing
toward, the exact framing this section warns against.

## Lineups guardrails: DST vs. own QB, and one RB per game (Part 7.4)

Two of Part 7.4's three stacking rules are simply correct for both cash
and GPP lineups alike, so they're real `Issues`-column warnings (the
third, stack SHAPE -- QB+1 vs QB+2 vs QB+3 -- is a judgment call that
depends on contest type Sam doesn't tag per lineup, so it's REPORTED in
the lineup-metrics block instead, never warned about -- see Part 7.5).

**1. Never roster a DST against your own QB's team.** Correlation -0.46
in the underlying review -- the single largest coefficient anywhere in
it. When this DST scores well (sacks, turnovers, a defensive/special-
teams score), it is specifically at the expense of the offense it just
beat, which is exactly the QB you rostered if he plays for that
opponent.

```
qb_team = INDEX(Team_range, MATCH("QB", Position_range, 0))
dst_opp = INDEX(Opp_range, MATCH("DST", Position_range, 0))
violation = qb_team <> "" AND dst_opp <> "" AND qb_team = dst_opp
```

Both `INDEX`/`MATCH` lookups are wrapped in `IFERROR` -- a still-partial
lineup missing a QB or a DST degrades to "no violation possible yet,"
never a broken `#N/A` cell.

**2. Max one RB per game.** A self-referential `COUNTIFS` inside
`SUMPRODUCT` -- for every RB row, count how many RB rows (including
itself) share its `GameID`; a violation exists if any such count exceeds
1:

```
= SUMPRODUCT((Position_range="RB") * (GameID_range<>"") * (COUNTIFS(Position_range,"RB",GameID_range,GameID_range) > 1)) > 0
```

The `GameID_range<>""` term is load-bearing, not defensive filler: found
live (2026-09-19) that Lineups' `GameID` is always a FORMULA cell
(`=IF($A="","",VLOOKUP(...))`), so an unfilled slot's `GameID` is a
formula-produced `""`, not a genuinely blank cell -- and `COUNTIFS`
treats two formula-blank `""` cells as matching each other, unlike two
truly-blank (never-typed) cells, which don't match. Without this term,
the check fired on almost every incomplete lineup with 2+ unfilled RB
slots (the two fixed RB-slot rows, both blank). An earlier verification
pass on the template's Scratch tab had tested the wrong shape (both
`Position` and `GameID` genuinely blank, which trivially can't match
since `Position="RB"` is already false for a blank cell) and wrongly
concluded the un-guarded formula was safe.

Both formulas were confirmed empirically on the template's Scratch tab
before shipping -- a violating lineup shape and a clean one, read back
both directions -- since `COUNTIFS` accepting a RANGE (not a single
value) as its own criteria argument, correctly broadcasting elementwise
inside `SUMPRODUCT`, was worth verifying rather than assuming, the same
"verify live" discipline this project applies to any new Sheets-formula
mechanism (see `sheet_pool_formulas.py`'s own `MATCH`-broadcast note for
a case where the equivalent assumption would have been WRONG).

**Combining with the existing cap/completeness check.** A real stack
violation is appended alongside whatever `OVER`/`INCOMPLETE`/`OK` the
totals row already resolved to -- e.g. `"OVER $500 RB/GAME"` -- rather
than replacing it, so one real problem can never silently hide another
(the same principle `Flags` already established, after Part 1.1's
`LINE_MOVE_FLAG_THRESHOLD` bug suppressed every other flag on ~95% of a
real slate). `"OK"` specifically IS replaced by a real violation (there's
nothing to combine it with -- "OK" just means nothing else fired).

**Deliberately not built: a QB+RB stack rule.** Sources in the
underlying review disagree wildly on this correlation (0.07 to 0.43),
and the two sources that measured it most carefully both call it
functionally zero -- not worth a rule that would flag real, harmless
lineups.

## Flags (and Flag)

The one column meant to be read at a glance. Evaluated in order
(`derived._flags_for_row`), and **every condition that matches is
included** -- space-separated, in priority order (e.g. a windy game with
a leveraged player reads `WIND LEVERAGE`, not just `WIND`). This replaced
a first-match-wins rule that silently hid every condition but the most
urgent one; `sheet_style.FLAG_CHIPS` matches on `TEXT_CONTAINS` rather
than `TEXT_EQ` accordingly (none of the six tokens below is a substring
of another, so this can't cross-match).

**Phase 6, Part 7.9 (2026-09-17): split into two sheet columns.** 7.9's
own spec assumed `Flag` was still the old first-match-only value and
asked to hide it in favor of a new all-matches `Flags` column -- verified
live first, per Rule Zero, and found that premise was already false
(`_flags_for_row`'s "every condition that matches" behavior above
predates this Part). Resolved with Sam directly: split for real rather
than just renaming. `Flags` (every matching token, exactly the behavior
described above) took over the visible spine slot; `Flag` (just
`flags[0]`, the single highest-priority token, empty string when nothing
fired) moved to the hidden zone beside `Id` -- kept, not deleted, since
`sheet_style._apply_name_flag_style`'s Name-bold-on-Flag check and a few
other boolean/categorical lookups still key off it. Every reading
consumer at the time (Board's `LANDMINES` panel -- since replaced
entirely by the Part 3/7.6 Board rebuild, see `CONTRIBUTING.md`'s
changelog -- plus the Movement view, `dfs edge`'s terminal report,
`dfs lineups late-swap`, `dfs sync --live`'s diff report, the "Leverage
plays" filter view) was repointed at `Flags`;
nothing needed the single-token `Flag` for anything except that one
boolean check.

| Priority | Flag | Condition |
|---|---|---|
| 1 | `OUT` | `Avail` is `OUT` or `IR` |
| 2 | `WIND` | `Wind ≥ 20` mph |
| 3 | `IMPL↑` (was `LINE↑` until 2026-09-30) | `ImpliedMove ≥ +6.0` |
| 3 | `IMPL↓` (was `LINE↓` until 2026-09-30) | `ImpliedMove ≤ −6.0` |
| 4 | `LEVERAGE` | rosterable-pool member, in the top `LEVERAGE_FLAG_TOP_SHARE` (7%) of the pool by `Leverage` this week (blank `Leverage` while unpublished can never clear this) |
| 5 | `CHALK` | `Own% ≥ 0.20` (20%) -- can only fire once ownership is real; `Own%` reads 0 for everyone until then |
| 6 | `TFFB↑` / `TFFB↓` (SPLIT) | rosterable-pool member with at least one of Sleeper/FantasyPros available, in the top `SPLIT_FLAG_TOP_SHARE` (7%) of the pool by `\|residual\|` (gap vs. this position's own trend line, not raw points) AND `\|residual\| ≥ SPLIT_MIN_RESIDUAL` -- see Part C's own section below |
| — | *(blank)* | none of the above |

`WIND_FLAG_THRESHOLD_MPH = 20.0` is a starting point, not empirically
derived. `LINE_MOVE_FLAG_THRESHOLD = 6.0` **was** retuned (Phase 6, Part
1.1, 2026-09-17) the same way `LEVERAGE_FLAG_THRESHOLD` below was: the old
flat `1.0` fired on **95.7% of the real live Week 2 slate** (605 players
with a real `ImpliedMove`) -- because `_flag_for_row` returns every
matching flag but the `IMPL` flag (was LINE) sits above LEVERAGE/CHALK in read priority, this
was drowning out every other flag. Retuned against the real odds-snapshot
history in `data/raw/nfl_odds/` (30 real per-team `|TeamPointsDelta|`
values: mean 2.87, std 1.94, quartiles 1.0/3.0/4.0, a real gap between 5
and 7 with nothing at 6) to `6.0`, which re-synced live to **4.5%** --
just under the 5-10% target band on that one day's real pull (6.7% on the
historical sample used to pick it), which is expected day-to-day variance
around a threshold tuned from history, not a sign it needs re-tuning
again from a single moment's read.

`LEVERAGE_FLAG_THRESHOLD = 30.0` (a fixed threshold on `Leverage`, no pool
restriction) **was replaced, 2026-09-24, with `LEVERAGE_FLAG_TOP_SHARE =
0.07`** (see `PROMPT_LEVERAGE_FLAG.md`). Two problems with the fixed
threshold, both found live: `CeilPct`/`OwnPct` (and so `Leverage`) are
percentiles over *every* player DK lists, not just the rosterable pool
(`VAL_ADJ_ROSTERABLE_TOP_N`) -- so on the 2026-09-20 snapshot, 10 of 30
flags at threshold 30 were players outside the pool (nine $2,500-$2,800
backup TEs plus one injury-limited player projecting ~4.5 points, whose
tiny ceiling still looked like a ~75th-percentile ceiling against the
league-wide backup pile). And even restricted to the pool, a fixed
threshold drifted week to week: at 30, the pool fire rate was 12-15% in
Week 1 (9/10-9/15 snapshots) and 5-8% in Week 2 (9/19-9/20 snapshots) --
the same failure mode `LINE_MOVE_FLAG_THRESHOLD` had before it was
retuned.

The fix: only rosterable-pool members are eligible for `LEVERAGE` at all,
and among them the flag goes to the top `LEVERAGE_FLAG_TOP_SHARE` (7%) by
`Leverage` each week, not a fixed number -- self-correcting by
construction rather than something that needs re-tuning as the slate's
`Leverage` distribution shifts. Ties at the cutoff are included (so the
flagged count can run slightly above the nominal 7%, not below it).
Verified against two real snapshots after the fix: 19 flagged on both
`data/raw/edge/20260920T155118Z.csv` (250-player pool) and
`data/raw/edge/20260913T152236Z.csv` (248-player pool), zero outside the
pool either time -- both slates had several genuine ties right at the
cutoff, which is why the count reads 19 rather than exactly 17-18.

`CHALK_OWNERSHIP_THRESHOLD = 0.20` was checked against a real 744-player
Week 1 slate with real ownership published, after the `Leverage` scale
fix that predates the change above: that slate's `Leverage` distribution
was mean -0.01, std 16.7, min -56.2, max 68.7 (quartiles -9.4 / -3.1 /
+6.1, 90th percentile +27.0), and `CHALK_OWNERSHIP_THRESHOLD` flagged
5/744 players (0.7%) on it -- it's an absolute ownership percentage, not
a percentile, so it's untouched by either the pool restriction or the
top-share change above. **Phase 6, Part 2 (2026-09-17):** the constant
itself changed from `20.0` to `0.20` when `Own%` (the sheet-facing name
for what this section still calls `ProjOwn` -- see the note at the top of
the `Leverage and OwnStatus` section) was rescaled from a 0-100 number to
a 0-1 fraction to match its already-0-1 scale on `PlayerPoolRaw`/`Player
Pool`/`Lineups`. The threshold's real-world meaning (20% ownership) and
the 5/744 flag rate above are both unchanged -- only the number's own
units moved.

## Part C: `AggPts` and `SPLIT` (second projection sources, 2026-09-24)

`docs/planning/PROMPT_PART_C.md`. Two free sources (Sleeper, FantasyPros)
are re-scored to exact DraftKings rules (`dk_scoring.py`) and joined onto
DK's own player Id by normalized name/team/position (`player_join.py`,
DSTs by team alone). Every source's own fantasy-point total is discarded
-- only its component stats (yards, TDs, receptions, ...) are re-scored,
so DK's real rules (including the 300/100/100-yard bonuses, treated as an
expected value rather than a hard cliff -- see `dk_scoring.py`'s own
`YARDAGE_CV`/`expected_yardage_bonus` docstrings) apply uniformly no
matter which source a number came from.

**`AggPts`** (EdgeRaw, Player Pool, Lineups -- immediately after
`Pts`/`ProjPts`) is the equal-weight mean of every source with a real
projection for that player: TFFB, Sleeper, FantasyPros. A source missing
this player (not synced, or a genuine "no real projection this week" --
both Sleeper and FantasyPros pad their player lists past what they
actually project, see their own source-module docstrings for how each is
detected and blanked to `NaN` rather than scored as a fabricated 0) is
excluded from that player's own average, not treated as a 0. With only
TFFB available, `AggPts` equals `ProjPts` exactly. It feeds nothing else
-- `ValAdj`/`Val`/`CeilVal`/the Board/every guardrail still key off
`ProjPts` alone; `AggPts` is a column Sam reads, not an input to anything
computed.

**Honest caveat on the blend itself:** the published research behind
"average several projection sources" is that the gain is *consistency*
(fewer wild single-source misses), not a large accuracy improvement over
any one good source. Don't read `AggPts` as more accurate than `ProjPts`
by construction -- it's a second opinion, not a better one.

**RB/WR calibration gap, not a scoring bug.** C2's own required
calibration check (compare each source to TFFB per position over the
rosterable pool after re-scoring) found QB/TE/DST agree with TFFB within
about a point on the real 2026-09-20 snapshot, but RB and WR run
systematically **1.5-2 points below** TFFB on both Sleeper and
FantasyPros independently (mean diff -1.55/-1.78 respectively for RB,
-1.95/-1.56 for WR; 71-87% of individual players negative, not a few
outliers dragging the mean). Hand-verified several players' scoring
arithmetic directly -- it's correct. This reads as TFFB genuinely
projecting more RB/WR volume than the market consensus, not a bug in
either scoring engine. Sam's call when this was reported: ship it as-is
and document the caveat here, rather than exclude either source from the
RB/WR aggregate. Practical effect: `AggPts` for a RB/WR pulls slightly
below `ProjPts` more often than not -- and, as first built, `SPLIT`
(below) fired on this backup-pile difference almost exclusively, which is
exactly what the 2026-09-25 rework fixes.

**`TFFB↑`/`TFFB↓`** (the SPLIT flag; named `SPLIT↑`/`SPLIT↓` until 2026-09-30, see below; in `Flags`, lowest priority in the hidden `Flag`,
below `CHALK`) flags disagreement **beyond the usual gap for a position
and projection level**, not raw points -- reworked 2026-09-25 after the
original raw-gap rule (`SPLIT_ABS_FLOOR`/`SPLIT_REL_THRESHOLD`, both now
removed) turned out to fire almost entirely on the RB/WR calibration gap
above: 15 fires on `data/raw/edge/20260925T191419Z.csv`, 12 of them
`SPLIT↓` on $3,400-$4,800 backups -- the same handful of players every
week, not a "look closer" signal.

```
gap       = mean(Sleeper, FantasyPros) − ProjPts                     (unchanged)
resid     = gap − fitted(ProjPts)   -- OLS line of gap~ProjPts, fit PER POSITION
                                        over rosterable-pool players with a source
fires if  |resid| is in the top SPLIT_FLAG_TOP_SHARE (7%) of the pool by |resid|
          AND |resid| ≥ SPLIT_MIN_RESIDUAL (2.0 points)
```

Same idea as `ValAdj`'s own price-edge residual (`_val_adj_residual_
within_position`): fit what "normal" looks like for this position at this
projection level, then flag departures from THAT, not from zero. A
position with fewer than `SPLIT_MIN_FIT_PLAYERS` (8) rosterable-pool
players carrying a source doesn't get a fitted line at all -- too few
points to trust a slope, so it's skipped entirely (no flag, a printed
warning) rather than fit against a handful of players. Still rosterable-
pool only (same restriction `LEVERAGE` got: a $2,500 backup's
disagreement is noise); still no flag when neither other source has a
real number for that player. Direction comes from the sign of `resid`:
`TFFB↑` means TFFB is HIGHER than the other sources are, for a player at
this position/level (`resid < 0`); `TFFB↓` means TFFB is lower (`resid > 0`).
**Renamed 2026-09-30** (Sam: "say what they mean"): the old `SPLIT↑`/`SPLIT↓`
arrows pointed at the OTHER sources, so the old `SPLIT↑` is now `TFFB↓` and
the old `SPLIT↓` is now `TFFB↑`. The next `dfs sync` rewrites EdgeRaw's `Flags`
with the new text; the tokens are `derived.SPLIT_TFFB_HIGH`/`SPLIT_TFFB_LOW`.

**Why both a quantile AND a floor:** the quantile alone would flag ~7% of
the pool even in a week where every source agrees closely (manufacturing
noise); the floor alone would flag however many players clear 2 points
in a week with unusually wide disagreement (no longer "look closer,"
"look at almost everyone"). Both conditions together, same discipline
`LEVERAGE_FLAG_TOP_SHARE`'s own tuning used.

Verified against the real 2026-09-23/24 inputs feeding EdgeRaw's
2026-09-25 sync (`data/raw/projections/20260923T041654Z.csv`,
`data/raw/draftkings/20260923T115552Z.csv`, and the one Sleeper/
FantasyPros snapshot each has, `20260924T...`): **19 fires (10 old-`SPLIT↑`
= `TFFB↓`, 9 old-`SPLIT↓` = `TFFB↑`)** across a 250-player rosterable pool, zero outside it, every
position had >= `SPLIT_MIN_FIT_PLAYERS` and got a real fit (`split_
skipped_positions` empty). Match rates: Sleeper 96.9-100% and FantasyPros
96.9-100% per position over the pool (DST/QB/TE/WR all 100% or 96.9-100%,
RB 98.4% both sources) -- see C1's own match-rate table for the exact
per-position numbers. Includes real starters the old raw-gap rule missed
entirely (`Jonathan Taylor ↑`, `De'Von Achane ↑`, `DJ Moore ↑`, `Kenneth
Walker III ↓`) while keeping the obvious news-driven cases (`Tyrone Tracy
Jr. ↓`, `Sam Darnold ↑`, `Rico Dowdle ↑`) -- all seven cross-checked
against Sam's own independent rough run (16 fires, 9↑/7↓) beforehand, and
all seven landed with the same direction here. Sleeper's QB match rate is
a clean 32/32 (100%) -- the four QBs Sam's own rough pass found missing
Sleeper data (Darnold, Jayden Daniels, Carson Wentz, Shedeur Sanders) are
real rows in Sleeper's own file with a correct name/team/position match,
just a genuinely blank projection (Sleeper simply hasn't projected them
this week) -- confirmed **not** a join miss.

## `Snap%` (Part C, C6, 2026-09-24)

Offensive snap share from nflverse's free `snap_counts_{season}.csv`
release (`sources/nflverse_snaps.py`), joined onto DK's Id the same way
as every other Part C source (`player_join.join_source_to_dk`, by
normalized name/team/position -- no separate crosswalk step turned out
to be needed; the release already carries name/team/position directly).

**"Most recent completed week" is per player, not one global week
number.** Confirmed live (2026-09-25, week 3): the release already
carried a lone Thursday-night week-3 game alongside every other team's
week-1/2 rows -- a single "current week minus one" filter would have
served that game's own players stale week-2 data. Grouped by
`pfr_player_id`, each player's own latest available week wins.

**Blank vs. zero, same rule as everywhere else in this codebase:** a
player nflverse has never recorded (a rookie, a bye week, DST -- defenses
have no individual snap share) reads blank, never a fabricated 0. A
player with a real recorded 0% (inactive/DNP in their own latest game)
keeps that real 0% -- it's a known fact, not missing data.

Placed in its own collapsed **Usage** group, positioned after Weather,
on EdgeRaw/Player Pool/Lineups. Feeds nothing else computed on this
sheet -- a read-only usage signal, not an input to any flag or score.

## Usage volume: `Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G` (2026-10-02)

Five columns in the USAGE group beside `Snap%`, from nflverse's free `stats_player_week_{season}`
parquet plus the current season's play-by-play (`sources/nflverse_usage.py`, source `usage`; all
the maths is in `usage_metrics.py`). They are for you to read alongside the projection -- **not
inputs to `ProjPts`, `ValAdj` or any flag**.

**Why volume, not efficiency.** Published research is consistent that usage holds up from week
to week and efficiency regresses. In one multi-season study the best predictors of future points
were target share (WR +0.22, TE +0.34), WOPR, and snap share and carries for RBs; receiving TDs,
receiving EPA and yards per target were *negative* predictors; for QBs only rushing usage
predicted anything. So this sheet adds volume columns and deliberately does **not** add player
efficiency stats (player EPA, RACR, yards per target, YAC over expected). Do not add them.

| Column | Definition | Applies to |
|---|---|---|
| `Tgt%` | the player's targets / his team's targets, over the window | WR, TE, RB |
| `WOPR` | 1.5 x target share + 0.7 x air-yards share, over the window | WR, TE |
| `Rush%` | the player's carries / his team's carries (QB scrambles included), over the window | RB, QB |
| `RZ/G` | targets + carries from inside the opponent's 20, per game | RB, WR, TE, QB |
| `HVT/G` | "high-value touches": targets + carries from inside the opponent's 10, per game | RB |

Blank (never 0) outside a metric's positions, for a player nflverse has no row for, and for
everyone in Week 1 or when the file can't be fetched. A real 0 (a player who played and had no
red-zone looks) stays 0 and gets the grey zero chip.

- **Window: each player's last 3 games played** (fewer if he has fewer). Shares are ratios of
  window *sums* (not a mean of weekly ratios), computed only over the games he played, so a
  missed game never counts as a zero and an earlier game fills the window instead. A game counts
  as played when the player has a row in `stats_player` that week; a player who suited up but
  recorded no stat at all has no row and is skipped the same way. The header notes and the
  Instructions tab state the window and the week the data runs through (the latest week with a
  near-full slate, so a lone Thursday game does not move it; those players still carry that game
  in their own window).
- **A short window is a noisy number.** A player with fewer than 3 games is shown from the games he
  has, as decided. Week 4 (data through Week 3): 270 players have 3 games, 99 have 2 and 92 have 1 --
  e.g. Brock Bowers and Puka Nacua read from a single game. The window length is not on the sheet; it
  is in the `Games` column of `data/current/usage.csv`. A minimum-games rule or a shown window length is
  the obvious next step if this proves misleading.
- **Team totals** are summed per team-week over every player in the file. Checked against
  nflverse's own `target_share` for every WR/TE/RB row of the 2026 file (987 rows): our single-week
  share matches with a **maximum difference of 0.0**.
- **WOPR's air-yards share divides by the team's PASSING air yards** (the sum of its passers'
  `passing_air_yards`), which is what nflverse's own `air_yards_share` divides by. Dividing by the
  sum of receivers' `receiving_air_yards` instead was tried first and is wrong by up to 0.09
  (throwaways and spikes carry air yards no receiver is credited with). With the passing
  denominator our single-week WOPR matches nflverse's `wopr` on all 707 WR/TE rows, **maximum
  difference 0.0**.
- **Red zone** comes from the pbp, not the stats file: `yardline_100 <= 20` (`<= 10` for
  `HVT/G`) on real scrimmage plays (`play_type` pass/run, the same allowlist `team_metrics` uses),
  counting `receiver_player_id` on pass plays and `rusher_player_id` on rush plays (scrambles are
  rushes), excluding two-point-conversion attempts and deleted plays. Both are gsis ids, matching
  the stats file's `player_id`. A game with none counts as a 0 over the same game count.
- **No prior-season blend** (roles change between seasons) and no fall back to last season:
  if the stats file cannot be fetched the columns go blank, the sync carries on with a warning,
  and the empty result also overwrites any stale `data/current/usage.csv`. A pbp failure alone
  blanks only `RZ/G`/`HVT/G`.
- **Join to DraftKings:** nflverse gsis id -> DK `Id` through `player_join` on display name +
  team + position (FB/HB read as RB, like every other source). Every sync prints the rosterable-pool
  coverage, writes the unmatched list to `data/current/unmatched_usage.csv` and the matched pairs
  to `data/current/gsis_crosswalk.csv` (reused by the results loop). A player nflverse has no row for
  (a QB who has not played) is legitimately unmatched; Travis Hunter is a CB in nflverse (a two-way
  player), deliberately not joined, as for Sleeper.
- **Colouring:** within position, through five more hidden `*%ile` helper columns (`Tgt%ile`,
  `WOPR%ile`, `Rush%ile`, `RZ/G%ile`, `HVT/G%ile`), exactly like `ProjPts` -- the same 26 steps, the
  same zero chip, and covered by the same `pct-helpers` doctor check.

## Slate Grid: game environment columns and the TEAMS section (2026-10-02)

**Game rows** gain `GameEnv`, `Pace`, `PROE` and `Expl%`: each is the mean of both teams' EdgeRaw
value (`sheet_views._edge_team_pair_mean`, the same helper the Board's Slate shape uses, so the two
tabs cannot disagree), coloured with the same game-metric rules.

**TEAMS** (below the games) has one row per team on the week's schedule, sorted by implied total,
highest first: `Team`, `Opp`, `Implied`, `Pace`, `PROE`, `Expl%`, `Off EPA/play`, `Off EPA/pass`,
`Off EPA/rush`, `Opp Def EPA/pass`, `Opp Def EPA/rush`.

- `Implied` is the Vegas implied team total from `GamesRaw`: home = (Total + Spread) / 2, away =
  (Total - Spread) / 2 (Spread is signed from the home team's view, positive = home favoured),
  the same convention `gps_check` uses.
- The offence columns are the team's own values; **`Opp Def` is what the opponent's defense
  allows** (the opponent's `DefEPA/Pass` / `DefEPA/Rush`), so a higher number is a softer defense
  and reads green. `Pace` is seconds per snap, so lower is faster and reads green.
- All of them are the `team_metrics` values (`TeamMetricsRaw`, one row per team, written by the
  `pbp` source) with the usual prior-season blend (`PBP_PRIOR_WEIGHT_GAMES`).
  `OffEPA/Pass` and `OffEPA/Rush` are new: mean EPA over pass plays (a scramble is a pass) and over
  rush plays, all game states, real scrimmage plays only, the same filters as the defensive
  `DefEPA/*` columns.
- Each column is coloured as one smooth gradient against the other teams (white at the median,
  zeros excluded); the rows are formula-sorted in place, so the colours stay attached.
- A team with no player on the DK main slate is dimmed, the same way its game is.

## The results loop and `Model Check` (2026-10-02)

`dfs results update` scores every completed week against what actually happened and rebuilds the `Model Check`
tab from `data/results/` (it also runs at the end of `dfs week close`, and never fails that command). Nothing on
the tab is typed, and nothing is part of `dfs sync`.

**Which projection.** Each player's projection is the **last TFFB snapshot taken before his own kickoff**
(`GameStart`), not before the Sunday 1 pm games; `Snapshot` in `data/results/scored_<season>_wNN.csv` records which.
TFFB only projects the Sunday main slate, so in practice one snapshot serves a whole week.
**TFFB's `GameStart` is Eastern wall-clock time with a trailing "Z"** (`...T16:05:00Z` is the 4:05 pm ET game --
checked against nflverse's own kickoff times, which match TFFB's strings exactly), so it is localised to
`America/New_York` (DST-aware) before comparing it with a UTC snapshot time. Read as true UTC it discards every
Sunday-morning snapshot. The same conversion (`kickoff.py`) now drives `dfs lineups late-swap`'s locked/open status
and swap candidates and the launcher's "games started / finished" state, which used to read the column as UTC.

**Derived fields are recomputed, never read.** `ValAdj`, `Flags` and the rosterable pool come from
`derived.build_edge_frame` over the week's selected projections and every other source *as of* the week's reference
time (the snapshot most players used), with today's code. A source with no snapshot that early is absent for that
week (pbp before 9/25, Sleeper/FantasyPros before 9/24), exactly as on a live sync, so older weeks carry fewer flags.

**Actual DK points** come from nflverse's free `stats_player` / `stats_team` files and the schedule's final scores
(`results_actual.py`, scoring in `dk_scoring.score_offense_actual_row` / `score_dst_actual_row`):

- *Offense:* the DK Classic table with the REAL yardage bonuses (a hard +3 at 300 passing / 100 rushing / 100
  receiving yards, not the projection path's expected value), -1 per interception and lost fumble, +2 per
  two-point conversion, +6 per kick/punt/FG return TD and per offensive fumble-recovery TD.
- *Defense* (not in `stats_player`): sacks +1, interceptions +2, fumble recoveries +2, safeties +2, blocked kicks +2,
  2-point/extra-point returns +2, and +6 for every defensive or special-teams TD (`def_tds` + `special_teams_tds` + the
  defenders' `fumble_recovery_tds`, which nflverse reports separately from `def_tds`), plus the points-allowed tier
  (0 -> +10, 1-6 -> +7, 7-13 -> +4, 14-20 -> +1, 21-27 -> 0, 28-34 -> -1, 35+ -> -4) on the opponent's final score.
- **Which points count as "allowed" is not stated on DK's scoring table**, and DK's help pages could not be read
  here. `dk_scoring.DST_POINTS_ALLOWED_EXCLUDES_OPP_DEF_ST_TDS = True` charges a defense the opponent's score LESS
  the opponent's own defensive/special-teams TDs (the common convention). Over Weeks 1-3 flipping it changes **4 of
  96** team-games by exactly 1 point; none of the 23 defenses in Sam's real DK file is affected, so the data cannot
  settle it.
- **Checked against nflverse and against DK itself.** nflverse's `fantasy_points_ppr` scores interceptions and lost
  fumbles at -2 and has no bonuses, so `DK - fantasy_points_ppr = bonuses + interceptions + lost fumbles` for every
  player-week: **0 of 1,114 offensive player-weeks break the identity** (`results_actual.identity_breaks`). Against
  the real DK contest-standings file in `data/ownership_log.csv`: **102 of 102 offensive players** match DK's own
  `FPTS` to the hundredth and **22 of 23 defenses** (the 23rd, the Panthers, is one point off, unexplained). That file
  is logged as "Week 3" but its points match Week 2 -- it was exported on a Tuesday, after the calendar had rolled.

**Join and coverage.** Offense joins to DraftKings by name + team + position through `player_join` (the same join as
every other source); a defense joins by team. Each main-slate player is `scored` (found a stat line that week),
`dnp` (no stat line that week but present elsewhere in the season file: inactive, injured or benched -- counted as
joined, **never as an actual of 0**) or `unmatched` (found nowhere; written to `data/results/unmatched_<season>_wNN.csv`).
Coverage is reported for both populations (the rosterable pool, and everyone with `ProjPts` > 0). Nearly all
unmatched rosterable players are backup QBs and rookies with no stat line anywhere in the file, plus Travis Hunter
(a CB in nflverse, deliberately not joined).

**The tables** (`results_analysis.py`; every one shows n, any cell with n < 30 is "thin", every rate carries a 90%
Wilson interval, and an empty answer says "not enough data yet"):

- *Accuracy:* bias = actual minus projected (negative = the projection ran high), MAE, calibration slope (OLS of
  actual on projected; 1.0 = calibrated, below 1 = too extreme), R squared, Spearman within position, calibration
  buckets (0-5 .. 20+) and a position x salary-tier split. R squared is expected to be low (public studies find 3-23%).
- *Sources compared:* TFFB, Sleeper, FantasyPros and `AggPts` on the rows where all three exist (Week 3 only so far,
  labelled), plus the head-to-head: for each player, whether `AggPts` or TFFB landed closer (exact ties are not
  decided). Week 3: `AggPts` wins 142 of 230 = 61.7% (public study: about 63%).
- *Ceiling:* hit rate, implied quantile (1 - hit rate) and pinball loss at tau = 0.80 / 0.85 / 0.90. Raw pinball loss
  shrinks as tau rises, so comparing raw losses across tau always favours the highest; the reported "best tau" is
  the highest pinball SKILL (1 - loss / loss of the best constant quantile), which is scale-free.
- *ValAdj:* quintiles within position, pooled; the mean of actual minus salary-expected points (salary-expected is
  fit on ACTUAL points against salary, per position, over the weeks scored) and the rank correlation of `ValAdj` with
  that residual.
- *Salary multiple:* the share reaching 3x (cash line) and 4x (GPP line) salary per $1,000, by projected `Val` band.
- *Flags:* per flag, n, mean actual minus projected and the unflagged same-position comparison. Reported, not judged.
- *Consistency over time:* each source's week-by-week MAE and its coefficient of variation once there are 4+ weeks.

## Player Pool ordering: tag group, then salary (Part 7.10)

Sam, 2026-09-17: *"The pool should order players by position by salary
high to low, but grouped by Both, Cash, GPP."*

Each of Player Pool's five position blocks sorts on two keys, in this
order:

1. **Pool tag rank**, ascending -- `Both`, then `Cash`, then `GPP`, per
   `sources.edge.POOL_TYPE_SORT_ORDER`. A `Both` player is usable in
   either contest type, so he's core and sits first.
2. **Salary**, descending -- within each tag group.

Mechanically, `sheet_pool_formulas._union_array` builds each row as a
(Name, Salary, TagRank) triple, and `_name_formula` sorts on it:

```
SORT(UNIQUE(union), 3, TRUE, 2, FALSE)
```

`TagRank` comes from `MATCH(pool_tag, {"Both","Cash","GPP"}, 0)` --
generated from `POOL_TYPE_SORT_ORDER`, never hand-written into the
formula string, so renaming or adding a tag only ever means editing that
one Python list. **This is deliberately a separate list from
`POOL_TYPE_OPTIONS`** (the dropdown's own order, `["", "Cash", "GPP",
"Both"]`): `"Both" < "Cash" < "GPP"` sorts correctly alphabetically too,
by coincidence -- relying on that would silently break the moment a tag
is renamed or a fourth one added, with nothing to indicate it broke.

A row whose Pool tag doesn't match any of the three (the control cell's
typed name, when its EdgeRaw lookup comes back blank or the player isn't
in EdgeRaw at all) gets `_UNKNOWN_TAG_RANK` (`len(POOL_TYPE_SORT_ORDER) +
1` = 4) -- sorts after every real tag group, never into an arbitrary
position among them.

**A Sheets-formula subtlety worth knowing before touching this again:**
`MATCH` does not broadcast elementwise against a multi-cell range on its
own -- `{range, MATCH(range, {...}, 0)}` resolves to `#REF!`. It only
broadcasts correctly when it's itself one of `FILTER`'s own array
arguments (confirmed empirically on the template's Scratch tab before
this shipped), which is why the tag-rank column is computed INSIDE
`_union_array`'s existing `FILTER(...)` call rather than joined on
afterward. The control cell's own tag lookup is a scalar (one cell, not a
range), so it needs no such handling.

## % of Cap (Lineups only)

`Lineups`' `% of Cap` (renamed from `% of Own` in Phase 6, Part 7.9,
`% of Rstr` before that in Part 2) is this player's `DK Sal` as a share
of the **salary cap** -- `config.toml`'s `[lineups] salary_cap`, never
hardcoded 50000:

```
= IF($A<row>="", "", <DK Sal cell> / <salary_cap>)
```

This column had never been documented anywhere before Part 7.9, which is
how it stayed mislabeled this long. It is **not** derived from `Own%` or
rostership despite its old names implying that -- Sam confirmed live,
2026-09-17, that the intended meaning is cap allocation: "what percentage
of my total lineup salary is this player taking up." The original
formula (`=F<row>/F$<totals_row>`, Part 1.2) divided by the block's own
running salary TOTAL instead of the cap -- correct only once a lineup was
complete, and actively misleading before then: three players typed in, a
$24,000 combined salary, each read `~33%` of that partial total rather
than its true `~16%` share of a $50,000 cap. Dividing by the cap constant
also removes the `#DIV/0!` Part 1.2 previously guarded against at the
source (a fixed denominator can't divide by zero) -- the only guard still
needed is the blank-slot case (`$A<row>=""`), not the whole-block-empty
case.

## Lineup-level metrics block (Part 7.5, 2026-09-18)

Sam's own spec: "the highest impact-per-effort item available" -- every
published DFS target is a LINEUP property, and the tool had been
entirely player-level. Two columns remain, `sheet_lineup_metrics.py`,
each written once per lineup block onto its own TOTALS row.

**Removed, Round 5 item 1c (2026-09-29):** `Stack`, `Bring-back`,
`Own% Used` and `Sub-10%` (Sam doesn't use them; `Sub-10%` never worked
to his eye). Stack shape is shown by subtle correlation tints on the
pick rows instead (item 4). `dfs setup remove-lineup-metrics` deletes
them from a sheet that still has them.

**`Games`** -- `IFERROR(ROWS(UNIQUE(FILTER(GameID_range, GameID_range<>""))),0)`,
distinct `GameID`s across the 9 picks. `ROWS`, not `COUNTA`, and the
`IFERROR(...,0)` wrapper are both load-bearing: found live (2026-09-19)
that with zero real `GameID`s in the block (nothing rostered yet),
`FILTER`'s result set is genuinely empty, which `FILTER` errors on
(`#N/A`) rather than returning nothing -- and `COUNTA` silently absorbs
that error into a valid count of 1 (an error value still "counts" as
present to `COUNTA`) *before* `IFERROR` ever sees an error to catch, so
`IFERROR(COUNTA(...),0)` never actually degrades to 0. `ROWS` does not
absorb the error -- it propagates it, so `IFERROR(ROWS(...),0)`
genuinely degrades to 0 for an empty block while still counting real
distinct games correctly once any exist.

**`Min Unique`** -- the smallest count of this lineup's own picks absent
from some OTHER lineup, minimized over every other lineup in the build:

```
= MIN( (9 - SUMPRODUCT(COUNTIF(other_lineup_1_range, this_range) > 0)),
       (9 - SUMPRODUCT(COUNTIF(other_lineup_2_range, this_range) > 0)),
       ... one term per other lineup block )
```

"This lineup's picks" and "that lineup's picks" are compared on Lineups' hidden
`Player Key` (DK's canonical name for whatever was typed -- Round 5 follow-up item 3),
so `kenneth walker` in one lineup and `Kenneth Walker III` in another are the same
player and two lineups that differ only by spelling read `Min Unique = 0`. The
"is this block empty?" test still reads the typed column: a formula-blank key cell
would count as non-empty. The same key drives the in-lineup `DUPLICATE` flag,
Exposure's counts and `Distinct QBs`, and Player Pool's `Used`/`In`; where nothing
resolves the key is the typed text itself, so two identical unresolved names still
count as duplicates. Note the counts are per SLOT: a player entered twice in one
lineup (which `DUPLICATE` flags) counts twice.

Answers "how different is my most similar other lineup" -- a portfolio-
diversification question a simple "how many total distinct players
across all lineups" count can't answer (two 9-player lineups sharing 8
picks and differing in exactly 1 slot look identical to that simpler
count as two lineups that share nothing at all, if the totals happen to
match). `O(lineups^2)` in the number of lineup blocks (each lineup's own
formula names every OTHER block's range once) -- fine at the 20-lineup
scale this sheet is built for, not something to scale past without
reconsidering the approach.

**Portfolio-level, on `Exposure`** (not `Lineups` -- Exposure is already
the portfolio-analysis tab, `sheet_views.build_exposure`): `Distinct QBs`
and `Distinct games` used across the WHOLE lineup build, plus a plain
`Shared QB?` Yes/No (a QB rostered in more than one lineup):

```
qb_names       = UNIQUE(FILTER(Lineups!Name, Lineups!Pos.="QB"))
Distinct QBs   = COUNTA(qb_names)
Shared QB?     = IF(COUNTIFS(Lineups!Pos.,"QB",Lineups!Name,"<>") > COUNTA(qb_names), "Yes", "No")
Distinct games = IFERROR(ROWS(UNIQUE(FILTER(Lineups!GameID, Lineups!GameID<>"", Lineups!GameID<>"GameID"))),0)
```

`Shared QB?` compares the count of FILLED QB slots against the count of
DISTINCT QB names -- if fewer distinct names than filled slots, at least
one QB repeats across lineups. Deliberately doesn't name WHICH QB
repeats -- Part 7.5's own spec text just asks for a flag ("Flag when two
lineups share a QB"), and a plain Yes/No is simpler and more robust than
enumerating names via a second array formula for a fact Sam can see at a
glance by scanning `Lineups`' own QB rows once flagged.

Both formulas needed a second pass, found live (2026-09-19) auditing this
exact section: `Lineups!Pos.` is the FIXED slot label, one "QB" row per
block regardless of whether a name is typed there, so a bare
`COUNTIF(Lineups!Pos.,"QB")` was always the block count (e.g. 20), never
"how many QB slots are actually filled" -- `Shared QB?` read "Yes" even
with zero real QBs rostered anywhere (20 > 0). The `Lineups!Name,"<>"`
criterion fixes it, since `Name` is typed by hand and genuinely blank
when empty (unlike `GameID`, a formula cell). Separately, `Distinct
games` needed BOTH the same header-repeat exclusion as `Games` above
(`Lineups!GameID<>"GameID"`, since every lineup block repeats its own
header row and that repeat's `GameID` cell reads the literal text
"GameID") AND the same `ROWS`-instead-of-`COUNTA` fix for the
FILTER-empty-result/`IFERROR` issue described under `Games`.

**Deliberately NOT built** (Part 7.4's own text, restated since 7.5 is
where a reader would look for it): player-level exposure caps -- "at
4-8 lineups they are actively harmful, they force Sam off his best plays
for no portfolio benefit." Exposure stays a REPORT, never a constraint.

## Late-swap lock check (`dfs lineups late-swap`)

Not a column in `EdgeRaw` -- computed on demand, reading `EdgeRaw` plus
the `Lineups` tab's typed names (`late_swap.py`).

**Lock status** (`lineup_slot_status`): for each of the 9 roster slots in
a lineup, the typed name is looked up in `EdgeRaw` by `Name`. A player is
`locked` once `now (UTC) ≥ GameStart`; `open` otherwise. A blank slot or
an unmatched name (typo, bye-week leftover) comes back as
`found=False`/`locked=None` rather than raising -- checking a half-built
lineup mid-week is a normal thing to do, not an error. A matched player
with no parseable `GameStart` also comes back `locked=None` (unknown, not
assumed either way).

**Swap candidates** (`swap_candidates`): for a given slot (`FLEX` accepts
RB/WR/TE; every other slot accepts only its own position), filters
`EdgeRaw` to players at an eligible position who are **not** already
rostered in this lineup and whose `GameStart` is both present and still
in the future, then sorts by `Leverage` descending and returns the top N.
A player with a missing/unparseable `GameStart` is **excluded**, not
included -- better to under-suggest than recommend a swap into a player
whose lock status can't actually be confirmed.

## Betting ledger (`sheet_bankroll_view.py`, Round 5 item 7, 2026-09-28)

**Odds** (American odds from Odds %, a per-row Sheet formula, `column C`):
`p` is Odds % normalized to a 0-1 fraction (`IF(x>1, x/100, x)`, so `53.3`,
`53.3%`, and `0.533` all resolve to `p = 0.533`):

- `p > 0.5` (favorite): `-ROUND(100 * p / (1 - p))`
- `p < 0.5` (underdog): `+ROUND(100 * (1 - p) / p)`
- `p = 0.5`: `+100`

Worked examples (matches `tests/test_sheet_bankroll_view.py`): 53.3% ->
-114, 50% -> +100, 40% -> +150, 75% -> -300, 20% -> +400. Blank when
Odds % is blank.

**Net** (`column F`): `Won - Entered`, blank while `Won` is blank
(pending). A loss writes `Won = 0`.

**Win / loss / push classification** (row 14's Record, and
`sheet_bankroll_view.compute_weekly_betting_stats`' Python equivalent for
the Season tab): a settled bet (`Won` non-blank) is a **win** if
`Won > Entered`; a **push** if `Won = Entered` AND `Entered > 0` (a real
stake came back even); a **loss** otherwise -- `Won < Entered`, OR
`Won = Entered = 0` (Sam, 2026-09-28: "loss, but no money lost" -- a
$0-entered promo/free bet that pays out $0 has no real stake to "push"
back, so it's scored as not having won, not as a tie). `Entered = 0` can
never itself satisfy `Won < Entered` (no negative payout), which is why
this needs its own clause rather than falling out by exclusion.

**Weekly Betting summary** (row 14) is built to the EXACT shape and
formula pattern rows 12/13 (Weekly Cash %/GPP %) already use, not a
separately-invented one (Sam, 2026-09-28: "I want to see the same
metrics... don't reinvent the wheel"):

- **%** (`B14 = D14/B7`): Betting Cost's share of `B7` (Weekly Cost) --
  same pattern as `B12 = D12/B7`.
- **Cost** (`D14`): `SUM` of settled bets' `Entered`, same `SUMPRODUCT`
  guard `entered_formula` always used.
- **Winnings** (`F14`): plain `SUM` of the ledger's `Won` column --
  blank (pending) cells are skipped by `SUM` itself, no guard needed,
  same as Cash/GPP's own `F12 = SUM(D17:D59)`-shaped Winnings cell.
- **Net** (`H14 = F14 - D14`): Winnings minus Cost, same pattern as
  `H12 = F12 - D12` -- not a `SUM` of the ledger's own `Net` column
  (mathematically equal, but this matches Cash/GPP's own formula shape
  exactly rather than an equivalent-but-different one).
- **Record** (`I14`/`J14`, `W-L-P`): the one field Cash/GPP don't have,
  placed immediately after the four that match. ROI and expected-vs-
  actual wins were dropped from this row entirely -- Sam: "Dont need roi
  and expected" (both still exist on the Season tab's year-to-date
  block, where they're meaningful across a whole season rather than one
  week).

**Wired into the existing Weekly Cost and Weekly Net rollups, not a
separate bankroll**: `B7` (Weekly Cost, the `%` denominator every row's
`%` cell divides by) was widened from `SUM(D12:D13)` to `SUM(D12:D14)` --
without this, `B14`'s own `%` is `#DIV/0!` on any week with no Cash/GPP
activity yet, and Cash/GPP's `%` readings would keep excluding a real
category of spend. `B9` (Weekly Net) was separately widened from
`SUM(H12:H13)` to `SUM(H12:H14)`. `B2` (Ending Bankroll = `B1 + B9`)
picks up Betting automatically with no change of its own. The separate
DK/PP/UD "parallel bankroll" carryover (`week.BANKROLL_CARRYOVER_CELLS`)
is untouched -- Betting is the same DK wallet as Cash/GPP, not a fourth
account.

## Season tab (Round 5, item 7d, 2026-09-28)

**Total Net** (`E`, per week): `=B+C+D` (Cash + GPP + Betting Net). Blank
inputs act as 0 in Sheets' own `+`, so a week with no bets yet still
totals correctly from just Cash/GPP.

**Cumulative columns** (`N`-`Q`, chart source only): each a running
`SUM($col$2:col{row})` -- e.g. `N5 = SUM($B$2:B5)`. `Cum. Total` is the
three cumulative columns added together (`=N+O+P`), not its own running
sum of `E`, so it can never drift from the per-bucket cumulatives even if
a row's `E` and `B+C+D` were ever briefly out of sync mid-edit.

**Year-to-date ROI** (`D23:D26`): `Net / Risked` for that bucket, over
the full season range (`SUM(B2:B19)/SUM(G2:G19)` for Cash, etc.) --
blank before any risked amount exists (`=IF(C23="","",IFERROR(B23/C23,""))`) --
see "Empty weeks read blank" below.

**Betting's YTD record/expected-vs-actual** (`E25`/`F25`): `SUM` of the
weekly `Wins`/`Losses`/`Pushes`/`Exp. Wins` columns (`J`-`M`) -- these are
NUMBERS, unlike the Bankroll tab's own weekly summary row (which only
ever holds the formatted TEXT `"1-1-1"`/`"1.5 vs 1"` and can't be summed
across weeks). `sheet_bankroll_view.compute_weekly_betting_stats` is what
turns the closing week's raw ledger rows into those numbers at `dfs week
close` time -- a pure-Python re-derivation of the same arithmetic
`record_formula`/`expected_vs_actual_formula` compute on the sheet,
verified against the same real 4-bet example (win/loss/push/pending) used
to verify those formulas live.

## Empty weeks read blank, not an error (Round 5 cleanup, 2026-09-29)

Every division and average on Results, Bankroll and Season carries a guard that returns
`""` when its inputs are empty, so a fresh week or an empty ledger shows blanks instead of
`#DIV/0!`:

- a division by a cell, `a/b`, is `=IF(b="","",IFERROR(a/b,""))` -- a blank denominator gives
  a blank, and a denominator that is 0 falls through to the `IFERROR`
  (Results `H2H %`, Bankroll `Weekly Net %`/`Weekly Cash %`/`Weekly GPP %`/`Weekly Betting %`,
  the ledgers' `% Paid`/`Place %`, Season's YTD `ROI`);
- a bare average is `=IF(COUNT(rng)=0,"",AVERAGE(rng))` (Results' totals row).

The one deliberate exception is Season's chart helper block `S:W`: `#N/A` there is what makes
the line chart stop at the last played week instead of drawing a flat line, so it stays, but
the block is hidden and the chart plots hidden data. `dfs doctor` flags a division that has
lost its guard (`empty-guards`, and `formula-ranges` for Results' `H2H %` rows);
`dfs setup guard-empty-states` puts it back.
