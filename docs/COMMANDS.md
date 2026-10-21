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
| 3 | In the sheet (no command) | Board, Slate Grid, EdgeRaw, Player Pool, Lineups | Research, tick your pool, build lineups. `dfs pool add/remove/list` does the pool without opening the sheet. `dfs lineups suggest` writes complete builds from your pool to the Suggested tab (Lineup Tools > Copy builds to Lineups drops one in); keep, lock, edit or drop builds there and run `dfs lineups suggest --iterate` to rebuild around your decisions. |
| 4 | Lineups are built | `dfs export -o lineups.csv` | Validates the slots on DK Upload and writes DraftKings' bulk-upload CSV. Before it: download DraftKings' entries file and run `dfs entries fill` (it lists the entries on DK Upload; you choose each entry's Lineup there), then run it again to fill the slots (`dfs entries fill --write` writes the upload file itself). `dfs entries print` lists every lineup as `Name (ID)`. |
| 5 | Sunday, before and between kickoffs | `dfs sync --live` | Fast refresh of odds, DK statuses, TFFB projections (with projected ownership) and weather only; prints what changed. Then `dfs lineups late-swap` shows who is still swappable and the best swaps for them. |
| 6 | After the games (nflverse posts stats a day or two late) | `dfs week close` | Reconciles Cash/GPP into Bankroll and Results from the newest contest-history export in Downloads (or `--csv <file>`), keeps your DraftKings downloads (history copy, standings files) and rebuilds the Contests tab, then scores the week's projections into the Model Check tab. `dfs results update` does just the scoring, any time. |

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

Read-only structural check: every tab named in config.toml exists, EdgeRaw's header matches derived.EDGE_COLUMNS, LINKED_EDGE_COLUMNS is linked exactly once (not zero, not twice) on Player Pool/Lineups/ PlayerPoolRaw, Lineups' header repeats fall exactly where LINEUPS_NAME_BLOCKS expects, and Bankroll's configured header rows aren't blank. Never writes anything. Exits non-zero on any failure -- this is the check that would have caught a stale template's missing tabs and drifted column positions before they broke `dfs export`/`dfs lineups clear`/`dfs setup link-edge` on a fresh weekly copy, instead of surfacing three commands later as a crash or a silently wrong formula. It also WARNS (without failing) when the bound Apps Script behind the `Set` dropdowns is not pasted or is older than apps_script/Code.gs, and when fewer than 100 Odds API credits are left.

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
| `--props` | Player props from The Odds API (about 6 credits a game, see docs/WORKFLOW.md). A plain full sync pulls them ONCE per NFL week, on the first full sync of a Saturday or Sunday. With --live this refreshes only the games whose players' DraftKings status changed since the last pull (nothing spent if none did); on a full sync it forces a pull. A plain --live never spends credits, and every pull must fit the budget (credits left / NFL weekends before the monthly reset). |
| `--fixed-lines` | Score against the old FIXED lines (your season-median typed Cash Line, and [sim] gpp_target) instead of lines that move with the slate (the field's average score plus a margin, [sim.lines]). Kept for one season so the old and new numbers can be compared. |

### `dfs lineups suggest`

Build complete lineups from your Player Pool and write them to the Suggested tab.

| Option | What it does |
|---|---|
| `--cash INT RANGE` | How many cash builds (0 for none). Default: `3`. |
| `--gpp INT RANGE` | How many GPP builds (0 for none). Left off, every QB stack gets a build and ideally two. |
| `--no-write` | Print the builds here and leave the Suggested tab alone. |
| `--seconds FLOAT RANGE` | The search budget, in seconds of search work (default 120, or 12 for --iterate; a fresh run gives cash 40%, GPP the rest). A second is a fixed amount of search, about a real second on a Mac, so the same pool always gives the same builds. Raise it for a big pool when the run says the time limit cut it short. |
| `--fixed-lines` | Score against the old FIXED lines (your season-median typed Cash Line, and [sim] gpp_target) instead of lines that move with the slate (the field's average score plus a margin, [sim.lines]). Kept for one season so the old and new numbers can be compared. |
| `--gpp-entries INT RANGE` | Size of the GPP the builds are for. The duplicates each build is expected to share are read against it (shown on every GPP build; they rank only with --rank share). Default: `1000`. |
| `--double-up-entries INT RANGE` | Size of a double-up, for the warning on a cash build many entries are expected to share. Default: `3000`. |
| `--rank CHOICE` | What orders the GPP candidates: top1 (their Top 1% chance) or share (that chance divided by 1 + the identical entries expected). Default: `top1`. |
| `--iterate` | Rerun around what you decided on the Suggested tab: Keep / Rebuild / Drop / Vary N on each build, the lock ticks, names you typed over a player (they count as locks) and the rules box. Kept builds are only rescored; rebuilt and new ones are searched (about 15 s). --cash / --gpp are ignored: the rules box's Builds wanted line says how many. |
| `--round INT RANGE` | Bring an earlier round back: its builds, locks, decisions and rules, rescored and written as a new round (the state is kept in data/suggest_state/<week>.json). |
| `--portfolio` | Also choose which GPP build goes in which of your GPP entries as one set (DK's entries file, the newest in Downloads, says which contests and how many entries) and print the plan beside today's pick; `dfs entries fill` then shows it as the grey Suggested hint. Off by default: it has not been proven yet. Needs `dfs stacks --refit` once. |
| `--portfolio-objective CHOICE` | What --portfolio maximises: edollars (expected winnings), top1 (the chance at least one entry finishes in a top 1%) or robust (risk-adjusted winnings on draws where the projections are wrong together). Default: `edollars`. |
| `--entries-csv PATH` | With --portfolio: the DraftKings entries file to read (default: the newest in Downloads). |

### `dfs stacks`

Rank every QB's stacks of one and two teammates (with and without a bring-back) by what the simulator thinks of them against how much of the generated field plays them, and write the `Stacks` tab where the sheet has one (`dfs setup stacks` creates it). Prints the same page. Before TFFB publishes ownership the field rate and leverage are left out. An information view: the tab shows its retro track record beside it.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Write to a different sheet instead of config.toml's (the template, say). |
| `--no-sheet` | Print the ranking; write nothing. |
| `--refit` | Refit the field model on the real standings first, and save it (about two minutes). |
| `--retro` | Refit, then rerun the retro on the weeks with standings and save the track record the tab shows (several minutes). |

### `dfs entries fill`

Fill DraftKings' entries file from the lineups you chose on the DK Upload tab.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DraftKings entries file (Edit entries). Left out, the newest one in your Downloads folder is used (found by its header row) and the command says which. |
| `--write` | Also write upload_<timestamp>.csv: only the entries you set that DK does not already hold. |
| `--output / -o PATH` | Where --write puts the file (default: here). |
| `--again` | After a late swap changed a lineup: rewrite only the entries whose lineup now differs from the slots on DK Upload. Nothing else on the tab changes. |
| `--dry-run` | Read and report only: write neither the DK Upload tab nor a file. |
| `--sheet-id STR` | Use a different sheet instead of config.toml's (e.g. the template). |

### `dfs entries print`

Print every lineup on the Lineups tab in DraftKings' `Name (ID)` slot format, and write it to a file.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DraftKings entries file. Left out, the newest one in your Downloads folder is used (found by its header row) and the command says which. |
| `--output / -o PATH` | Where to write the file (default: dk_lineups_<timestamp>.csv here). |
| `--sheet-id STR` | Read a different sheet instead of config.toml's. |

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
| `--metric CHOICE` | What to rank swaps by: CalPts, ProjPts or AggPts. Default: config.toml's [projection] default (CalPts, with ProjPts where CalPts is blank). |
| `--all-players` | Also consider the whole rosterable pool, not only your Player Pool. |
| `--goal CHOICE` | Rank swaps by the simulator's change in P(cash) or P(GPP); both are shown. Default: each lineup's Cash/GPP marker (column A of its Total row; cash if blank). |
| `--fixed-lines` | Score against the old FIXED lines (your season-median typed Cash Line, and [sim] gpp_target) instead of lines that move with the slate (the field's average score plus a margin, [sim.lines]). Kept for one season so the old and new numbers can be compared. |
| `--field STR` | Score swaps against the REAL field of a live contest: the standings export you downloaded by hand after the 1 pm lock (DraftKings > the contest > export full standings). Pass a file (repeat for a Double Up and a GPP), or `auto` for the newest live exports in your Downloads folder (this week's, found by their content; the entries file there says each contest's format). Without it, swaps are scored against the projected-ownership line. |
| `--state CHOICE` | `live`: take every started player's points so far from ESPN's public box scores (no download from DraftKings) and score swaps against the projected-ownership line on that state: the games already played are known, the rest simulated. If ESPN cannot be read, says so and runs as without it. Not with --field, whose export has the same points and the real field. |
| `--refresh-injuries` | Lay ESPN's game-day injury feed over the last sync's Avail first (Out, IR, Doubtful and Questionable only escalate a status, never clear one), and list what changed. Use it in the late window, after news breaks. |

### `dfs week close`

End-of-week bankroll reconciliation -- currently a thin wrapper over `dfs bankroll sync --csv`; see below for why it isn't more than that yet.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK contest-history CSV export (My Contests > export). Left out, the newest history export in your Downloads folder is used (found by its header row; [contests] downloads_dir in config.toml changes the folder). |
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

### `dfs odds snapshot`

Save the current betting lines (and nothing else): no sheet, no other source. Line movement for the week is measured against the week's opening lines, and the odds source only shows the current slate, so the opening is on record only if the lines were saved before the sheet was copied: run this early in the week (Monday or Tuesday morning), before `dfs week new`.

## Reference: bankroll, logs and accounts (only when needed)

Reconciling results and keeping records, and one-time logins. `dfs week close` already calls `bankroll sync` for you, and `dfs week new` rebuilds the STAKES block (`bankroll stakes`).

### `dfs bankroll sync`

Classify contest entries into Cash/GPP and append new ones to the bankroll tab, then auto-fill Results (Week, Cash Pts, H2H Entered/Win -- Fix 2.16/2.17) from the same export, sorted into NFL weeks by each entry's own contest date rather than assuming the file is one week's worth. Cash Line and the team-colour columns in Results stay yours.

| Option | What it does |
|---|---|
| `--csv PATH` | Path to a DK contest-history CSV export (My Contests > export). Left out, the newest history export in your Downloads folder is used (found by its header row). |
| `--week INT` | Reconcile this week's ledger instead of the week the connected sheet's own title says it is. Only needed to hand-reconcile a specific week. |

### `dfs bankroll stakes`

Rebuild the STAKES block on the Bankroll tab: your own ROI, cash rate and weekly volatility by format (this season and every season in the contest log), and this week's plan from the Weekly Budget on the tab and the rules you set under `[stakes]` in config.toml. Arithmetic on your history and your rules, not advice. `--propose` only prints; the block is rebuilt from scratch each run and nothing else on the tab is touched (it refuses if its area holds anything that is not a STAKES block).

| Option | What it does |
|---|---|
| `--propose` | Print values for max_pct_bankroll_per_week and max_entry_fee_pct worked out from your own bankroll and weekly volatility, with the arithmetic. Read-only: nothing is written anywhere. |
| `--no-sheet` | Print the plan; do not write the STAKES block to the sheet. |
| `--sheet-id STR` | Read and write this sheet instead of config.toml's (e.g. the template: template first, then the live sheet). |

### `dfs contests update`

The contest side of `dfs week close`, on its own: keeps the history export and moves new standings files out of Downloads (DraftKings only serves standings for about two weeks), reads them, logs their ownership, fills Results' blank Cash Line from the configured double-up, and rebuilds the Contests tab. Never contacts DraftKings.

| Option | What it does |
|---|---|
| `--csv PATH` | A DraftKings contest-history export. Left out, the newest one in your Downloads folder ([contests] downloads_dir) is used, found by its header row. |
| `--sheet-id STR` | Write the Contests tab to this sheet instead of config.toml's (the template first). Results' Cash Line is only ever filled on config.toml's own sheet. |
| `--no-sheet` | Keep the files and run the engine; touch no sheet. |

### `dfs contests analyze`

Merges your DraftKings history into a durable log, tags every contest with its series and format, scores how tough each series is (and your head-to-head record), and, given a folder of standings files, measures how the fields build their lineups. Writes CSVs under --out and prints a short summary; it never touches a sheet and never contacts DraftKings.

| Option | What it does |
|---|---|
| `--history PATH` | A DraftKings My Contests -> History export (CSV). Repeat for several; they are merged into the durable log, deduplicated by Entry_Key. **Required.** |
| `--standings PATH` | A folder of contest-standings-<id>.csv files and/or zips of them (searched recursively). |
| `--out PATH` | Where the log and the CSVs are written. Default: `data/contests`. |
| `--me STR` | Your DraftKings username. Default: `srwildman`. |
| `--players PATH` | CSV of players: Name, Team, Position, Opponent and optionally Salary (EdgeRaw has them), with Season and Week columns to cover several weeks. Without it each week's players come from the last DraftKings salary snapshot before that week's lock (data/raw/draftkings). |
| `--slate-index STR` | Which slate-scoring index is shown beside each week's fitted effect, and is the 'index' comparator: top_n_lineup, p95_optimal, points_per_game, or 'none' to leave it out. Default: `top_n_lineup`. |
| `--slate-index-file PATH` | A season,week,index CSV to use instead of computing the index. |

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

## Reference: the lineup simulator (only when needed)

The Typical, Big night, Cash % and Top 1% numbers on each Lineups Total row are written by every `dfs sync` (Cash % and the last number are measured against lines that move with the slate; `--fixed-lines` on `dfs sync`, `dfs lineups suggest` and `dfs lineups late-swap` gives the old fixed ones); these commands rebuild or check the correlations behind them. They read the model cache `dfs model fetch` fills and never touch a sheet.

### `dfs sim fit`

Rebuild models/sim/correlations.csv (and the QB1-TE1 team-shape table beside it) from the cached history (about a minute).

| Option | What it does |
|---|---|
| `--n-boot INT` | Bootstrap resamples for each interval. Default: `1000`. |
| `--seed INT` | Seed for every random draw. |
| `--out-dir PATH` | Where to write (default: models/sim/). |
| `--through INT` | Last season to fit on. Default: the shipped model's last complete season; the current season (`dfs model fetch` first) adds the weeks played so far. |

### `dfs sim backtest`

Back-test on 2023-2025: random legal lineups, correlations fitted on 2014-2022 only (about 30 minutes at the defaults; lower --lineups-per-week for a quick look).

| Option | What it does |
|---|---|
| `--lineups-per-week INT` | Random legal lineups per week. Default: `200`. |
| `--n-sims INT` | Simulations per lineup. Default: `10000`. |
| `--seed INT` | Seed for every random draw. |
| `--jobs INT` | Worker processes (the result does not depend on it). Default: `1`. |
| `--save` | Also write the report to models/sim/backtest.md. |
| `--results PATH` | Also write the per-lineup results (parquet). |
| `--from-results PATH` | Print the report for saved per-lineup results instead of re-simulating. |

### `dfs sim script`

Game script: does a pair's correlation depend on the spread or the team's implied total, do the projections already price it, and does any conditional table beat the pooled one on 2023-2025? Fetches the play-by-play for the mechanism tables (cached under data/model_cache). About 15 minutes on four cores with the defaults; `--lineups 0` skips the slowest part. Never touches a sheet.

| Option | What it does |
|---|---|
| `--lineups INT` | Lineups per family and week for the lineup-level test (0 skips that part). Default: `50`. |
| `--jobs INT` | Worker processes for the lineup-level test. Default: `1`. |
| `--shuffles INT` | Shuffled-context scans behind the chance flags. Default: `200`. |
| `--null-draws INT` | Shuffled-context tables behind the out-of-sample control. Default: `300`. |
| `--seed INT` | Seed for every random draw. |
| `--save` | Also write the report to models/sim/script.md. |
| `--results PATH` | Also write the per-lineup results (parquet). |
| `--from-results PATH` | Use saved per-lineup results instead of re-simulating them. |
| `--keep PATH` | Also pickle the whole analysis, to re-render the report without refitting. |

### `dfs sim demo`

Two lineups from 2025 week 12 -- one stacked around Patrick Mahomes, one not -- and the portfolio.

| Option | What it does |
|---|---|
| `--cash-line FLOAT` | Cash line in points (default 145). |
| `--gpp-target FLOAT` | GPP target in points (default 190). |

## Reference: the UM model and the research pack (only when needed)

The player-outcome model behind CalPts, Floor and the simulator's distributions, and the study pack that measured the Edge Finder's constants. Nothing here touches a sheet or needs a login; `dfs model fetch` fills the cache `dfs sim fit` reads.

### `dfs model fetch`

Download or refresh the public nflverse / ffopportunity history into data/model_cache/.

| Option | What it does |
|---|---|
| `--refresh-all` | Re-download completed seasons too (they never change). |

### `dfs model train`

Rebuild the artifacts: holdout comparison, shipping decision, shipped models, distribution tables and backtest.md. Run it once a season, or on demand; it needs `dfs model fetch` first.

| Option | What it does |
|---|---|
| `--through INT` | Last season to train on (default: the last complete season in the cache). |
| `--out-dir PATH` | Where to write artifacts (default: models/um/). |

### `dfs model backtest`

Print the holdout and calibration tables. By default this recomputes them from the cache (about half a minute) without writing anything; `--saved` prints the report that shipped with the artifacts.

| Option | What it does |
|---|---|
| `--saved` | Print the shipped backtest.md instead of recomputing. |

### `dfs model info`

Show the shipped artifacts: version, training seasons, chosen method per position, holdout metrics and the scikit-learn version they were built with (flagging a mismatch).

### `dfs research fetch`

Download the 2014-2025 public history the studies read (player and team stats, ffopportunity, the schedule, injury reports and a reduced play-by-play) into data/model_cache/ and data/research_cache/.

| Option | What it does |
|---|---|
| `--refresh` | Re-download seasons already cached. |

### `dfs research run`

Rebuild the study outputs under models/research/ from the cache (no network).

| Option | What it does |
|---|---|
| `--study STR` | r1, r2, r3, r4, r5, r6 or all. Default: `all`. |

### `dfs research refresh`

Rerun the weekly offline studies with the closed weeks so far and write one report.

| Option | What it does |
|---|---|
| `--through INT` | Last closed week to include (default: the newest scored week). |
| `--season INT` | Override the auto-detected NFL season. |
| `--shuffles INT` | Shuffled-feature tables behind the five-pair retest's chance control. Default: `40`. |
| `--only STR` | Comma-separated studies to run: shape, bump, recal, tails, ownership, shadows, props, contest_retro, curse; backtest (the Flashback backtest, about 6 minutes) runs only when named here. |

### `dfs backtest`

Would these build settings have made money? Rebuilds each week with today's code from the last pre-lock snapshot (no lookahead), on your pool where one was saved (`data/pools/`, written at every sync and `suggest`; earlier weeks use the constructed pool of the contest retro), puts the builds into the contests you actually entered and scores them against those contests' real fields and payouts: dollars, ROI, cash rate and top-1% hits beside your own lineups in the same contests. Local and read-only: no sheet, no DraftKings. Writes docs/research/backtest/ (gitignored) and prints a short summary. About 6 minutes for four weeks: the search runs to the end, so a rerun of the same settings gives the same builds.

| Option | What it does |
|---|---|
| `--weeks STR` | Comma-separated weeks to rebuild (each needs standings and a pre-lock snapshot on disk). Default: `2,4,5,6`. |
| `--settings FILE` | A settings TOML (builds, rules lines, named flags; see dfs.backtest_settings). Left off: today's defaults, what a plain `dfs lineups suggest` builds (with a search that runs to the end). |
| `--compare FILE` | A second settings TOML to backtest on the same contests; prints the paired difference (--settings minus --compare, so name the baseline here) with standard errors and weeks won. |
| `--fresh` | Rebuild even where a saved run of the same code exists. |
| `--workers INT RANGE` | Weeks built in parallel. Default: `6`. |
| `--no-report` | Print the summary; write no report file. |

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

A3: create (or refresh) Player Pool's own "add a player" row -- a type dropdown (Cash / GPP / Both, default Both) and a live search box (ONE_OF_RANGE validation) against EdgeRaw's Name column, pinned at the top of Player Pool itself, for when typing a name is faster than scrolling EdgeRaw to tick a checkbox (the "Pool picking" filter view, `dfs setup add-filters`, is the third way). Replaces the old separate `Pool Picks` tab (see `sheet_pool_control.py` and CONTRIBUTING.md's A3 changelog entry) -- Sam never wanted a second tab for this. The structural row-insert runs at most once per sheet (idempotent, see `ensure_pool_control_row`); safe to re-run any time, including as part of a future `dfs setup polish`.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Add/refresh the control row on a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup dk-upload`

Put the entries layout on the DK Upload tab: DK's columns, then Format, a typed Lineup dropdown (L1..L20, pale-yellow, the one cell exempt from the tab's protection), and the grey Suggested and Why columns `dfs entries fill` writes. Safe to re-run: it never touches an entry row's values.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build it on a different sheet instead of config.toml's, e.g. the canonical weekly template (always the template first). |

### `dfs setup cockpit`

Create the `Cockpit` tab right before Board (to its left), or rebuild it. One read-only page for the last 90 minutes of a game day: new Q / D / OUT among your players, what is still swappable with the best change from the last late-swap run, wind, the Lineups warnings, your exposure, entries without a lineup and the budget. Nothing on it is typed. `dfs sync --live` and `dfs lineups late-swap` keep it fresh from then on (only on a sheet that has the tab). Template first, then the live week; `dfs doctor` after.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build it on a different sheet instead of config.toml's, e.g. the canonical weekly template (always the template first). |

### `dfs setup player-card`

Create the `Player` tab right after Edge Finder, or rebuild it. Pick a player in B1 (a dropdown over EdgeRaw's names, the only typed cell) and read everything about him on one page: projections side by side, ownership, his last five games of usage, matchup and game, props, the signals firing on him with Model Check's record for each, and how many of your lineups he is in. Formulas over EdgeRaw plus two hidden raw tabs that `dfs sync` keeps fresh (only on a sheet that has the tab). Template first, then the live week; `dfs doctor` after.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build it on a different sheet instead of config.toml's, e.g. the canonical weekly template (always the template first). |

### `dfs setup stacks`

Create the `Stacks` tab right after Suggested, or rebuild it: the top 20 QB stacks and top 8 games ranked by simulated upside against how much of the field plays them, with the retro's track record on top. Nothing on it is typed. `dfs stacks` and `dfs sync` keep it fresh from then on (only on a sheet that has the tab). Needs the fitted field model (`dfs stacks --refit` once). Template first, then the live week; `dfs doctor` after.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Build it on a different sheet instead of config.toml's, e.g. the canonical weekly template (always the template first). |

### `dfs setup add-filters`

Fix 1: sorting and searching are real features that were invisible -- filter views live behind Data > Filter views, easy to never notice.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Add filters to a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup protect`

Warning-only protection (never a hard lock) on every fully formula-driven tab -- PlayerPoolRaw, Board, Slate Grid, Movement -- plus Player Pool, Exposure and Lineups protected everywhere EXCEPT their own typed cells (Player Pool's add-a-player control; Exposure's Target column; each lineup block's Name column and its Cash/GPP marker). EdgeRaw is left alone entirely -- see `sheet_protection.py`'s own docstring for why. Safe to re-run: each tab's protected ranges are cleared before being re-added, never stacked.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Protect a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

### `dfs setup suggested-controls`

Make the Suggested tab ready to iterate on (`dfs lineups suggest --iterate`). Safe to re-run.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Set up a different sheet instead of config.toml's -- e.g. the canonical weekly template. |

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

### `dfs setup lineup-chalk`

Rewrites Lineups' Issues formulas and chips (the same step `dfs setup polish` runs) so a complete lineup that holds none of the heavily owned players (projected 16%+ owned, about 20% expected in a double-up; 25%+ for a lineup marked GPP) shows "Chalk: Name N% +k" on its Total row. Nothing else on the tab changes. Template first, then the live sheet; run `dfs doctor` after.

| Option | What it does |
|---|---|
| `--sheet-id STR` | Apply to a different sheet instead of config.toml's -- the template first. |

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
| `espn` | `(none)` | - | ESPN's public fantasy API: weekly player projections, scored to DraftKings rules -- `EspnPts`. |
| `odds_props` | `(none)` | - | `odds_props`: The Odds API player props, turned into `PropPts` (`props.py` does the maths). |
| `snaps` | `SnapsRaw` | - | nflverse's free, unauthenticated snap-count release -- Part C, C6. |
| `pbp` | `TeamMetricsRaw` | - | nflverse's free, unauthenticated play-by-play release -- Part C, C7. |
| `usage` | `(none)` | - | Per-player usage (`Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`); no sheet tab, it feeds EdgeRaw. |
| `player_log` | `(none)` | - | Each QB / RB / WR / TE's last five games played, for the Player card (`player_card.usage_log`). |
| `ffopportunity` | `(none)` | - | Per-player-week expected points (`xFP`), current and prior season; no sheet tab. |
| `nflverse_injuries` | `(none)` | yes | This season's injury reports; no sheet tab. |
| `nflverse_depth` | `(none)` | yes | Recent depth-chart snapshots; no sheet tab. |
| `tffb_gps` | `GPSRaw` | - | Kyle Borgognoni's (@kyle_borg) weekly "Pace of Play: Matchups & Stacks for Week N" article on TFFB -- Sam's existing subscription, so this is not a new paid source (`docs/planning/PROMPT_GPS.md`). |
| `sos_qb` | `SoSQB` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_rb` | `SoSRB` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_wr` | `SoSWr` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_te` | `SoSTE` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `sos_dst` | `SoSDef` | - | One instance per position -- `position` set at construction rather than five near-identical hardcoded subclasses, same reasoning as `EntryTableConfig` covering both Bankroll buckets from one shape. |
| `nflverse_games` | `GamesRaw` | - | Per-game context (stadium, roof, surface, rest days, closing lines) from nflverse's free, unauthenticated `games.csv`. |
| `weather` | `WeatherRaw` | yes | Wind/precipitation/temperature for this week's outdoor games, from Open-Meteo -- free, no API key at all (unlike WeatherAPI.com, which docs/planning/archive/HANDOFF.md originally suggested). |
| `edge` | `EdgeRaw` | yes | Derived edge-layer signals (Leverage, CeilVal, GameEnv, ImpliedMove/TotMove/ SpdMove, Avail, Flag) computed locally from already-synced sources -- no network call of its own. |
