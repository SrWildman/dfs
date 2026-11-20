# Code map

Which modules, files, config keys and tests are behind each area of the tool outside the Week page (that one is
`docs/WEEK_PAGE.md`). What a command does for Sam is in `docs/COMMANDS.md` (generated from the CLI) and
`docs/WORKFLOW.md`; the math is in `docs/CALCULATIONS.md`; what a tab or column means is in `docs/SHEET_REFERENCE.md`.
`CLAUDE.md` points here. Each block names the round that built it where that helps find its planning notes.

Pattern everywhere: pure logic in one module, sheet or file I/O in a `sheet_*.py` / `*_store.py` / `*_inputs.py` beside
it; anything a sync or a close runs as a side effect fails soft (it warns and the command carries on); a tab that a sync
refreshes is refreshed **only on a sheet that already has it**, never created by the sync.

## The CLI itself

The user's **standard week** is `dfs week new`, `dfs sync`, `dfs lineups suggest`, `dfs export` (then, at lock,
`dfs entries record`), `dfs sync --live` + `dfs lineups late-swap`, `dfs week close`; everything else is reference. `docs/COMMANDS.md` lists every command
and option and is GENERATED from the CLI (`python -m dfs.commands_doc`): after adding or changing a command or
option, regenerate it -- `tests/test_commands_doc.py` fails if it is stale or a command has no section.

Plain `dfs` is a guided menu of that standard week (`launcher.py`) that prompts for any required argument; a new
command needs a `launcher.MORE_LABELS` entry and its `commands_doc.SECTIONS` section a `launcher.MORE_TOPICS`
topic, which is how the "More, by topic" screen reaches it (tests enforce both).
Typed commands also ask for a missing required argument at a terminal (`prompt_missing.py`; every Typer app needs
`cls=PromptingGroup`) and fail as before when piped/scripted.

- **The run lock** (`run_lock.py`): `data/.run.lock` makes one command that writes (sync, week new/close, setup *, lineups suggest,
  entries record/fill, bankroll *, season sync, contests plan, ...; `run_lock.WRITERS`) run at a time across the terminal, the chat and
  the buttons. A second writer is refused with what is running, since when and from where (terminal / chat / button); no `--force`.
  Read-only commands (status, doctor, week page, ...; `run_lock.READ_ONLY`) never take it; a dead owner's lock is replaced; a run removes the file as it ends (normally, failing or Ctrl-C); a `dfs`
  started by the holder (`week close --research`'s children) passes. A new command must be added to one of the two sets (a test fails).

- **A second slate in one week** (`slate.py`, `slate_close.py`; Thanksgiving first, `docs/WORKFLOW.md`): a slate key
  (`main`, or a day: `thu`, `fri`, `sat`, ...) chosen by `--slate` or `DFS_SLATE`, read by the CLI entry point
  (`prompt_missing.PromptingGroup.main`, before the run lock) and held for the whole command (`slate.active`). Only
  `slate.SLATE_COMMANDS` take it (a test checks the `--slate` options match); every other command refuses a secondary
  slate before it runs. A secondary slate's sheet is `[slates.<key>] sheet_id` (`config.load_config` hands it out as
  `google_sheets.sheet_id`; `config.load_base_config` is the file as written), and its files are under
  `data/slates/<key>/`, laid out like `data/` (`slate.root` / `slate.path`): `store.current_dir` / `raw_dir` /
  `manifest_file`, `pool_store.pool_dir`, `suggest_state.state_dir`, `entered.record_path`,
  `late_swap_store._store_dir`, `signals_data.signals_dir`, `news_store.changes_path` / `edge_dir`,
  `runlog.runlog_path`, `week_inputs.data_root` (so the page, its pidfile, chats, jobs and edits follow). Main's
  paths are exactly what they were. Its salaries: `sources.dk_salaries.pick_slate_draft_group` (the largest Classic
  group all on the slate's day, Eastern time). The close: `slate_close` keeps a secondary slate's contests out of
  Results and its standings out of `data/raw/dk_standings/` (`contests.capture.capture_standings(divert=...)`).
  Tests: `tests/test_slate.py`, `tests/test_slate_close.py`, `tests/sources/test_dk_salaries_slates.py`.

## Sync, sources and the news log

- **The news change log** (`news_log.py` pure, `news_store.py` I/O; blueprint Phase C item 1): every `dfs sync`, `dfs sync --live` and
  `dfs week new` diffs the EdgeRaw snapshot it just saved (`data/raw/edge/`) against the one before it and appends one row per changed
  field to `data/news/changes.csv` (`at, season, week, player_id, name, position, team, field, before, after, source, snapshot`; fields
  `status`, `proj_tffb` / `proj_sleeper` / `proj_fantasypros` / `proj_espn` / `proj_props` / `proj_agg`, `calpts`, `own_tffb`,
  `cash_own`, `gpp_own`, and per team `spread` / `total`); `dfs lineups late-swap --refresh-injuries` adds ESPN's escalations (source
  `espn_injuries`, once each). **Append-only**: the file is only ever opened for append (a test checks the earlier bytes after every
  write). A new slate's first snapshot is a baseline (no rows); `at` is when the sync SAW the change, not when the news broke. A failure
  warns and the command carries on. `dfs news backfill` loads every snapshot the log does not hold yet (idempotent; fills a hole a failed
  hook left); `dfs news changes [--week N] [--field status|...|all] [--player text]` prints a week's rows (read-only). No sheet.

## Builds: Suggested, the Default cash lineup, the search

- **The Default cash lineup** (blueprint Phase A; `cash_core.py`, pure): `dfs lineups suggest` builds ONE cash build by default,
  the **Default** (the top projection; ties: cheaper, then names), and from the same top-300 search its **core** (every player in
  70%+ of the lineups within 1.5 points of it) and its **open spots** (the other slots, each with up to two single swaps from the cash
  pool that keep the lineup legal and within the 1.5 points, shown as differences: points, salary freed, cash ownership, status).
  **No per-build Cash % is shown** on Suggested, in the terminal or on the Week page: the cash chance is a band (`under` / `coin
  flip` / `lean`, the simulated chance against the measured double-up line `du_line.json`) and the record of every cash build
  scored (`data/contests/cash_shown.csv`). The week input `cash_core` (`cash_core.payload`) feeds the Week page's **cash board**
  (Play / Fade per core player = `cash: Name` on Always / Never use, decision key `core:<Id>`), **open spots** (one button per
  candidate = `cash: Name` on Always use, key `spot:<SLOT>`; the served page then runs `dfs lineups suggest --iterate --only
  cash`) and **cash lineup** card; the board's Cash core role is the real core once a run saved one (`week_plays.cash_core_guess`
  before). `--only cash|gpp` (with `--iterate`) reruns one kind and freezes the other; `--only cash` rebuilds the cash builds even
  when their decision is Keep (blank), so the Default follows the rules box. The entries hint (`entries.suggest_pick`) for a cash
  contest is the Default. `dfs week close` refits the line (`du_line_retro.refresh_summary`, before the review). The Lineups tab's
  Cash % column and late swap's Cash % stay this season.
- **Rules box scope** (`suggest_state.scoped_item`): an Always use / Never use item may start `cash:` or `gpp:` and then
  applies to that kind of build only (`Rules.always_for(kind)` / `never_for(kind)`; unscoped items read as before, and a
  scoped conflict says Never wins for that kind).
- **`[builds] solver`** (config.toml, and the same key in a `dfs backtest` settings file; `build_exact.py`; blueprint Phase E;
  **default `"search"`, off**): `"search"` is the budgeted branch-and-bound that `--seconds` can cut short; `"exact"` runs the same
  searches to the end with no budget, in seconds (each QB's stack search split into cases where the shape holds by construction: his
  first teammate, the first bring-back(s); the old `_Search` does each case, told the score to beat), with the same pool, rules,
  objective, `rb_dst` / `contrarian` anchors, simulator and selection, so no "search cut short" note and the same builds on every run;
  `"milp"` is its independent checker (`build_milp.py`: HiGHS through `scipy.optimize.milp`, every search rule a linear constraint,
  top-K by no-good cuts; exact but 15-20 minutes a week, never for building). `lineup_suggest` / `lineup_shapes` call
  `build_exact.top_lineups` (the switch; `build_exact.use(name)` sets it for a block); a whole-lineup test says what it checks with
  `build_exact.described(test, LeafRules(...))` so the exact solvers can use it early (the test itself still decides). Late swap never
  goes through it. Proof: `python scripts/exact_equivalence.py --weeks 2,4,5,6,7,8,9` (the old search with no budget, `exact` and
  `milp`, shape by shape; `--report` prints the tables; results in `docs/SIM.md`, "Exact search") and `tests/test_build_exact.py`
  (the MILP against the exact search on a fixture). Settings for Flashback: `research/settings/today_exact.toml`,
  `today_gpp_exact.toml`, `calls_fade_gpp_exact.toml`. Flashback's tables also show the mean **finish percentile** (share of the
  real field matched or beaten), mean **points** and the **top-10% rate** beside the dollar ROI, and `--compare` their paired
  difference by week (`backtest.paired_metric`); the verdict line is still the ROI ship bar.

## Stacks

- `dfs stacks [--sheet-id <id>] [--no-sheet] [--refit] [--retro]` -- ranks every QB's stacks of one and two teammates (WR/TE, or a RB
  whose `Tgt%` is in the top third of the slate's RB1/RB2; with and without a bring-back) by simulated Top 1% rate against how much of the
  generated GPP field plays them, and writes the read-only **Stacks** tab (right after Suggested; top 20 stacks, top 8 games, the retro's
  track record on top; information only: no row is cued or tinted, and columns A and M stay blank where the `BUILD` cue and its
  `Always use: ...` line were until Removal F8, so nothing moves; `Top 1%` / `Lift` / `Lev` are colour-scaled and row 3 is the colour legend). Prints the same page; writes the sheet
  only where the tab exists. Before TFFB publishes ownership the field %/leverage columns read `-` (a CalPts stand-in drives the field). `--refit` refits
  the field model on the real standings (about two minutes, saved in `data/model_cache/stack_field.json`); `--retro` refits and reruns the retro
  that is the tab's track record (`data/model_cache/stacks_retro.json`, several minutes; run it after `dfs week close`). Math in `stacks.py`
  (pure), layout `stacks_tab.py`, I/O `sheet_stacks.py`, retro `sim/stacks_retro.py`, live inputs `stacks_inputs.py`. A plain `dfs sync` refreshes the tab
  (about a minute; never on `--live`, never fails the sync); `dfs week new` refreshes it on the new sheet after its sync. An information view: the verdict on its leverage signal (`tested` / `weak` / `untested`) is on the tab.
- `dfs setup stacks [--sheet-id <id>]` -- creates (or rebuilds) the Stacks tab right after Suggested. Template first, then live, `dfs doctor` after
  (check `stacks-tab`). Needs `dfs stacks --refit` once.

## Entries and DK Upload

- `dfs entries record [--csv <file>] [--h2h posted|joined|both] [--from-sheet] [--sheet-id <id>] [--dry-run]` -- at lock, after entering
  (`entered.py`): DK's entries file downloaded again (newest in Downloads, refused when it is another week's), else
  the Lineups tab; writes `data/entered/<season>-wk<NN>.json` (each lineup, its contests, its source build: 8+ of
  9 players in one saved build of the week, else "own"). `week close` warns when the week has none.
- `dfs entries fill [--csv <file>] [--write] [--again] [--dry-run] [--sheet-id <id>]` -- DK's entries file (newest in
  Downloads, by header) onto `DK Upload`: one row per Classic main-slate entry; **Sam picks each entry's `Lineup`**
  (dropdown) and nothing is ever auto-assigned (`Suggested` is a grey hint, `entries.suggest_pick`, the hook
  Contests Phase 2's "Best in" replaces); re-runs match rows by Entry ID and never overwrite a dropdown; `--write` writes
  `upload_<timestamp>.csv` (only set entries DK does not hold); `--again` after a late swap; a started player is never
  touched. `dfs entries print` lists every lineup as `Name (ID)` (CalPts, ProjPts, Own% first). Needs
  `dfs setup dk-upload` (DK Upload's layout: `lineups.DK_UPLOAD_SHEET_HEADER`; template first) once per sheet.

## Sunday tabs: the Cockpit

- `dfs setup cockpit [--sheet-id <id>]` -- creates (or rebuilds) the read-only **Cockpit** tab right before Board (to its left): Sunday's last 90
  minutes on one page (new Q / D / OUT in his lineups, open slots with the best change from the last `late-swap` run, wind, the Lineups
  warnings, exposure top 10, DK Upload entries without a Lineup, budget). Nothing on it is typed; column A is the cue (`SWAP`, `LOOK`,
  `FILL`, `ok`). `dfs sync` (plain and `--live`) and `dfs lineups late-swap` refresh it, **only on a sheet that already has the tab** (they never create it
  and never fail over it); `late-swap` saves its run to `data/cockpit/late_swap.json` for it (`late_swap_store`). Template first, then live,
  `dfs doctor` after (check `cockpit-tab`). `dfs week new` rebuilds it on the new sheet after the sync
  (fail-soft, only if the copied template has the tab) so the template's stale render never survives. Layout: `cockpit.py` (pure), I/O: `sheet_cockpit.py`.

## Contests, tiers and money

- `dfs contests plan [--week N] [--budget X] [--no-sheet]` -- terminal only (`contest_plan.py`): this week's plan
  by format (`[contest_plan]`, code defaults: Showdown at most $5, N-max GPPs with 2,000+ entrants at most $10,
  the rest of the Bankroll tab's Weekly Budget to double-ups, 50/50s, H2Hs and SE GPPs under 1,000 entrants),
  last week against it, ROI and effective rake (`1 - prize_pool/(entries x fee)`) by format and field size, and
  the H2H regulars to avoid (0-3 or worse, or under 30% over 5+). `week close` prints its one line ("spent $X vs
  plan $Y (by format)": facts, no "over"). Leaves `contest_plan` and `ledger` for the Week page (`week_inputs.save_input`).
- **Contest tiers** (`contest_tiers.py`, evidence in `docs/CONTESTS.md`): rules, not a lobby list. A / B / C / Avoid from a contest's
  format, fee, field, entries per user, prize pool and min-cash, with the rake, a reason and flags; the Weekly Budget splits about
  60 / 30 / at most 10% (and $15) across A / B / C. On the Week page: the plan in dollars, what to look for, the day's reminder
  and the entries drop's spend by tier against the plan before lock. `dfs contests plan` adds the tier view; `week close`
  prints one line (planned against spent by tier). `[contest_plan] casual_eligible = false`: Sam is not eligible for Casual contests.
- `dfs contests dupes [--weeks 4,5,6] [--dry-run]` -- refits the copies estimate on the saved standings (each week held
  out, ownership too), prints the error, saves `data/model_cache/dup_calibration.json`; files only. `week close` reruns it.
- `dfs bankroll stakes [--propose] [--no-sheet] [--sheet-id <id>]` -- rebuilds the **STAKES** block on Bankroll
  (to the right of the ledgers, `sheet_stakes.STAKES_FIRST_COLUMN`): his ROI / cash rate / weekly SD / entries by
  format, this season and all seasons in the contest log, and this week's plan from the tab's own Weekly Budget
  (`week.BANKROLL_WEEKLY_BUDGET_CELL`, never a second copy in config) and the optional `[stakes]` rules
  (`max_pct_bankroll_per_week`, `max_entry_fee_pct`, `split`). Math in `stakes.py`; phrased as arithmetic on his
  own settings, never advice. `--propose` prints suggested rule values with the arithmetic and writes nothing.
  `dfs week new` rebuilds the block on the new sheet (never failing); `doctor` flags a sheet without it.

- `dfs setup other-sports [--history] [--csv <file>] [--dry-run] [--sheet-id <id>]` -- once per sheet, template
  first: non-NFL DK contests get their own Bankroll ledger (`[bankroll.other]`, default rows 151-171, its line on
  row 11) and Season columns (`Other Net` / `Other Risked`, R/S), so Cash and GPP are NFL only and Ending
  Bankroll still counts every dollar; `--history` (never on the template) moves the season's non-NFL money out of
  GPP. `week new` / `week close` re-read the closed week's Season row from its own Bankroll. Doctor:
  `bankroll-other`, `season-other`.

## The close, Model Check and research

- `dfs week close --research [--monthly]` / `dfs research close-report [--week N] [--only scorecards,refresh,stacks,calls,calls_flashback] [--monthly] [--deadline MIN]` -- Monday
  on autopilot (`close_research.py`; blueprint Phase D): **every week the weekly set**, `close_research.WEEKLY` = the `scorecards` stage
  (the four scorecards, `week_review.scorecard_inputs`: no builder, a minute or two); **the monthly set** (`MONTHLY` = the four stages
  below) runs after it, under what is left of the same deadline, on the first close of a calendar month (`first_close_of_month`: the
  Monday after the week's Sunday; Weeks 4, 8, 13 and 17 in 2026) or with `--monthly`. The stages run as child processes (each with a log under
  `data/research/close/<season>-wk<NN>/`; the parent stops any still running at the deadline, 45 min): `dfs research refresh --through
  <week>`, `dfs stacks --retro`, the plays model's `calls` (the week's calls scored into
  `data/research/calls_history.csv`, weights refitted; `research/plays_backtest.close_week`), and `calls_flashback` (`dfs backtest` of
  `calls_avoid_cash` against `today_cash` and `calls_fade_gpp` against `today_gpp`, Plan M M1.5's ban-only variants; settings in `research/settings/*.toml`, in the repo). Each fails soft.
  ONE report, `docs/research/weekly/<season>-wk<NN>-close.md` (gitignored): the close summary (what `week close` printed, plus the Contests
  tab's decisions block), each study's headline and ship-bar verdict, "changed since last week" against the previous close report. Nothing
  writes a sheet (`close-report` reads none at all). `week close --research` skips the studies, saying why, when the week is not scored yet.
- `dfs week close` also prints the **cash post-mortem** (`cash_postmortem.py`): the 10 biggest double-up casher /
  non-casher gaps (8%+ of the field) tagged in N cash builds / pool only / missed, every distinct cash build
  scored against the cash line, the entered cash lineup against its nearest build; rows appended to
  `data/contests/cash_postmortem.csv`, the summary left as `postmortem` for the Week page. `dfs lineups suggest`
  prints the **cash chalk check** (every CashOwn 30%+ player and how many cash builds hold him; display only).
- `dfs results update [--week N] [--all] [--sheet-id <id>] [--no-sheet]` -- scores every completed week against
  nflverse's actual stats and rebuilds the `Model Check` tab (also run at the end of `dfs week close`, which it
  never fails). Projection = last TFFB snapshot before each player's own kickoff (ESPN and the props likewise: the last snapshot before his team's / his own kickoff); flags/ValAdj recomputed from the
  raw archives with current code.

- `dfs research refresh [--through N] [--season S] [--only a,b]` -- the weekly offline reruns in one
  command, no sheet: the teammate-bump race, the field_own scores, the ESPN / props blend gate, `sim/contest_retro`, the optimizer's-curse study (`research/curse.py`), and the calibrated
  double-up Cash % offset (`sim/du_line_retro.py`; it saves the offset `[sim.lines] calibrated_cash_offset` shows Cash % against,
  default off, and says when it clears the bar; all three plug in through `refresh_summary(season, through)`, see `research/refresh.py`). Each fails soft; it writes
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

## The plays model (shadow)

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
  `dfs sync --live` first (which refreshes the Cockpit). Never writes a sheet. The Week page shows only the
  AVOID and FADE calls, shadow, with their records (Model vs sources, Saturday's Fades, the Overview; `docs/WEEK_PAGE.md`),
  with the hold-back above; CORE / PLAY / TARGET / LEVERAGE are still computed and scored but left the page in Removal F8.
  Backtest: `python -m dfs.research.plays_backtest run` (report in `docs/research/plays/`, gitignored). Flashback:
  `dfs backtest --settings research/settings/calls_cash.toml --compare research/settings/today_cash.toml`
  (flag `calls`: `true` = CORE locked + AVOID banned; M1.5's ban-only variants `"avoid"` with `calls_avoid_cash.toml` against
  `today_cash.toml`, `"fade"` with `calls_fade_gpp.toml` against `today_gpp.toml`).

## Sheet set-up and `dfs doctor`

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
- `dfs setup sheet --sheet-id <id>` -- the full one-time sheet build (pool
  deck, Player Pool's add-a-player control row, view tabs, EdgeRaw linking,
  filters, protection, styling), in the one order that works; its own docstring
  (`setup_sheet` in `cli.py`) explains why that order. Everything under
  `dfs setup ...` is also runnable as an individual step.

## Season-long

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

## The tests

- **The tests** (`tests/conftest.py`; the study behind it is `docs/planning/audit/TEST_SPEED.md`). A bare `pytest -n 6 --dist loadfile`
  is the **editing run**: it leaves out every test marked `slow` (about a second or more: the lineup search's files,
  `tests/sim`'s long studies, individual tests elsewhere) and `browser` (Playwright, Chromium and Firefox; skipped anyway where an engine is not
  installed), says "N slow/browser/exhaustive tests not run", and **fails an unmarked test over 3 s** ("mark it slow or make it
  faster"; `--speed-guard SECONDS`, 0 turns it off). **Naming a file or a test (`pytest tests/test_plays.py`) runs everything
  in it.** **`pytest --full -n 6 --dist loadfile` is required before every PR**, locally, and CI runs the same with
  `-n auto`: a green default run proves nothing about the builder. `--exhaustive` adds the long versions of properties
  a shorter test already covers (the simulation-seed test's other three seeds). Mark a new slow test
  `@pytest.mark.slow` (a whole file: `pytestmark = pytest.mark.slow`). `--dist loadfile` keeps a module's fixtures on one
  worker (the default spread rebuilt a 41 s fixture on each). The data guard runs in every worker; only the controller
  snapshots `data/`. Then `ruff check .` and `ruff format --check .`. CI installs from the tracked `uv.lock`
  (`uv sync --locked --extra dev`): after changing a dependency in `pyproject.toml`, run `uv lock` and commit both. None of
  the tests need real credentials, a real sheet, or a browser.

