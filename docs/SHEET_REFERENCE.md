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
names; the table above uses the defaults. Run `dfs sync --help` for the
full `dfs sync` reference.

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
| `Pool` | A dropdown, column A -- blank / `Cash` / `GPP` / `Both` (Fix 2.11; was a plain checkbox). Any non-blank value puts this player into `Player Pool`'s matching position block, see "Player Pool / Lineups" below -- picking `Cash` or `GPP` specifically is a per-week note to yourself about which contest type(s) you want them in for, not enforced anywhere else yet. Survives every `dfs sync` (kept by Id, not row position -- this tab is sorted by `ValAdj`, so row order shifts every sync). Deliberately **not** part of the column list below -- `derived.EDGE_COLUMNS` -- since every VLOOKUP linked into `Player Pool`/`Lineups`/`PlayerPoolRaw` hardcodes column-index integers against that exact list; `Pool` sits ahead of it instead (`derived.EDGE_DATA_OFFSET` is what every column-position calculation elsewhere adds to account for this). |
| `Id` | DraftKings player ID, column B. Hidden by `dfs setup polish` -- never a useful thing to look at, and hiding it (rather than grouping) puts `Pool` and `Name` visually side by side. |
| `Flag` | Hidden (Phase 6, Part 7.9) -- just the single highest-priority matching condition (see `Flags` below for the full list). Kept, not deleted, since other formatting/filtering logic keys off it as a boolean/categorical value; nobody reads this one directly. |
| `Name` | Player name, DK-nickname convention for DST. |
| `Position`, `Team`, `Opp` | As above. |
| `Salary` | DraftKings' own salary (authoritative) -- falls back to TFFB's figure only for the rare player TFFB projects who isn't on DK's main-slate salary list (e.g. a Thursday/Monday-only game). |
| `ProjPts`, `Own%`, `Ceiling` | `ProjPts`/`Ceiling` passed through from TFFBOptoRaw as-is. `Own%` is TFFBOptoRaw's own `ProjOwn`, renamed and rescaled from a 0-100 number to a 0-1 fraction (Phase 6, Part 2) so the name and scale match `Own%` everywhere else on the sheet -- one shared name across `EdgeRaw`/`PlayerPoolRaw`/`Player Pool`/`Lineups`, one scale. Still reads 0 for every player until TFFB computes real ownership, usually midweek. `ProjPts`/`Ceiling` get the per-position colour scale described below `Own%` does not. |
| `Val` | `ProjPts / (Salary / 1000)` -- points per $1k salary. Per-position colour scale, below. No longer EdgeRaw's sort key (see `ValAdj`) -- kept for its `>= 3.0` cash-line threshold. |
| `ValAdj` | `ProjPts - E[ProjPts \| Salary, Position]` -- Part 7.2's replacement for `Val` as **EdgeRaw's default sort**: a per-position regression residual, so it isn't biased toward cheap players or QBs the way `Val` is. See `docs/CALCULATIONS.md` for the regression. Ordinary whole-tab colour scale (already position-comparable by construction), not the per-position one below. |
| `CeilVal` | `Ceiling / (Salary / 1000)` -- blank wherever `Ceiling` is blank. Per-position colour scale, below. |
| `CeilPct` | This player's `Ceiling` percentile rank **within their position** (0-100). The "how often could this player realistically be optimal" proxy. |
| `Leverage` | `CeilPct` minus an internal ownership percentile (computed the same way, from `Own%`) -- both are percentiles, so this is a real gap, roughly −100..100, centered near 0. Blank while `Own%` is all zeros (pre-midweek) -- see `OwnStatus`. Demoted off EdgeRaw's own decision columns into the collapsed Ceiling detail group in Phase 6, Part 2 (Part 7.1). The ownership percentile itself (`OwnPct`) is **not a sheet column any more** -- Part 7.9 dropped it entirely, since this Leverage formula was its only consumer anywhere in the codebase (verified by grep before removing). |
| `OwnStatus` | Renamed from `LevBasis` in Phase 6, Part 7.9 (Leverage's own demotion left this marker gating `Own%`, a spine column, not describing Leverage -- the old name no longer said what it does). `"real"` once any player has non-zero `Own%` this week, else `"unpublished"`. A data-freshness marker only -- tells you whether `Leverage` has a real number yet. |
| `GameEnv` | 0-100 per-game score from that game's own `OU`/`Spread` (higher total + tighter spread scores higher -- more reason for both offenses to keep throwing). |
| `OverUnder`, `Spread` | Straight passthrough of the same TFFB Vegas fields `GameEnv` is computed from. `OverUnder` (not `OU`) so it doesn't collide with Player Pool/Lineups' own `O/U`, sourced from a different tab. |
| `OppPosRank` | This player's OPPONENT's strength-of-schedule rank at this player's own position (1 = toughest matchup). Computed natively in Python from the already-synced `sos_qb`/`sos_rb`/`sos_wr`/`sos_te`/`sos_dst` frames (Phase 5, 2026-09-16, Sam: "all data should be in edge raw") -- the same value `PlayerPoolRaw`'s own `OppPosRank` computes via a `SoSComb` formula, just computed here without a live Sheets lookup. Blank for a position whose TFFB sync hasn't run yet, same graceful-degradation treatment as `Stadium`/`Roof`/`Wind`. |
| `GameID` | Part 7.4: this player's game, `nflverse_games`' own ID format (`"2026_02_DET_BUF"` -- season, week, away, home). Was already computed internally to join `Stadium`/`Roof`/`Wind`, just never surfaced before now. What makes a stack visible: two players sharing this value are in the same game. |
| `TmRank` | Part 7.4: this player's salary rank within his own team AND position -- 1 is the highest-salaried player at that position on that team (read alongside `Position`: "WR1", "RB1"). **A crude proxy for target hierarchy, not a measurement of it** -- salary reflects the market's own belief, not actual target share. No colour scale, deliberately -- see `docs/CALCULATIONS.md`. |
| `Stadium` / `Roof` | From `GamesRaw`, joined by team code. Blank if `nflverse_games` hasn't synced this run. |
| `Wind` | From `WeatherRaw`, joined by game. Blank for dome games or if `weather` hasn't synced. |
| `Avail` | DraftKings' own `Status` (`Q`/`OUT`/`IR`). |
| `Flags` | The one column meant to be read at a glance (renamed from `Flag` in Phase 6, Part 7.9 -- see the hidden `Flag`, above, for the single-highest-priority counterpart). Every matching condition is included, space-separated, in priority order (e.g. `WIND LEVERAGE`) -- not just the first match: `OUT` (from `Avail`) → `WIND` (`Wind` ≥ ~20mph) → `LINE↑`/`LINE↓` (`ImpliedMove` past a threshold -- `TotMove`/`SpdMove` don't drive this) → `LEVERAGE` (`Leverage` ≥ 30; blank `Leverage` while unpublished can never clear this) → `CHALK` (`Own%` ≥ 0.20 (20%), can only fire once ownership is real) → blank. |
| `ImpliedMove`, `TotMove`, `SpdMove` | This player's team's Vegas-implied point total / the game's total / the spread, each changed since the **start of the current NFL week** (not the previous sync -- that was tried first and dropped, since it made the number depend on how often `dfs sync` happened to run rather than reflecting a real move; see `docs/CALCULATIONS.md`). `ImpliedMove` was called `LineMove` before Fix 2.2, when it was the only one of the three surfaced; `TotMove`/`SpdMove` are new. Blank until at least one `nfl_odds` sync has happened this week. Sits in its own collapsed Movement group (Phase 6, Part 2) rather than grouped near `GameEnv` -- see `docs/ROADMAP.md`'s Phase 3 postmortem for why that positioning matters here specifically. `dfs odds movement` is a separate, terminal-only report that still diffs since the last sync. |
| `GameStart` | This player's game's kickoff time (UTC), passed through from TFFBOptoRaw. Backs `dfs lineups late-swap`'s lock-time check -- not something you'd read directly here. |
| `GAME`, `CEIL`, `MOVE`, `WX` | Zone labels, not data -- one sits immediately before each collapsed group (Game/Ceiling detail/Movement/Weather) it names, always visible, blank in every row below the header. See the canonical column order section (Player Pool/Lineups, above) for the full rationale; EdgeRaw has the same four for the same reason. |

A basic filter (Data > Create a filter, `dfs setup add-filters`) puts a
visible sort/search arrow in every header cell -- click one to sort or
search that column, the primary and most discoverable mechanism. Four
saved filter views (Data > Filter views, secondary) sort/filter within
the view only, never touching the stored rows: "Pool picking" (the whole
tab, no preset -- a filter view's own column header gets a type-ahead
search box for free), "Leverage plays" (`Flag = LEVERAGE`), "Available
only" (`Avail` blank), "In my pool" (`Pool = TRUE`). `EdgeRaw` itself is
deliberately **not** protected (`dfs setup protect`) -- ticking `Pool` is
the tab's entire reason to exist.

**Per-position colour scales** (2026-09-18): `ProjPts`/`Val`/`Ceiling`/
`CeilVal` each get a real red-to-green colour gradient, computed
independently **within each position** rather than across the whole
column -- a QB's real point totals and a DST's aren't on the same scale,
so one flat gradient across all 742 rows would be misleading (this is
exactly why these four were excluded from every other column's
whole-tab scale in the first place). Built for Sam's actual workflow:
filter to a position, sort by one of these, look for outliers -- a flat
white column made that hard. Every other field with a colour scale
(`GameEnv`, `CeilPct`, `ValAdj`, `Leverage`, `OppPosRank`, `OverUnder`,
`Spread`, `ImpliedMove`/`TotMove`/`SpdMove`) already scales sensibly
across the whole tab and is unaffected -- `ValAdj` in particular is
already a per-position residual by construction, so a flat scale on it
is correct, not a gap. `Salary` is never colour-scaled anywhere on
this sheet -- see the Ceiling/Val note in `docs/CALCULATIONS.md`.

See `docs/CALCULATIONS.md` for the exact formula behind every EdgeRaw column above.

## Derived hub tabs (formulas inside the sheet)

**Canonical column order** (Phase 3, redesigned Phase 6 Part 2 -- see
CONTRIBUTING.md's changelog for both): `PlayerPoolRaw`, `Player Pool`,
and `Lineups` all share the same **shared spine** (Name through Flag,
identical order on all three), with everything else behind collapsed
groups in a fixed left-to-right order -- Game, Ceiling detail, Movement,
Weather -- and `Id` hidden outright at the very end, not part of any
group. Each tab's own extra columns are inserted at a deliberate spot
rather than appended past the end. The exact order lives in
`sheet_columns.py` (`PLAYER_POOL_RAW_COLUMN_ORDER`/
`PLAYER_POOL_COLUMN_ORDER`/`LINEUPS_COLUMN_ORDER`) -- this table mirrors
it for reference, but that module is the source of truth if they ever
disagree:

| Zone | Columns |
|---|---|
| IDENTITY (spine) | `Name` `Pos.` `Team` `Opp.` |
| DECISION (spine) | `DK Sal` `Pts` `Val` `ValAdj` `Ceil` `CeilVal` `Own%` `Avail` `Flags` |
| — label `GAME` — | (always visible, not part of any group) |
| GAME (collapsed) | `O/U` `Spread` `Team Implied` `GameEnv` `OppPosRank` `GameID` `TmRank` |
| — label `CEIL` — | (always visible, not part of any group) |
| CEILING DETAIL (collapsed) | `CeilPct` `Leverage` `OwnStatus` |
| — label `MOVE` — | (always visible, not part of any group) |
| MOVEMENT (collapsed) | `ImpliedMove` `TotMove` `SpdMove` `GameStart` |
| — label `WX` — | (always visible, not part of any group) |
| WEATHER (collapsed) | `Venue` `Stadium` `Roof` `Wind` |
| INTERNAL (hidden, not grouped) | `Id` `Flag` |

**Zone labels** (added the same day as Part 7.9, a usability fix Sam
raised mid-session rather than something in the original spec): each
collapsed zone now has a real, always-visible one-word label column
immediately before it -- `GAME`/`CEIL`/`MOVE`/`WX` -- so you can tell
which `+`/`-` control is which without clicking to find out. A label
can't sit INSIDE the zone it names (collapsing hides every cell in a
group's range, label included), and it can't be a blank spacer either
(an unlabeled `+` is the exact "guess and click" problem being fixed).
This is also what makes the four zones independently collapsible at
all -- verified live (a raw `addDimensionGroup`/depth-nesting test on the
template's Scratch tab) that Sheets merges any adjacent same-depth column
groups into one regardless of nesting, so a real gap column between
zones is the only way to get four separate controls instead of one
merged region. No data lives in a label column below its own header
text -- it's a pure visual divider, styled with a light neutral tint
(`sheet_style._apply_zone_label_style`) so it reads as one rather than an
unexpectedly-blank data column.

`Own%` is the one column that changed **name**, not just position --
`Rstr%` on these three tabs, `ProjOwn` on `EdgeRaw`, both became one
shared `Own%` in Phase 6, Part 2 (a shared spine can't have a column that
means the same thing but is spelled differently per tab). `Leverage`
moved out of DECISION into the new CEILING DETAIL group in that same
pass (Part 7.1) -- it's no longer on the visible spine. `Venue` moved out
of IDENTITY into WEATHER -- demoted the same as everything else off the
spine, deliberately not gridfathered into IDENTITY just because it used
to sit there. Phase 6, Part 7.9 made three further changes: `OwnPct`
(which used to sit in CEILING DETAIL) is dropped entirely, not just
moved -- its only consumer anywhere in the codebase was the Leverage
formula, verified by grep before removing; `LevBasis` renamed to
`OwnStatus`; and `Flag`/`Flags` split for real (verified live that `Flag`
already held every matching condition, not the single first-match value
originally assumed) -- `Flags` (everything that fired) took `Flag`'s old
spine slot, and `Flag` (just the single highest-priority token) moved
into INTERNAL beside `Id`, hidden, kept only because other
formatting/filtering logic keys off it as a boolean value.

`PlayerPoolRaw` is exactly this, 37 columns (was 30 right after Part 7.9's
metric audit, 34 through Phase 5H before that -- Phase 5, Section I
removed the four reserved-but-never-wired `SoS 1..4` placeholders once
the real strength-of-schedule sync landed straight into `OppPosRank`
instead; see CONTRIBUTING.md's changelog -- the zone-label usability fix
then added the four label columns above, landing back at 34 by
coincidence; Part 7.2 then added `ValAdj`, one more, to 35; Part 7.4 then
added `GameID`/`TmRank`, two more, to 37). `Player
Pool` inserts `Edge ↗` (A3) right after `Opp.` (i.e. right after
IDENTITY, since `Venue` no longer sits there) and appends
`Overflow`/`Pool`/`Used`/`In`/`Added` at the very end (43 total; `Used`/
`In` are Phase 5B, `Added` is Week 3 feedback's A6 -- a HIDDEN column
accumulating every name typed into the add-a-player control cell this
week, see below). `Source`, which used to sit right before `Edge ↗`
in that same spot, was removed entirely in Week 3 feedback (A4,
2026-09-22) -- Sam had no use for it. `Lineups` inserts `% of Cap` (renamed from `% of
Own` in Part 7.9, `% of Rstr` before that in Part 2) immediately after
the full spine, then `Issues`, then Part 7.5's six lineup-metrics
columns (`Stack` through `Min Unique`, see above), then `Edge ↗` (A3),
before the collapsed groups begin (46 total, was 40 before Part 7.5).
Lineups also groups
`O/U`/`Spread`/`Team
Implied` (Phase 5D) behind their own +/- control, same idea as the
Game/Ceiling detail/Movement/Weather zones above -- see below. Column
letters aren't given here on purpose -- they move whenever a new column
is inserted (most recently A3's "Edge ↗"); `sheet_columns.py`'s own
lists are the only thing anything in this codebase actually depends on.
`ValAdj`/`CeilVal`/`Avail`/`Flags`/`GameEnv`/the whole CEILING DETAIL/MOVEMENT/
WEATHER zones (all but `Venue`, which is native) plus hidden `Id`/`Flag`
are linked from `EdgeRaw` by `dfs setup link-edge`
(`sheet_links.LINKED_EDGE_COLUMNS`); everything else in the table above
is native to the tab itself. Unlike Phase 3's grammar, `Leverage` is now
itself inside a collapsed group (Ceiling detail) rather than always
visible -- the whole spine-and-groups redesign's point is that nothing
off the spine stays permanently uncollapsed, `EdgeRaw`
included (see CONTRIBUTING.md's Part 2 writeup for the one deliberate
exception this overrode).

This is recorded explicitly here because it's the one thing that's
already drifted once before Phase 3 existed: two sheets built from the
same source (the live sheet and an older copy of the template) ended up
with `Venue`/`Ceil` in different positions after independent hand-edits,
while each stayed internally consistent -- nothing caught it until a
cross-sheet audit compared them directly. Phase 3 exists specifically so
this reorder happens once, to a designed order, rather than needing to
happen again piecemeal (the designed order originally included headroom
for a `SoS 1..4` placeholder block, since removed -- see Phase 5, Section
I in CONTRIBUTING.md's changelog). `dfs
doctor` checks the columns each sheet's formulas actually depend on
(`EdgeRaw`'s header, every `LINKED_EDGE_COLUMNS` name present exactly
once on each of these three tabs, header repeats) but does not check the
NATIVE columns' relative order, since nothing breaks if it moves as long
as both sheets move together. If you ever reorder these again, update
this table and `sheet_columns.py` together.

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
| `OppPosRank` | `SoSComb`, this player's opponent's strength-of-schedule rank at this position -- `VLOOKUP`'d by `Opp.` (see `sheet_pool_raw_sos.py`; fixed 2026-09-16 -- the formula had been keyed on `Team` instead for as long as it existed, measuring a player's own defense rather than their opponent's, invisible until real SoS data existed to expose it). |
| `Pts`, `Ceil` | `TFFBOptoRaw`'s `ProjPts`/`Ceiling`, same DST special-casing as `Venue`. |
| `Val` | `Pts / (DK Sal / 1000)`, computed in-sheet (independent of `EdgeRaw`'s own `Val`, though they should agree). |
| `Own%` | `TFFBOptoRaw`'s `ProjOwn`, already a 0-1 fraction here (unlike `EdgeRaw`'s own `Own%`, which needed a Part 2 rescale to match -- see EdgeRaw's column docs above). |
| `ValAdj`, `CeilVal`, `Avail`, `Flags`, `GameEnv`, `GameID`, `TmRank`, `Stadium`, `Roof`, `Wind`, `ImpliedMove`, `TotMove`, `SpdMove`, `GameStart`, `Id`, `Flag`, `CeilPct`, `Leverage`, `OwnStatus` | **Linked from `EdgeRaw`** by `dfs setup link-edge` (VLOOKUP by Name) -- see EdgeRaw's own column docs above for what each means (`Venue`, listed separately above, is native, not linked, despite sitting in the same Weather group). Interleaved into their designed zones (see the canonical column order above), not appended -- Weather (`Venue`/`Stadium`/`Roof`/`Wind`), Movement (`ImpliedMove`/`TotMove`/`SpdMove`/`GameStart`), and Ceiling detail (`CeilPct`/`Leverage`/`OwnStatus`) are each grouped so they can be collapsed from the sheet UI; `Id`/`Flag` are hidden outright, not grouped. `ValAdj`/`CeilVal`/`Avail`/`Flags`/`GameEnv`/`GameID`/`TmRank` stay on the visible spine/Game zone. `ValAdj`/`GameID`/`TmRank` are linked (not native, unlike `Val`) since each is a whole-slate computation, not a per-row formula. |

### Player Pool / Lineups

Where you actually build lineups. `Lineups` is still typed: type a
player's name into the `Name` column of a roster slot, and every other
column VLOOKUPs off that name against `PlayerPoolRaw`. `Lineups` repeats
a 9-row roster block (QB, RB, RB, WR, WR, WR, TE, FLEX, DST) once per
lineup you're building, each with its own **totals row** directly below
it (Salary, Pts, Ceil and Own% summed, `Total` labeled at `Opp.`'s
column -- Fix 2.4; `dfs setup polish` writes this via `sheet_style.
polish_lineups_totals_rows`), and one more row directly below THAT
holding `Remaining` (Week 3 feedback, A8, 2026-09-22: Sam wanted it
"under the total, not off to the side" -- previously stranded at
`Venue`'s column with its own label at `Val`'s, both now cleared as
dead) -- value and label both sit in the SAME two columns as `Total`'s
own (`DK Sal`'s column for the number, `Opp.`'s for the label), one row
down. That same new row also holds the average remaining salary per
UNFILLED slot, in `Pts`'s column (otherwise dead there): `=IF(COUNTBLANK
(<9 slots>)=0,"",<remaining>/COUNTBLANK(<9 slots>))`, blank once the
lineup is full rather than a divide-by-zero error. Both are plain,
directly-referenced formulas now, not `INDIRECT`-built ones -- an
earlier version of the average-remaining row was documented as reading
its own totals row via a hardcoded `INDIRECT("E"&(ROW()-1))`, one of the
`moveDimension`-can't-retarget hazards `CLAUDE.md`'s central-hazard
section warns about, but verified live (2026-09-22) that no such
formula actually existed on either sheet -- `sheet_style.
polish_lineups_remaining_per_slot_helper`, the function meant to keep it
correct across a reorder, only ever repaired an EXISTING `INDIRECT`
cell and silently no-op'd every time it ran, since there was never one
to find. Replaced entirely by the direct-reference version above (no
more `INDIRECT`, no more hazard class to guard against here).
`weekly_reset.LINEUPS_TOTALS_ROWS` names the totals row for each block;
the Remaining/average-remaining row is always the one directly below it.

`Player Pool` (A3) has a one-row control strip pinned at the top: `A1` is
a plain label ("Add a player"), `B1` is a live type-ahead search box
(`ONE_OF_RANGE` validation, non-strict) against `EdgeRaw`'s own `Name`
column -- typing a name there adds that player to the pool, the same as
ticking them in `EdgeRaw`. This replaced a separate `Pool Picks` tab
(removed -- see `CONTRIBUTING.md`'s A3 changelog entry); the real header
now sits at row 2, with every position block one row lower than before
Phase 3's own layout.

`Player Pool`'s `Name` column is **not typed** (except that one control
cell above it) -- it's a `SORT(UNIQUE({...}))` formula per position
block, pulling in the UNION of THREE sources: whichever players have a
non-blank `Pool` value on `EdgeRaw` (see EdgeRaw's column docs above);
whatever name is currently typed into the control cell, for that
position; and every name accumulated in the hidden `Added` column (Week
3 feedback, A6, 2026-09-22 -- see immediately below) that matches. Each
source's Salary/Position/Pool-tag is looked up against `EdgeRaw` by
name, never carried on the source itself. `UNIQUE` dedupes a player who
ends up in more than one source into one row, not several. `Edge ↗`
(right after `Opp.` -- `Venue` no longer sits in IDENTITY, see the
canonical column order above) is a `HYPERLINK` jumping straight to that
player's row on `EdgeRaw`, the fastest way to find and remove one
(there's no in-place delete -- see A3's changelog entry for why not). A
`Source` column used to sit here too, stating which of the (then two)
sources a row came from -- removed entirely in Week 3 feedback (A4,
2026-09-22); Sam had no use for it.

**`Added` (hidden, A6, 2026-09-22)** fixes "adding a second player in
the control cell deletes the first": the control cell holds only one
typed name at a time, so `dfs sync` drains it into the next free row of
this 50-row hidden column and blanks the cell, immediately after
writing it -- `sheet_pool_control.drain_control_cell_into_added_names`.
A typed name still shows up in the pool the INSTANT it's typed (the
control cell is still one of the three union sources above), and keeps
showing up after the next sync moves it here. Cleared every week by
`dfs week new`, same "typed state must not survive into a new week"
reasoning as the control cell itself.

`Pool` surfaces that player's actual `EdgeRaw` `Pool` value (`Cash`/`GPP`/
`Both`) via `INDEX`/`MATCH` by name (`Pool` sits left of `Name` on
`EdgeRaw`, so a plain `VLOOKUP` can't reach it). `Used`/`In` (Phase 5B,
appended past `Pool`) answer the one thing a second browser window can't
on its own: `Used` is a plain `COUNTIF` of how many of THIS WEEK's
`Lineups` roster this player (against the whole of `Lineups!A:A`, so a
repeated sub-header row's literal "Name" text can never be mistaken for a
pick); `In` lists WHICH ones (`L1, L3, L7`), one `TEXTJOIN`'d term per
`LINEUPS_NAME_BLOCKS` entry, generated in Python from that constant
(`sheet_pool_usage.py`) rather than hand-typed. `Used` is colour-scaled
like every other count on the tab (zero unstyled -- an unrostered pool
player is normal, not a low value on a scale). The block fills in sorted by **Pool tag group, then Salary descending**
within each group (Part 7.10, Sam: "The pool should order players by
position by salary high to low, but grouped by Both, Cash, GPP") --
`Both` first (usable in either contest type, so core), then `Cash`, then
`GPP`, per `sources.edge.POOL_TYPE_SORT_ORDER` -- not alphabetically, and
not the dropdown's own `POOL_TYPE_OPTIONS` order; the control cell's half
looks its own Salary, Position AND Pool tag up against `EdgeRaw` by name,
since it carries none of its own, and a typed name with no matching
EdgeRaw tag sorts after every real tag rather than into an arbitrary
position. See `docs/CALCULATIONS.md` for the formula mechanics. Capped at
that position's slot count (QB 10, RB 20, WR 25, TE 10, DST 10), with an
`Overflow` column (second to last, right before `Pool`)
warning per position if more players are ticked/typed than the block has
room for (counting the same deduped union, so a player counted in both
sources can't trigger a false warning) -- nobody is ever silently
dropped. `Player Pool`'s own `Pool` column gets a light per-tag tint
(`sheet_style.POOL_TAG_TINTS`) so the three sort groups read as bands at
a glance, not just by scrolling and reading the text. Every other column
still VLOOKUPs off `Name` the same as before. `Player Pool` is protected
everywhere except that one control
cell (warning-only, `dfs setup protect`) -- nothing else is meant to be
typed into directly. See `sheet_pool_control.py`/`sheet_pool_formulas.py`/
`sources/edge.py` for the mechanism and `CONTRIBUTING.md`'s changelog for
the block-resize and A3 history.

Columns mirror `PlayerPoolRaw`'s, pulled the same way, plus the same
linked `EdgeRaw` block at the far right (`dfs setup link-edge`).
`Lineups` additionally has `% of Cap` (renamed from `% of Own` in Phase 6,
Part 7.9, `% of Rstr` before that in Part 2) -- this pick's `DK Sal` as a
share of the **salary cap** (`config.toml`'s `[lineups] salary_cap`), not
of the lineup's own running total. Part 7.9 corrected both the name and
the denominator: Sam confirmed the intended meaning is cap allocation,
and the old running-total denominator lurched as slots filled (three
players in, each read ~33%) -- see `docs/CALCULATIONS.md`. Also a
per-lineup salary-remaining row. Each
lineup block's `Name` column (the only typed column on the tab) has a
live dropdown validated against `PlayerPoolRaw`'s real Name column
(non-strict -- a warning, not a hard block) so a typo doesn't silently
propagate as `#N/A` across the whole row; everything else on the tab is
protected (warning-only).

`Lineups`' `Issues` column (right after `Flags`, `dfs setup polish`) is a
per-lineup guardrail: on each roster slot, `DUPLICATE` if that name
appears twice in the same lineup, else that pick's linked `Avail` flag
(`OUT`/`IR`/`Q`) if it has one; on the totals row, `OVER` the salary cap,
`INCOMPLETE` (fewer than 9 picks), Part 7.4's two stack-rule violations
(`DST/QB` -- your rostered DST is playing against your own rostered QB's
team; `RB/GAME` -- more than one rostered RB shares a game), any
combination of those additively (e.g. `"OVER $500 RB/GAME"` -- a real
violation is never silently masked by an unrelated one), or `OK`. The
two stack checks need `Pos.`/`Team`/`Opp.`/`GameID` all linked -- they
degrade gracefully (skipped, exact prior behaviour) otherwise, never
blocking the cap/completeness check from running. `sheet_style.
polish_guardrails` finds every one of these columns by header name,
never a hardcoded letter -- see CONTRIBUTING.md's Phase 3 changelog
entry for the incident that happened when it didn't. See
`docs/CALCULATIONS.md` for the formula mechanics.

Right after `Issues` sit Part 7.5's six lineup-metrics columns
(`sheet_lineup_metrics.py`), one value per lineup on its own totals row:
`Stack` (a signature like `"QB+2 (KC) + 1 bring-back"`), `Games`
(distinct games represented), `Bring-back` (Yes/No), `Own% Used` (summed
projected ownership, blank pre-publish), `Sub-10%` (count of picks under
10% owned, same pre-publish guard), and `Min Unique` (the smallest count
of this lineup's own picks absent from some other lineup -- how
different is your MOST similar other lineup). See `docs/CALCULATIONS.md`
for the exact formulas. `Exposure`'s own header row (K1:P1) carries the
portfolio-level counterpart: `Distinct QBs`, `Shared QB?` (Yes/No), and
`Distinct games`, across the whole lineup build rather than one lineup.

`Lineups`' real header sits at row 1. It didn't always -- a "pool deck"
(frozen rows above the header holding a sortable/filterable window into
Player Pool) occupied that space from 2026-09-06 to 2026-09-16, resized
twice, then removed entirely after a week of real use: Sam found it "a
pain" and, on the one piece worth keeping (a "where is this player"
jump), "doesn't get me much. Cut it." Player Pool's own colour scales/
chips/`Used`/`In` columns (below) now cover the browsing job the deck
existed for. `dfs setup remove-pool-deck` is the one-time repair for a
sheet that still has the retired deck; see CONTRIBUTING.md's changelog
for the full history and `sheet_pool_deck.py`'s module docstring.

`dfs lineups clear` wipes `Lineups`' typed-in `Name` columns (and
`Scratch`/`DK Upload`, below) at the start of a new week. `Player Pool`'s
`Name` column is skipped -- it's a formula now, not a typed value, and
the actual per-week state (which players are ticked) lives in `EdgeRaw`,
which `dfs sync` already rewrites every week regardless.

On gameday, `dfs lineups late-swap` reads every built lineup's `Name`
column here directly (not `PlayerPoolRaw`, not `DK Upload`) and checks
each rostered player's real kickoff time (`EdgeRaw`'s `GameStart`) against
now: locked players are left alone, and for anyone not locked yet it shows
current `ProjPts`/`Leverage`/`Flags` plus the best still-open alternatives
at that slot, so a late swap is a read of one report instead of manually
cross-referencing kickoff times against your roster.

## Synced / output tabs

### SoSQB / SoSRB / SoSWr / SoSTE / SoSDef

Strength-of-schedule rankings from The Fantasy Footballers' FootClan
Strength of Schedule page -- automated by `dfs sync` (sources `sos_qb`/
`sos_rb`/`sos_wr`/`sos_te`/`sos_dst`, `sources/tffb_sos.py`) as of
2026-09-16; previously hand-pasted. Reuses the same `dfs auth tffb`
session `projections` already depends on -- no separate login. Header is
`Team | Team.1 | Rank | FPA | Opp` (5 columns; the source has no `PAE`
field, and the old `Week N`/`Week N Opp` labels are gone since the sync
always reflects whatever week is current -- no header text to go stale).
`Rank` is colour-scaled REVERSED (a low rank is the good matchup here --
`Player Pool`/`Lineups`/`PlayerPoolRaw`'s own `OppPosRank` gets the same
REVERSED treatment, see `sheet_style.FIELD_COLOR_SCALES`); `SoSComb`
combines all five into one lookup table keyed by team and position
(`VLOOKUP`ing each tab's column C, unaffected by the header/column
changes above since `Rank` stayed column C), which `PlayerPoolRaw`'s
`OppPosRank` reads. Verified live: 100% `OppPosRank` fill rate across
every real player on the pool once this was wired up.

**"Current week" here is deliberately not `nfl_calendar.current_week()`**
-- this reads TFFB's own page default (the lower bound of its own
"Weeks N-18" filter label) instead, since the two can legitimately
disagree by one week in the Tue/Wed window between one week's Monday
night game and the next week's Thursday kickoff (`nfl_calendar`'s
definition is correct for ITS OWN uses -- contest-history week-sorting,
line-movement baselines -- just not this one). See `tffb_sos.py`'s own
module docstring for the live-verified specifics.

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
`GPPin`/`DKLineupsRaw`/`DKLineupsFinal` derive views from it, entirely by
formula -- `dfs` itself doesn't read or write any of these; pulling
contest history directly (no manual export) would need probing
DraftKings' undocumented authenticated endpoints live, with you present;
investigated, not built. `dfs week new` does clear `EntriesRaw`'s data
rows (Fix 5G) if the tab exists, the same "typed input must not survive
into a new week looking current" reasoning as `Lineups`/`Bankroll` --
`GPPin`/`DKLineupsRaw`/`DKLineupsFinal`'s formulas just resolve blank/
`#N/A` against an empty `EntriesRaw`, same as before you ever pasted
anything.

### Exposure

Built/rebuilt by `dfs setup build-views` (`sheet_views.build_exposure`) --
a pivot-style count of how many times each `EdgeRaw`-sourced player name
appears across `Lineups`, how concentrated your week's lineups are on a
given player. `Exposure = # Lineups / H1`, not the sheet's 20-lineup
capacity -- dividing by capacity was a live bug (Phase 5A): a player
rostered in every one of a 6-lineup build read as 30% instead of 100%. A
blank/zero/out-of-range `H1` falls back to capacity in the formula
itself, so it can't produce `#DIV/0!` or silently read as 100%.

Two typed inputs on this tab survive a rebuild -- both read back before
`build-views` rewrites the tab, and re-placed into the fresh copy:
`Target` (column F, the one typed input for setting exposure targets),
and `H1` (how many lineups you're actually building this week, defaulting
to 6, with a note carrying the label since row 1 is otherwise all column
headers with nothing free next to it, and a warn-only 1..20 range check).
`H1` originally lived on `Lineups` itself (the one free cell in the pool
deck's row-1 control strip) -- moved here when the deck was removed
entirely (2026-09-16); it was never really about the deck, just parked
there for lack of anywhere better, and this is the one tab that actually
reads it. `H1` survives `dfs week new` too (nothing here clears it) --
it's still the right number until you change it.

### Movement

Built by `dfs setup build-views` (`sheet_views.build_movement`), styled by
`dfs setup polish` (`sheet_style.style_movement`, header-name-driven --
see Section F's own changelog entry for why). The top 40 players by
absolute `ImpliedMove` since the start of the current NFL week, with
`TotMove`/`SpdMove` riding along as extra columns once a row already
qualifies (sorting/filtering is on `ImpliedMove` alone -- the same
signal `Flags`' `LINE↑`/`LINE↓` keys off, so "biggest movers" keeps one
meaning). Prose headers (`Implied move`/`Total move`/`Spread move`) since
this is a view, not a contract -- a reader here shouldn't need to know
EdgeRaw's own header spells it `ImpliedMove`. Shows an explicit
empty-state message ("No line movement recorded yet") rather than a page
of real `0.0`s until at least one `nfl_odds` sync has happened this week.

### Bankroll

Cash/GPP ledgers plus starting/ending bankroll summary figures.
`dfs week new` clears each bucket's typed entry columns (A-H) and its
dedupe-key column when moving to a new week (Fix 5H) -- the two formula
columns per row (`% Paid`/`Place %`) and the Starting/Ending balance
cells are never touched. `dfs bankroll sync --csv <file>` (or `dfs week close --csv <file>`, a
thin wrapper over it) classifies each contest entry as Cash or GPP by
payout shape (roughly half the field paid, or a straight head-to-head,
counts as Cash; everything else is GPP) and appends new rows here -- it
only ever writes into its configured row range and dedupe-key column
(`[bankroll.cash]`/`[bankroll.gpp]`'s `entry_key_column` in
`config.toml`), never touching the summary figures or any other formula,
and re-running is safe since entries are deduped by that key. If your
sheet has no ledger tables shaped like this, leave `[bankroll.cash]`/
`[bankroll.gpp]` out of `config.toml` and the command says what's
missing rather than guessing where to write.

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

**Auto-fill (Fix 2.16/2.17):** `dfs bankroll sync --csv <file>` / `dfs
week close --csv <file>` also derive `Week`, `Cash Pts`, `H2H Entered`
and `H2H Win` from that same DK contest-history export and write them
straight into Results -- `results_autofill.compute_week_results` sorts
every entry into the NFL week its own contest date falls in (not by
assuming the file covers one week), so a season-long export backfills
every past week it has real data for in one pass. `Cash Line` (column C)
and the three team-colour columns (H/I/J) stay yours to maintain by hand;
`write_results_updates` never touches them, and never blanks `Cash Pts`
for a week that hasn't finished scoring yet.
