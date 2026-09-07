# The weekly workflow

The weekly grind, by phase rather than by tab -- each phase names which
tabs matter, the exact commands, what "done" looks like before moving on,
and what commonly goes wrong. For what a specific column or tab means,
see [SHEET_REFERENCE.md](SHEET_REFERENCE.md); for symptom-first
debugging, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Not sure what to run next? Just run `dfs` with no arguments -- it shows
where you are in the week (sheet, sync freshness, pool/lineup counts) and
the two or three commands that make sense right now, each one printed
next to its real name so you learn it as you use it.

## 1. Start the week

**Tabs:** none yet -- this points the CLI at a new sheet.

```
File > Make a copy of this template, name it for the week
dfs week new <url-of-the-copy>
```

`dfs week new` confirms before writing anything. It runs `dfs
doctor` against the new copy first (catches a stale/malformed template
before anything depends on it), rewrites `config.toml`'s `sheet_id`,
carries Bankroll and Results forward from the outgoing sheet, clears last
week's lineups, and runs a full `dfs sync`.

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
dfs sync --live       # gameday: odds/DK status/weather only, prints what changed
dfs edge              # top leverage plays, in the terminal, no browser needed
dfs go                # sync + dfs doctor + what changed, back to back
```

Check Board first each week -- it's a landing view: top-leverage, best
ceiling-value, and landmine panels plus a slate summary (games, highest
total, max wind, injury counts). Slate Grid is the same slate one row per
game instead of one row per player. EdgeRaw is where the real work
happens: sorted by Leverage descending, six colour scales on the
decision numbers, a muted position tint, banding to make one player's row
readable across 22 columns.

**Done looks like:** EdgeRaw has real data (not blank/zero rows), and
Board's slate summary matches what you'd expect for the week (right
number of games, no games missing from Slate Grid).

**Commonly goes wrong:** `dfs sync` failing partway -- it raises rather
than silently skipping a source, so a red `failed: ...` line in `dfs
status` means real, incomplete data, not a warning to ignore.

## 3. Build the pool

**Tabs:** EdgeRaw, Pool Picks, Player Pool.

Three ways to add a player, use any mix:

```
Tick the Pool checkbox in EdgeRaw (use the "Pool picking" filter view's
  Name column search to find one fast, rather than scrolling 743 rows)
Type a name into Pool Picks column A (live search against EdgeRaw's own
  Name column)
dfs pool add "name" "name2" ...   # exact match wins; ambiguous names are
                                    # printed as candidates, never guessed
```

`dfs pool list` shows everything currently ticked, by position. `dfs
pool remove "name"` and `dfs pool clear` (asks to confirm) take players
back out.

**Done looks like:** Player Pool's blocks show the players you meant to
add, at the position you expected, and the Overflow column (Z) is blank
for every block -- a non-blank Overflow means you're over that
position's cap and some picks are hidden, not dropped.

**Commonly goes wrong:** typing into Player Pool directly. It's fully
computed (and protected, warning-only) -- Player Pool's Source column
tells you whether a given row came from EdgeRaw or Pool Picks, which is
also the fastest way to find where to remove one.

## 4. Build lineups

**Tabs:** Lineups.

No commands -- this is done in the sheet. Rows 1-10 are the frozen pool
deck: set Position (B1), Sort by (D1), Start at (F1) to window into
Player Pool without leaving Lineups. Type names into column A starting
at row 12, one 9-player block per lineup (QB, RB, RB, WR, WR, WR, TE,
FLEX, DEF) -- the dropdown there is a typo guard, not just a search box.
Column O shows per-lineup guardrails (duplicate player, OUT/IR/Q, over
cap, incomplete).

**Done looks like:** every lineup block's column O reads OK, and the
salary-remaining row isn't negative.

**Commonly goes wrong:** a lineup that looked fine yesterday now shows
OUT/IR/Q in column O -- re-run `dfs sync` (or `dfs sync --live` on
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
dfs sync --live              # re-pull odds/DK status/weather, gameday only
dfs lineups late-swap         # who's still swappable, checked against real kickoffs
```

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
reset week to week) gets the same update.

**Done looks like:** Bankroll's running total moved by the amount you'd
expect from the week's actual results, and Results gained exactly one
new row for the week.
