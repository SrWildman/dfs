# The weekly workflow

The weekly grind, by phase rather than by tab -- each phase names which
tabs matter, the exact commands, what "done" looks like before moving on,
and what commonly goes wrong. For what a specific column or tab means,
see [SHEET_REFERENCE.md](SHEET_REFERENCE.md); for symptom-first
debugging, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Not sure what to run next? Just run `dfs` with no arguments -- it shows
where you are in the week (sheet, sync freshness, pool/lineup counts) and
a numbered menu of the standard week, the step that fits right now marked
`>`. Pick a number and it asks for anything the command needs (the new
sheet's link, a contest CSV, a player name) rather than erroring. Below it, "More, by topic" groups every
other command (extras, bankroll and logins, simulator and model, sheet building,
repairs): type a topic's letter, then a number. Each line is printed next to its real command name so
you learn it as you use it. Typing the full command still works, and a
typed command with a required argument left off asks for it.

## The standard week at a glance

This is what most weeks are -- **sections 1 to 7 below are the standard week**; everything else in this repo
(one-time sheet setup, repairs, the other commands) is reference you open only when you need it.

| # | When | Run |
|---|---|---|
| 1 | a new week starts | `dfs week new "<url-of-the-copy>"` |
| 2 | any time as lines/injuries move | `dfs sync` |
| 3 | research, pool, lineups | in the sheet (no command) |
| 4 | lineups are built | `dfs export -o lineups.csv` |
| 5 | gameday | `dfs sync --live`, then `dfs lineups late-swap` |
| 6 | after the games | `dfs week close` (reconciles Bankroll, keeps your DraftKings downloads and rebuilds Contests, **and** scores the week into Model Check) |

Sections 8 (log ownership) and 9 (read Model Check) are optional extras. The full command list is in
[COMMANDS.md](COMMANDS.md).

## 1. Start the week

**Tabs:** none yet -- this points the CLI at a new sheet.

```
File > Make a copy of this template
dfs week new "<url-of-the-copy>"
```

No need to rename the copy yourself -- `dfs week new` titles it "Week
<n>" for you (from the current NFL week; pass `--week N` to override).
It confirms before writing anything, including the title it's about to
set. It also runs `dfs doctor` against the new copy first (catches a
stale/malformed template before anything depends on it, and now checks
the title itself parses -- see below), rewrites `config.toml`'s
`sheet_id`, carries Bankroll and Results forward from the outgoing sheet,
clears last week's lineups, blanks every synced tab AND every local
synced-data cache (so a source that fails on the first sync reads as
no-data-yet rather than a previous week's now-mismatched numbers), and
runs a full `dfs sync`. It then prints which saved odds snapshot the week's line movement is measured against, e.g. "Opening lines: Tue 9:30 am ET snapshot (after the previous week's last game)"
(or says it is using the current lines when none is older).

**The opening lines.** Movement (`ImpliedMove`, `TotMove`, `SpdMove`, the Movement tab) is measured against the week's *opening* lines: the earliest saved `nfl_odds` snapshot that shows the new
week's slate, not whenever `dfs week new` happened to run (`store.opening_snapshot`). The odds source only ever shows the current slate, so the opening is on record only if the lines were saved
before the sheet was copied. `dfs odds snapshot` saves them (no sheet, no other source): run it on Monday or Tuesday morning, before `dfs week new`. There is no scheduled job; the Mac is not always on.

If the copy is somehow already titled `Week <n>` and `<n>` disagrees with
what `dfs week new` derives, it stops and asks rather than overwriting a
title that might have been deliberate -- pass `--week` to say which one
is right.

**Quote the URL.** A Google Sheets URL contains `?` and `#`, which zsh
(and some other shells) treat as glob/history characters rather than
plain text -- pasting one unquoted fails with something like `zsh: no
matches found: ...`. Quoting it (as above) always works. A bare sheet ID
(just the id segment, no URL at all) works too, quoted or not.

**Done looks like:** `dfs status` shows the new week's sheet title and
URL, and every source in the table reads "ok", not "never synced" or
"failed: ...".

**Commonly goes wrong:** pointing this at the wrong sheet. Always check
the title/URL a command prints (or run `dfs status`) before trusting
`config.toml` -- `sheet_id` is an opaque string, and a new one gets
copied every week.

## 2. Research

**Tabs:** Board, Edge Finder, Slate Grid, EdgeRaw, Movement (once the slate is live).

```
dfs sync              # re-run any time through the week as lines/injuries/weather move
dfs sync --live       # gameday: odds/DK status/TFFB projections+ownership/weather only, prints what changed
dfs edge              # top leverage plays, in the terminal, no browser needed
dfs go                # sync + dfs doctor + what changed, back to back
```

Every sync also rewrites the **Edge Finder** tab (cash core and GPP upside per position with a reason and a verb on every row, punt plays, where
`CalPts` disagrees with TFFB, who inherits an out back's carries, every absent regular as context, usage trends, matchups (context only), and
the unproven context signals; your open/closed sections survive the rewrite). The final `dfs sync --live` after inactives (about 90 minutes before kickoff) is the one that
matters for the injury list. `CalPts` is **not** the default projection: it is tracked in Model Check.

Check Board first each week -- it's a landing view, five collapsible
sections: Slate shape (games ranked by total, with Fav/Spread/Pace/wind/shootout
flag), Queue (what changed since the last sync, pooled players only; one line when
nothing did), Pool check (pooled players whose numbers went bad, with a Set cell to
remove them), Pool summary (your pool right now: counts, what is missing to fill a
lineup, the portfolio odds, each pooled QB's stack, salary spread) and Stack
candidates (QB + WR1/WR2/WR3/TE1/RB1 for the highest-total games).
Slate Grid is the same slate one row per game instead of one row per
player, with each game's combined environment (GameEnv, Pace, PROE, Expl%) and, below the games, a TEAMS table: every team on
the schedule by implied total, with its pace, pass rate over expected, explosive rate, offensive EPA per
play/pass/rush, and the EPA the OPPONENT's defense allows (a high number is a soft defense, so green).
EdgeRaw, Player Pool and Lineups also carry five usage columns in the collapsed USAGE group beside Snap% --
Tgt%, WOPR, Rush%, RZ/G and HVT/G -- each over a player's last 3 games played and coloured against his own
position; hover any header for its definition. These are for you to read next to the projection, not inputs to it. EdgeRaw is where the real work happens: sorted by ValAdj
descending, colour scales on every decision number, a muted position
tint, banding to make one player's row readable across 23 columns, and a
visible sort/search arrow in every header cell (click one to sort by any
column, or search it -- Data > Filter views holds the saved presets).

### Props and ESPN: two shadow projections, and the credit plan

`dfs sync` also pulls two independent projections that are **tracked, not trusted yet**: ESPN's own (`EspnPts`, free, every sync) and
Vegas player props from The Odds API (`PropPts`, **500 credits a month**). Both appear on EdgeRaw beside `CalPts` and are scored in Model
Check's SHADOW SOURCES block from their first week; neither enters `CalPts` until it has 3 scored weeks.

A call costs one credit per market that returns data per game, in one US region (every book in it; naming fewer books does not
save credits). We pull six markets for the unstarted games on the DraftKings slate, so a full pull is about **6 credits a game, ~78**. The plan:

| When | Command | Credits |
|---|---|---|
| Saturday or Sunday | the **first** full `dfs sync` of the NFL week pulls props on its own: **one automatic pull per week**, later full syncs that week reuse it | ~78 |
| Sunday, game day | `dfs sync --live --props` refreshes **only games where a player's DraftKings status changed** since the last pull (a quoted player or a $5,000+ starter went Q/OUT/IR or came back); if none changed it says so and spends nothing | ~6 per changed game |
| Any other day | a full `dfs sync` leaves props alone and says so; `dfs sync --props` forces a pull | 0 / ~78 |
| Gameday final sync | plain `dfs sync --live` **never** spends credits | 0 |

**Budget guard.** Before any pull, the credits left divided by the NFL weekends before the monthly reset must cover that pull's worst case
(6 credits x games), otherwise the pull is skipped with a sentence saying so (it is a skip, not a failure, and nothing is spent). The reset day is
`[odds_api] reset_day` in `config.toml` (your account page on the-odds-api.com; yours is the 1st). Expected use: one ~78-credit pull a week
(~335 a month) plus a few refreshes, **about 400-450 a month**, and the guard keeps it under 500.

Props are not all posted on a Tuesday (a Tuesday pull costs credits and prices only a few players), which is
why the main pull is the weekend. Every pull logs `x-requests-remaining` (`data/raw/odds_props/credits.csv`), and
`dfs doctor` warns under 100. The raw responses are kept in `data/raw/odds_props/` and the key lives only in
`config.toml` (`[odds_api] key`, never committed). `dfs week new` never pulls props.

**Done looks like:** EdgeRaw has real data (not blank/zero rows), and
Board's slate summary matches what you'd expect for the week (right
number of games, no games missing from Slate Grid).

**Commonly goes wrong:** `dfs sync` failing partway -- it raises rather
than silently skipping a source, so a red `failed: ...` line in `dfs
status` means real, incomplete data, not a warning to ignore.

## 3. Build the pool

**Tabs:** EdgeRaw, Player Pool.

Three ways to add a player, use any mix:

```
Set the Pool dropdown in EdgeRaw to Cash/GPP/Both (use its header arrow
  to sort/search, or the "Pool picking" filter view, rather than
  scrolling 743 rows)
Type a name into Player Pool's own row 1 search box (live search against
  EdgeRaw's own Name column -- row 2 is the real header, blocks start below it)
dfs pool add "name" "name2" ...   # exact match wins; ambiguous names are
                                    # printed as candidates, never guessed
```

`dfs pool list` shows everything currently ticked, by position. `dfs
pool remove "name"` and `dfs pool clear` (asks to confirm) take players
back out.

**Done looks like:** Player Pool's blocks show the players you meant to
add, at the position you expected, and the Overflow column (far right)
is blank for every block -- a non-blank Overflow means you're over that
position's cap and some picks are hidden, not dropped.

**Commonly goes wrong:** typing into Player Pool directly, anywhere other
than row 1's search box. Every other cell is fully computed (and
protected, warning-only) -- its "Edge ↗" column jumps straight to that
player's row on EdgeRaw, the fastest way to find and remove one (set
their Pool dropdown back to blank).

## 4. Build lineups

**Tabs:** Lineups.

No commands -- this is done in the sheet. Type names into column A
starting at row 2, one 9-player block per lineup (QB, RB, RB, WR, WR,
WR, TE, FLEX, DST) -- the dropdown there is a typo guard, not just a
search box.
The `Issues` column shows per-lineup guardrails (duplicate player,
OUT/IR/Q, over cap, incomplete).

**Done looks like:** every lineup block's `Issues` column reads OK, and
the salary-remaining row isn't negative.

**Commonly goes wrong:** a lineup that looked fine yesterday now shows
OUT/IR/Q in `Issues` -- re-run `dfs sync` (or `dfs sync --live` on
gameday) to refresh Avail flags before finalizing.

## 5. Enter

**Tabs:** DK Upload.

```
dfs export -o lineups.csv
```

Before that, if you want a starting point: `dfs lineups suggest` writes complete builds from your pool to the **Suggested** tab (3 cash, and GPP builds with a stack for every QB, two each when they fit, each at least 3 players
apart, FLEX on the latest kickoff), and **Lineup Tools > Copy build to Lineups** drops one into the first empty lineup. It never touches a lineup that has names.

Pair each finished lineup to a real DK contest entry in DK Upload (Entry
ID, Contest Name, Contest ID, Entry Fee, then the roster-slot columns),
then run `dfs export` and upload the resulting file on DraftKings.

`dfs export` also applies the **FLEX rule** before it writes the file: the FLEX slot holds whichever of its position's players kicks
off last (RB beyond 2, WR beyond 3 or TE beyond 1), so it stays open to a swap the longest. It swaps two names in the exported rows
only (the sheet is not touched) and prints what it moved ("Moved Puka Nacua to FLEX (plays SNF), Cooper Kupp to WR"). A player whose
game has started is never moved, and a tie or a missing kickoff leaves the lineup as it is. In the sheet, the Lineups `Issues`
column shows the same advice on the FLEX row, and **Lineup Tools > Fix FLEX order** applies it to every lineup (it asks first).

**Done looks like:** the exported CSV's row count matches the number of
lineups you actually paired, and DraftKings accepts the upload without a
validation error.

## 6. Monitor

**Tabs:** Lineups, Movement.

```
dfs sync --live              # re-pull odds/DK status/TFFB projections/weather, gameday only
dfs lineups late-swap         # who's still swappable, and the best swaps for them
```

Kickoff times are read as Eastern time (TFFB's `GameStart` is Eastern wall-clock time, not UTC); a player locks at
his own game's kickoff, and one whose game starts later still shows as swappable.

For each lineup with an open slot, `late-swap` then suggests, in this order: **(a)** the best full re-fill of all
the open slots together, **(b)** the best 2-for-2 swaps (an expensive RB and a cheap TE out, a mid RB and a better
TE in), **(c)** the best 1-for-1 swaps per open slot. Every suggestion stays under the $50,000 cap (the locked
players' salaries count), respects the roster slots (FLEX takes an RB, WR or TE), never puts a DST against your
own QB or a second RB in one game, and never moves a locked player; it shows who goes out and in, the points
gained, the salary left and a note when it adds or breaks a stack, bring-back or DST/RB same-team pair. Ranked by
`ProjPts` (`--metric AggPts` for the other), never Leverage. Candidates are the players in your Player Pool whose
game has not started; `--all-players` also searches the rosterable pool. If nothing beats the lineup it says so.

Each swap also shows what it does to the lineup's chances, from the lineup simulator (`docs/SIM.md`): `P(cash)` and `P(GPP)`
are the change in percentage points, beside the projection gain. `--goal cash` (the default) ranks the swaps by the change
in P(cash), `--goal gpp` by P(GPP), projection gain breaking ties; the search keeps four times as many candidates as it
shows, so a swap that wins on probability but not on projection can surface. The lines move with the slate: P(cash) is
measured against the double-up cash line (the field's average score plus a small margin, from projected ownership) and
P(GPP) against the top-1% line (the field's average plus about 63 points; `docs/SIM.md`, "Relative lines"). Before
ownership publishes, or with `--fixed-lines`, they are the old fixed lines (the median of every typed `Cash Line` in
Results this season and `[sim] gpp_target`, default 190); the top of the output says which.

After `dfs sync`, the **Lineups** tab also shows `Typical`, `Big night`, `Cash %` and `190+ %` (the chance of the top-1% line;
the label is still `190+ %` until the sheet is relabelled `Top 1%`) on every built lineup's Total row (set its Cash/GPP marker in column A) and a
portfolio line (first lineup's `Remaining` row): the chance at least one lineup cashes, the expected number that cash and
the chance at least one reaches the target.

Movement (once populated by a sync) lists each team by how far its
implied total has moved since the week started, with kickoff in Eastern time,
its top players and a sentence on what the move means -- a big shift is
worth a second look at anything you built around that number.

**Done looks like:** `dfs lineups late-swap` shows no unexpected locked
starters you meant to swap.

## 7. Reconcile

**Tabs:** Bankroll, Results.

Before you run it, in DraftKings: **My Contests > History > export** (the contest-entry history), and on each contest's results page
**export full standings** for the contests you want to learn from. **DraftKings only serves a contest's standings for about two
weeks, then sends an empty file**, so grab them on Monday or Tuesday; they cannot be backfilled.

```
dfs week close
```

With no `--csv` it uses the newest history export in your Downloads folder (found by its header row, `[contests] downloads_dir` changes
the folder) and says which file. `--csv <file>` still works.

Exports your DK contest history, classifies Cash vs GPP results, and
appends them to Bankroll -- never touching the summary figures or
anything outside its configured rows. Results (a season-long log, not
reset week to week) auto-fills `Week`/`Cash Pts`/`H2H Entered`/`H2H Win`
from the same export, sorted into NFL weeks by each entry's own contest
date -- a full-season export backfills every past week it has real data
for in one pass, not just the current one. `Cash Line` and the team-
colour columns stay yours to fill in by hand.

**Then it learns from your contests** (`docs/CONTESTS.md`). It keeps a copy of the history export in `data/raw/dk_history/`, merges it
into the durable log `data/contests/contest_log.csv`, moves new standings files out of Downloads into `data/raw/dk_standings/` (an
empty file is reported and left), reads them (score profiles, how the fields build, the actual ownership into `data/ownership_log.csv`),
fills a **blank** Results `Cash Line` from the GIANT $25 Double Up's standings (a value you typed is never changed) and rebuilds the
**Contests** tab. It never fails the close. `dfs contests update` does just this part.

**Then it scores the week's projections.** The close finishes by running `dfs results update`, which compares
every projection with what actually happened and rebuilds the **Model Check** tab (section 9). nflverse posts
a week's stats a day or two after the games, so if you close early it prints "stats not published yet" and carries on --
run `dfs results update` later and the tab fills in. It never fails the close.

**Done looks like:** Bankroll's running total moved by the amount you'd
expect from the week's actual results, and Results' row for the week you
just closed shows real `Cash Pts`/`H2H Entered`/`H2H Win` numbers.

## 8. Log actual ownership (optional -- a standalone record, not a calibration pipeline)

**Files:** `data/ownership_log.csv` (local, gitignored -- never leaves
your machine).

For each contest you want in the log, download that contest's own
"export full standings" CSV from its results page on DraftKings (a
per-contest file, `contest-standings-<id>.csv` -- not the account-level
contest-history export Reconcile uses above), then:

```
dfs ownership log --csv <contest-standings-file.csv>
```

Logs every player's real ownership % in that contest (summed across any
roster-slot split DK's export tracks separately, e.g. a player used at
both `RB` and `FLEX`), keyed by contest so re-running the same file is
safe. Pass `--week N` for anything other than the current week -- this
export has no date of its own, unlike the contest-history one. This is
file-based on purpose (an automated per-contest fetch was investigated
and deliberately not built -- see `ownership.py`'s module docstring);
there's no requirement to log every contest. **The ProjOwn-vs-actual
calibration this was originally meant to build toward is dropped** (Week
3 follow-ups, Item 3, 2026-09-23 -- see `docs/planning/PROMPT_DATA.md`'s
7.8 entry and `docs/planning/ROADMAP.md`'s "Deliberately not doing"
section): it needed a hand-logging volume Sam isn't going to do without
automation, and the automated path stays off over real account risk. Log
a contest here if you're curious about it on its own terms; nothing
downstream is waiting on it.

**Done looks like:** the command reports how many players it logged and
how big the contest's field was; nothing on the sheet changes.

## 9. Read the Model Check tab (optional -- it builds itself)

**Tab:** Model Check (after Results). **Command:** none needed -- `dfs week close` rebuilds it; `dfs results update
[--week N] [--all]` rebuilds it on its own.

It scores every projection against what actually happened, for every completed week so far, and is rebuilt from
`data/results/` each time (nothing on it is typed). Read it top to bottom: **Ceiling** (how often a player beat
his published ceiling -- near 15% means it is roughly an 85th percentile), **Projection accuracy** (bias:
negative means the projections ran high; MAE; calibration slope; Spearman, which matters most for DFS because
it is about ordering players), the **Projection Race** at the top (TFFB, AggPts, the calibrated `CalPts` and UM as its own row, judged on weeks they never trained on -- this is the table to read before deciding whether `CalPts` should be your default projection), **Reliability** (do `Hit3x%`, `Boom%` and `Bust%` come true, by decile), **ValAdj** quintiles (do the players it points at beat their salary), **Sources
compared** (TFFB vs Sleeper vs FantasyPros vs the average), **Salary multiple** (does a projected value of 3+
actually reach 3x salary), **Flags**, and **Signals** (`FADE↓`, `USAGE↑/↓`, `INJ+`, the matchup groups and an unflagged baseline per position, each with n, the mean miss and a hit rate). Every row shows n; **a muted italic row is "thin" (n under 30): read it
as "not enough data yet".** Hover any header for its definition.

Each projection is the last TFFB snapshot before that player's own kickoff, and `ValAdj` and flags are recomputed
from the archived raw data with today's code, so old weeks are judged by today's rules. It reports; it never
changes a threshold or a flag -- that is your decision after several weeks. The definitions behind every
number are in [CALCULATIONS.md](CALCULATIONS.md).

**Done looks like:** the status line shows the weeks scored and a high "joined" percentage; players it could not
find are listed in `data/results/unmatched_<season>_wNN.csv` (mostly backups who never recorded a stat).

