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
erroring; `m` lists every other command. Each line shows the real command,
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
dfs export -o lineups.csv         # 4. validate + export DK's bulk-upload format
dfs sync --live                   # 5. gameday: fast-moving sources only, prints what changed
dfs lineups late-swap             #    ...and what to swap them for (best re-fill, 2-for-2, 1-for-1)
dfs week close --csv history.csv  # 6. after the games: reconcile Cash/GPP into Bankroll AND score the
                                  #    week's projections into the Model Check tab
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
| `dfs sync` [`--live`] | you want fresh data in the sheet; `--live` on gameday for odds/statuses/weather only, printing what changed. |
| `dfs export -o <file>` | your lineups are built and paired to DK entries, ready to upload. |
| `dfs lineups late-swap` | gameday: which rostered players are still swappable, and the best swaps for them within the salary cap (`--metric AggPts`, `--all-players`). |
| `dfs week close --csv <file>` | the week is over: reconciles Cash/GPP into Bankroll and Results, then scores the week's projections into `Model Check`. |

Also handy most weeks:

| Command | Reach for it when... |
|---|---|
| `dfs` (no arguments) | you are not sure what to run: it shows where you are in the week and what makes sense next. |
| `dfs status` / `dfs doctor` | which sheet am I on and how fresh is the data / has the sheet's structure drifted. |
| `dfs go` | `sync` + `doctor` + what-changed, back to back. |
| `dfs pool add\|remove\|list\|clear` | managing your pool without opening the sheet. |
| `dfs results update` | scoring the week's projections into `Model Check` on its own (`dfs week close` already does this; nflverse posts stats a day or two late). |
| `dfs edge` / `dfs odds movement` | a terminal look at the top leverage plays / how lines have moved. |

**Reference, only when needed:** `dfs bankroll sync`, `dfs ownership log`, `dfs lineups clear`, `dfs auth ...`
(one-time logins), `dfs setup ...` (building, styling and repairing a sheet; `dfs setup sheet` runs the whole
build in order), and `dfs --profile <command>` (a slow command's wall-clock and API-call breakdown). All in
[docs/COMMANDS.md](docs/COMMANDS.md).

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

The sheet's own `Instructions` tab has a one-line-per-tab summary and
links back to all of the above.

## Development

```bash
pytest              # offline; network sources are mocked, Sheets calls are faked
ruff check .
ruff format --check .
```
