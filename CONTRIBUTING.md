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
plus a handful of manual tab clears (see `docs/ROADMAP.md` for the exact
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
with no error. This actually happened once (see `docs/ROADMAP.md`'s
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
