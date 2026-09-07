# DFS Companion

A personal CLI that handles the data-plumbing side of a weekly DFS
routine: pulling projections/salaries/odds into a Google Sheet (the
source of truth for actually building lineups), exporting finished
lineups back out to DraftKings, and reconciling contest results into a
bankroll tracker.

Google Sheets stays where lineups get built. This tool exists so the data
feeding that sheet is never stale, mislabeled, or manually copy-pasted
from the wrong file.

## Setup

Requires Python 3.11+ (developed against 3.14).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium   # only needed once TFFB/DK browser auth is used
dfs --install-completion      # optional: tab-completion for every command below
```

Copy the config template and fill in your own sheet:

```bash
cp config.example.toml config.toml
```

This project expects a Google Sheet shaped like the
[weekly template](https://docs.google.com/spreadsheets/d/10si1m87aaaSLloZa-Sht5dD6ZlG6dS8RDWjSzdxkhLA/edit)
-- `File > Make a copy` it, don't sync into a blank sheet. Fill in
`config.toml`'s `google_sheets.sheet_id` (the ID segment from your
sheet's URL) and `google_sheets.credentials_file` (a Google
service-account key -- see below); the rest of `config.toml` has
working defaults.

### Google service account

1. In the [Google Cloud Console](https://console.cloud.google.com), create
   a project and enable the Google Sheets API.
2. Create a service account, add a JSON key, and download it into the repo.
3. Open the key file, copy its `client_email`, and share your Google Sheet
   with that address (Editor access).
4. Point `credentials_file` in `config.toml` at the key file's path.

## Not sure what to run?

Run `dfs` with no arguments. It shows a compact status (which sheet,
how stale the data is, pool/lineup counts) and the two or three commands
that make sense right now, each printed next to its real name -- the
point is to make itself unnecessary once you know the commands below.

## The weekly loop

```bash
dfs week new <url-of-the-copy>   # new week: point config.toml at a fresh sheet copy, sync
dfs sync                         # re-run any time as lines/injuries/weather move
# build the pool and lineups in the sheet (three ways in -- see WORKFLOW.md)
dfs export -o lineups.csv        # validate + export DK's bulk-upload format
dfs sync --live                  # gameday: fast-moving sources only, prints what changed
dfs week close --csv history.csv # end of week: reconcile Cash/GPP into Bankroll
```

Every command exits non-zero on real failure -- nothing here silently
reports success when something failed.

## Commands

Weekly-loop commands first, one-time setup last -- run `dfs --help` (or
`dfs <command> --help`) for the full reference.

| Command | Reach for it when... |
|---|---|
| `dfs status` | you want to know which sheet you're pointed at and how fresh each source is. |
| `dfs sync` [`--live`] | you want fresh data in the sheet; `--live` on gameday for odds/statuses/weather only, printing what changed. |
| `dfs doctor` | you want to confirm the sheet's structure hasn't drifted -- run it after any structural edit, or when something looks wrong. |
| `dfs edge` | a quick look at top leverage plays in the terminal, no sheet needed. |
| `dfs go` | `sync` + `doctor` + what-changed, back to back -- the three you'd otherwise run in sequence anyway. |
| `dfs export -o <file>` | your lineups are built and paired to DK entries, ready to upload. |
| `dfs pool add\|remove\|list\|clear` | adding/removing players from your pool without opening the sheet. |
| `dfs lineups late-swap\|clear` | checking which rostered players are still swappable; clearing last week's picks on a new sheet copy. |
| `dfs odds movement` | checking how betting lines have moved since your last sync. |
| `dfs bankroll sync --csv <file>` | reconciling DK contest history into your bankroll tab. |
| `dfs week new <url>` / `dfs week close --csv <file>` | starting a new week's sheet, or closing out the one you're on. |
| `dfs auth tffb\|dk` | one-time interactive login for a source that needs a real browser session. |
| `dfs setup ...` | one-time sheet construction (pool deck, EdgeRaw linking, styling, protection, ...) -- see `dfs setup --help`; `dfs setup sheet` runs the whole thing in order. |

`dfs sheets ...` (the pre-reorganisation spelling of every `setup`
command, plus `doctor`) still works this season as a deprecated alias.

## Documentation

| Doc | Read it when |
|---|---|
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
