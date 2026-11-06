# DFS Companion

A personal CLI that handles the data-plumbing side of a weekly DFS
routine: pulling projections/salaries/odds into a Google Sheet (the
source of truth for actually building lineups), exporting finished
lineups back out to DraftKings, and reconciling contest results into a
bankroll tracker.

Google Sheets stays where lineups get built. This tool exists so the data
feeding that sheet is never stale, mislabeled, or manually copy-pasted
from the wrong file.

## Quickstart

Requires Python 3.11+ (developed against 3.14). This is the full path
from nothing to a synced sheet -- every step below has actually been run,
in this order, against a real sheet.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium   # only needed once TFFB/DK browser auth is used
dfs --install-completion      # optional: tab-completion for every command below
```

**Google credentials**, before touching `config.toml`:

1. In the [Google Cloud Console](https://console.cloud.google.com), create
   a project and enable the Google Sheets API.
2. Create a service account, add a JSON key, and download it into the repo.
3. Open the key file, copy its `client_email`, and share your Google Sheet
   with that address (Editor access).

**Your sheet:** `File > Make a copy` of the
[weekly template](https://docs.google.com/spreadsheets/d/10si1m87aaaSLloZa-Sht5dD6ZlG6dS8RDWjSzdxkhLA/edit)
-- don't sync into a blank sheet, the structure has to already match.

**`config.toml`:**

```bash
cp config.example.toml config.toml
```

Fill in `google_sheets.sheet_id` (the ID segment from your sheet copy's
URL) and `google_sheets.credentials_file` (the key file's path from
above); the rest of `config.toml` has working defaults.

**Verify, then sync:**

```bash
dfs doctor   # confirms the sheet copy's structure matches what dfs expects
dfs sync     # pulls projections/salaries/odds into it
```

`dfs doctor` failing here almost always means the sheet wasn't copied
from the template (a blank sheet, or one missing a tab) -- fix that
before `sync`, not after. From here on, the weekly loop below is what
you'll actually run week to week.

## Not sure what to run?

Run `dfs` with no arguments. It shows a compact status (which sheet,
how stale the data is, pool/lineup counts) and then a numbered menu of the
whole standard week below -- always the same list, with the step that fits
right now marked `>`. Pick a number and it asks for whatever the command
needs (the new sheet's link, your contest CSV, a player name) instead of
erroring. "More, by topic" groups every other command (extras, bankroll
and logins, simulator and model, sheet building, repairs): type a topic's letter,
then a number. Each line shows the real command,
and typing the full command (`dfs sync --live`) still works. Leave a
required argument off a typed command (`dfs week close`) and it asks for it
there too -- you never have to remember them.

## The weekly loop

Once you've done the Quickstart above once, this is what every
subsequent week looks like -- `dfs week new` replaces the manual
`config.toml` edit from Quickstart, carrying your bankroll and Results
log forward from the sheet you're leaving:

**This is your standard week, and it is all most weeks need:**

```bash
dfs week new "<url-of-the-copy>"  # 1. new week: point config.toml at a fresh sheet copy, sync (quote the URL)
dfs sync                          # 2. re-run any time as lines/injuries/weather move
# 3. research, tick your pool and build lineups in the sheet (see WORKFLOW.md)
dfs entries fill                  # 4. DK's entries file -> DK Upload; you pick each entry's Lineup, run it
                                  #    again to fill the slots (`--write` makes the upload file itself)
dfs export -o lineups.csv         #    ...or validate + export DK's bulk-upload format from the slots
dfs sync --live                   # 5. gameday: fast-moving sources only, prints what changed (also refreshes
                                  #    the Edge Finder tab: CalPts, Hit3x%/Boom%, injury beneficiaries)
dfs lineups late-swap             #    ...and what to swap them for (best re-fill, 2-for-2, 1-for-1)
dfs week close                    # 6. after the games: reconcile Cash/GPP into Bankroll, keep your DraftKings
                                  #    downloads (Contests tab, Cash Line) AND score the week into Model Check
```

Everything else -- the other commands, one-time sheet setup, repairs -- is **reference**, for when you
need it: [docs/COMMANDS.md](docs/COMMANDS.md) lists every command and option. You do not need to read it to
run a normal week.

Every command exits non-zero on real failure -- nothing here silently
reports success when something failed.

`dfs week new` accepts either a full sheet URL (quote it -- the `?`/`#`
it contains gets glob-expanded or dropped as a comment by zsh and some
other shells if left bare) or just the bare sheet ID segment.

## Commands

**The standard week** (the six lines in "The weekly loop" above) is what you will use almost every time. The rest
are listed here so they are findable, not because you need to read them. Run `dfs --help` (or `dfs <command>
--help`) for any command, or see the complete generated reference in [docs/COMMANDS.md](docs/COMMANDS.md).

| Command | Reach for it when... |
|---|---|
| `dfs week new "<url>"` | starting a new week: it checks the new sheet, carries your bankroll and Results forward, and runs a full sync. |
| `dfs sync` [`--live`] | you want fresh data in the sheet; `--live` on gameday for odds/statuses/weather only, printing what changed (a plain sync and `--live` both refresh the **Cockpit** tab, Sunday's last 90 minutes on one read-only page with its inactives-and-pivots and practice blocks, on a sheet that has it: `dfs setup cockpit` creates it, template first). |
| `dfs setup player-card` | once per sheet (template first): creates the **Player** tab, one page per player (pick a name in B1); `dfs sync` then keeps its data fresh. |
| `dfs setup compare` | once per sheet (template first): creates the **Compare** tab, up to four players side by side (pick names in B1:E1). |
| `dfs export -o <file>` | your lineups are built and paired to DK entries, ready to upload. Entries with no lineup are left out. |
| `dfs entries fill` [`--write`, `--again`, `--dry-run`, `--csv <file>`] | pairing lineups to contests: lists DK's entries file (newest one in Downloads, found by its header) on `DK Upload`, **you** choose each entry's Lineup (L1..L20), it fills the slots (FLEX = latest kickoff, a started player never touched), prints checks, and `--write` makes `upload_<timestamp>.csv` with only the entries you set that DK does not already hold. A grey `Suggested` column is a hint by contest format; it never fills anything. `--again` after a late swap rewrites only the entries using the changed lineup. |
| `dfs entries print` [`--csv <file>`, `-o <file>`] | every lineup on the Lineups tab as `Name (ID)` in DK's slot order with CalPts, ProjPts and summed Own% in front, printed and written to a file; no pairing needed. |
| `dfs lineups suggest` | building lineups: complete builds from your pool on the Suggested tab (3 cash, and GPP builds in several shapes (a QB with one to three teammates of any position, with or without a bring-back; a stack holding a RB; a game stack; a stack with a RB and his DST; a contrarian stack) for every QB, two each when they fit, ranked by `--rank top1` (the default: the Top 1% chance) or `--rank share` (that chance divided by 1 + the identical entries expected); `--cash N`, `--gpp N`, `--seconds N`, `--gpp-entries N`, `--double-up-entries N`, `--rank top1|share`, `--no-write`, `--fixed-lines`). Cash % and Top 1% are measured against lines that move with the slate (`docs/SIM.md`, "Relative lines"). Copy one in with Lineup Tools > Copy builds to Lineups. `--iterate` reruns around what you decided on the tab (a decision cell per build: Keep / Rebuild / Drop / Vary N; a lock tick per player; names you type over a player count as locks; a rules box: Always use, Never use (`cash: Name` / `gpp: Name` for one kind of build), Max exposure, Stack rules, Builds wanted), about 15 s; every run is a round in `data/suggest_state/<week>.json` and `--round N` brings one back. `dfs setup suggested-controls` readies the tab (template first). `--portfolio` (off by default, not proven yet) also chooses which GPP build goes in which of your GPP entries as one set, from DK's entries file (`--entries-csv`, else the newest in Downloads) by `--portfolio-objective edollars|top1|robust` (`--portfolio-wide N` also lets it choose from N builder runs on noisy projections; a chosen lineup that is not a build is added as a new GPP build), prints the plan beside today's pick and saves it for `dfs entries fill`'s grey Suggested hint (`docs/SIM.md`, "Portfolio selection"). |
| `dfs stacks` [`--refit`, `--retro`, `--no-sheet`] | building around a stack: every QB's one- and two-teammate stacks ranked by simulated Top 1% chance against how much of the field plays them, on the read-only **Stacks** tab (with its retro track record on top); `dfs setup stacks` creates the tab. |
| `dfs backtest` [`--weeks 2,4,5,6`, `--settings <file.toml>`, `--compare <file.toml>`, `--fresh`] | you want to know whether a way of building would have made money: rebuilds past weeks with today's code from the last pre-lock snapshot, puts the builds into the contests you actually entered and scores them against those contests' real fields and payouts, with your own lineups beside them (offline, no sheet; about 6 minutes for four weeks; the report goes to `docs/research/backtest/`). Your real pool is saved at every `dfs sync` and `dfs lineups suggest` (`data/pools/`) so later weeks use what you picked. |
| `dfs lineups late-swap` | gameday: which rostered players are still swappable, and the best swaps for them within the salary cap, with each swap's change in P(cash) / P(GPP) (it also refreshes the Cockpit tab) (`--goal cash|gpp`, `--metric CalPts|ProjPts|AggPts`, default `[projection] default` = CalPts, `--all-players`, `--fixed-lines`, `--field auto|<file>` scores swaps against a live contest's real field from a standings export you downloaded; otherwise the games' points so far come from ESPN's box scores by default, no download, and `--state projected` ignores them). It never syncs. |
| `dfs week close` | the week is over: reconciles Cash/GPP into Bankroll and Results (newest history export in Downloads, or `--csv <file>`), keeps your DraftKings history and standings downloads, fills a blank Cash Line, rebuilds `Contests`, then scores the week's projections into `Model Check`, prints the contest plan check and the cash post-mortem (the double-up separators, which cash builds would have cashed, your cash lineup against its nearest build; kept in `data/contests/cash_postmortem.csv`). `--research` then runs Monday's offline studies unattended (under 45 minutes) and writes one report, `docs/research/weekly/<season>-wk<NN>-close.md`. |
| `dfs contests update` | just the contest part of the close: keep the downloads, learn from the standings, rebuild `Contests` (`--sheet-id`, `--no-sheet`). |
| `dfs contests dupes` [`--weeks 4,5,6`, `--dry-run`] | you want the expected-copies estimate refitted on your saved standings: `copies = scale x entries x ownership product ^ exponent` per format, each week scored on a fit without it, the held-out error printed; saves `data/model_cache/dup_calibration.json` for the Week page's consensus block. `dfs week close` reruns it. Files only, no sheet. |
| `dfs contests plan` | before you enter: this week's plan by format (Showdown and big-field N-max GPPs capped, the rest to double-ups, H2Hs and small single-entry GPPs, within the Bankroll tab's Weekly Budget; `[contest_plan]` sets the caps) and by tier (A / B / C dollars and what to look for; rules in `docs/CONTESTS.md`), last week against it, ROI and effective rake by format and field size, and the H2H regulars to avoid. Terminal only. `dfs week close` prints its one line ("spent $X vs plan $Y; over in: ..."). |
| `dfs entries record` [`--csv <file>`, `--from-sheet`] | at lock, after entering: records every lineup you entered, its contests and the Suggested build it came from (8+ of 9 players) or "own", from DK's entries file downloaded again after entering (else the Lineups tab), to `data/entered/<season>-wk<NN>.json`. `dfs week close` warns when a week has none, and its cash post-mortem compares your cash lineup to the nearest build. |

Also handy most weeks:

| Command | Reach for it when... |
|---|---|
| `dfs` (no arguments) | you are not sure what to run: it shows where you are in the week and what makes sense next. |
| `dfs week page` | you want the whole week on one page: where the week stands (Pool, Contest plan, Builds, Entries recorded, Late swap, Close, the next one highlighted), a short brief, your pool by position with its gaps, the week's decisions (30%+ double-up chalk, a Questionable star, the GPP stack), one board (each player once with his roles: Value, Cash core, Upside, Fade) beside a value map, a leverage map and the slate's game cards, and the **Explorer** (every priced player and every EdgeRaw column, by lens, with filters and a player drawer). The rest of the week is collapsed with a one-line summary each, ordered by the day (what changed, contest plan by tier, the **entry plan** (cash builds only in cash contests, GPP builds only in GPPs, entry limits, spend by tier, every entry has a lineup, and an Untested hint per contest), **bankroll health** (the bankroll by week, spend as a share of it, a stake guide by tier: your Weekly Budget, or your `[stakes]` rules when set), season-long, last week, cash, GPP, the **field's lineup and copies** (the most-owned legal lineup and the expected copies of each of yours, calibrated on your standings with the error shown; a GPP lineup at 5+ copies is flagged), the **Sunday inactives check** (every OUT / D / Q player in what you entered, three pivots each; first on Sunday), Sunday, your tools). Every Q / D tag carries the player's practice week (`DNP Wed · Limited Thu`). Read-only; rebuilt after `sync`, `lineups suggest`, `lineups late-swap`, `contests plan`, `season sync` / `check` and `week close`. |
| `dfs week serve` [`--week N`, `--port P`, `--no-open`, `--sheet-id <id>`] | you want the Week page as a local app: Claude Code docked beside it (approvals shown on the page), run buttons with live output for every command, a contest check form, a drop zone for DK's entries file (at lock) and history / standings (at close), and the page's own controls: Cash / GPP / Both / none on every player (the EdgeRaw Pool cell the sheet's dropdown writes), the decisions (Play / Fade writes `cash: Name` on Always use / Never use; the stack writes `more` items on Max exposure; a pivot puts him in the pool as Both; each can be undone), and "Add the suggested starting pool". Each write re-reads the cell first and refuses if the sheet changed, takes the run lock, is read back and is logged to `data/week/edits.log`; a closed week is read only. Local only (`127.0.0.1`, a random token in the link). Ctrl-C stops it. |
| `dfs status` / `dfs doctor` | which sheet am I on and how fresh is the data / has the sheet's structure drifted. |
| `dfs go` | `sync` + `doctor` + what-changed, back to back. |
| `dfs pool add\|remove\|list\|clear` | managing your pool without opening the sheet. |
| `dfs results update` | scoring the week's projections into `Model Check` on its own (`dfs week close` already does this; nflverse posts stats a day or two late). |
| `dfs edge` / `dfs odds movement` | a terminal look at the top leverage plays / how lines have moved. |
| `dfs odds snapshot` | saving the week's opening lines (run it Monday or Tuesday morning, before `dfs week new`). |

**Reference, only when needed:** `dfs setup other-sports` (once per sheet, template first: non-NFL DK contests get their own Bankroll ledger and Season columns, so Cash and GPP are NFL only), `dfs bankroll sync`, `dfs bankroll stakes` (the Bankroll tab's STAKES block: your ROI by format and this week's plan from your own `[stakes]` rules), `dfs ownership log`, `dfs lineups clear`, `dfs auth ...`
(one-time logins), `dfs setup ...` (building, styling and repairing a sheet; `dfs setup sheet` runs the whole
build in order), and `dfs --profile <command>` (a slow command's wall-clock and API-call breakdown). All in
[docs/COMMANDS.md](docs/COMMANDS.md).

**Season-long leagues** (Sleeper, on their own Season sheet): `dfs season sync`, `dfs season trades` (the trade
finder), `dfs season trade "Give A" "Get B"` (the analyzer), `dfs season waivers` (pickups and FAAB bids),
`dfs season lineups` and `dfs season check` (this week's lineups, the Sunday check), `dfs season outlook` (playoff
odds, power rankings, strength of schedule). Setup and method:
[docs/SEASON.md](docs/SEASON.md).

`dfs sheets ...` (the pre-reorganisation spelling of every `setup`
command, plus `doctor`) still works this season as a deprecated alias.

## Documentation

| Doc | Read it when |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | You need a command or option that is not in the standard week -- every `dfs` command, generated from the CLI itself. Reference, not required reading. |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | You want the weekly grind phase by phase, with exact commands and what "done" looks like. |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Something looks wrong and you want symptom -> cause -> fix. |
| [docs/SHEET_REFERENCE.md](docs/SHEET_REFERENCE.md) | You need to know what a specific tab or column means. |
| [docs/CALCULATIONS.md](docs/CALCULATIONS.md) | You want the exact formula behind a computed number. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | You're adding a data source or touching the live sheet's structure -- read this first. |

The sheet's own `Instructions` tab is the playbook: the week step by step, a plain line for every number and chip,
one line per tab, and where to find the docs above.

## Development

```bash
pytest              # offline; network sources are mocked, Sheets calls are faked
ruff check .
ruff format --check .
```
