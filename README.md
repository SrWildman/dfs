# DFS Companion

A personal CLI that handles the data-plumbing side of a weekly DFS
routine: pulling projections/salaries/odds into a Google Sheet (the
source of truth for actually building lineups), exporting finished
lineups back out to DraftKings, and reconciling contest results into a
bankroll tracker.

Google Sheets stays where lineups get built. This tool exists so the data
feeding that sheet is never stale, mislabeled, or manually copy-pasted
from the wrong file.

## Status

| Area | State |
|---|---|
| DraftKings salaries | Working -- live, unauthenticated API |
| NFL odds (Rotowire) | Working -- live, unauthenticated API |
| TFFB projections (`ProjPts`/`ProjOwn`/Ceiling/Vegas context) | Working -- authenticated capture of the DFS Pass optimizer's own API (`dfs auth tffb` once) |
| Lineup export & validation | Working, against a manually-paired entries tab |
| Weekly sheet reset (`dfs lineups clear`) | Working -- clears last week's typed lineups/picks, formulas and formatting untouched |
| Bankroll sync (Cash/GPP) | Working, from a manually-exported DK CSV |
| Strength of Schedule | Not yet ported -- see `legacy/README.md` |
| Player ownership % (field consensus, not TFFB's own) | Not started -- see `docs/HANDOFF.md` |
| Live DK contest history / entries (no manual export) | Not yet built -- needs `dfs auth dk` exercised first |

See `docs/HANDOFF.md` for session history, current state, and what's next.

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
[weekly template](https://docs.google.com/spreadsheets/d/1ZSjMaRKRAXS-DmfOFePKaq_KemghmNQHsASSjttG97I/edit)
(tabs like `TFFBOptoRaw`, `PlayerPoolRaw`, `DkSalClean`, `SoS*`, etc., plus
the formulas that tie them together) -- `File > Make a copy` it, don't sync
into a blank sheet. In practice a new copy gets made every week (bankroll
carryover between weeks isn't automated yet -- copy last week's numbers
over by hand for now).

`config.toml` is gitignored -- it holds your sheet ID and credentials
filename, neither of which belong in version control. Edit it with:

- `google_sheets.sheet_id` -- the ID segment from your sheet's URL
- `google_sheets.credentials_file` -- path to a Google service-account key
  (see below)
- `google_sheets.tab_mappings` -- which tab each data source writes to
- `lineups.upload_tab` -- the tab holding your paired DK entries (see
  "Export lineups" below)
- `bankroll.cash` / `bankroll.gpp` -- row ranges of your bankroll ledger
  tables, if you have them (see "Bankroll" below)

### Google service account

1. In the [Google Cloud Console](https://console.cloud.google.com), create
   a project and enable the Google Sheets API.
2. Create a service account, add a JSON key, and download it into the repo.
3. Open the key file, copy its `client_email`, and share your Google Sheet
   with that address (Editor access).
4. Point `credentials_file` in `config.toml` at the key file's path.

## Commands

```bash
dfs status                        # config/credentials/data freshness at a glance
dfs sheets inspect                 # list every tab in your sheet, with headers

dfs sync                           # fetch all sources, upload to Sheets
dfs sync --only draftkings,nfl_odds
dfs sync --no-upload               # fetch and store locally, skip Sheets
dfs sync --week 3 --season 2026    # override auto-detected week/season

dfs export -o lineups.csv          # validate + export DK bulk-upload CSV

dfs lineups clear                  # wipe last week's typed lineups/picks (new sheet copy)

dfs auth dk                        # one-time interactive DraftKings login
dfs auth tffb                      # one-time interactive TFFB login

dfs bankroll sync --csv history.csv   # classify + append DK contest history
```

Every command exits non-zero on real failure -- nothing here silently
reports success when something failed.

Since a new sheet gets copied every week, `dfs status`, `dfs sheets inspect`,
and `dfs sync` (whenever it's actually about to write) all print the
connected sheet's real title and URL before doing anything else -- a quick
"is this actually this week's sheet, not last week's" check, since
`config.toml`'s `sheet_id` is otherwise just an opaque ID you can't eyeball.

## Weekly workflow

1. **New week**: duplicate the [weekly template](https://docs.google.com/spreadsheets/d/1ZSjMaRKRAXS-DmfOFePKaq_KemghmNQHsASSjttG97I/edit),
   point `config.toml`'s `sheet_id` at the copy, then `dfs lineups clear`
   to wipe last week's typed lineups/picks before rebuilding.
2. **Sync everything**: `dfs sync` (salaries, odds, and TFFB projections --
   the last needs `dfs auth tffb` done at least once). Re-run
   `dfs sync --only draftkings,nfl_odds` multiple times through the week as
   lines move.
3. **Build lineups** in the sheet, as always.
4. **Pair lineups to contest entries** in your DK-upload tab (this stays a
   manual step -- see below), then `dfs export -o lineups.csv` and upload
   that file to DraftKings.
5. **Watch/adjust** through the week; re-sync and re-export as needed.
6. **End of week**: export your contest history from DraftKings and run
   `dfs bankroll sync --csv <file>` to reconcile Cash and GPP results.

## Export lineups

`dfs export` reads whatever's in `lineups.upload_tab` (DraftKings' own
"bulk edit entries" layout: Entry ID, Contest Name, Contest ID, Entry Fee,
then the 9 roster slot columns) and validates each row against your most
recently synced salary data:

- every player exists in the current slate (catches referencing a stale
  or wrong week's IDs)
- no player used twice in a lineup
- each slot's position is legal (FLEX allows RB/WR/TE)
- total salary is under `lineups.salary_cap` (default 50000)

Rows with no errors are written to the output CSV; everything else is
reported with a specific reason instead of failing silently. Pairing
lineups to specific entries is a manual step in the sheet -- this tool
validates and exports what's already there.

## Bankroll sync

Classifies each contest entry as Cash or GPP by payout shape (roughly
half the field paid, or a straight head-to-head, counts as Cash;
everything else is GPP) and appends new rows into your existing bankroll
ledger tables -- never touching cells outside the configured row range,
so any formulas already in the sheet (e.g. per-row "% Paid") are left
alone. Re-running is safe: entries are deduped by a tracking column
(`bankroll.cash.entry_key_column` / `bankroll.gpp.entry_key_column`), so
nothing gets double-counted.

If your sheet doesn't have ledger tables shaped like this,
`dfs bankroll sync` will say so rather than guessing at where to write --
leave `[bankroll.cash]`/`[bankroll.gpp]` out of `config.toml` and it'll
tell you what's missing.

## Development

```bash
pytest              # offline; network sources are mocked, Sheets calls are faked
```
