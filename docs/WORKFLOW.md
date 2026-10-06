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
sheet's link, a contest CSV, a player name) rather than erroring; `m` lists
every other command. Each line is printed next to its real command name so
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
| 6 | after the games | `dfs week close --csv history.csv` (reconciles Bankroll **and** scores the week into Model Check) |

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
runs a full `dfs sync`.

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

**Tabs:** Board, Slate Grid, EdgeRaw, Movement (once the slate is live).

```
dfs sync              # re-run any time through the week as lines/injuries/weather move
dfs sync --live       # gameday: odds/DK status/TFFB projections+ownership/weather only, prints what changed
dfs edge              # top leverage plays, in the terminal, no browser needed
dfs go                # sync + dfs doctor + what changed, back to back
```

Check Board first each week -- it's a landing view, seven collapsible
sections: Queue (what changed since the last sync, pooled players only),
Slate shape (games ranked by total, with Fav/Spread/Pace/wind/shootout
flag), Per-position leaders (best ValAdj and highest ProjPts, ranked
within position), Punt finder (best value within $1,000 of each
position's own slate minimum), Stack candidates (QB + WR1/WR2/WR3/TE1/RB1
for the highest-total games), Pool diagnostics (reads your ticked pool,
not the slate), and a Chalk map placeholder (waiting on ownership data).
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

Pair each finished lineup to a real DK contest entry in DK Upload (Entry
ID, Contest Name, Contest ID, Entry Fee, then the roster-slot columns),
then run `dfs export` and upload the resulting file on DraftKings.

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

Movement (once populated by a sync) ranks players by how far their
team's implied total has moved since the week started -- a big shift is
worth a second look at anything you built around that number.

**Done looks like:** `dfs lineups late-swap` shows no unexpected locked
starters you meant to swap.

## 7. Reconcile

**Tabs:** Bankroll, Results.

```
dfs week close --csv <exported-dk-contest-history.csv>
```

Exports your DK contest history, classifies Cash vs GPP results, and
appends them to Bankroll -- never touching the summary figures or
anything outside its configured rows. Results (a season-long log, not
reset week to week) auto-fills `Week`/`Cash Pts`/`H2H Entered`/`H2H Win`
from the same export, sorted into NFL weeks by each entry's own contest
date -- a full-season export backfills every past week it has real data
for in one pass, not just the current one. `Cash Line` and the team-
colour columns stay yours to fill in by hand.

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
it is about ordering players), **ValAdj** quintiles (do the players it points at beat their salary), **Sources
compared** (TFFB vs Sleeper vs FantasyPros vs the average), **Salary multiple** (does a projected value of 3+
actually reach 3x salary), and **Flags**. Every row shows n; **a muted italic row is "thin" (n under 30): read it
as "not enough data yet".** Hover any header for its definition.

Each projection is the last TFFB snapshot before that player's own kickoff, and `ValAdj` and flags are recomputed
from the archived raw data with today's code, so old weeks are judged by today's rules. It reports; it never
changes a threshold or a flag -- that is your decision after several weeks. The definitions behind every
number are in [CALCULATIONS.md](CALCULATIONS.md).

**Done looks like:** the status line shows the weeks scored and a high "joined" percentage; players it could not
find are listed in `data/results/unmatched_<season>_wNN.csv` (mostly backups who never recorded a stat).

