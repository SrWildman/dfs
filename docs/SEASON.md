# Season-long leagues

`dfs season ...` runs Sam's season-long Sleeper leagues on their own Google Sheet, the **Season** sheet. It has
nothing to do with DraftKings or the weekly DFS sheet, and nothing is copied week to week. Phase 1 (this) covers
league sync, rest-of-season values, team value and needs, a trade analyzer and a trade finder. Code:
`src/dfs/season_long/`. Commands and options: `docs/COMMANDS.md` ("Reference: season-long leagues").

## Setup

1. `season.toml` beside `config.toml` (gitignored; its own file because `[season]` in config.toml is the DFS
   sheet's Season tab, and the weekly CLI rejects unknown config keys):

   ```toml
   sheet_id = "<the Season sheet's id>"

   [[leagues]]
   name = "Bourbon"            # what --league takes
   platform = "sleeper"        # espn / yahoo are listed for phase 2 and skipped
   league_id = "1397789176585322496"
   username = "srwildman"
   # playoff_week_start = 15   # only when Sleeper's settings say 0
   # faab_point_value = 1.5    # override the fitted points per FAAB dollar
   ```

   A top-level `buy_list = ["Chuba Hubbard", ...]` ADDS targeted ideas to the standard search: `dfs season
   sync` and a plain `dfs season trades` keep their usual best trades and add up to 10 per league that land a
   name from the list (on another roster in that league; one Sam owns there is skipped) or a player at his
   weakest spot there (FLEX counts as RB/WR/TE; K and DEF are streamed, so the next spot is used), each
   tagged `TARGET buy list: ...` or `TARGET weakest spot: ...`. List names match the full name only, never a
   last name alone. `--open` leaves the extra ideas out.

   Optional top-level numbers: `playoff_weight` (1.5), `starter_miss_rate` (0.06), `ir_weeks_out` (4),
   `survival_decay` (0.8), `survival_draws` (4000).

   An optional `[roster]` table sets the caps behind the roster rules (see "Roster rules" below):

   ```toml
   [roster]
   max_k = 1     # active kickers (default 1)
   max_def = 1   # active defenses (default 1)
   min_swap_gain = 5   # a same-position QB / K / DEF swap needs this many rest-of-season points (default 5)
   ```
2. Share the Season sheet with the service account as an Editor, then `dfs season setup` and `dfs season sync`.
   `dfs season doctor` checks the structure.

## The commands

| Command | What it does |
|---|---|
| `dfs season sync [--league X \| --all]` | Everything below for every league, written to the sheet |
| `dfs season trades --league X [--get ..] [--give ..] [--keep ..] [--partner ..] [--shapes ..] [--max-consensus-loss ..]` | The finder; with no filter it reads the Trade Finder tab's targeting box |
| `dfs season trade "Give A, B + $10 FAAB" "Get C" --league X` | The analyzer; writes the Analyzer tab |
| `dfs season waivers [--league X] [--player "Name"]` | Best pickups for your team with a FAAB bid guide, handcuffs, a chopped league's bid plan; writes the Waivers tab |
| `dfs season lineups [--league X]` | This week's best lineup per league: ranges, close calls decided by the matchup, K/DEF streams; writes My Teams (all leagues only) |
| `dfs season check` | The Sunday check across every league (read-only) |
| `dfs season trades --all --give "X, Y, Z"` | Sell any of these: a league where you own none of them is skipped (and says which names it skipped); `--get` works the same way for buys |
| `dfs season trades --all --players "A, B, C"` | Trades that move any of these, either way: per league a name you own is a sell, one on another roster a buy |
| `dfs season trades ... --lopsided` | Also show ideas the market has over 125% for the partner, each with the FAAB to ask back |
| `dfs season ros --league X [--check]` | Rest-of-season points, or the accuracy check |
| `dfs season outlook [--league X \| --all] [--check]` | Playoff odds (byes, divisions), chopped survival curves, power rankings with their move, strength of schedule; writes the Outlook tab. `--check` scores the simulation from earlier weeks and back-fills the odds history |
| `dfs season setup`, `dfs season doctor` | Build and check the sheet |

## Method

### Data (all public, no login)

- **Sleeper's API** (`sleeper_league.py`): league settings (scoring, roster slots, playoffs, trade deadline,
  FAAB budget, league type), rosters, users, matchups (a future week's are the schedule), transactions (FAAB
  bids), weekly stat lines and weekly stat projections for every remaining week. Cached under
  `data/season/sleeper/`: `/players/nfl` at most daily (Sleeper's ask), a finished week forever, and live calls
  spaced 0.25 s apart.
- **FantasyPros' rest-of-season consensus rankings** (`consensus.py`), the public page (robots.txt allows it;
  fetched at most daily per scoring format, honouring its 5 s crawl delay): the market value other managers see.
- **FantasyPros and ESPN weekly projections**, already archived by the weekly DFS sync, for the coming week.

### Rest-of-season points (`ros.py`)

- Every stat line is scored with the league's own `scoring_settings` (Sleeper's stat keys and scoring keys are
  one vocabulary; kicker distance buckets fall back where a projection is coarser). Checked: the rescoring
  reproduces Sleeper's own PPR / half / standard totals exactly on real Week 7 lines.
- A player's week t = `a * season rate + (1 - a) * projection_t`, `a = n / (n + k)` after n games. k is
  fitted on the season's finished weeks.
- Byes: 0, from the weeks Sleeper projects his team an opponent.
- Out: 0 this week. Doubtful: x0.25 this week. IR / PUP / NFI / suspended: 0 for `ir_weeks_out` weeks.
- A player Sleeper projects in no remaining week is 0, so a backup's one big game is not a rate.
- The coming week only: adjusted by the FantasyPros/ESPN consensus factor on the stats all sources project.
- CalPts is not used: its corrections are by DraftKings salary tier, on DraftKings points.

**Accuracy** (held out, 2026 Weeks 1-7, each cutoff week's blend fitted on the other cutoffs; per-game error
over the rest of the finished weeks, players with 6+ points a game):

| | Bourbon (PPR) | Agile (half) |
|---|---|---|
| Blend | 3.21 | 3.02 |
| Sleeper projection alone | 3.20 | 3.01 |
| Season average | 4.94 | 4.84 |
| Last-3 average | 4.99 | 4.90 |

The forward projection does almost all of the work (the fitted k is about 50: after 7 games the season rate
gets 12%). Sleeper's past-week projections were checked against the pre-kickoff copies the weekly sync
archived (receiving yards correlation 0.995+), so this is not hindsight. The FantasyPros/ESPN factor helps a
little (3.47 to 3.42 in Bourbon, in-sample).

### Team value (`team_value.py`, `objective.py`)

- **Lineup**: each remaining week, the roster's best legal lineup (exact assignment over the league's slots,
  FLEX / SUPER_FLEX / WRRB / REC flexes included). Bench depth counts only as cover: each starter misses a week
  with chance `starter_miss_rate` and the expected loss comes off.
- **Redraft**: `regular-season points + playoff_weight x P(make the playoffs) x playoff-week points`.
  P(make the playoffs) simulates the rest of the regular season on Sleeper's schedule, with the extra game
  against the weekly median where the league plays one. Teams go in on wins, then points for; divisions are
  modelled (see "Playoff odds" below).
  - A team that is safely in weighs its playoff weeks fully.
  - A team still fighting for a spot gets most of a trade's value from what it does to its odds (Sam: "we
    need to get to the playoffs first").
- **Chopped (last man standing)**: the survival simulation. Each week the lowest score is eliminated and its
  best three players go to waivers. Survivors win them in proportion to their FAAB, at the prices this
  league's own winning bids set. A roster's value is its chance of being alive after each week, week i ahead
  weighted `survival_decay ** i`.
  - Also reported: expected weeks survived, P(survive this week) and P(last man standing).
  - No playoff weighting. Floor counts more than ceiling, because only the bottom score matters.
  - A team near the cut lives or dies this week, so a desperate partner values now over later by construction.
- **Spread check**: real Sleeper team scores, Weeks 1-7, every league, against the model's spread of the actual
  starters' projections. The miss runs 0.97x the modelled spread (Choppa 1.06x, within the noise of 105
  team-weeks), so there is no correlation term. Choppa's real chops scored a log loss of 2.33 against 2.70 for
  a uniform guess: real but noisy. `calibration.py` now measures it for every sync (below) and the simulations
  multiply their team spread by it.
- **Needs**: per slot type, points per week from that slot in the team's best lineups, against the league
  median.
- **Free moves**: the best single free-agent swap a team can make with no trade.
  - It is the baseline every trade is measured from, so a trade is never credited with a pickup anyone could
    make.
  - It is also shown on its own ("free move first").

### FAAB

- **Redraft** (`faab.py`): points per dollar = what this season's winning bids bought (each player's lineup
  value now to the roster that won him) over the dollars bid. It shrinks as the weeks run out.
  `faab_point_value` overrides it.
- **Chopped**: simulated, as above (FAAB buys the chopped rosters).
- Either side of a trade can carry FAAB. The finder adds the smallest sweetener that makes a trade fair to the
  partner, or names the FAAB Sam could ask back. It never uses more than a team has.

### The analyzer and the finder (`trade.py`, `finder.py`)

- **Rosters stay legal.** A team over its roster size drops the player it misses least; a team short adds the
  free agent who helps most.
- **Our model**: each side's change in team value.
- **The market**: FantasyPros ROS consensus rank mapped onto our own value curve over the waiver level, so the
  two disagree only on who is ranked where. FAAB counts at the league's points per dollar.
- **Fair** means the partner gets at least `1 - max_consensus_loss` (5%) of what they give.
- **Buy-low / sell-high**: our value and the market's differ by 20% and 10+ points.
- **The finder** searches every other team in 1-1, 2-1, 1-2 and 2-2 shapes among each roster's top 7.
  - A quick lineup screen comes first, then a full analysis of each partner's best 8.
  - It keeps trades where Sam gains, the partner gains by our model, and the market calls it fair for them.
  - Ideas are ranked by Sam's gain.

### Roster rules (`roster_rules.py`, `roster_moves.py`, `trade.settle_full`)

The evaluators add up rest-of-season lineup points, so a second QB, K or DEF "beats" a bench RB: he covers
every future bye and injury. That is right arithmetic and wrong roster management (Sam, 2026-11-04: "I would
never drop a position player for a backup QB except on a bye week. K and DST, I never need more than 1."). So
the rules sit under every place an add or a drop is chosen: the waivers, the chopped bid plan, the trade
analyzer and finder (`trade.settle`), the "free move first", and the Sunday check.

1. **Caps.** Active K at most `[roster] max_k`, active DEF at most `max_def` (1 each). Active QB at most the
   number of slots that can start a QB, read from the league each time: 1 in a 1-QB league, 2 with a
   SUPER_FLEX (so a superflex league still values a QB2). A league that starts more K / DEF than the cap
   raises it to its slot count. Players in IR slots (Sleeper's `reserve` list) count toward no cap.
2. **An add at a capped position is a swap there.** It sends out the incumbent (to an open IR slot if the
   league lets him go), never an RB, WR or TE; the gain shown is the replacement over the incumbent. With
   nobody active at the position (Choppa has no QB) the add fills the empty slot and the drop is the player
   missed least.
3. **Bye and injury cover counts only the coming scoring week.** A second QB / K / DEF is suggested only
   when every active player there is on bye or OUT / IR / Doubtful that week. It is then a *bye-week
   stream*: valued on that week alone (`Evaluator.scoped`), a position player may be dropped for him, and
   the Waivers note says "bye-week stream: drop him after Week N". The next run suggests the drop again.
4. **IR slots before any drop.** A player the league lets go to IR on the active roster, with a slot open, is
   "move X to IR": it frees a spot with nobody dropped, and shows as its own line at the top of the league's
   list (Waivers tab, `dfs season waivers`, the Sunday check). A reserve player whose tag has cleared is
   flagged "IR slot holds a healthy player: activate him". Who is eligible comes from each league's
   `reserve_allow_*` settings: IR, PUP and NFI always; Out, Doubtful, Sus, COV, DNR where the league allows.
5. **Never a drop from an IR slot, and never a drop that leaves a starting slot empty.**
6. **A same-position swap has to be worth it.** A QB / K / DEF swapped for the incumbent is suggested only when
   its rest-of-season lineup gain is at least `[roster] min_swap_gain` (default 5 points), in the waivers and
   in the "free move first". An empty-slot fill and a bye-week stream are exempt, and a player asked about by
   name (`--player`) is always reported.

A roster that already breaks a cap (two defenses, say) is not charged for it: the caps bite on what an add or
a trade brings in.

### Waivers (`waivers.py`)

- **Value.** Each of the best 40 free agents (plus each position's best, so a K or DEF is never missed) is
  added to Sam's roster under the roster rules above: the player he misses least goes, an IR move comes
  before a drop, and a QB / K / DEF add is a swap (or a bye-week stream). The full objective decides, with
  this week's gain shown apart. At most two per position make the top list.
- **Bid guide.** The league's own completed paid claims of the same position, for players Sleeper projected
  (for the claim's week, in this league's scoring) within 3 points a week of this one.
  - The range runs from the median to the 80th percentile of those winning bids.
  - Shown beside it: what the player is worth to Sam (his gain over the league's points per dollar), and the
    rivals with enough FAAB who need the position. Inactive managers are left out of the rivals.
- **Handcuffs.** A free-agent RB2 is valued for the weeks his RB1 misses. What he adds then depends on how
  much of the backfield the RB1 has: his share of his team's carries over the last three games (Sleeper).
  Measured on the DFS side's R7 events (2014-2025, DraftKings points over the projection; fit 2014-21, test
  2022-25):
  - RB1 under 55% of the carries (a committee): +4.3 (fit +3.7, test +5.6);
  - RB1 at 55% or more (a lead back): +7.1 (fit +7.0, test +8.0).
  His RB1's chance of missing a week is `starter_miss_rate`, or 25% this week if Questionable, 75% if
  Doubtful, 100% if Out. The stash list shows, hurt starters first:
  - every backup whose RB1 is tagged right now;
  - every backup to a lead back;
  - any other backup with lineup value.
  Each comes with what he would score if the starter sits.
- **Chopped bid plan.** For the team most likely chopped this week: its best three players, what each adds to
  Sam's survival, that in dollars, and what the league has paid for a chopped roster's first, second and third
  pick. The bid runs from the league's price up to his worth, capped at Sam's FAAB.

### Lineups (`lineups.py`)

- **The lineup** is the best legal lineup on this week's points. Each starter shows floor and ceiling, the
  10th and 90th percentiles of his weekly spread.
- **Close calls** are bench players within 1.5 points of a starter they could replace. Each is decided by
  what the league rewards this week, computed for both versions:
  - head-to-head: P(beat this week's opponent's best lineup);
  - median-game leagues: expected wins, that P plus P(above the week's median), so the total counts twice;
  - chopped: P(not the lowest score).
  An underdog's answer is the higher ceiling and a favourite's the higher floor, with no hand-set threshold.
- **K/DEF streams.** The best free agents for each of the next three weeks, by Sleeper's matchup-aware weekly
  projections in the league's scoring.
- **The Sunday check** (`dfs season check`, read-only). Sam's lineup as set in Sleeper, slot by slot, with
  ESPN's game-day injury feed (the one DFS late swap reads) laid over Sleeper's status:
  - `SWAP` (with the best bench or free-agent replacement): an empty slot, a bye, or OUT / IR / suspended /
    Doubtful;
  - `LOOK`: Questionable, or a bench player projected 2+ points more than the starter he could replace.
  Nothing is ever written to Sleeper.

### More edges (`activity.py`, `consensus_check.py`)

- **Playoff schedule.** Each player's opponents in the league's playoff weeks, and his schedule ease: playoff
  per-game projection over rest-of-season per-game (above 1, an easier run-in). On the Players tab.
- **Manager activity**, from transactions and past matchups: moves, trades and the positions traded, the last
  week of either, and lineup misses over the last 3 weeks (an empty slot, or a starter whose team did not
  play).
  - `inactive`: no move in 3 weeks plus a recent lineup miss. The finder's why line says so; the bid guide
    drops them as rivals.
  - `trader`: 2+ trades.
  On the League tab.
- **Ours vs the market, held out.** Every season command keeps a dated copy of FantasyPros' rest-of-season
  ranks and of ours. Each finished week since then scores both: Spearman rank correlation with the actual
  points per game, by position, over the players either ranks in its top 24 QB / 40 RB / 50 WR / 20 TE. On
  Overview.
  - There is no backfill: the Internet Archive holds only off-season copies.
  - The series needs one season command a week before games.
- **Lopsided trades.** Ideas the market has above 125% for the partner are hidden unless asked for
  (`--lopsided`, or "Show lopsided: yes" in the box), and always carry the FAAB to ask back.

## Playoff odds, power rankings and schedule (`outlook.py`, `calibration.py`, `outlook_run.py`)

`dfs season outlook` (and every `dfs season sync`) writes the **Outlook** tab: every league, every team, by power
rank. The odds are the evaluator's own (`Evaluator.base()`), so the Outlook tab, Overview and the trade finder never
disagree.

### Playoff odds

- **Redraft.** Every remaining regular-season game is played 4000 times. A team's week is a normal draw around its
  best lineup's expected points (the cover term included), with the lineup's spread times the **spread scale**.
  Head-to-head games go to the higher score; where the league plays the median, every team above the week's median
  score also wins a second game (Sleeper counts it in the record). Final standings sort by wins, then points for.
  The top `playoff_teams` make it and the top `bye` seeds skip round one (a bracket rounds up to a power of two: 6
  teams, 2 byes).
- **Divisions** (Agile has three). Odds of a division title are always shown. With Sleeper's `playoff_seed_type` 1
  (Agile's) division leaders are seeded first, ahead of the wildcards, the NFL way. That is our reading of the
  setting, not something Sleeper documents: if Agile's real seeding is by record alone, set it to 0 in Sleeper and the
  Outlook follows (bye odds are what it changes).
- **Chopped.** The survival simulation, with a curve for every team: P(alive after each remaining week), P(survive
  this week), expected weeks and P(last one alive).
- **Spread scale** (`calibration.py`). For every team-week so far: the real score against the summed Sleeper projection
  of the starters actually set, in units of the model's spread for that lineup. The scale is the standard deviation of
  that over every league's team-weeks: **0.97x** over 357 team-weeks, Weeks 1-7 (Agile 1.02, Bourbon 0.96, Choppa 1.06,
  Mega 0.81: Mega's 84 team-weeks are about 2.5 standard errors under 1, so Mega's odds may run slightly too
  uncertain). Real scores ran 99% of the starters' projections. Pooled, clipped to 0.7-1.5, refitted each scored week,
  cached in `data/season/outlook/`.

### Held out: the simulation from earlier weeks against what happened

`dfs season outlook --check`. Each cutoff week c (2 to 6) is rebuilt from the league's own matchups (`outlook_check.py`):
wins and points for through c, each roster as set for week c + 1, injuries blank, the blend, spread scale and
`Evaluator` the production ones. The predictions are scored on the weeks after c, up to the last scored week
(Week 7). Brier score, lower is better; **ours** against the naive models. `record` is a rating from the record alone
(no scores, no rosters); `scoring` is each team's average real score so far, shrunk toward the league average by how
much of the spread between teams is real (empirical Bayes, fitted on weeks 1..c), no rosters or projections.

| What is predicted | ours | scoring only | record only | constant | n |
|---|---|---|---|---|---|
| a game's winner | **0.233** | 0.250 | 0.268 | 0.250 (a coin) | 270 |
| in the playoff places after Week 7 | **0.127** | 0.141 | 0.143 | 0.250 (base rate) | 180 |
| in a bye place after Week 7 | **0.050** | 0.066 | 0.072 | 0.139 (base rate) | 180 |
| still alive after each next week (Choppa) | **0.116** | 0.133 | - | 0.128 (an even chop) | 220 |

Paired over the 15 league-cutoffs (5 in Choppa), ours minus the naive model, with the standard error (optimistic: the
cutoffs overlap in time):

- vs record only: games -0.029 (se 0.009), playoff places -0.016 (0.009), byes -0.022 (0.009);
- vs scoring only: games -0.017 (0.006), playoff places -0.014 (0.008), byes -0.016 (0.006), Choppa survival -0.010 (0.009);
- vs a coin / the base rate: games -0.014 (0.007), playoff places -0.123 (0.016), byes -0.089 (0.011), Choppa survival
  vs an even chop -0.008 (0.006).

Read it as: the lineup-based simulation beats both naive models on every event and clearly (2+ standard errors) on
games and byes; on playoff places and Choppa survival it is ahead by 1-2 standard errors, so those two are
supported, not proven. A game is mostly luck (a coin scores 0.250, ours 0.233), so no one should expect more. The
record-only model scores worse than a coin on games because a 3-1 start says little. The spread scale makes no
measurable difference at this sample (0.2328 either way). The horizon is 1-5 weeks (the weeks played so far), not
the playoffs themselves; nothing here tests the odds of a seed in Week 15. One caveat: Sleeper's projections for
weeks after c are its last ones before those games, a little fresher than a forecast made at c would have had.
The scoring-only survival model has no waiver pickups from the chopped rosters; ours does.

### Power rankings

Rank by team value rest of season (the same Team value rank as Overview). **Move** = places gained since the
previous scored week's snapshot (`history.py`, `data/season/history/<league>.json`, written by every sync or
`outlook`). The earlier weeks were rebuilt from the leagues' own matchup history by `outlook --check` (tagged
`backfill`; a real sync's snapshot is never replaced by one), so the odds history starts at Week 2.

### Strength of schedule

- **NFL ease, per player and per team** (`outlook.defense_index`). How soft a defense is for a position, taken from
  the weekly projections themselves: each player's projection against that defense over his own average projection,
  averaged over the players who face it (weighted by his average, shrunk toward 1). A player's ease is the mean
  index of his opponents over the rest of the season and over the playoff weeks (Players tab: `DefIndexROS`,
  `DefIndexPlayoffs`); a team's is its players' ease weighted by their points. 1.00 is average, above 1 is easier.
- **Opp strength, per team** (redraft): the head-to-head opponents still to face, their average expected weekly points
  over the league's average team (points per game; positive = harder). Not defined for the playoff weeks, whose
  opponents depend on the bracket.
- **How much to trust it.** The index runs only 0.93-1.08 between offense-facing defenses (DEF 0.89-1.14) and a team's
  average lands in 0.98-1.02, so this is a tiebreaker, not a lever. Held out (Bourbon's scoring, 32 defenses,
  correlation of the index with what defenses really allowed per position; concurrent / index from projections of
  Weeks 1-4 against real Weeks 5-7 / for comparison, real Weeks 1-4 against real Weeks 5-7):
  QB 0.54 / 0.39 / 0.39, RB 0.37 / 0.28 / -0.13, WR 0.15 / 0.03 / -0.04, TE 0.27 / 0.35 / 0.29. Sleeper's projected
  matchups carry real information for QB, RB and TE (more than last month's points allowed, for RB), and almost none
  for WR.

### Overview

Below the league rows: **This week across leagues** (`across.py`) lists every lineup alert, pickup and trade idea in
priority order: `SWAP` alerts first, then `LOOK` alerts (and, in a chopped league, a 25%+ chance of being chopped),
then pickups and trades by size. Leagues count in different units, so size is the change over the spread of the
league's teams (the standard deviation of team value there; expected weeks survived when chopped). **Odds history** is
one row per league: his playoff odds (Choppa: his chance of being the last one alive) after each scored week, with a
sparkline.

## The Season sheet

One sheet for every league. Leagues are rows and pickers, never extra tabs.

| Tab | What it shows |
|---|---|
| Outlook | Every league's teams by power rank with the week-over-week move: playoff / bye / division odds, expected wins, opponent strength and NFL ease; a chopped league's survival curve for every team |
| Overview | The weekly report, one row per league: record, standing, value rank, odds, this week (win / median / survive), lineup alerts, biggest need, free move, best pickup, best idea, ours vs market, notes (a league with a median game says weekly points count double there). Below: this week across leagues (every alert, pickup and trade idea in priority order), the odds history (a line per league), then Sam's exposure, each player he rosters and in how many leagues |
| My Teams | One block per league: this week's recommended lineup (floor-ceiling, close calls), the check's alerts, K/DEF streams, the bench; values and the market's; needs |
| Waivers | Every league's best pickups with bid guides, then each league's handcuffs and a chopped league's bid plan |
| Trade Finder | The targeting box (`B2:B9`: League, Get, Give, Keep, Partner, Shapes, Max consensus loss, Show lopsided); every league's ideas in one list (League column, a filter view per league) |
| League | Picker in `B1`: every team's value, odds, strength by slot and manager activity |
| Players | Picker in `B1`: every player's rest-of-season points in that league's scoring, ours vs the market's, playoff-week opponents and schedule ease |
| Analyzer | The last trade analyzed |
| `SeasonLeaguesRaw`, `SeasonTeamsRaw`, `SeasonPlayersRaw` | Hidden; rewritten whole by every sync |

Every visible tab warns on an edit except the pickers and the box. The League and Players formulas find their
columns by name (`season_tabs.TEAM_COLUMNS` / `PLAYER_COLUMNS`). `dfs season doctor` checks the tabs, the raw
headers, the formulas, the pickers and the box.
