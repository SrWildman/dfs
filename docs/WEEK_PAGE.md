# The Week page

The Week page (`dfs week page`, static) and its local app (`dfs week serve`), for a session changing them: what each
part shows, the module behind it, the files it reads and the tests that cover it. What the page is FOR, day by day, is
in `docs/WORKFLOW.md`; the exact math behind a number is in `docs/CALCULATIONS.md`. `CLAUDE.md` points here; the
rules that are never broken (the served page's guards among them) stay in `CLAUDE.md`.

Every part below was built in a named round (Plans I, J, K, K2, N, O, O2; blueprint Phases A-D; the design pass); the
names are kept in parentheses so a session can find the round's planning notes, but the page is described as it is now.
The design pass (PR #92, 2026-11-17; `docs/planning/audit/DESIGN_PASS_2026-11.md`) put one shell and one design system
around the page: the shell and the Saturday view come first below; every older block is still on the page, on a
More view and on Everything.

## The shell: day views, status lines, This week, More (`week_shell.py`, `week_style.py`)

- **The header** (sticky): the week, three **day views** (Saturday `#build`, Sunday `#lock`, Tuesday `#close`;
  `week_shell.DAYS`), player search (`#psearch`, `/` focuses it) and **Ask Claude**. Every day view is reachable on any
  day; the clock (`lock_state.state`) only picks the one that opens first, and carries the "now" dot. The last view is
  kept per week and clock state (`dfs-view-<season>-<week>-<state>`), so Saturday's choice never hides Sunday's page.
- **The chat starts folded on every load**, at every width: there is no rail, **Ask Claude** opens it, and nothing
  remembers it open (`week_live`, the `chat-open` / `chat-closed` body classes).
- **A status line per day** (`week_shell.status_lines`; Sam, 2026-11-17), grey, amber or green only for an outlier:
  - Saturday (`saturday_facts`): Lock · Pool (by position) · Builds · News (the last day of the change log before a
    build, `NEWS_WINDOW`; since the build after) · Entered;
  - Sunday (`sunday_facts`): Lock · News since Saturday night · Lineups at risk · Swaps open · Entered;
  - Tuesday (`tuesday_facts`): Week net · Cash x of y · Scorecards · Next.

  An item that waits on a step says what it waits for: "not run yet" never takes a row.
- **This week** (the side column, `this_week_html`, on the three day views and Everything): the six steps of the week
  (`week_board.progress`) as a checklist, each step not done with its **Run** button (a command button on the served
  page, the `dfs` line to copy on the static one), and the **file drop zone** below it. A step that has not run is
  listed here, not in the body.
- **More** (`week_shell.MORE`, `more_html`): one row of pills, each a view of its own (`#name`), so every section is
  one click away while the day view leads: Board, Explorer, Games, Usage, Injuries, Model vs sources, Overview (the old
  Saturday scan) | Builds, Game day, Ledger and plan, Last week, Season-long | Run a command, Everything. Where each
  block shows: `week_views.PLACES` (the page's own blocks) and `week_views.FOLD_PLACES` (each older fold: its More view
  and the day view that also shows it, e.g. the inactives check on Sunday and Game day).
- **The design system** (`week_style.py`): tokens on `:root` with a dark mode, a type scale (11 chip, 12 meta, 13 dense,
  14 body, 16 section title, 22 headline number; weights 400 and 600), spacing 4 / 8 / 12 / 16 / 24 / 32, and the
  components new blocks are drawn with (header, status line, card and fold, table, player row, tag, trust chip,
  candidate card, lineup card, checklist, More bar). Grey by default; the accent only for actions; good / warn / bad
  only for outliers. The older blocks keep their class names: their variables (`--card`, `--dim`, ...) alias the tokens.
- **The pool square** on every name: 22 px, left of the name, C / G / B, `+` on hover, smaller inside running text;
  the same `dfsPool` store and popover as the pool chip below.

## Saturday: the build view (`week_saturday.py`, pure; placed by `week_top`)

- **Before a build, the slate digest leads** (`digest_html`, `#digest`): the games ranked by total with each team's
  implied total, wind / dome and the line's move since the opening snapshot; the key injuries among likely starters,
  with who gains, and the line moves (a spread move of `MOVE_SPREAD` points or more) in one list (`news_items`); then
  the best plays, `BEST_PER_POS` (3) per position with one number each, the projection (the rest is in the drawer).
- **After a build, "Chalk you're not on" leads** (`chalk_not_on` / `chalk_html`, `#chalk-off`): every player at 30%+
  CashOwn (`week_page.CHALK_THRESHOLD`) who is not in his cash lineup and not OUT / IR, with projection, salary and
  why, and the Play / Fade that used to sit in the decisions. His cash lineup (`your_cash_lineup`) is the Default when a
  run saved one, else the newest round's first cash build, else the cash lineup he entered. Information only. Then
  Phase A's cash board and open spots, the other decisions, and the digest folded.
- **"Someone else…" on each open spot**: a search over Sam's pool, limited to players who fit the slot (his position;
  RB / WR / TE for FLEX) and the salary (the Default's player's salary plus what the Default leaves), not already in
  the Default, each shown as a difference against the Default's player (points, salary freed, cash own) before he picks
  (`spot_options`, drawn by the client, `week_ui` `otherDraw`). A pick goes through `/api/decide` like a card: the
  server checks the fit again (`fits_spot`; a player who does not fit is refused), writes the same `cash: Name` on
  Always use, adds him to the pool as Cash, and only after the write succeeds remembers him as the spot's answer
  (the `decisions` input, key `other:spot:<SLOT>`, `week_board.OTHER`); Undo takes it back.
- **Fades** (`fades_html`, `#fades`, folded under the day's cards): FADE↓ with its Model Check record and the plays
  model's AVOID in cash (Shadow, with its backtest and live record), one list; and **Your pool**, folded.
- The cash board's Play / Fade column is no longer clipped at 1440 px.

## The page and its sections

- `dfs week page [--week N] [--open/--no-open]` -- **the Week page** (v2): one self-contained HTML file,
  `data/week/<season>-wk<NN>.html` (inline CSS, an inline JSON block and script for the Explorer, no external loads), opened
  in the browser. Its blocks, each on a view of the shell above since the design pass (`week_views.PLACES` /
  `FOLD_PLACES`): **the brief** (on Overview; 5-7 deterministic, day-aware sentences; `week_board.brief`) with the next step
  and the six **progress steps** under it (Pool, Contest plan, Builds, Entries recorded, Late swap, Close, each done / next /
  to do from the real state, `week_board.progress`; the same steps are This week's checklist), the **pool panel** (folded
  under Saturday's cards) (Cash / GPP counts by position, gaps, Questionable
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

## The local app: `dfs week serve`

- `dfs week serve [--week N] [--port P] [--no-open] [--sheet-id <id>] [--stop]` -- **the Week page as a local app** (`week_server.py`;
  the static `dfs week page` is unchanged): serves the page on this Mac only (`127.0.0.1`; the link it opens carries a
  random token, and every request needs the token and a localhost `Host`), with **Claude Code beside it** (folded on every load until **Ask Claude** opens it)
  (`week_chat.py`: `claude -p` in stream-JSON mode in the main checkout under Sam's own login; model / effort / New chat /
  Stop / this week's chats; a call that needs approval shows on the page as Approve / Deny with exactly what will run,
  nothing is auto-approved, `bypassPermissions` is never used, "auto" is a switch he turns on; no background commands
  (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), a turn is read until its process exits (it can print several `result`s;
  the answer is the first after Claude Code replays Sam's message), a turn with no answer ends in a red line; after any
  chat change run `python scripts/chat_smoke.py`, four turns against real Claude, not in pytest), an **Ask** button
  beside every phrase, **run buttons** for every command with live output (`week_jobs.py`: one job at a time, Stop, history;
  `week new` / `week close` / `lineups clear` / `pool clear` / `setup ...` need the command typed out; a closed week is
  read only), and the **file drop zone** (in This week; the check-this-contest form left in Removal F7) (`week_drops.py`: DK's entries file at lock, the
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

## The client: one script on both pages

- **Plan N on the Week page** (client side in `week_ui.py`: CSS and one script on the static and the served page; no
  library, no network of its own): the chat starts folded on every load (the shell, above); one wide column
  (board, `.charts-row`, games, Explorer); sticky headers in each `.scroll` box (the board repeats `board_head` per
  position) and a sticky name column (`table.stickyname`); Opp / Spread / Total / Implied on every player grid; the
  page state (open folds, Show more, scroll) kept per week in localStorage and restored after `refreshSections`; ONE
  client pool store (`dfsPool`, painted on every `.seg` / `.poolval` and the Explorer) fed by each write's read-back;
  multi-select with a bulk bar, sent as `{"each": true, "changes": [...]}` to `/api/pool`
  (`week_writes.SheetEdits.set_pool_each`: one batch, per-cell conflict / missing, `week_actions.MAX_BULK`), writes
  queued (`dfsQueue`); decisions also move the pool (`week_actions.decide_pool`, `merge_pool`, `add_kind`,
  `drop_cash_only`; the pool half is kept under `pool:` keys in the `decisions` input so Undo reverses it); the chart
  viewer (`week_charts.viewer_data`, JSON `chart-data`); the game view (`week_top.game_data`, JSON `game-data`;
  its "Write as a Max exposure stack" button, `/api/stack`, left in Removal F7); the cash core's caps
  (`week_plays.CASH_CORE_CAP`, `cash_core`; 30%+ CashOwn = `Chalk core`, always shown); the chalk check's header and
  3 "close" rows (`week_page.CHALK_CHECK_HEADER`, `CLOSE_COUNT`); the consensus in DK's roster order
  (`consensus.dk_order`) with `week_money.CONSENSUS_CAPTION`; and `week_page.table(..., how=...)`: every table and
  chart carries a "how this is built" note. Tests: `tests/test_week_ui.py` (HTML / script, no browser) and
  `tests/test_week_ui_browser.py` (Playwright on a fake sheet; skipped where Chromium is not installed, as on CI).

## Run panel, pool chips, colour, captions

- **Plan O on the Week page** (O1): the **run panel** (`week_jobs.panel`: `WEEKLY`, the eight weekly buttons in the day's order, each
  form showing the options his shortcuts use (`Weekly.common`, short labels `SHORT_LABELS`, the help as a tooltip, `Weekly.defaults`
  filled in, the rest under "More options", a preview line of the exact command); every other command behind a search; `setup ...` /
  `fix-...` behind a collapsed Advanced toggle (`is_advanced`); recent runs say ok / failed / stopped, how long and when (the per-run
  output viewer, `/api/jobs/output`, left in Removal F7)). **A pool chip on every name** (drawn as the shell's pool square since the design pass; `week_ui` `decorate`:
  any `a.pl` the page draws, resolved to an id from the Explorer's data by name and team when a section holds only the name,
  `week_page.pn` / `pns`; a name not on this slate stays plain text; a row that already has the control gets none) and **one click to add**
  a player listed as missing from the pool (`week_page.add_button`, kind from `week_board.kind_of`). **Colour by position**
  (`week_shade.py`: a z-score within the position in every table that has one, the sheet's palette, ownership orange, the top / bottom 10%
  bold, a legend above each coloured table; directions from `week_compare.SECTIONS`). **Captions** (`week_page.caption` / `fill_asof`:
  every `table()` and chart shows how it is built, the source columns and the date of its data; `week_charts.FORMULA` / `SOURCES`).
  **The Explorer's chart follows the lens and the filters** (`week_explorer.LENS_CHARTS`, one per lens; the full-screen viewer opens the
  chart that is showing). The game view maps nflverse's team codes to DK's (`player_join.TEAM_ALIASES`: LA = LAR), has Previous / Next and a
  game list, and a pool control on every row; pan is every chart's default (wheel / pinch / box to zoom); a Pool click keeps the scroll
  of the page and of the box it is in, and the focus.

## Views, search and compare

- **Plan O2 on the Week page** (`week_views.py`): the views (`VIEWS`: the three day
  views and More's pills, see the shell above; the page opens on the clock's state, `body[data-state]`), each view its own `#name` (a link or the back
  button opens it; no element id may equal one, a test checks), the last one kept per week and state; every block is
  wrapped in `week_views.wrap(html, place)` naming its views (`PLACES`); the header shows on all of them, and the
  one pool store serves every view. **Overview** (a More view since the design pass, the old Saturday scan): one row per position (top value, cash core and chalk, the board's Upside, the fades:
  FADE↓ plus the plays model's AVOID / FADE (Shadow) on one line per name, the top injury fallout = the calls' teammate bump), the top
  3 games, the pool by position (the run panel is its own More view, Run a command). **Usage**: `data/current/usage_weeks.csv`
  and `team_weeks.csv` (new sheetless sources `usage_weeks` / `team_weeks`, `sources/usage_weeks.py`, math `usage_weeks.py`; a full
  `dfs sync` only, never `--live`): per game snap share, targets, target / air-yards share, WOPR, RZ / HVT, carries, DK points; per
  team-game plays, dropback rate, PROE, pace, red-zone trips; drawn by the client (window, trend metric with a last-5 sparkline against
  the season mean, two scatters per position with the outliers labelled, a stacked bar of each team's targets and carries).
  **Injuries**: per slate team, DK's OUT / D / Q / IR plus the injury report's designations, the practice trend, who benefits (the calls'
  `bump` / `bump_from`). **Model vs sources**: CalPts against TFFB / Sleeper / FantasyPros / ESPN / props (the median of 2+ sources),
  the biggest disagreements by position, every player behind a fold, and the plays calls' AVOID and FADE (the CORE / PLAY / TARGET / LEVERAGE block, "Who to play", left in
  Removal F8), each label's record
  kept apart: the M1 backtest (newest `docs/research/plays/*-plays-summary.json`, gitignored) and the live `calls_history.csv`
  (`week_inputs.calls_record`, `week_views.record_of`). **Search** (`#psearch`, `/` focuses it) and a **compare tray** of 2-4 players
  (`+ Compare` in the search, the drawer and every pool chip's popover; kept per week) with the compare rows and directions
  (`week_views.compare_spec` over `week_compare.SECTIONS`), usage trends and ownership. The served page fires `dfs-sections` after a
  refresh (`week_live`) so the client-drawn views redraw. The pool store numbers each read-back and a render adopts a cell only when no
  newer read-back exists (`week_ui` `stale` / `mark`; a refresh asked for during one runs once more after it), and a `+ Cash` is
  resolved against the last read-back when its write runs (`requestPool` change `{id, add}`), never at the click.
  **Team news** (`news_context.py`, pure; Sam, 2026-11-09: "JSN had a backup QB, CeeDee did not have his WR2"): facts, not a
  model, from the last five games and this week's statuses (`WeekInputs.news` / `team_news`): a pass catcher whose QB is a
  backup (the starter = who started the team's latest game), a pass catcher or lead back out (12%+ of the targets, or 50%+ of the
  snaps; only while he played in one of the team's last three games) and who leads the targets then. A chip after the name on the
  board, decisions, overview, plays calls and injury report (`week_views.NEWS_AT`, client side), the full line in the drawer, the
  compare tray and each injury card's header, and one brief sentence for the chalk and $7,000+ players (`week_board.news_sentence`).

## One player in full: the drawer

- **One player in full** (the sheet's Player and Compare tabs were removed in batch F9, 2026-11-17; `dfs doctor` flags
  either tab, or `PlayerCardRaw` / `PlayerCardRows`, as `retired-tab` until it is deleted by hand): the Week page's player drawer
  (every source's projection, ownership, the last five games game by game from `data/current/player_log.csv`, written by the
  sheetless `player_log` source (`sources/player_log.py`), this week's prop lines by market (`week_page.prop_lines`), the signals
  with their record), the compare tray (`week_compare.SECTIONS`), the Explorer's All columns lens, and EdgeRaw itself.

## Money on the page

- **The ledger** (blueprint Phase B; `ledger.py` pure, `week_money.ledger_section` / `ledger_line`, `h2h_log.py`): the Week
  page's first fold on Saturday and Monday. Sam's record by format, the thirteen ground-truth buckets (`ledger.BUCKETS`,
  ported line for line from the ground-truth pack's `gt_common.bucket`), grouped Cash (H2H, Double-up, 50/50) / Small GPP
  (SE GPP) / Fun (Showdown, 3-max/5-max, 20-max and up, N-max with no max in the name, paid Discord) / Free / Other (one
  grey line): rake, ROI 2022-26 with a +/-2 SE bar (`ledger.cluster_se`: whole season-weeks resampled, 2,000 draws, seed 0),
  ROI and n this season, Plan $ per group (Cash = tier A, Small GPP = tier B of `contest_plan.tier_plan`; Fun =
  `[ledger] fun_per_week`, "no number set" when unset) and Entered $ (`ledger.entered_by_group` over `data/entered`,
  `skipped_fees` included). One H2H line: counts against regulars (5+ meetings in the seasons shown) and everyone else,
  **never a name**. Saved as the input `ledger` by `dfs contests plan`, `dfs entries record` and `dfs week close` (fail-soft);
  the close also appends `data/contests/h2h_log.csv` (one row per H2H game, opponent names stay in data/, nothing prints
  them). **Nothing caps, warns or says "over"**: the contest plan's caps and "over in:" wording left the page and the close.
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
  is `entries.suggest_pick`, Untested, never "Best in"), and the **practice trend** beside every
  Q / D tag (board, Explorer, drawer; `practice.py` from `data/raw/nflverse_injuries/`). The Cockpit gains blocks 8
  (inactives and pivots) and 9 (practice) below the first seven, and a plain `dfs sync` refreshes it too.

## Sunday: the Lock state

- **The Lock state** (blueprint Phase C2; `lock_state.py` pure, `week_views.lock_section`, `week_top.strip_html`): the Week page knows
  the clock (`lock_state.state`: `lock` from 13 hours before the main slate's first kickoff until 3.5 hours after its last, `close`
  after or on a closed week, `build` otherwise) and opens on it (`close` opens the Close view, Phase D below); five **tiles** (time to lock, entered vs planned, open decisions, news pending, lineup health;
  drawn as a strip at the top until the design pass, now feeding the Sunday status line, `week_shell.sunday_facts`;
  `week_top.strip_html` is no longer drawn). The **Sunday view** (`#lock`): **what changed
  since the build his lineups came from** (`lock_state.baseline`: the newest suggest round the Lineups-tab lineups match, 8+ of 9,
  else the latest round, else Saturday 9 pm ET; Sam, 2026-11-17), from `data/news/changes.csv` netted per player and field
  (`net_changes`), one row per player (status > projection > ownership), only rows touching a lineup or the pool, a status row
  carrying the teammates who moved and touching their lineups; the rows **with a better swap** first (`best_swap` = the inactives
  check's pivots, the first gaining 0.5+ CalPts, on the lineups the move went against), sorted by points at stake, then a collapsed
  "No action needed". **Sticky**: any player in a lineup, the Default or the pool whose current status is OUT / IR / D / Q is listed
  whatever the time of the change ("a rebuild must never hide a live problem"). A backup's status change (projected under 5) and IR
  moves are counted, never rows. Before lock the lineups are **the Lineups tab read fresh** (the served page reads it on every render,
  `week_writes.LiveSheet.lineups`; the static page uses the last read the sync saved); recorded entries only after lock. **Never asks
  him to record entries.** A four-line **pre-lock checklist** (no inactive player; late-game players in FLEX, `flex_order.plan_flex`;
  every contest has an entry once entries exist; salary sanity), grey when fine, amber only when wrong, and the **late window** (each
  lineup's players in later games and what could still swap in). **Swap / Keep / Skip** on the served page (`/api/lock`,
  `week_server.lock_answer`) records the choice in `data/week/decisions.jsonl` (`decisions_log.py`, append-only, ids `<season>-<week>-NNNN`,
  undos are lines of their own) and says the swap to make on DraftKings: **nothing writes a sheet, nothing blocks or warns.** Late
  swap's runs are append-only too: `late_swap_store.save` also appends each run to `data/cockpit/late_swaps.jsonl` with its top three
  options per lineup.

## Tuesday: the Close view

- **The Close view** (blueprint Phase D; `close_view.py` pure, `week_learn.close_section`, `scorecard.scorecards`, `edge_at_lock.py`,
  `decisions_log.py`): the Week page's **Tuesday** view (`#close`; the clock's `close` state opens it), the closed week on one
  screen from the `close` input that `dfs week review` (run by `week close`) saves: the week line (`close_view.week_line`: his
  double-up points against the double-up cash lines, how many cashed, the share of the field beaten = median of `1 - pct` from
  `decisions.csv`, the net from the contest log), **what decided it** (the cash post-mortem's top five, tagged `you had him` / `a
  choice` / `variance`), **you against the build** (`scorecard.csv`'s overrides, week and season; "too few to call" under 12 weeks;
  "the Default" once Phase A saved a `cash_core`, "the nearest build" before), the **four scorecards** (Projection: CalPts vs TFFB
  MAE; Ownership: ours vs TFFB's Own% (GPP) and the double-up curve (cash), the weaker format's status; Calibration: Cash % shown
  vs cashed, `cash_shown.csv`; Edge at lock: the status from the REALIZED margin by week, his cash lineups' actual points minus
  the field's mean actual points, the projected edge and percentile its "on paper" text; Sam, 2026-11-17), each Proven / Not proven / Overstated / New (the rules in `docs/CALCULATIONS.md`),
  the ledger (Phase B's section, as `#close-ledger`), four tiles (edge at lock "on paper", the projected edge as an early warning, avoidable errors = blank entries, calibration gap,
  cash percentile by week), **the one thing** (`close_view.one_thing`: the first of blueprint 13.3's monthly triggers that fires,
  else "Nothing"; its edge trigger reads the projected edge and says "on paper") and the **decision log** fold (`decisions_log.read`, reason pills; on the served page one tap posts
  `/api/reason`, a new line in `data/week/decisions.jsonl`, never a sheet write). **Edge at lock**
  (`data/contests/edge_at_lock.csv`, one row per distinct lineup of his per contest with standings): his nine summed on the
  **four-source consensus** (TFFB `ProjPts`, `SleeperPts`, `FantasyProsPts`, `EspnPts`; props are not in it) against every lineup
  of the field, from the last EdgeRaw snapshot before the lock (`sim.retro.edge_snapshot_before`; a week whose newest snapshot is
  older than the previous lock is skipped, `NoSnapshot`). The served page's decisions (`/api/decide`: open spot, core Play / Fade,
  stack, pivot, wait; `week_actions.decision_record`) and the Sunday view's answers append to the log (fail-soft; an undo is a
  line of its own). `dfs week review --week N --only edge_at_lock,close` backfills a past week (files only). The research
  registry is `research/registry.toml` (`tests/test_registry.py`: every key, at most two `open`, no verdict on an open one).

## The learning loop

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
