# Plan S, check 1: per-player volatility (2026-11-09)

*The script (`sim/volatility_check.py`) was removed in removal batch F2 (Nov 2026); it is last on main at `42e3362`.*

**Verdict: nothing clears the bar. No `[sim] player_volatility` switch ships, so no Flashback was run (Sam, 2026-11-09:
"don't ship" the one near miss).**

## The question

The distribution engine gives two players of one position at the same projection the same distribution (per position,
per projection: `dfs.model.distribution`). A friend's tool gives each player his own spread. Does a player-level spread
term, read from the player's own history, beat our tables at the tails?

## Method

Code: `src/dfs/sim/volatility_check.py` (`python -m dfs.sim.volatility_check --frame <parquet of sim.pipeline.load_frame(2026)>`);
tests: `tests/sim/test_volatility_check.py`.

- **Data.** Every out-of-fold UM player-game of 2014-2026 (2026 through Week 9; QB, RB, WR, TE; the projection is the
  UM out-of-fold prediction, the same frame the correlation table is fitted on). Tables are refitted on 2014-2022 only
  (`tail_check.train_tables`), so 2023-2026 is held out for the tables as well as for the term.
- **Features, from the player's previous games only** (up to 8, at least 3, across the season boundary; the game itself
  never counts; a test checks that changing the game's own stats does not move its features):
  `adot` (receiving air yards per target; a QB: passing air yards per attempt), `ay_share` (mean share of the team's air
  yards), `tgt_sd` (SD of targets; a QB: of pass attempts), `td_share` (share of his DK points from touchdowns),
  `snap_trend` (slope of offensive snap share over his last 4 games, nflverse snap counts), and one extra, `pts_cv`
  (coefficient of variation of his DK points). Coverage 97.5-98.5% of held-out rows; a missing feature means no stretch.
- **The term.** `delta_i = clip(beta . x_i, ±0.5)` stretches the player's ratio quantiles above the median by `1 + delta_i`
  (`marginal.stretch_upper`, the hook the tail study used per role). `x` is standardised on the training rows. Each feature
  alone, and all of them together (`all`), per position, fitted on 2014-2022 by the pinball loss at the 90th / 95th / 99th.
- **The bar** (house bar, pre-registered in the module): on 2023-2026, the pinball loss at the 90th + 99th better by more
  than 2 SE of the **weekly** paired difference AND better in most weeks, AND calibration better by more than 2
  week-bootstrap SE. Calibration = the chi-square, over terciles of the feature (for `all`, of the fitted spread), of the
  share of actuals above the predicted 90th against 10%.

## Results (held out, 2023-2026)

Pinball gain in points per player-game (positive = the term is better; base pinball 1.6-1.9), with its weekly SE and z;
calibration chi-square at the 90th, base -> with the term (its gain's SE in brackets).

| Pos | Feature | beta | Pinball gain (SE) | z | Weeks better | Chi-square 90th (SE of gain) | Clears |
|---|---|---|---|---|---|---|---|
| QB | adot | +0.011 | -0.0002 (0.0011) | -0.2 | 54% | 2.6 -> 3.0 (0.9) | no |
| QB | tgt_sd | -0.023 | -0.0010 (0.0025) | -0.4 | 57% | 3.5 -> 1.9 (1.7) | no |
| QB | td_share | -0.022 | -0.0020 (0.0026) | -0.8 | 54% | 2.4 -> 1.6 (1.2) | no |
| QB | snap_trend | +0.006 | +0.0003 (0.0006) | +0.4 | 49% | 2.4 -> 2.8 (0.5) | no |
| QB | pts_cv | +0.022 | +0.0011 (0.0020) | +0.6 | 52% | 3.1 -> 2.5 (1.2) | no |
| QB | all | | -0.0003 (0.0058) | -0.1 | 52% | 6.9 -> 2.7 (2.9) | no |
| RB | adot | -0.047 | -0.0030 (0.0045) | -0.7 | 44% | 5.2 -> 7.5 (3.0) | no |
| RB | ay_share | -0.058 | -0.0089 (0.0058) | -1.6 | 37% | 5.1 -> 9.9 (3.6) | no |
| RB | tgt_sd | -0.028 | -0.0028 (0.0023) | -1.2 | 40% | 4.8 -> 6.0 (1.9) | no |
| RB | td_share | -0.014 | +0.0011 (0.0015) | +0.7 | 60% | 6.5 -> 4.9 (1.7) | no |
| RB | snap_trend | +0.095 | +0.0095 (0.0056) | +1.7 | 59% | 11.4 -> 5.3 (4.0) | no |
| RB | pts_cv | +0.042 | -0.0081 (0.0033) | -2.4 | 41% | 10.6 -> 13.6 (2.0) | no |
| RB | all | | +0.0033 (0.0077) | +0.4 | 48% | 11.1 -> 4.4 (5.2) | no |
| WR | adot | +0.029 | +0.0004 (0.0018) | +0.2 | 52% | 6.9 -> 6.0 (1.6) | no |
| WR | ay_share | +0.022 | +0.0016 (0.0016) | +1.0 | 51% | 13.9 -> 10.3 (2.1) | no |
| WR | tgt_sd | +0.011 | +0.0011 (0.0008) | +1.3 | 52% | 7.2 -> 6.1 (1.4) | no |
| WR | td_share | +0.001 | -0.0000 (0.0000) | -0.6 | 52% | 6.7 -> 6.7 | no |
| WR | snap_trend | +0.055 | +0.0045 (0.0030) | +1.5 | 56% | 10.7 -> 8.5 (4.6) | no |
| WR | pts_cv | -0.001 | +0.0000 (0.0001) | +0.3 | 52% | 7.8 -> 7.4 (0.4) | no |
| **WR** | **all** | | **+0.0083 (0.0037)** | **+2.3** | **67%** | **15.3 -> 11.0 (6.0)** | **no (calibration +0.7 SE)** |
| TE | adot | +0.028 | +0.0015 (0.0024) | +0.6 | 56% | 3.4 -> 3.0 (2.3) | no |
| TE | ay_share | +0.056 | +0.0058 (0.0047) | +1.2 | 52% | 8.8 -> 4.1 (3.2) | no |
| TE | tgt_sd | +0.005 | -0.0002 (0.0004) | -0.5 | 37% | 2.2 -> 2.0 (0.9) | no |
| TE | td_share | -0.083 | +0.0078 (0.0059) | +1.3 | 59% | 4.2 -> 1.9 (2.4) | no |
| TE | snap_trend | +0.071 | +0.0077 (0.0054) | +1.4 | 59% | 4.1 -> 2.8 (3.6) | no |
| TE | pts_cv | -0.029 | +0.0023 (0.0022) | +1.0 | 41% | 2.0 -> 3.2 (2.0) | no |
| TE | all | | +0.0217 (0.0132) | +1.6 | 59% | 12.0 -> 1.7 (5.9) | no |

`all` coefficients, in feature order (adot, ay_share, tgt_sd, td_share, snap_trend, pts_cv; a QB has no ay_share):
QB +0.013, -0.050, -0.028, +0.004, +0.040; RB -0.025, -0.031, -0.007, -0.034, +0.086, +0.007; WR +0.030, +0.008, +0.011,
-0.007, +0.054, -0.012; TE +0.024, +0.070, -0.023, -0.096, +0.074, +0.017.

**Is there a raw spread effect at all?** Yes, a small one, and the tables mostly carry it already. Share of held-out
actuals above the predicted 90th percentile by tercile of the feature (low / mid / high), against 10%:

| | WR | TE | RB |
|---|---|---|---|
| air-yards share | 7.5 / 8.9 / 10.2% | 8.4 / 11.3 / 12.5% | 9.0 / 9.8 / 8.4% |
| aDOT | 8.6 / 8.9 / 9.4% | 10.0 / 11.5 / 11.7% | 9.1 / 9.6 / 8.3% |
| snap trend | 7.8 / 9.7 / 9.5% | 9.6 / 10.7 / 11.9% | 8.0 / 8.3 / 11.0% |
| the fitted `all` spread | 7.3 / 10.1 / 9.8% (with it: 8.4 / 10.1 / 8.4%) | 8.2 / 10.7 / 13.3% (with it: 10.3 / 10.7 / 11.2%) | 6.8 / 8.9 / 10.3% (with it: 9.2 / 9.2 / 8.7%) |

A receiver with a big share of his team's air yards beats his 90th percentile a little more often (WR 10.2% against 7.5%
for the low tercile, TE 12.5% against 8.4%), the direction the "deep-threat WR gets a wider spread" idea predicts. But the
fitted stretch is small (WR `delta` sd 0.06, 90% of players within -0.11 / +0.09; TE sd 0.13), the pinball gain is
0.4% of the loss for WR (1.4% for TE), and the calibration chi-square, which is dominated by the held-out WR level being
under 10% overall (the tables run slightly heavy at the top held out: the tail study's finding), does not fall by 2 SE.

## The near miss: the combined WR term

`all` for WR clears the pinball half (+0.0083 points, z 2.3, better in 67% of weeks) and not the calibration half (chi-square
15.3 -> 11.0, 0.7 SE). By season: 2023 +0.020 (SE 0.007), 2024 -0.007 (0.006), 2025 +0.010 (0.006), 2026 +0.014 (0.010):
ahead in 3 of 4. It was one of 28 terms tested; one z above 2 in 28 tries is about what chance alone gives (expected ~0.6
at a one-sided 2.3%). **Sam (2026-11-09): don't ship it.** Rerun at a later close: the module reruns in about a minute
from a cached frame.

## What this does not test

- A spread term below the median (busts): the question was the tails a GPP is built on, so the stretch is upper-only.
- Route participation and target quality beyond air yards (not in the public data the repo loads).
- CalPts / TFFB as the projection: the frame is UM's out-of-fold projections (the only ones with 12 seasons of history).
  The live sheet's CalPts could carry more or less of the player-level spread than UM does.
