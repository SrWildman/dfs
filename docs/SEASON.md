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
  not modelled.
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
  a uniform guess: real but noisy.
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

### Waivers (`waivers.py`)

- **Value.** Each of the best 40 free agents (plus each position's best, so a K or DEF is never missed) is
  added to Sam's roster in place of the player he misses least: never a needed bye or injury cover, because
  dropping one would cost lineup points. The full objective decides, with this week's gain shown apart. At
  most two per position make the top list.
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

## The Season sheet

One sheet for every league. Leagues are rows and pickers, never extra tabs.

| Tab | What it shows |
|---|---|
| Overview | The weekly report, one row per league: record, standing, value rank, odds, this week (win / median / survive), lineup alerts, biggest need, free move, best pickup, best idea, ours vs market, notes (a league with a median game says weekly points count double there). Below: Sam's exposure, each player he rosters and in how many leagues |
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
