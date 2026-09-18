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

## ValAdj (Part 7.2, 2026-09-18) -- EdgeRaw's default sort

`ValAdj = ProjPts - E[ProjPts | Salary, Position]`

`Val` is both salary-biased (a cheap player outranks a better, pricier one
just for being cheap) and position-biased (QBs project the most points on
any slate, so they dominate a raw points-per-dollar leaderboard regardless
of who's actually the better play). `ValAdj` answers the real question
instead: is this player projected *above what this slate's own pricing
implies for his position*?

**Version 1 (this one), needs no accumulated history.** For each position,
fit an ordinary least-squares line of `ProjPts` on `Salary` across THIS
SLATE's own projections only -- `derived._val_adj_within_position` -- and
take the residual (actual minus the line's prediction at that salary).
Refit fresh every sync; nothing carries over week to week. A position with
fewer than two usable rows, or where every row shares the exact same
`Salary` (nothing to fit a slope against), gets `0` for every row in that
group -- read as "no signal available," not a real computed value. A row
missing `ProjPts` stays blank (NaN), never coerced to 0, in every case
including that degenerate one.

*Version 2*, refitting against realized points once the results-tracking
loop exists, would additionally show where the market is systematically
wrong -- a deliberate later step, not built yet.

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

Already comparable across positions by construction (a within-position
residual, same as `CeilPct` is a within-position percentile) -- so unlike
raw `ProjPts`/`Val`/`Ceiling`/`CeilVal`, `ValAdj` gets EdgeRaw's ordinary
flat, whole-tab colour scale rather than the newer per-position one (see
"Per-position highlighting" below), and Player Pool/Lineups skip
re-scaling it per position block for the same reason (`sheet_style.
GROUPED_TAB_UNSCALED_COLUMNS`).

## Per-position highlighting (ProjPts, Val, Ceiling, CeilVal)

These four are `derived.EDGE_UNSCALED_PLAYER_METRICS` -- deliberately
excluded from EdgeRaw's ordinary whole-tab colour scales
(`sheet_style.FIELD_COLOR_SCALES`), since a flat scale across every
position at once is misleading (a QB's real `ProjPts` and a DST's aren't
comparable). Instead, `sheet_style.apply_edge_position_scales` builds one
3-point (red -> yellow -> green) gradient rule per (metric, position)
pair: reads `Position` once, groups EdgeRaw's data rows by that value,
and for each group writes a Sheets conditional-format rule whose
`ranges` is that position's rows only (merged into contiguous runs
first) -- so a QB's cells are scaled only against other QBs' cells in
that same column, independent of every other position.

This relies on a single gradient rule's `ranges` accepting multiple
non-contiguous `GridRange`s with one min/mid/max computed over their
union -- confirmed empirically against the template's Scratch tab before
being trusted (two interleaved fake "positions" with very different
magnitudes; only one's ranges were included in the rule, and only that
one's cells picked up the gradient). `SheetsClient.add_color_scales_
multi_range` is the primitive; `EDGE_COLUMN_GROUPS`/Phase 4's
`apply_grouped_color_scales` couldn't be reused here since that
mechanism needs each group to already be one CONTIGUOUS row range, and
EdgeRaw's rows are sorted by `Leverage`, not grouped by position.

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
ownership is published for **more than half the slate**
(`OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5`), else `"unpublished"`. It
has exactly one job now: a data-freshness marker telling you whether
ownership has been published yet, not a second formula to reason about.

**Phase 6, Part 1.4 (2026-09-17):** this used to be `.any()` -- a single
non-zero `ProjOwn` (one early-published player, a data glitch, a bye-week
artifact) flipped the WHOLE slate to `"real"`, computing `OwnPct`/
`Leverage` as a percentile over a column that was still ~99% zeros for
everyone else. Reproduced live before the fix: this marker (`LevBasis` at
the time) read `"real"` while every `ProjOwn` on `EdgeRaw` still read
`0.0%` and every `Leverage` cell was blank. A share threshold requires
ownership to be genuinely published for a majority of the slate, not just
present for one player.

**Sort order.** `Leverage` is no longer a primary sort anywhere (Part
7.1) -- `build_edge_frame` sorts the frame by `ValAdj` descending instead
(Part 7.2, see "ValAdj" above), unconditionally, regardless of whether
`OwnStatus` is `"real"` or `"unpublished"`. Revisit no earlier than a full
season of ownership logs (Part 7.8) -- TFFB's ownership projection is
large-field, Sam plays small-field, so treat `Leverage` as directional at
best until that gap has been measured.

## GameEnv

Computed once per unique game (`derived._game_env_scores`), then broadcast
to every player in it:

```
ou_pct        = this game's Over/Under, percentile rank across every game on the slate
tightness_pct = (1 − |Spread| percentile rank across every game on the slate)
GameEnv       = (ou_pct + tightness_pct) / 2
```

Higher total (more expected scoring) and a tighter spread (more
competitive, more reason for the trailing team to keep throwing) both
push `GameEnv` up. Uses only the Vegas context TFFB already attaches to
each player (`OU`/`Spread` on `projections.csv`) -- deliberately *not*
cross-referenced against the separately-synced `nfl_odds` source, since
that source is keyed by team nickname/abbreviation rather than DK's team
codes, and building a name-matching layer just to double-check numbers
TFFB already provides wasn't judged worth the join risk.

## OverUnder, Spread

Straight passthrough from the same Vegas context `GameEnv` already
computes from (TFFB's `OU`/`Spread` fields on `projections.csv`) --
computed into `GameEnv` since the start, but never surfaced as their own
columns until Fix 2.3. Named `OverUnder`, not `OU`, so its header doesn't
collide with Player Pool/Lineups' own `O/U` column, which is sourced from
a different tab (`oddsFinal` via `PlayerPoolRaw`) and isn't guaranteed to
agree number-for-number with TFFB's figure.

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
`Flags` column's `LINE↑`/`LINE↓` keys off `ImpliedMove` specifically --
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
= SUMPRODUCT((Position_range="RB") * (COUNTIFS(Position_range,"RB",GameID_range,GameID_range) > 1)) > 0
```

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
consumer (Board's `LANDMINES` panel, the Movement view, `dfs edge`'s
terminal report, `dfs lineups late-swap`, `dfs sync --live`'s diff
report, the "Leverage plays" filter view) was repointed at `Flags`;
nothing needed the single-token `Flag` for anything except that one
boolean check.

| Priority | Flag | Condition |
|---|---|---|
| 1 | `OUT` | `Avail` is `OUT` or `IR` |
| 2 | `WIND` | `Wind ≥ 20` mph |
| 3 | `LINE↑` | `ImpliedMove ≥ +6.0` |
| 3 | `LINE↓` | `ImpliedMove ≤ −6.0` |
| 4 | `LEVERAGE` | `Leverage ≥ 30` (blank `Leverage` while unpublished can never clear this) |
| 5 | `CHALK` | `Own% ≥ 0.20` (20%) -- can only fire once ownership is real; `Own%` reads 0 for everyone until then |
| — | *(blank)* | none of the above |

`WIND_FLAG_THRESHOLD_MPH = 20.0` is a starting point, not empirically
derived. `LINE_MOVE_FLAG_THRESHOLD = 6.0` **was** retuned (Phase 6, Part
1.1, 2026-09-17) the same way `LEVERAGE_FLAG_THRESHOLD` below was: the old
flat `1.0` fired on **95.7% of the real live Week 2 slate** (605 players
with a real `ImpliedMove`) -- because `_flag_for_row` returns every
matching flag but LINE sits above LEVERAGE/CHALK in read priority, this
was drowning out every other flag. Retuned against the real odds-snapshot
history in `data/raw/nfl_odds/` (30 real per-team `|TeamPointsDelta|`
values: mean 2.87, std 1.94, quartiles 1.0/3.0/4.0, a real gap between 5
and 7 with nothing at 6) to `6.0`, which re-synced live to **4.5%** --
just under the 5-10% target band on that one day's real pull (6.7% on the
historical sample used to pick it), which is expected day-to-day variance
around a threshold tuned from history, not a sign it needs re-tuning
again from a single moment's read.

`LEVERAGE_FLAG_THRESHOLD = 30.0`
and `CHALK_OWNERSHIP_THRESHOLD = 0.20` **were** checked against a real
744-player Week 1 slate with real ownership published, after the scale
fix above: that slate's `Leverage` distribution was mean -0.01, std 16.7,
min -56.2, max 68.7 (quartiles -9.4 / -3.1 / +6.1, 90th percentile +27.0).
30.0 sits at roughly the 93rd percentile and flags 53/744 players (7.1%)
-- inside the 5-10% target band. The old flat threshold of 15 (left over
from before the scale fix, when it wasn't actually checked against a real
gap-from-ownership number) would have flagged 149/744 (20.0%) under the
corrected formula -- almost exactly the "reports everything" failure this
column exists to avoid, and consistent with what got reported live once
real ownership existed. `CHALK_OWNERSHIP_THRESHOLD` flagged 5/744 players
(0.7%) on the same slate and is otherwise untouched by this fix -- it's an
absolute ownership percentage, not a percentile. **Phase 6, Part 2
(2026-09-17):** the constant itself changed from `20.0` to `0.20` when
`Own%` (the sheet-facing name for what this section still calls `ProjOwn`
-- see the note at the top of the `Leverage and OwnStatus` section) was
rescaled from a 0-100 number to a 0-1 fraction to match its already-0-1
scale on `PlayerPoolRaw`/`Player Pool`/`Lineups`. The threshold's
real-world meaning (20% ownership) and the 5/744 flag rate above are both
unchanged -- only the number's own units moved.

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
