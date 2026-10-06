# For Claude sessions working in this repo

## What this is

A Python CLI (`dfs`) that feeds a Google Sheet: it pulls projections,
salaries, odds, and derived signals (leverage, ceiling value, game
environment, weather, line movement) into the sheet, and reads finished
lineups back out for DraftKings upload and bankroll reconciliation. The
**sheet is the product** -- lineups get built there, by hand, in the
browser. The CLI's whole job is keeping it fed with correct data and
never corrupting its structure while doing so.

## The central hazard -- read this before touching sheet structure

Google Sheets auto-shifts formula *range references* app-wide when you
insert or delete a row/column (`PlayerPoolRaw!$A:I` becomes `$A:J`
everywhere it's referenced, even from other tabs) -- but it does **not**
shift a hardcoded integer argument inside a formula (the `9` in
`VLOOKUP(x, range, 9, false)`), and neither does any hardcoded row/column
number sitting in this repo's own Python. Both halves of that sentence
have independently corrupted a live sheet already (see
`CONTRIBUTING.md`'s "Live-sheet changes" section and its structural
changelog for the exact incidents) -- it is easy to reintroduce blind,
and the fix is always the same: **derive positions, never hardcode them.**

Concretely: column/row positions come from named constants --
`derived.EDGE_COLUMNS` (EdgeRaw's own columns), `EDGE_DATA_OFFSET` (the
Pool column sits ahead of `EDGE_COLUMNS`, so every absolute EdgeRaw column
letter needs `EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET`),
`weekly_reset.PLAYER_POOL_NAME_BLOCKS` / `LINEUPS_NAME_BLOCKS` (where each
position's rows live on Player Pool / Lineups), and `sheet_links.
LINKED_EDGE_COLUMNS` (the EdgeRaw-derived block appended onto Player
Pool/Lineups/PlayerPoolRaw). If you're writing a formula, a style rule, or
a row/column range and you find yourself typing a literal number instead
of deriving it from one of these -- stop. That literal is exactly the
thing that silently breaks the next time something upstream of it moves.

Before merging any change that inserts, deletes, or reorders a row,
column, or tab on the actual sheet: re-check every formula elsewhere that
used to point past that position, verify with a **real API read of the
resolved value** (not a read-through of the formula text, and not just
trusting a command's own "OK" output), and add a row to
`CONTRIBUTING.md`'s structural changelog naming the Python symbols that
now encode the new position.

## The two-sheet rule

There is a live weekly sheet and a canonical template, and they must stay
structurally identical (same tabs, same column order, same row layout).
**Template first, then live**, for any structural change -- point a
one-off command at the template with `--sheet-id <template-id>` before
touching the live sheet with the same change. `dfs doctor` is the
automated check that the two haven't drifted; run it against both after
any structural edit.

Before running `dfs sync` or anything else that writes to a sheet, run
`dfs status` (or note the sheet title/URL any writing command prints) to
confirm you're pointed at the sheet you think you are -- `config.toml`'s
`sheet_id` is an opaque string, and a new sheet gets copied every week.

## The commands you'll reach for most in an agent session

The user's **standard week** is six commands (`dfs week new`, `dfs sync`, `dfs export`, `dfs sync --live` +
`dfs lineups late-swap`, `dfs week close`); everything else is reference. `docs/COMMANDS.md` lists every command
and option and is GENERATED from the CLI (`python -m dfs.commands_doc`): after adding or changing a command or
option, regenerate it -- `tests/test_commands_doc.py` fails if it is stale or a command has no section.

Plain `dfs` is a guided menu of that standard week (`launcher.py`) that prompts for any required argument; a new
command needs a `launcher.MORE_LABELS` entry (a test enforces it).
Typed commands also ask for a missing required argument at a terminal (`prompt_missing.py`; every Typer app needs
`cls=PromptingGroup`) and fail as before when piped/scripted.

- `dfs results update [--week N] [--all] [--sheet-id <id>] [--no-sheet]` -- scores every completed week against
  nflverse's actual stats and rebuilds the `Model Check` tab (also run at the end of `dfs week close`, which it
  never fails). Projection = last TFFB snapshot before each player's own kickoff; flags/ValAdj recomputed from the
  raw archives with current code.

- `dfs doctor [--sheet-id <id>]` -- read-only structural check (tabs
  exist, EdgeRaw's header matches `EDGE_COLUMNS`, `LINKED_EDGE_COLUMNS` is
  linked exactly once, Lineups' header repeats are where
  `LINEUPS_NAME_BLOCKS` expects, and the hand-built per-row formula ranges on
  Results, Season, DkSalClean and PlayerPoolRaw have a formula on every row).
  Run this after any structural change, against both sheets. Never writes
  anything. `dfs setup repair-formula-ranges [--sheet-id <id>]` rewrites the
  gaps it reports; it also expects Results/Bankroll/Season divisions to carry their
  empty-state guard (`dfs setup guard-empty-states [--sheet-id <id>]` restores them).
- `dfs setup sheet --sheet-id <id>` -- the full one-time sheet build (pool
  deck, Player Pool's add-a-player control row, view tabs, EdgeRaw linking,
  filters, protection, styling), in the one order that works; its own docstring
  (`setup_sheet` in `cli.py`) explains why that order. Everything under
  `dfs setup ...` is also runnable as an individual step.
- `dfs sheets ...` is a deprecated spelling of the `setup` commands (plus
  `doctor`) kept working this season as a compatibility alias -- prefer
  the current names above in new code and docs.
- `pytest -q`, `ruff check .`, `ruff format --check .` -- all three run in
  CI on every push/PR; run them before considering a change done. None of
  the tests need real credentials, a real sheet, or a browser.

## Where things are documented -- one canonical home per fact

| Doc | Read it when |
|---|---|
| `README.md` | You want the front door: what this is, install steps, the weekly command loop. No detail that belongs elsewhere. |
| `CONTRIBUTING.md` | You're adding a data source, or touching the live sheet's *structure* -- design principles this codebase enforces, the hazard above in full incident-level detail, and the structural changelog (every row/column/tab move, ever, and what depends on it). Read before any structural edit. |
| `docs/COMMANDS.md` | You need a command or option's exact spelling. **Generated from the CLI** -- never hand-edit; regenerate. Leads with the standard week; the rest is reference. |
| `docs/WORKFLOW.md` | You want the weekly grind phase by phase, with the exact command for each step and what "done" looks like. |
| `docs/TROUBLESHOOTING.md` | Something looks wrong on the sheet and you want symptom -> cause -> fix. |
| `docs/SHEET_REFERENCE.md` | You need to know what a specific tab or column means, or the canonical column order for a shared tab. |
| `docs/CALCULATIONS.md` | You want the exact formula/threshold behind a computed column -- the one place the math is supposed to live; don't let a docstring's summary of it drift into "close enough." |
| `docs/planning/README.md` | **Gitignored, personal.** Start here for anything under `docs/planning/`: it names `HANDOFF.md` as the file to read first and says which one `PROMPT_*.md` is the active round. |
| `docs/planning/HANDOFF.md` | **Gitignored, personal.** The starting point for a new session: what the tool is, the sheet ids, the standing rules, the gotchas, and where each kind of decision lives. Current facts only -- no history. Keep it that way; don't try to `git add` it. |
| `docs/planning/ROADMAP.md` | **Gitignored, personal.** What has been built, what is deferred, and what was deliberately not done. Same rule: don't try to commit it. |
| `docs/planning/archive/` | **Gitignored.** Finished prompts, older handoffs and the V2 plan. History only: never act on anything in it. |

If you change a command's name, group, or behavior, update all of:
README's Commands section, this file's command list above, `docs/COMMANDS.md`
(regenerate: `python -m dfs.commands_doc`), `docs/WORKFLOW.md`, the Instructions tab on **both** the live sheet and the
template, and every docstring/comment mentioning the old name
(`grep -rn 'dfs ' src/`) -- see `CONTRIBUTING.md`'s doc-sync checklist for
the full table of "you changed X -> update Y."
