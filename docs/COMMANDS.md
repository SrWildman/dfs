# Command reference

Every `dfs` command and option, generated from the CLI itself (`python -m dfs.commands_doc`), so it
cannot drift from the code. `dfs <command> --help` shows the same text. For *when* to run what, see
[WORKFLOW.md](WORKFLOW.md); for what a sheet column means, see
[SHEET_REFERENCE.md](SHEET_REFERENCE.md).

**You do not have to remember any of this:** run `dfs` with no arguments for a menu of the standard
week (the step that fits right now is marked), and it asks for anything a command needs -- a sheet
link, a CSV file, a player name -- instead of erroring. Typing the full command still works, and
leaving a required argument off a typed command asks for it too (in a terminal; scripts and pipes
get the usual error).

## Your standard week

This is what most weeks look like. Everything below it is reference.

| # | When | Run | What it does |
|---|---|---|---|
| 1 | Tuesday/Wednesday: a new week starts | `dfs week new "<url-of-the-sheet-copy>"` | Points the CLI at a fresh copy of the template, carries your bankroll and Results forward, runs a full sync. Then check `dfs status`. |
| 2 | Through the week, as lines and injuries move | `dfs sync` | Re-pulls every source into the sheet. Safe to run as often as you like. |
| 3 | In the sheet (no command) | Board, Slate Grid, EdgeRaw, Player Pool, Lineups | Research, tick your pool, build lineups. `dfs pool add/remove/list` does the pool without opening the sheet. |
| 4 | Lineups are built | `dfs export -o lineups.csv` | Validates your lineups and writes DraftKings' bulk-upload CSV. |
| 5 | Sunday, before and between kickoffs | `dfs sync --live` | Fast refresh of odds, DK statuses, TFFB projections (with projected ownership) and weather only; prints what changed. Then `dfs lineups late-swap` shows who is still swappable and the best swaps for them. |
| 6 | After the games (nflverse posts stats a day or two late) | `dfs week close --csv history.csv` | Reconciles Cash/GPP into Bankroll and Results, then scores the week's projections into the Model Check tab. `dfs results update` does just the scoring, any time. |

Not sure what to run next? Run `dfs` on its own: it shows where you are in the week and the two or three commands that make sense right now.

## Global options

| Option | What it does |
|---|---|
| `--verbose / -v` | Show debug logging. |
| `--json` | Emit line-delimited JSON logs instead of console output. |
| `--profile` | Print wall-clock, API request count and the slowest phases when the command exits (same as DFS_PROFILE=1). |
| `--install-completion` | Install completion for the current shell. |
| `--show-completion` | Show completion for the current shell, to copy it or customize the installation. |

## Also used most weeks

Not part of the six steps above, but handy: checking where you are, a quick look at the best plays, and the commands the standard week calls for you.

### `dfs status`

Show whether config is set up and how fresh each data source is.

### `dfs doctor`

Read-only structural check: every tab named in config.toml exists, EdgeRaw's header matches derived.EDGE_COLUMNS, LINKED_EDGE_COLUMNS is linked exactly once (not zero, not twice) on Player Pool/Lineups/ PlayerPoolRaw, Lineups' header repeats fall exactly where LINEUPS_NAME_BLOCKS expects, and Bankroll's configured header rows aren't blank. Never writes anything. Exits non-zero on any failure -- this is the check that would have caught a stale template's missing tabs and drifted column positions before they broke `dfs export`/`dfs lineups clear`/`dfs setup link-edge` on a fresh weekly copy, instead of surfacing three commands later as a crash or a silently wrong formula.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Check a different sheet instead of config.toml's -- e.g. a freshly made weekly copy or the canonical template, before pointing anything at it. |

### `dfs go`

Sync, check the sheet, and report what changed -- the three commands you'd otherwise run back to back every time anyway. Stops at the first failure (a failed sync means nothing to check; a failed doctor means don't trust what changed until it's fixed).

### `dfs edge`

Print the top leverage plays from the last `dfs sync` locally -- no Sheets round-trip, quick look without opening the sheet.

| Option | What it does |
|---|---|
| `--top / -n INT` | Number of leverage plays to show. Default: `20`. |
| `--position / -p STR` | Filter to one position (e.g. RB). |

### `dfs week new`

Move config.toml to a new week's sheet copy: check the new sheet's structure (`dfs doctor`), carry the bankroll and Results log forward, clear last week's lineups, and run a full sync -- in that order, with one confirmation before anything is written.

| Option | What it does |
|---|---|
| `sheet_url STR` | URL (or bare ID) of this week's sheet, already copied from the template. **Required.** |
| `--week INT` | Override the auto-detected NFL week used to title the new sheet. |
| `--yes / -y` | Skip the confirmation prompt. |

### `dfs sync`

Fetch data sources and upload them to the connected Google Sheet.

| Option | What it does |
|---|---|
| `--only STR` | Comma-separated source names to sync (default: all). |
| `--no-upload` | Fetch and store locally, skip Sheets. |
| `--week INT` | Override auto-detected NFL week. |
| `--season INT` | Override auto-detected NFL season. |
| `--live` | Re-sync only fast-moving sources (odds, DK statuses, TFFB projections, weather) plus edge, and print what changed in EdgeRaw's Flag column since the last sync. The Sunday-afternoon command -- not a substitute for a full `dfs sync`. |
| `--sheet-id STR` | Write to this sheet instead of config.toml's (e.g. the template). |

### `dfs export`

Validate paired lineups in the lineups tab and export DK's upload CSV.

| Option | What it does |
|---|---|
| `--output / -o PATH` | Path to write the DK bulk-upload CSV. **Required.** |

### `dfs lineups late-swap`

Check every built lineup in the Lineups tab against real kickoff times, then suggest what to swap: the best full re-fill of the open slots, the best 2-for-2 swaps and the best 1-for-1 swaps -- all within the $50,000 cap (locked players' salaries included), the roster slots (FLEX takes an RB, WR or TE), no DST against your own QB and at most one RB per game. Locked players never move.

| Option | What it does |
|---|---|
| `--top / -n INT` | How many swaps to show per kind (and per open slot for 1-for-1 swaps). Default: `3`. |
| `--metric CHOICE` | What to rank swaps by: ProjPts (default) or AggPts. Default: `ProjPts`. |
| `--all-players` | Also consider the whole rosterable pool, not only your Player Pool. |

### `dfs week close`

End-of-week bankroll reconciliation -- currently a thin wrapper over `dfs bankroll sync --csv`; see below for why it isn't more than that yet.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK contest-history CSV export (My Contests > export). **Required.** |
| `--week INT` | Reconcile this week's ledger instead of the week the connected sheet's own title says it is. Only needed to hand-reconcile a specific week. |

### `dfs results update`

Score every completed week that is not scored yet against nflverse's actual stats, then rebuild the `Model Check` tab from everything scored so far.

| Option | What it does |
|---|---|
| `--week INT` | Score this completed week even if it is already scored. |
| `--all` | Rescore every completed week (flags are recomputed with today's code). |
| `--sheet-id STR` | Write Model Check to this sheet instead of config.toml's (e.g. the template). |
| `--no-sheet` | Score to data/results/ only; do not touch the sheet. |

### `dfs pool add`

Set EdgeRaw's Pool column to "Both" for each name given, by exact match if one exists, else by substring -- multiple matches are printed (name, position, salary) and skipped rather than guessed at. Refine to Cash-only or GPP-only afterward in the sheet itself; the CLI doesn't have a flag for that yet.

| Option | What it does |
|---|---|
| `names STR` | Player name(s) or substrings to add. **Required.** |

### `dfs pool remove`

Clear EdgeRaw's Pool column for each name given -- same matching rules as `dfs pool add`.

| Option | What it does |
|---|---|
| `names STR` | Player name(s) or substrings to remove. **Required.** |

### `dfs pool list`

Every currently-pooled EdgeRaw player, grouped by position.

### `dfs pool clear`

Clear every currently-pooled EdgeRaw player. Confirms first unless `--yes` is given -- this touches every pooled row at once.

| Option | What it does |
|---|---|
| `--yes / -y` | Skip the confirmation prompt. |

### `dfs odds movement`

Diff the two most recent nfl_odds syncs and show which teams' lines moved the most -- what to check before re-running a full sync on Sunday. Needs at least two `dfs sync`/`dfs sync --only nfl_odds` runs this week; the first one has nothing to diff against yet.

| Option | What it does |
|---|---|
| `--top / -n INT` | Number of biggest moves to show. Default: `10`. |

## Reference: bankroll, logs and accounts (only when needed)

Reconciling results and keeping records, and one-time logins. `dfs week close` already calls `bankroll sync` for you.

### `dfs bankroll sync`

Classify contest entries into Cash/GPP and append new ones to the bankroll tab, then auto-fill Results (Week, Cash Pts, H2H Entered/Win -- Fix 2.16/2.17) from the same export, sorted into NFL weeks by each entry's own contest date rather than assuming the file is one week's worth. Cash Line and the team-colour columns in Results stay yours.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK contest-history CSV export (My Contests > export). **Required.** |
| `--week INT` | Reconcile this week's ledger instead of the week the connected sheet's own title says it is. Only needed to hand-reconcile a specific week. |

### `dfs ownership log`

Logs one contest's actual per-player ownership into the durable local ownership log (`data/ownership_log.csv`) -- a standalone record, not a calibration pipeline (the ProjOwn-vs-actual calibration this was originally meant to feed is dropped, Week 3 follow-ups Item 3; see `ownership.py`'s module docstring). File-based on purpose, not automated -- see that same docstring for why an automated per-contest fetch was investigated and deliberately not shipped. Safe to re-run against the same file: replaces that contest's rows rather than duplicating them.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK 'export full standings' CSV, downloaded from a real contest's results page (a per-contest export, not the account-level contest-history one). **Required.** |
| `--contest-id STR` | Defaults to the CSV filename's own contest ID (DK names these contest-standings-<id>.csv) -- pass this only if the file's been renamed. |
| `--week INT` | Defaults to the current NFL week. |
| `--season INT` | Defaults to the current season. |

### `dfs lineups clear`

Clear last week's typed-in lineup data (Lineups/Player Pool name columns, DK Upload) so the sheet's ready for a new week.

| Option | What it does |
|---|---|
| `--yes / -y` | Skip the confirmation prompt. |
| `--sheet-id STR` | Clear a different sheet instead of config.toml's -- e.g. a fresh weekly copy or the canonical template, before pointing config.toml at it. |

### `dfs auth tffb`

One-time interactive login to The Fantasy Footballers (DFS Pass).

### `dfs auth dk`

One-time interactive login to DraftKings.

### `dfs auth fantasypros`

One-time interactive login to FantasyPros (Part C -- anonymous projection pages cap at 10 players/position behind a registration wall; a logged-in session is required for full weekly projections).

### `dfs bankroll backfill-keys`

One-time repair: fills in the dedupe-key column for existing Bankroll rows that don't have one -- confirmed live (2026-09-16) that rows written before this key existed, or by some path that skipped it, are invisible to `sync_bucket`'s dedupe check, so a later sync of overlapping contest history re-appends them as duplicates. Matches each keyless row to exactly one CSV entry by its Place/Entries/ Entry Fee/Prize Pool/Places Paid (fields that round-trip exactly through Sheets' own display formatting); a row matching zero or more than one entry is left alone and reported, never guessed. Never touches a row's own A-H data or an already-populated key. Safe to run against a CSV that also contains entries already fully synced -- only blank key cells are ever written.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK contest-history CSV export (My Contests > export). **Required.** |
| `--sheet-id STR` | Repair a different sheet instead of config.toml's -- e.g. a past week's sheet, since this is a one-time repair for rows that predate the dedupe key, not a standing per-week command. |

### `dfs bankroll build-betting-ledger`

Round 5, item 7a: insert the hand-entered Betting ledger above the Cash ledger (see `sheet_bankroll_view.py`'s module docstring for why above Cash rather than below GPP), wire its Net into the existing Weekly Net rollup, and add the weekly summary row + pending-bet note.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build on a different sheet instead of config.toml's -- template first, then live, per the two-sheet rule. This is a one-time structural build, not a per-week command. |

## Reference: building and styling a sheet (only when needed)

You do not run these in a normal week. Run against the TEMPLATE first, then the live sheet (`--sheet-id`), then `dfs doctor` and `dfs setup audit-style` on both. `dfs setup sheet` runs the whole build in the one order that works.

### `dfs setup sheet`

Run the full one-time sheet build, in the order that actually works, instead of the hand-ordered sequence that used to live only in CONTRIBUTING.md's tribal knowledge. Stops at the first step that fails -- nothing after it runs, so the sheet is left in a known, reported state rather than partially built by whatever happened to come next.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build a different sheet instead of config.toml's -- e.g. the canonical weekly template, so new weekly copies inherit everything below. |

### `dfs setup polish`

Mostly presentation: widths, freeze panes, number formats, header treatment, Flag/Avail chips, and the tab strip ordered by phase of the week -- none of which inserts, deletes, moves or renames a column, row or tab, so no VLOOKUP index, name block or config row range is affected. Two exceptions write real formula values, not styling, but both are safe to re-run the same way: Lineups' Guardrails column (O, additive-only -- it sits strictly left of the EdgeRaw-linked block and was never written to before, see `sheet_style.polish_guardrails`) and each lineup block's totals row (clears the dead per-slot VLOOKUPs a totals row was never a real 10th player for, sums Ceil, and labels the row -- see `sheet_style.polish_lineups_totals_rows`). A third, Instructions -- fully rewritten from `sheet_instructions.py` every run (Week 3 follow-ups, Item 2), so it can't drift the way the old hand-typed version did; `dfs doctor` also checks it for drift on its own, independent of this command.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Style a different sheet instead of config.toml's -- e.g. the canonical weekly template, so new copies are already styled. |
| `--skip-chrome` | Leave the tab strip alone (no reorder, recolour or hiding). |

### `dfs setup build-views`

Create (or rebuild) the four derived, read-only tabs: Board, Slate Grid, Exposure and Movement.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build the view tabs in a different sheet instead of config.toml's. |

### `dfs setup instructions`

Rewrites every row of the Instructions tab from `sheet_instructions.py` -- see that module's own docstring for why this exists (an independent review found the hand-typed version badly stale, Week 3 fixes, Fix 4) and what it does and doesn't fix (numbers/letters/names with a real Python constant behind them are derived; the surrounding English prose is still hand-written and can still describe behavior incorrectly if a feature changes and nobody updates this file).

| Option | What it does |
|---|---|
| `--sheet-id STR` | Regenerate Instructions on a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup link-edge`

Fill in EdgeRaw's derived columns (Leverage, Flags, etc.) on Player Pool, Lineups, AND PlayerPoolRaw (the hub tab those two already VLOOKUP against for Pos./Team/Pts/etc.), via the same VLOOKUP-by-Name join. Writes into whatever column already carries a given name in the header (Part 2's designed order interleaves these with native columns; see `sheet_links.py`'s module docstring) and only creates (appends) a column for a name genuinely absent -- a fresh sheet build that hasn't been through `dfs setup reorder-columns` yet. The four collapsed zones (Game, Ceiling detail, Movement, Weather -- Part 2) are grouped so they can be collapsed from the sheet UI (the little +/- control above the column letters) when you want a narrower view; `Id`/`Flag` (Part 7.9) are hidden outright instead, not grouped. Safe to re-run -- a tab where every linked column already exists is left alone, not duplicated, unless `--force` is given.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Link a different sheet instead of config.toml's -- e.g. the canonical weekly template, so new copies already have EdgeRaw's columns linked in. |
| `--force` | Refresh every linked column's formula even where the tab is already fully linked -- needed after `edge_lookup_formula`'s own generation logic changes (not a position change), since the normal 'something's missing' trigger never fires on an already-linked tab. |

### `dfs setup reorder-columns`

Phase 3/6, one-time: moves PlayerPoolRaw, Player Pool and Lineups into `sheet_columns.py`'s designed column order (a shared spine plus the Game/Ceiling detail/Movement/Weather collapsed groups, Phase 6 Part 2) -- the reorder CONTRIBUTING.md's central hazard section says must happen exactly once, to a designed order with headroom already built in, never again piecemeal.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Reorder a different sheet instead of config.toml's -- ALWAYS run against the canonical template first, verify with `dfs doctor`, then run again against the live sheet. |

### `dfs setup add-pool-control`

A3: create (or refresh) Player Pool's own "add a player" row -- a live search box (ONE_OF_RANGE validation) against EdgeRaw's Name column, pinned at the top of Player Pool itself, for when typing a name is faster than scrolling EdgeRaw to tick a checkbox (the "Pool picking" filter view, `dfs setup add-filters`, is the third way). Replaces the old separate `Pool Picks` tab (see `sheet_pool_control.py` and CONTRIBUTING.md's A3 changelog entry) -- Sam never wanted a second tab for this. The structural row-insert runs at most once per sheet (idempotent, see `ensure_pool_control_row`); safe to re-run any time, including as part of a future `dfs setup polish`.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Add/refresh the control row on a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup add-filters`

Fix 1: sorting and searching are real features that were invisible -- filter views live behind Data > Filter views, easy to never notice.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Add filters to a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup protect`

Warning-only protection (never a hard lock) on every fully formula-driven tab -- PlayerPoolRaw, Board, Slate Grid, Movement -- plus Player Pool, Exposure and Lineups protected everywhere EXCEPT their own typed cells (Player Pool's add-a-player control; Exposure's Target and lineup-count cell; each lineup block's Name column). EdgeRaw is left alone entirely -- see `sheet_protection.py`'s own docstring for why. Safe to re-run: each tab's protected ranges are cleared before being re-added, never stacked.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Protect a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup audit-style`

Read-only style check: per tab, the header row carries the shared dark fill, a freeze pane covers it, every column has an explicit pixel width, every FIELD_FORMATS column isn't left on Sheets' "Automatic" number format, and a Flag/Avail column has a matching chip rule. Exists so `sheet_style.py`'s formatting can't quietly rot the way its number-format dicts once did (see Task 2.1's consolidation into `FIELD_FORMATS`) -- run this after `dfs setup polish` rather than trusting its own "OK" output. Never writes anything. Exits non-zero if any audited tab has a finding.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Audit a different sheet instead of config.toml's -- e.g. right after `dfs sheets polish`, to confirm what actually landed. |

### `dfs setup build-season`

Round 5, item 7d: creates (or completely rewrites) the Season tab -- one pre-built row per NFL week, a year-to-date rollup, and a cumulative-net-by-week chart. See `sheet_season_view.py`'s module docstring for the full column layout.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build on a different sheet instead of config.toml's -- template first, then live, per the two-sheet rule. |

### `dfs setup inspect`

List every tab in the connected sheet, with dimensions and header row.

## Reference: one-time repairs and migrations (only when needed)

Each of these fixed a specific past change to the sheet's structure; they are safe to re-run but you should not need them. `CONTRIBUTING.md`'s structural changelog says when each ran.

### `dfs setup repair-formula-ranges`

Round 5 follow-up item 2 (2026-09-29): `dfs doctor` now requires a formula on every row of Results' `Cash Results`/`H2H %`, Season's total/cumulative columns, and every DkSalClean/PlayerPoolRaw column (rows 2 to `PLAYER_POOL_RAW_BLOCK`'s last), and -- on Results/Season/DkSalClean -- that row N reads row N. This rewrites each gap from the nearest healthy row and clears formulas left below the documented last row on DkSalClean/PlayerPoolRaw. Only flagged cells are written; healthy ones are never touched. Template first, then the live sheet; run `dfs doctor` afterwards.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Repair a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup guard-empty-states`

Round 5 cleanup item 2 (2026-09-29). Wraps the divisions and averages on Results, Bankroll and Season in a guard that returns a blank when its inputs are empty (`=F2/E2` becomes `=IF(E2="","",IFERROR(F2/E2,""))`), and rebuilds the Season chart with its `#N/A` helper block (S:W) hidden and "plot hidden data" on. Safe to re-run: guarded formulas are left alone. Template first, then the live sheet; run `dfs doctor` after.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Apply to a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup resize-player-pool`

PROMPT_BOARD_FIXES.md item 8 (2026-09-25): grows each of Player Pool's five position blocks to `weekly_reset.PLAYER_POOL_BLOCK_ROWS`' row counts (QB/TE 10->17, RB 20->22, WR 25->27, DST 10->12) via real `insertDimension` calls (`sheet_pool_resize.resize_player_pool`) -- restoring the player capacity `sheet_pool_formulas._grouped_with_ separators_formula`'s own blank separator rows (Fix 3/A7) had been silently eating into.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Resize a different sheet instead of config.toml's -- ALWAYS run against the canonical template first, verify with real cell reads, then run again against the live sheet. |

### `dfs setup fix-opp-pos-rank`

Found live 2026-09-16, the first time real strength-of-schedule data ever flowed through it: `PlayerPoolRaw`'s `OppPosRank` -- a hand-typed `VLOOKUP`+`HLOOKUP` against `SoSComb` that nothing in this codebase previously regenerated -- was keyed on this row's own `Team` column instead of `Opp.`, so every value measured how tough a player's OWN defense is, never their actual opponent's. Rewrites every row's formula, keyed correctly this time (`sheet_pool_raw_sos. rewrite_opp_pos_rank`). Player Pool/Lineups need no separate fix -- both already VLOOKUP their own `OppPosRank` off PlayerPoolRaw by Name, so they self-heal the moment PlayerPoolRaw's own value is correct.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup fix-pct-of-cap`

Part 7.9 (2026-09-17): `Lineups`' `% of Own` column (renamed from `% of Rstr` in Phase 6, Part 2) was really cap allocation misnamed and mis-derived -- `=F<row>/F$<totals_row>`, this player's DK Sal as a share of the lineup's own running salary total, not its rostership. Renames the header to `% of Cap` in place first (`rename_header_ column`, same "rename before any name-based lookup touches it" order as every other column rename in this codebase), then redivides by the salary cap (`config.toml`'s `[lineups] salary_cap`) instead, which also removes the `#DIV/0!` Part 1.2 previously guarded against -- a constant denominator can't divide by zero (`sheet_style.polish_lineups_pct_of_cap`).

| Option | What it does |
|---|---|
| `--sheet-id STR` | Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup fix-flag-split`

Part 7.9 (2026-09-17), three changes to `PlayerPoolRaw`/`Player Pool`/`Lineups`, run together since all three touch the same Ceiling-detail/spine region:

| Option | What it does |
|---|---|
| `--sheet-id STR` | Fix a different sheet instead of config.toml's -- ALWAYS run against the canonical template first, verify with `dfs doctor`, then run again against the live sheet. |

### `dfs setup fix-lineups-dst-label`

Found live (2026-09-18) verifying Part 7.4's DST-vs-own-QB guardrail with a real test lineup: it never fired. `Lineups`' own `Pos.` column is static text, one fixed roster-slot label per row -- the defense slot says `"DEF"` on every block, both sheets, instead of `"DST"` (this codebase's own convention everywhere else), so the guardrail's own `MATCH("DST", ...)` could never find it (`sheet_style.fix_lineups_dst_slot_label`).

| Option | What it does |
|---|---|
| `--sheet-id STR` | Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup remove-pool-deck`

Phase 5 (2026-09-16): the pool deck -- frozen rows at the top of Lineups holding a sortable/filterable window into Player Pool -- is retired. Sam, after a week building real lineups against it: "I've used it week 1 and it was a pain," and on the "where is this player" jump control alone -- "doesn't get me much. Cut it." Player Pool's own colour scales/chips/`Used`/`In` columns cover the browsing job now.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Remove the pool deck from a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup remove-retired-tabs`

Phase 6 Part 4b (2026-09-22): `Scratch` (a blank drafting grid, no formulas) and the `EntriesRaw`/`GPPin`/`DKLineupsRaw`/`DKLineupsFinal` hand-paste DK-contest-history chain (superseded by `dfs week close --csv`'s CSV-based path) are retired -- Sam confirmed he does not use any of the five, and `dfs` never read or wrote them except to clear/style them. Asymmetric by design: run this with `--mode delete` against the template and `--mode hide` against the live sheet -- never delete on live, `EntriesRaw` may hold real pasted history. Idempotent either way. Also removes these five's own rows from the `Instructions` tab on whichever sheet this runs against, regardless of `--mode` -- the documentation should read correctly on both. See `sheet_tab_removal.py`'s module docstring and `docs/planning/PROMPT_DATA.md` for where a past entry's roster-slot detail lives now that `EntriesRaw` is gone (the DK export CSVs on disk).

| Option | What it does |
|---|---|
| `--mode STR` | 'delete' (template -- future weekly copies start clean) or 'hide' (live sheet -- EntriesRaw may hold real pasted history a delete can't recover; a hidden tab is still fully readable/writable by dfs sync). See Part 4b in CONTRIBUTING.md's changelog. **Required.** |
| `--sheet-id STR` | Act on a different sheet instead of config.toml's -- e.g. the canonical weekly template (pass --mode delete there; the live sheet should get --mode hide). |

### `dfs setup remove-sos-placeholders`

Phase 5 (2026-09-16): `SoS 1`/`SoS 2`/`SoS 3`/`SoS 4` -- four blank GAME-zone columns reserved on PlayerPoolRaw/Player Pool/Lineups back when strength-of-schedule data didn't exist yet -- are retired now that the real sync (`sources/tffb_sos.py`) landed straight into `OppPosRank` instead. Sam: "Why still sos 1-4. Should only be one per player." Deletes all four columns (a real Sheets column delete, so everything to their right shifts left) from each of the three tabs. No-op per tab if none of the four are present (already removed, or a sheet built after this migration already shipped without them). See `sheet_reorder.remove_header_columns` and CONTRIBUTING.md's changelog.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Remove the placeholders from a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup remove-lineup-metrics`

Round 5 item 1c (2026-09-29): Sam doesn't use `Stack`, `Bring-back` or `Own% Used`, and `Sub-10%` never worked to his eye. Deletes all four from Lineups (a real Sheets column delete -- everything to their right shifts left, and the repeated per-block header rows go with them). `Games` and `Min Unique` stay. No-op if none are present. See `sheet_reorder.remove_header_columns` and CONTRIBUTING.md's changelog; run `dfs doctor` afterwards.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Remove the columns from a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup remove-model-implied`

Round 5 item 5c (2026-09-29): GPS's `Implied Total` is Vegas, not a model (`gps_check.py`), so EdgeRaw's `ModelImplied` and its linked copies on PlayerPoolRaw/Player Pool/Lineups are retired. This deletes the three linked columns (a real Sheets column delete -- everything to their right shifts left). EdgeRaw itself just needs a fresh `dfs sync --only edge`, and `dfs setup link-edge --force` then re-derives every VLOOKUP index. Run in that order; no-op per tab if the column is already gone.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Remove the column from a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

## Data sources

What `dfs sync --only NAME[,NAME]` takes. A plain `dfs sync` runs all of them in this order; `dfs sync --live` re-runs only the ones marked. The tab is the default name from `config.example.toml`; a source with no tab feeds EdgeRaw directly. A source that fails is reported and skipped, never fatal to the others.

| Source | Sheet tab | In `--live` | What it is |
|---|---|---|---|
| `nfl_odds` | `Odds` | yes | NFL odds from Rotowire's DraftKings market feed. |
| `draftkings` | `Salaries` | yes | DraftKings NFL salaries: draftgroups + player-pool CSV, both unauthenticated. |
| `projections` | `Projections` | yes | DraftKings projections from The Fantasy Footballers' DFS Pass optimizer. |
| `sleeper` | `SleeperRaw` | - | Sleeper's free, unauthenticated, undocumented weekly projections API -- Part C, C3. |
| `fantasypros` | `FantasyProsRaw` | - | FantasyPros weekly projection pages -- Part C, C4. |
| `snaps` | `SnapsRaw` | - | nflverse's free, unauthenticated snap-count release -- Part C, C6. |
| `pbp` | `TeamMetricsRaw` | - | nflverse's free, unauthenticated play-by-play release -- Part C, C7. |
| `usage` | `(none)` | - | Per-player usage (`Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`); no sheet tab, it feeds EdgeRaw. |
| `tffb_gps` | `GPSRaw` | - | Kyle Borgognoni's (@kyle_borg) weekly "Pace of Play: Matchups & Stacks for Week N" article on TFFB -- Sam's existing subscription, so this is not a new paid source (`docs/planning/PROMPT_GPS.md`). |
| `sos_qb` | `SoSQB` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_rb` | `SoSRB` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_wr` | `SoSWr` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_te` | `SoSTE` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_dst` | `SoSDef` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `nflverse_games` | `GamesRaw` | - | Per-game context (stadium, roof, surface, rest days, closing lines) from nflverse's free, unauthenticated `games.csv`. |
| `weather` | `WeatherRaw` | yes | Wind/precipitation/temperature for this week's outdoor games, from Open-Meteo -- free, no API key at all (unlike WeatherAPI.com, which docs/planning/archive/HANDOFF.md originally suggested). |
| `edge` | `EdgeRaw` | yes | Derived edge-layer signals (Leverage, CeilVal, GameEnv, ImpliedMove/TotMove/ SpdMove, Avail, Flag) computed locally from already-synced sources -- no network call of its own. |
