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
   template with `--sheet-id <template-id>` (see `dfs setup
   polish --help`) rather than editing `config.toml`, so you don't
   have to swap it back afterward.

## Building a sheet from scratch

`dfs setup sheet --sheet-id <id>` runs the full one-time build (pool deck,
Pool Picks, the four view tabs, EdgeRaw linking, filter views, protection,
styling, and two verification passes) in the one order that actually
works, stopping at the first step that fails. That order -- and why it's
that order and not another -- lives in the command's own docstring
(`setup_sheet` in `cli.py`), not duplicated here; this used to be
undocumented tribal knowledge (run these nine commands, in your head, in
roughly this order), which is exactly the kind of thing that goes stale
silently. If the order ever needs to change, change it there and it stays
the one place anyone (including a future Claude session) would look.

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
plus a handful of manual tab clears (see `docs/planning/ROADMAP.md` for the exact
list, if it's still around when you read this). Rebuilding this way next
time --  copy the live sheet, strip it -- is less error-prone than
hand-patching a drifted template, since it starts from a layout you know
the live sheet's formulas actually work with.

**`dfs doctor --sheet-id <id>`** is the check that would have
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
setup link-edge` (`sheet_links.py`) writes formulas into `PlayerPoolRaw`/
`Player Pool`/`Lineups` with a **hardcoded column-index integer per
EdgeRaw column**, computed from `derived.EDGE_COLUMNS`'s position list at
the time `link-edge` runs. Those formulas are plain text, not live
references -- if `EDGE_COLUMNS` is ever reordered, or a new column is
inserted anywhere but the very end, every already-written formula for
every column *after* the change silently starts reading the wrong data,
with no error. This actually happened once (see `docs/planning/ROADMAP.md`'s
Phase 3 section) and was caught only by re-checking resolved values on
the live sheet, not by a test that existed at the time. **Any new
`EDGE_COLUMNS` entry must be appended at the very end**, never inserted
among existing ones, until `dfs setup link-edge` is re-run (after
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

| 2026-09-06 | `Lineups` | The above row's 7-row "Bench" (names only) was superseded, same day, before general use: `sheet_bench.py` removed, replaced by `sheet_pool_deck.py`'s 14-row "pool deck" (`dfs sheets add-pool-deck`) -- a sortable/filterable window into Player Pool carrying every metric column (Salary/Pts/Ceil/Val/CeilVal/Leverage/Flag/...), not just names, since a names-only pinned list didn't remove the reason Sam was using split-screen in the first place. A sheet already migrated to the 7-row Bench was carried the rest of the way (clear its content, insert 7 *more* rows) rather than reverted and redone, so the net shift from the original template is +14, not a clean single move. | header row 8 (from the row above); blocks at 9, 22, 35 … 256 | header row 15; blocks at 16, 29, 42 … 263 | Live + Template | `weekly_reset.LINEUPS_NAME_BLOCKS` (now +14 from original), plus every symbol in the row above -- all of them are parameters *derived* from `LINEUPS_NAME_BLOCKS[0][0] - 1` (not hardcoded to 8), so this second shift required zero code changes to `polish_builder_tab`/`build_exposure`/`link_edge_columns`/`doctor`'s call sites beyond the constant itself. New: `sheet_pool_deck.DECK_ROWS`, `POOL_SORT_TAB` (a new hidden helper tab) |

Verified end-to-end on both sheets after the second shift: `P16` reads
`=D16/D$25` (was `=D9/D$18` before this, `=D2/D$11` originally) --
confirming Sheets re-shifted every existing formula (Lineups' own,
*and* the "Bench" row's `dfs sheets link-edge`/Guardrails formulas
written in the meantime) a second time, correctly, with no code
involved in that part at all. `dfs sheets doctor`/`link-edge` clean and
idempotent on both sheets afterward; `add_pool_deck` itself is
migration-aware (detects "fresh" / "bench" / "deck" state before
touching anything) and idempotent once a deck is present.

| 2026-09-06 | `Lineups` | The above row's 14-row deck shrunk to 10 rows (window from 10 rows to 6) after a live screenshot on a 16" MacBook showed two full lineup blocks didn't fit below the 14-row frozen zone -- `sheet_pool_deck.py`'s `DECK_ROWS`/`_WINDOW_SIZE`, `add_pool_deck` gained a fourth state ("deck14") that deletes the now-unwanted tail (a real `deleteDimension`, not a rewrite) instead of tearing the whole deck down, since rows 1-9 already held correct content at the new size. | header row 15; blocks at 16, 29, 42 … 263 | header row 11; blocks at 12, 25, 38 … 259 | Live + Template | `weekly_reset.LINEUPS_NAME_BLOCKS` (now +10 from original -- yes, *smaller* than the +14 two rows up; the net shift went down, not up, because 4 rows were deleted), `sheet_pool_deck.DECK_ROWS`/`_WINDOW_SIZE`, new `SheetsClient.delete_rows` |

**Two bugs found during this shrink, both real, neither caught before running live:**

1. **Inherited dark background on every inserted row.** `insertDimension`'s `inheritFromBefore=False` does not mean "insert blank rows" -- there is no such option. It means "inherit formatting from the dimension *after* the insertion point," and inserting at row 1 has no "before" to inherit from, so `False` was the only valid choice all along. Every row this feature has ever inserted (the original 7-row Bench, the 14-row deck, this 10-row shrink) therefore copied the formatting of whatever row got pushed below it -- which, at the very top of Lineups, is always the dark-filled header row. Invisible in every formula-level check this whole feature was verified with; caught only because Sam sent a screenshot showing a solid dark block where the deck should look blank, and a direct read of `userEnteredFormat.backgroundColor` confirmed rows 1-14 all carried the exact header fill color. Fixed by having `add_pool_deck` explicitly reset the deck zone's background to white after every construction path, and documented the real `inheritFromBefore` semantics on `SheetsClient.insert_rows` itself so the next row-insert-adjacent change doesn't rediscover this the same way.
2. **Off-by-one in the "deck14" shrink's delete range.** First shipped as `delete_rows(at_row=DECK_ROWS + 1, ...)` (row 11) instead of `delete_rows(at_row=3 + _WINDOW_SIZE + 1, ...)` (row 10, which happens to equal `DECK_ROWS` itself) -- one row too low, so the surviving row was the old window's 7th-ranked pull instead of the intended blank separator, landing a stray formula in what should have been an empty row. The unit test for this call didn't catch it because it asserted the same (wrong) value the implementation computed, rather than an independently-derived correct one -- a reminder that a test mirroring the code under test verifies self-consistency, not correctness. Caught by reading the live template's row 10 after the first run and finding a formula where a blank was expected; fixed in code, the test corrected to assert the right value, and the template's already-wrong row repaired by hand (`clear_ranges`) before live was ever touched with the buggy version -- live got the corrected code on its first and only run.

**Three more rounds of live-screenshot-driven fixes, same feature, same day:**

3. **The background-only reset (fix 1 above) left inherited white text on a now-white background -- invisible, not just unstyled.** `_HEADER_FMT`'s `textFormat.foregroundColor` is white (by design, for the dark header it's normally applied to); resetting only `backgroundColor` left every inherited cell's text white-on-white. Fixed by having the reset set both `backgroundColor` and `textFormat.foregroundColor` together, with `_hide_g1`'s own deliberate white-on-white (G1's helper cell) reapplied *after* that reset, not before, so the general fix doesn't undo the one deliberate exception.
4. **The formatting reset only ever ran on paths that changed row count.** `add_pool_deck` returned early for the "deck" state (already at the current size) *before* reaching the reset -- meaning every fix above worked in tests and on a freshly-migrated sheet, but never reached a sheet whose deck was already built at size, including both real sheets after the first correction round shipped. Fixed by removing the early return: the structural step (insert/delete) still runs at most once per state, but PoolSort/controls/formatting/freeze always rerun unconditionally, the same idempotent-rebuild pattern `dfs sheets polish` already uses.
5. **Two cosmetic-but-real issues visible only once the text was actually readable:** row 3 (the deck's own copy of Player Pool's header) was never styled, unlike every other header on the sheet -- fixed by exposing `sheet_style.py`'s `_HEADER_FMT` as a public `HEADER_FMT` and applying it to row 3 too. And the "N in pool" readout used `COUNTA` on PoolSort's result column, which counts a real Sheets gotcha: `IFERROR(SORT(FILTER(...)),"")` falls back to a single `""` cell when the pool is empty, and `COUNTA` counts that as non-blank -- misreporting "1 in pool" (and, once F1 exceeded the count, "showing 1-0", start past end) when there were actually zero players typed in. Fixed with `COUNTIF(...,"?*")` (requires at least one real character) and by suppressing the "showing N-M" half entirely when the count is 0.
6. **A pre-existing, unrelated data-validation rule on Lineups' column A (`ONE_OF_RANGE` against `PlayerPoolRaw!$A:$A` -- "must be a real player name") was inherited by every row this feature has ever inserted, for the identical root-cause reason as bug 1: `insertDimension`'s `inheritFromBefore=False` copies the *entire* format and validation of whatever row is pushed below the insert, not just the parts this feature happens to care about.** Completely invisible to every check so far, including the earlier background/text fixes, because every write in this whole feature goes through the Sheets API, and the API does not enforce `strict` validation the way the interactive UI does -- a script can write anything into a strictly-validated cell without error. Surfaced only when a person tried to type into one of the deck's own cells in the browser and got an "Input must fall within specified range" rejection. Fixed with a new `SheetsClient.clear_data_validation` (a `setDataValidation` request with no `rule`, which is how the API expresses "remove whatever's here"), called across the whole deck zone as part of the same formatting reset, before B1/D1's own deliberate dropdowns are (re)applied.

Cumulative lesson from this whole sequence: `inheritFromBefore` on an insert-at-top copies *everything* about the row it pushes down -- fill, text color, and data validation alike -- and none of it is visible to a formula-level or values-only check. Anything this codebase inserts at the top of a tab with real formatting/validation on it needs an explicit, complete reset afterward, verified by reading `userEnteredFormat` *and* `dataValidation` back from the API, not just cell values -- and that reset has to run on every code path, including "nothing structurally changed here."

| 2026-09-06 | `Player Pool` | RB/TE/DST position blocks grown (Task K: Sam asked for more headroom once ticking in `EdgeRaw` replaced retyping) via a real `insertDimension` per block, inserted in the *middle* of each block (immediately after its last existing row, `inheritFromBefore=True`) rather than at the top -- a materially safer case than every Lineups insert above, since there's a real, already-correctly-formatted in-block row to inherit from instead of the tab's own header. | RB 13-29 (17), WR 31-55 (25), TE 57-65 (9), DST 67-74 (8) | RB 13-32 (20), WR 34-58 (25, shifted only), TE 60-69 (10), DST 71-80 (10) | Live + Template | `weekly_reset.PLAYER_POOL_NAME_BLOCKS`, new `sheet_pool_resize.py` (`resize_player_pool`/`grow_block`/`fix_color_scale_ranges`), `sheet_pool_formulas.py`'s Name/Overflow formulas (re-run after resizing to pick up the new caps) |

**One real gap found and closed during this resize, not from a live screenshot this time but from reading `conditionalFormats` metadata directly before trusting the insert:** `sheet_links.link_edge_columns` writes the three EdgeRaw-linked color scales (Leverage/CeilVal/GameEnv) to a hardcoded `row 2 : max(block ends)` range exactly once, on first link, and is idempotent by skipping entirely on every later run ("already linked ... skipped") -- so simply re-running `dfs sheets link-edge` after growing a block does *not* re-point these ranges, and does not backfill the new rows' EdgeRaw-linked columns (B..Y) either. `insertDimension` itself auto-extends a range for an insert strictly *inside* it (confirmed: the RB and TE inserts, landing well before the old range's end, extended correctly with no code needed), but the DST insert -- the last block -- lands exactly at the range's own tail, an ambiguous case Sheets does not reliably auto-extend through. Rather than depend on that ambiguity, `fix_color_scale_ranges` deletes and re-adds all three explicitly at the verified final range every time, and every new row's B..Y columns are filled by copying the row immediately above it byte-for-byte (substituting only its own `A<row>` self-reference) rather than regenerated from `edge_lookup_formula` -- the already-linked formulas turned out to reference an older, narrower EdgeRaw range (`$B:$T`) than today's `derived.EDGE_COLUMNS` would generate (`$B:$V`); both are correct, but overwriting a working pattern with a cosmetically different one for no reason is exactly the kind of unforced drift this changelog exists to prevent.

**A second instance of the same bug class, found after the fact, no cells moved this time:** `sheet_pool_deck.py` hardcoded `'Player Pool'!$A$2:$Z$74` / `$A$74` in four places (PoolSort's filter formula, the "in pool" COUNTIF readout) -- the tail of the *original* five Player Pool blocks, before the resize row above grew them to end at row 80. The resize itself never touched `sheet_pool_deck.py`, so nothing broke loudly: the deck kept working, just silently capped at row 74, six rows short of DST's new 71-80 block -- six of ten DST slots invisible in the deck's "Start at" window with no error, the same failure shape (a hardcoded row range surviving a resize defined elsewhere) as the `link_edge_columns` incident above, just in a different module. Fixed by deriving `_POOL_LAST_ROW = max(end for _, end in weekly_reset.PLAYER_POOL_NAME_BLOCKS)` instead of a literal integer, so the deck now follows any future resize automatically. `doctor`'s new `pool-deck-range` check (see `doctor.py`) now fails loudly if this ever drifts again, instead of shipping a third silent instance of the same bug class.

| 2026-09-06 | `EdgeRaw` | `Pool` moved from column W (appended past `EDGE_COLUMNS`) to column A (ahead of it), per Sam's request to have it beside `Name` without scrolling -- achieved by hiding `Id` (now column B, immediately after Pool) rather than physically reordering it next to `Name`. Every other `EDGE_COLUMNS` column shifted one column right as a result. | Id A, Name B, ... GameStart V, Pool W | Pool A, Id B (hidden), Name C, ... GameStart W | Live + Template | New `derived.EDGE_DATA_OFFSET` (=1), added to every `EDGE_COLUMNS.index(name)` call that produces an absolute EdgeRaw column letter -- `sheet_links.py`'s `_EDGE_RANGE_START`/`_EDGE_RANGE_END`, `sheet_style.py`'s `_edge_letter`/`polish_edge`'s freeze calc, `sheet_pool_formulas.py`'s `_EDGE_NAME_COL`/`_EDGE_POSITION_COL`, `sheet_views.py`'s `_col`, `doctor._check_edge_header` (now expects `[Pool, *EDGE_COLUMNS]`), `sources/edge.py`'s own `_ID_COLUMN`. New `SheetsClient.hide_columns`. `EDGE_COLUMN_GROUPS` lost its `("Id","Id")` entry (hidden outright now, not grouped). |

**Migration mechanics, since this moved a column *inside* an already-linked range, unlike every append-only change above:** `EdgeRaw` itself holds no formulas (pure synced values, fully rewritten every `dfs sync`), so the tab itself needed no `insertDimension` dance -- just a `to_sheet_rows` change (Pool prepended, not appended) and a normal re-sync. The real risk was everything *else*: `PlayerPoolRaw`/`Player Pool`/`Lineups`' already-written VLOOKUP formulas hardcode `EdgeRaw!$B:$V`-style literal ranges and indices (see `sheet_links.py`'s own docstring on this exact scenario) -- correct under the old layout, silently wrong under the new one (same index, but the range's start/end letters point one column off). Fixed by clearing each tab's existing linked block (values, its 3 color scales, its column group) and re-running `link_edge_columns` fresh, exactly the "clear the old linked columns by hand first" recovery path that module's docstring already prescribed for "if `EDGE_COLUMNS`' order or membership ever changes." **One real gap found during this, template first:** `Player Pool` has a fixed `Overflow` column at `Z`, past its linked block -- clearing the block's own columns left `Z1` non-blank, and Sheets' header-row read only trims *trailing* blanks off the very end of a row, not a blank gap in the middle, so `link_edge_columns` measured the header as its full old width and appended the fresh block one column too far right (at `AA` instead of `P`). Caught by reading the header back before trusting it, not by the command's own "OK" output. Fixed by clearing `Z1` too before re-linking (`write_pool_formulas`, already run afterward to refresh Player Pool's Name/Overflow formulas for the new `EdgeRaw` column refs, restores it) -- live got the corrected sequence on its first and only run.

| 2026-09-06 | `Player Pool` | A stray `Cash` header (dead text typed directly into the sheet at some point -- never written by any code path, no data under it) cleared from column O | O1 = `Cash` | O1 = blank | Live + Template | `sheet_pool_deck.py`'s deck window and G1 `MATCH` array, both rewritten (see paragraph below) to stop assuming Player Pool and Lineups share one column layout |

**A design-pass side quest that turned into a real, live correctness bug fix, found while giving the pool deck's window the same field formats/colour scales as the blocks below it (the original ask):** `sheet_pool_deck.py`'s row 3 was built by copying **Player Pool's** header verbatim onto Lineups, and a hardcoded `_MATCH_ARRAY`/`_WINDOW_COLUMNS` assumed Player Pool and Lineups have identical column layouts from `N` (`Rstr%`) onward. They don't, and never reliably would: `sheet_links.link_edge_columns` appends each tab's linked EdgeRaw block one column past *that tab's own* width at first-link time, independently per tab, with nothing keeping the two in sync. Lineups genuinely has two columns (`Check`/guardrails, `% of Rstr`) Player Pool has no equivalent of; Player Pool's block therefore already sat one column left of Lineups', and the stray `Cash` header (above) coincidentally kept the *total* column count matching so nothing structural ever flagged it. Two real, live effects: (1) the deck's row 3 said `CeilVal` directly above a block row further down that said `% of Rstr` in the *same column*; (2) `_MATCH_ARRAY` being one entry short of reality meant G1's `MATCH($D$1,...)` returned 17 for `CeilVal` when its real position was 16 -- so **"Sort by: CeilVal", the deck's own default, silently sorted by `CeilPct` instead**, live, before this fix. Confirmed on both sheets by reading `G1` back directly (17, wrong) before the fix and (16, correct) after. Fixed by deriving both row 3 and the window from each tab's *own real header*, matched by column NAME rather than raw index: row 3 now copies Lineups' own block header (so labels always match what's below them), and each window cell looks up that column's name in Player Pool's header (where `PoolSort`'s data actually lives) rather than assuming the same index means the same field on both tabs. A Lineups-only column name with no Player Pool equivalent is now correctly left blank instead of pulling the wrong field under the wrong label. Surfaced a second, smaller idempotency bug in the same motion: `polish_pool_deck`'s conditional-format clear-before-re-add used an *exact-range* match (column + rows), which is not robust to the column moving between runs -- exactly what happened here once the header-matching fix changed which column `CeilVal` landed in, leaving the old rule orphaned (found via `conditionalFormats` metadata: 6 rules in the deck's row band instead of the expected 3). `SheetsClient.clear_conditional_formats` gained a `row_range` mode (clears anything confined to a given row band, any column) to replace it. The same design pass also consolidated every tab's number-format dict into one `sheet_style.FIELD_FORMATS` keyed by header text (`EDGE_NUMBER_FORMATS`/`BUILDER_NUMBER_FORMATS` deleted), and gave EdgeRaw row banding, six colour scales (was three) plus a diverging one for `LineMove`, a muted per-position tint, a Wind chip matching Slate Grid's, and a pooled/flagged tint+bold on the Name cell.

| 2026-09-07 | `Pool Picks` | A plain-text title explaining the tab (Fix 3.1 -- Sam's question was literally "what is this new pool picks tab") inserted at row 1, pushing the real header from row 1 to row 2 and typed picks from rows 2-101 to rows 3-102 (+1 row of capacity, to keep the same 100-row headroom) | header row 1; picks 2-101 | header row 2; picks 3-102 | Live + Template | `sheet_pool_picks.py`'s `FIRST_DATA_ROW`(=3)/`HEADER_ROW`(=2)/`LAST_ROW`(=102) (all newly public, were private/hardcoded-101 before), `sheet_pool_formulas.py`'s `_PICKS_NAME_RANGE`/`_PICKS_POSITION_RANGE` (now derived from those constants instead of a literal `$A$2:$A$101`), `sheet_audit.AUDITED_TABS`' Pool Picks entry (header_row 1 -> 2), `sheet_filters.add_basic_filters`' `pool_picks_range` argument (`cli.py`'s call site derives it from `HEADER_ROW`/`LAST_ROW`, not a literal) |

Verified before writing to either sheet, not assumed: read back column A rows 1-101 on both live and template first -- both held only the old header text (`Player`) in row 1, no real typed picks anywhere else -- so the plain overwrite `create_pool_picks_tab` already does (never touches column A's own values, only rewrites title/header/formula columns) carried no data-loss risk this time. A sheet with a real pick already sitting in the old row 2 would need that value physically moved to row 3 first (a real row insert, not a targeted overwrite) before running the new code -- not needed here, but worth remembering if this tab is ever restructured again after picks exist.

**Incident, 2026-09-08, no cell moved -- a live corruption bug in `dfs sync`'s EdgeRaw write, found from a user screenshot showing broken checkboxes:** after Fix 1 gave EdgeRaw a basic filter with clickable sort arrows, a person sorting/filtering it (here: filtered to QB, sorted by `Val`) leaves that sort *active* on the tab's one shared basic filter. The next `dfs sync`'s `EdgeSource.post_upload` then calls `SheetsClient.set_checkbox_validation` on the whole Pool column while that sort is still active -- and the `setDataValidation` batch write silently no-ops on most of the range. Reproduced down to a clean 9-row range on the live tab (only the first row took); reproduced at the real 742-row scale too; did **not** reproduce at all on a disposable scratch tab with the same row count and no filter -- isolating the trigger to "a `setDataValidation` write landing on a tab whose basic filter has an active sort," not range size. The underlying tick values were never wrong (the 5 players that read `TRUE` were exactly the QBs visible under that filter -- real data, not corruption), but most of the column silently lost its checkbox rendering (blank cells, or a bare `TRUE`/`FALSE` string instead of a clickable box). Fixed by having `EdgeSource.post_upload` reset the basic filter (`client.set_basic_filter(tab, _FILTER_RANGE)` -- `setBasicFilter` always replaces wholesale, clearing any sort/hidden-value state) immediately before the checkbox-validation call, every sync -- consistent with `write_tab` already rewriting the whole tab fresh every time, so a person's previous sort/filter choice doesn't survive a sync anyway. Repaired live by the same sequence: reset the filter, then reapply `set_checkbox_validation` across the full column, verified via a direct `dataValidation` metadata read-back (not the browser) showing all 742 rows correctly validated and the same 5 ticks intact. Lesson: any future structural `batchUpdate` write (data validation, conditional formatting, banding) against a tab carrying a live basic filter should be treated as unreliable while that filter has an active sort, and reset it first.

| 2026-09-14 | `Lineups` | Each of the 20 blocks' `(start, end)` shrunk to the 9 REAL roster rows only -- `end` used to also be that block's totals row, which meant every VLOOKUP-by-name column treated it as a permanently-`#N/A` tenth player (its own Name cell is always blank). The totals row is now `end + 1` everywhere, never `end` itself. | block *n*: rows *start*..*start+9* (9 slots + totals row as the 10th) | block *n*: rows *start*..*start+8* (9 slots only); totals row is `weekly_reset.LINEUPS_TOTALS_ROWS[n]` (= `end + 1`) | Live + Template | `weekly_reset.LINEUPS_NAME_BLOCKS` (every tuple's `end` shrunk by 1), new `weekly_reset.LINEUPS_TOTALS_ROWS`, `sheet_style._slot_check_formula`/`_totals_check_formula` (dropped their `end - 1` "back out the real last slot" math -- `end` already *is* the last real slot now), `sheet_style.polish_guardrails`'s per-block loop (`range(start, end)` -> `range(start, end + 1)`, totals formula written at `end + 1` not `end`), `cli.py`'s `lineups_last` (now `max(LINEUPS_TOTALS_ROWS)`, not `max(end for _, end in LINEUPS_NAME_BLOCKS)`, so the final totals row still gets FIELD_FORMATS/colour scales). New `sheet_style.polish_lineups_totals_rows`: clears the now-provably-dead VLOOKUPs on every totals row (`Venue`, `OppPosRank`, `Val`, and the whole `sheet_links.LINKED_EDGE_COLUMNS` block), sums `Ceil` there the same way `Pts`/Salary already were, and labels the row (`Total` beside the Salary sum, `Remaining` beside the remaining-cap formula -- both found by header name, not hardcoded letters). `sheet_protection.py`/`sheet_typo_guard.py`/`polish_lineups_input_column`'s per-block ranges needed no code change at all: they already derived their bounds from `LINEUPS_NAME_BLOCKS` directly, so they became correct automatically (the totals row's Name cell is no longer left typeable/typo-guarded, since it's simply outside every block's range now) the moment the constant changed. |

**Verification, before either sheet was touched:** every consumer of `LINEUPS_NAME_BLOCKS` (8 files: `doctor.py`, `late_swap.py`, `cli.py`, `sheet_pool_deck.py`, `sheet_protection.py`, `sheet_typo_guard.py`, `weekly_reset.py`, `sheet_style.py`) was read and individually reasoned about before running anything live -- exactly the audit this fix's own commit message asked for. This caught a real bug before it ever reached a live cell: `polish_guardrails`'s per-block loop still assumed `end` was the totals row (`range(start, end)` for the 9 slots, then the totals formula at `end`) -- under the new meaning of `end`, that would have wiped the 9th roster slot's guardrail formula and left the real totals row with none. Fixed in the same pass, before the first live run, and pinned with a unit test asserting the exact formula text at both the 9th slot and the totals row.

| 2026-09-15 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | Phase 3: `derived.EDGE_COLUMNS` and the three formula-bearing tabs all reordered ONCE into a designed IDENTITY/DECISION/GAME/WEATHER/MOVEMENT/INTERNAL grammar (see `sheet_columns.py`), with four blank `SoS n` columns reserved in GAME for the strength-of-schedule work landing later -- the reorder this whole hazard class exists to warn about, done deliberately instead of piecemeal. `sheet_links.link_edge_columns` rewritten to find each linked column BY HEADER NAME (new `LINKED_EDGE_COLUMNS`, 10 -> 16 members: added `Id`/`OwnPct`/`ImpMove`/`TotMove`/`SpdMove`/`GameStart`) instead of assuming one contiguous appended block; new `sheet_native_links.py` regenerates Player Pool/Lineups' hardcoded VLOOKUP indices into PlayerPoolRaw the same way; new `column_reorder.py`/`sheet_reorder.py` do the actual `moveDimension` sequencing (`dfs setup reorder-columns`). | EdgeRaw: `Id` column B (hidden), append-only history below it. PlayerPoolRaw/Player Pool/Lineups: native columns first, `LINKED_EDGE_COLUMNS` (10) appended past them. | EdgeRaw: designed order, `Id` moved into the INTERNAL zone near the end (still individually hidden, found by name). Other three tabs: `sheet_columns.PLAYER_POOL_RAW_COLUMN_ORDER`/`PLAYER_POOL_COLUMN_ORDER`/`LINEUPS_COLUMN_ORDER` exactly. | Live + Template | `derived.EDGE_COLUMNS`, new `sheet_columns.py` (whole module), `sheet_links.LINKED_EDGE_COLUMNS`/`link_edge_columns` (rewritten), new `sheet_native_links.py`, new `column_reorder.py`/`sheet_reorder.py`, `sheet_style.EDGE_COLUMN_GROUPS`'s comment (Id no longer physically beside Pool), `doctor._check_linked_edge_columns` (contiguity check -> presence+uniqueness per name) |

**Six more bugs found during this migration's own live verification, every one the same hazard class (something hardcoded a position that used to be true) -- none caught by any test until the reorder made the assumption false:**

1. **`sheet_style.polish_guardrails` hardcoded `_GUARDRAILS_COLUMN = "O"`** -- true only because "Issues" happened to sit at O pre-reorder. Post-reorder, O held `Flag`; every `dfs setup polish` run clobbered Flag's real EdgeRaw formula with a duplicate copy of the guardrails header/formulas. `_totals_check_formula` had the same problem one level down (hardcoded `D` for the Salary cap check). Fixed: both now find their column by header name (`"Issues"`, `"DK Sal"`) every call.
2. **`sheet_pool_formulas.py` hardcoded `_SOURCE_COLUMN="O"` / `_OVERFLOW_COLUMN="Z"` / `_POOL_TYPE_COLUMN="AA"`** on Player Pool -- true under the old append-only layout; post-reorder those letters hold `Flag`/`Roof`/`Wind`. Caught by a codebase-wide sweep for the same hazard shape *before* `dfs setup add-pool-picks` was ever run live, not by a live incident. Fixed: all three found by header name (`"Source"`/`"Overflow"`/`"Pool"`), raising a clear error if any is missing rather than guessing.
3. **`sheet_pool_resize.py` hardcoded `_LAST_FORMULA_COLUMN = "Y"`** in `grow_block` (a one-time Player Pool block-growing tool, not currently wired into any CLI command) -- same sweep, same fix (derived from the tab's own header width each call).
4. **Lineups' 19 repeated header rows had silently drifted from the real header on the *template*** -- unrelated to anything this session touched (confirmed: the live sheet's repeats were all in sync), invisible because `doctor` only ever checked column A there. `moveDimension` carried the drift forward unchanged rather than fixing or worsening it. Fixed at the root: new `sheet_reorder.resync_header_repeats`, now called unconditionally at the end of every `migrate_tab_to_designed_order`, so a repeat row can never stay out of sync past the next reorder.
5. **Three adjacent `addDimensionGroup` calls (WEATHER/MOVEMENT/INTERNAL, now back-to-back in the designed order) silently merge into one group at the API level** -- the second and third `group_columns` calls' own collapse-fold request then fails outright ("no group spans exactly that range"), since Sheets extended the first group instead of creating independent ones. Only ever hit once nothing sat between the three zones any more. Fixed: `link_edge_columns` now merges adjacent zone ranges into one combined group before calling `group_columns`, instead of assuming each zone groups independently.
6. **Every linked/native lookup formula lacked a blank-name guard** (`edge_lookup_formula`/`native_lookup_formula`, both pre-dating this phase) -- an unfilled Player Pool/Lineups slot's Name cell is blank, and `VLOOKUP` against a blank key resolves to `#N/A` rather than blank, unlike `sheet_pool_formulas.py`'s own Source/Pool columns (which always had the guard). Cosmetic under the old layout (nobody looked closely at a mostly-full pool); glaring after the reorder put fresh eyes on every column. Fixed: both formulas now wrap in `IF($A<row>="","",...)`; refreshing the already-correct-position formulas required a new `link_edge_columns(..., force=True)` (`dfs setup link-edge --force`), since the normal "something's missing" trigger never fires on an already-fully-linked tab.

**Two more, found spot-checking real values after the above (3.5's mandate) rather than in the reorder itself:**

7. **The Lineups totals row's Name cell (column A) still carried the same input background and typo-guard player dropdown as a real roster slot** -- a leftover from the 2026-09-14 fix above shrinking each block's own range to exclude the totals row, never retroactively cleaned off the row it stopped covering. Fixed: `polish_lineups_totals_rows` now clears data validation and resets the background on every totals row's Name cell.
8. **A separate, genuinely hand-authored row directly below each totals row** (the "average remaining per slot" helper, documented in `docs/SHEET_REFERENCE.md`, never written by any `dfs` command) **reads Venue's totals-row cell via `INDIRECT("E"&(ROW()-1))`** -- a string-built reference `moveDimension` cannot see or retarget. The first fix for `polish_lineups_totals_rows`'s stranded "Remaining" (item 6's neighbor problem, not listed separately above: Spread moving into the GAME zone left the old hand-authored remaining-cap formula and its label a dozen columns from the Salary sum they describe) moved a self-labeled *text* formula onto that exact cell, breaking the INDIRECT row with `#VALUE!` -- found live, from a screenshot, immediately after that first fix shipped. Fixed for real this time: Venue's cell holds a bare number again (required), "Total" moved to Opp.'s column, "Remaining" (the text label) moved to Val's column -- both dead-on-a-totals-row cells found by name, neither the one column an unrelated formula depends on by hardcoded letter.

**A ninth, unrelated to the column reorder but found the same day chasing a live data-quality symptom:** `EdgeRaw`'s Salary/Val looked wrong for real players (verified: `derived.build_edge_frame`'s projections<->salaries join was matching 0 of 744 IDs, not a subset -- the two local `data/current/*.csv` caches were each internally valid but for different weeks, left over from *before* a full `dfs sync` had run this week). New `store.clear_current()`, called from `dfs week new` right alongside the existing `weekly_reset.clear_synced_tabs` (Fix 2.14, same reasoning): a source that hasn't synced yet under the new week now reads as no-data (`FileNotFoundError`) rather than silently returning a previous week's now-mismatched numbers. Separately, `dfs sync` with no `--week` override auto-detected "week 1" on the last calendar day of week 1 even though the sheet itself was already named "Week 2" for the upcoming slate -- a reminder that `--week` should be passed explicitly when prepping a sheet ahead of the auto-detected week, not a code bug.

**A tenth, found restoring Pool ticks after the week-2 re-sync above:** `Id`'s column had no `FIELD_FORMATS` entry, so it silently inherited a stray `"0.0"` number-format pattern from wherever it sat before Phase 3 moved it -- every Id rendered as e.g. `"44133074.0"` instead of `"44133074"`, which broke `sources/edge.py`'s Pool-tick preservation across a sync (it matches ticks by this exact string) even after the tick-preservation position bug below was independently fixed. Fixed: `FIELD_FORMATS["Id"] = _num("0")`, a plain integer pattern set explicitly on every polish run like every other field.

**An eleventh, a genuine live data-loss incident, found when a routine `dfs sync --only edge` wiped 43 real Pool ticks:** `sources/edge.py`'s `EdgeSource.pre_upload` captured each row's Id via the module-level `_ID_COLUMN` constant -- derived from `EDGE_COLUMNS`' *target* order, correct for `post_upload` (which reads back *after* `write_tab` has rewritten the tab to match) but wrong for `pre_upload`, which runs *before* that rewrite while the sheet still has Id at its *old* position. It silently captured whatever field used to sit at Id's new position under the old layout as if it were the Id list, so nothing in the freshly-written sheet matched any preserved key. No local backup existed to recover the 43 ticks from (Pool state lives only in the sheet). Fixed: `pre_upload` now reads the tab's own current header and finds Id by name, same as everything else in this file.

**A2 diagnosis (2026-09-16, no code change):** Sam reported Lineups' highlighting missing while EdgeRaw looked correct. Read the live sheet's actual `conditionalFormats` metadata directly rather than guessing: all 47 rules on `Lineups` already spanned the full `header_row+1 : max(LINEUPS_TOTALS_ROWS)` range (all 20 blocks), built from `cli.py`'s own `LINEUPS_NAME_BLOCKS`-derived `header_row`/`last_row` -- never a literal. The symptom was stale state from before this session's own `dfs setup polish` reruns (each of which does a whole-tab `clear_conditional_formats` + rebuild), not a structural bug that can recur -- confirmed by reading cells back, not by trusting `polish`'s own "OK" output. No fix needed; ranges were already correctly derived.

| 2026-09-16 | `Player Pool` | A3: one row inserted at the very top (`sheet_pool_control.ensure_pool_control_row`, a real `insertDimension`) -- an "add a player" control (`A1` label, `B1` a live search box against EdgeRaw's Name column) replacing the separate `Pool Picks` tab. The real header and every position block shifted down by 1. | header row 1; blocks at `(2,11) (13,32) (34,58) (60,69) (71,80)` | header row 2; blocks at `(3,12) (14,33) (35,59) (61,70) (72,81)` | Live + Template | `weekly_reset.PLAYER_POOL_NAME_BLOCKS` (+1 throughout), new `weekly_reset.PLAYER_POOL_HEADER_ROW`/`PLAYER_POOL_CONTROL_ROW`, new `sheet_pool_control.py` (whole module), `sheet_pool_formulas.py`'s `_union_array`/`_source_formula` (control cell replaces Pool Picks' 100-row range), `sheet_pool_deck.py`'s `_build_pool_sort`/`_write_deck_controls` (Player Pool header/data-start row, was hardcoded 1/2), `sheet_links.link_edge_columns`/`sheet_reorder.migrate_tab_to_designed_order`/`sheet_pool_formulas.write_pool_formulas` call sites in `cli.py` (all now pass `header_row=PLAYER_POOL_HEADER_ROW` for Player Pool instead of relying on the default of 1), `doctor.run_doctor`'s new override of `list_tabs()`'s always-row-1 header for Player Pool (same fix Lineups already needed for the same reason -- see the 2026-09-06 Bench incident above), `sheet_audit.AUDITED_TABS`' Player Pool entry, `sheet_protection.py`'s `FULLY_PROTECTED_TABS` (Player Pool moved out, now protected-except-the-control-cell like Lineups/Exposure) |
| 2026-09-16 | `Lineups` | Phase 5, user feedback after a week of real use ("I've used it week 1 and it was a pain... Cut it"): the pool deck -- the 10 frozen rows at the top of Lineups this whole changelog section's earlier rows built, resized, and resized again -- is REMOVED ENTIRELY (`sheet_pool_deck.remove_pool_deck`, a real `deleteDimension`), not just shrunk. The real header and every lineup block shifted UP by 10, back to their pre-deck positions. `PoolSort` (the hidden helper tab the deck's window formulas read from) deleted outright. | header row 11; blocks at `(12,20) (25,33) ... (259,267)` | header row 1; blocks at `(2,10) (15,23) ... (249,257)` | Live + Template | `weekly_reset.LINEUPS_NAME_BLOCKS` (-10 throughout, the fourth shift: +7, +14 net, -4, now -10), `sheet_pool_deck.py` gutted to just `remove_pool_deck`/`DECK_ROWS`/`POOL_SORT_TAB` (`add_pool_deck`, `_build_pool_sort`, `_write_deck_controls`, `_write_deck_scale_helpers`, `MIN_HELPER_ROW`/`MAX_HELPER_ROW`/`_WINDOW_SIZE` all deleted), `sheet_style.py`'s `apply_deck_color_scales`/`polish_pool_deck` deleted, `polish_builder_tab`'s Lineups call in `cli.py` no longer overrides `freeze_rows`/`freeze_cols` (both back to the tab's own defaults -- Name is pinned again), `doctor.py`'s `_check_pool_deck_range`/`_check_deck_block_alignment` deleted (the latter's still-relevant half, "row 1 says Name", was already redundant with `_check_lineups_header_repeats`) along with its now-unnecessary Lineups-header-row override in `run_doctor` (list_tabs()'s own row-1 read is correct again), `sheet_audit.AUDITED_TABS`' Lineups entry (was hardcoded `11` -- a literal that had shipped despite this file's own "never hardcode" rule, caught only by a real `dfs setup audit-style` run against a live sheet, not by any test), `sheet_audit.FREEZE_OVERRIDES` emptied, `sheet_protection.py`'s Lineups carve-out (dropped `B1`/`D1`/`F1`, the deck's own controls). `Lineups!H1` (Phase 5A's Exposure divisor, previously the one free cell in the deck's row-1 strip) relocated to **`Exposure!H1`** itself -- `sheet_views.LINEUP_COUNT_CELL`/`DEFAULT_LINEUP_COUNT` (moved from `sheet_pool_deck.py`), `build_exposure` now reads/writes/notes/validates it directly as part of building its own tab, `doctor._check_lineup_count_cell` now checks `Exposure` not `Lineups`, `sheet_protection.py`'s Exposure carve-out gained it alongside Target. Verified live on both sheets: real header/column-group/rule-count reads after removal, plus a live 3-lineup/`H1`=6 -> exactly 50.0% Exposure re-check post-move (same value as Phase 5A's original verification, now on the new tab). |
| 2026-09-16 | `EdgeRaw` | Phase 5, Section J: `CeilPct`/`OwnPct` moved out of the tail INTERNAL zone to sit beside their raw counterparts (`Ceiling`/`CeilVal` and `ProjOwn`), so they're visible (and already position-fair) while filtering EdgeRaw down to one position -- Sam: "I usually filter this by position(s) an scan it tht way." Real `moveDimension` on the LIVE sheet's data (via a full `dfs sync --only edge` rewrite, since EdgeRaw is wholesale Python-generated) and via two explicit `SheetsClient.move_columns` calls on the TEMPLATE (whose EdgeRaw holds stale sample data never touched by `dfs sync`). | `CeilPct`/`OwnPct` at `EDGE_COLUMNS` index 25/26 (tail, beside `Id`/`LevBasis`) | `CeilPct` at index 9 (right after `CeilVal`), `OwnPct` at index 11 (right after `ProjOwn`) | Live + Template | `derived.EDGE_COLUMNS` (reordered), new `SheetsClient.delete_columns` (unrelated to this move itself, added alongside for Section K below), `tests/test_sheet_links.py`'s `test_already_linked_columns_positions_never_move`/`test_edge_lookup_formula_uses_correct_range_and_column_index` (both pin `EDGE_COLUMNS.index(...)` values that shift with any EdgeRaw reorder -- updated, not just re-asserted, per their own docstrings' instructions). `dfs setup link-edge --force` re-run on both sheets (regenerates every VLOOKUP's hardcoded column-index integer -- the normal "already linked" skip never fires just because a linked column's SOURCE position moved), then `polish_edge` re-run on live (a full `dfs setup polish` was started first but killed after 25+ minutes stuck in Google's own rate-limit backoff from this session's accumulated API traffic -- `polish_edge` alone, called directly, finished in about a second: same fix, far fewer requests). Template's own gradient/chip rules needed no separate re-polish -- confirmed live that Sheets' `moveDimension` carries a conditional-format range along with the column it colors, the same guarantee `move_columns`'s own docstring already documents for formula ranges. |
| 2026-09-16 | `PlayerPoolRaw` / `Player Pool` / `Lineups` | Phase 5, Section K: the four reserved, always-blank `SoS 1`/`SoS 2`/`SoS 3`/`SoS 4` GAME-zone columns (Phase 3 headroom, never wired to anything) are REMOVED ENTIRELY, not filled -- Sam, once the real per-player SoS sync (Section I) was live: "Why still sos 1-4. Should only be one per player." New `SheetsClient.delete_columns` (`deleteDimension` on COLUMNS, mirrors `delete_rows`) and `sheet_reorder.remove_header_columns` (finds each name by CURRENT header position, deletes right-to-left so removing one can't shift the still-pending indices for the others, no-ops per-name if already absent -- same idempotent-migration contract as `sheet_pool_deck.remove_pool_deck`), exposed as `dfs setup remove-sos-placeholders`. | `GAME` zone: `..., OppPosRank, SoS 1, SoS 2, SoS 3, SoS 4, Stadium, ...` (PlayerPoolRaw 34 cols, Player Pool 40, Lineups 37) | `..., OppPosRank, Stadium, ...` (PlayerPoolRaw 30, Player Pool 36, Lineups 33) | Live + Template | `sheet_columns.GAME` (4 names dropped), `sheet_style.BUILDER_WIDTHS` (`SoS 1..4` width entries removed). **One real bug found running this, caught only by passing the wrong `header_row` and then checking the actual live header afterward, not by trusting the command's own "skipped" output as success:** Player Pool's real header sits at `PLAYER_POOL_HEADER_ROW` (2, since row 1 is the "Add a player" control cell) -- the command's first draft defaulted every tab to `header_row=1`, so on Player Pool it read the control row, concluded (wrongly) that none of the four columns were present, and silently no-op'd instead of removing them. PlayerPoolRaw/Lineups (both real header at row 1) worked correctly on the first pass, which is exactly what let the Player Pool miss look like success. Fixed before touching the live sheet -- caught on the TEMPLATE run (per the mandated template-first order), verified by re-reading Player Pool's actual row-2 header afterward and confirming all four names were gone, then re-run clean against live. `dfs doctor`/`dfs setup audit-style` clean on both sheets afterward; `OppPosRank`'s own real values (and EdgeRaw's separately-restored `Pool` ticks, see Section J's own incident note below) re-verified unaffected by the column shift. |

Also part of this change, additive (no position moved): a new `"Edge ↗"` column on both `Player Pool` (right after `Source`) and `Lineups` (right after `Issues`) -- a `HYPERLINK` jumping straight to that player's row on `EdgeRaw` (`sheet_links.edge_row_hyperlink_formula`/`write_edge_row_links`, wired into `dfs setup link-edge`). `PLAYER_POOL_COLUMN_ORDER` grew from 37 to 38 columns, `LINEUPS_COLUMN_ORDER` from 36 to 37; both provisioned/positioned via the existing `dfs setup reorder-columns` machinery (`sheet_reorder.migrate_tab_to_designed_order` already knows how to insert and place a new name in the designed order, so no new insert-column code was needed).

**A3.2 scoped out, not built:** the original ask included a per-row checkbox on Player Pool to "drop" a player from the pool for one week without unticking them in EdgeRaw. Worked through with Sam live: a checkbox bound to a *visible* row of the computed (SORT/FILTER-spilled) pool list would have to feed back into that same spill's own exclusion condition -- a circular reference Sheets can't evaluate. Binding it to a *stable* candidate rank instead avoids the circularity but detaches the checkbox from whatever player is actually shown next to it after any drop, since dropping someone reflows every row below it -- exactly the class of silent mismatch this codebase's whole design philosophy exists to prevent. The only clean non-circular alternative (a small typed "drop list," mirroring the add-a-player mechanism) was explicitly declined -- Sam: "I don't want a new sheet... if we have to make a new table just to remove from the pool, that's too much." Confirmed separately that deleting/clearing a cell in Player Pool's `Name` column directly doesn't work either, since it's a single spilling array formula per block: editing a non-anchor cell is rejected by Sheets, and clearing the anchor cell wipes the whole block. Net: no in-place removal exists or is planned; removing a player stays "untick Pool on EdgeRaw," made faster by the new `Edge ↗` column above rather than replaced.

**Phase 4 (2026-09-16): highlighting rebuilt on a metric that now means something.** Phase 1 made `Leverage`/`OwnPct` real, and Phase 3's reorder put every position/lineup block on fixed row ranges -- but the colour scales themselves still hadn't caught up: one gradient per column, computed across the WHOLE tab, meant a DST's real ceiling of 10 and a QB's real ceiling of 27 shared one scale and every DST rendered red. No structural move (no row/column/tab position changed), so this isn't a Structural changelog table row -- but it touched enough real behavior, and surfaced two genuine Sheets API constraints neither obvious nor documented, to warrant its own entry.

1. **4.1/4.2 -- per-position (Player Pool) and per-lineup (Lineups) colour scales.** New `sheet_style.apply_grouped_color_scales`: one gradient rule per `(scaled column, group)` pair instead of one per column tab-wide, each scoped to that group's own actual min/max. `Player Pool` scales 14 columns x 5 position blocks = 70 rules; `Lineups` scales the same 14 columns x 20 lineup blocks = 280 rules (totals rows excluded -- a sum isn't comparable to the 9 real picks it's a sum of). `EdgeRaw` (sorted by Leverage, not grouped by position, so it has no "group" to scale within) instead skips its raw player-performance metrics entirely (new `EDGE_UNSCALED_PLAYER_METRICS` = ProjPts/Ceiling/Val/CeilVal) and relies on the already-percentile `CeilPct`/`OwnPct`/`Leverage` (newly added to `FIELD_COLOR_SCALES`) as the position-agnostic substitute -- recommended over adding new percentile columns for Pts/Ceil, since those already existed. The reverse exclusion (new `GROUPED_TAB_UNSCALED_COLUMNS` = CeilPct/OwnPct) keeps Player Pool/Lineups from also scaling those two redundantly now that they scale the real metrics properly per group -- both sit in the collapsed INTERNAL zone there and aren't worth 5-20 more rules per column for a chip nobody's looking at.
2. **4.3 -- verified live, not assumed: a single gradient rule cannot independently scale multiple groups, even with a formula-anchored endpoint.** Built a real test on the template's `Scratch` tab: one rule spanning two disjoint blocks (values 1-5 and 100-500), minpoint anchored via `=MIN($Z$600:$Z$604)` (block 1's own range only). Read back the resolved `effectiveFormat.backgroundColor` for every cell: block 1 rendered correctly against its own min, but block 2 rendered against block 1's min combined with the RULE'S OWN true max (500, since maxpoint was left at the default MAX type, which resolves against the rule's *entire* range) -- a smeared, wrong hybrid, not two independently-scaled blocks. Confirms minpoint/midpoint/maxpoint are each evaluated ONCE per rule, never per-cell-relative like a custom boolean formula. There is therefore no rule count below `len(groups) x matched_columns` for this kind of scaling -- 4.3's "try a formula-anchored single rule first" is answered: it doesn't work, not "it wasn't tried."
3. **Rule-count mitigation: batched conditional-format writes.** 280 individual `addConditionalFormatRule` calls (Lineups alone) would have meant 280 sequential HTTP round-trips through `BackOffHTTPClient`'s retry/backoff -- a real risk of `dfs setup polish` taking many minutes or tripping the write-quota window it already runs close to. New `SheetsClient.add_color_scales`/`add_boolean_rules` each issue ONE `batchUpdate` covering every rule; `sheet_style._scale_rule_specs` was split out of the old `add_color_scale`-calling code specifically so `apply_field_color_scales` (a handful of whole-tab rules, calls the client directly) and `apply_grouped_color_scales`/`apply_deck_color_scales` (hundreds of rules, collect specs and batch) can share the exact same rule-building logic without duplicating it. `_gradient_rule`/`_boolean_rule` in `sheets.py` do the same extraction one level down, so `add_color_scale` and `add_color_scales` (singular/batch) build byte-identical rule shapes.
4. **4.4 -- the deck window, and a second live-verified Sheets constraint.** The window (rows 4-9, whatever 6 players are currently visible) used to scale against its own 6 cells, re-scaling misleadingly as `Start at` paged through a position. Fix: scale against `PoolSort`'s full range for the current position instead (`apply_deck_color_scales`). First attempt anchored the gradient directly at a cross-sheet formula (`min_value="=MIN('PoolSort'!$B$2:$B$81)"`) -- rejected outright live: `APIError: [400] Invalid InterpolationPoint.value`. Confirmed via a second real test (both quoted and unquoted sheet-name forms, both same-tab and cross-tab) that a gradient rule's `NUMBER`-type endpoint **cannot reference another sheet at all**, same-tab formulas working fine otherwise. Fixed by indirection: two of the deck's existing blank separator rows (row 2, row `DECK_ROWS`) now hold real `=MIN(PoolSort!...)`/`=MAX(...)` formulas per scaled column (white-on-white, same technique as `G1`'s own hidden helper -- new `sheet_pool_deck.MIN_HELPER_ROW`/`MAX_HELPER_ROW`, `_write_deck_scale_helpers`), and the gradient rule anchors at THOSE same-tab cells (`=$G$2`/`=$G$10`) instead. The helper cells are live formulas, not snapshotted values, so they self-update without needing to be rewritten on every `dfs setup polish` run -- only `dfs setup add-pool-deck` (idempotent, already part of the standard build order) needs to have run at least once since this shipped.
5. **A live regression found and fixed while re-running `add-pool-deck` to test the above:** every hardcoded `...Z...`/26-column-wide range in `sheet_pool_deck.py` (`_build_pool_sort`'s Player Pool header read AND its own SORT/FILTER formula's range, `_write_deck_controls`'s row-3/window writes, `_reset_deck_formatting`'s reset span) broke or silently truncated the moment Player Pool/Lineups genuinely exceeded 26 columns (38/37, post-A3) -- a **pre-existing regression from A3, not from Phase 4**, just never re-triggered until this session ran `add-pool-deck` again. The header READ silently truncated Player Pool's header to its first 26 names (missing columns just don't match anything, no error); the SORT/FILTER formula's own `$A:$Z` bound silently dropped every Player Pool column past Z from `PoolSort` entirely (Overflow, Pool, half of INTERNAL); the row-3/window WRITES errored loudly (`Requested writing within range [...Z...], but tried writing to column [AA]`), which is what actually surfaced this. Fixed at the root: every read now uses the bare `f"A{row}:{row}"` idiom (no end column) already standard elsewhere in this codebase for "the whole header row," and every write derives its own `last_col` from the real header's length. `_reset_deck_formatting` (which runs before any header has been read) widened to a flat `A:AZ` (52 columns) instead -- deliberately generous headroom, same reasoning as `EDGE_ROWS`/`POOL_RAW_ROWS`, since it has no width of its own to derive from at that point in the sequence.
6. **4.5 -- zero-exclusion (Fix 2.7) re-verified per group, not just whole-tab.** `Rstr%`'s `MINIFS` formula and grey zero-chip are both correctly re-scoped to each group's own range under `apply_grouped_color_scales` (confirmed live: `M3:M12` for the QB block, `M14:M33` for RB, etc. -- never the whole column) rather than leaking one position's unpublished-ownership zeros into another's scale.

Final rule counts, read back live on both sheets (not asserted from the code): `EdgeRaw` 10 gradient rules (was 12 pre-Phase-4: -4 raw metrics, +2 CeilPct/OwnPct), `PlayerPoolRaw` 16 (whole-tab, unaffected -- gained CeilPct/OwnPct same as everywhere), `Player Pool` 70 gradient + 5 zero-exclusion chip = 75, `Lineups` 280 gradient + 20 zero-exclusion chip = 300, deck window 14. `dfs doctor` and `dfs setup audit-style` both clean on Player Pool/Lineups on both sheets after; spot-checked live via `conditionalFormats` metadata (not the CLI's own "OK" output) that the Pts column genuinely carries 5 independent rules on Player Pool at exactly `PLAYER_POOL_NAME_BLOCKS`' row ranges, and that the deck's Pts rule anchors at `=$G$2`/`=$G$10` rather than a range of its own.

**Phase 5 (2026-09-16), Section A: the Exposure divisor was a live bug, not a refactor.** `sheet_views.build_exposure` divided each player's lineup count by `lineup_count` (the sheet's fixed 20-lineup CAPACITY, `len(LINEUPS_NAME_BLOCKS)`), not by how many lineups Sam actually builds in a given week (4-8) -- a player rostered in every one of a 6-lineup build read as 30% exposure, not 100%. No structural move (no row/column/tab position changed): fixed by adding a new typed value at `Lineups!H1` (the one free cell in the deck's existing row-1 control strip -- `sheet_pool_deck.LINEUP_COUNT_CELL`, default 6, a cell note carrying the label since there's no room for a separate label cell, warn-only 1-20 range validation) and changing the Exposure formula's divisor to `IF(N(Lineups!$H$1)>0,N(Lineups!$H$1),<capacity>)`. Verified live: 3 lineups against `H1`=6 read back as exactly 50.0%, not asserted from the formula text. `doctor` gained a matching check that `H1` is numeric and in range. `H1` is deliberately NOT cleared by `weekly_reset.clear_previous_week` -- it's still the right number until Sam changes it, unlike a stale pick.

**Phase 5, Section B: `Used`/`In` appended to `Player Pool`.** Additive (no position moved, per `sheet_links.link_edge_columns`'s own append convention) -- new `sheet_pool_usage.py` (`write_pool_usage_columns`) writes two native formula columns past `Pool` (Player Pool's previous last column): `Used` (`=COUNTIF(Lineups!$A:$A,name)`, deliberately the WHOLE column since a repeated sub-header row's own column A always holds the literal text "Name", which can never collide with a real pick) and `In` (which lineups, `L1, L3, ...`, one `TEXTJOIN`'d `IF(COUNTIF(...))` term per `LINEUPS_NAME_BLOCKS` entry, generated in Python from that constant -- never hand-typed, with a length-checked fallback to leaving `In` blank if a much larger `LINEUPS_NAME_BLOCKS` ever pushed the generated formula past Sheets' real ~50,000-character ceiling). `PLAYER_POOL_COLUMN_ORDER` grew from 38 to 40 columns; provisioned via the existing `sheet_reorder.provision_missing_columns` machinery, called from `write_pool_usage_columns` itself rather than requiring a separate `reorder-columns` run first. `Used` added to `FIELD_COLOR_SCALES` (gradient, scaled per-position-block like every other Player Pool metric) and `ZERO_EXCLUDED_COLUMNS` (zero is this column's overwhelmingly common value -- an unrostered pool player is normal, not the bottom of a scale). Wired into `dfs setup link-edge` (the same "refresh native per-row formulas" moment `write_edge_row_links` already runs at), not a new standing command.

**Phase 5, Section C: Player Pool/Lineups given the rest of EdgeRaw's own look.** No structural move. `polish_builder_tab` (used by all three of PlayerPoolRaw/Player Pool/Lineups) previously covered `FIELD_FORMATS`/colour scales/Flag-Avail-Source-Venue-Pool chips -- but not `polish_edge`'s Wind chip, per-position tint, `LevBasis` grey freshness marker, or Name-bold-on-Flag, which had been EdgeRaw-only. Extracted those four into shared helpers (`_apply_wind_chip`/`_apply_position_tint`/`_apply_lev_basis_marker`/`_apply_name_flag_style`, all header-NAME-driven) that `polish_edge` and `polish_builder_tab` now both call, rather than writing a second copy -- `polish_edge` itself was refactored to call them too, so there is exactly one implementation of each going forward. `_apply_name_flag_style` takes an optional `pool_column`: EdgeRaw (the one tab spanning both pooled and unpooled players) still gets the three-way pooled/flagged tint; Player Pool/Lineups/PlayerPoolRaw (every row already someone's pool pick or roster slot -- no "unpooled" row to distinguish) get a plain bold-on-Flag instead.

**Phase 5, Section D: a `Lineups`-only "Vegas" column group.** No structural move (grouping is presentation metadata, not a row/column/tab position change). New `sheet_links.group_lineups_columns`, called right after `link_edge_columns` in `dfs setup link-edge`, Lineups only: groups `O/U`/`Spread`/`Team Implied` behind their own +/- control, verified by real column NAME against the live header (the task's own original "E:H" letter guess was checked against the current designed order and found stale -- those three columns actually sit at `R:T` post-Phase-3). Also fully recomputes the pre-existing WEATHER/MOVEMENT/INTERNAL merge `link_edge_columns` already applies, because `SheetsClient.clear_column_groups` wipes EVERY group on a tab -- anything added after `link_edge_columns` runs would be silently destroyed the next time it re-runs unless one function owns the full recreate, every time. A second, originally-requested group (`GameEnv` onward, provisionally labeled "Venue") was **not built**: `GameEnv` sits immediately adjacent to the already-collapsed WEATHER/MOVEMENT/INTERNAL block, and Sheets does not keep two `addDimensionGroup` calls at the same depth independent when their ranges are adjacent -- it silently EXTENDS the first to cover the second (the same failure mode `link_edge_columns`' own merge logic already works around for its three zones, re-verified live here). Left visible instead of force-merging it into one much larger group; see `docs/planning/ROADMAP.md`'s "Proposed, awaiting a decision" for the costed writeup.

**Phase 5, Section F: `ImpMove` -> `ImpliedMove` (rename in place -- no VLOOKUP index or range shift).** The literal instruction ("rename `LineMove` to `ImpliedMove`") was itself stale: a prior session had already split the old `LineMove` into `ImpMove`/`TotMove`/`SpdMove` (2026-09-15's Phase 3 reorder), so the rename actually applied was `ImpMove` -> `ImpliedMove` -- the same ambiguity concern Sam raised, one abbreviation short of fully resolved. Every reference renamed together: `derived.EDGE_COLUMNS`/`_attach_line_movement`/`_flag_for_row`, `sheet_columns.MOVEMENT`, `sheet_style.FIELD_FORMATS`/`FIELD_COLOR_SCALES`/`EDGE_WIDTHS`, `sheet_views.build_movement`, `docs/CALCULATIONS.md`, `docs/SHEET_REFERENCE.md`. `LINE_MOVE_FLAG_THRESHOLD`/the `LINE↑`/`LINE↓` flag text were deliberately left as-is (Section F's own instruction -- the flag is about a line moving, still accurate). `doctor._check_edge_header` needed no code change (it already compares against `EDGE_COLUMNS` by name). Beyond the rename, `sheet_views.build_movement` was rebuilt to show all three prose columns ("Implied move"/"Total move"/"Spread move") instead of just the one ambiguous "Line Move" header it shipped with -- sorting/filtering stays on `ImpliedMove` alone (what `LINE↑`/`LINE↓` actually keys off), `TotMove`/`SpdMove` ride along as extra display columns once a row already qualifies. `sheet_style.style_movement` was rewritten header-NAME-driven (it previously hardcoded an A:E, 5-column range, which broke the moment the header could be 4-7 columns wide depending on which optional columns exist).

**A live-verified gap in the rename mechanism itself, found applying the above.** Every column-finding function in this codebase locates a column by NAME -- so simply renaming `ImpMove` to `ImpliedMove` in Python made `link_edge_columns` see the sheet's still-literal `ImpMove` text as a genuinely MISSING `ImpliedMove` column the next time it ran, and try to APPEND a new one past the tab's current width. `PlayerPoolRaw` was already at exactly its provisioned 34-column grid width (fully linked, nothing missing before the rename) -- appending past that raised `APIError: [400] Range (PlayerPoolRaw!AI1) exceeds grid limits` live, on the template, the first time `dfs setup link-edge` ran after the rename. Two fixes: new `sheet_reorder.rename_header_column` does a pure header-text rename IN PLACE (finds the old name, overwrites that exact cell and any `header_repeats_at` rows with the new name, no append, no position change) -- run once against `EdgeRaw`/`PlayerPoolRaw`/`Player Pool`/`Lineups` on both sheets BEFORE `link_edge_columns` ever saw the new name, so it found `ImpliedMove` already present everywhere and correctly reported "already linked" instead of appending. Separately, `link_edge_columns`'s own append path gained the same `ensure_column_capacity` call `provision_missing_columns` already had (it was missing entirely, an independent robustness gap -- any genuinely new linked column added to a tab already at its grid's column limit would have hit this same error, rename or not). Verified live via real header reads (not the CLI's "OK" output) on both sheets before/after: `ImpliedMove` present at its original column position, `ImpMove` gone, `link-edge` re-run afterward reporting "already linked" rather than appending a duplicate.

**Phase 5, Section G: audited every tab `dfs week new` doesn't already clear for stale-data risk (Sam: "New week should clear out everything").** Confirmed already fixed, nothing to do: the Lineups totals-row `Pts` sum and the dead per-slot `VLOOKUP`s on every totals row (both landed in the 2026-09-14 changelog row above). Confirmed still genuinely open, NOT built (needs Sam's sign-off -- both are `EDGE_COLUMNS`/column-count growth): a `Flags` column and Cash-vs-GPP tagging on `Lineups`. Confirmed already correct, contrary to the task's own premise: `derived._flag_for_row` already returns every matching flag space-separated (fixed in an earlier session, "Fix 2.1" per its own docstring) -- there is no first-match-wins bug to fix. Found and fixed live: `EntriesRaw` (hand-pasted DK contest history) was the one other typed tab `clear_previous_week` never cleared -- `GPPin`/`DKLineupsRaw`/`DKLineupsFinal` are confirmed entirely formula-driven off it (2026-09-05's Phase 9 audit already verified this via `value_render_option="FORMULA"`), so clearing it can only ever make their formulas resolve blank, never delete a formula. New `weekly_reset.ENTRIES_RAW_TAB`/`ENTRIES_RAW_RANGE` (`A2:M1000`, the same 13-column shape `DK_UPLOAD_RANGE` already established for "same roster-slot shape as Lineups/DK Upload," per `docs/SHEET_REFERENCE.md`), cleared only if the tab exists.

**Phase 5, Section H: `dfs week new` now clears Bankroll's Cash/GPP contest rows.** Not a structural move -- typed-column clearing, same class of fix as Section G's `EntriesRaw` one. New `weekly_reset._clear_bankroll_bucket`, driven entirely from `cfg.bankroll.cash`/`cfg.bankroll.gpp` (`EntryTableConfig`'s `first_row`/`last_row`/`entry_key_column`), never hardcoded. Clears only columns A-H (the typed entry data, `bankroll.sync_bucket`'s own `_entry_row`) plus the dedupe-key column -- verified live via `read_formula` (not just `read_range`) against a real synced sheet that `I` ("% Paid") and `J` ("Place %") are formulas, never touched. Ordering (carry the Ending balance forward, THEN clear) was already correct in `cli.py`'s existing call sequence before this change; made explicit in `week_new`'s own docstring rather than left implicit. **Surprising finding, not fixed here (out of this section's scope):** the dedupe-key column (`entry_key_column`, configured as `L` for both buckets) is entirely EMPTY on a real, already-synced historical sheet (Week 1, both Cash and GPP buckets, checked via a live read) despite `bankroll.sync_bucket` supposedly writing it alongside every entry -- dedup detection may not actually be working. Not investigated further; flagged for a future session.

**Phase 5, Section H follow-up (2026-09-16): the Section H finding above was real -- Sam confirmed live duplicates from re-running the same sync twice.** Root-caused by replaying Sam's own real DK CSV exports through `bankroll.sync_bucket` locally against a faithful fake Sheets client: `sync_bucket` itself dedupes correctly (`existing_keys` read from `entry_key_column`) -- the bug is that Week 1's rows were written *before* (or by a path that skipped) that column ever being populated, so `sync_bucket`'s own dedupe check can never see them as "already present" and re-appends them on every subsequent sync touching the same contest history. No code defect in `sync_bucket` itself. Fixed with a repair tool rather than a sync-code change, since the sync code was already correct: new `bankroll._row_signature`/`_entry_signature`/`backfill_entry_keys` (matches a blank-keyed row to its CSV entry by Place/Entries/Entry Fee/Prize Pool/Places Paid -- fields that round-trip exactly through Sheets' own display formatting -- and writes the key ONLY on an exactly-one-match; ambiguous or unmatched rows are reported, never guessed at) and a new `dfs bankroll backfill-keys --csv <file>` command. Run live against the real Week 1 sheet (Sam's own already-hand-fixed-for-duplicates CSV, from Downloads): cash 21/24 rows backfilled (2 ambiguous, 1 unmatched), gpp 15/17 backfilled (0 ambiguous, 2 unmatched) -- verified via a real read of column `L`. The 5 rows left unresolved are a real, disclosed gap (ambiguous Place/Entries/Fee combinations, or rows with no matching CSV entry at all), not a bug -- guessing at them would risk silently mis-keying a real entry.

**Phase 5, Section I (2026-09-16): Strength-of-schedule sync -- `SoSQB`/`SoSRB`/`SoSWr`/`SoSTE`/`SoSDef` go from a weekly hand-paste to an automated `dfs sync` source.** Not a structural move -- these five tabs and their column shape already existed (reserved since an earlier session); this fills them with real data instead of a manual copy-paste, the same "additive, no position moved" class as Section B. New `sources/tffb_sos.py`, `TffbSosSource` (one instance per position -- `sos_qb`/`sos_rb`/`sos_wr`/`sos_te`/`sos_dst` in `SOURCES`, D/ST's own TFFB `?position=` value confirmed live as `"D"`, not `"DST"`), reusing the same `dfs auth tffb` session `projections` already depends on -- no new auth flow needed. Reverse-engineered live (Claude-in-Chrome): the page's displayed `Rank`/`FPA` are a pure CLIENT-SIDE recompute the instant a week is clicked (`a.week-button[data-week="N"]`), not a network call and not the large `week_N_fan_duel_<position>_*` blob found first in a `<script>` tag (a red herring -- that blob's fields don't exist on the live table's own row objects at all); the real source is `jQuery('#strength-of-schedule').DataTable().rows().data().toArray()`, read back after the click. **Which week counts as "current" is deliberately NOT `nfl_calendar.current_week()`** -- checked live same-day (a Wednesday between Week 1's finale and Week 2's kickoff): `nfl_calendar.current_week()` correctly returns 1 for its own existing uses (contest-history bucketing, line-movement baselines), but TFFB's own page already defaults to "Weeks 2-18," matching the live sheet's own "Week 2" title. `tffb_sos.py` parses the page's own default filter label instead of trusting `ctx.week` directly (`ctx.week` still compared as a >1-week-gap sanity warning, not a hard override). Sam: "SOS comes from TFFb... SOS 1-4 doesn't make sense. We should show the proper position sos for each player, not all of them" -- confirmed each `?position=` genuinely serves different numbers under the same generic field names (checked live), so five independent per-position `Source` instances was necessary, not five ways of reading one shared payload. No `PAE` field exists anywhere on the page (checked, not assumed) -- the old hand-pasted `PAE` column is dropped from the synced shape (`Team | Team.1 | Rank | FPA | Opp`), rather than invented. Verified live end-to-end against the real Week 2 sheet: `dfs sync --only sos_qb,sos_rb,sos_wr,sos_te,sos_dst` (dry run, then real), all 5 tabs got 32 real rows each, `SoSComb`'s VLOOKUP chain resolves real numbers (no more `#N/A`), and `PlayerPoolRaw`'s `OppPosRank` hit a 100% fill rate across all 658 real player rows (the earlier apparent `#N/A`s were blank rows past real data, not real players). `dfs doctor`/`dfs setup audit-style` both clean afterward on the live sheet; template deliberately left un-synced (per this codebase's own weekly-workflow design, `dfs sync` only ever targets the live sheet). The follow-on question this section originally left open -- whether the four reserved `SoS 1..4` columns were worth filling from this same data -- was answered the same day, not left open: see the two changelog rows below (EdgeRaw's `CeilPct`/`OwnPct` move, then the `SoS 1..4` removal itself).

**Phase 5, Section J incident (2026-09-16): a stale header AND a silent data-loss bug, both found live while moving `CeilPct`/`OwnPct`, neither one caused by the move itself.**

1. **`EdgeRaw!U1` still read `"Wind"` -- twice.** Section F's `ImpMove` -> `ImpliedMove` rename (above) changed `derived.EDGE_COLUMNS` correctly, but the LIVE sheet's own header cell at that position was never actually rewritten with it -- the last `edge` sync in the sync manifest predated the code fix, and nothing re-ran `dfs sync --only edge` afterward to push it. Since EdgeRaw is wholesale Python-generated (`write_tab` clears and rewrites the whole tab, header included, every sync), the fix was just running the sync again -- but this is exactly the "derive, don't assume it already happened" trap: `dfs doctor` never caught it because nothing in this session had re-verified EdgeRaw's OWN header against `EDGE_COLUMNS` since the rename shipped in code. Fixed by re-running `dfs sync --only edge` and confirming via `client.read_range` (not `dfs doctor`'s cached notion of "clean") that the header now reads `ImpliedMove`.
2. **`EdgeSource.pre_upload` silently wiped two real Pool ticks.** Root cause: `--force`-relinking Player Pool/Lineups/PlayerPoolRaw was run BEFORE re-syncing EdgeRaw with the new column order (backwards -- should always be sync-the-source-tab-first, link-the-derived-tabs-second), and the resulting burst of API calls pushed this session into Google's own rate-limit backoff. `pre_upload`'s `try/except Exception: return {}` (added originally "so a malformed/missing prior tab can't block a sync") caught a REAL API failure the same way it would catch a legitimately-empty tab, silently returning "nothing to preserve" instead of erroring -- and the next `write_tab` call then cleared EdgeRaw with no ticks to restore. Sam's `Ja'Marr Chase` (`GPP`) and `Colston Loveland` (`Cash`) tags were wiped with no error printed anywhere. Caught only because Sam had a live screenshot open and flagged "no SOS coming in" independently; the actual EdgeRaw Pool column was re-read directly (`A1:B30`) and found blank where it shouldn't be. Restored both values by hand (the same two cells, same values, confirmed from an earlier read taken before the loss) -- verified live that both `EdgeRaw`'s `Pool` column and `Player Pool`'s own formula-driven row list agree again. **Fixed at the root, not just patched around:** `pre_upload`'s two legitimate "nothing to preserve yet" cases (tab doesn't exist; tab exists but has no `Id` column) already had their own explicit early returns -- the blanket `except Exception` past those two checks was deleted outright, so a real failure (rate limit, network blip, permission error) now raises and fails the sync loudly instead of silently discarding data, matching this module's own "raise, don't return partial data" contract. Re-verified live: re-running `dfs sync --only edge` with the fix in place preserved both ticks correctly.

**Lesson for the next EdgeRaw-adjacent change:** sync the source tab, then re-link the derived tabs -- never the other way around -- and if a burst of Sheets API calls is about to happen (a reorder + relink + repolish, all close together), expect backoff and don't fire MORE calls (including ad-hoc verification reads) into an already-throttled window; it only extends the wait.

**Phase 5, Section L (2026-09-16): `PlayerPoolRaw`'s `OppPosRank` formula was keyed on the wrong column -- a real, pre-existing correctness bug, not a sync issue.** Found while adding `OppPosRank` to EdgeRaw itself (Section M below): the hand-typed `=VLOOKUP($C2,SoSComb!$B:$G,HLOOKUP($B2,...),false)` formula -- never regenerated by any Python code, since nothing in this repo tracked it -- keyed its `SoSComb` lookup on column C (`Team`, this player's OWN team) instead of column D (`Opp.`, their actual opponent). Every `OppPosRank` value on the sheet measured how tough a player's own defense is, never their opponent's. Invisible for as long as `SoSQB`/`SoSRB`/etc. were blank (`#N/A` regardless of which column the lookup used); confirmed by cross-checking 29 real players by hand against `SoSComb`'s own per-team ranks -- every single one matched "team's own rank," never "opponent's rank" (e.g. Ja'Marr Chase, CIN vs. HOU, showed `5` -- CIN's own WR rank -- not HOU's real `20`). Fixed as a real, regenerable Python-generated formula rather than a second hand-typed patch (the exact gap that let this hide): new `sheet_pool_raw_sos.py` (`rewrite_opp_pos_rank`), `dfs setup fix-opp-pos-rank`. Player Pool/Lineups needed no separate fix -- both already `VLOOKUP` their own `OppPosRank` off `PlayerPoolRaw` by Name, so they self-heal the moment `PlayerPoolRaw`'s own value is correct (confirmed live: both tabs' live formulas read `PlayerPoolRaw!$A:$S,19`). Applied template first, then live; re-verified all 29 players by hand afterward, every one now matching the opponent's real rank.

**Phase 5, Section M (2026-09-16): `OppPosRank` added natively to EdgeRaw ("all data should be in edge raw"), which surfaced a THIRD live instance of the exact stale-cell-format incident class `FIELD_FORMATS["Id"]`'s own comment already documents.** New `derived._attach_opp_pos_rank` (joins each position's already-synced `sos_<position>` frame by `Opp`, computing the SAME opponent-based value Section L just fixed on `PlayerPoolRaw`, natively in pandas -- no live Sheets lookup); `EDGE_COLUMNS` gained `OppPosRank` right after `GameEnv` (`GameEnv, Stadium, ...` -> `GameEnv, OppPosRank, Stadium, ...`); `sources/edge.py`'s `EdgeSource.fetch` builds `sos_by_position` from the five `sos_*` sources the same optional/graceful-degradation way `games`/`weather` already work (one position's TFFB sync failing blanks only that position, never the whole column). Applied to both sheets -- live via `dfs sync --only edge` (EdgeRaw is wholesale Python-generated), template via `provision_missing_columns` (append the new header text) + `move_columns` (relocate it into place), since the template's EdgeRaw holds stale sample data `dfs sync` never touches. Symbols touched: `derived.EDGE_COLUMNS`, `sheet_style.EDGE_WIDTHS`/`FIELD_FORMATS` (`OppPosRank` entries added), `tests/test_sheet_links.py`'s two EDGE_COLUMNS-index-pinning tests (re-derived, not just re-asserted), 10 new tests in `test_derived.py`.

**Two more real bugs found live applying this, neither one in the new code path itself:**
1. **A THIRD stale-cell-format incident** (`Id`'s own `FIELD_FORMATS` comment already documents the first; the second was Section J's `+44132966.0` mangling). `OppPosRank`'s real rank values rendered as `"11 mph"` on the live sheet -- the physical column it landed on had carried a stale `"0 \"mph\""` format since it held `Wind` two reorders ago, then `Stadium` (Section J's reorder), both times invisible because neither `Wind`'s real values nor `Stadium`'s (plain text) are affected by a number format, only revealed once a real NUMBER (`OppPosRank`'s rank) finally occupied that cell. `write_tab`/`dfs sync` only ever rewrites VALUES, never formatting -- a column's physical position can silently carry ANY prior tenant's number format indefinitely until something explicitly resets it. Fixed at the root the same way `Id` was: added `FIELD_FORMATS["OppPosRank"] = _num("0")`, so `polish_edge`/`polish_builder_tab` now explicitly resets it on every run instead of leaving it to inherit whatever the physical column last carried. Since `FIELD_FORMATS` is shared across every tab (Fix 2.1), adding this entry also surfaced (via `dfs setup audit-style`) that `PlayerPoolRaw`/`Player Pool`/`Lineups`' own `OppPosRank` columns had NEVER been explicitly formatted either (sitting on Sheets' default "General" the whole time -- harmless there since `OppPosRank` was `#N/A` until Section I, and an error value ignores number format same as text does). Re-ran `polish_builder_tab` on all three (targeted call, not the full `dfs setup polish`) on both sheets; `audit-style` clean on both afterward. Re-running `polish_edge` (also targeted) fixed the live `OppPosRank` mangling AND, as a side effect, re-confirmed `Id`'s own format was still correct. **Lesson, stated plainly for the next new EdgeRaw column:** every new `EDGE_COLUMNS` entry needs its own `FIELD_FORMATS` entry from the start, even if "the sheet already shows the right thing today" -- a column's own current-looking-fine format is not evidence it's SET, only that nothing numeric has landed there yet to reveal it isn't.
2. **A SECOND, independent way the Section J Pool-tick-preservation bug could still occur**, caught live re-syncing EdgeRaw after adding `OppPosRank`: even with Section J's broad-`except Exception` fix in place, `pre_upload`/`post_upload` still read Id via `read_range`'s default FORMATTED rendering -- which bakes in exactly the stale-cell-format problem above. A clean numeric Id can render as `"+44132966.0"` on one side of a reorder (`pre_upload`, reading Id's OLD physical position) and differently -- or not at all -- on the other (`post_upload`, reading Id's NEW physical position), so the two reads of the "same" Id silently fail to match and the restore quietly does nothing (no exception, no error -- `preserved` just doesn't contain the key `post_upload` looks up). Reproduced and confirmed: `pre_upload` returned `{'+44132966.0': 'GPP', '+44133446.0': 'Cash'}` -- Sheets' own display formatting baked directly into the dict key. **Fixed at the root, not by normalizing strings after the fact:** new `SheetsClient.read_range_unformatted` (passes `ValueRenderOption.unformatted` through to `ws.get`, returning each cell's raw JSON value -- an actual Python `int`/`float`, not a formatted display string) and `sources/edge.py`'s new `_canonical_id` helper (strips a whole-number float's stray `.0`), used for EVERY Id read in `pre_upload`/`post_upload` instead of `read_range`. This makes the join immune to whatever number format either physical column happens to carry, permanently, rather than requiring every future reorder to remember to reset `Id`'s format first. Verified live: restored the two real ticks lost to incident #1 above, then re-ran `dfs sync --only edge` twice more (once right after this fix, once again after fixing incident #1's `OppPosRank` mangling) -- both ticks survived both times, confirmed via a real read of `EdgeRaw!A3`/`A8` and `Player Pool`'s own formula-driven row list agreeing.

**Running total, Pool-tick preservation across this one session: three independent latent bugs found and fixed** (Section J's broad exception swallow; this section's formatted-vs-unformatted Id mismatch; and the stale-cell-format class underlying both). All three were invisible before real data + a real column reorder happened to intersect -- the standing lesson for `sources/edge.py` specifically: an EdgeRaw column reorder is exactly the moment this join is most fragile, so verify Pool ticks by a REAL read (`EdgeRaw!A:B`, not just "sync reported ok") immediately after every one, not just once per session.

**Phase 5, Section N (2026-09-16): two more real bugs found once Sam actually had players in his pool to look at -- both existed long before this session, both invisible on an empty Player Pool.** Sam: "I still see extra black boxes in the top row of the pool" (a screenshot showing most of row 1 -- the "Add a player" control row -- solid dark, from `Edge ↗` and again from `Wind` all the way to `In`), then "There is data filled in the IN column."

1. **Row 1's dark header fill leaking onto columns that should be plain white.** `sheet_pool_control.ensure_pool_control_row`'s original reset-to-white step only ever ran ONCE (gated behind `if not migrated`) and only ever covered a hardcoded `A:Z` -- both correct assumptions the one time this shipped (Phase 5A, when the tab was exactly that wide), neither true any more. Every column added or moved since (`Edge ↗`'s own insert, the whole WEATHER/MOVEMENT/INTERNAL zone, `Used`/`In`, this session's `OppPosRank`) could inherit a stray dark fill into its own row-1 cell via `insertDimension`'s inherit-from-neighbor behavior or a reorder's `moveDimension` carrying formatting along with a column -- and nothing ever reset it again. Invisible the entire time Player Pool was empty (nothing to look at up there); visible the instant real players filled the rows below and drew the eye upward. Fixed at the root: the reset now runs on EVERY call (not just first migration) and covers the tab's own CURRENT width, read fresh from row 2's real header each time (`column_letter(max(len(header), 26) - 1)`, so a genuinely-narrow fresh tab still gets the original generous `A:Z` floor) -- self-healing for whatever gets added next, rather than needing a fourth incident to notice again. Applied live via a direct call to `ensure_pool_control_row` (not the full `dfs setup polish`) on both sheets; re-verified via `get_cell_formats` that every row-1 cell reads plain white except `A1`/`B1`'s own intentional styling.
2. **`Player Pool`'s `In` column showing "L1, L2, ..., L20" on rows with no player at all.** `sheet_pool_usage._in_formula` was missing the exact blank-name guard `_used_formula` right next to it already has -- `COUNTIF(range, "")` counts truly EMPTY cells in `range` as matches, so a blank Player Pool name cell matched every still-empty Lineups block (all 20, since no real lineups exist yet this week) and printed every lineup label instead of nothing. Invisible until a genuinely-blank row sat next to a real, non-blank one for someone to compare against and notice the blank one wasn't actually blank. Fixed by wrapping the whole formula in the same `IF({name_cell}="","",...)` guard `_used_formula` uses. Verified live: a blank row's `In` cell now reads empty; the two real pooled players (still rostered nowhere, since no real lineups are built yet) correctly read empty too, not "in every lineup."

## Phase 6, Part 1 (2026-09-17): the five bugs, shipped as their own commit before any structural work

Sam's own instruction: land and verify this before touching Part 2 (the
column spine reorder), so that work isn't building on top of broken
cells. No structural move in this part -- no changelog table row -- but
enough real findings to warrant a full write-up, the same as Phase 4/5's
non-structural incidents above.

**1.1 -- `LINE_MOVE_FLAG_THRESHOLD` retuned, 1.0 -> 6.0.** See
`docs/CALCULATIONS.md`'s `Flag` section for the full distribution and the
before/after numbers (95.7% -> 4.5% on the real live Week 2 slate,
verified by re-running `dfs sync --only edge` and reading `ImpliedMove`
back directly, not by trusting the sync's own "ok" status).

**1.2 -- `Lineups`' `% of Rstr` `#DIV/0!`, and a new one-time repair
command.** Verified live first: `L2 = =F2/F$11`, dividing by the block's
own running salary total, which is 0 until a name is typed -- `#DIV/0!`
on all 180 slot rows on a fresh week. Not written by any `dfs` command
(hand-authored in the template, like the "average remaining per slot"
helper documented in `docs/SHEET_REFERENCE.md`), so this is the first
Python-side ownership of it: new `sheet_style.polish_lineups_pct_of_rstr`
/ `dfs setup fix-pct-of-rstr`, guarding both the whole-block-empty case
and the individual-blank-slot case (an empty slot reads blank now, not a
real-looking `0.0%`). Run against both sheets; verified live afterward by
reading `Lineups!L2`'s actual formula back, not just the command's "OK."

**1.3 -- Board's three panels, two different failure classes.**

1. **`TOP LEVERAGE` and `LANDMINES` were reading stale EdgeRaw column
   references, not a formatting bug.** The spec's own diagnosis for
   `TOP LEVERAGE` ("blank Leverage renders as 0.0 due to number format")
   turned out to be wrong when checked against the tab's real formula:
   `build_board` bakes `EDGE_COLUMNS` positions into a written formula
   string at the moment it's called, and hadn't been re-run since the
   Sept 16 `CeilPct`/`OwnPct` reorder shifted every column after
   `CeilVal`/`ProjOwn` two slots right. `TOP LEVERAGE`'s "Lev" column was
   silently reading `ProjOwn` (genuinely 0.0 pre-midweek, which is
   exactly what made the wrong diagnosis look plausible), and
   `LANDMINES`'s filter was reading `OwnPct`/`Leverage` (numbers) where
   it expected `Avail`/`Flag` (text), so its `SEARCH("WIND"/"OUT", ...)`
   conditions essentially never matched -- explaining "renders 0.0" and
   "is empty" without either being a formatting issue at all. Fixed by
   re-running `dfs setup build-views` (regenerates every formula fresh
   against the CURRENT `EDGE_COLUMNS`), verified live: real Leverage
   numbers, and `LANDMINES` populated with real `OUT`/`Q`/`D` players and
   their flags on the live Week 2 slate. **Standing lesson for Part 2's
   own reorder:** any tab whose formulas were generated by baking in
   `EDGE_COLUMNS` positions at write time (Board is not the only one)
   must be regenerated again after that reorder ships, not just after
   this one.
2. **`BEST CEILING VALUE` was a real design bug, not stale data.**
   `CeilVal` (points per $1,000) isn't comparable across positions, so a
   flat `SORT` by `CeilVal` read as ~11 QBs of 12 rows on a real slate.
   Rebuilt as five independent per-position blocks (`_best_value_block`),
   2 rows each, vertically stacked -- verified live: exactly QB, QB, RB,
   RB, WR, WR, TE, TE, DST, DST.
3. **Row 2's summary labels, a genuine layout conflict, not just a width
   fix.** `style_board` reuses the same physical columns for row 2's
   summary stats (`C`/`E` hold "Highest total"/"Max wind") that the
   panels below use for `Lev`/a visual spacer -- so `C`/`E` were sized for
   the panel, not the label, and "Highest total"/"Max wind" clipped to
   "Highest tota"/"Max" live. Widened `C` (64 -> 110) and `E` (24 -> 75);
   accepted as a temporary, minor cosmetic cost to the panel/spacer below
   (Part 3 rebuilds this whole tab again shortly).

**1.4 -- `has_real_ownership` was `.any()`, not a share.** See
`docs/CALCULATIONS.md`'s `OwnPct, Leverage and LevBasis` section. New
`OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5`.

**1.5 -- truncated EdgeRaw headers, and a lesson about trusting a pixel
heuristic over actually looking.** The ten headers named in the spec
(`Position`/`ProjPts`/`Ceiling`/`CeilPct`/`ProjOwn`/`Leverage`/
`OverUnder`/`Spread`/`GameEnv`/`OppPosRank`) were widened in `EDGE_WIDTHS`,
and a new `sheet_audit.py` check (`_min_header_width_px`, a deliberately
low-calibrated floor -- see its own comment) flags any column whose width
falls below what its header text needs. Sam, mid-fix: **"Make sure you're
visibly validating these widths too. Not just using numbers. I'll never
see the numbers, only the sheet."** That instinct caught real bugs the
heuristic missed on both sides:

- **False negatives** (heuristic said "fine," the live sheet did not):
  `Team` (54px) clipped to "Tearr", `OwnPct` (68px) to "OwnPc", `LevBasis`
  (74px) to "LevBasi" -- none of the three were in the originally-reported
  list, all three found only by opening the template in a browser and
  zooming into the real rendered header row. Widened to 66/82/90px.
- **A related, non-header truncation found the same way**: `Lineups`'
  totals-row "Remaining" label (written by `polish_lineups_totals_rows`
  into the `Val` column) clipped to "Remainin" -- `Val`'s 58px width was
  sized for a short number, never for the 9-character label a totals row
  puts there. Widened `Val` to 80px.
- **A false positive** (heuristic said "truncated," the live sheet did
  not): `Player Pool`'s `O/U` (33px, heuristic floor ~37px) renders in
  full -- a short two-character-plus-slash header needs less room than
  the floor assumes. Left as-is; the check stays deliberately generous
  rather than chasing every few-pixel margin.
- **Extended scope, per Sam's own call** ("widen those too" over scoping
  the check back to EdgeRaw only): the identical pattern existed on
  `BUILDER_WIDTHS` (shared by `PlayerPoolRaw`/`Player Pool`/`Lineups`) and
  `_MOVEMENT_WIDTHS`. `Team Implied` had NO entry in `BUILDER_WIDTHS` at
  all (only ever managed by `EDGE_WIDTHS`'s own spread) and sat at two
  DIFFERENT widths on two tabs (81px, 57px) -- consistent with nothing
  ever having set either on purpose; same story for `Ceil`/`Overflow`.
  `Movement`'s `Implied move` (92px, 11 characters) had never been
  widened past `Total move`/`Spread move`'s own width (7 characters each)
  when Fix 2.2 renamed it from the 8-character `LineMove`.
- **Not fixed, out of scope on purpose:** `Results`' `Black/White/Purple`
  header (110px, genuinely clipped to "Black/White/P" live) is Sam's own
  hand-typed team-colour label on a column `dfs` code has never managed
  the width of (`docs/SHEET_REFERENCE.md`: columns H/I/J on `Results`
  "stay yours to maintain by hand"). The audit check still reports it --
  correctly, since it IS truncated -- but widening it isn't this
  codebase's call to make unilaterally for a hand-typed label. Flagged to
  Sam; his to fix by hand if he wants it wider.

**A live rate-limit lesson, same shape as Section J's, worth repeating
for the next `dfs setup polish`-heavy session:** several consecutive full
`dfs setup polish` runs in a short window (three total, chasing three
rounds of width fixes) escalated from gspread's normal patient backoff
into hard `ConnectionError`s (ended a wait, immediately re-hit `[429]
Quota exceeded ... Write requests per minute per user`) -- worse than the
plain 429-and-wait pattern documented earlier, and it did not clear on
its own within the next couple of minutes either. Recovered two ways:
waiting several real minutes before the next attempt, and -- the more
effective fix, same lesson as Section J's `polish_edge`-alone recovery --
calling `polish_edge`/`polish_builder_tab` directly for only the four
width-affected tabs instead of the full pipeline, which finished in
seconds. A process left running 50+ minutes with only ~10 seconds of real
CPU time (`ps`'s own `TIME` column, not `ELAPSED`) is the tell that it's
stuck in this pattern rather than doing real work -- worth checking
before assuming a long-running `dfs setup polish` is merely slow.

**Also found and fixed, not in the original five/six but caught verifying
1.2's own block formulas -- a real, live correctness bug affecting most
of Sam's lineup slots.** `Lineups`' per-block totals row is supposed to
sum `Pts`/`Rstr%` across that block (`=SUM(...)`, same shape as the
already-correct `Salary`/`Ceil` sums) -- verified live that only 5 of 20
blocks (0, 1, 2, 3, 6) actually had that. The other 15 had
`=VLOOKUP($A<totals_row>,PlayerPoolRaw!$A:S,11,false)` (or `,14,false` for
Rstr%) instead -- a lookup against the totals row's own permanently-blank
Name cell, which resolves to `#N/A` the moment a lineup in one of those
blocks is built. Not written by any `dfs` command --
`polish_lineups_totals_rows`'s own docstring had assumed (before this
fix) that Pts/Rstr% "already hold real, working formulas," which was true
for only a quarter of the blocks; likely a stale hand-edit or an
incomplete copy/paste, not a code defect. Fixed the same self-healing way
`Ceil`'s own sum already was: unconditionally rewritten from
`name_blocks` on every call. Verified live on all 20 blocks post-fix
(`dfs setup polish`'s own summary: "60 sum(s) written (Ceil/Pts/Rstr%)"),
and spot-checked the previously-corrupted last block directly
(`Lineups!G258`/`K258`, both real `SUM`s now, not the old `VLOOKUP`).

**1.6 -- dead CLI commands removed.** `dfs sheets add-pool-deck` and `dfs
sheets add-pool-picks` (both `sheets_app` compatibility aliases that had
drifted to mean something different from their own names -- "add" a deck
that no longer exists, redirecting to remove it) and `dfs setup
remove-pool-picks` (the `Pool Picks` tab itself is long gone) are deleted
outright. `dfs setup remove-pool-deck` is kept -- unlike the other three,
a real un-migrated sheet could still have a deck on it -- with its
short-help/docstring now saying so explicitly.

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
| `EDGE_COLUMNS` (added/removed a column) | `docs/SHEET_REFERENCE.md`'s EdgeRaw table, the Instructions tab's EdgeRaw row on **both** the live sheet and the template |
| A formula or threshold (how a column is actually calculated, not just its name) | `docs/CALCULATIONS.md` -- the one place the exact math is supposed to live; don't let it drift into being "close enough" to what the code does |
| A CLI command's name, flags, or behavior | `docs/WORKFLOW.md`, the Instructions tab's "Weekly workflow" row on both sheets |
| The weekly workflow itself (a step added, removed, or reordered) | Same two places as above, plus this file's own affected section if the change touched something documented here |
| A new tab | Everywhere the "Adding a new data source" checklist above already says, **plus** a new Instructions tab row describing it, **plus** `docs/SHEET_REFERENCE.md` |
| The template's tab set or a shared tab's column order (`PlayerPoolRaw`/`Player Pool`/`Lineups`) | Run `dfs doctor` against **both** the live sheet and the template, `docs/SHEET_REFERENCE.md`'s canonical-column-order note, the Instructions tab's column-order row on both sheets |
| A new symptom worth debugging by hand | `docs/TROUBLESHOOTING.md` |
| A command's name, group, or behavior | README's Commands section, `CLAUDE.md`'s doc map, `docs/WORKFLOW.md`, the Instructions tab on both sheets, and every docstring/comment mentioning it (`grep -rn 'dfs ' src/`) |

The Instructions tab is a real Google Sheet, not a file in this repo --
update it with `SheetsClient.update_range` (see any `dfs setup`/`dfs
week` command for the pattern), once against the live sheet and once
against the template (`--sheet-id` or a `model_copy(update=...)`'d
config, same as `dfs setup polish --sheet-id`). There's no test that
catches this going stale, so it's on the honor system -- treat it as
part of the change, not a follow-up.

Also worth a periodic check regardless of what you just changed: any
Instructions-tab `HYPERLINK` pointing at a repo file assumes that file is
tracked and public. `docs/planning/ROADMAP.md` used to be one of those links; once
it was gitignored (session working notes, not public documentation), the
link 404'd, and had to be found and removed by hand -- nothing flags a
sheet formula pointing at a path git no longer tracks.

## Phase 6, Part 2 (2026-09-17): the column spine reorder, EdgeRaw included

The reorder this whole hazard class exists to warn about, run once,
deliberately, on all four column-bearing tabs at the same time (`EdgeRaw`
plus the three `sheet_columns.py` tabs) -- template first, then live, per
the two-sheet rule. Landed after Part 1's five bugs were committed and
verified (Sam's own instruction: don't build structural work on top of
broken cells).

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-17 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | `derived.EDGE_COLUMNS` and the three `sheet_columns.py` tabs redesigned into a shared spine (`Name/Pos./Team/Opp./DK Sal/Pts/Val/Ceil/CeilVal/Own%/Avail/Flag` -- `Position/Team/Opp/Salary/ProjPts/Val/Ceiling/CeilVal/Own%/Avail/Flag` on EdgeRaw itself, plus `Pool` ahead of it per `EDGE_DATA_OFFSET`) with everything else behind four collapsed groups in Sam's specified left-to-right order (Game, Ceiling detail, Movement, Weather) and `Id` hidden outright, not grouped. `Leverage` (Part 7.1) demoted off the spine into the new Ceiling detail group, folded into this same reorder rather than run as a second pass. `ProjOwn`/`Rstr%` renamed to one shared `Own%` and rescaled to a true 0-1 fraction. | EdgeRaw: Phase 3's append-only IDENTITY/DECISION/GAME/WEATHER/MOVEMENT/INTERNAL grammar. Other three: `sheet_columns.BASE_COLUMN_ORDER` under that same grammar. | EdgeRaw: `derived.EDGE_COLUMNS`, 28 columns, spine + 4 collapsed groups + hidden `Id`. Other three: `sheet_columns.BASE_COLUMN_ORDER`, same grammar (`PLAYER_POOL_RAW_COLUMN_ORDER` 30 cols, `PLAYER_POOL_COLUMN_ORDER` 36, `LINEUPS_COLUMN_ORDER` 33). | Live + Template | `derived.EDGE_COLUMNS`, whole `sheet_columns.py` rewrite (`IDENTITY`/`DECISION`/`GAME`/new `CEILING_DETAIL`/`MOVEMENT`/`WEATHER`/`INTERNAL`, `LINKED_COLUMNS`), `sheet_links.link_edge_columns`'s grouping rewrite (finds each zone member by `header.index(name)` over `(GAME, CEILING_DETAIL, MOVEMENT, WEATHER)`, not the old `columns[name]` dict over `(WEATHER, MOVEMENT, INTERNAL)`), `sheet_style.EDGE_COLUMN_GROUPS` (now `[("OverUnder", "Wind")]`, `collapsed=True` -- EdgeRaw grouped for the first time), `sheet_native_links.NATIVE_LOOKUP_COLUMNS` (`Rstr%` -> `Own%`). Removed `sheet_links.VEGAS_GROUP_COLUMNS`/`group_lineups_columns` outright -- superseded, not just redundant: Vegas's three columns now sit inside the collapsed Game group, and a second standalone group over an overlapping range would re-trigger the adjacent-`addDimensionGroup`-merge bug (Phase 3's bug #5) this same reorder already works around for the four zones. |

**Own% -- one name, one scale, resolved as a Rule Zero question before touching anything.** The spec's own Ceiling-detail group listed `ProjOwn` as a fourth member while also asking for the `Rstr%`/`ProjOwn` rename -- genuinely ambiguous which name should survive. Asked Sam directly: **"Rename wins"** -- `Own%` is the one name, everywhere. That immediately exposed a second, real conflict: EdgeRaw's `ProjOwn` was a 0-100 number (TFFB's own raw scale), while `Rstr%` on the other three tabs was already 0-1 (a `NumberFormat.PERCENT`-backed fraction) -- the same header could not mean two different scales once merged into a shared spine. Asked again: **"Make EdgeRaw a true fraction"** -- `derived.build_edge_frame` now divides `ProjOwn` by 100 before renaming it, rather than multiplying the other three tabs' values by 100 (which would have meant re-deriving every already-correct percentile/threshold downstream). `derived.CHALK_OWNERSHIP_THRESHOLD` moved with it, `20.0` -> `0.20`, and `_percentile_within` (used for `CeilPct`/`OwnPct`) needed no change at all -- it's scale-invariant by construction, exactly the property that made this a safe rescale rather than a second hunt through every formula that reads ownership.

**The grouping rewrite, and why it had to change shape, not just its zone list.** `link_edge_columns`'s old grouping code assumed every group member was a *linked* column, looked up via the `columns` dict it builds from `LINKED_EDGE_COLUMNS` positions -- true for the old WEATHER/MOVEMENT/INTERNAL zones (all-linked), false for the new GAME/CEILING_DETAIL zones, both of which mix linked and native columns (`Venue`, `O/U`, `Spread`, `Team Implied`, `OppPosRank` are native). The first pass crashed with a `KeyError` on `"Venue"` the moment GAME was added to the group list. Fixed by switching to `header.index(name) for name in group if name in header` -- reads the tab's own real header directly, works for any mix of linked/native, and degrades gracefully (skips silently) if a zone member is genuinely absent rather than raising.

**Two self-caught inconsistencies before anything went live, neither from a live incident:**
1. A duplicate `"Leverage"` entry in `LINKED_COLUMNS` -- added via the new `*CEILING_DETAIL` spread while an old explicit `"Leverage"` line from the pre-Part-2 list was still sitting a few lines above it. Caught by the unit tests failing on a length mismatch, not live; removed the explicit entry.
2. `sheet_columns.py`'s own module docstring claimed Leverage was "folded into `CEILING_DETAIL`" while the actual `DECISION`/`CEILING_DETAIL` lists still had it in `DECISION` -- self-inconsistent on a re-read before the reorder ever ran. Fixed by moving the constant, not the comment.

**The `INDIRECT("E"&...)` hazard, hit again, resolved as a Rule Zero question.** The hand-authored "average remaining per slot" helper row below each Lineups totals row (documented in `docs/SHEET_REFERENCE.md`, never written by any `dfs` command -- the same helper Part 1's 1.3 incident already touched once) reads `INDIRECT("E"&(ROW()-1))`, hardcoding column E for what used to be `Venue`'s totals-row cell. Venue moves out of IDENTITY into the Weather group under this reorder, so E no longer holds it after the move -- a string-built reference `moveDimension` cannot see or retarget, the exact hazard class `CLAUDE.md`'s central-hazard section warns about, and RULE ZERO trigger #4 (formula this codebase's own tools can't fix). Asked Sam: **"Regenerate the helper formula"** (over leaving it broken, or hardcoding the new letter -- which would just reintroduce the identical hazard one column over). New `sheet_style.polish_lineups_remaining_per_slot_helper`, which finds the totals row's own Salary-cap-remaining cell by header name and writes a real formula referencing it directly -- no `INDIRECT`, no hardcoded letter, self-healing across any future reorder the same way every other formula in this codebase already is.

**EdgeRaw's own Weather visibility, overridden on Sam's call.** Fix 2.9 (an earlier phase) had deliberately kept EdgeRaw's Weather columns (`Stadium`/`Roof`/`Wind`) always visible, uncollapsed, on the reasoning that EdgeRaw is the one tab Sam actually filters/scans by position rather than reading top-to-bottom -- collapsing them there would hide the exact columns that filtering surfaces. Part 2's spine-and-groups design calls for collapsing everything off the spine, everywhere, uniformly. Flagged as a real conflict with a prior, deliberate decision (RULE ZERO trigger) rather than silently overriding it either way. Asked Sam: **"Override it -- collapse on EdgeRaw too"** -- consistency across all four tabs won out over the earlier per-tab exception; `EDGE_COLUMN_GROUPS`' single `("OverUnder", "Wind")` range now covers Game through Weather on EdgeRaw exactly like the other three tabs' four separate (but adjacency-merged) groups.

**Migration mechanics -- `EdgeRaw`'s template needed manual surgery no other tab did.** `EdgeRaw` is wholesale Python-generated on a real `dfs sync`, but `dfs sync` only ever targets the LIVE sheet by design (per the weekly-workflow model) -- the template's own `EdgeRaw` tab holds frozen sample data from whenever it was last hand-refreshed, and no code path ever rewrites it. The template-first rule still applies, so a one-off script called `rename_header_column` (renaming `ProjOwn`/`Rstr%` cells in place) then `reorder_tab_columns` directly against the template's `EdgeRaw`, mirroring by hand what a real sync does automatically on live. The live sheet got the same structural result "for free" from its own real `dfs sync --only edge` run later in the sequence -- the one tab in this whole reorder where template and live took genuinely different code paths to reach the same shape, worth remembering the next time `EdgeRaw`-specific work touches the template.

**Two incidents found on the template, from that manual-surgery ordering, neither of which recurred on live:**
1. **Board's `LANDMINES`/`TOP LEVERAGE` panels resolved stale EdgeRaw column letters after the template's manual reorder, despite `dfs setup build-views`'s own "OK: built" success message.** The template's `build_board` bakes `EDGE_COLUMNS` positions into a written formula string at call time (the same mechanism Part 1's 1.3 already flagged for the live sheet); it was called once early in the sequence, before the template's `EdgeRaw` had actually finished its manual column move, so it baked in letters (`$K$`/`$S$`) that were correct for the *pre-move* layout and wrong for the post-move one. Not caught by the command's own return value -- confirmed only by reading the panels' real formulas back directly and finding the wrong letters. Fixed by calling `build_board()` again, directly (not through the CLI, to avoid another round through the rate-limited write path), and re-verifying via a fresh formula read that both panels now cite the correct post-move letters. **Standing lesson, same one Part 1's 1.3 already stated for the live sheet, now confirmed to apply per-sheet independently:** any tab whose formulas bake in `EDGE_COLUMNS` positions at write time has to be rebuilt AFTER that sheet's own reorder is fully done, not just once globally -- template and live can each be mid-reorder at different points in the same session.
2. **Column widths landed on the wrong physical columns on the template, for the identical reason.** `dfs setup polish`'s width-setting (`polish_edge`, keyed by column letter derived from `EDGE_COLUMNS`) ran once before the template's manual `EdgeRaw` fix finished, so the widths it wrote landed on whatever columns occupied those letters under the *old* layout -- correct widths, wrong columns. Fixed by re-running `polish_edge` directly a second time, after the manual reorder completed, and confirmed by a real browser screenshot of the template's rendered header row (per the standing "verify by looking, not by the numbers" lesson from Part 1's 1.5) rather than trusting the recomputed pixel values alone.

**Live execution, run in the corrected order (sync EdgeRaw before relinking, per Section J's own lesson from the prior phase):** `dfs setup reorder-columns`, `dfs setup link-edge --force`, `dfs sync --only edge`, `dfs setup build-views`, `dfs setup polish`, all against the live Week 2 sheet, all succeeded without the template's two incidents recurring -- Board's panels resolved correctly on the first `build-views` call this time, since nothing was mid-reorder when it ran.

**Verification, both sheets, real reads rather than tool-return-value trust throughout:** `dfs doctor` clean on both; `dfs setup audit-style` clean on both (only the pre-existing, out-of-scope `Results` "Black/White/Purple" hand-typed-column finding from Part 1's 1.5 remains); Board's three panels spot-checked via direct formula reads on both sheets; both real Pool ticks (`Ja'Marr Chase`/GPP, `Colston Loveland`/Cash) confirmed surviving the live `dfs sync --only edge` resync; a live sweep of every tab for hardcoded `VLOOKUP`s found no new instances of this reorder's own hazard class (only pre-existing, already-known letter-based array formulas on `TFFBOptoRaw`). Column-group collapse state confirmed persistent via a direct `fetch_sheet_metadata` read on both sheets (`EdgeRaw`/`PlayerPoolRaw`/`Player Pool`/`Lineups` each show exactly one collapsed group spanning Game through Weather) -- re-verified around a real sync to confirm `dfs sync` does not reset it, then confirmed visually in the browser on the live sheet: EdgeRaw's spine (Pool through Flag) sits left of a collapsed `+` group jumping straight to `Id`; Player Pool's spine (Name through Flag) sits left of its own collapsed group, with `Overflow`/`Pool`/`Used`/`In` reappearing after it; `Own%` renders as a real percent on both. Test suite: the reorder cascaded roughly 21 failures across `test_sheet_links.py`/`test_sheet_native_links.py`/`test_sheet_reorder.py`/`test_sheet_style.py`/`test_doctor.py`, all fixed by updating fixtures/pinned indices to the new order (computed programmatically, not by hand, to avoid a second arithmetic error on top of the first) -- 524 tests passing, `ruff` clean, before either sheet was touched.

## Phase 6, Part 7.9 (2026-09-17): the metric audit

Sam reviewed every EdgeRaw/shared-tab metric and decided three concrete
changes, landed together since all three touch the same Ceiling-detail/
spine region Part 2 just finished redesigning.

| Date | Tab | What moved | Old position/name | New position/name | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-17 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | `OwnPct` dropped entirely from the sheet-facing column set (its only consumer anywhere in the codebase was the Leverage formula, verified by grep before removing -- it stays a real internal pandas column in `derived.py`, just not written to the sheet); `LevBasis` renamed to `OwnStatus`; `Flag`/`Flags` split for real, not just renamed -- `Flags` (every matching condition, space-separated) took `Flag`'s old visible spine slot, and `Flag` (just the single highest-priority token) moved into the hidden zone beside `Id`, kept rather than deleted since other formatting/filtering logic keys off it as a boolean value. | CEILING DETAIL: `CeilPct, OwnPct, Leverage, LevBasis` (4 cols). Spine ended in visible `Flag`. | CEILING DETAIL: `CeilPct, Leverage, OwnStatus` (3 cols). Spine ends in visible `Flags`; hidden `Flag` joins `Id` in INTERNAL. | Live + Template | `derived.EDGE_COLUMNS` (`OwnPct` gone, `LevBasis`→`OwnStatus`, `Flag`→`Flags` on the spine + new hidden `Flag` in INTERNAL), `derived.OWN_STATUS_REAL`/`OWN_STATUS_UNPUBLISHED` (renamed from `LEV_BASIS_REAL`/`UNPUBLISHED`), `derived._flags_for_row` (renamed from `_flag_for_row`, now returns a list consumed twice), `sheet_columns.CEILING_DETAIL`/`INTERNAL`/`DECISION`/`LINKED_COLUMNS`, `sheet_style._apply_own_status_marker` (renamed from `_apply_lev_basis_marker`), `sheet_style.EDGE_WIDTHS`/`FIELD_FORMATS`/`FIELD_COLOR_SCALES`/`GROUPED_TAB_UNSCALED_COLUMNS` (all lost their `OwnPct`/`LevBasis` entries, gained `OwnStatus`/`Flags`), new `dfs setup fix-flag-split` command |
| 2026-09-17 | `Lineups` | `% of Own` (Part 2's rename of `% of Rstr`) renamed again to `% of Cap`, and its denominator changed from the lineup's own running salary total to the salary cap constant | `=IF(OR($A<row>="",<totals row>=0),"",<DK Sal>/<running total>)` | `=IF($A<row>="","",<DK Sal>/<salary_cap>)` | Live + Template | `sheet_style.polish_lineups_pct_of_cap` (renamed from `polish_lineups_pct_of_own`), new `dfs setup fix-pct-of-cap` command, `config.toml`'s `[lineups] salary_cap` now a real formula input, not just a Python-side constraint check |

**Rule Zero trigger, resolved before touching anything:** 7.9's own spec
text described `Flag` as "the single highest-priority value (first match
wins)" -- checked against the real code first, per the standing rule, and
found that premise was already false: `_flags_for_row`'s "every matching
condition, space-separated" behavior (Fix 2.1, an earlier session)
predates this Part entirely. Two live-verified outcomes were possible --
just rename the existing all-matches column to `Flags` and stop, or split
it for real into a genuine single-token `Flag` plus all-matches `Flags`
-- with materially different blast radius (a real split touches ~10
reading consumers: Board's `LANDMINES` panel, the Movement view, `dfs
edge`'s terminal report, `dfs lineups late-swap`, `dfs sync --live`'s
diff report, the "Leverage plays" filter view, `sheet_audit`'s chip-rule
check). Asked Sam directly rather than guessing either way: "Split it for
real." Every one of those ~10 consumers was individually re-pointed at
`Flags` (all of them wanted "everything that fired," none wanted the
single-token value) except `_apply_name_flag_style`'s Name-bold-on-Flag
check, which is a pure boolean test and correctly still reads the hidden
`Flag` per Sam's own "keep it, it's still a formatting key" framing.

**A real gspread bug found migrating this, unrelated to the metric audit
itself but blocking it:** `SheetsClient.delete_columns` issues a raw
`deleteDimension` request directly via `sheet.batch_update(...)`,
bypassing gspread's own dimension-changing methods (`resize`/`add_cols`)
-- the only things that update the cached `Worksheet._properties
["gridProperties"]["columnCount"]` gspread's own `col_count` property
reads (gspread's own docs: "not dynamically updated when adding columns,
yet"). Deleting `OwnPct` shrank the real grid by one column while the
cached `SheetsClient` instance's `ws.col_count` still reported the OLD,
now-too-wide count for the rest of that script's run -- so the very next
`provision_missing_columns` call (adding the new `Flag` column) compared
against stale data, concluded the grid was already wide enough, skipped
`add_cols`, and the actual write failed outright ("exceeds grid limits").
Fixed at the root: `delete_columns` now decrements the cached grid
property itself after a successful delete, the same way gspread's own
`resize()` updates it after a successful resize -- not a workaround, the
missing half of the same convention gspread already uses everywhere else.

**Three more incidents, all found and fixed during the template/live
migration itself, none from the metric-audit code:**

1. **Template: `EdgeRaw`'s `Name` column was found hidden**, discovered
   only because Part 7.9's own hidden-column verification (checking
   `Id`/`Flag` landed correctly) happened to also read `Name`'s state.
   Root cause: an old Part 2 incident (`polish_edge` run before the
   template's manual `EdgeRaw` reorder had finished) applied `hide_
   columns("Id")` against a not-yet-correct physical layout, which at
   that moment resolved to `Name`'s column instead. Live's own `Name` was
   already correct (confirmed via the same check) -- template-only.
   Fixed by unhiding it directly, verified via `get_column_widths`.
2. **Live: `EdgeRaw` was never actually resynced before the other three
   tabs got relinked against it.** `dfs setup fix-flag-split` only
   touches `PlayerPoolRaw`/`Player Pool`/`Lineups` -- `EdgeRaw` needed a
   real `dfs sync --only edge` to pick up the new column set (the same
   "gets it for free" mechanism Part 2 relied on for the live sheet),
   which was skipped this time. `dfs setup link-edge --force` then wrote
   new VLOOKUP formulas assuming `EdgeRaw`'s NEW column positions while
   the real sheet still had the OLD ones -- every newly-relinked column
   briefly pointed at the wrong `EdgeRaw` field. Caught by a genuinely
   confusing moment: a direct width/hidden-state read looked
   coincidentally consistent with the new shape (the old `Flag` column
   happened to share `Flags`' own pixel width, and `Id` is unconditionally
   hidden either way), which delayed noticing until `dfs doctor` reported
   the header mismatch directly. Fixed by running the real sync, then
   re-running `link-edge --force`, then re-polishing `EdgeRaw` -- verified
   every relinked formula (`Flags`, `Leverage`, `OwnStatus`) resolves to
   the correct column by computing the expected VLOOKUP index
   programmatically and comparing, not by eye. **Lesson for the next
   EdgeRaw-adjacent live change:** verify a structural assumption by
   reading the ACTUAL current state (header text, or a resolved formula),
   never by inference from a coincidentally-matching side effect like a
   pixel width.
3. **Live: `Lineups`' own chip-application step had been cut short** by
   one of several background `dfs setup polish`/narrow-script runs killed
   mid-flight while recovering from a sustained Google Sheets API write
   rate-limit (this session's heaviest write night yet: the Flag/Flags
   split alone relinks 16 columns across 3 tabs, plus a 180-row `% of
   Cap` rewrite, plus polish's ~280 conditional-format rules on `Lineups`
   alone). Widths/hiding had already landed from an earlier kill (each
   `polish_builder_tab` step writes independently, so a mid-function kill
   still leaves whatever ran before the kill point in place), but the
   chip loop -- the last step in the function -- hadn't. Caught by `dfs
   setup audit-style`, not assumed clean from the process's own exit
   code; fixed with one direct re-run of `polish_builder_tab` for
   `Lineups` alone.

**A rate-limit lesson worth stating plainly, since it cost real time
tonight:** low CPU time relative to elapsed time on a running `dfs setup
polish`-family process is NOT reliable evidence of "stuck in backoff" on
its own -- it looks identical to "blocked on one legitimately large
batchUpdate" from `ps`'s perspective, and repeatedly killing+restarting a
script that's actually making real, landing progress just discards that
progress and extends the total time to done. The Google Cloud Console's
own Sheets API quota page (APIs & Services > Quotas, and > Metrics for a
request-volume/error-rate view) is the actual ground truth -- checking it
directly settled a multi-attempt stuck/not-stuck question this session's
own `ps`-based heuristic alone couldn't. When a `dfs setup polish`-family
command looks stuck, verify against the SHEET's own current state (a
direct `get_column_widths`/header read) before assuming a restart is
free -- tonight's restarts were each silently redoing already-landed work
rather than resuming from nothing.

Verified live and on template, end to end, after all of the above:
`dfs doctor`/`dfs setup audit-style` clean on both (only the
pre-existing, unrelated `Results` hand-typed-header finding remains on
live); column groups on all four tabs shrank by exactly one column
(`OwnPct`'s removal), confirmed via direct `columnGroups` metadata reads
matching between template and live; both real Pool ticks (`Ja'Marr
Chase`/GPP, `Colston Loveland`/Cash) survived every resync; every
relinked VLOOKUP formula's index checked programmatically against
`derived.EDGE_COLUMNS`, not eyeballed.

## Phase 6, zone labels (2026-09-17): a usability fix, not in the original spec

Raised mid-session, after Part 7.9 shipped: Sam, looking at the four
collapsed zones (Game/Ceiling detail/Movement/Weather) Part 2 built,
"Like, I should be able to open just game details or just weather" --
and, once told that requires a real structural change: "Make sure I know
what group is what somehow and I'm not just clicking random stuff.
Always check and think about the usability of things."

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-17 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | A real, always-visible, native (no-formula) label column added immediately before each of the four collapsed zones -- `GAME`, `CEIL`, `MOVE`, `WX` -- naming what's inside. Deliberately not a member of the zone's own `sheet_columns.py` list, so the label sits OUTSIDE the range that collapses. | Four zones sat back-to-back with nothing between them; Sheets merges adjacent same-depth column groups into one, so all four collapsed as a single region with one unlabeled `+`. | Each zone flanked by its own label, now independently collapsible -- four separate `+`/`-` controls, each named. | Live + Template | `derived.GAME_LABEL`/`CEILING_DETAIL_LABEL`/`MOVEMENT_LABEL`/`WEATHER_LABEL`/`ZONE_LABELS`, `derived.EDGE_COLUMNS` (4 new entries, one before each zone -- every already-linked column from the first zone member onward shifts by 1-4 positions, depending how many labels precede it), `derived.build_edge_frame` (writes `""` for every zone label on every row -- text lives in the header only), `sheet_columns.BASE_COLUMN_ORDER`/`PLAYER_POOL_COLUMN_ORDER`/`LINEUPS_COLUMN_ORDER` (labels inserted the same way), `sheet_style.EDGE_COLUMN_GROUPS` (one merged tuple -> four independent tuples), new `sheet_style._apply_zone_label_style` (called from both `polish_edge` and `polish_builder_tab`), `sheet_style.EDGE_WIDTHS` (`GAME`/`CEIL`/`MOVE`/`WX` entries) |

**Tested empirically before writing any implementation code, per this
codebase's own "verify live, don't assume" discipline:** nesting was the
first idea (a depth-1 group wrapping all four zones, four depth-2 groups
inside it, one per zone) -- if Sheets treats nested same-depth siblings
differently from top-level adjacent ones, no structural change would be
needed at all. Built a real test on the template's Scratch tab (same
methodology as Phase 4.3's own live conditional-format test): created
the outer depth-1 group, then four adjacent depth-2 zone groups inside
it, read the metadata back. Result: Sheets merged all four depth-2
requests into ONE depth-2 group spanning the entire outer range anyway --
confirmed by the fold attempt's own error message (`There is no group at
dimensionGroup.depth 2 that spans exactly ... Scratch!G:J; it is over
... Scratch!C:R`). Nesting depth doesn't change the merge behavior at
all; a real physical gap between zones is the only fix. Cleaned up the
test groups from Scratch immediately after.

**Why a label, not a blank spacer.** A collapsed group hides every cell
in its range -- a label placed INSIDE the zone it names would disappear
the moment someone collapses it, which is exactly the information the
fix exists to preserve. A blank spacer would keep the zones independently
collapsible but wouldn't solve the actual problem Sam raised (which `+`
is which) -- an unlabeled control is still "clicking random stuff."

**How the fix actually delivers independent collapse, mechanically:**
`sheet_links.link_edge_columns`'s existing zone-range computation reads
each zone's OWN member list (`GAME`, `CEILING_DETAIL`, `MOVEMENT`,
`WEATHER` in `sheet_columns.py`) to find contiguous index runs, then
merges any runs that end up touching. Since the label constants are
deliberately NOT members of those lists, a zone's own real column span no
longer starts immediately after the previous zone's last column -- there's
now a one-column gap (the next zone's own label) in between, so the
existing merge-adjacent-ranges check (`prev_end + 1 == next_start`)
naturally stops firing. No change was needed to the merge logic itself --
inserting the labels into the column layout was the whole fix. Verified
with a new test (`test_link_edge_columns_groups_independently_once_zone_
labels_separate_them`) feeding `link_edge_columns` the tab's real, fully
zone-labeled `PLAYER_POOL_COLUMN_ORDER` header and confirming it produces
four independent `group_columns` calls, not four merged, and not one.
`sheet_style.polish_edge`'s own `EDGE_COLUMN_GROUPS` needed a literal
change (one merged tuple -> four explicit ones), since that path never
merged ranges to begin with -- there was nothing for the labels to
"naturally" fix there; it just needed the four ranges spelled out
correctly with the labels excluded, which was always true of `EdgeRaw`'s
zones once the labels existed, merged or not.

**A real bug found and fixed in passing, unrelated to this change
itself:** `sheet_columns.LINEUPS_COLUMN_ORDER` still literally said
`"% of Own"` -- Part 7.9's live rename to `% of Cap` used `rename_header_
column` directly against the sheet, which never consults this constant,
so nothing had caught the list itself going stale. Left as-is, the next
`dfs setup reorder-columns` (or this zone-label migration, which reuses
the same `migrate_tab_to_designed_order` machinery) would have seen
`"% of Own"` as genuinely missing from a header that actually says
`"% of Cap"` and appended a duplicate near-miss column. Caught before
touching either sheet, while inserting the zone labels into this same
list -- fixed to `"% of Cap"` in the same edit.

Verified live and on template, same standard as every other structural
change here: header order matches `sheet_columns.py` exactly on all four
tabs, `dfs doctor`/`dfs setup audit-style` clean, column-group metadata
shows four independent collapsed ranges (not one merged) with widths
matching `EDGE_WIDTHS`, both real Pool ticks intact, `sheet_style.
_apply_zone_label_style`'s tint applied to all four label columns on
every tab (confirmed via a direct `format_range` read-back, not the
command's own success message).

## Stale-hidden-column bug, found and fixed during the zone-label rollout (2026-09-18)

Not a structural move -- no changelog table row -- but a real, three-times-
repeated live incident worth documenting in full, since it's a genuine
gap in a mechanism (`polish_edge`/`polish_builder_tab`'s hide-Id/Flag
step) this codebase already relies on elsewhere.

**The bug.** `hide_columns(letter, letter)` for `Id`/`Flag` only ever
ADDS a hide; nothing ever undoes one that shouldn't be there. During the
zone-label migration's own rate-limit-driven kill/retry cycles (this
session's heaviest write night), a `polish_edge` invocation that got
interrupted mid-reorder computed `Id`/`Flag`'s letter against a header
that hadn't finished moving yet, and hid whatever ACTUALLY sat at that
letter at that moment instead. Found live three separate times on
EdgeRaw across one session: `Name` (an old Part 2-era instance, found
while verifying this same class of bug); `Avail`/`Flags`; and
`GAME`/`CEIL`/`MOVE`. Every instance was only caught by direct
`get_column_widths` reads, never by a command's own success message or
by `dfs doctor` (which checks header TEXT order, not visibility).

**The fix, and why it isn't just "unhide everything first."** The
obvious fix -- reset the whole tab visible, then hide only `Id`/`Flag`
-- was tested on the template's Scratch tab before trusting it (a
collapsed group's own member columns already show `hiddenByUser: true`
as an intrinsic side effect of being collapsed, established earlier this
session): explicitly calling `hide_columns(..., hidden=False)` over a
grouped range genuinely un-hides those columns while the group's own
`columnGroups` metadata still reports `collapsed: true` -- a real desync,
not a hypothetical one. A blanket reset would have silently broken every
collapsed group the next time `dfs setup polish` ran after `dfs setup
link-edge`.

Fixed with new `SheetsClient.get_grouped_column_indices` (reads the
tab's current `columnGroups` metadata, same pattern `clear_column_groups`
already uses) and new `sheet_style._unhide_ungrouped_columns`, called
first thing in both `polish_edge` and `polish_builder_tab`: unhides every
column NOT currently covered by an existing group, batched into
contiguous runs to keep the API call count reasonable, before the
existing Id/Flag-specific hide runs. Makes the whole operation
idempotent and self-correcting instead of purely additive -- a
killed/retried run can no longer leave a permanent stale hide behind.

Verified on a real sheet (the template), not just via fakes: ran the
fixed `polish_edge` and confirmed directly that `Avail`/`Flags`/`GAME`/
`CEIL`/`MOVE`/`WX` all read visible afterward AND the four collapsed
groups still report `collapsed: true` with their original ranges,
undisturbed. `dfs doctor`/`dfs setup audit-style` clean on the template
afterward. New tests (`test_get_grouped_column_indices_*` in
`test_sheets.py`; `test_polish_edge_reset_before_hide_skips_columns_
already_inside_a_group`/`test_polish_builder_tab_reset_before_hide_skips_
columns_already_inside_a_group` in `test_sheet_style.py`) pin both halves
of the fix: the reset unhides what it should, and never touches what a
group already owns.

## Phase 6, Part 7.2 (2026-09-18): `ValAdj`, EdgeRaw's new default sort

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-18 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | New column `ValAdj` inserted into the shared spine, immediately after `Val` -- every column from `Ceiling`/`Ceil` onward shifts one position right. Linked (VLOOKUP against EdgeRaw) on the three builder tabs, same as `CeilVal`, since it's a whole-slate regression residual, not a per-row native formula. | `derived.EDGE_COLUMNS` 32 columns (`Val` at index 6, `Ceiling` at 7). `sheet_columns.DECISION` = `DK Sal, Pts, Val, Ceil, CeilVal, Own%, Avail, Flags` (8). `LINKED_COLUMNS` 16 members. `PLAYER_POOL_RAW_COLUMN_ORDER` 34 cols, `PLAYER_POOL_COLUMN_ORDER` 40, `LINEUPS_COLUMN_ORDER` 37. | `derived.EDGE_COLUMNS` 33 columns (`Val` still at 6, new `ValAdj` at 7, `Ceiling` now at 8). `DECISION` = `DK Sal, Pts, Val, ValAdj, Ceil, CeilVal, Own%, Avail, Flags` (9). `LINKED_COLUMNS` 17 members (`ValAdj` first). `PLAYER_POOL_RAW_COLUMN_ORDER` 35, `PLAYER_POOL_COLUMN_ORDER` 41, `LINEUPS_COLUMN_ORDER` 38. | Live + Template | `derived.EDGE_COLUMNS`, new `derived._val_adj_within_position`, `derived.build_edge_frame` (new `ValAdj` column; default sort changed from `Leverage`/`CeilPct` fallback to `ValAdj` unconditionally), `sheet_columns.DECISION`/`LINKED_COLUMNS` (both gain `ValAdj`), `sheet_style.EDGE_WIDTHS`/`FIELD_FORMATS`/`FIELD_COLOR_SCALES`/`EDGE_UNSCALED_PLAYER_METRICS` (unaffected -- `ValAdj` deliberately excluded)/`GROUPED_TAB_UNSCALED_COLUMNS` (gains `ValAdj`, alongside `CeilPct`), every EDGE_COLUMNS-index-pinning test in `tests/test_sheet_links.py` (`test_already_linked_columns_positions_never_move`, `test_edge_lookup_formula_uses_correct_range_and_column_index`, and the several tests hardcoding `LINKED_EDGE_COLUMNS`' width as a literal column-letter range -- all updated, not just re-asserted, per their own docstrings' instructions). |

**Why not native.** `Val` is a native per-row formula (`Pts / (DK Sal /
1000)`) on PlayerPoolRaw/Player Pool/Lineups -- each row only needs its
own two numbers. `ValAdj` can't work that way: the regression it's a
residual FROM needs every other row at that position on the same slate,
which is exactly the kind of whole-tab computation this codebase already
keeps in Python rather than Sheets formulas (see `CeilPct`/`Leverage`/
`GameEnv`, all computed once in `derived.py` and linked out). So `ValAdj`
is computed on `EdgeRaw` only, in `_val_adj_within_position`, and joins
`LINKED_COLUMNS` like every other EdgeRaw-computed signal.

**The regression, and its edge cases.** Per position, on the slate's own
`ProjPts`/`Salary` only (no accumulated history needed) -- `numpy.polyfit`
degree 1, residual = actual minus predicted. Two edge cases needed
explicit handling, both found by writing a docstring-first spec before
the implementation and then testing against it: a position with fewer
than two usable rows, or a constant `Salary` within a position (nothing
to fit a slope against), gets `0` for every row in that group rather than
raising or fabricating a number; a genuinely missing `ProjPts` stays
`NaN` in every case, including that degenerate one, never coerced to 0.

**A real pandas bug, found before it ever reached a real sheet.** The
first implementation used `frame.groupby("Position", group_keys=False).
apply(_residual)`, which is idiomatic pandas for "compute something per
group, keep row alignment." It silently breaks with exactly ONE group
present (which happens often here -- test fixtures with a single
position, and legitimately possible on a very short real slate too):
pandas' `apply` collapses the per-row Series into a single aggregate row
and reinterprets its own row-index (0, 1, 2, ...) as new COLUMN labels,
returning a transposed one-row DataFrame instead of a row-aligned Series
-- caught immediately by the offline test suite (`ValueError: Cannot set
a DataFrame with multiple columns to the single column ValAdj`), not live,
because `pytest -q` ran before any sheet was touched (see this repo's own
"offline first" testing principle). Fixed by iterating positions
explicitly with boolean masks and writing into a pre-sized result Series
-- see `_val_adj_within_position`'s own docstring for why `groupby(...)
.apply(...)` is deliberately avoided here.

**Sort order.** `build_edge_frame` sorted by `Leverage` descending (with a
`CeilPct` fallback while ownership was unpublished) before this change --
Part 7.1 had already decided Leverage should no longer be a primary sort
anywhere, but left the actual sort-key swap for Part 7.2 to land, since
`ValAdj` is what replaces it. Now sorts by `ValAdj` descending,
unconditionally -- no ownership-dependent fallback branch needed, since
`ValAdj` never depends on `Own%` having published.

**`Val` itself is unchanged and still present** -- kept for its
`>= 3.0` cash-line threshold (Part 7.3), just no longer the sort key.

Verified: `pytest -q`/`ruff check`/`ruff format --check` clean (541
tests). Applied to the template first, then live, via the normal
`dfs setup link-edge --force` (regenerates every VLOOKUP, including the
newly-shifted ones past `ValAdj`) followed by `dfs setup polish`
(re-applies `ValAdj`'s own colour scale/width/format) -- `dfs doctor`/
`dfs setup audit-style` clean on both sheets afterward.

## Per-position raw-metric highlighting on EdgeRaw (2026-09-18)

**The ask.** Sam's actual workflow: filter EdgeRaw to a position or two,
sort by `ProjPts`/`Val`/`Ceiling`/`CeilVal`/`Salary`, and look for
outliers. Every one of those columns rendered as plain white numbers --
`EDGE_UNSCALED_PLAYER_METRICS` (`ProjPts`, `Ceiling`, `Val`, `CeilVal`)
had been deliberately excluded from the flat whole-tab colour scale
(`FIELD_COLOR_SCALES`) since a QB's real 27 points and a DST's real 10
points aren't comparable on one scale -- but "excluded" had never been
followed up with a real per-position alternative, so those four columns
just sat uncoloured. `Salary`/`DK Sal` stay deliberately unscaled
everywhere, unchanged by this work -- a constraint, not a quality;
colouring it would imply cheap is good.

**Why not a grouped-tab scale (Phase 4's own mechanism).** Phase 4's
`apply_grouped_color_scales` already independently rescales the SAME
column across multiple contiguous row blocks (used for Player Pool/
Lineups, which are laid out as literal per-position blocks). EdgeRaw
isn't laid out that way -- it's one flat list sorted by `Leverage`, every
position interleaved row by row -- so "QB's own rows" is a scattered set
of row indices, not one contiguous range. Never verified until this
session whether a single gradient rule's `ranges` field even accepts
multiple non-contiguous `GridRange`s with one shared min/max computed
over their union. Tested for real on the template's Scratch tab
(`scratchpad/test_multirange_gradient.py`, two interleaved "positions"
with wildly different magnitudes, 10-50 vs 1000-5000): confirmed a
single rule's `ranges` list DOES accept multiple disjoint ranges, and
min/mid/max are computed over their union only, completely independent
of every other cell on the sheet. That's the only mechanism that can
scope a gradient to "just the QB rows" when those rows aren't contiguous.

**The fix.** New `sheets.SheetsClient.add_color_scales_multi_range`
(list of specs, each spec's `a1_ranges` --plural-- becoming one rule's
`ranges` list; one batchUpdate for every spec) and new
`sheet_style.apply_edge_position_scales`: reads the `Position` column
once, buckets data rows by position value, then for each of the four
unscaled metrics builds one 3-point gradient rule per position, its
`ranges` merged into contiguous runs first (`_merge_contiguous`) to keep
the request small. Called from `polish_edge` right after
`apply_field_color_scales`; its own return count feeds `polish_edge`'s
summary string ("N per-position scale(s)").

**Verifying it actually worked -- a real dead end.** `polish_edge`
reported success (20 rules applied) but a `get_cell_formats` read of
`ProjPts` showed no background colour at all on every sampled row.
Turned out to be a bug in the VERIFICATION, not the feature:
`get_cell_formats` reads `userEnteredFormat`, which conditional-format
colour scales never populate -- they're a computed overlay that only
ever shows up in `effectiveFormat`. Re-reading with
`fields: "...effectiveFormat(backgroundColor)"` (the same field
`test_multirange_gradient.py` already used) showed real, distinct
per-row colours immediately. Confirmed by directly listing the sheet's
`conditionalFormats` metadata too: 20 real `gradientRule` entries exist,
each scoped to one position's rows in one metric column. A screenshot of
the template's EdgeRaw tab confirmed it visually as well -- colours vary
sensibly within a position even with positions interleaved by the
Leverage sort. Lesson for next time a colour-scale write needs
verifying: always read `effectiveFormat`, never `userEnteredFormat`.

Applied to the template first, verified (`dfs doctor`/
`dfs setup audit-style` clean, screenshot), then to the live sheet the
same way. Tests: `test_polish_edge_scales_raw_metrics_per_position_via_
multi_range_rules` in `test_sheet_style.py`;
`test_add_color_scales_multi_range_*` in `test_sheets.py`.

## Column-group toggle position, EdgeRaw + linked tabs (2026-09-18)

**The bug.** Sam looked at the live sheet after the zone-label rollout
and flagged that the collapsed-group `+` toggles looked like they
belonged to the wrong zone -- e.g. the toggle that expands GAME's own
hidden columns rendered floating in front of `CEIL`'s label instead of
next to `GAME`'s. Root cause: Google Sheets' own per-sheet
`gridProperties.columnGroupControlAfter` defaults to `true`, which
places a collapsed group's toggle immediately AFTER its last column --
and since this codebase's zone-label design puts each zone's label
column immediately BEFORE its own group (see the zone-labels section
above), "after zone N" is the same cell as "immediately before zone
N+1's label." Confirmed live: `gridProperties` on EdgeRaw explicitly
carried `columnGroupControlAfter: true`.

**The fix.** New `SheetsClient.set_column_group_control_before`, a thin
`updateSheetProperties` call flipping that one boolean to `false` per
tab. Called once at the top of both grouping call sites, before
`clear_column_groups`/`group_columns` run: `polish_edge` (EdgeRaw) and
`sheet_links.link_edge_columns` (PlayerPoolRaw/Player Pool/Lineups --
the three tabs that get the same zone groups linked onto them). Purely a
rendering setting: doesn't move a cell, value, or group, and is safe to
set on every polish/link-edge re-run. Verified the write round-trips
correctly against a real sheet (Scratch, template) before wiring it in.
Tests: `test_set_column_group_control_before_flips_the_per_sheet_toggle_
position` (`test_sheets.py`), `test_polish_edge_sets_column_group_
control_before_the_group` (`test_sheet_style.py`),
`test_link_edge_columns_sets_column_group_control_before_the_group`
(`test_sheet_links.py`).

## Phase 6, Part 7.10 (2026-09-18): Player Pool ordering -- tag group, then salary

Sam, 2026-09-17: *"The pool should order players by position by salary
high to low, but grouped by Both, Cash, GPP."* Not a structural move (no
column moved or was added) -- pure formula work in
`sheet_pool_formulas.py`, changing how each of Player Pool's five
position blocks sorts internally.

**The design.** `_union_array` (Fix 2.10's Name+Salary pairs) grows a
third column: each row's Pool tag turned into a rank via `MATCH` against
a new `sources.edge.POOL_TYPE_SORT_ORDER = ["Both", "Cash", "GPP"]` --
deliberately a SEPARATE list from `POOL_TYPE_OPTIONS` (the dropdown's own
order), per the spec's own explicit warning: `"Both" < "Cash" < "GPP"`
alphabetically gives the right answer by coincidence; renaming a tag or
adding a fourth would silently stop matching it. `_name_formula`'s `SORT`
becomes two keys (tag rank ascending, then Salary descending);
`ARRAY_CONSTRAIN(...,cap,1)` still drops every helper column back out
regardless of how many there now are. `_overflow_formula` needed no
change at all -- `INDEX(...,0,1)` always takes column 1 (Name), whatever
else rides alongside it.

**A real Sheets-formula bug caught before it ever touched a real
formula.** `MATCH` does not broadcast elementwise against a multi-cell
range on its own in Google Sheets -- `{range, MATCH(range, {...}, 0)}`
resolves to `#REF!`. Confirmed empirically on the template's Scratch tab
(a controlled 4-row test) before writing a single line of the real
formula: bare `{}` fails, `ARRAYFORMULA(...)` wrapping the whole
expression works, and -- the pattern actually used here, since the
existing code has no `ARRAYFORMULA` anywhere -- `MATCH` broadcasts
correctly when it's ITSELF one of `FILTER`'s own array arguments. The
tag-rank column lives inside `_union_array`'s existing `FILTER(...)` call
for exactly this reason. The control cell's own tag lookup needed no such
handling: it reads one cell, not a range, so a plain scalar `MATCH` works
everywhere.

**The control cell has no Pool tag of its own.** Looked up against
EdgeRaw by name via `INDEX`/`MATCH` (not `VLOOKUP` -- Pool sits to the
LEFT of Name on EdgeRaw, same reason `_pool_type_formula` already uses
INDEX/MATCH instead of VLOOKUP), with `IFERROR` degrading a non-match (a
typed name EdgeRaw doesn't carry a tag for) to `_UNKNOWN_TAG_RANK`
(`len(POOL_TYPE_SORT_ORDER) + 1` = 4) -- sorts after every real tag,
never into an arbitrary position.

**Showing the groups.** New `sheet_style.POOL_TAG_TINTS` (Both/Cash/GPP,
the same muted "don't compete with the Flag chips" palette
`POSITION_TINTS` uses) and `_apply_pool_tag_tint`, called from
`polish_builder_tab` -- no-ops on PlayerPoolRaw/Lineups, which have no
`Pool` column of their own, same guard pattern `_apply_position_tint`
already uses for its own optional column.

**Verified live on the template**, not just via fakes, per the spec's own
"Verify" step: ticked 4 EdgeRaw QB rows across all three tags with
distinct salaries, ran the real `dfs setup add-pool-control` (the actual
`write_pool_formulas` call site), read Player Pool's QB block back and
got exactly the predicted order (`Both` descending by salary, then
`Cash`, then `GPP`); repeated for RB with the same result. Separately
verified the control-cell half: typed an UNTICKED player's name into the
add-a-player cell for WR alongside two tagged EdgeRaw ticks -- it sorted
after both tagged players, confirming the unknown-tag-sorts-last
fallback actually works, not just compiles. All test ticks/typed name
reverted afterward; `dfs doctor`/`dfs setup audit-style` clean on the
template before and after.

**Incidental regression, found and fixed in the same session:** an
earlier `ws.clear()` call against the template's `Scratch` tab (cleaning
up an UNRELATED empirical formula test, see above) wiped its real header
row (`QB, RB, RB, WR, WR, WR, TE, FLEX, DST` -- a genuine DK roster-slot
layout, not throwaway content), which `dfs setup audit-style` caught
(`style_flat_tab` skips styling a tab with an empty header entirely, so
the header loss went unstyled rather than erroring). Recovered by
reading the still-correct header off the LIVE sheet's own `Scratch` tab
(never touched) and writing it back to the template, then re-running
`dfs setup polish`. Lesson: `ws.clear()` on any tab that might hold real
content (not just formatted-but-empty test cells) needs the same
"read live, verify it wasn't real data" caution as any other cross-sheet
recovery in this file -- Scratch specifically is used for empirical
Sheets-behavior tests throughout this project's history and had
accumulated a real, load-bearing header along the way.

## Phase 6, Part 7.4 (2026-09-18): make stacks visible -- data + guardrails

The largest gap the review identified, per Sam's own words in the spec:
"the sheet currently cannot show you a stack at all." No solver, no
optimizer -- just the data and two hard correctness rules.

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-18 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | Two new columns, `GameID` and `TmRank`, inserted into the GAME zone right after `OppPosRank`. `GameID` existed already as an internal join key (`GameId`, used to attach Stadium/Roof/Wind) but was dropped before reaching the sheet -- now kept and renamed to its own header text. `TmRank` is new: salary rank within team+position. | `derived.EDGE_COLUMNS` 33 columns, GAME zone ends at `OppPosRank`. `sheet_columns.GAME` = `O/U, Spread, Team Implied, GameEnv, OppPosRank` (5). `LINKED_COLUMNS` 17 members. `PLAYER_POOL_RAW_COLUMN_ORDER` 35, `PLAYER_POOL_COLUMN_ORDER` 41, `LINEUPS_COLUMN_ORDER` 38. | `EDGE_COLUMNS` 35 columns (`GameID` at 18, `TmRank` at 19). `GAME` = 7 members (adds `GameID`, `TmRank`). `LINKED_COLUMNS` 19 members. `PLAYER_POOL_RAW_COLUMN_ORDER` 37, `PLAYER_POOL_COLUMN_ORDER` 43, `LINEUPS_COLUMN_ORDER` 40. | Live + Template | `derived.EDGE_COLUMNS`, new `derived._tm_rank_within_team_position`, `derived.build_edge_frame` (keeps `GameId`, renamed `GameID`; new `TmRank` column), `sheet_columns.GAME`/`LINKED_COLUMNS`, `sheet_style.EDGE_WIDTHS`/`FIELD_FORMATS` (both gain `GameID`/`TmRank`; deliberately absent from `FIELD_COLOR_SCALES` -- see that constant's own comment), every `EDGE_COLUMNS`-index-pinning test in `tests/test_sheet_links.py` (updated, not just re-asserted, per their own docstrings). |

**`TmRank`, and why it's a proxy, not a measurement.** Salary rank within
a player's own team AND position -- 1 is the highest-salaried player at
that position on that team (read alongside `Position`: "WR1", "RB1").
Computed once in `derived._tm_rank_within_team_position`, ranked by
Salary descending with ties broken by Name ascending (deterministic
regardless of the frame's own row order, which changes every sync since
EdgeRaw sorts by `ValAdj`). **This is a crude proxy for target hierarchy,
not a measured one** -- salary reflects the market's own belief about
usage, not actual target share -- so it deliberately gets no colour
scale (scaling it would visually imply it's a ranked quality) and
`docs/CALCULATIONS.md` says so explicitly.

**Two new Lineups `Issues` guardrails, both real warnings (not the
reported-only stack-shape metric -- see Part 7.5):**

1. **Never roster a DST against your own QB's team** -- correlation
   -0.46, the largest single coefficient anywhere in the underlying
   review; when this DST does well, it's specifically at this QB's
   expense.
2. **Max one RB per game.**

New `sheet_style._stack_check_formula` (an `INDEX`/`MATCH` lookup for
rule 1, a `SUMPRODUCT`/self-referential `COUNTIFS` duplicate-count for
rule 2), wired additively into `_totals_check_formula` -- a real
violation is appended alongside whatever OVER/INCOMPLETE/OK the
cap/completeness check already produced, never silently masked by it
(same "don't let one condition hide another" principle `Flags` already
established after Part 1.1's LINE-suppresses-everything bug).
`polish_guardrails` degrades gracefully (skips the two new checks, exact
prior behaviour) if Position/Team/Opp./GameID aren't all linked yet.

**Both new formula mechanisms confirmed empirically on the template's
Scratch tab before shipping**, not assumed: the self-referential
`COUNTIFS`-inside-`SUMPRODUCT` duplicate-detection idiom, and the
`INDEX`/`MATCH` cross-lookup, each tested with both a violating and a
clean lineup shape and read back to confirm the resolved value in both
directions. A third case tested along the way, because it's a real,
not-hypothetical scenario: two RBs both with a genuinely BLANK `GameID`
(an un-synced `nflverse_games` week) do NOT spuriously trigger `RB/GAME`
-- confirmed a self-referential `COUNTIFS` criteria that resolves to
blank behaves differently from an explicit `""` literal criteria (the
latter matches blank cells; the former doesn't), which is exactly the
opposite of what an assumption would have guessed and would have meant
the check fired on every still-un-synced week if gotten wrong.

**Deliberately NOT built, per the spec's own text:** a QB+RB stack rule
(sources disagree wildly, 0.07 to 0.43 correlation, and the two that
measured it carefully call it functionally zero); stack SHAPE (QB+1 vs
QB+2 vs QB+3) as an `Issues` warning -- it's reported, not warned about,
in the lineup-metrics block instead (Part 7.5), since there's no cash/GPP
tag per lineup (Part 7.3) and a QB+2 warning would fire wrongly on a cash
lineup that should have no stack at all.

**The "game-grouped view" (players grouped by GameID, games sorted by
total, physical adjacency for spotting stacks) is DEFERRED, not built**
-- asked Sam directly (2026-09-18) rather than guess at the real design
decisions it needs (row budget per game, which positions to include, how
to rank games) that the spec's own terse text doesn't pin down, and that
overlap heavily with Part 3/7.6's upcoming Board rebuild (its own "Slate
shape" and "Stack candidates" panels cover this same need with a clearer
spec to build against). Sam: fold it into the Board rebuild rather than
build a standalone tab now that would likely need rework days later.

## Phase 6, Part 7.5 (2026-09-18): the lineup-level metrics block

Sam's own spec called this "the highest impact-per-effort item
available" -- every published DFS target (ownership, stacking,
uniqueness) is a LINEUP property, and the tool had been entirely
player-level until now. Pure formula work, no new data source.

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-18 | `Lineups` | Six new columns inserted right after `Issues`: `Stack`, `Games`, `Bring-back`, `Own% Used`, `Sub-10%`, `Min Unique` (new `sheet_lineup_metrics.LINEUP_METRIC_HEADERS`). Everything from `Edge ↗` onward shifts six positions right. `Exposure`'s header row (not a structural move, but changed the same day) gains three portfolio cells at K1:P1: `Distinct QBs`, `Shared QB?`, `Distinct games`. | `sheet_columns.LINEUPS_COLUMN_ORDER` 40 columns, `Issues` at index 14, `Edge ↗` at 15. `Exposure`'s header row: A-J only (`Slots filled` the last pair, I1/J1). | `LINEUPS_COLUMN_ORDER` 46 columns, `Issues` still at 14, `Stack` at 15 through `Min Unique` at 20, `Edge ↗` now at 21. `Exposure`'s header row: A-P, K1:P1 the new portfolio headline. | Live + Template | New `sheet_lineup_metrics.py` (whole module), `sheet_columns.LINEUPS_COLUMN_ORDER`, `sheet_views.build_exposure` (new `lineups_header_row` param, reads `Lineups`' own header for `Pos.`/`GameID` letters rather than assuming EdgeRaw's), `cli.py`'s `sheets_polish`/`sheets_build_views` call sites. |

**The six per-lineup columns**, all written once per lineup block onto
its own TOTALS row (blank on every slot row, same convention `Issues`/
`% of Cap` already use for a whole-lineup property):

- **`Stack`** -- e.g. `"QB+2 (KC) + 1 bring-back"`, `"QB+0 (KC)"` (no
  stack), `"no QB"` (still-partial lineup). Stack count is every OTHER
  rostered player (any position) on the QB's own team; bring-back count
  is every rostered player in the QB's own `GameID` but on the OPPONENT's
  team.
- **`Games`** -- distinct `GameID`s across the 9 picks.
- **`Bring-back`** -- Yes/No, a plain-language mirror of `Stack`'s own
  bring-back count for a reader scanning the column rather than parsing
  the string.
- **`Own% Used`** -- summed projected ownership. Blank (not a
  confidently-wrong 0%) until `OwnStatus` says ownership is real --
  same policy `Leverage` already established.
- **`Sub-10%`** -- count of picks under 10% projected ownership (Part
  7.5's own explicit threshold, `SUB_10_OWNERSHIP_THRESHOLD = 0.10`, not
  invented). Same ownership-published guard as `Own% Used` -- otherwise
  every player reads exactly 0% pre-publish, making this read "9/9"
  every single week before Tuesday.
- **`Min Unique`** -- the smallest count of this lineup's own picks
  ABSENT from some other lineup, minimized over every other lineup in
  the build. Answers "how different is my most similar other lineup" --
  `O(lineups^2)` in lineup-block count (each lineup's own formula names
  every OTHER block's range once), fine at the 20-lineup scale this
  sheet is built for.

**Portfolio-level, on `Exposure`** (not `Lineups` -- Exposure is already
the portfolio-analysis tab): `Distinct QBs` and `Distinct games` used
across the WHOLE build, plus a plain `Shared QB?` Yes/No (a QB rostered
in more than one lineup) -- found by reading `Lineups`' own real header
for `Pos.`/`GameID`'s column letters, never EdgeRaw's.

**Two genuinely new Sheets-formula mechanisms, both confirmed empirically
on the template's Scratch tab before shipping:**

1. `COUNTIFS(range, "<>"&formula_expression)` -- "not equal to" a
   FORMULA-computed value (the QB's own team), not a literal or a plain
   cell reference. Confirmed this concatenates and evaluates correctly,
   same as it would with a literal.
2. The cross-lineup `MIN` -- `9 - SUMPRODUCT(COUNTIF(other_range,
   this_range) > 0)` per other lineup, wrapped in one `MIN(...)` --
   tested against three small (3-player) mock lineups with a known
   overlap pattern, confirmed the resolved minimum matched the
   hand-computed expected value.

**Deliberately NOT built, per Part 7.4's own text (restated here since
7.5 is where a reader would look for it):** player-level exposure caps --
"at 4-8 lineups they are actively harmful, they force Sam off his best
plays for no portfolio benefit." Exposure stays a REPORT, never a
constraint. Stack SHAPE (QB+1 vs QB+2 vs QB+3, a real GPP-target finding
from the underlying review) is reported via `Stack` here, never warned
about in `Issues` -- there's no cash/GPP tag per lineup (Part 7.3), so a
shape warning would fire wrongly on a lineup that should have no stack
at all.

## Column-group regression, found live right after Part 7.5 shipped (2026-09-18)

Sam noticed the GAME zone's `GameID`/`TmRank` columns weren't inside
EdgeRaw's own collapsible group, then separately asked whether every
zone was correctly collapsible on all the tabs it should be -- a
question worth actually checking rather than assuming, given the first
half had just turned out to be true.

**Two distinct bugs, same root cause: GAME gained new members (Part
7.4's `GameID`/`TmRank`) after every other zone's own membership had
been stable for a while.**

1. **EdgeRaw specifically:** `sheet_style.EDGE_COLUMN_GROUPS`' own
   `("OverUnder", "OppPosRank")` tuple (a hardcoded first/last boundary,
   unlike the other three tabs' self-deriving-from-the-header approach)
   never got updated when `GameID`/`TmRank` were added to `EDGE_COLUMNS`
   right after `OppPosRank` -- both sat outside the collapsed range,
   always visible regardless of the group's own state. Fixed:
   `("OverUnder", "TmRank")`.
2. **PlayerPoolRaw/Player Pool/Lineups:** checked directly against the
   live sheet's own `columnGroups` metadata (not assumed) -- GAME had
   **no group at all**, while Ceiling detail/Movement/Weather (no
   membership change) were fine. Root cause: `sheet_links.
   link_edge_columns` computes each zone's grouping from the header
   BEFORE `sheet_reorder.migrate_tab_to_designed_order`'s own subsequent
   `reorder_tab_columns` call physically moves a newly-appended column
   into its designed position -- `link_edge_columns`'s own contiguity
   check (deliberately conservative, see its comment) correctly declines
   to group a zone whose members aren't YET physically adjacent, but
   nothing ever re-derived the grouping after the move that would have
   made them adjacent. `GameID`/`TmRank`, freshly appended past the end
   of the header at the moment `link_edge_columns` ran, meant GAME's
   `indices` weren't contiguous at that instant -- so it was silently
   skipped rather than merged incorrectly, which is the right failure
   mode for the WRONG problem (this project's history has plenty of
   "silently wrong" incidents; "silently skipped" is a first).

   Fixed in `migrate_tab_to_designed_order`: re-run `link_edge_columns
   (force=True)` AFTER `reorder_tab_columns`, once every column is in
   its final position -- re-derives every zone's grouping correctly,
   idempotent (same formula text written a second time, matching this
   function's own established re-run guarantee).

**Verified the collapse/expand MECHANISM itself still works correctly**
(not just the membership bug) by testing it directly, live, on EdgeRaw:
expanded the (still-buggy-at-the-time) GAME group, confirmed the `-`
toggle appeared and worked, collapsed it again successfully. This ruled
out a UI-mechanism regression from the earlier column-group-toggle-
position fix and confirmed the actual problem was purely a group-
membership gap.

A real regression test (`test_migrate_tab_to_designed_order_groups_a_
zone_that_just_gained_a_new_member`) reproduces the exact incident using
a header that omits `GameID`/`TmRank` from the start (matching the real
pre-Part-7.4 state) -- confirmed it fails without the fix (GAME's group
missing entirely from `group_calls`) and passes with it.

## Lineups' stale "DEF" slot label silently disabled the DST/QB guardrail (2026-09-18)

Found verifying Part 7.4's DST-vs-own-QB guardrail against a real test
lineup on live (typed a real QB + a real teammate + a real opponent from
the same game, per this project's own "verify by reading resolved
values" discipline): the check never fired.

**Root cause has nothing to do with the guardrail's own formula.**
`Lineups`' `Pos.` column is STATIC TEXT, not a formula (confirmed with a
real `value_render_option="FORMULA"` read) -- one fixed DK roster-slot
label per row (`QB, RB, RB, WR, WR, WR, TE, FLEX, DST`), written once,
presumably by hand, well before this codebase's own "DST" convention
existed everywhere else (`derived.EDGE_COLUMNS`, Player Pool's own DST
block, every reference in `sheet_style.py`/`sheet_lineup_metrics.py`).
The defense slot's own label reads `"DEF"` instead, on all 20 blocks, on
both sheets -- confirmed by reading every single block's own last row
directly, not assumed from one instance. `_stack_check_formula`'s
`MATCH("DST", Position_range, 0)` can never find a slot literally
labelled `"DEF"`, so its own `IFERROR` guard silently degrades `dst_opp`
to `""` every time -- the check LOOKS like a working guardrail that
simply hasn't found a violation yet, which is exactly why it went
unnoticed until tested against real data instead of Scratch mock data
(where I'd typed "DST" myself, matching my own assumption rather than
the sheet's real, stale value).

Fixed with new `sheet_style.fix_lineups_dst_slot_label` / `dfs setup
fix-lineups-dst-label` -- verify-then-overwrite per block (only a cell
that actually still says `"DEF"` gets touched; anything else is reported,
never silently clobbered), same pattern as `fix-flag-split`/
`fix-pct-of-cap`.

**A known, separate, NOT-yet-fixed limitation surfaced by this same
investigation:** the "max one RB per game" check reads `Position_range
="RB"` -- which correctly identifies the two dedicated RB slots, but
CANNOT see a real RB rostered in the `FLEX` slot (a common real
construction; `FLEX`'s own label is always `"FLEX"`, never revealing
which position actually occupies it). Fixing this properly needs each
row's REAL player position (an inline lookup against `EdgeRaw` by name,
not the slot label) -- a genuinely new array-formula mechanism this
session hadn't verified yet. Flagged to Sam rather than shipped
half-verified; see whatever follow-up entry (if any) resolves it.

## RB/GAME false-positive: a formula-blank GameID self-matched another formula-blank GameID (2026-09-19)

Found immediately after the "DEF"->"DST" fix above, verifying the SAME
test lineup on live: `Issues` correctly showed `DST/QB` (confirming that
fix worked) but ALSO showed `RB/GAME`, even though the test lineup had
zero real RB players -- only a QB and a DST typed, every RB slot left
blank.

**This directly contradicted Part 7.4's own "confirmed live" claim**
(see that section above) that a self-referential `COUNTIFS` criteria
resolving to blank does not match another blank cell. That claim was
real, but the test behind it was wrong: it used two rows with BOTH
`Position` and `GameID` genuinely blank (never-typed cells), which
trivially cannot match `_stack_check_formula`'s own
`Position_range="RB"` term regardless of what `GameID` does. It never
exercised the REAL shape sitting on live: `Lineups`' two dedicated RB
slots have a real, non-blank `"RB"` label (the fixed DK roster-slot
text, present whether or not a name is typed -- same static-label fact
as the DEF/DST bug above) while `GameID` is a FORMULA cell
(`=IF($A="","",VLOOKUP(...))`) that RESOLVES to `""` when the slot has
no name. Confirmed directly on the template's Scratch tab: a
self-referential `COUNTIFS` DOES treat two formula-produced `""`
results as matching each other, unlike two genuinely-blank (never-typed)
cells, which don't match. That made `rb_per_game` fire on almost every
incomplete lineup with 2+ unfilled RB slots -- i.e. the normal state of
a lineup still being built.

Also confirmed on Scratch that the obvious-looking alternative,
`COUNTIFS(range,"<>")` as a blank-exclusion criterion, does NOT fix
this: that pattern treats a cell holding a formula as non-blank
regardless of what it resolves to, so it doesn't filter these rows out.
The fix that does work: a direct cell/range inequality
(`GameID_range<>""`) multiplied in as its own `SUMPRODUCT` term --
direct comparison correctly evaluates `FALSE` for a formula-blank cell,
unlike `COUNTIFS`' own `"<>"` criteria syntax.

Fixed in `sheet_style._stack_check_formula`'s `rb_per_game`; re-verified
against the same real test lineup, whose `Issues` then correctly read
`INCOMPLETE 2/9 DST/QB` with no `RB/GAME`. Test data cleared from live
`Lineups` afterward.

## Exposure/Lineups portfolio metrics: two bugs found auditing Part 7.5's new headline (2026-09-19)

Live end-to-end verification of the RB/GAME fix above surfaced two
unrelated bugs in Part 7.5's portfolio-level Exposure headline
(`sheet_views.build_exposure`), both only visible against a genuinely
EMPTY `Lineups` (no real names typed anywhere) -- exactly the state a
sheet is in right after a weekly reset, which is why neither showed up
during the original Part 7.5 rollout (tested against a partially-built
lineup, not an empty one).

**1. `Shared QB?` always read `"Yes"`, even with zero real QBs
rostered.** `qb_slots_filled` was `COUNTIF(Lineups!Pos.,"QB")` -- but
`Pos.` is the FIXED slot label, one `"QB"` row per lineup block
regardless of whether a name is typed there (same static-label fact as
the two bugs above), so this was always the block count (e.g. 20),
never "how many QB slots are actually filled." `20 > 0` is always true,
so `Shared QB?` read `"Yes"` unconditionally. Fixed by also requiring
the `Name` cell non-blank: `COUNTIFS(Lineups!Pos.,"QB",Lineups!Name,
"<>")`. `Name` is typed by hand, not a formula, so it's genuinely blank
when empty -- no formula-blank complication like `GameID` has.

**2. `Distinct games` always read `1` instead of `0` -- twice, for two
different reasons, found one after the other.** First: every lineup
block repeats its own header row, and that repeated row's `GameID` cell
literally contains the text `"GameID"` -- a real, non-blank string that
passed the original `GameID_range<>""` filter and always counted as one
phantom "distinct game." Fixed by excluding that literal text too:
`GameID_range<>"GameID"`. That fix exposed a SECOND, subtler bug: with
the header text now correctly excluded and genuinely zero real games in
progress, `FILTER`'s own result set is truly empty, and `FILTER` errors
on an empty result (`#N/A`) rather than returning nothing. `COUNTA`
silently absorbs that error into a valid count of `1` (an error value
still "counts" as present to `COUNTA`) *before* `IFERROR` ever gets a
chance to catch it -- so `IFERROR(COUNTA(...),0)` never actually
degrades to `0`. Confirmed step-by-step on the template's Scratch tab:
the bare `FILTER` call itself correctly returned `#N/A`, but wrapping it
in `COUNTA` (even with no `IFERROR` at all) silently turned that `#N/A`
into `1`. `ROWS` does not have this problem -- it propagates the error
instead of absorbing it, so `IFERROR(ROWS(UNIQUE(FILTER(...))),0)`
genuinely degrades to `0`.

**The second bug pre-dated this session and wasn't new to the portfolio
headline** -- `sheet_lineup_metrics.distinct_games_formula` (the
already-shipped PER-LINEUP `Games` column, live since Part 7.5) used
the identical `IFERROR(COUNTA(...),0)` idiom and had the same silent
flaw, showing `1` instead of `0` for every still-empty lineup block
since it shipped. Fixed there too, not just in the new code -- a real
correctness fix to existing functionality, discovered only because
fixing the portfolio version required understanding exactly why the
first "fix" attempt (`IFERROR(COUNTA(...),0)`) hadn't worked.

All four values (`Distinct QBs`, `Shared QB?`, `Distinct games`, and the
per-lineup `Games` column) reconfirmed correct live against a genuinely
empty `Lineups` tab after both fixes shipped.

## Week 3 feedback, A4 (2026-09-22): `Source` removed from Player Pool

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-22 | `Player Pool` | `Source` column (which of EdgeRaw's tick or the add-a-player control cell a row came from) removed entirely -- Sam: "No need for source column. In the pool." A real `deleteDimension` (`sheet_reorder.remove_header_columns`), not just dropped from the Python column-order constant, so every column after it physically shifts left to match. Confirmed by grep before removing that nothing else in the codebase read it. | `sheet_columns.PLAYER_POOL_COLUMN_ORDER` 41 columns, `Source` right after `Opp.`, ahead of `Edge ↗`. | `PLAYER_POOL_COLUMN_ORDER` 40 columns, `Edge ↗` now sits immediately after `Opp.`. | Live + Template | `sheet_columns.PLAYER_POOL_COLUMN_ORDER` (`"Source"` entry removed), `sheet_pool_formulas.py` (`_source_formula`/`_SOURCE_HEADER` deleted, `write_pool_formulas` no longer writes a Source header/formula column), `sheet_style.py` (`SOURCE_CHIPS` deleted, `"Source": 64` width entry removed, `"Source"` dropped from `polish_builder_tab`'s chip-column tuple). Removed live via `sheet_reorder.remove_header_columns(client, "Player Pool", ["Source"], header_row=PLAYER_POOL_HEADER_ROW)`, template first then live; both re-verified by reading the actual header row back afterward (not trusting the function's own return string), and `dfs doctor` re-run clean against both. |

## Week 3 feedback, A6 (2026-09-22): `Added` column accumulates add-a-player names

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-22 | `Player Pool` | New hidden column `Added`, appended past `In` -- Sam: "Adding a player in row one of the pool works, but only once. If you try and add a second in the same spot, the first is deleted." Root cause: the add-a-player control cell held one typed name and `sheet_pool_formulas._union_array` only ever read that one cell, so a second entry replaced the first in every formula depending on it. `Added` accumulates every name ever typed into the control cell this week (50 rows of capacity); `dfs sync` drains the control cell into it and blanks the cell (`sheet_pool_control.drain_control_cell_into_added_names`). Provisioned via `sheet_reorder.provision_missing_columns` (blank header text, appended, no data), then `dfs setup add-pool-control` writes its header text and wires the new formulas; hidden the same way `Id`/`Flag` are. | `sheet_columns.PLAYER_POOL_COLUMN_ORDER` 42 columns, ends at `In`. | `PLAYER_POOL_COLUMN_ORDER` 43 columns, `Added` appended after `In`. | Live + Template | `sheet_columns.PLAYER_POOL_COLUMN_ORDER` (`"Added"` appended), `weekly_reset.PLAYER_POOL_ADDED_NAMES_HEADER`/`PLAYER_POOL_ADDED_NAMES_ROWS` (new), `weekly_reset.clear_previous_week` (clears the accumulated list, same "typed weekly state" reasoning as the control cell itself), `sheet_pool_formulas._union_array`/new `_added_names_filter` (third union source, alongside EdgeRaw's ticks and the control cell), `sheet_pool_formulas.write_pool_formulas` (writes the `Added` header, computes and threads `added_range` through), new `sheet_pool_control.drain_control_cell_into_added_names`, `cli.py`'s `sync` command (calls the drain function after every live sync), `sheet_style.polish_builder_tab` (hides `Added` alongside `Id`/`Flag`). Provisioned live via `sheet_reorder.provision_missing_columns`, template first then live; `VLOOKUP`'s broadcast-inside-`FILTER` behavior (needed for the accumulator's per-row Salary/Position/Pool-tag lookups) confirmed empirically on the template's Scratch tab before shipping, same discipline this module's own docstring already used for the tag-rank `MATCH`. |

## Phase 6, Part 4 (2026-09-22): tab strip reordered by week phase, not Fix-2.12's frequency

`WEEK_ORDER`'s tab position used to encode Fix-2.12's "most-used-first"
frequency ordering while its own second tuple element (the family/colour
tag) encoded actual phase-of-week -- the two disagreed for at least one
tab (`Slate Grid`, tagged `decide`, sat physically among `contest`-tagged
tabs), which is exactly the kind of "colour says one thing, position says
another" mismatch `apply_tab_chrome`'s colour-coding exists to prevent.
Rewritten to the phase-of-week order Sam gave directly: `Instructions`
first, then one contiguous colour band per phase (decide -> build ->
contest -> money -> feed) with no interleaving. `SoSQB`/`SoSRB`/`SoSWr`/
`SoSTE`/`SoSDef` moved out of the visible strip into `HIDE_TABS` (still
fully styled/audited, just not competing for tab-strip space -- `SoSComb`,
the combined lookup everything else actually reads, stays visible).

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-22 | tab strip | Visible tab order/colour rewritten from a 21-tab, frequency-ordered list to the 13-tab, phase-ordered list Sam specified; the 5 individual SoS tabs moved from the visible strip into the hidden-staging group. No row/column inside any tab moved -- `set_tab_properties`' `index`/`hidden`/`color` only, same "order, colour and visibility only" contract `apply_tab_chrome`'s own docstring already states. | `sheet_style.WEEK_ORDER` (21 entries, position encoding frequency) | `sheet_style.WEEK_ORDER` (13 entries, position encoding week phase); `sheet_style.HIDE_TABS` gains the 5 SoS tabs | Live + Template | `sheet_style.WEEK_ORDER`, `sheet_style.HIDE_TABS`. Applied via `dfs setup polish` (`apply_tab_chrome`), template first then live; re-verified with `dfs doctor` clean on both afterward (structural checks only -- tab order/colour aren't something `doctor` inspects, so this was also eyeballed directly on both sheets). |

One judgment call flagged rather than silently made: Part 4's own given
table tags `Movement` as `contest`, but it is arguably a Thu/Sun research
tab (line movement since the week opened) closer in spirit to `decide`.
Left as `contest` per the table as given -- reordering fixed `Slate
Grid`'s visible mismatch, which was the actual complaint; `Movement`'s tag
is a smaller, more debatable case and changing it wasn't asked for.

## Phase 6, Part 4b (2026-09-22): five unused tabs removed

Sam confirmed on 2026-09-17 he does not use `Scratch` (a blank drafting
grid, no formulas) or the `EntriesRaw`/`GPPin`/`DKLineupsRaw`/
`DKLineupsFinal` hand-paste DK-contest-history chain -- `dfs` never read
or wrote any of the five except to clear/style them (`EntriesRaw`'s
contest history flows into Bankroll/Results via a CSV file on disk,
`dfs week close --csv` / `parse_contest_history`, not via this tab chain).
Grepped to confirm nothing else in the codebase referenced any of the
five before removing them.

Asymmetric by design, per Sam's own instruction: **template -- delete the
tabs outright** (a real Sheets tab delete, so a future weekly copy starts
clean). **Live sheet -- hide, don't delete** (`EntriesRaw` may hold real
pasted contest history a delete can't recover; a hidden tab is still
fully readable/writable by `dfs sync`, so hiding costs nothing). Both
paths share one function (`sheet_tab_removal.remove_retired_tabs`,
`mode="delete"` vs `mode="hide"`) so the two sheets can't drift onto
different tab lists.

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-22 | `Scratch` | Deleted (template) / hidden (live). No formula anywhere referenced it. | Present, visible in `HIDE_TABS`'s complement | Template: absent. Live: present, hidden. | Template: deleted. Live: hidden. | `config.LineupsConfig.scratch_tab` (removed), `config.example.toml`'s `scratch_tab` line (removed), `weekly_reset.SCRATCH_RANGE` (removed), `clear_previous_week`'s `scratch_tab` param and call sites in `cli.py` (removed), `doctor._expected_tabs`' `cfg.lineups.scratch_tab` entry (removed), `sheet_audit.AUDITED_TABS`' `("Scratch", 1)` entry (removed), `sheet_style.WEEK_ORDER`/`TAB_NOTES` entries (removed), `style_tier23_tabs`' `scratch_last_row` param and its `style_flat_tab(client, "Scratch", ...)` call (removed) |
| 2026-09-22 | `EntriesRaw` / `GPPin` / `DKLineupsRaw` / `DKLineupsFinal` | Deleted (template) / hidden (live). Pre-CLI parallel path, superseded by the CSV-based results path. | Present (`EntriesRaw`/`DKLineupsRaw` also in `HIDE_TABS`) | Template: absent. Live: present, hidden. | Template: deleted. Live: hidden. | `weekly_reset.ENTRIES_RAW_TAB`/`ENTRIES_RAW_RANGE` and the Fix-5G clearing block (removed -- moot once the tab is gone), `sheet_style.HIDE_TABS`' `EntriesRaw`/`DKLineupsRaw` entries (removed -- Part 4b's dedicated removal step handles them instead of the generic staging-hide list), `sheet_audit.AUDITED_TABS`' `("DKLineupsFinal", 1)` entry (removed), `sheet_style.TAB_NOTES` entries for `GPPin`/`DKLineupsFinal` (removed) |

New: `sheet_tab_removal.py` (`RETIRED_TABS`, `remove_retired_tabs`), CLI
command `dfs setup remove-retired-tabs --mode delete|hide [--sheet-id]`.
Executed and verified: `remove-retired-tabs --mode delete` against the
template, `--mode hide` against the live sheet, then `dfs setup polish`
(the `WEEK_ORDER` reorder above) against both, then `dfs doctor` clean on
both (aside from a pre-existing, unrelated `EdgeRaw`-empty failure --
today's `projections` sync failed upstream at TFFB's Optimizer endpoint
before any of this work started, so `edge` had nothing to compute from;
not caused by and not fixed by this change).

`docs/SHEET_REFERENCE.md`'s sections for all five tabs removed, replaced
with a pointer to this changelog entry. `EntriesRaw` was the only place
in the sheet holding a past contest entry's roster-slot detail (which
players were in which entry) -- `docs/planning/PROMPT_DATA.md` now documents that
this detail lives only in the DK export CSVs on disk going forward, for
whichever session eventually builds the results loop.

**Unrelated find during this verification pass, fixed in passing:**
`audit-style` flagged `Results`' `Black/White/Purple` column truncating
(110px < ~135px) on the live sheet -- no `Results` column had ever had an
explicit width in `BUILDER_WIDTHS` (`style_results` falls back to the
generic column width for anything not in the dict), and this one text
happened to be long enough to actually truncate. Added `"Black/White/
Purple": 150` to `BUILDER_WIDTHS`, same fix shape as `"Edge ↗"` above;
re-verified clean via `audit-style` on the live sheet afterward.

**Instructions tab, both sheets:** `remove-retired-tabs` also deletes
these five's own rows from `Instructions` (matched by column A text, not
a hardcoded row number, so it isn't thrown off by the tab's other rows
shifting over time), regardless of `--mode` -- the documentation should
read correctly on both sheets even though one deletes the tabs and the
other only hides them. Verified by reading `Instructions` back on both:
4 rows removed on each (`Scratch`; `EntriesRaw`; `GPPin`; the combined
`DKLineupsRaw / DKLineupsFinal` row), everything below shifted up
correctly, nothing else touched.

## Phase 6, Part 3 + 7.6 (2026-09-22): the Board rebuilt

The old Board ranked all 744 players on the slate -- a question Sam
already answers the moment he ticks his pool. Replaced entirely: one tab,
seven collapsible row sections (Queue and Slate shape open by default,
everything else collapsed) instead of three fixed side-by-side ranked
panels. Not a structural-changelog entry (the row/column-position table
above) -- nothing else in the codebase reads Board's cells, so there is
no downstream position for this rebuild to invalidate, unlike every
change in that table.

Two things made this bigger than a normal view rewrite:

- **Row groups didn't exist yet.** Every other collapsible section in
  this sheet groups *columns* (`sheets.group_columns`/
  `clear_column_groups`). New `sheets.group_rows`/`clear_row_groups`
  mirror them exactly (same `addDimensionGroup`/`deleteDimensionGroup`
  API, `dimension: "ROWS"` instead), committed separately first since
  it's pure, zero-sheet-risk infrastructure.
- **Queue can't be a pure formula.** "What changed since the last sync"
  only exists as a diff between two point-in-time snapshots -- a Sheets
  formula can't see yesterday's values. New `live_diff.
  diff_queue_changes` covers two gaps the existing `diff_edge_flags`
  couldn't: a move to `Avail = Q` (only `OUT`/`IR` ever set the `OUT`
  flag token) and a plain `Salary` change (never reflected in `Flags` at
  all). New `sheet_views.write_queue_section`, called from `dfs sync
  --live`/`dfs go` right after the diff is computed (NOT from
  `build_board` -- a routine `dfs setup build-views` re-run reads
  Queue's existing body back and restores it, same "typed/live input
  survives a rebuild" contract this module already has for Exposure's
  Target column). The pool tick itself isn't even in the local diff
  dataframe -- `sources/edge.py`'s `fetch()` never includes it, only
  `pre_upload`/`post_upload` read/write it directly against the live
  sheet -- so `write_queue_section` reads it the same way, by Id, off
  EdgeRaw's own current header.

New sections, replacing the old three panels:

| Section | Reads | Notes |
|---|---|---|
| Queue | EdgeRaw diff (Python) | Pooled players only; populated by live sync, not build-views |
| Slate shape | GamesRaw, WeatherRaw | New shootout flag, `derived.SHOOTOUT_TOTAL_THRESHOLD` |
| Per-position leaders | EdgeRaw | `ValAdj` + `ProjPts` blocks (7.6: was `CeilVal` only) |
| Punt finder | EdgeRaw | `ValAdj`, `Salary < 4000` |
| Stack candidates | EdgeRaw | Replaces the old leverage panel (7.6); QB + `TmRank=1` WR/TE per team, highest-`OverUnder` games first |
| Pool diagnostics | **Player Pool**, not EdgeRaw | New; salary spread, chalk/leverage counts, structural-gap checks |
| Chalk map | -- | Labelled placeholder; deferred until ownership publishes |

Row positions live as `BOARD_*` constants in `sheet_views.py` (`build_board`) and are
imported directly into `sheet_style.style_board`, so the two can't drift
the way EdgeRaw's own column order once did (this file's Phase 8
postmortem). Every EdgeRaw-derived section is regenerated fresh against
`derived.EDGE_COLUMNS` at call time via `sheet_views._rng`/`_col`, same
contract the pre-rebuild Board already had -- re-run `build-views` after
any EdgeRaw column reorder. Pool diagnostics is the same idea against
`sheet_columns.PLAYER_POOL_COLUMN_ORDER` via a new `_pp_rng`/`_pp_col`.

`docs/SHEET_REFERENCE.md` gained a `### Board` section (it had none
before this, an existing gap); `docs/CALCULATIONS.md` documents the new
shootout threshold next to `OverUnder`/`Spread`.

**Two real bugs, found live against the template, fixed before this
shipped to the live sheet:**

1. **Queue's rebuild-preserve read stale pre-rebuild data as if it were
   real Queue data.** A Board still on the OLD 3-panel design has TOP
   LEVERAGE's own rows sitting in exactly the row range the NEW Queue
   section now occupies -- the very first post-rebuild `build-views` run
   copied that stale data forward as if a live sync had written it.
   Fixed by checking the live column-header row against a new
   `BOARD_QUEUE_COLHEADER` constant before trusting anything below it;
   only genuinely matches on a sheet already on the new layout.
2. **`COUNTA`/`COUNTIF` do not propagate a `FILTER`-of-nothing's `#N/A`
   the way `MIN`/`MAX`/`AVERAGE`/`ARRAY_CONSTRAIN` do -- they count the
   single error as "1 item present."** Three formulas built on
   `IFERROR(COUNTA(FILTER(...)),0)` or an outer `IFERROR` around
   `COUNTIF(FILTER(...),FILTER(...))` never reached their empty-state
   fallback: the Board's own "Games" count read `1` against a genuinely
   empty `GamesRaw` (this bug predates the rebuild -- it was already in
   the pre-existing summary-banner formula, just never noticed since
   `GamesRaw` is rarely actually empty), `pool_empty_notice` never fired
   with an empty pool, and `pool_concentration` read "Most pooled players
   sharing one game: 1" with nobody pooled. All three rebuilt on
   `SUMPRODUCT((range<>"")*1)`, which never touches `FILTER` and so never
   generates an error to (fail to) catch. See `sheet_views.py`'s inline
   comments at each fix for the exact empirical repro.

`sheet_audit.SKIPPED_TABS`'s Board entry also had it still describing
the pre-rebuild "three side-by-side panels" shape -- corrected to
describe the new multi-section layout; Board stays skipped from the
generic header/width audit either way (still not a uniform
header-driven table).

Verified against the template: `dfs setup build-views`/`polish`, real
cell reads confirming both fixes (Games banner reads `0`, not `1`; the
empty-pool notice fires; Queue starts genuinely empty), `dfs doctor`/
`dfs setup audit-style` clean.

**A third bug, found verifying against the LIVE sheet** (the template's
own `GamesRaw` is empty, so this one only became visible once real game
data was present): Slate shape was never actually sorted by total --
the first cut was a straight per-row passthrough of `GamesRaw`'s own row
order, contradicting both the spec and what `docs/SHEET_REFERENCE.md`
already said about it. Fixed with a real `SORT`, which then needed a
second, independent `SORT` on the identical key (`Total`) to produce a
parallel `GameId` column (`sheet_views.BOARD_SLATE_GAMEID_COL`, column
J -- one past every other section's own rightmost visible column, so
hiding it can't hide real content belonging to a different section) as
a join key for the per-row Wind lookup, since a sorted row's `GameId`
can no longer be assumed to match its original `GamesRaw` row number.
Verified live that two `SORT`s on the same key preserve identical
relative order for tied values, so the two columns stay row-aligned.
Re-verified against both sheets afterward: live's Slate shape now reads
54.5 down to 39.5 in strict descending order with Wind correctly
attached to each game.

**A fourth bug, found by actually opening the sheet in a browser rather
than only reading cell values back:** a leftover plain (non-conditional)
grey background fill sat in column E, a spacer column between two of the
pre-rebuild Board's three side-by-side panels -- `clear_conditional_
formats` only clears conditional-format RULES, not a fill `format_range`
already set directly, so this survived the rebuild invisibly to every
cell-VALUE check run so far. Fixed by resetting `style_board`'s entire
working range to white before applying any of its own formatting, so a
future redesign can't leave the same kind of residue behind either. Also
fixed while looking: the `Instructions` tab's own `Board` row, on both
sheets, still described the pre-rebuild three-panel design -- updated to
name the seven sections. Neither of these two would have been caught by
`dfs doctor`/`audit-style` or a resolved-value API read -- both are
presentation-layer facts, not data -- which is why this pass finished
with an actual visual open of the live sheet, not just another round of
`read_range` calls.

## Phase 6, Part 7.8 (2026-09-23): actual DK ownership logging, investigated then deliberately scoped down

Step 1/2's investigation (per `docs/planning/HANDOFF_PHASE6.md`'s own "investigate
first" instruction) happened live, with Sam present, checking his real
DraftKings account -- not guessed:

- DK's per-contest "export full standings" CSV has real per-player
  ownership (`%Drafted`), found on a real completed contest's results
  page. Two side-by-side tables share the same rows (an unrelated entry
  leaderboard in columns A-F, per-player ownership in H-K) -- confirmed
  on a real 100-entry contest that a player used in more than one
  roster-slot TYPE across the field (e.g. some entries at `RB`, others in
  `FLEX`) gets one row per slot type, each a partial share; total
  ownership is the sum, not any single row.
- Weeks 1-2 history is still reachable on DK's site.
- A real per-contest export URL exists
  (`.../contest/exportfullstandingscsv/<id>`), found by inspecting the
  actual download button -- and DK's account-level contest-history
  export (the file `dfs bankroll sync --csv` already reads) has one too
  (`.../mycontests/historycsv?...`). Both could technically be automated
  via `dfs auth dk`'s already-existing authenticated browser profile.

**Built, then a real risk surfaced, then deliberately scoped back down.**
An automated per-contest fetch was built (a new `browser.
authenticated_get` helper, `bankroll.fetch_contest_history_csv`,
`ownership.fetch_standings_csv`, and optional-`--csv` auto-fetch modes
for `bankroll sync`/`week close`) -- then, exercising `dfs auth dk`'s
saved session during this same investigation, DraftKings' own site
returned a bot/geo-detection error ("you're off our grid") against the
authenticated Playwright browser. Sam decided the risk of automated,
repeated requests against his real-money account wasn't worth solving
"downloading CSVs by hand is tedious" -- **all of that automation was
reverted**, on his explicit instruction, before it ever shipped. `dfs
bankroll sync`/`week close` are unchanged (still `--csv`-only); their
docstrings now record that the automated path was built and investigated,
not just "not gotten to yet," so a future session doesn't rediscover the
same risk by re-attempting it.

**What shipped instead:** `src/dfs/ownership.py`
(`parse_ownership_export`, `append_ownership`, `already_logged_contest_ids`)
and `dfs ownership log --csv <file> [--week N] [--season Y]` --
purely local, file-based, no network call of any kind. Logs one
contest's real per-player ownership into `data/ownership_log.csv`
(gitignored, keyed by season/week/contest_id/player, idempotent re-runs
replace rather than duplicate). Verified against a real downloaded
export: 126 unique players correctly merged from 155 raw rows, a known
split-roster-slot player's ownership summed correctly (0.51 + 0.04 =
0.55), re-running the same file left the log at the same row count
rather than doubling it.

**Not yet built:** the calibration view itself (`ActualOwn - ProjOwn` by
decile/position) -- needs several weeks of logged contests plus the
archived `ProjOwn` snapshot cross-referenced by player-week, which is
only meaningful once `docs/planning/PROMPT_DATA.md`'s Move 1/2 (a queryable
history) exists. See that doc's own Section 7.8 note for the join
details a future session will need (name-matching, no DK player ID in
this particular export).

**Update, Week 3 follow-ups Item 3 (2026-09-23): dropped, not just "not
yet built."** The hand-logging volume this needed isn't coming -- Sam:
*"if we can't automate it, I'm not doing it,"* and automation stays off
over the same account risk described above. See `docs/planning/PROMPT_DATA.md`'s
7.8 entry and `docs/planning/ROADMAP.md`'s "Deliberately not doing"
section (also updated the same day to demote `Leverage` indefinitely,
since its own revisit condition was "a full season of ownership logs").

## Week 3 fixes, Fix 1 (2026-09-23): `week close`/`bankroll sync` dropped the week just played

Found by an independent review of the Week 3 session, before it caused
real data loss. Commit `e8bf3d5` fixed the wrong-week bugs from that same
session with two mechanisms, each correct alone but broken together:
`nfl_calendar.current_week()` now rolls the week number over on Tuesday
(`WEEK_ROLLOVER_LEAD_DAYS`, two days before that week's own Thursday
kickoff, matching how DK/Vegas/the waiver wire treat weeks industry-wide),
and `_sync_bankroll_from_csv`'s ledger filter (`entries_for_week`) was
scoped to `nfl_calendar.current_week()` -- **today's** calendar week.

Sam runs `week close` on Tuesday, right after Monday Night Football, once
the DK contest-history export is available. By Tuesday, `current_week()`
has already rolled over to the *next* week, so the filter kept **zero**
of the entries from the week that was just played -- and the next week
those same entries look like "an earlier week" and get skipped again.
They never reach the Bankroll ledger; nothing errors. Reproduced against
the real `nfl_calendar` code for a real Week 3 slate (Sunday 9/27, MNF
Monday 9/28): entries kept dropped from 2-of-2 (run Monday night) to
0-of-2 (run Tuesday or Wednesday). No data was actually lost yet -- Week
2's ledger was fully populated before this was caught -- but Week 3's
close, due the following Tuesday, would have been silently empty.

**The fix:** the ledger must be scoped to the week the *target sheet*
represents, not to today's date. Sheets are titled `Week N`
(`week.parse_week_from_title`, new); `_sync_bankroll_from_csv` reads the
connected sheet's own title via `client.describe()` and derives the week
from that instead of `current_week()`. If the title doesn't match `Week
<n>` (e.g. the template, titled `Template`), this raises rather than
falling back to `current_week()` -- that silent fallback is exactly the
shape of bug this replaces. `week close`/`bankroll sync` both gained a
`--week` override for hand-reconciling a specific week. Verified live:
the connected Week 3 sheet's title reads exactly `"Week 3"` (parses to
3); the template's title reads `"Template"` (correctly raises).

**`current_week()` caller audit** (Sam asked for the full list before
touching anything beyond this fix):

| Caller | Means | Verdict |
|---|---|---|
| `cli.py`'s `_sync_bankroll_from_csv` (`week close`/`bankroll sync`) | "the week just played" | **Was wrong, now fixed above** -- scoped by sheet title, not `current_week()`. |
| `sources/base.py`'s `SyncContext.current()` default | "the week being built" (what `dfs sync` should fetch data for) | Correct as-is. The Tuesday rollover moving this forward *is* the intended fix from `e8bf3d5` -- Tuesday should fetch the new week's odds/salaries. |
| `cli.py`'s no-arg launcher status (`state.week = SyncContext.current().week`) | "the week being built," shown for orientation | Correct as-is, same reasoning. |
| `cli.py`'s `dfs sync`/`dfs week new` (`ctx = SyncContext.current(...)`) | "the week being built" | Correct as-is, same reasoning. |
| `cli.py`'s `ownership_log` (`dfs ownership log`) default `--week` | Ambiguous -- logging a contest that already happened, like `week close`, but a purely local/optional file (`data/ownership_log.csv`), not the money ledger, and it never opens a sheet connection to read a title from. | **Flagged, not changed.** Same Tuesday/Wednesday gap could mislabel a logged contest's week if run late, but the command already documents "pass `--week` explicitly for anything other than the current week," the write is idempotent per-contest (safe to re-run with the right `--week` if a wrong one slips in), and there's no natural sheet title to derive from without adding a sheet round-trip to a command that otherwise makes none. Left as `current_week()`; revisit if Sam wants it scoped differently. |

No other `current_week()` callers exist in `src/dfs/` (grepped to confirm).

## Week 3 fixes, Fix 2 (2026-09-23): ValAdj's reference population narrowed to rosterable players

Sam's complaint on the A3 rework (previous entry): "cheap players float
too high." That rework moved the problem rather than fixing it -- on the
live Week 3 EdgeRaw, sorted by `ValAdj` (the default sort), the top 15
contained six tight ends, six players at $4,000 or less, and Mason
Taylor, a $2,500 TE projecting 5.7 points, at #14. Root cause: `PtsPct`
and `EdgePct` were both percentile ranks computed across **every** player
DraftKings lists at the position, backups included. Measured on the
2026-09-23 snapshot, 71% of listed TEs project under 2 points -- against
that pile, a 5.7-pt player looks like the 82nd percentile at TE, vs. only
66th at WR (57% of WRs project under 2). The metric was measuring "better
than the backup pile," not "a good play," and the deeper a position's
backup pile, the worse the inflation.

**The fix (decided by Sam, 2026-09-23):** keep the 50/50 blend, narrow
the reference population both percentiles AND the residual's own OLS fit
are computed against to rosterable players only --
`VAL_ADJ_ROSTERABLE_TOP_N = {"QB": 32, "RB": 64, "WR": 96, "TE": 32,
"DST": 32}`, a new named constant in `derived.py`. New
`derived._rosterable_pool_mask` (top N by `ProjPts` within position, or
everyone if the position is thinner than its own N); new
`derived._percentile_against_pool` replaces `_percentile_within` for
`PtsPct`/`EdgePct` specifically (still scores every row, pool or not --
"no blanks," a true backup just sorts naturally toward the bottom);
`_val_adj_residual_within_position` gained a `pool_mask` parameter so its
regression line is fit on the pool only, then applied to every row.
`docs/CALCULATIONS.md`'s ValAdj section rewritten with the population
rule, the before/after percentile table, and the constant.

**Reproduction against the reference snapshot,** exactly as the fix
prompt specified (`data/raw/edge/20260923T115601Z.csv`, the same
population counts confirmed: QB 85, RB 153, WR 247, TE 147, DST 26):

```
 1 Kenneth Walker III   RB  $7,400  26.4      9 Seahawks            DST $3,800  10.7
 2 Jaxon Smith-Njigba   WR  $8,600  24.1     10 Chase Brown         RB  $6,600  17.6
 3 Sam LaPorta          TE  $4,300  13.0     11 Malik Nabers        WR  $6,500  17.0
 4 Texans               DST $3,200   9.1     12 Jalen Coker         WR  $5,500  14.7
 5 Garrett Wilson       WR  $6,300  17.1     13 Brock Purdy         QB  $6,500  23.1
 6 Dalton Kincaid       TE  $5,500  15.0     14 Travis Kelce        TE  $4,500  11.9
 7 Chris Olave          WR  $7,200  19.1     15 Derrick Henry       RB  $7,700  21.2
 8 Parker Washington    WR  $6,000  16.3
```

13 of Sam's 15 reference names appear, in nearly the same order, and no
punts survive -- the fix works. **One honest discrepancy, reported rather
than tuned away:** Sam's reference list has no QB in the top 15 at all;
this reproduction has Brock Purdy at #13, and Adonai Mitchell (Sam's
#14) doesn't appear here in the top 15. The fix prompt's own text flags
"no QBs in the top 15" as something for Sam to eyeball, not a hard
invariant, and this implementation follows the spec's literal algorithm
(pool = top N by `ProjPts`; OLS fit on pool; percentile of every row
against the pool, generalized to non-pool rows via the same average-rank
formula `pandas.rank(pct=True)` already uses) with no invented thresholds
or hand-tuning. Flagged for Sam rather than adjusted further, per the fix
prompt's own instruction ("if you can't [reproduce], stop and ask rather
than tuning").

## Week 3 fixes, Fix 3 (2026-09-23): A7's blank row between pool tag groups, actually built

Sam: *"I like the blank line between cash/gpp/both blocks in the pool, but
it doesn't seem to consistently work."* Root cause: there was no
separator logic in `sheet_pool_formulas.py` at all -- Sam was seeing an
artifact of how `SORT` happened to lay out ties, not anything deliberate.
`docs/planning/HANDOFF.md` had also listed "A7/A8" as done, describing
only A8's content -- A7 itself was never built.

**The fix.** `_name_formula`'s flat `SORT(UNIQUE(union),3,TRUE,2,FALSE)`
is replaced by new `_grouped_with_separators_formula`: each of the three
pool tags (Both/Cash/GPP) becomes its own sorted sub-array, and a single
blank (`{"",0,0}`) row is inserted between adjacent NON-EMPTY groups only
-- skipping the separator entirely next to an empty group, so (for
example) a week where nobody's tagged "Both" at a position never leaves a
stray leading blank row before Cash's names. A 4th, unlabeled catch-all
group holds any row whose tag rank isn't one of the three known ones
(`_UNKNOWN_TAG_RANK` -- a typed control-cell/add-a-player name EdgeRaw
can't currently match a real Pool tag for), appended last with its own
conditional separator; the original flat sort included these rows too
(sorted last), and a first draft of this rewrite silently dropped them by
only filtering tag ranks 1-3, caught before shipping. Still purely inside
the array -- no real row insert, so `PLAYER_POOL_NAME_BLOCKS`' fixed
ranges never move; each separator still consumes one row of the block's
own capacity, same as any real player would.

**Two Sheets-formula findings, not documented anywhere obvious, verified
empirically on the template's own throwaway scratch tab before shipping**
(this codebase's standing discipline for non-obvious array-formula
behavior -- Scratch itself was removed in Part 4b, so a fresh tab was
created and deleted for this): `IFS`, and a plain `IF` used as one
argument of another function, do NOT reliably return a spilled multi-row
array result -- they silently give `#VALUE!` where a bare array, or a
plain `IF` used as a formula's own top-level result, spills correctly.
Every branch is therefore built from nested top-level `IF`s. And a `LET`
variable name that happens to read as a cell reference (`g1`, which
collides with cell G1) resolves to `#NAME?` even though it's a
syntactically ordinary identifier -- every name in the final formula
(`uArr`, `grpOne`, `hasBoth`, ...) is deliberately not cell-shaped.

**A second, more serious bug found during that same verification, not in
Fix 3's spec, but fixed because it sits in the exact code this rewrite
touches and was about to actively break Sam's Week 3 pool:**
`_union_array` stacks three sources (EdgeRaw ticks, the add-a-player
control cell, the accumulated added-names list) with `{a;b;c}`. `FILTER`
raises `#N/A` when a source has zero matches (this codebase's well-known
"FILTER of nothing" failure mode -- see the Board rebuild entries above),
and vertical-concatenating a healthy piece with an erroring one
propagates that single error to the ENTIRE combined array -- verified
directly: `{{"X",1;"Y",2};FILTER({"Z",3},FALSE)}` resolves to `#VALUE!`,
not the two real rows plus nothing extra. On the live Week 3 sheet, right
now, neither the control cell nor the added-names list has anything in
them (nobody's used add-a-player yet this week) -- so the moment Sam
ticked his first EdgeRaw checkbox, `control_filter`/`added_filter` would
each independently error and blank out the WHOLE block despite the real
tick existing. Confirmed this was pre-existing, not introduced by Fix 3:
the OLD flat-sort formula shape has the identical failure under the same
test. Fixed by individually `IFERROR`-guarding each of the three sources
in `_union_array` to a same-shaped blank placeholder row, with
`_grouped_with_separators_formula`'s new `uArr` step filtering that
placeholder back out before grouping (so it's never miscategorized as a
real "unknown tag" row), and `_overflow_formula` switched from
`COUNTA(...)` to `SUMPRODUCT((...<>"")*1)` -- COUNTA counts a
formula-produced `""` placeholder as present, the exact same class of bug
Board's empty-state guards hit; SUMPRODUCT doesn't.

**Capacity check** (as the fix prompt asked): each separator consumes one
row of a block's own cap. Worst case (all 3 tags populated, both
separators fire): QB 10 -> 8 real slots, RB 20 -> 18, WR 25 -> 23, TE 10
-> 8, DST 10 -> 8. All comfortably above what a normal week's pool uses;
no resize needed or made.

**Verified:** all three empty/non-empty group combinations plus the
all-empty and unknown-tag-rank cases, live on the template's scratch tab;
`dfs setup add-pool-control` re-applied to template then live; a real
3-tag tick across two real positions (QB and WR) on the LIVE sheet read
back with exactly one blank row between each tag group, then un-ticked;
`dfs doctor` clean on both sheets afterward. `docs/planning/HANDOFF.md`'s A7/A8
lettering also corrected (see Fix 3 in the session's own report to Sam).

## Week 3 fixes, Fix 4 (2026-09-23): the Instructions tab rewritten, badly stale

`Instructions` is now the first tab (Phase 6, Part 4) and describes
itself as the place to start -- but nothing in this codebase generates
it (it's hand-written prose on the template), and the only code that
touches it, `sheet_tab_removal.clean_instructions_tab`, only ever deletes
rows for the five tabs retired in Part 4b. Every other kind of drift went
uncaught. Read every row against current behaviour (not just the list
the fix review flagged) and found more than that list named:

- Two entire rows described tabs that no longer exist at all: `Pool
  Picks` (replaced by Player Pool's own add-a-player control cell, A3)
  and `PoolSort` (the Lineups pool deck's hidden helper -- the deck
  itself was removed in Phase 5). Both rows deleted outright (real
  `deleteDimension` calls via the existing `delete_rows`, matching
  `clean_instructions_tab`'s own row-removal pattern), not just
  reworded.
- `LevBasis` (renamed `OwnStatus` in Part 7.9) and `EdgeRaw`'s "sorted by
  Leverage" (replaced by `ValAdj`, Part 7.2) were both still named by
  their old identities.
- Lineups' own row-1 pool deck (rows 1-10, `Position`/`Sort by`/`Start
  at`) was described as if it still existed -- removed in Phase 5. The
  "type names starting at row 12" instruction was consequently also
  wrong (`LINEUPS_NAME_BLOCKS`' first block starts at row 2); the roster
  slot list still said `DEF` where the sheet has read `DST` since the
  DEF/DST guardrail fix earlier this project.
- Player Pool's own row described `Source`/`Overflow`/`Pool` at
  hardcoded letters O/Z/AA -- `Source` doesn't exist any more (A4) and
  `Overflow`/`Pool` now sit at AM/AN (`PLAYER_POOL_COLUMN_ORDER` has
  grown since those letters were written down).
- The SoS tabs' own row still said "MANUAL: copy and paste ... Not yet
  automated" -- sync has been automated since Phase 5, and the five
  individual tabs are now hidden from the visible strip (Part 4), with
  `SosComb` as the one meant to be checked directly.

Rewritten on the **template first**, then the live Week 3 sheet, using
the existing `SheetsClient.read_range`/`update_range`/`delete_rows`
primitives directly (matched by column A's own label text, never a
hardcoded row number, since two of the rewritten rows required deleting
earlier rows and everything below shifts). `dfs doctor` clean on both
afterward; every rewritten cell read back and checked against this
session's own knowledge of current behavior (not just the fix review's
list).

**Also found, not fixed (separate, smaller staleness, flagged for a
later pass):** `sheet_style.TAB_NOTES` -- the per-tab A1 cell notes,
generated by code (`apply_tab_notes`) -- has some of the identical stale
claims in its own hardcoded strings (EdgeRaw's note still says "sorted by
Leverage"; Board's still describes "three ranked panels"). Being
code-generated didn't prevent this: the MECHANISM regenerates the note
on every run, but the TEXT inside it is still hand-written prose nobody
updated when Leverage/Board changed. Worth keeping in mind for the
Instructions-generation proposal below -- generating the tab doesn't by
itself solve staleness if the strings inside the generator are still
manually maintained.

**Proposed, not built** (per the fix's own instruction): generate
`Instructions` from code the same way `TAB_NOTES` is, so a future
structural change has an actual code path to update instead of relying
on someone remembering to hand-edit a sheet. Shape: a `sheet_instructions.py`
module with an ordered list of (label, text) entries -- general-info
rows (the 5 non-tab rows: "THE ONE THING TO KNOW" etc.) plus one entry
per VISIBLE tab (hidden tabs, per Part 4's `HIDE_TABS`, excluded or
listed separately) -- and a `build_instructions_tab(client)` that writes
the whole tab in one pass, matching this module's `RETIRED_TABS`-driven
cleanup for tabs no longer present. The genuinely useful improvement over
today's hand-edited prose or `TAB_NOTES`-style hardcoded prose: pull numbers that
already have a named constant (Player Pool's caps from
`PLAYER_POOL_NAME_BLOCKS`, the roster slot list from wherever
`LINEUPS_NAME_BLOCKS`' own slot labels are defined, `POOL_TYPE_SORT_ORDER`
for the tag-group order) into the generated text via f-string
interpolation instead of typing `10, 20, 25, 10, 10` again -- that
specific class of drift (a real number changes in code, the doc still
says the old one) becomes structurally impossible rather than merely
less likely. Rough size: a new ~150-200 line dict-of-descriptions module
plus a ~40-60 line write function (mirroring `TAB_NOTES`/`apply_tab_notes`'s
own split), plus tests in the same style as `test_sheet_tab_removal.py` --
roughly 300-400 lines total, on the order of half a day's focused work.
Not started; needs Sam's go-ahead before building, per Rule Zero (a
sheet-generation redesign, not a bug fix).

## Week 3 fixes, Fix 5 (2026-09-23): stack signature no longer counts the DST

`sheet_lineup_metrics.stack_signature_formula` counted "every other
rostered player, any position, on the QB's own team" toward the stack
count -- which included a same-team DST, so a QB plus his own defense
(no real stack piece at all) read as `QB+1`, indistinguishable from an
actual one-player stack. Sam's decision: keep counting RBs (deliberate --
narrowing to just WR/TE was considered and rejected), just stop counting
the DST.

**Implemented as a denylist**, not an allowlist: `stack_count`'s
`COUNTIFS` already excluded `"<>QB"`; `"<>DST"` is the one criterion
added, on the same static `Pos.` column. Same exclusion added to `bring_
back_count` (an opposing DST isn't a real "bring-back" bet either, and it
fed the exact same displayed count) -- both in `stack_signature_formula`
itself and its standalone Yes/No twin, `bring_back_present_formula`.

**Why no FLEX-to-real-position resolution was needed here, unlike
`d08b0b3`'s RB/GAME guardrail fix:** that commit's own message already
analyzed this exact function while fixing a DIFFERENT guardrail's FLEX
blind spot, and concluded the stack signature's existing "any non-QB
teammate" denylist shape was already safe for FLEX -- DraftKings' FLEX
slot can never legally hold a QB or a DST, so a FLEX-rostered player's
own static slot label ("FLEX") already satisfies both `"<>QB"` and the
new `"<>DST"` without needing to look up his real position. The trap
`d08b0b3` actually warned against is the opposite shape: an ALLOWLIST of
`{"RB","WR","TE"}` would have needed FLEX resolution, since a FLEX row's
label is never literally "RB"/"WR"/"TE" and would have silently dropped
every FLEX-rostered stack piece from the count. A test
(`test_stack_and_bring_back_exclude_dst_not_via_an_rb_wr_te_allowlist`)
guards against that regression by asserting the formula never contains a
literal `"RB"`/`"WR"`/`"TE"` string.

Deployed directly via `write_lineup_metrics` (not the full `dfs setup
polish`, which touches unrelated formatting), template first then live;
formula text read back from both sheets to confirm the added `"<>DST"`
criterion landed exactly as intended.

## Week 3 fixes, Fix 6 (2026-09-23): five smaller items, all verified live

**6.1 -- Board's row-3 banner was stale.** It described a ranked
ceiling-percentile panel Part 7.6's Board rebuild removed entirely
("ranked by ceiling percentile instead; treat it as a ceiling ranking,
not a leverage ranking" -- there's no ranked panel left to describe).
Replaced with Part 7.1's own caveat (TFFB's ownership is a large-field
projection, Sam plays small-field, so Leverage wherever it's still shown
is directional at best), present regardless of publish status rather
than only in the unpublished branch.

**6.2 -- Lineups' "Remaining" label clipped to "Remaini".** A8 moved the
totals row's "Total"/"Remaining" labels into the `Opp.` column
(`polish_lineups_totals_rows`), but nothing widened `Opp.` for its new
content -- it was still sized for a 4-character "Opp." header.
`BUILDER_WIDTHS["Opp."]` widened 54 -> 80, reusing the exact value
already proven for this same "Remaining" text on `Val` (from before A8
moved the label off that column).

**6.3 -- average-remaining-per-slot showed a raw "5555.6".** That cell
lives in `Pts`'s column (otherwise dead on a totals row), whose
column-wide format is `"0.0"` (points) -- it needed its own per-cell
currency format (`FIELD_FORMATS["DK Sal"]`) to match "$5,556" one row
up, matching Val/DK Sal's own convention rather than the points format
it happened to inherit.

**6.4 -- an empty lineup block showed "no QB" and other false readings.**
`stack_signature_formula` showed "no QB" for a genuinely EMPTY block
(nobody's typed anything in yet), reading as a warning on a lineup
nobody has started -- now blank until at least one name is typed, with
"no QB" reserved for a block that has SOME picks but none of them a QB
(a real, useful warning). Checking every other lineup-metric cell for
the same class of bug turned up two more, both fixed the same way:
`sub_10_percent_formula`'s `COUNTIFS(...,"<0.10")` treats a blank cell
as satisfying "< 0.10" (Sheets coerces blank to 0 for a numeric COUNTIF
criterion), so an empty lineup with ownership published read "9" --
every unfilled slot miscounted as a sub-10%-owned pick, not just an
uninformative zero. `min_unique_formula`'s `COUNTIF(other_rng,
this_rng)` treats a blank cell in `this_rng` as matching any blank cell
in `other_rng`, so an unbuilt lineup compared against another
still-unbuilt one (near-universal early in the week, before every
lineup is filled) read "Min Unique: 0" -- indistinguishable from two
complete duplicates. Both now also blank while their own block is empty.

**6.5 -- the Edge ↗ column was still effectively full width.** A5
(Week 3 feedback) narrowed it from 64px to 60px, constrained by its own
header text ("Edge ↗" -- the column's lookup-by-name key everywhere in
this codebase, so renaming it is out of scope) needing at least ~57px to
avoid `dfs setup audit-style`'s own truncation check flagging it. 60px
is not "roughly a glyph's width" the way A5 originally asked for --
narrowed further to 28px (matching the per-row cell's own already-
glyph-only content, and the exact ~28px estimate A5's own code comment
already worked out), accepting that the HEADER text now visibly clips --
a deliberate trade-off for this one utility column. New `sheet_audit.
TRUNCATION_EXEMPT_COLUMNS` stops the audit tool from re-flagging this
specific, intentional case as a regression on every future run.

Deployed via `dfs setup build-views` (6.1) then `dfs setup polish`
(6.2-6.5, since `write_lineup_metrics`/`polish_lineups_totals_rows`/
`polish_builder_tab` are all called from that one command), template
first then live; `dfs doctor` and `dfs setup audit-style` clean on both
afterward.

## Post-Week-3-fixes: Instructions generated from code; TAB_NOTES' own staleness fixed (2026-09-23)

Fix 4's own proposal, approved by Sam after the fact: new `sheet_instructions.py`
generates the whole Instructions tab (`dfs setup instructions`), replacing
the hand-typed prose that had drifted stale enough to need Fix 4 in the
first place. Every fact with a real Python constant behind it is now
derived, not retyped: Player Pool's per-position caps
(`weekly_reset.PLAYER_POOL_NAME_BLOCKS`), Lineups' roster slot order and
block count (`models.ROSTER_SLOTS`, `weekly_reset.LINEUPS_NAME_BLOCKS`),
Player Pool's Overflow/Pool column letters
(`sheet_columns.PLAYER_POOL_COLUMN_ORDER`), and the pool tag-group order
(`sources.edge.POOL_TYPE_SORT_ORDER`). Which tabs get a row and in what
order stays an editorial choice, same shape as `sheet_style.TAB_NOTES`'
own explicit dict -- not derived from `WEEK_ORDER`/`HIDE_TABS`, since
Instructions documents several hidden "raw" sync tabs a reader needs
explained even though they're not in the visible tab strip.

**Verified by diffing the generated content against the live sheet's own
(already Fix-4-corrected) text, cell for cell, before ever writing
anything:** 29 of 30 rows matched exactly. The one mismatch was a real
bug -- `PlayerPoolRaw`'s own row still said `LevBasis` (renamed
`OwnStatus`, Part 7.9) on the live sheet, missed during Fix 4's manual
pass because that pass touched EdgeRaw/Player Pool/Lineups/SoS but never
PlayerPoolRaw's own description. This module fixes it as a side effect
of being freshly written from scratch rather than edited in place --
codified as its own test
(`test_playerpoolraw_row_says_ownstatus_not_the_stale_levbasis`).

Deliberately does NOT use a full-tab `write_tab` clear+rewrite -- targeted
per-row `update_range` calls instead, since Instructions' bold header
row, wrapped text, and column widths are hand-styled on the template and
no other `dfs` command ever touches them; a blanket clear risks stripping
formatting nothing else would restore. Never inserts or deletes a row.
Deployed template first then live; `dfs doctor` clean on both; column
widths (3826px on the body-text column) confirmed unchanged after the
write, since `values.update` cannot alter them regardless.

**What this doesn't fix, on purpose:** the surrounding English prose is
still hand-written and can still describe behavior incorrectly the
moment a feature changes and nobody updates this file -- generating the
ROWS from code doesn't generate the FACTS inside them. `sheet_style.
TAB_NOTES` hit this exact limit already (its own code-generation
mechanism didn't stop ITS strings from going stale) -- also fixed, same
session: Board's note still described the old 3-panel ranked-player
layout (now 7 sections), EdgeRaw's still said "sorted by Leverage" (now
`ValAdj`) and called Pool "the checkbox" (a dropdown since Fix 2.11), and
all five SoS notes still said "pasted in by hand" (automated since Phase
5). Deployed via `apply_tab_notes` directly, template then live.

## Week 3 follow-ups (2026-09-23): three gaps left by `5206e50`

An independent review of `5206e50` (the Instructions code-generation
commit) found it clean, but flagged three gaps it left open. All three
closed the same day.

**Item 1 -- a bad sheet title used to fail at Tuesday's `week close`, not
at `week new`.** Fix 1 made `week close`/`bankroll sync` derive the week
from the sheet's own title (`week.parse_week_from_title`, strict, no
fallback -- correctly). But Sam still had to TYPE that title by hand when
copying the template, and nothing checked it was right until the ledger
scoping itself failed on it days later. Two changes:

- `dfs doctor` gained a title check (`doctor._check_sheet_title`) -- the
  sheet's title must parse as `Week <n>`, with the template's own literal
  title (`"Template"`) exempted (there is no OTHER signal in this
  codebase that distinguishes "this is the template" from "this is a
  weekly copy" -- no dedicated sheet-id constant, no config flag -- so
  the title, the very thing being validated, is also the only thing
  available to make that exemption).
- `dfs week new` now TITLES the new sheet itself -- a real spreadsheet
  rename, new `SheetsClient.set_title` (`gspread.Spreadsheet.
  update_title`, a real `updateSpreadsheetProperties` call) -- derived
  from `nfl_calendar.current_week()` (or `--week`, new option). Sam no
  longer types the week number into Drive's rename box. The actual
  decision logic (compute the target title, refuse to silently overwrite
  an EXISTING `Week <n>` title that disagrees with the derived week) is
  a new pure function, `week.resolve_week_title` -- split out of cli.py's
  own wrapper the same way `parse_sheet_id_from_url`/`rewrite_sheet_id`
  already are, specifically so it has real unit tests rather than only
  being exercised by a real `dfs week new` run.

`week new`'s own pre-flight `dfs doctor` call runs BEFORE the rename (it
has to -- the confirmation, which includes the new title, comes first),
so at that moment the copy's title is expected to not parse yet (`Copy
of Template`, whatever Drive's dialog left it as) -- that's normal, not
a doctor failure. `run_doctor` gained `check_title: bool = True` so this
one caller can opt out of that specific check while every other check
(and every OTHER `dfs doctor` caller) still runs it for real.

WORKFLOW.md, the generated Instructions "Weekly workflow" row, and
`week new`'s own docstring updated so none of them tell Sam to name the
copy by hand any more (README's quickstart never did -- it only said
"File > Make a copy", no naming instruction to fix).

Not verified live: `SheetsClient.set_title` itself, against a real
sheet -- doing so would have meant renaming the actual template or the
live Week 3 sheet away from a title other code (this same check
included) depends on, or standing up a throwaway spreadsheet solely to
rename it, which felt disproportionate for a one-line wrapper around
gspread's own well-established `update_title`. Everything upstream of
the actual API call (`resolve_week_title`'s decision logic, `dfs
doctor`'s title check both exempting the template and flagging a bad
title) is unit- and live-tested.

**Item 2 -- the generated Instructions tab could still drift.** `5206e50`
built `sheet_instructions.py`, but nothing regenerated it except the
standalone `dfs setup instructions` command someone has to remember to
run -- the same "correct in code, stale on the sheet" drift class every
other `dfs doctor` check exists to catch, just not yet applied here.

- `sheet_instructions.py` refactored around one shared function,
  `render_instructions_grid() -> dict[row_num, (col_a, col_b)]` -- the
  single source of truth both the writer (`build_instructions_tab`) and
  the new doctor check build on, so the two can never disagree about
  what "correct" looks like.
- `dfs doctor` gained `doctor._check_instructions_drift`: one bulk
  `A1:B<last row>` read (not one read per row -- this tab is ~29 rows,
  and a doctor check shouldn't cost that many round trips), diffed
  row-by-row against the rendered grid.
- `dfs setup polish` now also calls `build_instructions_tab` -- polish is
  already the command run after every structural change, so it's the
  natural place to bring Instructions back in sync rather than relying
  on the standalone command.
- Checked first (per the review's own instruction) whether anything
  generated is legitimately sheet-specific (a title, a URL, a date) and
  would need normalising out of the drift comparison: nothing is --
  every fact `sheet_instructions.py` writes comes from a static Python
  constant, confirmed by reading through the whole module before writing
  the check. The comparison needs no per-sheet special-casing.

Verified live, end to end, on the template: hand-edited one cell
(Board's row body), confirmed `dfs doctor` failed with exactly that row
number named, ran `dfs setup instructions`, confirmed `dfs doctor` passed
clean again. Both sheets pass the new title check and the new drift
check as of this session.

**Item 3 -- docs only, recording that ownership calibration is off the
table.** Sam, 2026-09-23: *"if we can't automate it, I'm not doing it."*
The weekly hand-logging of DK ownership CSVs the ProjOwn-vs-actual
calibration plan depended on was never going to happen at the volume it
needed, now made explicit rather than left as an open "not yet built."

- `docs/planning/PROMPT_DATA.md`: Phase 6 Section 7.8's Step 3 (the
  calibration itself) marked DROPPED, with the reasoning and Sam's quote.
  Step 7.7 (Ceiling instrumentation) is untouched -- it depends on
  nflverse results data, not ownership logs, and still stands.
- `docs/planning/ROADMAP.md`: new entry in "Deliberately not doing (and
  why)" -- `Leverage`'s demotion (`PROMPT_PHASE6.md` 7.1) had one
  explicit revisit condition, "no earlier than a full season of
  ownership logs." That condition can no longer be met, so the demotion
  is now indefinite, not pending. `PROMPT_PHASE6.md` itself is left
  as-is (a historical record of what was decided, not a living doc).
- `dfs ownership log` itself is unchanged -- it works, it's tested, and
  Sam may still use it occasionally. Every doc that described it as
  feeding a future calibration (`ownership.py`'s module docstring,
  `docs/WORKFLOW.md` step 8, README's command table, the `dfs ownership
  log` command's own docstring) updated to describe it as a standalone
  record instead. `CONTRIBUTING.md`'s own Part 7.8 changelog entry above
  gets a short update note rather than a rewrite -- it's a historical
  record of what happened at the time, same reasoning as leaving
  `PROMPT_PHASE6.md` alone.

No sheet changes for this item, as specified.

**Item 4 -- "ownership published" could never become true (found and
fixed 2026-09-23, same day as Items 1-3, own commit `eb861c3`).**
`has_real_ownership`'s share was still measured over every player DK
lists, not the `VAL_ADJ_ROSTERABLE_TOP_N` pool Fix 2 had already scoped
`ValAdj`'s own percentiles to -- but TFFB only ever publishes ownership
for players who'll actually be rostered, so that share topped out around
38% and could never cross `OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5`.
Live symptom: `OwnStatus` read `unpublished` and `Leverage` was blank all
season, on every snapshot, even ones where ownership had clearly
published. Verified on the real 2026-09-20 15:51 UTC snapshot
(`data/raw/edge/20260920T155118Z.csv`, 668 players, 255 with `ProjOwn` >
0): 38% over the whole list vs. 90% over the 250-player rosterable pool.
Fix: `has_real_ownership` now measures its share against `val_adj_pool`
(the same mask `ValAdj` uses), not the full DK list. `derived.py` and
`docs/CALCULATIONS.md` updated; two new tests in `tests/test_derived.py`.
No sheet-structure change.

## LEVERAGE flag: rosterable pool only, top 7% each week (2026-09-24)

Decided by Sam; see `docs/planning/PROMPT_LEVERAGE_FLAG.md` for the full
brief. A follow-up to Item 4 above: once `OwnStatus` could actually read
`real`, `LEVERAGE_FLAG_THRESHOLD = 30.0` (a fixed threshold, no pool
restriction) turned out to have two problems, both found live. First,
`CeilPct`/`OwnPct` (and so `Leverage`) are percentiles over every player
DK lists, not just the rosterable pool -- on the 2026-09-20 snapshot, 10
of 30 flags at threshold 30 were players outside the pool (nine
$2,500-$2,800 backup TEs plus one injury-limited player, all looking like
a high-percentile ceiling only because the reference population included
the whole backup pile). Second, even pool-restricted, a fixed threshold
drifted week to week: the pool fire rate at 30 was 12-15% in Week 1
(9/10-9/15 snapshots) and 5-8% in Week 2 (9/19-9/20) -- the same failure
mode `LINE_MOVE_FLAG_THRESHOLD` had before it was retuned (Phase 6, Part
1.1).

Fix: `LEVERAGE_FLAG_THRESHOLD` removed. New `LEVERAGE_FLAG_TOP_SHARE =
0.07` -- only rosterable-pool members are eligible at all, and among them
the flag goes to the top 7% by `Leverage` each week (`derived.
_leverage_flag_eligible`, computed slate-wide inside `build_edge_frame`
before `_flags_for_row` runs, since that function only ever sees one
row). Ties at the cutoff are included, so the flagged count can run
slightly above the nominal 7%. Verified against two real snapshots after
the fix: 19 flagged on both the 2026-09-20 snapshot (250-player pool) and
a 2026-09-13 Week 1 snapshot (248-player pool), zero outside the pool
either time -- both slates had genuine ties right at the cutoff.
`derived.py`, `docs/CALCULATIONS.md`, and `tests/test_derived.py` (three
new tests: outside-pool exclusion, exact top-share count with no ties,
ties-at-cutoff inclusion). No sheet-structure change -- `Flags`/`Flag`
already existed as columns; only which rows populate `LEVERAGE` changed.

## Part C, C1-C3 in progress (2026-09-24): player join, DK scoring, Sleeper

Second projection sources, an aggregate column and snap share
(`docs/planning/PROMPT_PART_C.md`, superseding Part C of `PROMPT_WEEK3.md`).
Landed so far, each independently tested and verified against real data;
C4 (FantasyPros) and C5/C5b/C6/C7 continue in a later session.

- **C1, the player join** (`src/dfs/player_join.py`): matches a free
  source's player rows onto DK's own `Id` by `(normalized name, team,
  position)`; DSTs match by team alone. A hand-maintained, committed
  alias file (`src/dfs/player_aliases.csv`, empty until a real miss needs
  one) is the fallback for the residue normalization can't fix. Verified
  against the real 2026-09-20 snapshot + a live Sleeper pull: 248/250
  (99.2%) rosterable-pool match rate; the two misses (Travis Hunter,
  dual-eligible WR/CB; Kyle Juszczyk, a fullback DK lists as RB) are both
  noise-tier, not starters. `nflverse/nfldata`'s `teams.csv` `draft_kings`
  column turned out NOT to be a clean per-team abbreviation (it conflates
  both LA teams under `"LA"`, e.g. `"LA Rams"` and `"LA Chargers"`) --
  kept the existing hand-rolled `nflverse_games.NFLVERSE_TO_DK_TEAM` map
  instead of replacing it, per C1's own "if it isn't clean, ask"
  instruction (resolved without needing to ask Sam: Sleeper's own team
  codes already match DK's exactly, so no crosswalk was needed for it at
  all; FantasyPros' `JAC`->`JAX` drift is handled the same
  confirmed-live-only way as `nflverse_games`'s own map).
- **C2, DK scoring** (`src/dfs/dk_scoring.py`): re-scores component stats
  to exact DK Classic rules -- verified 2026-09-24 against RotoGrinders'
  independent scoring-comparison page (`draftkings.com/help/rules/nfl`
  itself is a client-rendered SPA with no scoring content in its raw
  HTML, confirmed both live and via the Wayback Machine), matches
  `PROMPT_PART_C.md`'s table exactly. Yardage/points-allowed bonuses use
  an expected-value treatment (`YARDAGE_CV`/`POINTS_ALLOWED_CV`, stated
  starting guesses, not fit to real game logs -- flagged to Sam per the
  prompt's own "don't bury a guess" instruction). Running C2's own
  required calibration check against real data found a real bug:
  Sleeper returns a `stats` dict for every player in its database,
  including ones with no real weekly projection at all (confirmed:
  Jayden Daniels, Caleb Williams both came back with nothing but a
  ranking placeholder) -- scoring that as a real 0 was silently pulling
  every position's mean-diff-vs-TFFB down by roughly 2-3 points. Fixed:
  `score_offense_row`/`score_dst_row` return `nan` (excluded from the
  aggregate, never a fabricated 0) when any required stat is missing --
  same "blank is not zero" rule `derived.py` already enforces elsewhere.
  Re-run after the fix, over the real 2026-09-20 rosterable pool: QB
  +0.50, TE -0.16, DST -0.57 (all fine); RB -1.55 and WR -1.95 exceed the
  prompt's own ~1-point stop-and-ask line, and hand-verified individual
  players' arithmetic is correct -- not a scoring bug, TFFB appears to
  project RB/WR volume more aggressively than Sleeper does. Reported to
  Sam; his call: ship it, document the caveat (see `docs/CALCULATIONS.md`
  once C5's aggregate lands).
- **C3, Sleeper** (`src/dfs/sources/sleeper_projections.py`, registered
  as source `"sleeper"`): Sleeper's own `pts_ppr`/`pts_half_ppr`/
  `pts_std` totals are never read -- every DK point comes from C2's
  re-scoring of Sleeper's raw component stats, so DK's full-PPR
  reception value applies regardless of what scoring format Sleeper's
  own totals assume. `order_by=pts_ppr` (not `order_by=ppr`, per C3's own
  warning) confirmed live to return real data. New `SleeperRaw` tab
  (Template then Live, per the two-sheet rule) holds the raw fetch --
  written and read back for real on both sheets, `dfs doctor` clean on
  both afterward. Deliberately left OUT of `cli.py`'s `LIVE_SYNC_SOURCES`
  -- satisfies C3's "make it skippable" instruction (a full `dfs sync`
  includes it, `dfs sync --live` doesn't) with no new flag needed.

## Part C, C4 (2026-09-24): FantasyPros, gated then unblocked by a real login

FantasyPros' anonymous projection pages cap at exactly 10 players per
position behind a `<div id="registration-module">` paywall -- confirmed
live on all five position pages (QB/RB/WR/TE/DST), nowhere near the
rosterable pool C2's validation and C5b's SPLIT tuning both need, and a
real gap from what C4's "server-rendered HTML, `pandas.read_html()` works
directly" premise assumed. Stopped and asked Sam per Rule Zero rather
than guessing around it; his call: authenticate. New `dfs auth
fantasypros` (`cli.py`, same interactive-login pattern as `dfs auth
tffb`/`dk`) opens a real browser window for Sam to log in by hand -- once
he did, confirmed live the gate disappears entirely (80 QBs, 124 RBs, 208
WRs, 123 TEs, 32 DSTs, no gate markup at all).

- `src/dfs/sources/fantasypros_projections.py` (source `"fantasypros"`):
  loads each position page inside that authenticated Playwright profile
  (`browser.persistent_context`, not a plain `httpx` GET, which would
  still hit the anonymous gate) and parses the rendered HTML with
  `pandas.read_html`. Honours `robots.txt`'s `Crawl-delay: 5` with a real
  `time.sleep(5)` between the five position requests (~25s total) --
  same as `sleeper_projections.py`, deliberately left OUT of `cli.py`'s
  `LIVE_SYNC_SOURCES` (C4's own "make it skippable" instruction, no new
  flag needed).
- Team codes: FantasyPros' own codes already match DK's for every
  offensive position except `JAC` vs DK's `JAX` (confirmed live),
  handled by `player_join.TEAM_ALIASES`. DST rows have no team-code
  column at all -- the `Player` cell IS the full team name ("Kansas City
  Chiefs"), converted via a hardcoded `FULL_TEAM_NAME_TO_CODE` (all 32
  teams, stable season to season, sourced from nflverse's own
  `teams.csv`).
- Found live, same underlying problem C2 already found in Sleeper's data
  (`stats` dict present but empty), encoded differently here: FantasyPros
  pads every position's page out to its full rostered depth, not just
  the players it has a real weekly projection for -- 11 of 80 QBs, 8 of
  124 RBs, 30 of 208 WRs, 10 of 123 TEs came back with EVERY page-sourced
  stat at exactly 0.0 (real example: Caleb Williams, a starting QB TFFB
  projects at 22.0, showed up as a literal 0.0 across the board). Fixed
  the same way: `_blank_unprojected_rows` treats an all-zero real row as
  no projection at all (`NaN`, excluded from the aggregate), never a
  fabricated 0.
- No 2-point-conversion column on any FantasyPros page, and no
  blocked-kick column on the DST page -- both set to a real, always-0.0
  value for every row (not this codebase's usual "blank is not zero"
  treatment, since there's no FantasyPros signal to be blank ABOUT; the
  field simply isn't part of what this source publishes at all).
- Match rate against the real 2026-09-20 rosterable pool (245/250,
  98.0%): QB 31/32, RB 63/64, WR 95/96, TE 30/32, DST 26/26. Calibration
  vs. TFFB after the zero-projection fix: TE -0.14, DST -0.69 (fine); QB
  -1.21 (borderline); RB -1.78, WR -1.56 (both outside the ~1-point
  band, same direction and similar magnitude to Sleeper's own RB/WR gap)
  -- consistent with Sam's existing call on Sleeper (ship it, document
  the caveat in `docs/CALCULATIONS.md` once C5's aggregate lands), so not
  re-asked for FantasyPros specifically.
- New `FantasyProsRaw` tab (Template then Live, per the two-sheet rule)
  holds the raw fetch -- written and read back for real on both sheets,
  `dfs doctor` clean on both afterward.

## Part C, C5 (2026-09-24): `AggPts`, a new spine column

`AggPts` -- the equal-weight mean of every DK-scored source with a real
projection for a player (TFFB's `ProjPts`, Sleeper, FantasyPros) --
inserted into the shared spine immediately after `Pts`/`ProjPts`, per
Sam's own instruction. Linked (VLOOKUP against EdgeRaw), same treatment
as `ValAdj`, since it's a whole-slate join/average, not a per-row native
formula. Feeds nothing else: `ValAdj`/`Val`/`CeilVal`/the Board/every
guardrail still key off `ProjPts` alone, unchanged.

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-24 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | New column `AggPts` inserted into the shared spine, immediately after `Pts`/`ProjPts` -- every column from `Val`/`ValAdj` onward shifts one position right. Linked (VLOOKUP against EdgeRaw) on the three builder tabs, same as `ValAdj`. | `derived.EDGE_COLUMNS` 35 columns (`ProjPts` at index 5, `Val` at 6). `sheet_columns.DECISION` = `DK Sal, Pts, Val, ValAdj, Ceil, CeilVal, Own%, Avail, Flags` (9). `LINKED_COLUMNS` 19 members (`ValAdj` first). | `derived.EDGE_COLUMNS` 36 columns (`ProjPts` still at 5, new `AggPts` at 6, `Val` now at 7). `DECISION` = `DK Sal, Pts, AggPts, Val, ValAdj, Ceil, CeilVal, Own%, Avail, Flags` (10). `LINKED_COLUMNS` 20 members (`AggPts` first). | Live + Template | `derived.EDGE_COLUMNS`, new `derived._attach_agg_pts` + `EdgeBuildResult.agg_pts_joins`, `derived.build_edge_frame` (new `sleeper`/`fantasypros` params), `sheet_columns.DECISION`/`LINKED_COLUMNS` (both gain `AggPts`), `sheet_style.FIELD_FORMATS`/`FIELD_COLOR_SCALES`/`EDGE_UNSCALED_PLAYER_METRICS`/`BUILDER_WIDTHS`/`EDGE_WIDTHS` (all gain `AggPts`), every EDGE_COLUMNS-index-pinning test in `tests/test_sheet_links.py` (same set ValAdj's own row above named) and `tests/test_sheet_views.py`'s slate-grid VLOOKUP-range test, `tests/test_sheet_style.py`'s multi-range-scale count. |

**Applied via the same mechanism `ValAdj` used** (`sheet_reorder.
migrate_tab_to_designed_order`, run through `dfs setup reorder-columns`):
provision the header name if missing, link it against EdgeRaw, physically
move it into position with a real `moveDimension` call, rewrite every
native PlayerPoolRaw-lookup formula whose own position shifted as a
result. Verified both sheets structurally identical before starting
(`PlayerPoolRaw`/`Player Pool`/`Lineups` headers checked against
`sheet_columns.py`'s designed order, minus `AggPts`, on both -- exact
match, no pre-existing drift to worry about).

**Verification, both sheets:** `EdgeRaw` rebuilt first (a full sync
naturally carries the new column since it's a plain rewrite, no formulas
involved); `dfs setup reorder-columns` reported exactly 1 newly-created +
1 column-move per tab, as expected; real formula reads (not resolved
values) confirmed `AggPts`'s VLOOKUP lands on EdgeRaw's own column 7 and
`Val`'s formula correctly re-derived its shifted PlayerPoolRaw column
index (8, not the old 7); real resolved-value reads cross-checked three
players (Jahmyr Gibbs, Jaxon Smith-Njigba, Christian McCaffrey) on
`PlayerPoolRaw` against `EdgeRaw`'s own row for the same player -- exact
match on `AggPts`/`Val`/`ValAdj` for all three. `dfs setup polish` +
`dfs doctor` + `dfs setup audit-style` all clean on both sheets afterward
(the pre-existing `SoSQB`/`SoSRB`/`SoSWr`/`SoSTE`/`SoSDef` "empty this
week" skips are unrelated -- nothing pasted into them yet this week).

## Part C, C5b (2026-09-24): `SPLIT↑`/`SPLIT↓` flag

No new column -- `SPLIT↑`/`SPLIT↓` join the existing `Flags`/`Flag`
system (`derived._flags_for_row`), lowest priority, below `CHALK`, so it
can never mask a higher-priority flag in the singular `Flag` column (the
exact bug the LINE flag caused once by sitting too high in that chain).
Compares TFFB's `ProjPts` against the mean of the *other* two sources
(Sleeper, FantasyPros) -- deliberately not `AggPts`, which already
includes TFFB. Rosterable-pool only, same restriction the `LEVERAGE` flag
got on 2026-09-24 (see that day's own entry above), for the same reason.
New `SPLIT_ABS_FLOOR`/`SPLIT_REL_THRESHOLD` constants, tuned against the
real 2026-09-20 rosterable pool to an 8.0% fire rate -- full distribution,
the constants' values, and the honest one-sided-firing caveat (the RB/WR
calibration gap C2 found dominates the fire pattern right now) are all in
`docs/CALCULATIONS.md`'s own Part C section, not repeated here. New chip
in `sheet_style.FLAG_CHIPS` (`_chip(FLAT_BG, FLAT_FG)`, the same muted
tone `CHALK` uses -- SPLIT is direction-neutral information, not
good/bad news the way `LINE↑`/`LINE↓` genuinely is). No sheet-structure
change -- `Flags`/`Flag` already existed; only which rows populate them
changed, same shape of change as the `LEVERAGE` retune.

## Part C, C6/C7 (2026-09-24): `Snap%`, a fifth collapsed group, and storage

`Snap%` -- offensive snap share from nflverse's free `snap_counts_
{season}.csv` release (`src/dfs/sources/nflverse_snaps.py`, source
`"snaps"`) -- inserted as a new **Usage** collapsed group, positioned
after Weather per Sam's own instruction. New `derived.USAGE_LABEL`
zone-label constant, joining `GAME_LABEL`/`CEILING_DETAIL_LABEL`/
`MOVEMENT_LABEL`/`WEATHER_LABEL`. C6 described routing this through a
pfr->gsis crosswalk; verified live the release already carries name/
team/position directly, so it joins the same way every other Part C
source does (`player_join.join_source_to_dk`), no crosswalk needed.
"Most recent completed week" resolved per player, not one global week
number -- see `docs/CALCULATIONS.md`'s own Part C section for why (a
real Thursday-night-game edge case, confirmed live the same day).

| Date | Tab | What moved | Old position | New position | Sheets | Invalidated/updated symbols |
|---|---|---|---|---|---|---|
| 2026-09-24 | `EdgeRaw`, `PlayerPoolRaw`, `Player Pool`, `Lineups` | New collapsed group `USAGE` (label + `Snap%`) inserted after `WEATHER`, before `INTERNAL` (`Id`/`Flag`) -- both shift two positions right. Linked (VLOOKUP against EdgeRaw) on the three builder tabs, same as every other collapsed-group metric. | `derived.EDGE_COLUMNS` 36 columns (`Wind` at index 33, `Id` at 34). `sheet_columns.LINKED_COLUMNS` 20 members. `PLAYER_POOL_RAW_COLUMN_ORDER` 38 cols, `PLAYER_POOL_COLUMN_ORDER` 44, `LINEUPS_COLUMN_ORDER` 47. | `derived.EDGE_COLUMNS` 38 columns (`Wind` still at 33, new `USAGE_LABEL`/`Snap%` at 34/35, `Id` now at 36). `LINKED_COLUMNS` 21 members (`Snap%` last, right before `INTERNAL`). `PLAYER_POOL_RAW_COLUMN_ORDER` 40, `PLAYER_POOL_COLUMN_ORDER` 46, `LINEUPS_COLUMN_ORDER` 49. | Live + Template | `derived.EDGE_COLUMNS`, `derived.USAGE_LABEL`, `derived.ZONE_LABELS` (gains `USAGE_LABEL`), new `derived._attach_snaps`, `derived.build_edge_frame` (new `snaps` param), `sheet_columns.USAGE`/`LINKED_COLUMNS`/`BASE_COLUMN_ORDER`/`PLAYER_POOL_COLUMN_ORDER`/`LINEUPS_COLUMN_ORDER` (all gain the new zone), `sheet_style.FIELD_FORMATS`/`EDGE_WIDTHS` (gain `Snap%`/`USAGE`), every EDGE_COLUMNS-index-pinning test in `tests/test_sheet_links.py`. |

**Applied via the same `dfs setup reorder-columns` mechanism** as
`AggPts` -- provisioned the `USAGE` label as a native placeholder (it's
not itself a VLOOKUP, just header text), linked `Snap%` against EdgeRaw,
moved both into position with 2 real column moves per tab (label +
data), rewrote every native PlayerPoolRaw-lookup formula whose position
shifted. Template first, then live, both `dfs doctor`/`dfs setup
audit-style` clean afterward (pre-existing empty-SoS-tab skips
unrelated).

**One real verification catch, not a data bug:** immediately after the
reorder, `PlayerPoolRaw`'s displayed `Snap%` read `"1"` for a player
whose real value was `0.67` -- traced to `dfs setup polish` not yet
having run against the live sheet in this pass, so the newly-moved
column still carried whatever number format used to live at that
physical position (rounding 0.67 to the nearest whole number for
display). The underlying stored value was correct the whole time
(confirmed via `read_range_unformatted` on both `PlayerPoolRaw` and
`EdgeRaw` -- both read `0.67`); running `polish` applied the real
`0.0%` format and the display corrected itself. Same class of "stale
inherited format at a moved position" issue `FIELD_FORMATS`'s own `Id`
comment already documents -- **`dfs setup polish` is not optional after
`reorder-columns`**, confirmed live rather than assumed.

**C7, storage:** no new code needed -- `sleeper`/`fantasypros`/`snaps`
are ordinary registry sources (`sources/__init__.py`'s `SOURCES` dict),
so `sync.run_sync`'s existing `store.save(name, df)` call already
snapshots each one to `data/raw/<source>/<timestamp>.csv` and
`data/current/<source>.csv` on every sync, the same as every source that
predates Part C.

## Commit messages / PR descriptions

Explain *why*, not just what -- especially for anything that was tried and
rejected first. Future-you (or the next contributor) benefits far more
from "X didn't work because Y, so this does Z instead" than from a list of
changed files. Several files in this codebase have long module-docstring
"here's what didn't work" sections for exactly this reason; that's a
pattern worth continuing, not something specific to how they were written.
