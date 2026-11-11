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

The user's **standard week** is `dfs week new`, `dfs sync`, `dfs lineups suggest`, `dfs export` (then, at lock,
`dfs entries record`), `dfs sync --live` + `dfs lineups late-swap`, `dfs week close`; everything else is reference. `docs/COMMANDS.md` lists every command
and option and is GENERATED from the CLI (`python -m dfs.commands_doc`): after adding or changing a command or
option, regenerate it -- `tests/test_commands_doc.py` fails if it is stale or a command has no section.

Plain `dfs` is a guided menu of that standard week (`launcher.py`) that prompts for any required argument; a new
command needs a `launcher.MORE_LABELS` entry and its `commands_doc.SECTIONS` section a `launcher.MORE_TOPICS`
topic, which is how the "More, by topic" screen reaches it (tests enforce both).
Typed commands also ask for a missing required argument at a terminal (`prompt_missing.py`; every Typer app needs
`cls=PromptingGroup`) and fail as before when piped/scripted.

- `dfs week close --research` / `dfs research close-report [--week N] [--only refresh,stacks,calls,calls_flashback] [--deadline MIN]` -- Monday
  on autopilot (`close_research.py`): after the close, four stages run side by side as child processes (each with a log under
  `data/research/close/<season>-wk<NN>/`; the parent stops any still running at the deadline, 45 min): `dfs research refresh --through
  <week>`, `dfs stacks --retro`, the plays model's `calls` (the week's calls scored into
  `data/research/calls_history.csv`, weights refitted; `research/plays_backtest.close_week`), and `calls_flashback` (`dfs backtest` of
  `calls_avoid_cash` against `today_cash` and `calls_fade_gpp` against `today_gpp`, Plan M M1.5's ban-only variants; settings in `research/settings/*.toml`, in the repo). Each fails soft.
  ONE report, `docs/research/weekly/<season>-wk<NN>-close.md` (gitignored): the close summary (what `week close` printed, plus the Contests
  tab's decisions block), each study's headline and ship-bar verdict, "changed since last week" against the previous close report. Nothing
  writes a sheet (`close-report` reads none at all). `week close --research` skips the studies, saying why, when the week is not scored yet.
- `dfs contests plan [--week N] [--budget X] [--no-sheet]` -- terminal only (`contest_plan.py`): this week's plan
  by format (`[contest_plan]`, code defaults: Showdown at most $5, N-max GPPs with 2,000+ entrants at most $10,
  the rest of the Bankroll tab's Weekly Budget to double-ups, 50/50s, H2Hs and SE GPPs under 1,000 entrants),
  last week against it, ROI and effective rake (`1 - prize_pool/(entries x fee)`) by format and field size, and
  the H2H regulars to avoid (0-3 or worse, or under 30% over 5+). `week close` prints its one line ("spent $X vs
  plan $Y; over in: ..."). Leaves `contest_plan` for the Week page (`week_inputs.save_input`).
- `dfs entries record [--csv <file>] [--from-sheet] [--sheet-id <id>] [--dry-run]` -- at lock, after entering
  (`entered.py`): DK's entries file downloaded again (newest in Downloads, refused when it is another week's), else
  the Lineups tab; writes `data/entered/<season>-wk<NN>.json` (each lineup, its contests, its source build: 8+ of
  9 players in one saved build of the week, else "own"). `week close` warns when the week has none.
- `dfs week close` also prints the **cash post-mortem** (`cash_postmortem.py`): the 10 biggest double-up casher /
  non-casher gaps (8%+ of the field) tagged in N cash builds / pool only / missed, every distinct cash build
  scored against the cash line, the entered cash lineup against its nearest build; rows appended to
  `data/contests/cash_postmortem.csv`, the summary left as `postmortem` for the Week page. `dfs lineups suggest`
  prints the **cash chalk check** (every CashOwn 30%+ player and how many cash builds hold him; display only).
- `dfs setup other-sports [--history] [--csv <file>] [--dry-run] [--sheet-id <id>]` -- once per sheet, template
  first: non-NFL DK contests get their own Bankroll ledger (`[bankroll.other]`, default rows 151-171, its line on
  row 11) and Season columns (`Other Net` / `Other Risked`, R/S), so Cash and GPP are NFL only and Ending
  Bankroll still counts every dollar; `--history` (never on the template) moves the season's non-NFL money out of
  GPP. `week new` / `week close` re-read the closed week's Season row from its own Bankroll. Doctor:
  `bankroll-other`, `season-other`.
- `dfs results update [--week N] [--all] [--sheet-id <id>] [--no-sheet]` -- scores every completed week against
  nflverse's actual stats and rebuilds the `Model Check` tab (also run at the end of `dfs week close`, which it
  never fails). Projection = last TFFB snapshot before each player's own kickoff (ESPN and the props likewise: the last snapshot before his team's / his own kickoff); flags/ValAdj recomputed from the
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

- `dfs lineups suggest --portfolio [--portfolio-objective edollars|top1|robust] [--portfolio-wide N] [--entries-csv <file>]` -- **default off, shadow**
  (the retro did not prove it): after the builds, picks which GPP build goes in which of his GPP entries as one set (greedy marginal gain
  on shared sim draws, `portfolio.py`; his N-max entries compete for the same places, so they are placed together), prints the plan beside
  today's pick (`entries.suggest_pick`: the best build in every contest) and, on a write run, saves `data/suggest_state/<week>_portfolio.json`;
  `dfs entries fill` then shows its lineup as the grey Suggested hint for GPP entries once the builds are on Lineups (cash entries keep today's
  pick; a hint is never a choice). `--portfolio-wide N` adds the retro's wide pool (the builder run N times on noisy projections, ~15 s
  each); a chosen lineup that is not a build joins the round as a new GPP build (note `portfolio pick (wide pool)`). The retro's lead
  candidate is `--portfolio-objective robust --portfolio-wide 8` (robust is chosen on correlated-shock draws). Needs DK's entries file (downloaded by hand) and `dfs stacks --refit` once. Never writes a sheet. Plan I/O:
  `portfolio_inputs.py`; the retro behind the verdict: `sim/portfolio_retro.py` (needs the curse study's saved builds). **Dropped from the weekly
  reruns** (the Week 7 backtest lost again, -13.8 ROI pts/wk, 1 of 5): the code and the `portfolio` backtest flag stay, default off.
- `dfs setup cockpit [--sheet-id <id>]` -- creates (or rebuilds) the read-only **Cockpit** tab right before Board (to its left): Sunday's last 90
  minutes on one page (new Q / D / OUT in his lineups, open slots with the best change from the last `late-swap` run, wind, the Lineups
  warnings, exposure top 10, DK Upload entries without a Lineup, budget). Nothing on it is typed; column A is the cue (`SWAP`, `LOOK`,
  `FILL`, `ok`). `dfs sync` (plain and `--live`) and `dfs lineups late-swap` refresh it, **only on a sheet that already has the tab** (they never create it
  and never fail over it); `late-swap` saves its run to `data/cockpit/late_swap.json` for it (`late_swap_store`). Template first, then live,
  `dfs doctor` after (check `cockpit-tab`). `dfs week new` rebuilds it on the new sheet after the sync
  (fail-soft, only if the copied template has the tab) so the template's stale render never survives. Layout: `cockpit.py` (pure), I/O: `sheet_cockpit.py`.
- `dfs setup player-card [--sheet-id <id>]` -- creates (or rebuilds) the **Player** tab right after Edge Finder: pick a name in `B1` (the only typed cell, a dropdown over EdgeRaw's names)
  and read one player on one page (projections side by side, ownership, last five games of usage, matchup, props, signals with Model Check's record, his place in your lineups). Formulas over
  EdgeRaw (columns from `derived.edge_sheet_letter`) plus two hidden raw tabs, `PlayerCardRaw` / `PlayerCardRows`, that every `dfs sync` rewrites **only on a sheet that already has the tab** (never
  fails the sync; it also rebuilds the card itself when the sync changed EdgeRaw's width). Template first, then live, `dfs doctor` after (checks `player-card-*`, including `player-card-refs`: every EdgeRaw reference on the card is the column `derived.edge_sheet_letter` gives). Layout and spec: `player_card.py` (pure; one `Field` spec drives formula and text), I/O: `sheet_player_card.py`,
  data: `sources/player_log.py`. A rebuild keeps the name in `B1`.
- `dfs setup compare [--sheet-id <id>]` -- creates (or rebuilds) the **Compare** tab right after Player: four pickers in `B1:E1` (the only typed cells, dropdowns over EdgeRaw's names) and the Player card's
  measures one column per player (header, projections, ownership, usage, opportunity per dollar, matchup and game, your lineups); the best value per row is green (lowest for Bust% and OppPosRank; no shade on salary or ownership, whose better side
  depends on cash vs GPP, nor anywhere there is no clear direction). Formulas over EdgeRaw only (reuses the card's `Field` specs, so nothing is forked); a sync that **widens EdgeRaw rebuilds it** (only on a sheet that has the tab).
  Template first, then live, `dfs doctor` after (checks `compare-*`, including `compare-refs`). Layout and rules: `compare_card.py` (pure), I/O: `sheet_compare.py`. A rebuild keeps the four names.
- `dfs stacks [--sheet-id <id>] [--no-sheet] [--refit] [--retro]` -- ranks every QB's stacks of one and two teammates (WR/TE, or a RB
  whose `Tgt%` is in the top third of the slate's RB1/RB2; with and without a bring-back) by simulated Top 1% rate against how much of the
  generated GPP field plays them, and writes the read-only **Stacks** tab (right after Suggested; top 20 stacks, top 8 games, the retro's
  track record on top, column A `BUILD` = one of the 5 highest-leverage stacks that beat the field (Lift > 1x, Lev > 0; `stacks_tab.BUILD_CUES`), a pale green row: type
  the last cell's `Always use: ...` line into Suggested's rules box; `Top 1%` / `Lift` / `Lev` are colour-scaled and row 3 is the colour legend). Prints the same page; writes the sheet
  only where the tab exists. Before TFFB publishes ownership the field %/leverage columns read `-` (a CalPts stand-in drives the field). `--refit` refits
  the field model on the real standings (about two minutes, saved in `data/model_cache/stack_field.json`); `--retro` refits and reruns the retro
  that is the tab's track record (`data/model_cache/stacks_retro.json`, several minutes; run it after `dfs week close`). Math in `stacks.py`
  (pure), layout `stacks_tab.py`, I/O `sheet_stacks.py`, retro `sim/stacks_retro.py`, live inputs `stacks_inputs.py`. A plain `dfs sync` refreshes the tab
  (about a minute; never on `--live`, never fails the sync); `dfs week new` refreshes it on the new sheet after its sync. An information view: the verdict on its leverage signal (`tested` / `weak` / `untested`) is on the tab.
- `dfs plays [--position QB,RB] [--per N] [--no-save] [--inactives]` -- **the plays model, SHADOW** (Plan M M1; `plays.py`,
  `sim/plays_sim.py`): two calls per rosterable player, cash CORE / PLAY / NEUTRAL / AVOID and GPP TARGET / LEVERAGE /
  NEUTRAL / FADE, each with a score and one reason. The cash score is the sim's priced Cash % over near-optimal cash lineups
  (M1.6: chalk 30%+ is CORE unless a NON-chalk player at the price is 2+ points of Cash % better; AVOID is the bottom 30% by
  score within the position and never a player whose Cash % ties or beats every alternative at his price; FADE↓ is its
  measured effect, Model Check's record taken off his projection before the sim, not an override). The GPP score is the
  simulated top-1% share against GppOwn, with the tails stretches, the teammate bump for fresh OUT / Doubtful news minus what
  the projections already moved, the QB downgrade (`news_context`'s backup-QB fact, R8's measured -0.64 for WR / TE,
  `models/research/qb_downgrade.csv`), and a QB's best stack. `plays.contradictions` checks every label against the numbers in
  its reason (the backtest exits 1 on any); until the new labels clear the bar the Week page holds back a cash AVOID on 30%+
  chalk and any contradicted label ("no call: label under review", `plays.held_back`). Weights are a ridge fit toward a prior, walk-forward, saved in
  `data/research/calls_weights/`. Every `dfs sync` (and `week new`'s) adds a timestamped version to
  `data/week/inputs/<season>-wk<NN>-calls.json` and prints the "new must-plays" (calls that moved up). `--inactives` runs
  `dfs sync --live` first (which refreshes the Cockpit). Never writes a sheet. The Week page does not show the calls yet (M2).
  Backtest: `python -m dfs.research.plays_backtest run` (report in `docs/research/plays/`, gitignored). Flashback:
  `dfs backtest --settings research/settings/calls_cash.toml --compare research/settings/today_cash.toml`
  (flag `calls`: `true` = CORE locked + AVOID banned; M1.5's ban-only variants `"avoid"` with `calls_avoid_cash.toml` against
  `today_cash.toml`, `"fade"` with `calls_fade_gpp.toml` against `today_gpp.toml`).
- `dfs setup stacks [--sheet-id <id>]` -- creates (or rebuilds) the Stacks tab right after Suggested. Template first, then live, `dfs doctor` after
  (check `stacks-tab`). Needs `dfs stacks --refit` once.
- `dfs research refresh [--through N] [--season S] [--shuffles N] [--only a,b]` -- the weekly offline reruns in one
  command, no sheet: the five-pair shape retest, the teammate-bump race, the probability recalibration check, the
  tails check, the field_own scores, the ESPN / props blend gate and the PROPS SHADOW (`props_edge.build_shadow`), `sim/contest_retro`, the optimizer's-curse study
  (`research/curse.py`), OurDST, our own DST projection against TFFB and CalPts (`model/dst_study.py`), and the calibrated
  double-up Cash % offset (`sim/du_line_retro.py`; it saves the offset `[sim.lines] calibrated_cash_offset` shows Cash % against,
  default off, and says when it clears the bar; all four plug in through `refresh_summary(season, through)`, see `research/refresh.py`). Each fails soft; it writes
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
  and the flag registry other sessions plug into: `backtest_settings.py` (`register(Flag(...))`, hooks `prepare` / `build` / `assign` / `choose` (the week's entries as one set), plugin modules in
  `PLUGIN_MODULES`). The search runs to the end (`seconds = 600` default; the live clock is 120 and its runs differ), so reruns give the same builds. Saved builds (`data/research/backtest/jobs/<fingerprint>/`) are keyed by settings + code, `--fresh` ignores them. Report in
  `docs/research/backtest/` (gitignored). Read-only: no sheet. Also `dfs research refresh --only backtest` (optional study). Math/orchestration
  `backtest.py`, text `backtest_report.py`.
- `dfs week page [--week N] [--open/--no-open]` -- **the Week page** (v2): one self-contained HTML file,
  `data/week/<season>-wk<NN>.html` (inline CSS, an inline JSON block and script for the Explorer, no external loads), opened
  in the browser. Top, in this order: the **progress strip** (Pool, Contest plan, Builds, Entries recorded, Late swap,
  Close, each done / next / to do from the real state; `week_board.progress`), **the brief** (5-7 deterministic, day-aware
  sentences; `week_board.brief`) with the next step, the **pool panel** (Cash / GPP counts by position, gaps, Questionable
  in the pool, the top 5 the data likes that are not pooled), the **decisions** (30%+ CashOwn Play / Fade, a Questionable
  star's pivot or wait, the GPP stack between the top two QBs; `week_board.decisions`), **one board** (Plan I's plays,
  `week_plays.plays`, deduped: each player once with his role chips, top 3 per role and position plus Show more; FADE↓
  wins and sits last with its record; columns Salary, Proj, Per $1K, DU own, GPP own, Why, Pool) beside the value map,
  the leverage map (`week_charts`, inline SVG) and the slate's game cards, then the **Explorer** (`week_explorer`: every
  priced player and every EdgeRaw column, lenses Value / Cash / GPP-Leverage / Usage / Matchup / Projections / Ownership /
  Signals / All columns, show 3/10/25/all per position or across, filters, sort, a column picker, trust chips on headers, a
  player drawer from any name; a lens naming a column EdgeRaw no longer has fails `tests/test_week_explorer.py`). Below,
  collapsed with a one-line summary each and **ordered by the day** (`week_page.DAY_ORDER`): What changed, Contest plan,
  Season-long, Last week, Cash, GPP, Sunday (with the unknowns), Your tools. Layout `week_top.py`, answers `week_board.py`
  (pure). Read-only: it reads outputs that already exist (`week_inputs.py`) and a section whose input is missing says
  "not run yet". `sync`, `lineups suggest`, `lineups late-swap`, `contests plan`, `season sync`, `season check`, `week close`
  and `week new` rebuild it at the end (`week_page.after_command`, called from the CLI entry point; a failure warns and
  the command carries on). The run log behind "last run" is `data/runlog.csv` (time, command, ok; option NAMES only, never
  values), written by `prompt_missing.PromptingGroup.main` for every command; tests redirect it (`tests/conftest.py`) and
  `tests/test_runlog.py` guards the real file. Inputs left by commands for the page live in `data/week/inputs/`
  (`week_inputs.save_input`): the Stacks tab's top rows, the season-long view, `contest_plan`, `postmortem`, and the page's
  own `decisions` (the pivots lined up).
- `dfs week serve [--week N] [--port P] [--no-open] [--sheet-id <id>] [--stop]` -- **the Week page as a local app** (`week_server.py`;
  the static `dfs week page` is unchanged): serves the page on this Mac only (`127.0.0.1`; the link it opens carries a
  random token, and every request needs the token and a localhost `Host`), with **Claude Code docked on the right**
  (`week_chat.py`: `claude -p` in stream-JSON mode in the main checkout under Sam's own login; model / effort / New chat /
  Stop / this week's chats; a call that needs approval shows on the page as Approve / Deny with exactly what will run,
  nothing is auto-approved, `bypassPermissions` is never used, "auto" is a switch he turns on; no background commands
  (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), a turn is read until its process exits (it can print several `result`s;
  the answer is the first after Claude Code replays Sam's message), a turn with no answer ends in a red line; after any
  chat change run `python scripts/chat_smoke.py`, three turns against real Claude, not in pytest), an **Ask** button
  beside every phrase, **run buttons** for every command with live output (`week_jobs.py`: one job at a time, Stop, history;
  `week new` / `week close` / `lineups clear` / `pool clear` / `setup ...` need the command typed out; a closed week is
  read only), a **check this contest** form and the **file drop zone** (`week_drops.py`: DK's entries file at lock, the
  history export and standings at close; only .csv / .zip, size and zip-bomb limits, existing parsers only, filed into
  Downloads where `entries record` and `week close` look, never deleted; after a drop it shows what was recognised and
  offers the matching command). **The page's own writes** (`week_actions.py`
  over `week_writes.SheetEdits`; routes `/api/pool`, `/api/pool/start`, `/api/decide`): a player's EdgeRaw **Pool** cell
  (Cash / GPP / Both / none, the dropdown's cell), a decision (Play / Fade = `cash: Name` on Always use / Never use; the
  stack = `more QB`, `more Mate` on Max exposure; a pivot = his Pool cell to Both plus the `decisions` input; Wait writes
  nothing; Undo removes only what the answer wrote) and the suggested starting pool. Every write finds cells by header,
  re-reads and refuses on a change (409 with what the sheet holds), is one batch under the run lock, is read back and is
  logged to `data/week/edits.log`; a closed week is refused. Every render reads the live Pool column and rules box
  (a read failure falls back to the last sync, read only). `--sheet-id` points it at a test copy. No DK access; the log
  (`data/week/serve.log`) never holds prompts. Ctrl-C stops it.
- **Plan N on the Week page** (client side in `week_ui.py`: CSS and one script on the static and the served page; no
  library, no network of its own): the chat folds to a tab (`dfs-chat-collapsed` in localStorage); one wide column
  (board, `.charts-row`, games, Explorer); sticky headers in each `.scroll` box (the board repeats `board_head` per
  position) and a sticky name column (`table.stickyname`); Opp / Spread / Total / Implied on every player grid; the
  page state (open folds, Show more, scroll) kept per week in localStorage and restored after `refreshSections`; ONE
  client pool store (`dfsPool`, painted on every `.seg` / `.poolval` and the Explorer) fed by each write's read-back;
  multi-select with a bulk bar, sent as `{"each": true, "changes": [...]}` to `/api/pool`
  (`week_writes.SheetEdits.set_pool_each`: one batch, per-cell conflict / missing, `week_actions.MAX_BULK`), writes
  queued (`dfsQueue`); decisions also move the pool (`week_actions.decide_pool`, `merge_pool`, `add_kind`,
  `drop_cash_only`; the pool half is kept under `pool:` keys in the `decisions` input so Undo reverses it); the chart
  viewer (`week_charts.viewer_data`, JSON `chart-data`); the game view (`week_top.game_data`, JSON `game-data`;
  `/api/stack` = `week_actions.more_stack`, a Max exposure `more` line, needs a QB); the cash core's caps
  (`week_plays.CASH_CORE_CAP`, `cash_core`; 30%+ CashOwn = `Chalk core`, always shown); the chalk check's header and
  3 "close" rows (`week_page.CHALK_CHECK_HEADER`, `CLOSE_COUNT`); the consensus in DK's roster order
  (`consensus.dk_order`) with `week_money.CONSENSUS_CAPTION`; and `week_page.table(..., how=...)`: every table and
  chart carries a "how this is built" note. Tests: `tests/test_week_ui.py` (HTML / script, no browser) and
  `tests/test_week_ui_browser.py` (Playwright on a fake sheet; skipped where Chromium is not installed, as on CI).
- **Plan O on the Week page** (O1): the **run panel** (`week_jobs.panel`: `WEEKLY`, the eight weekly buttons in the day's order, each
  form showing the options his shortcuts use (`Weekly.common`, short labels `SHORT_LABELS`, the help as a tooltip, `Weekly.defaults`
  filled in, the rest under "More options", a preview line of the exact command); every other command behind a search; `setup ...` /
  `fix-...` behind a collapsed Advanced toggle (`is_advanced`); recent runs say ok / failed / stopped, how long and when, and open their
  output (`data/week/jobs/<id>.log`, the newest `OUTPUT_KEPT`; `/api/jobs/output`)). **A pool chip on every name** (`week_ui` `decorate`:
  any `a.pl` the page draws, resolved to an id from the Explorer's data by name and team when a section holds only the name,
  `week_page.pn` / `pns`; a name not on this slate stays plain text; a row that already has the control gets none) and **one click to add**
  a player listed as missing from the pool (`week_page.add_button`, kind from `week_board.kind_of`). **Colour by position**
  (`week_shade.py`: a z-score within the position in every table that has one, the sheet's palette, ownership orange, the top / bottom 10%
  bold, a legend above each coloured table; directions from `compare_card.SECTIONS`). **Captions** (`week_page.caption` / `fill_asof`:
  every `table()` and chart shows how it is built, the source columns and the date of its data; `week_charts.FORMULA` / `SOURCES`).
  **The Explorer's chart follows the lens and the filters** (`week_explorer.LENS_CHARTS`, one per lens; the full-screen viewer opens the
  chart that is showing). The game view maps nflverse's team codes to DK's (`player_join.TEAM_ALIASES`: LA = LAR), has Previous / Next and a
  game list, and a pool control on every row; pan is every chart's default (wheel / pinch / box to zoom); a Pool click keeps the scroll
  of the page and of the box it is in, and the focus.
- **Plan O2 on the Week page** (`week_views.py`): a **view bar** under the brief (`VIEWS`: Overview, the default | Board & Explorer |
  Usage | Injuries | Model vs sources | Everything = every block; Sam, 2026-11-09), each view its own `#name` (a link or the back
  button opens it; no element id may equal one, a test checks), the last one kept per week (`dfs-view-<season>-<week>`); every block is
  wrapped in `week_views.wrap(html, place)` naming its views (`PLACES`); the progress strip and the brief show on all of them, and the
  one pool store serves every view. **Overview**: one row per position (top value, cash core and chalk, the board's Upside, the fades:
  FADE↓ plus the plays model's AVOID / FADE (Shadow) on one line per name, the top injury fallout = the calls' teammate bump), the top
  3 games, the pool by position; the day's folds, the run panel and the tools sit under it. **Usage**: `data/current/usage_weeks.csv`
  and `team_weeks.csv` (new sheetless sources `usage_weeks` / `team_weeks`, `sources/usage_weeks.py`, math `usage_weeks.py`; a full
  `dfs sync` only, never `--live`): per game snap share, targets, target / air-yards share, WOPR, RZ / HVT, carries, DK points; per
  team-game plays, dropback rate, PROE, pace, red-zone trips; drawn by the client (window, trend metric with a last-5 sparkline against
  the season mean, two scatters per position with the outliers labelled, a stacked bar of each team's targets and carries).
  **Injuries**: per slate team, DK's OUT / D / Q / IR plus the injury report's designations, the practice trend, who benefits (the calls'
  `bump` / `bump_from`). **Model vs sources**: CalPts against TFFB / Sleeper / FantasyPros / ESPN / props (the median of 2+ sources),
  the biggest disagreements by position, every player behind a fold, and the plays calls leading with AVOID and FADE, each label's record
  kept apart: the M1 backtest (newest `docs/research/plays/*-plays-summary.json`, gitignored) and the live `calls_history.csv`
  (`week_inputs.calls_record`, `week_views.record_of`). **Search** (`#psearch`, `/` focuses it) and a **compare tray** of 2-4 players
  (`+ Compare` in the search, the drawer and every pool chip's popover; kept per week) with the Compare tab's rows and directions
  (`week_views.compare_spec` over `compare_card.SECTIONS`), usage trends and ownership. The served page fires `dfs-sections` after a
  refresh (`week_live`) so the client-drawn views redraw. The pool store numbers each read-back and a render adopts a cell only when no
  newer read-back exists (`week_ui` `stale` / `mark`; a refresh asked for during one runs once more after it), and a `+ Cash` is
  resolved against the last read-back when its write runs (`requestPool` change `{id, add}`), never at the click.
  **Team news** (`news_context.py`, pure; Sam, 2026-11-09: "JSN had a backup QB, CeeDee did not have his WR2"): facts, not a
  model, from the last five games and this week's statuses (`WeekInputs.news` / `team_news`): a pass catcher whose QB is a
  backup (the starter = who started the team's latest game), a pass catcher or lead back out (12%+ of the targets, or 50%+ of the
  snaps; only while he played in one of the team's last three games) and who leads the targets then. A chip after the name on the
  board, decisions, overview, plays calls and injury report (`week_views.NEWS_AT`, client side), the full line in the drawer, the
  compare tray and each injury card's header, and one brief sentence for the chalk and $7,000+ players (`week_board.news_sentence`).
- **Plan K on the Week page** (`week_money.py`, sections placed by day in `week_page.DAY_ORDER`, each with a trust chip):
  **bankroll health** (`bankroll_health.py`: the Season tab's curve by week, NFL DFS / betting / other / total, found by
  header; spend as a share of the bankroll; a stake guide by tier: `[stakes]` rules when set (with one "over the
  guide" sentence), else the Bankroll tab's Weekly Budget itself (Sam, 2026-11-06; `stakes.propose` is not used); input `bankroll`, saved fail-soft by `dfs sync`,
  `contests plan`, `bankroll stakes` and `week close`), the **Sunday inactives check** (`inactives.py`: every OUT / D / Q
  player, game not started, in `data/entered`, else the Lineups tab as the last Cockpit refresh saved it (`sheet_lineups`,
  only from config's live sheet); three pivots: fits the slot, salary <= freed + left, not started, CalPts for cash /
  Leverage for GPP; first and open on Sunday), the **field's lineup and copies** (`consensus.py`: max total CashOwn / GppOwn
  under DK rules via `field_gen.chalk_lineups(objective="sum")`; copies from `sim/dup_calibration.py`'s held-out fit,
  `data/model_cache/dup_calibration.json`, error shown; 5+ copies flags a GPP lineup, never cash), the **entry plan**
  (`entry_plan.py`: kind, entry limits, spend by tier, every entry has a lineup (`entered.EnteredRecord.blank`); the hint
  is `entries.suggest_pick`, Untested, never "Best in", never the portfolio plan), and the **practice trend** beside every
  Q / D tag (board, Explorer, drawer; `practice.py` from `data/raw/nflverse_injuries/`). The Cockpit gains blocks 8
  (inactives and pivots) and 9 (practice) below the first seven, and a plain `dfs sync` refreshes it too.
- **The news change log** (`news_log.py` pure, `news_store.py` I/O; blueprint Phase C item 1): every `dfs sync`, `dfs sync --live` and
  `dfs week new` diffs the EdgeRaw snapshot it just saved (`data/raw/edge/`) against the one before it and appends one row per changed
  field to `data/news/changes.csv` (`at, season, week, player_id, name, position, team, field, before, after, source, snapshot`; fields
  `status`, `proj_tffb` / `proj_sleeper` / `proj_fantasypros` / `proj_espn` / `proj_props` / `proj_agg`, `calpts`, `own_tffb`,
  `cash_own`, `gpp_own`, and per team `spread` / `total`); `dfs lineups late-swap --refresh-injuries` adds ESPN's escalations (source
  `espn_injuries`, once each). **Append-only**: the file is only ever opened for append (a test checks the earlier bytes after every
  write). A new slate's first snapshot is a baseline (no rows); `at` is when the sync SAW the change, not when the news broke. A failure
  warns and the command carries on. `dfs news backfill` loads every snapshot the log does not hold yet (idempotent; fills a hole a failed
  hook left); `dfs news changes [--week N] [--field status|...|all] [--player text]` prints a week's rows (read-only). No sheet.
- `dfs contests dupes [--weeks 4,5,6] [--dry-run]` -- refits the copies estimate on the saved standings (each week held
  out, ownership too), prints the error, saves `data/model_cache/dup_calibration.json`; files only. `week close` reruns it.
- **Plan K2, the learning loop** (`week_learn.py` on the page; `dfs week review [--week N]`, `week_review.py`, which
  `week close` runs at its end, fail-soft; files only, saved as inputs under the closed week): **what won**
  (`field_review.py`: each contest type's winner and last paid place with own sum / salary left / stack shape, the
  field's QB stack / bring-back / salary left / RB-in-FLEX / top stacks, Sam's lineups beside it; first and open
  Monday and Tuesday), the **process scorecard** under Last week and in the close output (`scorecard.py`: tier spend
  from `tier_week`, `cash:` chalk answers on the last round's rules against his cash lineups, each lineup against
  its nearest build of its kind, edited (8+ shared) or his own; `data/contests/scorecard.csv` gives the season line),
  **exposure against the field** (`exposure.py`: GPP entries per contest entry against GppOwn this week, against
  actual %Drafted after the close; 15+ points over / under), **how good are our numbers** (`numbers.py`: CalPts vs
  TFFB MAE by week from `calibration.backtest_by_week`, Cash % shown vs cashed from `data/contests/cash_shown.csv`,
  the signal table with chips; **every trust chip on the page links to it**, `week_page.chip_html`) and the
  **salary watch** (`salary_watch.py`: $300+ drop against last week's DK main-slate file with a steady role, snaps or
  targets within 10% over three games). The drawer has **Ask Claude** on the served page, and `dfs week serve` keeps
  `data/week/serve.pid`: a second start opens the running one, `--stop` sends SIGTERM to that pid only.
- **Rules box scope** (`suggest_state.scoped_item`): an Always use / Never use item may start `cash:` or `gpp:` and then
  applies to that kind of build only (`Rules.always_for(kind)` / `never_for(kind)`; unscoped items read as before, and a
  scoped conflict says Never wins for that kind).
- **The run lock** (`run_lock.py`): `data/.run.lock` makes one command that writes (sync, week new/close, setup *, lineups suggest,
  entries record/fill, bankroll *, season sync, contests plan, ...; `run_lock.WRITERS`) run at a time across the terminal, the chat and
  the buttons. A second writer is refused with what is running, since when and from where (terminal / chat / button); no `--force`.
  Read-only commands (status, doctor, week page, ...; `run_lock.READ_ONLY`) never take it; a dead owner's lock is replaced; a run removes the file as it ends (normally, failing or Ctrl-C); a `dfs`
  started by the holder (`week close --research`'s children) passes. A new command must be added to one of the two sets (a test fails).
- **Contest tiers** (`contest_tiers.py`, evidence in `docs/CONTESTS.md`): rules, not a lobby list. A / B / C / Avoid from a contest's
  format, fee, field, entries per user, prize pool and min-cash, with the rake, a reason and flags; the Weekly Budget splits about
  60 / 30 / at most 10% (and $15) across A / B / C. On the Week page: the plan in dollars, what to look for, the day's reminder, the
  check form, and the entries drop's spend by tier against the plan before lock. `dfs contests plan` adds the tier view; `week close`
  prints one line (planned against spent by tier). `[contest_plan] casual_eligible = false`: Sam is not eligible for Casual contests.
- `dfs doctor [--sheet-id <id>]` -- read-only structural check (tabs
  exist, the Cockpit and Stacks tabs are there, the Suggested tab is in the current layout (lock column, rules box; an old one
  says to run a plain `dfs lineups suggest`), `Exposure!H1` is the formula that counts built lineups (not a typed number), EdgeRaw's header matches `EDGE_COLUMNS`, `LINKED_EDGE_COLUMNS` is
  linked exactly once, Lineups' header repeats are where
  `LINEUPS_NAME_BLOCKS` expects, DK Upload's entries layout with its
  Lineup dropdown, typed-cell cue and protection exemption, and the hand-built per-row formula ranges on
  Results, Season, DkSalClean and PlayerPoolRaw have a formula on every row).
  Player Pool's and Lineups' native lookups are the current formula (`native-lookup-stale`: a baked EdgeRaw
  Name / NameKey letter an EdgeRaw column moved under; `dfs setup native-lookups` rewrites them, and a plain
  `dfs sync` does too).
  Run this after any structural change, against both sheets. Never writes
  anything. `dfs setup repair-formula-ranges [--sheet-id <id>]` rewrites the
  gaps it reports; it also expects Results/Bankroll/Season divisions to carry their
  empty-state guard (`dfs setup guard-empty-states [--sheet-id <id>]` restores them).
- `dfs bankroll fix-odds [--sheet-id <id>] [--dry-run]` -- the Betting ledger's Odds % is a true percent column
  (type 46%; a stored 0.007 is 0.7% and reads +14,186): converts the old literal-"%" values once (each to what it
  showed), then the format and the Odds formulas; never twice. A cell that is not a probability shows "type it as
  46%". Template first, then live; a closed week keeps its old reading (`odds_cells_are_percent`).
- `dfs setup sheet --sheet-id <id>` -- the full one-time sheet build (pool
  deck, Player Pool's add-a-player control row, view tabs, EdgeRaw linking,
  filters, protection, styling), in the one order that works; its own docstring
  (`setup_sheet` in `cli.py`) explains why that order. Everything under
  `dfs setup ...` is also runnable as an individual step.
- `dfs season sync|waivers|lineups|check|trades|trade|ros|outlook|setup|doctor [--league <name> | --all]` -- Sam's season-long Sleeper leagues
  (`src/dfs/season_long/`; leagues in the gitignored `season.toml` beside config.toml, NOT config.toml) on their own
  **Season** sheet (season.toml's `sheet_id`; never the weekly DFS sheet, no template, nothing copied week to week).
  Rest-of-season points in each league's scoring (Sleeper's projections rescored, held-out accuracy in
  `docs/SEASON.md`), team value (redraft: playoff weeks weighted by the team's own playoff odds; chopped: the survival
  simulation with chopped rosters going to waivers by FAAB), a trade analyzer (`dfs season trade "Give A + $10 FAAB"
  "Get B"`, writes the Analyzer tab) and finder (`dfs season trades --get/--give/--keep/--partner/--shapes/
  --max-consensus-loss`; with no filter it reads the Trade Finder tab's targeting box). Every trade is measured
  from each side's best free waiver move, never crediting a pickup anyone could make. Market value = FantasyPros'
  public ROS consensus (archived daily and scored held out beside ours on Overview; lopsided ideas, over 125% for the
  partner, are hidden unless `--lopsided`). Phase 2: `waivers` (pickups by value to his team, a FAAB bid guide from the
  league's own claims, handcuffs, a chopped bid plan; the Waivers tab), `lineups` (this week's best lineup with
  floor-ceiling and close calls decided by the matchup: win, median game or survival; K/DEF streams; My Teams),
  `check` (the Sunday check, read-only), playoff-week schedules (Players) and manager activity (League).
  Phase 4 (`outlook`): playoff odds by simulating every remaining game (byes, divisions, the median game; the team
  spread scaled by `calibration.py`'s measurement on the leagues' real scores), a chopped league's survival curve for
  every team, strength of schedule (team and player), power rankings with the week-over-week move (`history.py`,
  `data/season/history/`), on the Outlook tab; Overview also carries "this week across leagues" (`across.py`) and an
  odds history line per league. `outlook --check` scores the simulation from earlier weeks against what happened
  (Brier, vs record-only and scoring-only models; `outlook_check.py`) and back-fills the history.
  Nothing is ever written to Sleeper. `dfs season doctor` is the Season sheet's structural check. Method: `docs/SEASON.md`.
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
| "sync" | `dfs sync`, then `dfs doctor` | each source's status (name any failure, e.g. an expired TFFB login; a source that says "not published yet", like TFFB's GPS midweek, is **pending** -- a yellow line, exit 0 -- not a failure), doctor findings |
| "final sync" | `dfs sync --live`, then `dfs doctor` (safe at any time on Sunday: `sync_guard` keeps every started player's pre-kickoff values, rows a feed dropped, and DK's main slate after lock; report any "Kept from the last sync" lines it prints) | what changed since the last sync (the terminal diff / the Board's Queue), anyone in his pool now OUT or Q; then read the **Cockpit** tab (the sync refreshed it) and report its `SWAP` / `FILL` rows |
| "add X, Y to my pool" | `dfs pool add "X" "Y"` (writes EdgeRaw's Pool column to "Both" on the live week's sheet: say the sheet; a name that matches none or several is printed and skipped, never guessed; the Week page's Plays tick boxes run the same command) | who was added to the pool, and any name that matched nothing or more than one player |
| "build me lineups" | `dfs lineups suggest` (his pool must not be empty: if it is, say so and stop) | each build in one line (cash or GPP, shape, salary, Cash % / Top 1% (GPP builds rank by Top 1%; `--rank share` ranks by Top 1% share, only if Sam asks), and for a GPP build its ownership sum and product and expected duplicates: the numbers the terminal prints; say so if a cash build carries a duplicate warning), the cash chalk check (any CashOwn 30%+ player in none of the cash builds), and that **Lineup Tools > Copy builds to Lineups** (type `1,4,6` or `all`) puts them in |
| "contest plan" | `dfs contests plan` | the plan by format and by tier (A / B / C dollars from this week's budget and what to look for), last week against it (what was over, by format and by tier), and the H2H opponents to avoid |
| "record my entries" | `dfs entries record` (he downloads DK's entries file again after entering; never fetch it) | how many lineups, which came from a Suggested build and which are his own, the contests each went into; say if it fell back to the Lineups tab |
| "keep build 2" | set build 2's decision cell to `Keep` on the Suggested tab (a write to the live week's sheet: say the sheet), then `dfs lineups suggest --iterate` | the round produced and what changed (each build's note) |
| "lock Stafford and Nacua in build 4, swap the bring-back for McBride, rerun" | tick the locks and type the name over the bring-back cell on Suggested (the sheet and the chat must never disagree), then `dfs lineups suggest --iterate` | the round, build 4's note ("bring-back → McBride (yours); ...; Cash % 61% → 64%") or why the edit was refused |
| "give me 3 more like build 5" | set build 5 to `Vary 3`, then `dfs lineups suggest --iterate` | the round and the three variations (what differs, their Cash % / Top 1%) |
| "never Kyren", "always Bijan", "no bring-back for LAR" | write the rules box lines (`Never use`, `Always use`, `Stack rules`), then `dfs lineups suggest --iterate` | the round, which builds changed, and any rules line it could not read |
| "bump Nacua 2", "Kyren -15%", "Stafford to 24" | write the `Adjust` rules line (`Nacua +2`, `Kyren -15%`, `Stafford 24`; a percent needs its sign), then `dfs lineups suggest --iterate` | the round, which builds hold him (each note says "Nacua +2 (yours)") and how their Cash % / Top 1% moved; say that Model Check still scores the raw CalPts |
| "Kyren 40% owned" | write the `Ownership` rules line (`Kyren 40%`), then `dfs lineups suggest --iterate` | the round and what it moved (builds, dups, chalk watch); if Own% has not published yet, say the line waits for it |
| "min unique 2", "no more than 5 from one game" | write the `Build rules` line (`min unique 2`, `max from one game 5`), then `dfs lineups suggest --iterate` | the round and whether every new build honoured it (a build that cannot is reported, never relaxed) |
| "go back to round 2" | `dfs lineups suggest --round 2` | the round it was written as (a new round) and the builds |
| "fix flex" | nothing to run: tell him to use **Lineup Tools > Fix FLEX order** on Lineups (a sheet script); `dfs export` also puts the FLEX right in the file it writes | which of the two did it |
| "late swap" | `dfs lineups late-swap --refresh-injuries` (it covers every built lineup; the flag lays ESPN's game-day injury feed over the last sync's status first). **Live by default:** once a game has started it reads every started player's points so far from ESPN's public box scores and scores swaps on that state; if the output shows a red `LIVE SCORES UNAVAILABLE` line it ran on projections only: say so, prominently. Late swap never syncs (it reads `data/current/edge.csv` and ESPN). **If Sam says he downloaded a live standings export** (a Double Up and/or a GPP, from DraftKings by hand after the 1 pm lock), add `--field auto` (newest live exports in Downloads, this week's; say which file(s) it named, and "field: real" or "field: projected" as the output does; a file path works too). Never fetch the export yourself | a summary per lineup: who is still swappable (and any heavy chalk in none of his lineups, the yellow warning at the end), the best change and its Cash % / GPP % effect, or "nothing beats it"; for a GPP lineup on a live state (the live default once games are on, or `--field`) its position ("top 12%, 18 pts behind the top-1% line"; "about" top X% without an export) and each swap headed LEVERAGE (lowers his overlap with the field: the players in are less owned than the ones out), HOLD (raises it) or EVEN, with the in/out ownership (`late_swap_position.py`; the Cockpit's swap block shows the same label) (the run also refreshes the Cockpit tab where the sheet has one); with `--field`, which contest's field the lines came from and when it was exported |
| "late swap with live scores" | the same as "late swap" (live is the default now). "late swap on projections only" adds `--state projected` | the same summary as "late swap", plus the "field: projected + live scores (ESPN, read <time>)" line the output prints |
| "close the week" | `dfs week close --research` (it finds the newest history export in Downloads by its header and says which; pass `--csv <file>` if he names one). The close runs first, as always; then the offline studies run unattended (under 45 minutes, about as long as the slowest one: run it detached with a log, `caffeinate -i`, and read the log; a stage that fails or times out is reported, never retried). If the close itself fails, show the error and stop | the Model Check verdict and the learned block, the bankroll lines it reconciled, and what it kept and filled on the contest side (standings moved, empty downloads, Cash Line, the Contests tab), then the Contests tab's THIS WEEK, YOUR DECISIONS block (five lines), the contest plan line (spent vs plan, over in), the cash post-mortem's headline (how many cash builds would have cashed, the top separators and their tags, his cash lineup against its nearest build), and any "no record of what was entered" warning; then the 15-line research summary it prints (paste it to planning as is) and the path of the one report, `docs/research/weekly/<season>-wk<NN>-close.md` |
| "show me the week", "what now" | `dfs week page` (builds the page and opens it in the browser; it only reads what the tool already saved: no sheet, no network) | today's steps from the page (the phrase and command for each), what it says is stale, and every section that says "not run yet" with what to say to fill it |
| "open the week" | `dfs week serve` (opens the Week page as a local app with the chat docked beside it; leave it running; Ctrl-C stops it) | the link it printed, and say that the chat, buttons and drops live on that page |
| "check a contest" | nothing to run: tell him to use the **check this contest** form on the Week page (`dfs week serve`): format, fee, entrants so far / max, entries per user, prize pool or min-cash | the tier, the rake and the reason the form returns |
| "drop my entries" | nothing to run: tell him to drop DK's entries file (downloaded again after entering) on the Week page's drop zone, then press **Record these entries** (it runs `dfs entries record --csv <that file>`) | how many lineups and contests were recognised, the spend by tier against the plan (and what is over, while he can still withdraw), and any warning that the file is for another week |
| "how did we do" | read (do not write) Model Check: the opening verdict sentences and "What the model learned this week" | five lines: who leads the projection race by position, what CalPts learned, how the signals are running, whether the probabilities hold, the Week 8 checkpoint |

Season-long shortcuts (the Season sheet; `dfs season ...` prints its title and URL before any write):

| Sam says | Run | Report back |
|---|---|---|
| "season sync" | `dfs season sync` (all leagues; a few minutes, run it detached with a log) | per league: his odds (playoffs, or survive this week / last man standing), this week's win / survive chance, lineup alerts, the free move first, the best pickup with its bid, the top 3 ideas |
| "playoff odds" / "power rankings" | `dfs season outlook` (a sync writes the same tab) | per league: his playoff odds (and bye / division), where the others stand, his power rank and move, the schedule ease; in Choppa his survival curve; then Overview's "this week across leagues" in order |
| "check my lineups" | `dfs season check` (read-only; ESPN's game-day feed laid over Sleeper's statuses) | every `SWAP` (empty slot, bye, OUT / Doubtful, with the swap to make) and `LOOK` (Questionable, a better bench option), per league; or that every league is clean |
| "set my lineups" | `dfs season lineups` (prints; Sam sets them in Sleeper by hand, nothing is written to Sleeper) | per league: the lineup with each starter's floor-ceiling, the close calls and why, the win / median / survive odds, the K/DEF streams |
| "best pickups in <league>" | `dfs season waivers --league <league>` | the top pickups (add, drop, gain, this week, the bid range and who could outbid him), handcuffs, and in Choppa the bid plan |
| "what should I bid on <player>" | `dfs season waivers --league <league> --player "<player>"` | what he adds, the drop, the bid range from similar claims, what he is worth to Sam, the rivals |
| "find me trades in <league>" | `dfs season trades --league <league>` | the top 10 ideas (give / get / partner / his gain / market for them / the why line) |
| "find me an RB" (in <league>, else every league) | `dfs season trades --get RB [--league <league> \| --all]` | the same, per league |
| "who'll buy <player>" | `dfs season trades --give "<player>" --league <league>` | the partners and what each gives back |
| "trades for <player>" | `dfs season trades --get "<player>" --league <league>` | the same |
| "add X, Y to my buy list" / "take X off my buy list" | edit `buy_list` in season.toml (gitignored, beside config.toml; keep its comment) | the list as it now stands; the next "season sync" / "find me trades" ADDS ideas for it and each league's weakest spot (tagged TARGET) to the standard ones (`--open` leaves them out) |
| "I want to trade away X, Y, Z" (any leagues) | `dfs season trades --all --give "X, Y, Z"` (a trade that sends any one of them counts; in a league where he owns none of them, that league is skipped and the output says so) | per league: the top ideas that move them, and any name he does not own there |
| "find me trades for A, B, C and D" (a list, any leagues) | `dfs season trades --all --players "A, B, C, D"` (per league, a name he owns is a sell and one on another roster a buy; names not in a league are skipped there) | per league: which names were sells and which buys, and the top ideas |
| "analyze trade X for Y + $10 FAAB in <league>" | `dfs season trade "X" "Y + $10 FAAB" --league <league>` (FAAB goes on the side that pays it) | both sides' change, the market line and the verdict; it wrote the Analyzer tab |

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
| `docs/SEASON.md` | You're working on `src/dfs/season_long/` (season-long leagues, the Season sheet): setup, method, accuracy, the sheet's tabs. |
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
