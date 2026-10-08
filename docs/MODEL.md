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
| Distribution | `distribution.py` | The outcome-ratio tables (a smooth quantile fit per position) and `outcome_distribution` / `prob_at_least` / `floor_ceiling`. |
| Bench | `tests/model/bench_distribution.py` | Scores candidate table builders (band ratios, coverage, reliability, pinball loss, the simulator back-test). Not a pytest module. |
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
predicting the held-out one; the blend needs no fit) give, per position, between 6,000 and 19,000 pairs of
projection and actual DK points. The engine learns how the distribution of `actual / predicted` depends on the
projection.

**How (table format `knots-v2`).** For each of the 23 quantile levels, a quantile regression of the ratio on a
**linear spline in log(projection)**: knots at the 20th, 40th, 60th, 80th and 95th percentile of the position's
projections, seven coefficients per level, fitted exactly (a linear program, `distribution._quantile_fit`).
The fitted curve is evaluated at 21 projections, log-spaced between the 0.5th and 99.5th percentile of the
position's projections, and stored in `distribution.csv`: one row per position and knot, `center` the knot's
projection, `p_zero`, and one column per level. Each row is sorted across levels so quantiles never cross, and
no ratio quantile falls below zero unless the data do at that level (QB and DST).

**P(actual = 0)** is handled exactly as before: the share of games that scored 0 in each of the specified
buckets (`BUCKET_EDGES`: QB `<14, 14-18, 18-22, 22+`; RB and WR `<6, 6-10, 10-14, 14-18, 18+`; TE `<5, 5-8,
8-11, 11+`; DST `<5, 5-7, 7-9, 9+`), blended linearly between bucket centres.

```python
from dfs.model.distribution import outcome_distribution, prob_at_least, floor_ceiling

outcome_distribution("WR", 12.0)  # Quantiles: 23 levels, in points, plus p_zero
prob_at_least("WR", 12.0, 3 * 5.5)  # P(actual >= 16.5)
floor_ceiling("WR", 12.0)  # 20th and 85th percentile, in points
```

A projection is scored by linearly interpolating between the two knots around it, so the engine works for
**any** projection. Beyond the first and last knot -- the edge of the data -- the **ratio is held flat**, not the
points. `prob_at_least` reads the ratio CDF at `threshold / projection`. A projection <= 0 returns zeros; a NaN
projection returns NaN; a threshold <= 0 is always met. The public functions (`outcome_distribution`,
`prob_at_least`, `prob_at_least_many`, `floor_ceiling`, `floor_ceiling_many`, `ratio_matrix`) are unchanged
from the first release; only what the table holds changed. Its shape (columns) did not, so a first-release
file still loads and is scored by the same interpolation, with its old top-end bias; `metadata.json` records
`distribution.table_format` (`knots-v2`), the knot quantiles, the edge percentiles and the knot count.

Two things go beyond the original spec, both flagged here so they can be reviewed:

1. **Extra tail levels.** Besides the required 0.05, 0.10, ..., 0.95 the tables carry 0.01, 0.025, 0.975 and
   0.99. P(>= 4x salary) for a cheap player sits above the 95th percentile of his outcomes, where a grid that
   stopped at 0.95 would force an arbitrary extrapolation.
2. **A continuity correction for DST.** DST points are always whole numbers and 7-8% of DST games land
   exactly on 3 or exactly on 6 (two of the calibration thresholds below). A continuous ratio CDF answers
   P(X > T) at a whole-number T, understating P(X >= T), so for DST `prob_at_least` reads P(X >= ceil(T)) at
   ceil(T) - 0.5.

### Why the table is smooth now: the top end

The first release stored the empirical ratio quantiles of a handful of projection buckets (four or five per
position) and interpolated between bucket *centres*, reusing the top bucket's ratios for any projection
beyond its centre. Real outcomes regress harder at the top, so the engine overstated the best players, and
the simulator's top cash deciles over-predicted by 6-12 points ([SIM.md](SIM.md)). Actual points saturate:
a QB projected 23, 26 and 29 averages 21.3, 22.5 and 23.3.

**The bench.** `tests/model/bench_distribution.py` scores a candidate table builder on the out-of-fold rows
the tables are built on, three ways: *in sample* (tables built on 2014-2025, scored on the same rows: the
acceptance rule's reading), *out of time* (built on 2014-2022, scored on 2023-2025) and *season-block
cross-validation* (four blocks of three seasons; every row is scored by tables that never saw its block, to
tell an in-sample pass from an overfit). It reports (a) actual / engine-mean per projection band (the
simulator's own marginal, zero atom included), (b) q20 / q85 coverage, (c) the reliability tables below, (d)
pinball loss over the stored levels, and with `--sim` (e) the real `dfs sim backtest`. The band grid was fixed
before any candidate was scored (the bands of [SIM.md](SIM.md)'s table, extended to cover each position).
A band is *gated* when it has at least 50 rows; its standard error is the sampling error of its mean.

Actual / engine mean, in sample (`*`: a gated band beyond 0.95-1.05):

| Position | Projection | n | Mean projection | Engine mean (before) | Engine mean (after) | Actual mean | Actual / engine (before) | Actual / engine (after) |
|---|---|---|---|---|---|---|---|---|
| QB | <14 | 1,704 | 10.4 | 11.1 | 11.0 | 11.0 | 0.99 | 1.00 |
| QB | 14-18 | 2,171 | 16.1 | 16.4 | 16.4 | 16.5 | 1.00 | 1.00 |
| QB | 18-22 | 1,872 | 19.8 | 19.0 | 19.1 | 19.0 | 1.00 | 1.00 |
| QB | 22-25 | 673 | 23.3 | 21.2 | 21.3 | 21.3 | 1.01 | 1.00 |
| QB | 25-28 | 209 | 26.2 | 23.5 | 22.5 | 22.5 | 0.96 | 1.00 |
| QB | 28+ | 60 | 29.5 | 26.4 | 23.6 | 23.3 | 0.88* | 0.99 |
| RB | <6 | 2,262 | 4.8 | 5.4 | 5.4 | 5.4 | 1.01 | 1.00 |
| RB | 6-10 | 3,796 | 7.9 | 8.1 | 8.0 | 8.0 | 0.99 | 1.00 |
| RB | 10-14 | 3,127 | 11.9 | 11.6 | 11.6 | 11.6 | 1.00 | 1.00 |
| RB | 14-18 | 1,959 | 15.8 | 14.9 | 14.9 | 14.9 | 1.00 | 1.00 |
| RB | 18-22 | 790 | 19.7 | 18.2 | 18.1 | 18.1 | 1.00 | 1.00 |
| RB | 22-26 | 268 | 23.7 | 21.7 | 21.3 | 21.9 | 1.01 | 1.03 |
| RB | 26+ | 78 | 27.8 | 25.5 | 24.5 | 23.4 | 0.92* | 0.95 |
| WR | <6 | 2,959 | 5.0 | 4.7 | 4.7 | 4.7 | 1.00 | 0.99 |
| WR | 6-10 | 7,099 | 7.9 | 7.8 | 7.8 | 7.9 | 1.01 | 1.01 |
| WR | 10-14 | 5,384 | 11.9 | 12.1 | 12.2 | 12.2 | 1.01 | 1.00 |
| WR | 14-18 | 2,757 | 15.6 | 15.6 | 15.6 | 15.6 | 1.00 | 1.00 |
| WR | 18-22 | 715 | 19.5 | 19.2 | 19.0 | 19.1 | 1.00 | 1.01 |
| WR | 22+ | 71 | 22.9 | 22.4 | 21.9 | 22.7 | 1.01 | 1.04 |
| TE | <5 | 1,400 | 4.2 | 4.3 | 4.4 | 4.3 | 1.00 | 0.98 |
| TE | 5-8 | 2,680 | 6.4 | 6.5 | 6.5 | 6.6 | 1.01 | 1.01 |
| TE | 8-11 | 1,905 | 9.3 | 9.0 | 9.0 | 9.0 | 1.00 | 1.00 |
| TE | 11-14 | 980 | 12.3 | 11.2 | 11.2 | 11.2 | 1.00 | 1.00 |
| TE | 14-17 | 437 | 15.3 | 13.7 | 13.5 | 13.5 | 0.98 | 1.00 |
| TE | 17+ | 202 | 19.1 | 17.0 | 15.8 | 15.9 | 0.93* | 1.01 |
| DST | <5 | 1,367 | 3.8 | 5.3 | 5.4 | 5.2 | 1.00 | 0.97 |
| DST | 5-7 | 2,042 | 6.0 | 6.5 | 6.3 | 6.3 | 0.97 | 1.01 |
| DST | 7-9 | 1,770 | 7.9 | 7.6 | 7.6 | 7.6 | 1.00 | 1.00 |
| DST | 9-11 | 806 | 9.8 | 8.5 | 8.3 | 8.5 | 1.00 | 1.02 |
| DST | 11-13 | 254 | 11.8 | 9.8 | 9.0 | 9.0 | 0.92* | 1.00 |
| DST | 13+ | 61 | 13.8 | 11.4 | 9.5 | 9.2 | 0.81* | 0.97 |

The tightest cell after the change is RB 26+ at 0.954 (n = 78, standard error 4.6%): with fewer than a hundred
games above the top band, "within 5%" is the most a band that thin can be asked for, and a different sample
would land a few points either side.

**What was tried.** "Top-5% tail error" is the mean absolute gap, in percentage points, between predicted and
realized P(actual >= m x projection) for m = 1, 1.25 and 1.5 on each position's top 5% of projections: the
Boom%-style probabilities of the best plays. Lower is better in every column.

| Remedy | Band fails (n >= 50), in sample / CV | Reliability bins beyond +/-4 (of 150), in sample / out of time | Pinball loss (all positions), in sample / out of time | Top-5% tail error (pts), in sample |
|---|---|---|---|---|
| Current (first release) | 5 / 5 | 4 / 45 | 1.7578 / 1.7309 | 2.88 |
| Finer buckets, top + QB bottom (new bucket needs >= 300 rows) | 3 / 3 | 2 / 45 | 1.7560 / 1.7296 | 0.86 |
| Finer buckets, top + every bottom | 3 / 3 | 0 / 46 | 1.7555 / 1.7294 | 0.86 |
| Smooth: one line in log(projection) | 3 / 3 | 9 / 47 | 1.7589 / 1.7317 | 2.60 |
| Smooth: 3-knot spline | 1 / 0 | 0 / 41 | 1.7551 / 1.7281 | 0.54 |
| **Smooth: 5-knot spline (shipped)** | **0 / 0** | **0 / 37** | **1.7546 / 1.7279** | **0.51** |
| Together: finer buckets, spline beyond their outer centres | 1 / 0 | 0 / 46 | 1.7549 / 1.7289 | 0.66 |

(The bench's reliability counts use the position-wide p25 / p50 / p75 as thresholds, so they differ a little
from the tables below, which take the percentiles of the rows being scored.)

Component (e), the real `dfs sim backtest` (correlated, shipped marginals, all lineups; largest coverage gap /
cash-line decile gap / GPP-target decile gap, in points), is in [SIM.md](SIM.md); across the remedies it reads
first release 2.5 / 9.3 / 5.5, finer buckets (top + every bottom) 1.7 / 7.6 / 3.8, one line 2.2 / 9.3 / 2.9,
3-knot spline 1.3 / 6.0 / 2.4, **5-knot spline 1.4 / 5.4 / 2.2**, together 1.4 / 6.3 / 2.7. The three
spline-based remedies have every GPP-target decile inside +/-4 for all, stacked and random lineups; finer
buckets and the single line do not (random lineups: 4.9 to 5.1). None brings the cash line inside +/-4, for
the reason given in SIM.md (one season's scoring level).

**Chosen: the 5-knot spline.** It is the only candidate that passes every band in sample, and it also passes
them in cross-validation, so the pass is not flexibility fitting the thin top. It has the best pinball loss
in sample and out of time (in cross-validation it ties the 3-knot spline, 1.7581 against 1.7580), the best
top-5% tail error, and no reliability bin beyond +/-4 in sample. What it does *not* win is mid-range
reliability in cross-validation: there the bucket-based candidates are cleaner (no bin beyond +/-4, largest
gap 3.9) than the spline (one bin, largest gap 4.4, a DST decile). Why the others fell short:

- *Finer buckets* help (the 300-row rule splits the top bucket: QB 22-24 and 24+, RB 18-20, 20-22 and 22+, TE
  11-12 through 15+, DST 9-10, 10-11 and 11+; WR's stays whole) but a 300-row bucket is still centred well
  below the last few percent of projections, where the mean plateaus: QB 28+, RB 26+ and DST 13+ stay at 0.91,
  0.93 and 0.90 in sample (0.90, 0.93 and 0.85 cross-validated). Splitting the bottom buckets of every
  position fixes the lowest deciles (QB, DST) that the top-only split does not.
- *One straight line in log(projection)* is too stiff: QB's ratio is flat at about 1.03 from 10 to 17 and only
  then falls, so a line overshoots at both ends (9 reliability bins beyond +/-4 in sample).
- The *3-knot spline* is within noise of the shipped one (it misses one band, RB 26+, at 0.948 against 0.954),
  and is two coefficients per level simpler. The 5-knot version was shipped because it meets the acceptance
  rule as written; a tie is a tie, and the cost is small.
- *Together* is no better than the spline alone and has more parts.

The spline is also robust to its free choices: 21 against 40 knots, and edges at the 0th/100th against the
0.5th/99.5th percentile, change neither a pass/fail nor the pinball loss; the edge at the 1st/99th percentile
(flat beyond it sooner) lets RB 26+ and DST 13+ slip to 0.946 and 0.942, which is why the data edge is where
the data still has a few dozen rows. A six-knot variant (knots at 10/30/50/70/90/97%) fit slightly better in
sample (pinball 1.7544) and worse out of time (11 failing bands against 9).

**What it costs.** `dfs model train` takes about 2 minutes on a 4-core container, 35 seconds of it the
23 x 5 exact quantile fits. The artifacts are 740 KB against the 15 MB limit.

## Calibration

Run on the out-of-fold data. **Acceptance rule: every reliability bin within +/-4 points and coverage within
+/-3 points.**

### Coverage (target: below q20 = 20%, above q85 = 15%; accept within +/-3 points)

| Pos | N | Realized below q20 | Realized above q85 | Within +/-3 |
|---|---|---|---|---|
| QB | 6,689 | 20.2%-20.3% | 15.0% | yes |
| RB | 12,280 | 19.9% | 14.9% | yes |
| WR | 18,985 | 20.2%-20.4% | 15.0% | yes |
| TE | 7,604 | 20.1% | 15.0% | yes |
| DST | 6,302 | 20.0% | 14.6% | yes |

A range means the quantile sits on an atom (e.g. a cheap WR's q20 is exactly 0 points): the strict and inclusive shares bracket the target.

Salaries are not in this data, so P(actual >= k x salary) is checked at three stand-in thresholds: the 25th,
50th and 75th percentile of actual points for the position.

### Reliability of P(actual >= T), T at the 25th/50th/75th percentile of actual points

Ten equal-count bins of predicted probability per threshold. `*` marks a gap beyond +/-4 points. A bin's SE is the binomial standard error of its realized rate.

| Pos | Max abs gap | Bins beyond +/-4 (of 30) | Bins beyond 2 SE | All within +/-4 |
|---|---|---|---|---|
| QB | 2.4 | 0/30 | 0 | yes |
| RB | 2.1 | 0/30 | 0 | yes |
| WR | 1.9 | 0/30 | 0 | yes |
| TE | 3.1 | 0/30 | 0 | yes |
| DST | 3.5 | 0/30 | 1 | yes |

**QB, P(actual >= 10.1 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 38.3% | 38.1% | -0.2 | 1.9 |
| 2 | 669 | 55.8% | 55.2% | -0.7 | 1.9 |
| 3 | 669 | 66.9% | 67.6% | +0.6 | 1.8 |
| 4 | 669 | 75.9% | 74.6% | -1.3 | 1.7 |
| 5 | 669 | 80.6% | 81.8% | +1.1 | 1.5 |
| 6 | 668 | 82.6% | 82.8% | +0.2 | 1.5 |
| 7 | 669 | 84.4% | 85.4% | +0.9 | 1.4 |
| 8 | 669 | 86.3% | 86.5% | +0.3 | 1.3 |
| 9 | 669 | 88.4% | 87.4% | -1.0 | 1.3 |
| 10 | 669 | 91.1% | 90.9% | -0.2 | 1.1 |

**QB, P(actual >= 16.2 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 18.6% | 18.5% | -0.1 | 1.5 |
| 2 | 669 | 31.1% | 31.5% | +0.5 | 1.8 |
| 3 | 669 | 38.6% | 39.6% | +1.0 | 1.9 |
| 4 | 669 | 45.8% | 44.5% | -1.3 | 1.9 |
| 5 | 669 | 50.9% | 51.6% | +0.6 | 1.9 |
| 6 | 668 | 54.3% | 54.0% | -0.3 | 1.9 |
| 7 | 669 | 57.4% | 58.0% | +0.6 | 1.9 |
| 8 | 669 | 61.2% | 60.8% | -0.3 | 1.9 |
| 9 | 669 | 66.3% | 68.2% | +1.9 | 1.8 |
| 10 | 669 | 73.5% | 73.4% | -0.1 | 1.7 |

**QB, P(actual >= 22.7 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 669 | 7.0% | 5.7% | -1.3 | 0.9 |
| 2 | 669 | 11.5% | 11.5% | -0.0 | 1.2 |
| 3 | 669 | 15.7% | 15.8% | +0.2 | 1.4 |
| 4 | 669 | 20.4% | 18.7% | -1.7 | 1.5 |
| 5 | 669 | 23.7% | 26.2% | +2.4 | 1.7 |
| 6 | 668 | 26.2% | 23.8% | -2.4 | 1.6 |
| 7 | 669 | 29.1% | 30.0% | +0.9 | 1.8 |
| 8 | 669 | 32.9% | 33.5% | +0.5 | 1.8 |
| 9 | 669 | 38.2% | 37.4% | -0.8 | 1.9 |
| 10 | 669 | 47.5% | 48.1% | +0.6 | 1.9 |

**RB, P(actual >= 3.8 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 42.6% | 42.9% | +0.3 | 1.4 |
| 2 | 1,228 | 52.2% | 52.8% | +0.5 | 1.4 |
| 3 | 1,228 | 60.4% | 59.2% | -1.2 | 1.4 |
| 4 | 1,228 | 68.9% | 70.7% | +1.7 | 1.3 |
| 5 | 1,228 | 75.5% | 74.6% | -0.9 | 1.2 |
| 6 | 1,228 | 81.3% | 83.1% | +1.9 | 1.1 |
| 7 | 1,228 | 85.9% | 84.9% | -1.1 | 1.0 |
| 8 | 1,228 | 90.2% | 91.5% | +1.4 | 0.8 |
| 9 | 1,228 | 93.7% | 93.0% | -0.7 | 0.7 |
| 10 | 1,228 | 97.1% | 97.6% | +0.4 | 0.4 |

**RB, P(actual >= 8.6 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 20.5% | 20.0% | -0.4 | 1.1 |
| 2 | 1,228 | 24.6% | 24.9% | +0.3 | 1.2 |
| 3 | 1,228 | 30.2% | 30.5% | +0.2 | 1.3 |
| 4 | 1,228 | 38.1% | 39.0% | +0.9 | 1.4 |
| 5 | 1,228 | 44.9% | 43.9% | -1.0 | 1.4 |
| 6 | 1,228 | 52.4% | 54.5% | +2.1 | 1.4 |
| 7 | 1,228 | 59.6% | 59.1% | -0.5 | 1.4 |
| 8 | 1,228 | 67.7% | 69.5% | +1.8 | 1.3 |
| 9 | 1,228 | 75.4% | 75.7% | +0.4 | 1.2 |
| 10 | 1,228 | 84.4% | 84.6% | +0.2 | 1.0 |

**RB, P(actual >= 15.3 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,228 | 6.9% | 6.5% | -0.4 | 0.7 |
| 2 | 1,228 | 8.2% | 8.1% | -0.1 | 0.8 |
| 3 | 1,228 | 9.8% | 9.5% | -0.3 | 0.8 |
| 4 | 1,228 | 14.3% | 15.1% | +0.8 | 1.0 |
| 5 | 1,228 | 19.1% | 19.2% | +0.1 | 1.1 |
| 6 | 1,228 | 23.4% | 24.8% | +1.4 | 1.2 |
| 7 | 1,228 | 28.5% | 28.0% | -0.5 | 1.3 |
| 8 | 1,228 | 35.1% | 34.4% | -0.7 | 1.4 |
| 9 | 1,228 | 43.6% | 45.1% | +1.6 | 1.4 |
| 10 | 1,228 | 59.8% | 60.2% | +0.4 | 1.4 |

**WR, P(actual >= 3.5 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 42.3% | 42.2% | -0.1 | 1.1 |
| 2 | 1,898 | 54.4% | 56.3% | +1.9 | 1.1 |
| 3 | 1,899 | 63.3% | 63.5% | +0.2 | 1.1 |
| 4 | 1,898 | 70.1% | 71.0% | +0.9 | 1.0 |
| 5 | 1,899 | 75.8% | 75.5% | -0.3 | 1.0 |
| 6 | 1,898 | 81.0% | 81.7% | +0.6 | 0.9 |
| 7 | 1,898 | 85.5% | 86.8% | +1.3 | 0.8 |
| 8 | 1,899 | 89.8% | 90.9% | +1.1 | 0.7 |
| 9 | 1,898 | 92.5% | 92.8% | +0.2 | 0.6 |
| 10 | 1,899 | 95.8% | 96.2% | +0.4 | 0.4 |

**WR, P(actual >= 8.1 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 17.8% | 17.9% | +0.1 | 0.9 |
| 2 | 1,898 | 26.7% | 28.0% | +1.3 | 1.0 |
| 3 | 1,899 | 33.3% | 32.6% | -0.6 | 1.1 |
| 4 | 1,898 | 38.2% | 38.1% | -0.1 | 1.1 |
| 5 | 1,899 | 45.7% | 46.1% | +0.3 | 1.1 |
| 6 | 1,898 | 54.1% | 53.3% | -0.8 | 1.1 |
| 7 | 1,898 | 61.2% | 62.6% | +1.4 | 1.1 |
| 8 | 1,899 | 67.5% | 68.0% | +0.5 | 1.1 |
| 9 | 1,898 | 73.8% | 73.8% | -0.0 | 1.0 |
| 10 | 1,899 | 82.4% | 82.3% | -0.1 | 0.9 |

**WR, P(actual >= 14.5 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 1,899 | 5.2% | 4.7% | -0.5 | 0.5 |
| 2 | 1,898 | 9.1% | 9.5% | +0.5 | 0.7 |
| 3 | 1,899 | 12.3% | 12.0% | -0.3 | 0.7 |
| 4 | 1,898 | 14.5% | 14.3% | -0.2 | 0.8 |
| 5 | 1,899 | 19.0% | 18.5% | -0.5 | 0.9 |
| 6 | 1,898 | 25.3% | 25.4% | +0.1 | 1.0 |
| 7 | 1,898 | 31.3% | 32.0% | +0.7 | 1.1 |
| 8 | 1,899 | 36.9% | 37.5% | +0.7 | 1.1 |
| 9 | 1,898 | 43.4% | 43.9% | +0.5 | 1.1 |
| 10 | 1,899 | 55.0% | 54.9% | -0.1 | 1.1 |

**TE, P(actual >= 2.7 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 50.5% | 51.6% | +1.2 | 1.8 |
| 2 | 760 | 57.5% | 56.7% | -0.7 | 1.8 |
| 3 | 760 | 62.9% | 66.1% | +3.1 | 1.7 |
| 4 | 761 | 69.0% | 67.0% | -2.0 | 1.7 |
| 5 | 760 | 74.8% | 77.8% | +2.9 | 1.5 |
| 6 | 760 | 80.0% | 81.2% | +1.1 | 1.4 |
| 7 | 761 | 83.2% | 83.2% | -0.0 | 1.4 |
| 8 | 760 | 86.3% | 86.2% | -0.1 | 1.3 |
| 9 | 760 | 89.4% | 90.5% | +1.1 | 1.1 |
| 10 | 761 | 93.4% | 93.7% | +0.3 | 0.9 |

**TE, P(actual >= 6.2 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 23.3% | 23.7% | +0.3 | 1.5 |
| 2 | 760 | 29.6% | 28.3% | -1.3 | 1.6 |
| 3 | 760 | 35.1% | 35.5% | +0.4 | 1.7 |
| 4 | 761 | 41.1% | 41.0% | -0.1 | 1.8 |
| 5 | 760 | 47.1% | 48.7% | +1.5 | 1.8 |
| 6 | 760 | 53.8% | 53.3% | -0.5 | 1.8 |
| 7 | 761 | 59.5% | 61.4% | +1.9 | 1.8 |
| 8 | 760 | 63.7% | 62.1% | -1.6 | 1.8 |
| 9 | 760 | 70.1% | 70.5% | +0.4 | 1.7 |
| 10 | 761 | 80.6% | 80.2% | -0.4 | 1.4 |

**TE, P(actual >= 11.4 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 761 | 6.8% | 5.8% | -1.0 | 0.8 |
| 2 | 760 | 9.6% | 7.9% | -1.7 | 1.0 |
| 3 | 760 | 13.1% | 13.8% | +0.7 | 1.3 |
| 4 | 761 | 17.0% | 18.1% | +1.1 | 1.4 |
| 5 | 760 | 20.5% | 20.4% | -0.1 | 1.5 |
| 6 | 760 | 24.9% | 23.4% | -1.4 | 1.5 |
| 7 | 761 | 29.1% | 31.1% | +2.1 | 1.7 |
| 8 | 760 | 33.6% | 31.1% | -2.6 | 1.7 |
| 9 | 760 | 41.3% | 42.8% | +1.5 | 1.8 |
| 10 | 761 | 55.5% | 55.8% | +0.3 | 1.8 |

**DST, P(actual >= 3.0 pts)** (p25 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 62.4% | 63.7% | +1.3 | 1.9 |
| 2 | 630 | 70.0% | 67.9% | -2.1 | 1.9 |
| 3 | 630 | 71.9% | 71.3% | -0.6 | 1.8 |
| 4 | 630 | 72.6% | 73.8% | +1.2 | 1.8 |
| 5 | 630 | 74.0% | 75.9% | +1.9 | 1.7 |
| 6 | 630 | 76.5% | 80.0% | +3.5 | 1.6 |
| 7 | 630 | 78.4% | 78.1% | -0.3 | 1.6 |
| 8 | 630 | 81.2% | 83.0% | +1.9 | 1.5 |
| 9 | 630 | 83.5% | 83.8% | +0.4 | 1.5 |
| 10 | 631 | 87.2% | 87.0% | -0.2 | 1.3 |

**DST, P(actual >= 6.0 pts)** (p50 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 37.6% | 36.6% | -1.0 | 1.9 |
| 2 | 630 | 42.7% | 42.2% | -0.4 | 2.0 |
| 3 | 630 | 43.9% | 44.9% | +1.0 | 2.0 |
| 4 | 630 | 47.0% | 47.1% | +0.2 | 2.0 |
| 5 | 630 | 50.1% | 48.9% | -1.2 | 2.0 |
| 6 | 630 | 55.4% | 56.8% | +1.4 | 2.0 |
| 7 | 630 | 57.5% | 57.0% | -0.5 | 2.0 |
| 8 | 630 | 57.9% | 57.9% | +0.1 | 2.0 |
| 9 | 630 | 60.7% | 60.2% | -0.5 | 2.0 |
| 10 | 631 | 69.3% | 70.2% | +0.9 | 1.8 |

**DST, P(actual >= 10.0 pts)** (p75 of actual points)

| Bin | N | Predicted | Realized | Gap (pts) | SE (pts) |
|---|---|---|---|---|---|
| 1 | 631 | 17.6% | 15.5% | -2.1 | 1.4 |
| 2 | 630 | 21.5% | 20.3% | -1.1 | 1.6 |
| 3 | 630 | 22.4% | 22.1% | -0.3 | 1.7 |
| 4 | 630 | 23.1% | 23.3% | +0.2 | 1.7 |
| 5 | 630 | 25.3% | 23.5% | -1.8 | 1.7 |
| 6 | 630 | 29.7% | 31.9% | +2.2 | 1.9 |
| 7 | 630 | 32.6% | 33.3% | +0.7 | 1.9 |
| 8 | 630 | 32.9% | 33.5% | +0.6 | 1.9 |
| 9 | 630 | 34.4% | 34.1% | -0.3 | 1.9 |
| 10 | 631 | 40.4% | 39.8% | -0.6 | 1.9 |

### Verdict against the acceptance rule

| Position | Coverage | Reliability |
|---|---|---|
| QB | pass | pass (max gap 2.4) |
| RB | pass | pass (max gap 2.1) |
| WR | pass | pass (max gap 1.9) |
| TE | pass | pass (max gap 3.1) |
| DST | pass | pass (max gap 3.5) |

**All five positions pass, in sample.** The first release failed QB and DST on their lowest-projection decile,
where realized beat predicted by about 6 points (QB +6.2 and +6.3, DST +6.2 and +5.9 at the two thresholds):
the specified lowest buckets (`<14` for QB, `<5` for DST) were too wide, and actuals do not scale down with
a tiny projection, so scaling a wide bucket's ratios understated what a low-projected player does. Those four
bins are now -0.2, -0.1, +1.3 and -1.0. The smooth fit lets the ratio rise as the projection falls instead
of averaging it over a bucket.

### A stricter check: tables fit on 2014-2022, scored on 2023-2025

The check above measures the tables on the games that built them. This one measures them on seasons they never saw (fewer games per bin, so more sampling noise).

| Pos | N | Below q20 | Above q85 | Max abs gap (pts) | Bins beyond +/-4 (of 30) | Bins beyond 2 SE |
|---|---|---|---|---|---|---|
| QB | 1,792 | 22.5%-22.7% | 14.1% | 6.9 | 4/30 | 1 |
| RB | 2,989 | 18.1% | 13.9% | 10.7 | 8/30 | 9 |
| WR | 4,788 | 20.1%-20.6% | 13.8% | 5.7 | 2/30 | 2 |
| TE | 1,940 | 18.1% | 16.1% | 9.6 | 15/30 | 9 |
| DST | 1,632 | 22.7% | 12.3% | 9.8 | 6/30 | 3 |

Out of time the engine still does not meet the +/-4 rule: 35 of the 150 bins are beyond it, against 45 in the
first release (DST 13 -> 6 of 30, QB 7 -> 4, RB 9 -> 8, WR 2 -> 2, TE 14 -> 15), and the largest gaps are about
the same (QB 9.1 -> 6.9, DST 11.8 -> 9.8, TE 9.6 -> 9.6, but RB 9.9 -> 10.7 and WR 4.6 -> 5.7). Coverage is
within +/-3 for every position (the closest are DST's 12.3% above q85 and QB's 22.7% below q20, both 2.7 off).
This is the era drift the first
release already documented, not the top end: ratios move between eras and seasons, and the lowest RB, WR, TE
and DST projections move the most. Actual points over the engine's mean, all positions, by season (tables
built on all twelve): 1.02, 1.04, 1.00, 0.99, 1.02, 1.02, 1.00, 0.98, 0.98 for 2014-2022, then 1.00, 1.00 and
**0.96** for 2023, 2024 and 2025 (2025: WR 0.92, DST 0.92, QB 0.96). A static table cannot see a season's
scoring level, and 2025 alone moves the simulator's top cash decile (see [SIM.md](SIM.md)). A
recency-weighted version of the tables is the obvious next step; it is not done here.

Season-block cross-validation (the bench; four blocks of three seasons, every row scored by tables that never
saw its block) sits between the two: no failing band, one of 150 reliability bins beyond +/-4 (largest gap
4.4, a DST decile), coverage within 0.6 points of the targets.

### The stored table, at selected projections

`distribution.csv` has 21 rows per position (the knots); this is the table it produces at a few round
projections. The ratio quantiles are `actual / projection`; Floor and Ceiling are the 20th and 85th percentile
in points. The table is evaluated between the 0.5th and 99.5th percentile of each position's projections
(QB 3.8-28.9, RB 3.3-26.3, WR 3.9-21.7, TE 3.1-20.2, DST 1.2-13.4) and held flat in ratio beyond that.

| Pos | Projection | P(0) | q20 | q50 | q85 | q95 | Floor (pts) | Ceiling (pts) |
|---|---|---|---|---|---|---|---|---|
| QB | 8 | 2.8% | 0.13 | 0.93 | 2.29 | 3.16 | 1.0 | 18.3 |
| QB | 12 | 2.1% | 0.28 | 0.99 | 1.78 | 2.22 | 3.4 | 21.4 |
| QB | 16 | 0.5% | 0.63 | 1.00 | 1.57 | 1.92 | 10.0 | 25.2 |
| QB | 20 | 0.2% | 0.58 | 0.94 | 1.41 | 1.75 | 11.7 | 28.3 |
| QB | 24 | 0.0% | 0.60 | 0.90 | 1.30 | 1.53 | 14.3 | 31.1 |
| QB | 28 | 0.0% | 0.53 | 0.83 | 1.16 | 1.33 | 14.8 | 32.5 |
| RB | 4 | 9.0% | 0.11 | 0.71 | 2.59 | 4.13 | 0.4 | 10.4 |
| RB | 8 | 3.4% | 0.29 | 0.80 | 1.86 | 2.72 | 2.3 | 14.9 |
| RB | 12 | 0.6% | 0.41 | 0.83 | 1.67 | 2.29 | 4.9 | 20.1 |
| RB | 16 | 0.1% | 0.47 | 0.84 | 1.53 | 1.96 | 7.5 | 24.4 |
| RB | 20 | 0.0% | 0.46 | 0.85 | 1.46 | 1.88 | 9.3 | 29.2 |
| RB | 26 | 0.0% | 0.52 | 0.86 | 1.28 | 1.53 | 13.5 | 33.3 |
| WR | 4 | 22.4% | 0.00 | 0.52 | 1.90 | 3.25 | 0.0 | 7.6 |
| WR | 8 | 9.2% | 0.29 | 0.79 | 1.80 | 2.64 | 2.3 | 14.4 |
| WR | 12 | 2.9% | 0.41 | 0.89 | 1.77 | 2.39 | 4.9 | 21.3 |
| WR | 16 | 1.2% | 0.48 | 0.88 | 1.67 | 2.16 | 7.7 | 26.7 |
| WR | 20 | 0.3% | 0.50 | 0.89 | 1.54 | 1.97 | 9.9 | 30.7 |
| WR | 24 | 0.3% | 0.50 | 0.90 | 1.46 | 1.90 | 11.9 | 35.1 |
| TE | 4 | 20.1% | 0.15 | 0.72 | 2.13 | 3.22 | 0.6 | 8.5 |
| TE | 7 | 9.1% | 0.30 | 0.78 | 1.89 | 2.67 | 2.1 | 13.2 |
| TE | 10 | 4.0% | 0.36 | 0.83 | 1.64 | 2.30 | 3.6 | 16.4 |
| TE | 13 | 2.5% | 0.38 | 0.80 | 1.54 | 2.12 | 4.9 | 20.0 |
| TE | 16 | 2.0% | 0.40 | 0.79 | 1.46 | 1.89 | 6.4 | 23.4 |
| TE | 19 | 2.0% | 0.43 | 0.75 | 1.39 | 1.62 | 8.2 | 26.3 |
| DST | 3 | 6.5% | 0.16 | 1.40 | 3.61 | 5.69 | 0.5 | 10.8 |
| DST | 5 | 5.9% | 0.24 | 1.00 | 2.23 | 3.13 | 1.2 | 11.2 |
| DST | 7 | 4.1% | 0.29 | 0.88 | 1.90 | 2.65 | 2.0 | 13.3 |
| DST | 9 | 2.6% | 0.32 | 0.78 | 1.58 | 2.22 | 2.9 | 14.3 |
| DST | 11 | 2.1% | 0.29 | 0.70 | 1.39 | 1.91 | 3.2 | 15.3 |
| DST | 13 | 2.1% | 0.34 | 0.66 | 1.16 | 1.58 | 4.4 | 15.1 |

The full set of quantile levels is in `distribution.csv`.

## Using it

```
dfs model fetch      # download / refresh the cache (data/model_cache/, gitignored)
dfs model train      # rebuild models/um/ (about 2 minutes). Once a season, or on demand.
dfs model backtest   # recompute and print the holdout and calibration tables (--saved: print the shipped one)
dfs model info       # artifact version, training seasons, chosen method, metrics, scikit-learn version
python tests/model/bench_distribution.py   # score the table-building remedies (--help for modes and --sim)
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
- **Where the engine is weakest:** drift between eras and seasons (see the stricter check): the tables pool
  twelve seasons, so a season that scores low or high as a whole (2025 ran 4% under them) shifts every
  probability and the engine cannot see it. And the thinnest cells: beyond the 99.5th percentile of a
  position's projections the ratio is held flat, and the thinnest gated band (RB 26+, 78 games) is inside 5%
  by 0.4 points, with a standard error of 4.6%. The lowest-projection decile, the first release's other weak
  spot, passes in sample (it still moves with the era out of time). The CDF ends a little above the 99th
  percentile, so P(>= T) is 0 beyond about 1.5x the projection for a QB at the top of the range: right for
  the data (no QB projected 27+ has scored 46 points; the first release said 5% for a QB projected 30), but a
  4x-salary line above that reads 0.0%. Treat Floor and Ceiling as wider than they look.
- **No injury or depth-chart features.** A player returning from injury, or about to lose snaps, looks like
  his trailing games say. A rookie or newcomer with no prior games gets no UM projection at all.
- **Not tuned, with one exception.** The hyper-parameters are the fixed ones, and the P(0) bucket edges are the
  specified ones. The spline's five knots, the 21 stored knots and the 99.5th-percentile data edge were chosen
  on the bench, on the same rows they are scored on; cross-validation by season block checks that the top-end
  pass is not an artefact of that, but it is not a held-out test.
- DST rank signal is weak for every method.
