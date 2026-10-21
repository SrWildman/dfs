# Troubleshooting

Symptom, then cause, then fix. For the weekly grind, see
[WORKFLOW.md](WORKFLOW.md); for what a column/tab means, see
[SHEET_REFERENCE.md](SHEET_REFERENCE.md).

---

**I don't know what to run next.**
Cause: the weekly workflow has ~15 commands across setup/sync/pool/
lineups/bankroll, and it's easy to lose track of where you are mid-week.
Fix: just run `dfs` with no arguments -- it shows a compact status (sheet,
sync freshness, pool/lineup counts) and a menu of the standard week with the
step that fits right now marked `>`. It asks for anything a command needs
(a link, a CSV, a name), and `m` lists every other command.

---

**A command you used to run says "No such command".**
Cause: this project's CLI got reorganized (one-time sheet construction
moved under `dfs setup ...`, `dfs sheets doctor` promoted to top-level
`dfs doctor`) -- see README's Commands table or `dfs --help` for the
current shape.
Fix: `dfs sheets ...` (the old spelling) still works for one season as a
deprecated alias and prints the new name the first time you use it; or
just use the new name directly.

---

**`dfs doctor` says a column has "no formula" on some rows, or "a formula pointing at another row".**
Cause: a hand-built per-row formula range (Results' `Cash Results`/`H2H %`, Season's
totals, DkSalClean, PlayerPoolRaw) lost formulas -- usually a template copy or a
row delete/sort -- so the cells read blank or read the wrong row. Player Pool,
Lineups and PlayerPoolRaw then look empty or wrong with no error anywhere.
Fix: run `dfs setup repair-formula-ranges --sheet-id <template>` first, then the
same command on the live sheet, then `dfs doctor` on both. It rewrites only the
cells doctor flagged, from the nearest healthy row.

---

**Results, Bankroll or Season shows `#DIV/0!` on an empty week, or `dfs doctor` reports `empty-guards`.**
Cause: a division or average lost its empty-state guard (a hand edit, or a template copy from
before the guards existed), so it errors until something is entered.
Fix: `dfs setup guard-empty-states --sheet-id <template>`, then the same on the live sheet, then
`dfs doctor` on both. Formulas already guarded are left alone. Season's hidden `T:W` `#N/A` cells
are deliberate (they make the chart stop at the last played week).

---

**Player Pool is empty (or a position block is empty).**
Cause: nothing's been added to the pool for that position yet.
Fix: three ways in -- set a player's Pool dropdown on EdgeRaw, type a
name into Player Pool's own row 1 search box, or `dfs pool add "name"`.
Check `dfs pool list` to see what's currently ticked.

---

**#N/A across an entire lineup row.**
Cause: a typo in the name typed into Lineups column A -- everything else
in that row VLOOKUPs off the name, so one bad cell breaks fourteen
columns with no obvious cause.
Fix: use the dropdown in that cell (it validates against PlayerPoolRaw's
real Name column) instead of typing free text, or retype the name to
match exactly.

---

**Added a player but they're not showing in Player Pool.**
Cause: that position's block is at its cap (QB 10 / RB 20 / WR 25 / TE
10 / DST 10) -- the extra is hidden, not dropped.
Fix: check that block's Overflow column (near the far right of the tab
-- its exact letter shifts as columns are added, so look it up by header
name rather than assuming a position) for a warning naming the cap and
how many are actually ticked/typed. Remove someone else at that position
first, or accept the cap.

---

**The "Add a player" box does nothing.**
Cause (found live on Weeks 5 and 6): the old layout had a white label cell reading "Add a player" in the Name column with the real box one cell to its right, so people typed over the label, and the script (which found the box FROM the label) then had nowhere to look. Since the Week 6 fixes the box IS the Name-column cell of row 1 (it reads "Add a player: type a name" while empty) and the script finds it from the header. A sheet still on script version 4 has no working box in the new layout: paste `Code.gs` (version 5).
Fix: `dfs doctor` reports a blank box as `pool-control-row` and an old script as a version warning; `dfs setup add-pool-control` converts the old layout (a name left in the box is added to the pool first) and `dfs sync` adds a name left in the box even without the script.

---

**"Pool change failed: The data you entered in cell A741 violates the data validation rules", and the Pool cell is now empty.**
Cause: the Pool cell is a strict Cash / GPP / Both dropdown holding a formula, and the Apps Script (version 2) could not write the formula back when its
result was blank (a player removed from the pool): the pool change itself had already landed on EdgeRaw, the cell was left empty. Fix: paste the current
`apps_script/Code.gs` (version 3, `docs/APPS_SCRIPT.md`), run `dfs setup polish` (the Pool dropdowns become warnings), then `dfs sync --only edge` to write the
formula back into the empty cell. `dfs doctor` flags a player row whose Pool cell is empty.

---

**A tick in EdgeRaw's Pool column vanished after `dfs sync`.**
Cause: usually nothing's actually wrong -- ticks are preserved across a
sync by matching on the DraftKings player Id, not row position, so a
player whose Id changed (rare) or who dropped off this week's DK salary
file entirely won't have anything to restore onto.
Fix: check whether that player is still in this week's DKSalRaw/EdgeRaw
at all. If they are and the tick still didn't survive, that's a real bug
-- see `sources/edge.py`'s `pre_upload`/`post_upload` preserve-by-Id
logic.

---

**`dfs setup link-edge` reports a column appended twice, or Player Pool/
Lineups' EdgeRaw-linked columns look duplicated.**
Cause: `link_edge_columns`'s own idempotency guard (skip if
`LINKED_EDGE_COLUMNS` already appears as a contiguous run in the header)
failed to recognize an existing link, usually because something inserted
a column into the middle of that block.
Fix: run `dfs doctor` -- it checks for exactly this. If it fails,
do not re-run `link-edge` blindly; see `sheet_links.py`'s own docstring
for the "clear the old linked columns by hand first" recovery path.

---

**A number shows as `33.2900000001` instead of a clean decimal.**
Cause: the cell has no number format applied (or the wrong one), so
Sheets is showing a raw float.
Fix: run `dfs setup polish` -- every column whose header is a
`FIELD_FORMATS` key gets its format re-applied, safe to re-run any time.

---

**A tab looks unstyled (no dark header, no colours, default-width
columns).**
Cause: that tab was never styled, or a styling command didn't reach it
(e.g. a SoS tab pasted after the last `dfs setup polish` run).
Fix: run `dfs setup audit-style` -- it reports exactly which tabs and
which checks (header fill, freeze, widths, number formats, chips) are
missing, per tab, without trusting any command's own "OK" output. Then
run `dfs setup polish` and check again.

---

**You're not sure which sheet a command is about to write to.**
Cause: `config.toml`'s `sheet_id` is an opaque string, and a new sheet
gets copied every week -- easy to lose track of which week you're
pointed at.
Fix: run `dfs status` (or note the title/URL any writing command prints)
before running `dfs sync` or anything else that writes.

---

**I can't sort or search EdgeRaw, and I can't find a search bar.**
Cause: both exist, but the wrong one is easy to miss and the right one
is easy to never look for. `dfs setup add-filters` puts a saved preset
under Data > Filter views, which is genuinely hidden if you've never
opened that menu.
Fix: click the dropdown arrow in any EdgeRaw header cell -- that's a
basic filter (Data > Create a filter), on by default, and sorts/searches
that column directly. The saved presets under Data > Filter views
("Pool picking", "Leverage plays", ...) are the secondary, quick-preset
mechanism, not the primary one.

---

**Player Pool's Venue column (or another column that's just a category,
like Pool) looks colour-scaled instead of chipped.**
Cause: a colour scale hand-applied directly in the sheet at some point,
before `sheet_style.FIELD_COLOR_SCALES` existed as the one canonical
policy every tab follows.
Fix: run `dfs setup polish` -- it clears a builder tab's conditional
formats before reapplying them, so a stray manual rule doesn't survive a
re-run.

---

**A cell you're sure you didn't touch shows a "you're editing a
protected range" warning.**
Cause: working as intended -- most formula-driven tabs are protected
(warning-only, see `dfs setup protect`) so a stray keystroke doesn't
silently overwrite a working formula.
Fix: the warning is dismissible, not a hard lock -- if you really meant
to edit there, click through. If you didn't mean to, that's the warning
doing its job; undo (Ctrl+Z) instead of confirming.

---

**A guardrail formula involving `GameID` (or any other linked column
that blanks itself out via a formula) fires on an incomplete lineup when
it shouldn't, or two "blank" cells seem to match each other in a
`COUNTIFS`/`FILTER` self-reference.**
Cause: a linked column like `GameID` is never a genuinely empty cell,
even on an unfilled roster slot -- it's a FORMULA
(`=IF($A="","",VLOOKUP(...))`) that RESOLVES to `""`. `COUNTIFS`/`FILTER`
treat a formula-produced `""` differently from a truly blank (never-
typed) cell: two formula-blank cells DO match each other in a
self-referential `COUNTIFS` criteria, but two genuinely-blank cells
don't. Found live (2026-09-19) in the RB/GAME guardrail (`sheet_style.
_stack_check_formula`'s `rb_per_game`), which fired on almost every
incomplete lineup with 2+ unfilled RB slots until fixed.
Fix: add a direct inequality term against the linked column itself
(`GameID_range<>""`) as an extra `SUMPRODUCT`/`FILTER` criterion --
`COUNTIFS`' own `"<>"` criteria pattern does NOT work here, since it
treats a formula-holding cell as non-blank regardless of what it
resolves to. A direct cell/range comparison (`<>""`) correctly evaluates
`FALSE` for a formula-blank cell; a genuinely-typed (non-formula) column
like `Lineups!Name` doesn't have this problem at all, since an untyped
cell there really is blank.

---

**A `COUNTA(UNIQUE(FILTER(...)))`-style formula reads 1 instead of 0
when its range is genuinely empty of real values.**
Cause: `FILTER` errors (`#N/A`) when NOTHING in its range satisfies its
own criteria -- an empty result isn't returned as "nothing," it's a
hard error. `COUNTA` then silently absorbs that error into a valid
count of 1 (an error value still "counts" as present to `COUNTA`)
*before* `IFERROR` ever gets a chance to catch it, so a naive
`IFERROR(COUNTA(UNIQUE(FILTER(...))),0)` never actually degrades to 0.
Found live (2026-09-19) in both the per-lineup `Games` column
(`sheet_lineup_metrics.distinct_games_formula`) and Exposure's portfolio
`Distinct games` headline (`sheet_views.build_exposure`) -- both showed
a phantom "1" for every still-empty lineup/build.
Fix: use `ROWS` instead of `COUNTA` as the outermost function --
`IFERROR(ROWS(UNIQUE(FILTER(...))),0)`. `ROWS` does not absorb the
error the way `COUNTA` does; it propagates it, so `IFERROR` finally has
something real to catch and correctly degrades to 0.

---

**The usage columns (`Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`) are blank.**
Cause: they come from nflverse's weekly stats file, and they are blank in Week 1 (no games played yet), when the
file could not be fetched (a warning is logged; it never falls back to last season), or for a player nflverse has
no stat line for. A metric is also blank for positions it does not apply to (`WOPR` is WR/TE only, `HVT/G` RB only).
Fix: `dfs sync --only usage,edge` once nflverse has the week's games. Players with no stat line yet (a backup QB who
has not played) are listed in `data/current/unmatched_usage.csv`.

---

**Slate Grid's TEAMS section is blank, or `dfs sync` / `build-views` complains about a `pbp` tab.**
Cause: TEAMS reads a hidden `TeamMetricsRaw` tab that the `pbp` source writes, and `config.toml` needs
`pbp = "TeamMetricsRaw"` under `[google_sheets.tab_mappings]` (it is in `config.example.toml`; an older local
`config.toml` will not have it).
Fix: add that line, then `dfs sync --only pbp` and `dfs setup build-views`.

---

**`dfs week close` (or `dfs results update`) says "stats not published yet".**
Cause: nflverse posts a week's player and team stats a day or two after the games.
Fix: nothing is wrong. Run `dfs results update` later; the Model Check tab fills in. The close itself already
succeeded.

---

**Model Check says "Not enough data yet", or most rows are muted italic.**
Cause: no completed week has been scored yet, or the sample is small (a row with n under 30 is "thin" on
purpose). Early in the season almost everything is thin.
Fix: nothing to fix; it fills in as weeks are scored. `dfs results update --all` rescores every completed week
(useful after a scoring or flag change).

---

**`dfs lineups late-swap` says my Player Pool is empty / suggests nothing.**
Cause: swap candidates come from your Player Pool (the names on that tab), and nothing has been ticked there yet --
or everyone in it has already kicked off, or is OUT/IR.
Fix: tick players into your pool on EdgeRaw, or run `dfs lineups late-swap --all-players` to search the whole
rosterable pool. "Nothing beats this lineup" is a real answer: every swap that fits the cap and the rules scores
less than what you have.

---

**`dfs lineups late-swap` calls a player locked before his game has started.**
Cause: fixed 2026-10-02. TFFB's `GameStart` is Eastern wall-clock time labelled "Z"; it used to be read as UTC, which
made every kickoff look four hours (five after the November clock change) early.
Fix: update to the current code (`kickoff.py` converts it). If you still see it, check `dfs status`/EdgeRaw's
`GameStart` column is populated and your computer's clock is right.


---

**The Edge Finder columns (`CalPts`, `Hit3x%`, `Boom%`, `Edge`, ...) are blank, or `Edge Finder` says "Not synced yet".**
Cause: the Edge Finder step is fail-soft, so a problem blanks only what depends on it and never fails the sync. Look
for `Edge Finder columns skipped` in the sync output, and `notes` in `data/current/edge_finder/status.json`. Usual
causes: a `ffopportunity` / `nflverse_injuries` / `nflverse_depth` download that failed (each leaves an empty frame, and
only its own columns go blank), or no scored weeks yet (Week 1: `CalPts` is `AggPts`). UM is no longer an input to
`CalPts` (2026-10-08), so a missing model cache cannot blank these columns.
Fix: `dfs sync` (a full sync refreshes the cached season inputs; `--live` reuses them).
A QB projected under 10 points is blank on purpose ("not rated below 10 pts"), and a DST projected under 4 is shown
muted because the engine understates its upside.

---

**Model Check has no Signals or Reliability numbers, or says `UM unavailable`.**
Cause: `dfs results update` builds each scored week's signals (and attaches UM) from the free nflverse files; if a
download or the model cache is missing it says so and leaves that part blank.
Fix: `dfs model fetch`, then `dfs results update --all`. Weeks are rebuilt without lookahead, so rerunning is safe.

---

**"Priced in?" is blank on the Edge Finder tab, or the injury report line says "Practice reports only so far".**
Not a fault. "Priced in?" compares TFFB's projection before and after the DraftKings status change, so it needs a
projection snapshot from before the out designation; a back already OUT in the first snapshot we hold cannot be priced
(blank, never a guess). The injury report line on the Edge Finder tab shows the nflverse report's row count, how many
carry a final status and when it was fetched: before Friday's final report the Week's rows are practice reports with
no status, so outs come from DraftKings' `Avail`.

---

**The Lineups simulator numbers (`Typical`, `Big night`, `Cash %`, `Top 1%`) are blank.**
A lineup with an empty slot, or a name EdgeRaw does not have (typo, bye week), is left blank on purpose; fill it in and
`dfs sync` again. If every lineup is blank, check the sync output for `Lineup simulator not written` (a Sheets error) or
`column(s) [...] not found` (run `dfs setup reorder-columns`). `Cash %` and the last number move with the slate
(the field's average score plus a margin, from projected ownership); until ownership publishes, or with `--fixed-lines`,
the cash line is the median of every typed `Cash Line` in Results this season (with none typed the sync says it used the
145 placeholder) and the last number is the fixed `[sim] gpp_target`. The sync line says which (`Ownership has not
published ... using FIXED lines`).

---

**`dfs week close` says a standings download is EMPTY, or the Contests tab has no cash lines for last week.**
DraftKings only serves a contest's standings for about two weeks and then sends a 0-byte file, and a download that failed looks the
same. An empty file is reported and left in Downloads; nothing can be rebuilt from it. Grab each contest's "export full standings" on
Monday or Tuesday, then run `dfs contests update` (or the next `dfs week close`). A contest with no standings file still counts in
the table's ROI, cash rate and finish; it just has no real cash line.

**The Contests tab's `Cash line vs same-format peers` is blank for a series.**
Not a fault. It needs a week where standings files exist for two or more series of that format (Double Ups, SE GPPs ...). `Weeks behind
it` says how many weeks the number rests on.

**`dfs week close` did not fill Results' `Cash Line`.**
It only fills a **blank** cell, and only from the standings of `[contests] cash_line_series` (default `NFL GIANT $25 Double Up [Single
Entry]`). A value you typed is kept; if the standings disagree by more than half a point both numbers are printed. A week with no
standings file for that series stays blank.

---

**`PropPts` is blank for a player, or most of the slate.**
Not a fault. `PropPts` needs every market his position uses (QB: pass yards and pass TDs; RB: rush yards and anytime TD; WR/TE:
receptions, receiving yards and anytime TD), otherwise it is blank rather than a partial score. Books post props through the week, so
before Saturday only some players are priced; the Saturday pull is the main one. The Texans/Jaguars-style games that are not on the
DraftKings slate are never pulled. `data/current/unmatched_odds_props.csv` lists prop players that did not match a DraftKings name.

**`dfs sync` says "props: the main pull is Saturday", or "Week N's one automatic pull was made ...".**
That is the credit guard: a plain full sync pulls props once per NFL week, on the first full sync of a Saturday or Sunday, and later full syncs
that week reuse it. `dfs sync --props` forces a full pull; `dfs sync --live --props` refreshes only the games where a DraftKings status changed
since the last pull and says "nothing spent" when none did. A pull is also skipped (with the numbers) when credits left / NFL weekends before the
reset (`[odds_api] reset_day`) would not cover it. `dfs doctor` warns under 100 credits. Plain `--live` never spends credits.

**`EspnPts` is blank for a starter.**
ESPN has no weekly projection for him (its API returns an empty entry for a player it expects out or on a bye), so there is nothing to score.
It is a shadow column: Model Check scores it where it exists.

---

**The Player card shows blanks for a player (or `-` everywhere).**
Check `B1` first: the name must match EdgeRaw's (pick from the dropdown). A blank usage block means no games on file for him (a rookie, a bye, a new team) or `player_log` has not synced (`dfs sync` prints
its status; a failed nflverse file blanks only its columns). A blank PROPS block is the weekend pull not having run yet (the line under it says which week it has). A signal saying "no record yet" means
`dfs results update` has not saved Model Check's records since the card was built. `dfs doctor` says `player-card-helpers` if EdgeRaw's columns moved under the card: `dfs setup player-card` rewrites it.

**`dfs sync` did not touch the Player card.**
It only refreshes the card's hidden raw tabs on a sheet that already has the Player tab (`dfs setup player-card` creates it, template first). A yellow "Player card data not refreshed (...)" line means the write
itself failed; the sync result above it still stands.

**The Cockpit says "no late-swap result", "lineup changed since the late-swap run" or "open slots differ from the run".**
Not a fault. Its best-change lines are from the last `dfs lineups late-swap` run (kept in `data/cockpit/late_swap.json`), and a lineup you edited since, a run from an earlier day, or a
game that locked since the run is shown as `LOOK`, never as current. Run `dfs lineups late-swap` (it refreshes the tab).

**`dfs sync --live` or `late-swap` did not touch the Cockpit.**
They only refresh a sheet that already has the tab. `dfs doctor` says `cockpit-tab` when it is missing; `dfs setup cockpit` creates it (template first). A yellow "Cockpit not refreshed (...)" line means
the refresh itself failed (a Sheets error, usually); the sync or late-swap result above it still stands. `dfs setup cockpit` rebuilds it.

**Exposure shows a player above 100%.**
`Exposure!H1` is the divisor and it must be the formula that counts built lineups, not a typed number (a typed 6 stayed put through a 15-lineup week and read 150%). `dfs doctor` says `lineup-count-cell`
when H1 holds a typed value; `dfs setup build-views` rewrites it (template first, then the live week; your Targets are kept).

