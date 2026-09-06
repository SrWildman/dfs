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
| Game context (stadium/roof/surface/rest/closing lines, `GamesRaw` tab) | Working -- free, unauthenticated `nflverse` schedule data |
| Weather (wind/gusts/precip/temp for outdoor games, `WeatherRaw` tab) | Working -- free, unauthenticated Open-Meteo, no API key |
| Edge layer (Leverage/CeilVal/GameEnv/Stadium/Roof/Wind/LineMove/Avail, `EdgeRaw` tab) | Working -- computed locally from already-synced sources, no network call of its own (see `dfs edge` / `dfs sheets format-edge`) |
| Lineup export & validation | Working, against a manually-paired entries tab |
| Weekly sheet reset (`dfs lineups clear`) | Working -- clears last week's typed lineups/picks, formulas and formatting untouched |
| New-week transition (`dfs week new`) | Working -- repoints `config.toml`, carries the bankroll forward, clears lineups, syncs |
| Line movement (`EdgeRaw`'s `LineMove`, `LINE↑`/`LINE↓`) | Working -- diffs the current sync against the *start of the current NFL week*, not the last sync |
| Odds diff report (`dfs odds movement`) | Working -- a separate, terminal-only report: diffs the current `nfl_odds` sync against the previous one |
| Live re-sync with a diff report (`dfs sync --live`) | Working -- re-syncs odds/DK status/weather + edge, prints EdgeRaw Flag changes |
| Gameday late-swap check (`dfs lineups late-swap`) | Working -- flags locked vs. open roster slots by real kickoff time, suggests open replacements |
| End-of-week reconciliation (`dfs week close`) | Working, from a manually-exported DK CSV -- thin wrapper over `dfs bankroll sync` |
| Bankroll sync (Cash/GPP) | Working, from a manually-exported DK CSV |
| Strength of Schedule | Not yet ported -- see `legacy/README.md` |
| Player ownership % (field consensus, not TFFB's own) | Not started |
| Live DK contest history / entries (no manual export) | Investigated, not built -- would need probing DraftKings' undocumented authenticated endpoints live, with the user present; `dfs auth dk`'s saved session is unused until then |

See `CONTRIBUTING.md` before adding a source or touching the live sheet's
structure. See `docs/SHEET_REFERENCE.md` for what every tab and column in
the actual Google Sheet means, and `docs/CALCULATIONS.md` for the exact
formula behind every computed one -- the sheet's own `Instructions` tab
points to both for anything beyond its own one-line-per-tab summary.

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
(tabs like `TFFBOptoRaw`, `PlayerPoolRaw`, `DkSalClean`, `SoS*`, etc., plus
the formulas that tie them together) -- `File > Make a copy` it, don't sync
into a blank sheet. In practice a new copy gets made every week; run `dfs
week new <url-of-the-copy>` afterward to point `config.toml` at it and carry
the bankroll forward automatically (see "Weekly workflow" below).

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
dfs sheets doctor                  # read-only structural check: tabs exist, EdgeRaw's
                                    # header matches, linked columns aren't duplicated, etc.
dfs sheets doctor --sheet-id <id>  # check a different sheet (e.g. before pointing at it)
dfs sheets add-bench                # one-time: 7-row frozen Player Pool bench atop Lineups
dfs sheets add-bench --sheet-id <id>  # apply to a different sheet (e.g. the template)

dfs week new <sheet-url>           # point config.toml at a new weekly sheet copy,
                                    # carry the bankroll forward, clear lineups, sync
dfs week close --csv history.csv   # end-of-week bankroll reconciliation (see Bankroll sync below)

dfs sync                           # fetch all sources, upload to Sheets
dfs sync --only draftkings,nfl_odds
dfs sync --no-upload               # fetch and store locally, skip Sheets
dfs sync --week 3 --season 2026    # override auto-detected week/season
dfs sync --live                    # Sunday: re-sync odds/DK status/weather + edge,
                                    # print what changed in EdgeRaw's Flag column

dfs edge                           # top leverage plays, printed locally (no Sheets round-trip)
dfs edge --top 10 --position RB
dfs sheets format-edge             # one-time: freeze header + color scales on the EdgeRaw tab
dfs sheets link-edge                # one-time: append EdgeRaw's columns to Player Pool/Lineups/PlayerPoolRaw
dfs sheets format-edge --sheet-id <id>  # apply to a different sheet (e.g. the template)

dfs odds movement                  # which teams' lines moved since the last nfl_odds sync
dfs odds movement --top 5

dfs export -o lineups.csv          # validate + export DK bulk-upload CSV

dfs lineups clear                  # wipe last week's typed lineups/picks (new sheet copy)
dfs lineups clear --sheet-id <id>  # apply to a different sheet (e.g. the template)
dfs lineups late-swap              # gameday: which rostered players have locked,
                                    # which haven't, and who's still open at that slot
dfs lineups late-swap --top 5

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

1. **New week**: duplicate the [weekly template](https://docs.google.com/spreadsheets/d/10si1m87aaaSLloZa-Sht5dD6ZlG6dS8RDWjSzdxkhLA/edit),
   then `dfs week new <url-of-the-copy>` -- it runs `dfs sheets doctor`
   against the copy first (aborting before anything is written if the copy's
   missing a tab or its layout has drifted), points `config.toml` at the
   copy, carries the Bankroll tab's Ending balances and the season-level
   Results log forward, runs `dfs lineups clear`, and finishes with a full
   `dfs sync`, all after one confirmation prompt. (`dfs lineups clear`
   alone still exists if you only need that one step.)
2. **Sync everything**: `dfs sync` (salaries, odds, TFFB projections, and
   the derived `edge` layer computed from them -- the last needs `dfs auth
   tffb` done at least once). Re-run `dfs sync --only draftkings,nfl_odds`
   multiple times through the week as lines move (re-run with `edge` too,
   or just `dfs sync`, to keep `EdgeRaw` current).
3. **Build lineups** in the sheet, using `EdgeRaw`'s `Leverage` sort and
   `Flag` column (`LEVERAGE`/`CHALK`/`OUT`/`WIND`) to find the plays worth
   a second look, alongside the usual `Player Pool` view.
4. **Pair lineups to contest entries** in your DK-upload tab (this stays a
   manual step -- see below), then `dfs export -o lineups.csv` and upload
   that file to DraftKings.
5. **Watch/adjust** through the week; re-sync and re-export as needed. On
   Sunday, `dfs sync --live` re-pulls just the fast-moving sources (odds,
   DK status, weather), recomputes `EdgeRaw`, and prints a "what changed"
   report of every `Flag` change since the last sync -- late inactives,
   wind picking up, a last-minute line move -- instead of making you
   re-scan the whole sheet. As games kick off in waves, `dfs lineups
   late-swap` checks each built lineup against real kickoff times and
   shows, for anyone not locked yet, who else is still available at that
   slot -- so you know when a late swap is actually worth making, and how
   the lineup looks either way.
6. **End of week**: export your contest history from DraftKings and run
   `dfs week close --csv <file>` (a thin wrapper over `dfs bankroll sync
   --csv` -- see "Bankroll sync" below for why it isn't more than that yet)
   to reconcile Cash and GPP results.

## Edge layer

`EdgeRaw` is computed locally from your already-synced `projections` and
`draftkings` data (joined exactly on DraftKings' own player ID -- no name
matching), plus `nflverse_games`/`weather` if you've synced those too (both
optional -- `EdgeRaw`'s columns are always the same regardless, just blank
without them, so the tab's shape never changes week to week). None of this
makes a network call of its own, so `dfs sync --only edge --no-upload`
recomputes it offline any time. Rows are written pre-sorted by `Leverage`
descending, so the top of the tab is the answer:

- `Val` / `CeilVal` -- points (median / ceiling) per $1,000 salary.
- `CeilPct` -- this player's `Ceiling` percentile rank within their
  position; blank wherever TFFB hasn't given that player a `Ceiling` yet.
- `Leverage` = `CeilPct - ProjOwn`. TFFB's `ProjOwn` reads 0 for everyone
  until it computes real ownership midweek, so until then this degenerates
  to `CeilPct` alone -- still useful as a pure ceiling proxy, but the
  `LevBasis` column always says `real` or `proxy` so you know which one
  you're looking at. Because a "proxy" score is really just a raw
  percentile (0-100, centered around 50) rather than a gap from ownership
  (-100..100, centered around 0), the `Flag` column uses a much higher bar
  under `proxy` (top ~15% of position) than under `real` -- otherwise
  almost every above-average player would get flagged before ownership
  data even exists.
- `GameEnv` -- 0-100 stacking-environment score per game, from that game's
  total and spread (both already in `projections`).
- `Stadium` / `Roof` -- from `GamesRaw`, joined by team code; blank if
  `nflverse_games` hasn't been synced.
- `Wind` -- from `WeatherRaw` (blank for dome games, or if `weather` hasn't
  been synced); only computed for `Roof == outdoors` games in the first
  place.
- `Avail` -- DraftKings' own `Status` (`Q`/`OUT`/`IR`).
- `Flag` -- the one column meant to be read at a glance, in priority order:
  `OUT` (from `Avail`), `WIND` (`Wind` over ~20mph), `LINE↑`/`LINE↓`
  (`LineMove` past a threshold), `LEVERAGE`, `CHALK`, or blank.
- `LineMove` -- this player's team's Vegas-implied point total, change
  since the **start of the current NFL week** (not the previous sync --
  diffing against the last sync meant the exact same real move could show
  as a big number or nothing depending purely on how often `dfs sync`
  happened to run, which isn't a signal). `dfs odds movement` is a
  separate, terminal-only report that still diffs since-the-last-sync,
  for a quick "did anything just move" check before a full re-sync.
- `GameStart` -- this player's game's kickoff time (UTC), straight from
  TFFB's projections. Backs `dfs lineups late-swap`'s lock-time check; not
  something you'd read directly in the sheet.

See `docs/CALCULATIONS.md` for the exact formula behind every column
above, if you want to verify a number rather than take the description on
faith.

`dfs edge [--top N] [--position POS]` prints the same thing to the
terminal without opening the sheet. `dfs sheets format-edge` is a one-time
setup command (frozen header row, color scales on `Leverage`/`CeilVal`/
`GameEnv`) -- re-running it is safe, and `--sheet-id <id>` points it at a
different sheet (e.g. the canonical template) instead of `config.toml`'s.

`dfs sheets link-edge` goes further: it appends most of `EdgeRaw`'s
columns (all except `Val`, which already exists elsewhere, and
`LineMove`/`GameStart`, both added after `link-edge` was last run against
the live sheet -- see CONTRIBUTING.md) onto the far right of
`Player Pool`, `Lineups`, **and**
`PlayerPoolRaw` -- the tab those two already read from for Pos./Team/
Pts/etc. -- via the same VLOOKUP-by-Name join, so the signal shows up
right where lineups get built, not just in a separate tab. Always appends
past whatever's currently there (never inserts -- see CONTRIBUTING.md's
Phase 8 postmortem), groups the new columns so they can be collapsed from
the sheet UI when you want the narrower view back, and is safe to re-run
(a tab that's already linked is left alone -- detected anywhere in the
header row, not just at the end, so a sheet whose layout has drifted
doesn't get a silent duplicate append).

**If you add a new tab like this to the pipeline, add it to the canonical
template too** (the sheet linked above), not just your own weekly copy --
see `CONTRIBUTING.md`'s "Adding a new data source" checklist.

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
