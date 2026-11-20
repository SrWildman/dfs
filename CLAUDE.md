# For Claude sessions working in this repo

## What this is

A Python CLI (`dfs`) and a local Week page for one DraftKings NFL player, Sam. It pulls projections, salaries,
odds and derived signals into a Google Sheet, reads finished lineups back out for DraftKings upload, and
reconciles the bankroll. **The sheet is the source of truth**: lineups are built there, by hand. The code's job
is to keep it fed with correct data and never corrupt its structure.

**The product principle (Sam's words):** "The tool gives me the information that matters most, in the easiest
digest possible, so I can make the call and have the biggest edge over the field." The tool informs and Sam
decides. Nothing blocks, warns, caps or enforces. Nothing is auto-assigned: a hint is never a choice, and Sam's
locks, edits and typed values always win over the tool.

## Rules that are never broken

1. **Rule Zero.** If anything is ambiguous, contradicts what you find, or doesn't work as described, stop and
   ask Sam. Never guess or work around it. A command that fails: show the error and stop. No retry loops.
2. **Never push or open a PR without Sam's OK in chat, and never merge: Sam merges.** Build in your own git
   worktree, on a branch off `origin/main`: other sessions share this machine and the main checkout.
3. **Sheets:** template first, then live. Never write a closed week (a previous week's sheet is read only).
   Ask before `dfs week new`, and before writing any sheet other than the live week (the one `config.toml`
   points at). Say which sheet every write used. The sheet section below has the rest.
4. **Sources and logins.** Free and public first; respect robots.txt and terms; a new paid source needs Sam's
   yes. Log in only to sources Sam subscribes to (TFFB and FantasyPros: `dfs auth tffb`, `dfs auth
   fantasypros`), with his own login. ESPN is personal, non-commercial use: never redistribute it. There is a
   paid analytics site Sam does not want named: never name it, and never put it in the repo.
5. **Never a DraftKings login or automation of any kind:** no scraping, no fetching, no auto-entry, no browser
   automation on DraftKings (it tripped DK's bot detection once, on a real-money account). Sam downloads files
   by hand and the tool reads them. DK data never goes in git.
6. **Secrets:** API keys and the Google service-account key are never printed, logged, committed or put in a
   prompt. `config.toml` and `season.toml` are gitignored and stay that way.
7. **No scheduled or background jobs** (no cron, no launchd). Sam runs things himself.
8. **Kill processes by PID only.** Never `pkill` or `killall`: another session's job looks the same as yours.
9. **The run lock** (`run_lock.py`, `data/.run.lock`): one writing command at a time across terminals, the
   page's chat and its buttons. If it is held, wait. Never delete it; there is no `--force`.
10. **`data/` and `docs/planning/` are local and gitignored.** Never `git add` them. No test may touch the real
    `data/` or the network; `tests/conftest.py` fails any that does.
11. **Nothing changes a build or a shown number without proof and Sam's yes.** New signals ship shadow or
    default off. Report results as they are, "not proven" included.
12. **Your numbers are a copy.** The rules box's `Adjust` and `Ownership` lines (`my_frame.py`) make a framed
    copy of the slate (`MyPts`) for builds, `--iterate`, late swap, the simulator's field, chalk watch and the
    Cockpit. Never hand it to anything that saves or scores the model (`data/current/edge.csv`, Model Check),
    and never apply it twice (the Cockpit applies it itself).
13. **Season-long:** nothing is ever written to Sleeper, and the Season sheet (`season.toml`) is never the
    weekly sheet.
14. **The served page (`dfs week serve`) keeps its guards:** this Mac only (`127.0.0.1`), a token and a
    localhost `Host` on every request; the chat never auto-approves a call and never uses
    `bypassPermissions`; every sheet write from the page finds its cells by header, re-reads, refuses on a
    change, is one batch under the run lock and reads back; a closed week is refused; no DK access; the log
    never holds prompts. Don't weaken any of these.

## The sheet hazard: read this before touching sheet structure

Google Sheets shifts formula *range references* everywhere when a row or column is inserted or deleted
(`PlayerPoolRaw!$A:I` becomes `$A:J`, even from other tabs). It does **not** shift a hardcoded integer inside a
formula (the `9` in `VLOOKUP(x, range, 9, false)`), nor any row or column number in this repo's Python. Each half
has corrupted a live sheet (`CONTRIBUTING.md`, "Live-sheet changes"). The fix is always the same: **derive
positions, never hardcode them.**

- Positions come from named constants and functions:
  - `derived.EDGE_COLUMNS` (the data frame's append-only column list) and `derived.EDGE_SHEET_ORDER` (the order
    the EdgeRaw tab is written in; Pool sits ahead of it in column A);
  - every EdgeRaw column letter and VLOOKUP index from `derived.edge_sheet_letter` / `edge_sheet_index` /
    `edge_vlookup_index`, never `EDGE_COLUMNS.index` (a test fails on anything else);
  - `weekly_reset.PLAYER_POOL_NAME_BLOCKS` / `LINEUPS_NAME_BLOCKS` (where each position's rows live on Player
    Pool / Lineups), and `sheet_links.LINKED_EDGE_COLUMNS` (the EdgeRaw block linked onto Player Pool, Lineups
    and PlayerPoolRaw).

  If you are typing a literal row or column number into a formula, a style rule or a range, stop.
- A write from code finds its cells by header or label, re-reads before it writes, and reads back after.
- **Two sheets, kept structurally identical** (same tabs, column order and row layout): the canonical template
  and the live week. A structural change goes to the template first (`--sheet-id <template-id>`), then to the
  live sheet. `dfs doctor` (read-only) is the check that they haven't drifted: run it against both afterwards.
- Before merging a change that inserts, deletes or reorders a row, column or tab: re-check every formula that
  pointed past that position, verify with a **real API read of the resolved value** (not the formula text, and
  not a command's own "OK"), and add a row to `CONTRIBUTING.md`'s structural changelog naming the Python
  symbols that now encode the new position.
- Before any command that writes a sheet, run `dfs status` (or note the title and URL a writing command prints)
  to confirm which sheet `config.toml` points at. `sheet_id` is an opaque string, and a new sheet is copied
  every week.

## Build and test

- The **editing run** is a bare `pytest -n 6 --dist loadfile`: it skips tests marked `slow` or `browser` and
  fails an unmarked test over 3 s (mark it `@pytest.mark.slow` or make it faster). Naming a file runs all of it.
- **Before every PR**, locally: `GITHUB_ACTIONS=true pytest --full -n 6 --dist loadfile` (CI runs the same; a
  green editing run proves nothing about the builder), `ruff check .` and `ruff format --check .`. No test needs
  credentials, a real sheet, the network or a browser (Playwright tests run in Chromium and Firefox, and skip
  an engine not installed, as on CI).
- CI installs from the tracked `uv.lock` (`uv sync --locked --extra dev`): after changing a dependency in
  `pyproject.toml`, run `uv lock` and commit both. A new worktree needs its own `.venv` the same way.
- Don't start a full test run while another session's pytest or backtest is running (`pgrep -fl pytest`).
- Long jobs (a sync, a close, a backtest): run detached with a log file under `caffeinate -i`, and read the
  log. Google calls hang now and then.
- A new or changed command needs all of these, and tests enforce them:
  - `docs/COMMANDS.md` regenerated (`python -m dfs.commands_doc`; it is generated, never hand-edit it);
  - a place in `run_lock.WRITERS` or `run_lock.READ_ONLY`;
  - a `launcher.MORE_LABELS` entry, and a `launcher.MORE_TOPICS` topic for its `commands_doc.SECTIONS` section;
  - `cls=PromptingGroup` on every Typer app.

  Then follow the doc-sync table in `CONTRIBUTING.md` ("You changed / Update"), which includes the Instructions
  tab on **both** sheets.
- A change to the page's chat: run `python scripts/chat_smoke.py` (four turns against real Claude; not in
  pytest).
- Code shape: pure logic in one module, sheet or file I/O in a `sheet_*.py` or `*_store.py` beside it. Anything
  a sync or a close runs as a side effect fails soft: it warns, and the command carries on. A sync refreshes a
  tab only on a sheet that already has it; it never creates one.

## Running Sam's week

Sam runs the week by talking to a local session (a terminal, or the Week page's chat). **When he uses a weekly
or season-long phrase, open `docs/SHORTCUTS.md`, run exactly what it lists, in order, and report what its last
column says.** Always say which sheet the commands used. The standard week:

| Sam says | Run |
|---|---|
| "start week N" | an odds snapshot if none yet, his OK and the new sheet's URL, `dfs week new "<url>"`, `dfs doctor` |
| "sync" / "final sync" | `dfs sync` / `dfs sync --live`, then `dfs doctor` |
| "build me lineups" | `dfs lineups suggest` (stop if his pool is empty); after an edit on Suggested, `--iterate` |
| "record my entries" | `dfs entries record` (he downloads DK's entries file; never fetch it) |
| "late swap" | `dfs lineups late-swap --refresh-injuries` (`--field auto` if he downloaded a live export) |
| "close the week" | `dfs week close --research`, detached, with a log |
| "open the week" / "what now" | `dfs week serve` / `dfs week page` |

Every other phrase (the rules box, contests, season-long), each command's exact options and what to report back
are in `docs/SHORTCUTS.md`. Anything that is not a phrase is a normal request: `docs/COMMANDS.md` has every
command and option.

## Where things are documented: one home per fact

| Doc | Read it when |
|---|---|
| `README.md` | You want the front door: what this is, install, the weekly loop. |
| `docs/SHORTCUTS.md` | Sam said a weekly or season-long phrase. Code parses its tables: keep their shape. |
| `docs/COMMANDS.md` | You need a command or option's exact spelling. Generated from the CLI. |
| `docs/WEEK_PAGE.md` | You're changing the Week page or its local server: sections, views, routes, the client, tests. |
| `docs/CODE_MAP.md` | You need the modules, files and tests behind any other area (builds, contests, money, research). |
| `docs/WORKFLOW.md` | You want the week phase by phase, and how the Week page is used. |
| `docs/SHEET_REFERENCE.md` | You need what a tab or column means, or a shared tab's canonical column order. |
| `docs/CALCULATIONS.md` | You want the exact formula or threshold behind a number. The math lives only here. |
| `docs/SIM.md`, `docs/RESEARCH.md`, `docs/MODEL.md` | You're working on the simulator, Flashback, a study or a measured constant. |
| `docs/CONTESTS.md`, `docs/SEASON.md` | You're working on the contests engine and tiers, or the season-long leagues. |
| `docs/APPS_SCRIPT.md` | You're changing the sheet's bound Apps Script (`apps_script/Code.gs`). |
| `docs/TROUBLESHOOTING.md` | Something looks wrong and you want symptom, cause and fix. |
| `CONTRIBUTING.md` | You're adding a source or touching sheet structure: principles, the changelog, the doc-sync table. |
| `docs/planning/` | Gitignored and personal. Start at its `README.md` (it names `HANDOFF.md` first); `archive/` is history, never act on it. |
