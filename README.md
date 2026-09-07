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
reports success when something failed. Run `dfs --help` (or `dfs
<command> --help`) for the full command reference; `dfs status` shows
config/credential/data-freshness at a glance and prints which sheet
you're actually pointed at, since a new one gets copied every week.

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
