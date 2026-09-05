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
value.

## Leverage and LevBasis

`Leverage = CeilPct − ProjOwn`

TFFB's `ProjOwn` (projected ownership %) reads **0 for every player**
until TFFB computes real ownership, which usually happens midweek. While
that's true, the formula above degenerates to `CeilPct` alone -- which is
still a useful ceiling-only proxy, just not the same *kind* of number as
the real gap-from-ownership metric.

`LevBasis` names which case is in effect, computed once for the whole
frame (not per player): `"real"` the moment *any* player that sync has
non-zero `ProjOwn`, else `"proxy"`. This matters because the two modes
produce numbers on different scales:
- **real**: `CeilPct` (0-100) minus actual ownership (0-100) → roughly
  **-100..100**, centered near 0. A positive number means "this player's
  ceiling rank outpaces how much he'll be owned" -- genuine leverage.
- **proxy**: `CeilPct` minus 0 → just `CeilPct` again, **0..100**,
  centered around 50. This is *not* a gap from anything; it's a raw
  percentile. Treating it like the real metric would call half the slate
  "leverage."

This is why the `Flag` column (below) uses two different thresholds
depending on `LevBasis`.

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

## Stadium, Roof, Wind

`Stadium`/`Roof` are looked up from `GamesRaw` by team code (each team's
row appears once whether it played home or away that week) -- blank if
`nflverse_games` hasn't been synced this run. `Wind` is then looked up
from `WeatherRaw` by that game's `GameId` -- blank for dome games (weather
is only fetched for `Roof == outdoors` games in the first place) or if
`weather` hasn't synced.

## LineMove

`LineMove` = `line_movement.diff_odds()`'s `TeamPointsDelta` for this
player's team, joined onto `EdgeRaw` by team code.

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
re-sync, but not the same number as `EdgeRaw`'s `LineMove`.

**`diff_odds()` internals** (`line_movement.py`): joins two `nfl_odds`
snapshots on `abbr` (Rotowire's own DK-compatible team code); computes
`SpreadDelta`, `TotalDelta`, `TeamPointsDelta` as `current − baseline` for
each; sorts by `|TeamPointsDelta|` descending. A team present in only one
of the two snapshots (a bye week resolving, a rare mid-week schedule
change) is dropped from the diff rather than guessed at.

`TeamPointsDelta` specifically is the delta in this team's Vegas-implied
point total (`total/2 ± spread/2`, computed upstream by Rotowire, not by
this project) -- a team's *own* expected points changing, not just the
game's total or spread moving in the abstract.

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
(`derived._flag_for_row`); **first match wins**:

| Priority | Flag | Condition |
|---|---|---|
| 1 | `OUT` | `Avail` is `OUT` or `IR` |
| 2 | `WIND` | `Wind ≥ 20` mph |
| 3 | `LINE↑` | `LineMove ≥ +1.0` |
| 3 | `LINE↓` | `LineMove ≤ −1.0` |
| 4 | `LEVERAGE` | `Leverage ≥ 15` if `LevBasis == "real"`, or `Leverage ≥ 85` if `LevBasis == "proxy"` |
| 5 | `CHALK` | `ProjOwn ≥ 20%` -- **real basis only**, never fires under proxy |
| — | *(blank)* | none of the above |

All five thresholds (`WIND_FLAG_THRESHOLD_MPH = 20.0`,
`LINE_MOVE_FLAG_THRESHOLD = 1.0`, `LEVERAGE_FLAG_THRESHOLD_REAL = 15.0`,
`LEVERAGE_FLAG_THRESHOLD_PROXY = 85.0`, `CHALK_OWNERSHIP_THRESHOLD =
20.0`) are **starting points, not empirically derived** -- they're
documented as such in `derived.py` directly. The proxy Leverage threshold
in particular is set high (top ~15% of the position by raw ceiling
percentile) specifically to avoid flagging half the slate before real
ownership data exists partway through the week; the real-basis threshold
was checked against a live slate and found to flag roughly 15% of each
position, which was judged reasonable.

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
