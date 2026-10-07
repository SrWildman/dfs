# The UM player-outcome model

`dfs model ...` builds, trains and back-tests a player-outcome model on **public nflverse and ffverse history
only** -- no sheet, no credentials, no TFFB. It ships small trained artifacts under `models/um/` and is meant
to be wired into the sheet by a separate, local step. Nothing in `src/dfs/model/` writes to a sheet.

**What it is for, in order of value:**

1. **A distribution engine.** It turns *any* projection into Floor, Ceiling, P(>= 3x salary), P(>= 4x salary)
   and P(< 2x salary), using outcome-ratio tables learned from 12 seasons. This is the most valuable output,
   and it is not tied to UM: it works on a calibrated TFFB number just as well.
2. **One more independent projection** (`UM`) for an ensemble.

**What it is not:** a better ranker than TFFB. See [Limitations](#limitations).

Data and credit: [nflverse](https://github.com/nflverse/nflverse-data) (player-week and team-week stats,
schedules and Vegas lines) and [ffverse's ffopportunity](https://github.com/ffverse/ffopportunity) (expected
fantasy points, an xgboost model). All of it is free and public; none of it is ours.

## What each part does

| Part | Module | What it does |
|---|---|---|
| Fetch | `data.py` | Downloads the release assets below into `data/model_cache/` (gitignored). Completed seasons are fetched once; the season in progress and the schedule every time. |
| History | `history.py` | Actual DK points per player-game and team-game, expected DK points (xFP), Vegas context. |
| Features | `features.py` | One function builds features for training **and** inference, from prior games only. |
| Mean model | `train.py` | One model per position, judged against baselines on a holdout; ships the GBM only where it earns it. |
| Distribution | `distribution.py` | The outcome-ratio tables and `outcome_distribution` / `prob_at_least` / `floor_ceiling`. |
| Checks | `evaluate.py`, `report.py` | Coverage and reliability of the engine, and `backtest.md`. |
| Inference | `predict.py` | `predict_slate` and a vectorised `prob_at_least`. |
| Artifacts | `artifacts.py`, `pipeline.py` | Saving, loading and version-checking `models/um/`. |

### Data sources

| What | URL |
|---|---|
| Player-week stats | `https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.parquet` |
| Team-week stats (DST) | `.../stats_team/stats_team_week_{season}.parquet` |
| Expected fantasy points | `https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.parquet` |
| Schedules and Vegas lines | `.../schedules/games.parquet` (`total_line`; `spread_line` is home-perspective, positive = home favoured) |

Release-asset URLs only: the `github.com/<org>/<repo>/raw/...` form 403s. Regular season only, 2014 onward.
Team codes are normalised (LA/STL -> LAR, OAK -> LV, SD -> LAC) and FB is read as RB. nflverse's snap counts
were **not** used: they are keyed by a different player id and the planning notes made them optional; they
were not tested, so there is no evidence either way on whether they would help.

## History and targets

- **Actual DK points** come from `dk_scoring`'s *actual* scorers via `results_actual.py` (real +3 yardage
  bonuses, exact points-allowed tiers): the same function the results loop uses, so a model target and a
  Model Check row are the same number. Fumbles lost are sack + rushing + receiving; 2-point conversions are
  summed across all three; return TDs and offensive fumble-recovery TDs are scored.
- **DK identity check.** `DK - fantasy_points_ppr` must equal yardage bonuses + interceptions + fumbles lost.
  It holds for every row **except 30 of 70,439**, all exactly +6 and all an offensive fumble-recovery
  touchdown: DraftKings scores it, nflverse's PPR formula does not. With that one line accounted for, the
  break count is **0**.
- **xFP** is ffopportunity's expected components weighted to DK scoring (reception 1, 0.1 per yard, 6 per
  TD, 0.04 per passing yard, 4 per passing TD, -1 per expected interception, 2 per expected 2-pt
  conversion), summed over a player's rows in a game, with no yardage bonuses. ffopportunity has no row
  for the 10% of player-games where the player had **no target, carry or attempt** (their mean DK score is
  0.06); xFP is set to 0 for exactly those rows and left NaN for any row that has touches but no match (there
  are none).

| Check | Value |
|---|---|
| offense player-games | 70,439 |
| ffopportunity xFP join, all player-games | 89.9% |
| ffopportunity xFP join, player-games with any target/carry/attempt | 100.0% |
| unmatched rows (xFP set to 0): with touches / mean DK points | 0 / 0.06 |
| Vegas context (implied total, spread) joined | 100.0% |
| DK identity breaks, raw (DK - PPR vs bonuses + INT + fumbles lost) | 30 |
| DK identity breaks after the fumble-recovery-TD adjustment | 0 |
| team-games (DST) | 6,430 |
| DST Vegas context joined | 100.0% |

## Features

All features use only games **strictly before** the row's week, and rolling windows are over games *played*
and **continue across the season boundary**. A player with no prior games keeps NaN and a games count of 0.

- **Offense** (all positions): DK points, xFP, targets, carries, target share, air-yards share and receiving
  air yards, each averaged over the last 1, 3 and 8 games; games played in the 8-game window; weeks into the
  season; this game's team implied total, spread (positive = this team favoured), game total and home/away;
  and **opponent defense vs the position** -- DK points the opposing defense allowed to the position over its
  last 8 games, net of the league average for the same (season, week, position).
- **QB** additionally: pass attempts and passing EPA over the last 3 and 8 games.
- **DST**: the opponent's implied total; home/away; weeks into the season; the defense's own sacks,
  takeaways and DK points over its last 8 games; and the opposing offense's sacks allowed, giveaways and
  *DST points conceded* (the DK points opposing defenses scored against it) over its last 8 games.

**One code path.** `build_features(history, rows)` is the only place features are made. Each history table is
first rolled up into running means that include each game; a row's features are the running means of the
latest game with `t < row.t` (`merge_asof`, exact matches disallowed, `t = season * 100 + week`). A row can
never see its own game, and `tests/model/test_features.py` pins that three ways: inference-mode features equal
training-mode features exactly, adding week N and later (with absurd values) does not change week N's
features, and every feature matches a naive per-player loop. Deliberately leaking the row's own game fails 8
of those tests.

## The mean model

- **Population.** Offense: player-games with at least 1 prior game and last-3 xFP >= 4. DST: every team-game.
  Outside this gate `predict_slate` returns blank (NaN), never zero.
- **Model.** One `HistGradientBoostingRegressor` per position: `max_iter=400, learning_rate=0.05,
  max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0`. Everything else is scikit-learn's default
  (squared error; its automatic early stopping on large frames). The settings were fixed up front and not
  tuned on the holdout. scikit-learn is pinned (`scikit-learn==1.9.1`).
- **Splits.** Fit on 2014-2022, score on the 2023-2025 holdout, then refit the shipped model on 2014-2025.
- **Baselines.** Trailing-8 DK points; the blend `0.5 x L8 DK + 0.5 x L3 xFP`; for DST, trailing-8 DST points
  (which plays the blend's role in the rule below).
- **Shipping rule.** A position ships the GBM only if its holdout MAE <= the blend's **and** its mean
  within-position-week Spearman rho >= the blend's; otherwise it ships the blend formula. The data decides:

| Pos | Train / holdout rows | MAE L8 | MAE blend | MAE GBM | rho L8 | rho blend | rho GBM | Shipped |
|---|---|---|---|---|---|---|---|---|
| QB | 4,897 / 1,792 | 6.984 | 6.822 | 6.840 | 0.375 | 0.402 | 0.400 | **blend** |
| RB | 9,291 / 2,989 | 5.794 | 5.688 | 5.752 | 0.540 | 0.559 | 0.538 | **blend** |
| WR | 14,197 / 4,788 | 5.892 | 5.830 | 5.797 | 0.482 | 0.495 | 0.504 | **gbm** |
| TE | 5,664 / 1,940 | 4.870 | 4.808 | 4.865 | 0.411 | 0.421 | 0.401 | **blend** |
| DST | 4,670 / 1,632 | 4.710 | 4.710 | 4.683 | 0.103 | 0.103 | 0.187 | **gbm** |

MAE in DK points; rho is the mean Spearman rank correlation within position-week. DST has no blend: its baseline is trailing-8 DST points, so the two baseline columns are identical and that baseline plays the blend's role in the shipping rule. The GBM ships only if MAE_gbm <= MAE_blend AND rho_gbm >= rho_blend.

**Chosen: GBM for WR and DST; the blend formula for QB, RB and TE.** So for QB, RB and TE the "UM" projection
is literally `0.5 x L8 DK + 0.5 x L3 xFP` -- a useful, honest, but not very independent ensemble member. The
independent signal is mostly in WR and DST. The GBM is barely better than the blend where it wins (WR MAE
5.797 against 5.830), and DST's rank correlation is weak for every method (0.10 for the baseline, 0.19 for
the GBM): defenses are close to a coin flip week to week.

This reproduces the planning session's prototype, which was built separately: the trailing-8 MAE and rho
match to the digit (QB 6.98 / .375, RB 5.79 / .540, WR 5.89 / .482, TE 4.87 / .411) and the blend and GBM
columns land within about 0.03 MAE.

## The distribution engine

Out-of-fold predictions on 2014-2025 of **the shipped method** (the GBM fit on all other seasons and
predicting the held-out one; the blend needs no fit) are bucketed by predicted mean:

| Position | Buckets |
|---|---|
| QB | <14, 14-18, 18-22, 22+ |
| RB, WR | <6, 6-10, 10-14, 14-18, 18+ |
| TE | <5, 5-8, 8-11, 11+ |
| DST | <5, 5-7, 7-9, 9+ |

For each (position, bucket) it stores the empirical quantiles of `actual / predicted` and `P(actual = 0)`.
`distribution.csv` holds every bucket's table.

```python
from dfs.model.distribution import outcome_distribution, prob_at_least, floor_ceiling

outcome_distribution("WR", 12.0)  # Quantiles: 23 levels, in points, plus p_zero
prob_at_least("WR", 12.0, 3 * 5.5)  # P(actual >= 16.5)
floor_ceiling("WR", 12.0)  # 20th and 85th percentile, in points
```

A projection is scored by linearly interpolating between the two buckets whose centres (mean predicted
value) bracket it, so the engine works for **any** projection. `prob_at_least` reads the ratio CDF at
`threshold / projection`. A projection <= 0 returns zeros; a NaN projection returns NaN; a threshold <= 0 is
always met.

Two things go beyond the original spec, both flagged here so they can be reviewed:

1. **Extra tail levels.** Besides the required 0.05, 0.10, ..., 0.95 the tables carry 0.01, 0.025, 0.975 and
   0.99. P(>= 4x salary) for a cheap player sits above the 95th percentile of his outcomes, where a grid that
   stopped at 0.95 would force an arbitrary extrapolation. The smallest bucket has 786 games, so the 0.99
   level rests on 8 or more observations.
2. **A continuity correction for DST.** DST points are always whole numbers and 7-8% of DST games land
   exactly on 3 or exactly on 6 (two of the calibration thresholds below). A continuous ratio CDF answers
   P(X > T) at a whole-number T, understating P(X >= T), so for DST `prob_at_least` reads P(X >= ceil(T)) at
   ceil(T) - 0.5. Before the correction DST failed 9 of 30 reliability bins (worst gap 11.6 points); after it,
   2 of 30 (worst 6.2).

## Calibration

Run on the out-of-fold data. **Acceptance rule: every reliability bin within +/-4 points and coverage within
+/-3 points.**

### Coverage (target: below q20 = 20%, above q85 = 15%; accept within +/-3 points)

| Pos | N | Realized below q20 | Realized above q85 | Within +/-3 |
|---|---|---|---|---|
| QB | 6,689 | 20.2% | 15.3% | yes |
| RB | 12,280 | 19.8% | 14.7% | yes |
| WR | 18,985 | 17.4%-19.5% | 15.1% | yes |
| TE | 7,604 | 17.4%-19.2% | 14.8% | yes |
| DST | 6,302 | 20.6% | 14.7% | yes |

A range means the quantile sits on an atom (e.g. a cheap WR's q20 is exactly 0 points): the strict and inclusive shares bracket the target.

Salaries are not in this data, so P(actual >= k x salary) is checked at three stand-in thresholds: the 25th,
50th and 75th percentile of actual points for the position.

### Reliability of P(actual >= T), T at the 25th/50th/75th percentile of actual points

Ten equal-count bins of predicted probability per threshold. `*` marks a gap beyond +/-4 points. A bin's SE is the binomial standard error of its realized rate.

| Pos | Max abs gap | Bins beyond +/-4 (of 30) | Bins beyond 2 SE | All within +/-4 |
|---|---|---|---|---|
| QB | 6.3 | 2/30 | 2 | **NO** |
| RB | 2.6 | 0/30 | 3 | yes |
| WR | 2.4 | 0/30 | 3 | yes |
| TE | 3.2 | 0/30 | 1 | yes |
| DST | 6.2 | 2/30 | 2 | **NO** |

**QB, P(actual >= 10.1 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 31.9% | 38.1% | +6.2* | 1.9 |
| 2 | 669 | 56.0% | 55.2% | -0.8 | 1.9 |
| 3 | 669 | 65.8% | 67.6% | +1.7 | 1.8 |
| 4 | 669 | 73.5% | 74.6% | +1.1 | 1.7 |
| 5 | 669 | 79.2% | 81.8% | +2.6 | 1.5 |
| 6 | 668 | 81.3% | 82.8% | +1.5 | 1.5 |
| 7 | 669 | 83.7% | 85.4% | +1.7 | 1.4 |
| 8 | 669 | 86.0% | 86.5% | +0.5 | 1.3 |
| 9 | 669 | 88.1% | 87.4% | -0.6 | 1.3 |
| 10 | 669 | 91.0% | 90.9% | -0.1 | 1.1 |

**QB, P(actual >= 16.2 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 12.2% | 18.5% | +6.3* | 1.5 |
| 2 | 669 | 31.0% | 31.5% | +0.5 | 1.8 |
| 3 | 669 | 39.1% | 39.6% | +0.5 | 1.9 |
| 4 | 669 | 45.0% | 44.5% | -0.5 | 1.9 |
| 5 | 669 | 50.3% | 51.6% | +1.3 | 1.9 |
| 6 | 668 | 54.1% | 54.0% | -0.0 | 1.9 |
| 7 | 669 | 57.4% | 58.0% | +0.6 | 1.9 |
| 8 | 669 | 60.6% | 60.8% | +0.2 | 1.9 |
| 9 | 669 | 65.7% | 68.2% | +2.5 | 1.8 |
| 10 | 669 | 74.2% | 73.4% | -0.8 | 1.7 |

**QB, P(actual >= 22.7 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 4.3% | 5.7% | +1.4 | 0.9 |
| 2 | 669 | 13.5% | 11.5% | -2.0 | 1.2 |
| 3 | 669 | 17.7% | 15.8% | -1.9 | 1.4 |
| 4 | 669 | 20.5% | 18.7% | -1.8 | 1.5 |
| 5 | 669 | 22.9% | 26.2% | +3.3 | 1.7 |
| 6 | 668 | 25.8% | 23.8% | -2.0 | 1.6 |
| 7 | 669 | 29.1% | 30.0% | +0.9 | 1.8 |
| 8 | 669 | 32.9% | 33.5% | +0.6 | 1.8 |
| 9 | 669 | 37.8% | 37.4% | -0.4 | 1.9 |
| 10 | 669 | 48.3% | 48.1% | -0.2 | 1.9 |

**RB, P(actual >= 3.8 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 42.4% | 42.9% | +0.5 | 1.4 |
| 2 | 1,228 | 52.7% | 52.8% | +0.1 | 1.4 |
| 3 | 1,228 | 60.6% | 59.2% | -1.4 | 1.4 |
| 4 | 1,228 | 68.1% | 70.7% | +2.6 | 1.3 |
| 5 | 1,228 | 74.6% | 74.6% | +0.0 | 1.2 |
| 6 | 1,228 | 81.0% | 83.1% | +2.2 | 1.1 |
| 7 | 1,228 | 86.1% | 84.9% | -1.2 | 1.0 |
| 8 | 1,228 | 89.9% | 91.5% | +1.7 | 0.8 |
| 9 | 1,228 | 93.7% | 93.0% | -0.7 | 0.7 |
| 10 | 1,228 | 97.0% | 97.6% | +0.6 | 0.4 |

**RB, P(actual >= 8.6 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 18.0% | 20.0% | +2.0 | 1.1 |
| 2 | 1,228 | 25.5% | 24.9% | -0.6 | 1.2 |
| 3 | 1,228 | 30.9% | 30.5% | -0.4 | 1.3 |
| 4 | 1,228 | 37.7% | 39.0% | +1.3 | 1.4 |
| 5 | 1,228 | 44.1% | 43.9% | -0.2 | 1.4 |
| 6 | 1,228 | 51.8% | 54.5% | +2.6 | 1.4 |
| 7 | 1,228 | 60.1% | 59.1% | -1.0 | 1.4 |
| 8 | 1,228 | 67.0% | 69.5% | +2.4 | 1.3 |
| 9 | 1,228 | 75.3% | 75.7% | +0.4 | 1.2 |
| 10 | 1,228 | 84.5% | 84.6% | +0.2 | 1.0 |

**RB, P(actual >= 15.3 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 5.6% | 6.5% | +0.9 | 0.7 |
| 2 | 1,228 | 9.3% | 8.1% | -1.3 | 0.8 |
| 3 | 1,228 | 11.7% | 9.5% | -2.2 | 0.8 |
| 4 | 1,228 | 14.4% | 15.1% | +0.8 | 1.0 |
| 5 | 1,228 | 18.6% | 19.2% | +0.7 | 1.1 |
| 6 | 1,228 | 23.3% | 24.8% | +1.5 | 1.2 |
| 7 | 1,228 | 28.7% | 28.0% | -0.7 | 1.3 |
| 8 | 1,228 | 35.2% | 34.4% | -0.9 | 1.4 |
| 9 | 1,228 | 43.9% | 45.1% | +1.2 | 1.4 |
| 10 | 1,228 | 59.5% | 60.2% | +0.7 | 1.4 |

**WR, P(actual >= 3.5 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 43.8% | 42.2% | -1.6 | 1.1 |
| 2 | 1,898 | 53.9% | 56.3% | +2.4 | 1.1 |
| 3 | 1,899 | 62.7% | 63.5% | +0.8 | 1.1 |
| 4 | 1,898 | 69.5% | 71.0% | +1.5 | 1.0 |
| 5 | 1,899 | 75.2% | 75.5% | +0.3 | 1.0 |
| 6 | 1,898 | 81.0% | 81.7% | +0.6 | 0.9 |
| 7 | 1,898 | 85.7% | 86.8% | +1.1 | 0.8 |
| 8 | 1,899 | 89.4% | 90.9% | +1.5 | 0.7 |
| 9 | 1,898 | 92.4% | 92.8% | +0.4 | 0.6 |
| 10 | 1,899 | 95.6% | 96.2% | +0.6 | 0.4 |

**WR, P(actual >= 8.1 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 17.5% | 17.9% | +0.4 | 0.9 |
| 2 | 1,898 | 25.7% | 28.0% | +2.3 | 1.0 |
| 3 | 1,899 | 32.0% | 32.6% | +0.7 | 1.1 |
| 4 | 1,898 | 38.3% | 38.1% | -0.1 | 1.1 |
| 5 | 1,899 | 45.2% | 46.1% | +0.9 | 1.1 |
| 6 | 1,898 | 53.1% | 53.3% | +0.3 | 1.1 |
| 7 | 1,898 | 60.6% | 62.6% | +2.0 | 1.1 |
| 8 | 1,899 | 66.8% | 68.0% | +1.2 | 1.1 |
| 9 | 1,898 | 73.4% | 73.8% | +0.4 | 1.0 |
| 10 | 1,899 | 82.1% | 82.3% | +0.2 | 0.9 |

**WR, P(actual >= 14.5 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 4.7% | 4.7% | +0.1 | 0.5 |
| 2 | 1,898 | 8.6% | 9.5% | +0.9 | 0.7 |
| 3 | 1,899 | 11.9% | 12.0% | +0.1 | 0.7 |
| 4 | 1,898 | 15.3% | 14.3% | -1.0 | 0.8 |
| 5 | 1,899 | 19.8% | 18.5% | -1.3 | 0.9 |
| 6 | 1,898 | 25.1% | 25.4% | +0.4 | 1.0 |
| 7 | 1,898 | 31.0% | 32.0% | +1.0 | 1.1 |
| 8 | 1,899 | 36.9% | 37.5% | +0.7 | 1.1 |
| 9 | 1,898 | 43.5% | 43.9% | +0.4 | 1.1 |
| 10 | 1,899 | 55.4% | 54.9% | -0.4 | 1.1 |

**TE, P(actual >= 2.7 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 50.4% | 51.6% | +1.2 | 1.8 |
| 2 | 760 | 57.8% | 56.7% | -1.1 | 1.8 |
| 3 | 760 | 64.0% | 66.1% | +2.1 | 1.7 |
| 4 | 761 | 69.6% | 67.0% | -2.6 | 1.7 |
| 5 | 760 | 74.6% | 77.8% | +3.2 | 1.5 |
| 6 | 760 | 79.2% | 81.2% | +2.0 | 1.4 |
| 7 | 761 | 82.9% | 83.2% | +0.3 | 1.4 |
| 8 | 760 | 86.1% | 86.2% | +0.1 | 1.3 |
| 9 | 760 | 89.8% | 90.5% | +0.7 | 1.1 |
| 10 | 761 | 93.1% | 93.7% | +0.6 | 0.9 |

**TE, P(actual >= 6.2 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 22.3% | 23.7% | +1.3 | 1.5 |
| 2 | 760 | 28.9% | 28.3% | -0.6 | 1.6 |
| 3 | 760 | 35.2% | 35.5% | +0.4 | 1.7 |
| 4 | 761 | 41.4% | 41.0% | -0.4 | 1.8 |
| 5 | 760 | 46.9% | 48.7% | +1.7 | 1.8 |
| 6 | 760 | 52.4% | 53.3% | +0.9 | 1.8 |
| 7 | 761 | 58.6% | 61.4% | +2.8 | 1.8 |
| 8 | 760 | 63.9% | 62.1% | -1.8 | 1.8 |
| 9 | 760 | 70.3% | 70.5% | +0.3 | 1.7 |
| 10 | 761 | 79.4% | 80.2% | +0.7 | 1.4 |

**TE, P(actual >= 11.4 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 5.9% | 5.8% | -0.2 | 0.8 |
| 2 | 760 | 9.8% | 7.9% | -1.9 | 1.0 |
| 3 | 760 | 13.5% | 13.8% | +0.3 | 1.3 |
| 4 | 761 | 17.2% | 18.1% | +0.9 | 1.4 |
| 5 | 760 | 20.6% | 20.4% | -0.2 | 1.5 |
| 6 | 760 | 24.4% | 23.4% | -0.9 | 1.5 |
| 7 | 761 | 28.5% | 31.1% | +2.6 | 1.7 |
| 8 | 760 | 33.8% | 31.1% | -2.7 | 1.7 |
| 9 | 760 | 41.8% | 42.8% | +0.9 | 1.8 |
| 10 | 761 | 55.2% | 55.8% | +0.7 | 1.8 |

**DST, P(actual >= 3.0 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 57.5% | 63.7% | +6.2* | 1.9 |
| 2 | 630 | 68.9% | 67.9% | -0.9 | 1.9 |
| 3 | 630 | 71.5% | 71.3% | -0.2 | 1.8 |
| 4 | 630 | 73.3% | 73.8% | +0.5 | 1.8 |
| 5 | 630 | 75.3% | 75.9% | +0.5 | 1.7 |
| 6 | 630 | 77.9% | 80.0% | +2.1 | 1.6 |
| 7 | 630 | 80.6% | 78.1% | -2.5 | 1.6 |
| 8 | 630 | 82.3% | 83.0% | +0.7 | 1.5 |
| 9 | 630 | 84.4% | 83.8% | -0.6 | 1.5 |
| 10 | 631 | 87.7% | 87.0% | -0.7 | 1.3 |

**DST, P(actual >= 6.0 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 30.7% | 36.6% | +5.9* | 1.9 |
| 2 | 630 | 45.4% | 42.2% | -3.2 | 2.0 |
| 3 | 630 | 47.5% | 44.9% | -2.6 | 2.0 |
| 4 | 630 | 48.6% | 47.1% | -1.5 | 2.0 |
| 5 | 630 | 51.0% | 48.9% | -2.1 | 2.0 |
| 6 | 630 | 53.6% | 56.8% | +3.3 | 2.0 |
| 7 | 630 | 56.6% | 55.6% | -1.1 | 2.0 |
| 8 | 630 | 60.0% | 59.4% | -0.7 | 2.0 |
| 9 | 630 | 62.6% | 60.2% | -2.5 | 2.0 |
| 10 | 631 | 68.5% | 70.2% | +1.7 | 1.8 |

**DST, P(actual >= 10.0 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 13.0% | 15.5% | +2.5 | 1.4 |
| 2 | 630 | 22.1% | 20.3% | -1.8 | 1.6 |
| 3 | 630 | 24.0% | 22.7% | -1.3 | 1.7 |
| 4 | 630 | 24.8% | 22.7% | -2.1 | 1.7 |
| 5 | 630 | 26.8% | 23.5% | -3.3 | 1.7 |
| 6 | 630 | 29.2% | 31.9% | +2.7 | 1.9 |
| 7 | 630 | 31.8% | 32.5% | +0.7 | 1.9 |
| 8 | 630 | 34.0% | 34.3% | +0.3 | 1.9 |
| 9 | 630 | 35.7% | 34.1% | -1.6 | 1.9 |
| 10 | 631 | 41.6% | 39.8% | -1.8 | 1.9 |

### Verdict against the acceptance rule

| Position | Coverage | Reliability |
|---|---|---|
| QB | pass | **fail**: 2 of 30 bins beyond 4 points (max 6.3) |
| RB | pass | pass (max gap 2.6) |
| WR | pass | pass (max gap 2.4) |
| TE | pass | pass (max gap 3.2) |
| DST | pass | **fail**: 2 of 30 bins beyond 4 points (max 6.2) |

**Every failing bin is bin 1 -- the lowest-projection decile** (QB projected below about 10, 10% of QB rows;
DST projected below about 4, 10% of DST rows) and each of those misses is an *understatement*: the realized
rate is 6 points higher than predicted. The spec's lowest buckets (`<14` for QB, `<5` for DST) are too wide:
actuals do not scale down with a tiny projection, so scaling the bucket's ratios understates what a
low-projected player does. Bins 2-10 are all within 3.3 points, for both QB and DST. These are not
players anyone rosters, but the rule is the rule: **QB and DST do not pass as specified.** The remedy is an
extra lowest bucket (e.g. QB `<10`); it changes the specified bucket edges, so it is left for review
rather than done.

### A stricter check: tables fit on 2014-2022, scored on 2023-2025

### Supplementary: tables built on 2014-2022, scored on 2023-2025

The check above measures the tables on the games that built them. This one measures them on seasons they never saw (fewer games per bin, so more sampling noise).

| Pos | N | Below q20 | Above q85 | Max abs gap (pts) | Bins beyond +/-4 (of 30) | Bins beyond 2 SE |
|---|---|---|---|---|---|---|
| QB | 1,792 | 22.3% | 14.6% | 9.1 | 7/30 | 1 |
| RB | 2,989 | 18.1% | 13.3% | 9.9 | 9/30 | 8 |
| WR | 4,788 | 17.4%-19.6% | 13.5% | 4.6 | 2/30 | 1 |
| TE | 1,940 | 17.9% | 15.8% | 9.6 | 14/30 | 10 |
| DST | 1,632 | 23.0% | 12.5% | 11.8 | 13/30 | 4 |

The check the spec asks for measures the tables on the games that built them. This one does not, and it is
**materially worse for TE (14 of 30 bins beyond 4 points, 10 of them beyond two standard errors), RB (9, 8)
and DST (13, 4)**: more than sampling noise. WR holds up (2 of 30), and QB's 7 of 30 is mostly noise (only 1 bin beyond two standard errors, at about 180 games a bin). Ratios drift between eras: for example
DST's overall actual-to-predicted ratio fell from 1.03 (2014-22) to 0.93 (2023-25), and the lowest RB, WR
and DST buckets move the most. The shipped tables pool all 12 seasons, so applied to a future season the
real error is closer to this table than to the in-sample one. A recency-weighted version of the tables is the
obvious next step; it is not done here.

### The outcome-ratio tables

| Pos | Bucket | N | Centre | P(0) | q20 | q50 | q85 | q95 |
|---|---|---|---|---|---|---|---|---|
| QB | <14 | 1,704 | 10.36 | 2.8% | 0.17 | 0.98 | 1.97 | 2.76 |
| QB | 14-18 | 2,171 | 16.10 | 0.3% | 0.60 | 0.99 | 1.55 | 1.91 |
| QB | 18-22 | 1,872 | 19.81 | 0.2% | 0.58 | 0.93 | 1.43 | 1.77 |
| QB | 22+ | 942 | 24.32 | 0.0% | 0.59 | 0.89 | 1.28 | 1.54 |
| RB | <6 | 2,262 | 4.81 | 9.0% | 0.17 | 0.71 | 2.24 | 3.70 |
| RB | 6-10 | 3,796 | 7.91 | 3.3% | 0.27 | 0.79 | 1.86 | 2.75 |
| RB | 10-14 | 3,127 | 11.92 | 0.5% | 0.40 | 0.83 | 1.67 | 2.28 |
| RB | 14-18 | 1,959 | 15.77 | 0.1% | 0.47 | 0.85 | 1.55 | 1.96 |
| RB | 18+ | 1,136 | 21.21 | 0.0% | 0.48 | 0.86 | 1.44 | 1.85 |
| WR | <6 | 2,959 | 4.98 | 22.4% | 0.00 | 0.63 | 1.89 | 3.05 |
| WR | 6-10 | 7,099 | 7.90 | 9.0% | 0.28 | 0.78 | 1.85 | 2.74 |
| WR | 10-14 | 5,384 | 11.93 | 2.9% | 0.41 | 0.88 | 1.76 | 2.38 |
| WR | 14-18 | 2,757 | 15.60 | 1.2% | 0.47 | 0.88 | 1.67 | 2.19 |
| WR | 18+ | 786 | 19.82 | 0.3% | 0.50 | 0.90 | 1.55 | 1.97 |
| TE | <5 | 1,400 | 4.24 | 20.1% | 0.00 | 0.72 | 2.05 | 3.05 |
| TE | 5-8 | 2,680 | 6.44 | 10.3% | 0.29 | 0.78 | 1.92 | 2.79 |
| TE | 8-11 | 1,905 | 9.35 | 4.3% | 0.35 | 0.84 | 1.68 | 2.34 |
| TE | 11+ | 1,619 | 13.95 | 2.0% | 0.38 | 0.79 | 1.49 | 2.04 |
| DST | <5 | 1,367 | 3.80 | 6.5% | 0.22 | 1.15 | 2.91 | 4.61 |
| DST | 5-7 | 2,042 | 6.04 | 5.3% | 0.30 | 0.88 | 2.03 | 2.94 |
| DST | 7-9 | 1,770 | 7.93 | 3.0% | 0.35 | 0.83 | 1.76 | 2.42 |
| DST | 9+ | 1,121 | 10.50 | 2.1% | 0.32 | 0.72 | 1.43 | 1.97 |

The full set of quantile levels is in `distribution.csv`.

## Using it

```
dfs model fetch      # download / refresh the cache (data/model_cache/, gitignored)
dfs model train      # rebuild models/um/ (about 40 s). Once a season, or on demand.
dfs model backtest   # recompute and print the holdout and calibration tables (--saved: print the shipped one)
dfs model info       # artifact version, training seasons, chosen method, metrics, scikit-learn version
```

`dfs model` is **registered hidden** for now: a visible command must also be added to the launcher's
`MORE_LABELS`, `commands_doc.SECTIONS` and the generated `docs/COMMANDS.md`, which are outside this model's
files. Whoever wires the model into the sheet drops `hidden=True` in `src/dfs/model/cli.py` and adds those
three entries.

`predict_slate(players)` takes `gsis_id, position, team, opp, season, week, implied, spread, total, home` and
returns `gsis_id, um_mean, floor, ceil`, in input order. `implied` is **this team's** implied total, `spread`
is positive when this team is favoured (the opposite sign of a betting-line "-3"), `home` is 1 or 0. For a
defense, `gsis_id` is the team code and the context describes the defense's own team. It reads the cached
history after fetching the current season's files fresh (`fetch=False` skips that), builds features through
the same `build_features`, and returns blank outside the training population. For a vectorised
`prob_at_least` over mixed positions and per-row thresholds (e.g. `3 * salary / 1000`), use
`dfs.model.predict.prob_at_least`.

**Applying the engine to TFFB.** The tables were learned from UM's own errors, so they assume a projection
that is roughly unbiased at its level. TFFB's error is mostly a level bias (too high, especially on cheap RBs
and WRs), so apply the engine to a *calibrated* TFFB number, not the raw one.

**Artifacts** (`models/um/`, under 1 MB against a 15 MB limit): a `joblib` per position whose GBM shipped
(`WR.joblib`, `DST.joblib`), `metadata.json`, `distribution.csv`, `backtest.md`. A joblib file is valid only
under the scikit-learn that wrote it, so loading checks the version (and the feature list) and fails with
"run `dfs model train`" on any mismatch. Bump the `scikit-learn` pin and retrain together.

## Limitations

- **Public usage data has limited signal.** The GBM is barely better than a simple blend where it wins at all
  (WR), and loses to it for QB, RB and TE.
- **TFFB ranks better.** In the planning session's check against Sam's real 2026 TFFB projections (weeks
  1-4, rosterable pool), TFFB's rank correlation was about 0.48 against 0.38 for a gradient-boosted usage
  model. UM is not a replacement for it.
- **The value is the distribution engine and ensemble diversity** -- not the point projections.
- **Where the engine is weakest:** the lowest projection decile (see the acceptance verdict), and drift
  between eras for TE, RB and DST (see the stricter check). Treat Floor and Ceiling as wider than they look.
- **No injury or depth-chart features.** A player returning from injury, or about to lose snaps, looks like
  his trailing games say. A rookie or newcomer with no prior games gets no UM projection at all.
- **Not tuned.** The hyper-parameters are the fixed ones; the bucket edges are the specified ones.
- DST rank signal is weak for every method.
