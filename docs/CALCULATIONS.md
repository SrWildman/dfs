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

## CeilPct

This player's `Ceiling` percentile rank **within their position** (QB vs
QB, RB vs RB, etc.), 0-100. Implementation
(`derived._percentile_within`): `series.groupby(position).rank(pct=True) *
100`. Reads as "how often would this player realistically be the optimal
play at his position" -- a proxy for upside independent of ownership.
`NaN` (missing `Ceiling`) stays `NaN` here too; pandas' `rank()` already
excludes them from the ranking rather than treating them as the lowest
value. `OwnPct` (below) is the same computation applied to `ProjOwn`.

## OwnPct, Leverage and LevBasis

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

`LevBasis` names which case is in effect, computed once for the whole
frame (not per player): `"real"` the moment *any* player that sync has
non-zero `ProjOwn`, else `"unpublished"`. It has exactly one job now: a
data-freshness marker telling you whether ownership has been published
yet, not a second formula to reason about.

**Sort order.** `build_edge_frame` sorts the frame by `Leverage`
descending once ownership is real, or by `CeilPct` descending while
`LevBasis` is `"unpublished"` (sorting by an all-blank `Leverage` column
would just return join order). The Board tab's "top leverage" panel
trusts this order directly rather than re-sorting, so it automatically
reflects whichever ranking is actually in effect.

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
`Flag` column's `LINE↑`/`LINE↓` keys off `ImpliedMove` specifically --
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

## Flag

The one column meant to be read at a glance. Evaluated in order
(`derived._flag_for_row`), and **every condition that matches is
included** -- space-separated, in priority order (e.g. a windy game with
a leveraged player reads `WIND LEVERAGE`, not just `WIND`). This replaced
a first-match-wins rule that silently hid every condition but the most
urgent one; `sheet_style.FLAG_CHIPS` matches on `TEXT_CONTAINS` rather
than `TEXT_EQ` accordingly (none of the six tokens below is a substring
of another, so this can't cross-match):

| Priority | Flag | Condition |
|---|---|---|
| 1 | `OUT` | `Avail` is `OUT` or `IR` |
| 2 | `WIND` | `Wind ≥ 20` mph |
| 3 | `LINE↑` | `ImpliedMove ≥ +1.0` |
| 3 | `LINE↓` | `ImpliedMove ≤ −1.0` |
| 4 | `LEVERAGE` | `Leverage ≥ 30` (blank `Leverage` while unpublished can never clear this) |
| 5 | `CHALK` | `ProjOwn ≥ 20%` -- can only fire once ownership is real; `ProjOwn` reads 0 for everyone until then |
| — | *(blank)* | none of the above |

`WIND_FLAG_THRESHOLD_MPH = 20.0` and `LINE_MOVE_FLAG_THRESHOLD = 1.0` are
starting points, not empirically derived. `LEVERAGE_FLAG_THRESHOLD = 30.0`
and `CHALK_OWNERSHIP_THRESHOLD = 20.0` **were** checked against a real
744-player Week 1 slate with real ownership published, after the scale
fix above: that slate's `Leverage` distribution was mean -0.01, std 16.7,
min -56.2, max 68.7 (quartiles -9.4 / -3.1 / +6.1, 90th percentile +27.0).
30.0 sits at roughly the 93rd percentile and flags 53/744 players (7.1%)
-- inside the 5-10% target band. The old flat threshold of 15 (left over
from before the scale fix, when it wasn't actually checked against a real
gap-from-ownership number) would have flagged 149/744 (20.0%) under the
corrected formula -- almost exactly the "reports everything" failure this
column exists to avoid, and consistent with what got reported live once
real ownership existed. `CHALK_OWNERSHIP_THRESHOLD = 20.0` flagged 5/744
players (0.7%) on the same slate and is otherwise untouched by this fix --
it's an absolute ownership percentage, not a percentile.

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
