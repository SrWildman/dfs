# Sheet reference: every tab, every column

What each tab in the weekly Google Sheet is for, where its data comes from,
and what every column means -- so you don't have to reverse-engineer a
formula to know what a number represents. The sheet's own `Instructions`
tab points here for anything beyond the one-line summary it gives per tab.

Tabs are grouped by role: **synced** (a `dfs sync` source writes the whole
tab), **derived** (formulas inside the sheet, computed from synced tabs),
and **manual** (you type into these, or paste from elsewhere).

If a formula or column here doesn't match what you're actually seeing,
trust the sheet over this doc and let it be known -- this is written by
reading the live formulas, but the sheet can be hand-edited without this
doc knowing.

## Synced tabs (dfs sync writes these)

| Tab (default name) | Source | Command reference |
|---|---|---|
| `TFFBOptoRaw` | The Fantasy Footballers DFS Pass optimizer | `projections` |
| `DKSalRaw` | DraftKings, unauthenticated | `draftkings` |
| `oddsraw` | Rotowire's DK odds feed | `nfl_odds` |
| `GamesRaw` | nflverse, free/unauthenticated | `nflverse_games` |
| `WeatherRaw` | Open-Meteo, free/no key | `weather` |
| `EdgeRaw` | Computed locally from the above -- no network call | `edge` |

Your `config.toml`'s `[google_sheets.tab_mappings]` controls the actual tab
names; the table above uses the defaults. See README.md's Commands section
for the full `dfs sync` reference.

### TFFBOptoRaw

TFFB's own projections for the current week's optimizer, one row per
rosterable player (DST included).

| Column | Meaning |
|---|---|
| `Id` | DraftKings' own player ID. This is the join key everything else uses -- see `docs/SHEET_REFERENCE.md`'s note on EdgeRaw below. |
| `Name` | TFFB's player name. For DST, this is the **full team name** ("Jacksonville Jaguars"), not DK's nickname -- a real gotcha, see EdgeRaw's `Name` note below. |
| `Position` | QB/RB/WR/TE/DST. |
| `Team` | 3-letter team code (already overwritten to DK's DST nickname convention at upload time for DST rows -- e.g. `Team` reads "Chargers" for a DST row even though `Name` reads "Los Angeles Chargers"). |
| `ProjPts` | TFFB's median points projection. |
| `ProjOwn` | TFFB's projected ownership %. **Reads 0 for every player until TFFB computes it, usually midweek** -- this is TFFB's own data timing, not a sync gap. |
| `Opp` | Opponent team code. |
| `Salary` | TFFB's own salary figure (kept for reference; `EdgeRaw`/`PlayerPoolRaw` use DraftKings' own salary as authoritative instead). |
| `Ceiling` | TFFB's ceiling (high-outcome) projection. Populated for most, not all, players. |
| `ImpPts` | Vegas-implied team point total for this player's team. |
| `OU` | This game's over/under total. |
| `Spread` | This game's spread (signed). |
| `Game` | TFFB's own game identifier string. |
| `GameStart` | Kickoff time. |
| `Venue` | `H`/`A` (home/away). |

### DKSalRaw

DraftKings' own salary CSV for the current main slate, unauthenticated.

| Column | Meaning |
|---|---|
| `Position`, `Name`, `ID`, `Salary`, `TeamAbbrev`, `Game Info` | DraftKings' own fields, as exported. `ID` is the same DraftKings player ID as TFFBOptoRaw's `Id`. |
| `AvgPointsPerGame` | DraftKings' own season-average points -- not TFFB's projection. |
| `Status` | `Q`/`OUT`/`IR` when DK flags an injury designation; blank otherwise. Feeds `EdgeRaw`'s `Avail`/`Flag`. |

### oddsraw

Rotowire's DraftKings odds feed, one row per team (two rows per game).

| Column | Meaning |
|---|---|
| `team` | Team nickname (Rotowire's own naming, not DK's team code). |
| `date` | Kickoff. |
| `moneyline`, `spread`, `total` | This team's DK moneyline/spread, and the game's O/U. |
| `team_points` | Vegas-implied point total for this team specifically. |
| `home_away`, `abbr` | Home/away flag and DK-compatible team code. |

### GamesRaw

Per-game context from nflverse's free schedule data, one row per game.

| Column | Meaning |
|---|---|
| `GameId` | nflverse's own game ID. |
| `Away` / `Home` | Team codes, already normalized to DraftKings' convention (nflverse's `LA` becomes `LAR` -- the one confirmed drift between the two naming schemes). |
| `Date` / `Time` | Kickoff date and time, **in US/Eastern regardless of the stadium's own timezone** (nflverse's own convention). |
| `Stadium` | Venue name. |
| `Roof` | `outdoors`/`dome`/blank. Blank means nflverse doesn't know yet (common for retractable-roof stadiums before the roof decision is made) -- not the same as `dome`. |
| `Surface` | Playing surface. |
| `AwayRest` / `HomeRest` | Days of rest since each team's last game. |
| `DivGame` | 1 if a divisional matchup, else 0. |
| `Spread` / `Total` | Vegas closing line at the time of sync (from nflverse, independent of `oddsraw`). |

### WeatherRaw

Open-Meteo forecast for every `GamesRaw` row where `Roof == outdoors`
(dome and unknown-roof games are skipped -- nothing to forecast).

| Column | Meaning |
|---|---|
| `GameId`, `Away`, `Home`, `Stadium` | Matches the `GamesRaw` row this forecast is for. |
| `Temp` | Forecast temperature (°F) at kickoff hour. |
| `Wind` | Forecast sustained wind speed (mph) at kickoff hour. |
| `Gust` | Forecast wind gusts (mph). |
| `Precip` | Forecast precipitation (inches) in that hour. |
| `Flag` | `WIND` if `Wind` is at/above ~20mph, else blank. |

### EdgeRaw

The derived "which players are actually worth a look" tab -- computed
locally by joining the tabs above, no network call of its own. Rows are
pre-sorted by `Leverage` descending. Full design rationale in
`docs/ROADMAP.md`'s Phase 1/2; summarized here as what each column means.

**The join**: TFFB's `Id` *is* DraftKings' own player ID -- verified an
exact match, no name-matching needed for the projections↔salaries join.
One real fix that had to happen for this to work at all: TFFB's `Name` for
a DST is the full team name, but everything else (DK, and therefore any
manually-typed lineup) uses just the nickname -- `EdgeRaw`'s own `Name`
column is rewritten to the nickname for DST rows specifically so every
downstream Name-keyed lookup (see "linked into Player Pool" below)
actually matches.

| Column | Meaning |
|---|---|
| `Id` | DraftKings player ID. |
| `Name` | Player name, DK-nickname convention for DST. |
| `Position`, `Team`, `Opp` | As above. |
| `Salary` | DraftKings' own salary (authoritative) -- falls back to TFFB's figure only for the rare player TFFB projects who isn't on DK's main-slate salary list (e.g. a Thursday/Monday-only game). |
| `ProjPts`, `ProjOwn`, `Ceiling` | Passed through from TFFBOptoRaw. |
| `Val` | `ProjPts / (Salary / 1000)` -- points per $1k salary. |
| `CeilVal` | `Ceiling / (Salary / 1000)` -- blank wherever `Ceiling` is blank. |
| `CeilPct` | This player's `Ceiling` percentile rank **within their position** (0-100). The "how often could this player realistically be optimal" proxy. |
| `Leverage` | `CeilPct − ProjOwn`. While `ProjOwn` is all zeros (pre-midweek), this degenerates to `CeilPct` alone -- see `LevBasis`. |
| `LevBasis` | `"real"` once any player has non-zero `ProjOwn` this week, else `"proxy"`. Tells you whether `Leverage` is the real gap-from-ownership metric or just a ceiling-percentile stand-in. |
| `GameEnv` | 0-100 per-game score from that game's own `OU`/`Spread` (higher total + tighter spread scores higher -- more reason for both offenses to keep throwing). |
| `Stadium` / `Roof` | From `GamesRaw`, joined by team code. Blank if `nflverse_games` hasn't synced this run. |
| `Wind` | From `WeatherRaw`, joined by game. Blank for dome games or if `weather` hasn't synced. |
| `Avail` | DraftKings' own `Status` (`Q`/`OUT`/`IR`). |
| `Flag` | The one column meant to be read at a glance. Priority order (first match wins): `OUT` (from `Avail`) → `WIND` (`Wind` ≥ ~20mph) → `LINE↑`/`LINE↓` (`LineMove` past a threshold) → `LEVERAGE` (`Leverage` above a basis-specific threshold -- 15 under "real", 85 under "proxy", since proxy-mode Leverage is a raw 0-100 percentile rather than a −100..100 gap, and a flat threshold would flag most of the slate) → `CHALK` (`ProjOwn` ≥ 20%, real basis only) → blank. |
| `LineMove` | This player's team's Vegas-implied point total, change since the **start of the current NFL week** (not the previous sync -- that was tried first and dropped, since it made the number depend on how often `dfs sync` happened to run rather than reflecting a real move; see `docs/CALCULATIONS.md`). Blank until at least one `nfl_odds` sync has happened this week. Appended at the very end of the column list rather than grouped near `GameEnv` -- see `docs/ROADMAP.md`'s Phase 3 postmortem for why that positioning matters here specifically. `dfs odds movement` is a separate, terminal-only report that still diffs since the last sync. |
| `GameStart` | This player's game's kickoff time (UTC), passed through from TFFBOptoRaw. Backs `dfs lineups late-swap`'s lock-time check -- not something you'd read directly here. |
| `Pool` | A real checkbox. Tick it to put this player into `Player Pool`'s matching position block -- see "Player Pool / Lineups" below. Survives every `dfs sync` (kept by Id, not row position -- this tab is sorted by `Leverage`, so row order shifts every sync). Deliberately **not** part of the column list above -- `derived.EDGE_COLUMNS` -- since every VLOOKUP linked into `Player Pool`/`Lineups`/`PlayerPoolRaw` hardcodes column-index integers against that exact list; `Pool` sits one column past it instead. |

See `docs/CALCULATIONS.md` for the exact formula behind every EdgeRaw column above.

## Derived hub tabs (formulas inside the sheet)

**Canonical column order** (`PlayerPoolRaw`, `Player Pool`, and `Lineups`
all share the same left-hand columns, in this order, before each tab's own
extra columns and the `link-edge` block): `Name`(A) `Pos.`(B) `Team`(C)
`DK Sal`(D) `O/U`(E) `Spread`(F) `Team Implied`(G) `Opp.`(H) `Venue`(I)
`OppPosRank`(J) `Pts`(K) `Ceil`(L) `Val`(M) `Rstr%`(N). This is recorded
explicitly here because it's the one thing that's already drifted once:
two sheets built from the same source (the live sheet and an older copy
of the template) ended up with `Venue`/`Ceil` in different positions after
independent hand-edits, while each stayed internally consistent -- nothing
caught it until a cross-sheet audit compared them directly. No Python code
hardcodes these column positions (only the sheet's own formulas do), so
this doesn't matter for `dfs` itself, but it matters for keeping the live
sheet and the template from drifting apart again -- `dfs sheets doctor`
checks the columns each sheet's formulas actually depend on (`EdgeRaw`'s
header, the linked `EdgeRaw` block, header repeats), but it does not check
this specific ordering, since nothing breaks if it moves as long as both
sheets move together. If you ever reorder these, update this table.

### PlayerPoolRaw

The sheet's own hub tab: one row per player, aligned 1:1 with `DkSalClean`
(row 2 here = row 2 there), pulling from every raw tab so `Player Pool`
and `Lineups` only ever need one VLOOKUP target. **This is the tab where
the Phase 8 formula-corruption bug happened** (see CONTRIBUTING.md) --
its own formulas use hardcoded column-index integers into `TFFBOptoRaw`
and elsewhere, which don't auto-update if a column gets inserted upstream.

| Column | Source |
|---|---|
| `Name`, `Pos.`, `Team`, `DK Sal` | `DkSalClean` (this week's slate, row-aligned). |
| `O/U`, `Spread`, `Team Implied` | `oddsFinal`, VLOOKUP by team. |
| `Opp.` | `DkSalClean`. |
| `Venue` | `TFFBOptoRaw`, VLOOKUP by Name (DST rows look up against `TFFBOptoRaw`'s Team column instead of Name, since DK's DST name doesn't match TFFB's own Name field for DST -- the same mismatch `EdgeRaw` fixes at its source instead). |
| `OppPosRank` | `SoSComb`, this player's opponent's strength-of-schedule rank at this position. |
| `Pts`, `Ceil` | `TFFBOptoRaw`'s `ProjPts`/`Ceiling`, same DST special-casing as `Venue`. |
| `Val` | `Pts / (DK Sal / 1000)`, computed in-sheet (independent of `EdgeRaw`'s own `Val`, though they should agree). |
| `Rstr%` | `TFFBOptoRaw`'s `ProjOwn`. |
| `CeilVal`, `CeilPct`, `Leverage`, `LevBasis`, `GameEnv`, `Stadium`, `Roof`, `Wind`, `Avail`, `Flag` | **Linked from `EdgeRaw`** by `dfs sheets link-edge` (VLOOKUP by Name) -- see EdgeRaw's own column docs above for what each means. Appended at the far right, grouped so they can be collapsed from the sheet UI. `LineMove`/`GameStart` (both added after `link-edge` was last run) are **not yet included here** -- adding either means re-running `link-edge` against a manually-cleared linked block on every sheet it's been applied to, not done yet. Until then, `LineMove` is only visible on `EdgeRaw` itself (and `GameStart` has no reason to be linked here anyway -- `dfs lineups late-swap` reads it straight from `EdgeRaw` locally). |

### Player Pool / Lineups

Where you actually build lineups. `Lineups` is still typed: type a
player's name into the `Name` column of a roster slot, and every other
column VLOOKUPs off that name against `PlayerPoolRaw`. `Lineups` repeats
a 9-row roster block (QB, RB, RB, WR, WR, WR, TE, FLEX, DEF) once per
lineup you're building, each with its own salary total row underneath.

`Player Pool`'s `Name` column is **not typed** -- it's a
`SORT(FILTER(...))` formula per position block, pulling in whichever
players are ticked in `EdgeRaw`'s `Pool` checkbox column (see EdgeRaw's
column docs above). Tick a player there instead of typing them here; the
block fills in sorted by name, capped at that position's slot count (QB
10, RB 20, WR 25, TE 10, DST 10), with an `Overflow` column (far right,
`Z`) warning per position if more players are ticked than the block has
room for -- a ticked player is never silently dropped. Every other
column still VLOOKUPs off `Name` the same as before, so nothing past
column A changed. See `sheet_pool_formulas.py`/`sources/edge.py` for the
mechanism and `CONTRIBUTING.md`'s changelog for the block-resize history.

Columns mirror `PlayerPoolRaw`'s, pulled the same way, plus the same
linked `EdgeRaw` block at the far right (`dfs sheets link-edge`).
`Lineups` additionally has `% of Rstr` (this pick's `Rstr%` as a share of
the lineup's total `Rstr%`) and a per-lineup salary-remaining row.

`Lineups`' column O ("Check", `dfs sheets polish`) is a per-lineup
guardrail: on each roster slot, `DUPLICATE` if that name appears twice in
the same lineup, else that pick's linked `Avail` flag (`OUT`/`IR`/`Q`) if
it has one; on the totals row, `OVER` the salary cap, `INCOMPLETE` (fewer
than 9 picks), or `OK`. Formula values, not just formatting -- but
additive-only, since O was an empty spacer column nothing else wrote to.

`Lineups` also has a 10-row frozen "pool deck" at the very top (`dfs
sheets add-pool-deck`): a sortable, filterable window into `Player
Pool` -- pick a position (B1) and a sort field (D1), set a starting rank
(F1), and the six rows below (4-9) show that slice of the pool with
every metric column `Lineups` itself already has (Salary, Pts, Ceil,
Val, CeilVal, Leverage, Flag, ...), not just names. Row 3 mirrors `Player
Pool`'s header; a hidden `PoolSort` tab materializes the sorted/filtered
result the window formulas index into (Sheets' INDEX needs a range
reference, not a live array, to support "start at rank N"). Read-only
and purely additive -- the header row and every lineup block moved down
by exactly 10 rows to make room (`weekly_reset.LINEUPS_NAME_BLOCKS`), but
nothing about how a lineup is built changed. Sized this way (not the
14-row / 10-row-window size it first shipped as) after a live check
showed two full lineup blocks didn't fit below 14 frozen rows on a 16"
laptop screen. Superseded a first, names-only "Bench" attempt at the
same idea -- see CONTRIBUTING.md's changelog for the full history.

`dfs lineups clear` wipes `Lineups`' typed-in `Name` columns (and
`Scratch`/`DK Upload`, below) at the start of a new week. `Player Pool`'s
`Name` column is skipped -- it's a formula now, not a typed value, and
the actual per-week state (which players are ticked) lives in `EdgeRaw`,
which `dfs sync` already rewrites every week regardless.

On gameday, `dfs lineups late-swap` reads every built lineup's `Name`
column here directly (not `PlayerPoolRaw`, not `DK Upload`) and checks
each rostered player's real kickoff time (`EdgeRaw`'s `GameStart`) against
now: locked players are left alone, and for anyone not locked yet it shows
current `ProjPts`/`Leverage`/`Flag` plus the best still-open alternatives
at that slot, so a late swap is a read of one report instead of manually
cross-referencing kickoff times against your roster.

## Manual / output tabs

### SoSQB / SoSRB / SoSWr / SoSTE / SoSDef

Strength-of-schedule rankings, pasted in by hand from The Fantasy
Footballers' Foot Clan Premium each week (**not yet automated** -- see
`legacy/README.md`). Each has a per-week opponent rank and points-allowed
column; `SoSComb` combines all five into one lookup table keyed by team
and position, which `PlayerPoolRaw`'s `OppPosRank` reads.

### Scratch

A blank grid (same roster-slot columns as `Lineups`) for drafting a
lineup idea before committing it to a real `Lineups` slot. No formulas;
`dfs lineups clear` wipes it each week.

### DK Upload

Where you pair a finished lineup to a real DK contest entry: DraftKings'
own "bulk edit entries" layout (Entry ID, Contest Name, Contest ID, Entry
Fee, then the 9 roster-slot columns). `dfs export` reads this tab as-is,
validates it against the current salary data, and writes DK's own
upload-ready CSV. Pairing lineups to entries stays a manual step.

### EntriesRaw / GPPin / DKLineupsRaw / DKLineupsFinal

Contest-entry tracking, same roster-slot shape as `Lineups`/`DK Upload`.
`EntriesRaw` is where you paste your exported DK contest history
(`Import options: Replace Data at Selected Cell`, active cell A1);
`GPPin`/`DKLineupsRaw`/`DKLineupsFinal` derive views from it. `dfs`
doesn't read or write any of these yet -- see README.md's status table
("Live DK contest history / entries" is the corresponding not-yet-built
item).

### Exposure

A pivot-style count of how many times each typed-in `Name` appears across
`Lineups` -- how concentrated your week's lineups are on a given player.
No `dfs` involvement.

### Bankroll

Cash/GPP ledgers plus starting/ending bankroll summary figures.
`dfs bankroll sync --csv <file>` appends new contest results here (see
README.md's "Bankroll sync" section for the full behavior and the
`[bankroll.cash]`/`[bankroll.gpp]` config shape) -- it only ever writes
into its configured row range and dedupe-key column, never touching the
summary figures or any other formula.

### Results

A season-level results log (Week, Cash Pts/Line, H2H Entered/Win, Red/
Blue/Black), one row per week -- unlike every other tab here, this one is
**not** reset by a new weekly sheet copy. Each row's `Cash Results`
(column D) and `H2H %` (column G) are formulas already built in
(`=IF(B, B>C, "")` / `=F/E`); every other column is typed by hand. `dfs
week new` copies the typed-value columns from the outgoing sheet to the
new one (config.toml's `[results]` table controls the row range) so this
log keeps accumulating across weekly copies instead of resetting to empty
every week -- see `dfs.week.extract_results_value_columns` and `dfs week
new`'s own docstring for exactly which columns are carried and which
formula columns are deliberately left alone.
