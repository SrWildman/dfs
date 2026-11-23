# Sam's shortcuts

The phrases Sam uses to run his week from a local Claude session (a terminal, or the chat on the Week page). `CLAUDE.md`
points here. When he says one of them, run exactly what the middle column lists, in order, then report what the last
column says. Anything not in these tables is a normal request: `docs/COMMANDS.md` has every command and option.

Code reads this file, so keep the table rows in the `| "phrase" | run | report |` shape:
`week_tools.parse_phrases` builds the Week page's tools list from every row, and the page's day plan
(`week_today.PLANS`) may only use a phrase that is here (`tests/test_week_page.py`). Every command in the weekly
table must be named on the sheet's Instructions tab (`tests/test_sheet_instructions.py`).

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
| "build me lineups" | `dfs lineups suggest` (his pool must not be empty: if it is, say so and stop) | the **Default cash lineup** (its points, its band in words with the cash record, e.g. "lean: cash builds have cashed 14 of 25", never a percentage), its core and each open spot with its 2-3 candidates as differences; each GPP build in one line (shape, salary, Top 1% (GPP builds rank by Top 1%; `--rank share` ranks by Top 1% share, only if Sam asks), and for a GPP build its ownership sum and product and expected duplicates: the numbers the terminal prints; say so if a cash build carries a duplicate warning), the cash chalk check (any CashOwn 30%+ player in none of the cash builds), and that **Lineup Tools > Copy builds to Lineups** (type `1,4,6` or `all`) puts them in |
| "contest plan" | `dfs contests plan` | the plan by format and by tier (A / B / C dollars from this week's budget and what to look for), what last week spent (by format and by tier, planned against spent; no "over"), and the H2H opponents to avoid |
| "record my entries" | `dfs entries record` (he downloads DK's entries file again after entering, and again after late swap; never fetch it; if he says whether his H2Hs were posted or joined, add `--h2h posted\|joined\|both`: a chat run is not a terminal, so it never asks) | how many lineups, which came from a Suggested build and which are his own, the contests each went into; say if it fell back to the Lineups tab; on a re-record, what changed lineup by lineup |
| "keep build 2" | set build 2's decision cell to `Keep` on the Suggested tab (a write to the live week's sheet: say the sheet), then `dfs lineups suggest --iterate` | the round produced and what changed (each build's note) |
| "lock Stafford and Nacua in build 4, swap the bring-back for McBride, rerun" | tick the locks and type the name over the bring-back cell on Suggested (the sheet and the chat must never disagree), then `dfs lineups suggest --iterate` | the round, build 4's note ("bring-back → McBride (yours); ...; Cash % 61% → 64%") or why the edit was refused |
| "give me 3 more like build 5" | set build 5 to `Vary 3`, then `dfs lineups suggest --iterate` | the round and the three variations (what differs, their points / Top 1%) |
| "Nacua for FLEX", "fade Chase in cash" (an open spot or a core player) | write `cash: Nacua` on Always use (or `cash: Chase` on Never use) on Suggested's rules box (the live week's sheet: say the sheet), then `dfs lineups suggest --iterate --only cash` (the served Week page's cash board does both in one click) | the new Default, its band and what changed in its open spots |
| "never Kyren", "always Bijan", "no bring-back for LAR" | write the rules box lines (`Never use`, `Always use`, `Stack rules`), then `dfs lineups suggest --iterate` | the round, which builds changed, and any rules line it could not read |
| "bump Nacua 2", "Kyren -15%", "Stafford to 24" | write the `Adjust` rules line (`Nacua +2`, `Kyren -15%`, `Stafford 24`; a percent needs its sign), then `dfs lineups suggest --iterate` | the round, which builds hold him (each note says "Nacua +2 (yours)") and how their Cash % / Top 1% moved; say that Model Check still scores the raw CalPts |
| "Kyren 40% owned" | write the `Ownership` rules line (`Kyren 40%`), then `dfs lineups suggest --iterate` | the round and what it moved (builds, dups, chalk watch); if Own% has not published yet, say the line waits for it |
| "min unique 2", "no more than 5 from one game" | write the `Build rules` line (`min unique 2`, `max from one game 5`), then `dfs lineups suggest --iterate` | the round and whether every new build honoured it (a build that cannot is reported, never relaxed) |
| "go back to round 2" | `dfs lineups suggest --round 2` | the round it was written as (a new round) and the builds |
| "fix flex" | nothing to run: tell him to use **Lineup Tools > Fix FLEX order** on Lineups (a sheet script); `dfs export` also puts the FLEX right in the file it writes | which of the two did it |
| "late swap" | `dfs lineups late-swap --refresh-injuries` (it covers every built lineup; the flag lays ESPN's game-day injury feed over the last sync's status first). **Live by default:** once a game has started it reads every started player's points so far from ESPN's public box scores and scores swaps on that state; if the output shows a red `LIVE SCORES UNAVAILABLE` line it ran on projections only: say so, prominently. Late swap never syncs (it reads `data/current/edge.csv` and ESPN). **If Sam says he downloaded a live standings export** (a Double Up and/or a GPP, from DraftKings by hand after the 1 pm lock), add `--field auto` (newest live exports in Downloads, this week's; say which file(s) it named, and "field: real" or "field: projected" as the output does; a file path works too). Never fetch the export yourself | a summary per lineup: who is still swappable (and any heavy chalk in none of his lineups, the yellow warning at the end), the best change and its Cash % / GPP % effect, or "nothing beats it"; for a GPP lineup on a live state (the live default once games are on, or `--field`) its position ("top 12%, 18 pts behind the top-1% line"; "about" top X% without an export) and each swap headed LEVERAGE (lowers his overlap with the field: the players in are less owned than the ones out), HOLD (raises it) or EVEN, with the in/out ownership (`late_swap_position.py`; the Cockpit's swap block shows the same label) (the run also refreshes the Cockpit tab where the sheet has one); with `--field`, which contest's field the lines came from and when it was exported |
| "late swap with live scores" | the same as "late swap" (live is the default now). "late swap on projections only" adds `--state projected` | the same summary as "late swap", plus the "field: projected + live scores (ESPN, read <time>)" line the output prints |
| "close the week" | `dfs week close --research` (it finds the newest history export in Downloads by its header and says which; pass `--csv <file>` if he names one). The close runs first, as always; then the offline studies run unattended (under 45 minutes, about as long as the slowest one: run it detached with a log, `caffeinate -i`, and read the log; a stage that fails or times out is reported, never retried). If the close itself fails, show the error and stop | the Model Check verdict and the learned block, the bankroll lines it reconciled, and what it kept and filled on the contest side (standings moved, empty downloads, Cash Line, the Contests tab), then the Contests tab's THIS WEEK, YOUR DECISIONS block (five lines), the contest plan line (spent vs plan, by format), the cash post-mortem's headline (how many cash builds would have cashed, the top separators and their tags, his cash lineup against its nearest build), and any "no record of what was entered" warning; then the 15-line research summary it prints (paste it to planning as is) and the path of the one report, `docs/research/weekly/<season>-wk<NN>-close.md` |
| "show me the week", "what now" | `dfs week page` (builds the page and opens it in the browser; it only reads what the tool already saved: no sheet, no network) | today's steps from the page (the phrase and command for each), what it says is stale, and every section that says "not run yet" with what to say to fill it |
| "open the week" | `dfs week serve` (opens the Week page as a local app with the chat beside it, folded until he presses Ask Claude; leave it running; Ctrl-C stops it) | the link it printed, and say that the chat, buttons and drops live on that page |
| "drop my entries" | nothing to run: tell him to drop DK's entries file (downloaded again after entering) on the Week page's drop zone, then press **Record these entries** (it runs `dfs entries record --csv <that file>`) | how many lineups and contests were recognised, the spend by tier against the plan (and what is over, while he can still withdraw), and any warning that the file is for another week |
| "how did we do" | read (do not write) Model Check: the opening verdict sentences and "What the model learned this week" | five lines: who leads the projection race by position, what CalPts learned, how the signals are running, whether the probabilities hold, the Week 8 checkpoint |

## Season-long shortcuts

These run on the Season sheet, never the weekly one (`dfs season ...` prints its title and URL before any write).

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

## A second slate (Thanksgiving, Saturdays, Christmas)

A week with a second Classic slate (`docs/WORKFLOW.md`, "A second slate in one week"). Thanksgiving is `thu`; a
Saturday slate is `sat` and Christmas `fri`: the same rows with that key. Always say **which sheet** each command
used: the slate's own, never the main week's.

| Sam says | Run, in order | Report back |
|---|---|---|
| "start thanksgiving" | **ask Sam for the copy's URL** (a copy of the template named "Week N Thanksgiving", shared with the service account) if he has not given it, then `dfs week new --slate thu "<url>"`, then `dfs doctor --slate thu` | the slate sheet's title and URL, that config.toml's main `sheet_id` is unchanged, doctor findings |
| "sync thanksgiving" | `dfs sync --slate thu`, then `dfs doctor --slate thu` | each source's status, the games in the draft group it picked |
| "final sync thanksgiving" | `dfs sync --live --slate thu` | what changed since the last sync, anyone in his Thanksgiving pool now OUT or Q |
| "build thanksgiving lineups" | `dfs lineups suggest --slate thu` (his Thanksgiving pool must not be empty: if it is, say so and stop) | the same as "build me lineups", for the Thanksgiving sheet |
| "record my thanksgiving entries" | `dfs entries record --slate thu` (he downloads the Thanksgiving entries file; never fetch it) | how many lineups and the contests each went into |
| "thanksgiving late swap" | `dfs lineups late-swap --refresh-injuries --slate thu` | the same as "late swap", for the Thanksgiving lineups |
| "open thanksgiving" | `dfs week serve --slate thu` | the link it opened (a second page beside the main one) |

## Around the tables

- **Iterating on Suggested:** `dfs lineups suggest --iterate` reads the tab (decisions, locks, typed names, rules box) and `data/suggest_state/<week>.json`; always say **which round** it produced and what changed. A plain `dfs lineups suggest` builds from scratch and replaces the tab (the previous round stays in the file: `--round N`). Edits made through chat go in the same cells, only on the live week's sheet.
- Edits Sam asks for in chat go in the same cells he would type in, on the live week's sheet only. Say **which sheet**
  every write used, and **which round** every `--iterate` produced.
- A command that fails: show the error and stop; do not retry in a loop or work around it. Google calls hang now and
  then: run a long job detached with a log file under `caffeinate -i`, and read the log.
