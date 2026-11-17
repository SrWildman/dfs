# Plan S, check 2: the QB-receiver correlation, re-checked (2026-11-09)

*The script (`sim/qbwr_check.py`) was removed in removal batch F2 (Nov 2026); it is last on main at `42e3362`.*

**Verdict: the shipped correlation table stays as it is.** The method is not what keeps QB1-WR1 near +0.31: every
residual method lands between +0.32 and +0.35 when fitted on 2014-2022. Only raw points reach +0.39, and raw points mix
in the projections' own correlation. The friend's hand-set 0.45 matches the held-out joint-90th count best, but only by
1.6 SE on the table margins and 1.0 SE on exact margins, short of the 2 SE bar. The held-out pair log-likelihood rejects
it clearly: −18.4 per 1,000 pairs, SE 4.6. Applied to WR2 as well, 0.45 overshoots that pair's joint count.

## Method

- **Code:** `src/dfs/sim/qbwr_check.py`. Run it with `python -m dfs.sim.qbwr_check --frame <parquet of sim.pipeline.load_frame(2026)> --boot 1000 --save <dir>`.
- **Tests:** `tests/sim/test_qbwr_check.py`. One of them checks the analytic joint tail against `simulate_lineups` itself.
- **Pairs:** each team-game's QB1 with WR1, WR2, TE1 and RB1 (`role` in the UM frame), and QB1 with the opposing DST.
- **Data:** 2014-2026 out-of-fold UM projections. 2026 runs through Week 9.
- **Methods:**

  | Method | What it is |
  |---|---|
  | `surprise` | The shipped method: Pearson on normal scores, each actual pushed through the player's predicted distribution |
  | `surprise_shrunk` | As shipped, with the prior at 200 |
  | `resid_pearson` | Pearson on the points residuals |
  | `resid_spearman` | Spearman on the points residuals |
  | `z_spearman` | Spearman on the normal scores |
  | `points` | Pearson on raw DK points |

  Spearman values go into the copula as `2 sin(pi rho / 6)`. Intervals are 90%, from 1,000 game-level bootstrap resamples.
- **The decider, held out:** fit every candidate on 2014-2022. On 2023-2026, count the team-games where both players beat their own predicted 90th (and 95th) percentile. Compare that count with `n * P(Z1 > c, Z2 > c; rho)`, which is what the simulator gives for two players. The count's SE comes from a game bootstrap.
- **Two views:**
  - **Table margins:** percentiles come from the 2014-2022 tables, the same way the simulator scores a held-out game.
  - **Exact margins:** each margin is made exactly uniform by rank within the held-out role, which takes the tables out of the comparison.
- **Ship bar:** a candidate replaces the constant only if it cuts the calibration miss by more than 2 SE.
- **Second test:** the held-out Gaussian-copula log-likelihood of every pair. This test is the sensitive one.

## Estimates (90% bootstrap intervals)

| Pair | Period | n | surprise | shrunk (shipped) | resid Pearson | resid Spearman | z Spearman | points |
|---|---|---|---|---|---|---|---|---|
| QB1-WR1 | 2014-22 | 4,506 | .335 (.315, .358) | .322 | .353 (.330, .375) | .332 | .333 | .390 (.369, .412) |
| QB1-WR1 | 2023-26 | 1,818 | .295 (.258, .331) | .267 | .321 (.286, .355) | .292 | .289 | .315 (.277, .352) |
| QB1-WR2 | 2014-22 | 4,393 | .286 (.262, .309) | .274 | .309 | .288 | .289 | .334 |
| QB1-WR2 | 2023-26 | 1,755 | .300 (.262, .336) | .270 | .323 | .292 | .298 | .363 |
| QB1-TE1 | 2014-22 | 3,978 | .262 (.238, .287) | .250 | .284 | .254 | .258 | .304 |
| QB1-TE1 | 2023-26 | 1,638 | .272 (.230, .315) | .244 | .282 | .268 | .278 | .305 |
| QB1-RB1 | 2014-22 | 4,463 | .068 (.043, .094) | .065 | .064 | .071 | .070 | .070 |
| QB1-RB1 | 2023-26 | 1,806 | .090 (.050, .132) | .081 | .073 | .090 | .091 | .080 |
| QB1-opp DST | 2014-22 | 4,511 | −.379 (−.401, −.356) | −.365 | −.324 | −.321 | −.369 | −.406 |
| QB1-opp DST | 2023-26 | 1,798 | −.380 (−.414, −.345) | −.345 | −.324 | −.336 | −.382 | −.422 |

The committed table (`correlation.load_correlations()`) has QB1-WR1 at +0.306, WR2 at +0.275, TE1 at +0.248, RB1 at +0.070
and the opposing DST at −0.342.

**Does the surprise method understate?** By a little, if anything:

- It sits about 0.02 under the Pearson of the residuals.
- On both data and ranks it agrees with the Spearman methods within 0.01-0.02.
- On raw points the figure is higher (+0.39 for 2014-22). Raw points also carry the correlation the projections themselves share: a good offence projects high for both players. The cited 0.4-0.5 measures this.
- The simulator draws each player around his own projection, so the residual figure is the right input. A points figure would count the shared projection twice.

## Calibration: both players above their 90th percentile, 2023-2026

| Pair | View | Observed | Shipped method (rho, expected, miss in SE) | Best alternative (rho, expected, gain over shipped in SE) | Hand-set / cited |
|---|---|---|---|---|---|
| QB1-WR1 | table margins | 53 of 1,818 | .322 -> 41.2 (+1.6) | points .390 -> 47.5 (+0.9) | .45 -> 53.5, gain +1.57 SE |
| QB1-WR1 | exact margins | 51 | 41.2 (+1.4) | points 47.5 (+0.9) | 53.5, gain +1.03 SE |
| QB1-WR2 | table margins | 41 of 1,755 | .274 -> 35.8 (+0.8) | points .334 -> 40.8 (+0.8) | .45 -> 51.7 (miss −1.7 SE), gain −0.87 SE |
| QB1-WR2 | exact margins | 45 | 35.8 (+1.4) | points 40.8 (+0.7) | 51.7, gain +0.38 SE |
| QB1-TE1 | table margins | 39 of 1,638 | .250 -> 31.7 (+1.2) | points .304 -> 35.7 (+0.7) | .30 -> 35.4, gain +0.6 SE |
| QB1-TE1 | exact margins | 45 | 31.7 (+2.0) | points 35.7 (+0.6) | 35.4, gain +0.6 SE |
| QB1-RB1 | table margins | 20 of 1,806 | .065 -> 21.9 (−0.4) | none better | .10 -> 24.1, gain −0.45 SE |
| QB1-opp DST | table margins | 4 of 1,798 | −.365 -> 3.7 (+0.1) | none better | −.46 -> 1.9, gain −0.94 SE |

At the 95th percentile, QB1-WR1 had 23 joint hits against 13.8 expected (miss +2.0 SE). Hand-set 0.45 expects 19.5, a gain of
+1.2 SE. No other pair misses by more than 1.5 SE at the 95th.

**The one real signal.** The QB1-WR1 joint-boom count runs high: 53 against 41, a 29% excess, at 1.6 SE.

- Most of that excess is dependence, not the margins. Under exact margins the count is 51 against 41 (25%).
- So the tables account for 2 of the 12 extra hits, about a sixth. The earlier finding was that about 15 of 37 points of stack-boom excess came from the marginals. Here the marginals' share is smaller, but they do not explain it all.

**Why not move the constant anyway?**

- No candidate clears 2 SE.
- The pair log-likelihood weighs every QB1-WR1 game, not only the corner where both boom. It says 0.45 fits the whole joint distribution worse than 0.32:

  | QB1-WR1 candidate | Log-likelihood vs shipped, per 1,000 pairs (SE) |
  |---|---|
  | hand-set 0.45 | −18.4 (4.6) |
  | points 0.39 | −5.9 (2.2) |
  | resid Pearson | −1.7 (1.0) |
  | committed table | +0.4 (0.5) |
  | independence | −43.8 (8.1) |

- 0.45 also fails on QB1-WR2, where it overshoots the joint count (51.7 against 41) and loses 16.5 per 1,000 (SE 6.3).

What fits both facts is tail dependence: QB1 and WR1 boom together more often than a Gaussian copula at the body's
correlation implies, while the body itself sits at about 0.32. A single Gaussian rho cannot do both. If this signal grows,
the follow-up is a t-copula, or a tail-only stack bump on the top QB-WR1 draws, not a larger rho. Neither is built here.

## What this does not test

- **Week 9 standings:** not used anywhere in this check. The frame runs through 2026 Week 9 for actual points.
- **Three-player joint booms (QB + WR1 + WR2):** the stack-boom table in `tail_check` covers those. This check is pairwise.
- **Projections other than UM's (CalPts, TFFB):** not tested. The shipped table is fitted on UM's out-of-fold projections, the same frame used here.
