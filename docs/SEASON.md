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

## The Season sheet

One sheet for every league. Leagues are rows and pickers, never extra tabs.

| Tab | What it shows |
|---|---|
| Overview | One row per league: record, standing, value rank, odds, biggest need, free move, best idea, notes (a league with a median game says weekly points count double there). Below: Sam's exposure, each player he rosters and in how many leagues |
| My Teams | One block per league: roster with this week's slot, values and the market's; needs |
| Trade Finder | The targeting box (`B2:B8`: League, Get, Give, Keep, Partner, Shapes, Max consensus loss); every league's ideas in one list (League column, a filter view per league) |
| League | Picker in `B1`: every team's value, odds and strength by slot |
| Players | Picker in `B1`: every player's rest-of-season points in that league's scoring, ours vs the market's |
| Analyzer | The last trade analyzed |
| `SeasonLeaguesRaw`, `SeasonTeamsRaw`, `SeasonPlayersRaw` | Hidden; rewritten whole by every sync |

Every visible tab warns on an edit except the pickers and the box. The League and Players formulas find their
columns by name (`season_tabs.TEAM_COLUMNS` / `PLAYER_COLUMNS`). `dfs season doctor` checks the tabs, the raw
headers, the formulas, the pickers and the box.
