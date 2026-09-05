# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
pytest -q          # run the test suite
ruff check .        # lint
ruff format .       # format
```

CI runs all three on every push/PR (`.github/workflows/ci.yml`). None of
the tests need real credentials, a real sheet, or a browser -- they mock
HTTP (`pytest-httpx`) and the Sheets client, and nothing in `tests/`
touches Playwright at all. If a test you're adding needs any of those,
that's a sign the logic under test should be split into a pure function
first (see "Sources" below).

## Design principles this codebase actually enforces

These aren't aspirational -- they're things that were previously broken
in the pre-rebuild code and got fixed on purpose (see `git log`, Phases
0-7). Code that reintroduces them will likely get pushback in review:

- **Every command returns a real exit code.** No subcommand should ever
  print an error and then exit 0. `cli.py`'s docstring exists specifically
  because the old `run_all.py` didn't do this.
- **No silent partial success.** A sync across multiple sources reports a
  real per-source result (`SourceResult`) -- `sync.py`'s docstring
  explains the old bug this replaced (`any(results.values())` counting
  one success out of eight as the whole run succeeding).
- **A source raises on failure; it never returns an empty/partial
  DataFrame to signal an error.** See `Source.fetch`'s docstring in
  `src/dfs/sources/base.py`.
- **Sheet-writing helpers only touch what they're told to.**
  `SheetsClient.update_range`/`clear_ranges` never clear or touch cells
  outside the given range, specifically so they're safe to use next to
  hand-built formulas and formatting in a live sheet. If you add a new
  sheet-writing method, keep that contract.

## Adding a new data source

Sources are a registry, not a hardcoded list (see
`src/dfs/sources/__init__.py`'s docstring for why that matters). To add
one:

1. Subclass `Source` (`src/dfs/sources/base.py`): implement `fetch(ctx) ->
   pd.DataFrame`, and override `to_sheet_rows`/`post_upload` only if your
   tab needs something other than "header + rows, values as-is".
2. Register it in `SOURCES` in `src/dfs/sources/__init__.py`.
3. Add a `tab_mappings` entry in `config.example.toml` (and document any
   new `[google_sheets.tab_mappings]` key there).
4. **Split fetch logic into pure, testable functions wherever the source
   needs a browser or live auth.** `tffb_projections.py` is the reference
   for this: `players_to_df()` is pure and fully unit-tested; the
   Playwright-touching `fetch()` method is a thin wrapper around it that
   isn't tested directly, since it can't run in CI. If you can't find a
   way to make some part of the logic pure, that's worth raising in review
   rather than skipping the test.
5. Read `tffb_projections.py`'s module docstring before building anything
   that touches TFFB's site -- it documents two dead-end approaches
   already tried (a CSV-export button that never resolves in a fresh
   headless Playwright profile, and a non-iframe page with no ownership
   data) so you don't re-discover either the hard way.
6. **If the source writes to a new tab, add that tab to the canonical
   weekly template** (the sheet linked from README.md's Setup section --
   currently
   `1ZSjMaRKRAXS-DmfOFePKaq_KemghmNQHsASSjttG97I`), not just to whatever
   sheet your own `config.toml` happens to point at. The template is what
   `File > Make a copy` actually duplicates every week; a tab that only
   exists in your personal sheet is invisible to every future weekly copy
   and to anyone else using this repo. Point a one-off command at the
   template with `--sheet-id <template-id>` (see `dfs sheets
   format-edge --help`) rather than editing `config.toml`, so you don't
   have to swap it back afterward.

## Live-sheet changes (formatting, new columns, conditional formatting)

If you're adding something that touches the *structure* of the actual
Google Sheet (not just syncing data into an existing tab), know this
before you start: inserting a column into a sheet auto-shifts formula
*range references* app-wide (`PlayerPoolRaw!$A:I` becomes `$A:J` in every
formula that referenced it, even from other tabs), but does **not** shift
hardcoded integer arguments (the `9` in `VLOOKUP(x, range, 9, false)`).
That silently broke several columns in a downstream tab during this
project's own Phase 8 (see `git log`) -- caught only because someone
checked a *resolved value*, not just that the formula text looked
reasonable. If your change inserts rows/columns anywhere, re-check every
formula elsewhere that used to point past the insertion point, and verify
with real output, not a read-through.

The same bug class exists purely in Python, no Sheets API involved: `dfs
sheets link-edge` (`sheet_links.py`) writes formulas into `PlayerPoolRaw`/
`Player Pool`/`Lineups` with a **hardcoded column-index integer per
EdgeRaw column**, computed from `derived.EDGE_COLUMNS`'s position list at
the time `link-edge` runs. Those formulas are plain text, not live
references -- if `EDGE_COLUMNS` is ever reordered, or a new column is
inserted anywhere but the very end, every already-written formula for
every column *after* the change silently starts reading the wrong data,
with no error. This actually happened once (see `docs/ROADMAP.md`'s
Phase 3 section) and was caught only by re-checking resolved values on
the live sheet, not by a test that existed at the time. **Any new
`EDGE_COLUMNS` entry must be appended at the very end**, never inserted
among existing ones, until `dfs sheets link-edge` is re-run (after
clearing the old linked block by hand) against every sheet it's been
applied to. `test_sheet_links.py`'s
`test_already_linked_columns_positions_never_move` pins the positions the
live sheet currently depends on -- if it ever needs to change, that
re-run has to happen first.

## Keeping docs and the sheet's Instructions tab in sync

This has already gone stale more than once: `EDGE_COLUMNS` gained columns
that `README.md`/`docs/SHEET_REFERENCE.md` didn't mention, and the
Instructions tab's "Weekly workflow" step-by-step and its EdgeRaw column
list both fell behind `dfs week new`/`dfs sync --live`/`dfs lineups
late-swap` being added -- caught only because someone went looking, not
because anything forced the update. **Before considering a change done,
if it touched any of the following, update every doc in its row**, not
just the code:

| You changed | Update |
|---|---|
| `EDGE_COLUMNS` (added/removed a column) | `README.md`'s "Edge layer" section, `docs/SHEET_REFERENCE.md`'s EdgeRaw table, the Instructions tab's EdgeRaw row (`B12` as of this writing) on **both** the live sheet and the template |
| A CLI command's name, flags, or behavior | `README.md`'s Commands list and "Weekly workflow" section, the Instructions tab's "Weekly workflow" row (`B4`) on both sheets |
| The weekly workflow itself (a step added, removed, or reordered) | Same two places as above, plus this file's own affected section if the change touched something documented here |
| A new tab | Everywhere the "Adding a new data source" checklist above already says, **plus** a new Instructions tab row describing it |

The Instructions tab is a real Google Sheet, not a file in this repo --
update it with `SheetsClient.update_range` (see any `dfs sheets`/`dfs
week` command for the pattern), once against the live sheet and once
against the template (`--sheet-id` or a `model_copy(update=...)`'d
config, same as `dfs sheets format-edge --sheet-id`). There's no test that
catches this going stale, so it's on the honor system -- treat it as
part of the change, not a follow-up.

Also worth a periodic check regardless of what you just changed: any
Instructions-tab `HYPERLINK` pointing at a repo file assumes that file is
tracked and public. `docs/ROADMAP.md` used to be one of those links; once
it was gitignored (session working notes, not public documentation), the
link 404'd, and had to be found and removed by hand -- nothing flags a
sheet formula pointing at a path git no longer tracks.

## Commit messages / PR descriptions

Explain *why*, not just what -- especially for anything that was tried and
rejected first. Future-you (or the next contributor) benefits far more
from "X didn't work because Y, so this does Z instead" than from a list of
changed files. Several files in this codebase have long module-docstring
"here's what didn't work" sections for exactly this reason; that's a
pattern worth continuing, not something specific to how they were written.
