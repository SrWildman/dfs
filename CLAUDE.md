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

Concretely: column/row positions come from named constants and functions --
`derived.EDGE_COLUMNS` (the data frame's append-only column list) and
`derived.EDGE_SHEET_ORDER` (the order the EdgeRaw TAB is written in; Pool sits
ahead of it in column A): every EdgeRaw column letter and VLOOKUP index comes
from `derived.edge_sheet_letter` / `edge_sheet_index` / `edge_vlookup_index`,
never `EDGE_COLUMNS.index` (a test fails if anything else computes one),
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

The user's **standard week** is `dfs week new`, `dfs sync`, `dfs lineups suggest`, `dfs export`, `dfs sync --live` +
`dfs lineups late-swap`, `dfs week close`; everything else is reference. `docs/COMMANDS.md` lists every command
and option and is GENERATED from the CLI (`python -m dfs.commands_doc`): after adding or changing a command or
option, regenerate it -- `tests/test_commands_doc.py` fails if it is stale or a command has no section.

Plain `dfs` is a guided menu of that standard week (`launcher.py`) that prompts for any required argument; a new
command needs a `launcher.MORE_LABELS` entry and its `commands_doc.SECTIONS` section a `launcher.MORE_TOPICS`
topic, which is how the "More, by topic" screen reaches it (tests enforce both).
Typed commands also ask for a missing required argument at a terminal (`prompt_missing.py`; every Typer app needs
`cls=PromptingGroup`) and fail as before when piped/scripted.

- `dfs results update [--week N] [--all] [--sheet-id <id>] [--no-sheet]` -- scores every completed week against
  nflverse's actual stats and rebuilds the `Model Check` tab (also run at the end of `dfs week close`, which it
  never fails). Projection = last TFFB snapshot before each player's own kickoff; flags/ValAdj recomputed from the
  raw archives with current code.

- `dfs entries fill [--csv <file>] [--write] [--again] [--dry-run] [--sheet-id <id>]` -- DK's entries file (newest in
  Downloads, by header) onto `DK Upload`: one row per Classic main-slate entry; **Sam picks each entry's `Lineup`**
  (dropdown) and nothing is ever auto-assigned (`Suggested` is a grey hint, `entries.suggest_pick`, the hook
  Contests Phase 2's "Best in" replaces); re-runs match rows by Entry ID and never overwrite a dropdown; `--write` writes
  `upload_<timestamp>.csv` (only set entries DK does not hold); `--again` after a late swap; a started player is never
  touched. `dfs entries print` lists every lineup as `Name (ID)` (CalPts, ProjPts, Own% first). Needs
  `dfs setup dk-upload` (DK Upload's layout: `lineups.DK_UPLOAD_SHEET_HEADER`; template first) once per sheet.
- `dfs bankroll stakes [--propose] [--no-sheet] [--sheet-id <id>]` -- rebuilds the **STAKES** block on Bankroll
  (to the right of the ledgers, `sheet_stakes.STAKES_FIRST_COLUMN`): his ROI / cash rate / weekly SD / entries by
  format, this season and all seasons in the contest log, and this week's plan from the tab's own Weekly Budget
  (`week.BANKROLL_WEEKLY_BUDGET_CELL`, never a second copy in config) and the optional `[stakes]` rules
  (`max_pct_bankroll_per_week`, `max_entry_fee_pct`, `split`). Math in `stakes.py`; phrased as arithmetic on his
  own settings, never advice. `--propose` prints suggested rule values with the arithmetic and writes nothing.
  `dfs week new` rebuilds the block on the new sheet (never failing); `doctor` flags a sheet without it.

- `dfs setup cockpit [--sheet-id <id>]` -- creates (or rebuilds) the read-only **Cockpit** tab right before Board (to its left): Sunday's last 90
  minutes on one page (new Q / D / OUT in his lineups, open slots with the best change from the last `late-swap` run, wind, the Lineups
  warnings, exposure top 10, DK Upload entries without a Lineup, budget). Nothing on it is typed; column A is the cue (`SWAP`, `LOOK`,
  `FILL`, `ok`). `dfs sync --live` and `dfs lineups late-swap` refresh it, **only on a sheet that already has the tab** (they never create it
  and never fail over it); `late-swap` saves its run to `data/cockpit/late_swap.json` for it (`late_swap_store`). Template first, then live,
  `dfs doctor` after (check `cockpit-tab`). `dfs week new` rebuilds it on the new sheet after the sync
  (fail-soft, only if the copied template has the tab) so the template's stale render never survives. Layout: `cockpit.py` (pure), I/O: `sheet_cockpit.py`.
- `dfs setup player-card [--sheet-id <id>]` -- creates (or rebuilds) the **Player** tab right after Edge Finder: pick a name in `B1` (the only typed cell, a dropdown over EdgeRaw's names)
  and read one player on one page (projections side by side, ownership, last five games of usage, matchup, props, signals with Model Check's record, his place in your lineups). Formulas over
  EdgeRaw (columns from `derived.edge_sheet_letter`) plus two hidden raw tabs, `PlayerCardRaw` / `PlayerCardRows`, that every `dfs sync` rewrites **only on a sheet that already has the tab** (never
  fails the sync). Template first, then live, `dfs doctor` after (checks `player-card-*`). Layout and spec: `player_card.py` (pure; one `Field` spec drives formula and text), I/O: `sheet_player_card.py`,
  data: `sources/player_log.py`. A rebuild keeps the name in `B1`.
- `dfs stacks [--sheet-id <id>] [--no-sheet] [--refit] [--retro]` -- ranks every QB's stacks of one and two teammates (WR/TE, or a RB
  whose `Tgt%` is in the top third of the slate's RB1/RB2; with and without a bring-back) by simulated Top 1% rate against how much of the
  generated GPP field plays them, and writes the read-only **Stacks** tab (right after Suggested; top 20 stacks, top 8 games, the retro's
  track record on top, column A `BUILD` = type the last cell's `Always use: ...` line into Suggested's rules box). Prints the same page; writes the sheet
  only where the tab exists. Before TFFB publishes ownership the field %/leverage columns read `-` (a CalPts stand-in drives the field). `--refit` refits
  the field model on the real standings (about two minutes, saved in `data/model_cache/stack_field.json`); `--retro` refits and reruns the retro
  that is the tab's track record (`data/model_cache/stacks_retro.json`, several minutes; run it after `dfs week close`). Math in `stacks.py`
  (pure), layout `stacks_tab.py`, I/O `sheet_stacks.py`, retro `sim/stacks_retro.py`, live inputs `stacks_inputs.py`. A plain `dfs sync` refreshes the tab
  (about a minute; never on `--live`, never fails the sync); `dfs week new` refreshes it on the new sheet after its sync. An information view: the verdict on its leverage signal (`tested` / `weak` / `untested`) is on the tab.
- `dfs setup stacks [--sheet-id <id>]` -- creates (or rebuilds) the Stacks tab right after Suggested. Template first, then live, `dfs doctor` after
  (check `stacks-tab`). Needs `dfs stacks --refit` once.
- `dfs research refresh [--through N] [--season S] [--shuffles N] [--only a,b]` -- the weekly offline reruns in one
  command, no sheet: the five-pair shape retest, the teammate-bump race, the probability recalibration check, the
  tails check, the field_own scores, the ESPN / props blend gate and the PROPS SHADOW (`props_edge.build_shadow`), `sim/contest_retro` and the optimizer's-curse study
  (`research/curse.py`; both plug in through `refresh_summary(season, through)`, see `research/refresh.py`). Each fails soft; it writes
  `docs/research/weekly/<season>-wk<NN>.md` (gitignored; an `--only` run writes `-partial.md` instead) with one block per study (headline numbers, ship bar,
  "changed since last week" against the previous report), prints a line per study with its time. About two
  minutes, plus about 15 the first run after a week closes (the curse study's builder runs for the new week). Run after `dfs week close`.
- `dfs backtest [--weeks 2,4,5,6] [--settings <file.toml>] [--compare <file.toml>] [--fresh] [--workers N] [--no-report]` -- Flashback, the
  settings backtest: rebuilds each past week with today's `suggest_builds` from the last pre-lock snapshot (`curse.week_inputs`), on Sam's saved
  pool where the week has one (`pool_store`, `data/pools/<season>-wk<NN>.json`, written by every `dfs sync` and `dfs lineups suggest`; fail-soft,
  gitignored) else the constructed pool, enters the best `k` builds of each kind in the contests he entered (the standings' `srwildman` rows and the
  history log), and pays them against the real field (place among the real scores, shared with real copies, on the fitted payout table) beside his
  real lineups (as DK paid, and on the same table). ROI / cash rate / top-1% / ROI without the best ticket, "as entered" and "any build"; the
  standard errors are across weeks. `--compare` pairs two settings files (`--settings` minus `--compare`) with the ship bar's verdict. Settings TOML
  and the flag registry other sessions plug into: `backtest_settings.py` (`register(Flag(...))`, hooks `prepare` / `build` / `assign`, plugin modules in
  `PLUGIN_MODULES`). The search runs to the end (`seconds = 600` default; the live clock is 120 and its runs differ), so reruns give the same builds. Saved builds (`data/research/backtest/jobs/<fingerprint>/`) are keyed by settings + code, `--fresh` ignores them. Report in
  `docs/research/backtest/` (gitignored). Read-only: no sheet. Also `dfs research refresh --only backtest` (optional study). Math/orchestration
  `backtest.py`, text `backtest_report.py`.
- `dfs doctor [--sheet-id <id>]` -- read-only structural check (tabs
  exist, the Cockpit and Stacks tabs are there, the Suggested tab is in the current layout (lock column, rules box; an old one
  says to run a plain `dfs lineups suggest`), `Exposure!H1` is the formula that counts built lineups (not a typed number), EdgeRaw's header matches `EDGE_COLUMNS`, `LINKED_EDGE_COLUMNS` is
  linked exactly once, Lineups' header repeats are where
  `LINEUPS_NAME_BLOCKS` expects, DK Upload's entries layout with its
  Lineup dropdown, typed-cell cue and protection exemption, and the hand-built per-row formula ranges on
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

## Sam's weekly shortcuts

Sam runs the week by talking to a local Claude session. When he says one of these, run exactly this (in order),
then report what the last column says. Always say **which sheet** the commands used (`dfs status` prints its title and
URL; check it before any write).

| Sam says | Run, in order | Report back |
|---|---|---|
| "start week N" | 1) `dfs odds snapshot` if no opening snapshot exists for the week yet (`dfs odds movement` shows what is saved); 2) **ask Sam to confirm, and for the new sheet's URL** if he has not given it, then `dfs week new "<url>"`; 3) `dfs doctor` | the new sheet's title and URL, what `week new` carried over, any doctor findings |
| "sync" | `dfs sync`, then `dfs doctor` | each source's status (name any failure, e.g. an expired TFFB login), doctor findings |
| "final sync" | `dfs sync --live`, then `dfs doctor` | what changed since the last sync (the terminal diff / the Board's Queue), anyone in his pool now OUT or Q; then read the **Cockpit** tab (the sync refreshed it) and report its `SWAP` / `FILL` rows |
| "build me lineups" | `dfs lineups suggest` (his pool must not be empty: if it is, say so and stop) | each build in one line (cash or GPP, shape, salary, Cash % / Top 1% (GPP builds rank by Top 1%; `--rank share` ranks by Top 1% share, only if Sam asks), and for a GPP build its ownership sum and product and expected duplicates: the numbers the terminal prints; say so if a cash build carries a duplicate warning), and that **Lineup Tools > Copy builds to Lineups** (type `1,4,6` or `all`) puts them in |
| "keep build 2" | set build 2's decision cell to `Keep` on the Suggested tab (a write to the live week's sheet: say the sheet), then `dfs lineups suggest --iterate` | the round produced and what changed (each build's note) |
| "lock Stafford and Nacua in build 4, swap the bring-back for McBride, rerun" | tick the locks and type the name over the bring-back cell on Suggested (the sheet and the chat must never disagree), then `dfs lineups suggest --iterate` | the round, build 4's note ("bring-back → McBride (yours); ...; Cash % 61% → 64%") or why the edit was refused |
| "give me 3 more like build 5" | set build 5 to `Vary 3`, then `dfs lineups suggest --iterate` | the round and the three variations (what differs, their Cash % / Top 1%) |
| "never Kyren", "always Bijan", "no bring-back for LAR" | write the rules box lines (`Never use`, `Always use`, `Stack rules`), then `dfs lineups suggest --iterate` | the round, which builds changed, and any rules line it could not read |
| "bump Nacua 2", "Kyren -15%", "Stafford to 24" | write the `Adjust` rules line (`Nacua +2`, `Kyren -15%`, `Stafford 24`; a percent needs its sign), then `dfs lineups suggest --iterate` | the round, which builds hold him (each note says "Nacua +2 (yours)") and how their Cash % / Top 1% moved; say that Model Check still scores the raw CalPts |
| "Kyren 40% owned" | write the `Ownership` rules line (`Kyren 40%`), then `dfs lineups suggest --iterate` | the round and what it moved (builds, dups, chalk watch); if Own% has not published yet, say the line waits for it |
| "min unique 2", "no more than 5 from one game" | write the `Build rules` line (`min unique 2`, `max from one game 5`), then `dfs lineups suggest --iterate` | the round and whether every new build honoured it (a build that cannot is reported, never relaxed) |
| "go back to round 2" | `dfs lineups suggest --round 2` | the round it was written as (a new round) and the builds |
| "fix flex" | nothing to run: tell him to use **Lineup Tools > Fix FLEX order** on Lineups (a sheet script); `dfs export` also puts the FLEX right in the file it writes | which of the two did it |
| "late swap" | `dfs lineups late-swap --refresh-injuries` (it covers every built lineup; the flag lays ESPN's game-day injury feed over the last sync's status first). **If Sam says he downloaded a live standings export** (a Double Up and/or a GPP, from DraftKings by hand after the 1 pm lock), add `--field auto` (newest live exports in Downloads, this week's; say which file(s) it named, and "field: real" or "field: projected" as the output does; a file path works too). Never fetch the export yourself | a summary per lineup: who is still swappable (and any heavy chalk in none of his lineups, the yellow warning at the end), the best change and its Cash % / GPP % effect, or "nothing beats it" (the run also refreshes the Cockpit tab where the sheet has one); with `--field`, which contest's field the lines came from and when it was exported |
| "late swap with live scores" | `dfs lineups late-swap --refresh-injuries --state live`: no download; every started player's points so far come from ESPN's public box scores and Cash % / Top 1% are measured against the projected-ownership line on that state (the played games known, the rest simulated). If the output says ESPN could not be read or ownership is not out, it ran as plain "late swap": say so. Not with `--field` | the same summary as "late swap", plus the "field: projected + live scores (ESPN, read <time>)" line the output prints |
| "close the week" | `dfs week close` (it finds the newest history export in Downloads by its header and says which; pass `--csv <file>` if he names one) | the Model Check verdict and the learned block, the bankroll lines it reconciled, and what it kept and filled on the contest side (standings moved, empty downloads, Cash Line, the Contests tab), then the Contests tab's THIS WEEK, YOUR DECISIONS block (five lines) |
| "how did we do" | read (do not write) Model Check: the opening verdict sentences and "What the model learned this week" | five lines: who leads the projection race by position, what CalPts learned, how the signals are running, whether the probabilities hold, the Week 8 checkpoint |

Guardrails:

- **Ask before `dfs week new`**, and before writing to any sheet other than the live week (the one `config.toml` points at).
- **Never touch a closed week** (a previous week's sheet is read only).
- **Your numbers are a copy.** The rules box's `Adjust` and `Ownership` lines (`my_frame.py`) turn the synced slate
  into "your" frame (`MyPts`); `lineups suggest`, `--iterate`, `late-swap`, the simulator's field, chalk watch and
  the Cockpit all read it, and Model Check / `data/current/edge.csv` never do. Never hand the framed edge to
  anything that saves or scores the model, and never apply the frame twice (the Cockpit applies it itself).
- **Iterating on Suggested:** `dfs lineups suggest --iterate` reads the tab (decisions, locks, typed names, rules box) and `data/suggest_state/<week>.json`; always say **which round** it produced and what changed. A plain `dfs lineups suggest` builds from scratch and replaces the tab (the previous round stays in the file: `--round N`). Edits made through chat go in the same cells, only on the live week's sheet.
- **Never push** (or open a PR) without Sam's OK in chat.
- A command that fails: show the error and stop; do not retry in a loop or work around it. Google calls hang now and
  then: run a long job detached with a log file and read the log.
- Anything not in this table is a normal request: `docs/COMMANDS.md` has every command and option.

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
| `docs/CONTESTS.md` | You're working on `src/dfs/contests/` (the contests engine: history log, series/format tagging, standings parsing, field stats, slate index, toughness curves). Method, the slate-index finding, every assumption, and what the local session must wire up. The command is hidden until it is. |
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
