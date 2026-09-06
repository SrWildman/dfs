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
   `10si1m87aaaSLloZa-Sht5dD6ZlG6dS8RDWjSzdxkhLA`), not just to whatever
   sheet your own `config.toml` happens to point at. The template is what
   `File > Make a copy` actually duplicates every week; a tab that only
   exists in your personal sheet is invisible to every future weekly copy
   and to anyone else using this repo. Point a one-off command at the
   template with `--sheet-id <template-id>` (see `dfs sheets
   format-edge --help`) rather than editing `config.toml`, so you don't
   have to swap it back afterward.

## The canonical template is rebuilt from the live sheet, not hand-patched

The template used to be maintained by hand-patching it directly (adding a
tab here, a column there) independently of whatever the live sheet had
already accumulated. That's exactly how it drifted: an audit eventually
found the live sheet at 28 tabs and the template at 24, with two tabs
(`DK Upload`, `Scratch`) config.toml already assumed existed on every
sheet -- meaning `dfs export`/`dfs lineups clear` would fail on the very
next fresh copy -- plus `Venue`/`Ceil` sitting in different columns on
each sheet (each internally consistent, so nothing local caught it), which
in turn broke `link-edge`'s tail-only idempotency check (see below).

The current template is instead a Google Drive copy of the live sheet,
stripped back to empty with `dfs lineups clear --sheet-id <template-id>`
plus a handful of manual tab clears (see `docs/ROADMAP.md` for the exact
list, if it's still around when you read this). Rebuilding this way next
time --  copy the live sheet, strip it -- is less error-prone than
hand-patching a drifted template, since it starts from a layout you know
the live sheet's formulas actually work with.

**`dfs sheets doctor --sheet-id <id>`** is the check that would have
caught the drift above before it shipped: every config-mapped tab exists,
`EdgeRaw`'s header matches `derived.EDGE_COLUMNS`, the `link-edge` block
is linked exactly once (not zero, not twice) on `Player Pool`/`Lineups`/
`PlayerPoolRaw`, `Lineups`' header repeats fall where
`LINEUPS_NAME_BLOCKS` expects, and Bankroll's configured header rows
aren't blank. It's read-only, and `dfs week new` now runs it against every
freshly-copied weekly sheet before writing anything -- run it by hand
against the template too after any structural change to it.

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

`link_edge_columns`'s own idempotency guard (the check that lets it be
safely re-run) used to compare only the tab's *last* N header columns
against `LINKED_EDGE_COLUMNS`, on the assumption that "already linked"
always means "linked at the very end." That assumption broke the moment
two sheets built from the same source diverged in total width: the old
template had two extra trailing columns (`Venue`/`Ceil`) the live sheet
didn't, so its tail no longer matched even though the columns were linked
correctly earlier in the row -- `link-edge` would have appended a second
copy on top of a real, working one. It now detects
`LINKED_EDGE_COLUMNS` as a contiguous run **anywhere** in the header, not
just at the tail (`sheet_links.find_all_contiguous`) --
`test_link_edge_columns_skips_when_linked_block_is_not_at_the_tail` pins
this exact scenario as a regression test.

## Structural changelog

Every change that actually moves a row, column, or tab position on the
live sheet and/or template gets a row here -- this is what lets a future
change confirm "does anything still assume the old position" without
re-deriving the whole history from git log. See "Live-sheet changes"
above for why this matters; each row names the Python symbol(s) that
encode the *new* position, so grepping for them finds every dependent.

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-06 | `Lineups` | Header row + all 20 lineup blocks, pushed down by a new 7-row frozen "Bench" (`dfs sheets add-bench`, `sheet_bench.py`) inserted at the top via a real Sheets row insert | header row 1; blocks at 2, 15, 28 … 249 | header row 8; blocks at 9, 22, 35 … 256 | Live + Template | `weekly_reset.LINEUPS_NAME_BLOCKS`, `doctor._check_lineups_header_repeats`'s `expected_rows`, `sheet_style.polish_builder_tab`'s new `header_row`/`freeze_rows`/`freeze_cols` params (Lineups' `dfs sheets polish` call site in `cli.py`), `sheet_views.build_exposure`'s new `lineups_data_start_row` param (its "Slots filled" formula would otherwise miscount the Bench's own position labels as filled roster slots), `sheet_links.link_edge_columns`'s new `header_row` param and `doctor.run_doctor`'s override of `list_tabs()`'s always-row-1 header for Lineups specifically (see incident note below) |

**Incident, same date:** `sheet_links.link_edge_columns` also hardcoded
reading/writing a tab's header at row 1 -- a second, independent instance
of the exact bug class `polish_builder_tab` had (and got fixed for) just
above. It wasn't caught in review before running against the template:
`dfs sheets link-edge --sheet-id <template>` read Lineups' new Bench
title (row 1) as its "header," concluded Lineups wasn't linked yet, and
appended a second, wrongly-positioned `LINKED_EDGE_COLUMNS` block
starting at column B -- overwriting Pos./Team/DK Sal/O-U/Spread/Team
Implied/Opp./Venue/OppPosRank/Pts across all 20 lineup blocks (rows
9-265) on the **template only**. The mandated template-first order (see
"Live-sheet changes" above) is exactly what kept this off the live sheet.
Recovered by reading the still-correct original formulas off live's
untouched pre-shift rows, shifting every row reference by +7, and writing
them back to the template; the 3 erroneous conditional-format rules and
the erroneous B:K column group the same bad run added were identified by
inspecting the template's raw `conditionalFormats`/`columnGroups`
metadata (not blind-cleared) and deleted individually, leaving the
legitimate pre-existing ones untouched. Fixed at the root: `doctor.py`'s
`run_doctor` no longer trusts `list_tabs()`'s row-1-only header for
Lineups, and `link_edge_columns` takes an explicit `header_row`. Lesson
for the next row-insert-adjacent change: grep for *every* hardcoded
`"A1:1"` / `row_values(1)` in the codebase before running anything live,
not just the one function under active review.

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
| A formula or threshold (how a column is actually calculated, not just its name) | `docs/CALCULATIONS.md` -- the one place the exact math is supposed to live; don't let it drift into being "close enough" to what the code does |
| A CLI command's name, flags, or behavior | `README.md`'s Commands list and "Weekly workflow" section, the Instructions tab's "Weekly workflow" row (`B4`) on both sheets |
| The weekly workflow itself (a step added, removed, or reordered) | Same two places as above, plus this file's own affected section if the change touched something documented here |
| A new tab | Everywhere the "Adding a new data source" checklist above already says, **plus** a new Instructions tab row describing it |
| The template's tab set or a shared tab's column order (`PlayerPoolRaw`/`Player Pool`/`Lineups`) | Run `dfs sheets doctor` against **both** the live sheet and the template, `docs/SHEET_REFERENCE.md`'s canonical-column-order note, the Instructions tab's column-order row on both sheets |

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
