# Game script: spread and implied total, tested

Fit on 2014-2022 (38,332 player-games), judged on 2023-2025 (12,935). Normal scores as in `docs/SIM.md`; spread is team-relative (positive = this team favoured); buckets BD/SD/SF/BF are big dog (+7 or more), small dog, small favourite, big favourite; the implied total is cut at the training terciles (21.0, 24.5).

## 1. Does a pair's correlation move with the spread or the implied total?

Slope of the cross-product of the two z-scores on the covariate, game-clustered t. With 110 pairs a few exceed 1.96 by chance, so the same scan is repeated with the games' contexts shuffled among games of the same season.

| Conditioned on | Pairs | |t| > 1.96, real contexts | shuffled: mean | shuffled: 95th pct | largest |t|, real | largest |t|, shuffled 95th pct |
|---|---|---|---|---|---|---|
| closing spread | 110 | 11 | 5.8 | 10 | 2.81 | 3.43 |
| team implied total | 110 | 13 | 5.6 | 9 | 3.58 | 3.46 |

## 2. The named pairs, in the training seasons

Correlation of the surprises (unshrunk) by bucket of role A's team; `p adj.` is the share of the shuffled scans whose LARGEST |t| over all pairs is at least this pair's.

| Pair | n | pooled r | spr:BD | spr:SD | spr:SF | spr:BF | imp:low | imp:mid | imp:high | t (spread) | t (implied) | p adj. (spread) | p adj. (implied) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RB1 with his own DST | 4585 | +0.061 | +0.093 | +0.068 | +0.050 | -0.006 | +0.085 | +0.086 | +0.008 | -0.5 | -1.0 | 1.00 | 1.00 |
| RB1 with the opposing DST | 4585 | -0.236 | -0.195 | -0.246 | -0.247 | -0.188 | -0.190 | -0.280 | -0.219 | -1.1 | -2.1 | 1.00 | 0.98 |
| QB1 with WR1 | 4506 | +0.333 | +0.314 | +0.343 | +0.327 | +0.338 | +0.339 | +0.336 | +0.325 | -0.2 | +0.8 | 1.00 | 1.00 |
| QB1 with WR2 | 4393 | +0.284 | +0.243 | +0.272 | +0.302 | +0.302 | +0.248 | +0.272 | +0.319 | +1.7 | +3.0 | 1.00 | 0.24 |
| QB1 with TE1 | 3978 | +0.260 | +0.255 | +0.254 | +0.248 | +0.291 | +0.228 | +0.267 | +0.267 | +1.2 | +2.0 | 1.00 | 0.99 |
| QB1 with the opposing WR1 (bring-back) | 4505 | +0.085 | +0.051 | +0.085 | +0.108 | +0.078 | +0.074 | +0.102 | +0.085 | +1.4 | +0.5 | 1.00 | 1.00 |
| WR1 with WR2 (cannibalisation) | 4511 | +0.003 | +0.065 | +0.007 | -0.020 | -0.012 | +0.031 | -0.001 | -0.017 | -1.6 | -1.3 | 1.00 | 1.00 |
| favourite's RB1 with the dog's WR1 | 4580 | +0.020 | -0.001 | +0.027 | +0.025 | +0.026 | +0.011 | +0.032 | +0.020 | +0.4 | +0.2 | 1.00 | 1.00 |
| favourite's RB1 with the dog's WR2 | 4461 | +0.008 | -0.062 | +0.006 | +0.037 | +0.023 | -0.045 | +0.014 | +0.050 | +1.8 | +2.3 | 1.00 | 0.90 |
| favourite's RB1 with the dog's TE1 | 4028 | +0.041 | +0.032 | +0.057 | +0.025 | +0.067 | +0.044 | +0.044 | +0.041 | +0.3 | +0.5 | 1.00 | 1.00 |
| QB1 with the opposing RB1 | 4462 | +0.025 | +0.040 | +0.033 | +0.040 | -0.011 | +0.042 | +0.013 | +0.033 | -0.2 | +0.1 | 1.00 | 1.00 |

## 3. Out of sample: does conditioning beat the pooled value?

Log-likelihood gain of the bucket correlation (shrunk toward the pooled value, prior 200) over the pooled value, on the 2023-2025 pairs; the control fits the same table on shuffled training contexts.

| Conditioned on | Pairs | Gain per 1,000 pair-obs | SE | Shuffled contexts: mean | 95th pct | Share of shuffles >= real |
|---|---|---|---|---|---|---|
| closing spread | all 110 | -0.053 | 0.074 | -0.224 | -0.097 | 0.02 |
| closing spread | 11 named | +0.265 | 0.149 | -0.047 | +0.222 | 0.02 |
| closing spread | 11 flagged in training | +0.067 | 0.328 |  |  |  |
| implied total | all 110 | +0.023 | 0.078 | -0.180 | -0.040 | 0.01 |
| implied total | 11 named | +0.078 | 0.171 | +0.004 | +0.315 | 0.34 |
| implied total | 13 flagged in training | -0.030 | 0.266 |  |  |  |

## 4. Lineup level (the shipping bar)

Better than the pooled table by (percent of its loss; positive = better; t over weeks), all lineups:

| Marginals | Conditional table | Pinball | Brier cash | Brier top end |
|---|---|---|---|---|
| shipped | spread | +0.0031% (t +1.4) | -0.0057% (t -1.8) | -0.0030% (t -0.3) |
| shipped | spread_named | +0.0035% (t +1.8) | +0.0025% (t +0.9) | -0.0024% (t -0.3) |
| shipped | implied | +0.0004% (t +0.2) | -0.0034% (t -1.1) | -0.0052% (t -0.8) |
| shipped | shape_tshare | +0.0022% (t +0.4) | +0.0008% (t +0.1) | -0.0129% (t -0.7) |
| shipped | shape_all | +0.0026% (t +0.3) | +0.0077% (t +0.7) | -0.0213% (t -0.6) |
| train-only | spread | +0.0039% (t +1.7) | -0.0034% (t -1.2) | +0.0074% (t +0.7) |
| train-only | spread_named | +0.0049% (t +2.4) | +0.0025% (t +0.9) | -0.0001% (t -0.0) |
| train-only | implied | +0.0016% (t +0.7) | -0.0003% (t -0.1) | -0.0040% (t -0.6) |
| train-only | shape_tshare | +0.0029% (t +0.6) | +0.0004% (t +0.1) | -0.0200% (t -0.9) |
| train-only | shape_all | +0.0033% (t +0.4) | +0.0065% (t +0.6) | -0.0372% (t -0.9) |

Pinball loss by lineup family:

| Marginals | Conditional table | random | stack | rb_dst | game | qb_te | qb_rb |
|---|---|---|---|---|---|---|---|
| shipped | spread | -0.0007% (t -0.2) | +0.0039% (t +0.6) | +0.0133% (t +2.7) | +0.0040% (t +0.5) | -0.0025% (t -0.6) | +0.0009% (t +0.1) |
| shipped | spread_named | +0.0026% (t +1.2) | +0.0030% (t +0.6) | +0.0086% (t +2.5) | +0.0072% (t +1.1) | +0.0004% (t +0.1) | -0.0009% (t -0.3) |
| shipped | implied | +0.0020% (t +0.5) | -0.0013% (t -0.2) | -0.0004% (t -0.1) | +0.0047% (t +0.7) | +0.0010% (t +0.2) | -0.0036% (t -0.8) |
| shipped | shape_tshare | +0.0042% (t +1.1) | -0.0011% (t -0.1) | -0.0035% (t -1.2) | +0.0055% (t +0.5) | +0.0064% (t +0.9) | +0.0016% (t +0.2) |
| shipped | shape_all | +0.0116% (t +1.6) | +0.0030% (t +0.1) | -0.0038% (t -0.7) | +0.0001% (t +0.0) | +0.0101% (t +0.7) | -0.0053% (t -0.5) |
| train-only | spread | +0.0001% (t +0.0) | +0.0062% (t +1.0) | +0.0107% (t +2.4) | +0.0059% (t +0.7) | +0.0012% (t +0.3) | -0.0009% (t -0.1) |
| train-only | spread_named | +0.0028% (t +1.4) | +0.0055% (t +1.1) | +0.0071% (t +2.4) | +0.0117% (t +1.5) | +0.0018% (t +0.6) | +0.0002% (t +0.1) |
| train-only | implied | +0.0030% (t +0.8) | +0.0024% (t +0.4) | -0.0006% (t -0.1) | +0.0062% (t +0.8) | +0.0010% (t +0.3) | -0.0027% (t -0.6) |
| train-only | shape_tshare | +0.0066% (t +1.7) | +0.0018% (t +0.1) | -0.0031% (t -1.0) | +0.0052% (t +0.5) | +0.0070% (t +1.1) | -0.0003% (t -0.1) |
| train-only | shape_all | +0.0123% (t +1.6) | +0.0014% (t +0.1) | -0.0054% (t -0.9) | +0.0047% (t +0.2) | +0.0113% (t +0.8) | -0.0046% (t -0.5) |

## 4b. Team shape: who the players are, as a modifier on the pair

Terciles (cut on the training seasons) of each modifier, three previous games only: target share of the receiver or back, the QB's carry share, the team's pass rate over expected, target concentration. Route participation is not in the weekly data this repo loads; target share stands in for it. Correlations are unshrunk; `p adj.` is the share of shuffled scans (the feature shuffled among rows of the same role and season) whose largest |t| over all 22 tests is at least this test's.

| Pair, by modifier | n | pooled | low | mid | high | t | p adj. |
|---|---|---|---|---|---|---|---|
| QB1 with RB1, by the back's target share | 4463 | +0.069 | +0.021 | +0.082 | +0.102 | +1.3 | 0.99 |
| QB1 with TE1, by the tight end's target share | 3978 | +0.260 | +0.189 | +0.283 | +0.310 | +2.9 | 0.07 |
| QB1 with WR1, by his target share | 4506 | +0.333 | +0.281 | +0.327 | +0.390 | +3.3 | 0.03 |
| QB1 with WR1, by target concentration | 4506 | +0.333 | +0.285 | +0.314 | +0.397 | +2.7 | 0.14 |
| QB1 with RB1, by the QB's rush share | 4463 | +0.069 | +0.109 | +0.039 | +0.058 | -0.8 | 1.00 |
| QB1 with WR1, by the team's PROE | 4506 | +0.333 | +0.328 | +0.342 | +0.332 | +0.2 | 1.00 |
| WR1 with WR2, by target concentration | 4511 | +0.003 | +0.017 | -0.014 | +0.008 | -0.1 | 1.00 |

Held-out gain of each named test (2023-2025), then the totals against the shuffled-feature control:

| Pair, by modifier | n | Held-out gain per 1,000 | SE |
|---|---|---|---|
| QB1 with RB1, by the QB's rush share | 1566.0 | -0.812 | 0.589 |
| QB1 with RB1, by the back's target share | 1566.0 | -0.124 | 0.683 |
| QB1 with TE1, by the tight end's target share | 1413.0 | +4.468 | 1.299 |
| QB1 with WR1, by target concentration | 1580.0 | +0.987 | 1.370 |
| QB1 with WR1, by the team's PROE | 1580.0 | -0.221 | 0.426 |
| QB1 with WR1, by his target share | 1580.0 | +0.267 | 1.410 |
| WR1 with WR2, by target concentration | 1558.0 | -0.236 | 0.262 |

| Tests | Gain per 1,000 | SE | Shuffled features: mean | 95th pct | Share of shuffles >= real |
|---|---|---|---|---|---|
| all 22 tests | +0.454 | 0.222 | +0.012 | +0.237 | 0.000 |
| receiver target share (6 tests) | +1.284 | 0.488 | +0.050 | +0.581 | 0.000 |

### The ruling

| Conditional table | Pinball t (shipped / train-only) | Control share | pinball > 2 SE, both marginal sets | no Brier loss beyond 1 SE | gain > 0 and control share <= 0.0083 | Ships |
|---|---|---|---|---|---|---|
| spread | +1.4 / +1.7 | 0.017 | NO | NO | NO | NO |
| spread_named | +1.8 / +2.4 | 0.017 | NO | yes | NO | NO |
| implied | +0.2 / +0.7 | 0.007 | NO | NO | yes | NO |
| shape_tshare | +0.4 / +0.6 | 0.000 | NO | yes | yes | NO |
| shape_all | +0.3 / +0.4 | 0.000 | NO | yes | yes | NO |

## 5. What a bring-back adds, by the stacked team's spread

Lift = P(stack + the other side's player reaches the tail) / P(stack + an unrelated player of the same role and spread bucket does), 2014-2025 actual DK points; above 1 the bring-back helps, at 1 it adds nothing, below 1 it hurts. 90% interval from resampling games; the top-1% hit counts are in brackets, and are small.

| Stack | Plus the other side's | Tail of S + B | BD | SD | SF | BF | all |
|---|---|---|---|---|---|---|---|
| QB+WR1+WR2 | WR1 | top 10% | 1.16 [0.98, 1.36] (57) | 1.02 [0.95, 1.09] (199) | 1.04 [0.98, 1.11] (245) | 1.05 [0.97, 1.12] (118) | 1.05 [1.00, 1.09] (619) |
| QB+WR1+WR2 | WR1 | top 5% | 1.03 [0.74, 1.32] (23) | 1.22 [1.10, 1.34] (120) | 1.20 [1.10, 1.32] (142) | 1.10 [0.95, 1.23] (63) | 1.17 [1.10, 1.24] (348) |
| QB+WR1+WR2 | WR1 | top 1% | 1.93 [1.15, 2.50] (7) | 1.44 [1.17, 1.80] (30) | 1.35 [1.06, 1.71] (32) | 1.44 [0.94, 2.01] (15) | 1.44 [1.24, 1.67] (84) |
| QB+WR1+WR2 | TE1 | top 10% | 1.22 [1.04, 1.40] (51) | 1.10 [1.03, 1.17] (188) | 1.03 [0.97, 1.10] (208) | 1.05 [0.97, 1.12] (114) | 1.07 [1.03, 1.11] (561) |
| QB+WR1+WR2 | TE1 | top 5% | 0.98 [0.76, 1.25] (19) | 1.01 [0.91, 1.13] (86) | 1.08 [0.99, 1.18] (105) | 1.05 [0.96, 1.15] (62) | 1.04 [0.99, 1.11] (272) |
| QB+WR1+WR2 | TE1 | top 1% | 0.00 [0.00, 0.00] (0) | 1.16 [0.90, 1.46] (21) | 1.16 [0.87, 1.43] (22) | 0.79 [0.51, 1.08] (10) | 1.00 [0.84, 1.16] (53) |
| QB+WR1 | WR1 | top 10% | 1.06 [0.89, 1.22] (62) | 1.11 [1.02, 1.20] (221) | 1.05 [0.98, 1.12] (248) | 1.04 [0.95, 1.15] (117) | 1.07 [1.02, 1.12] (648) |
| QB+WR1 | WR1 | top 5% | 1.09 [0.80, 1.42] (30) | 1.14 [1.01, 1.28] (111) | 1.08 [0.97, 1.18] (130) | 1.08 [0.93, 1.24] (62) | 1.10 [1.02, 1.18] (333) |
| QB+WR1 | WR1 | top 1% | 1.69 [0.93, 2.61] (8) | 1.24 [0.94, 1.53] (27) | 1.36 [1.01, 1.69] (33) | 1.61 [1.08, 2.18] (17) | 1.39 [1.15, 1.62] (85) |
| QB+RB1 | WR1 | top 10% | 0.94 [0.77, 1.11] (61) | 1.08 [0.99, 1.17] (210) | 1.09 [1.00, 1.17] (266) | 1.15 [1.02, 1.28] (112) | 1.08 [1.03, 1.13] (649) |
| QB+RB1 | WR1 | top 5% | 1.00 [0.75, 1.28] (32) | 1.15 [1.00, 1.31] (108) | 1.13 [1.00, 1.25] (146) | 0.97 [0.80, 1.15] (46) | 1.10 [1.01, 1.18] (332) |
| QB+RB1 | WR1 | top 1% | 1.51 [0.75, 2.46] (8) | 1.38 [0.96, 1.75] (23) | 1.52 [1.19, 1.88] (42) | 2.02 [1.33, 2.79] (17) | 1.55 [1.32, 1.79] (90) |

Pearson r of the stack's points with the opposing WR1's, by the stacked team's spread bucket:

| Stack | BD | SD | SF | BF | all |
|---|---|---|---|---|---|
| QB+RB1 | +0.019 | +0.083 | +0.120 | +0.146 | +0.064 |
| QB+WR1 | +0.053 | +0.103 | +0.126 | +0.095 | +0.076 |
| QB+WR1+TE1 | +0.025 | +0.090 | +0.122 | +0.094 | +0.065 |
| QB+WR1+WR2 | +0.032 | +0.123 | +0.128 | +0.113 | +0.084 |

## 6. Do the projections already price the spread?

Change in (actual - projection) DK points per +10 points of spread; a projection that priced the script fully would miss by 0. The naive baseline is each player's mean over his previous three games.

| Role | Mean projection | Projection moves per +10 spread | Naive baseline misses (2014-22) | Projection misses (2014-22) | Projection misses (2023-25) | Position in its own distribution, z per +10 (2014-22 / 2023-25) |
|---|---|---|---|---|---|---|
| QB1 | 17.7 | +2.40 | +0.27 (t +1.4) | +0.68 (t +4.0) | +0.00 (t +0.0) | +0.15 (t +7.6) / +0.06 (t +1.5) |
| RB1 | 13.9 | +1.01 | +0.43 (t +2.0) | +0.73 (t +3.7) | +1.34 (t +3.6) | +0.09 (t +3.9) / +0.16 (t +3.9) |
| WR1 | 13.7 | +1.78 | +0.23 (t +1.0) | +0.31 (t +1.5) | -0.44 (t -1.3) | +0.04 (t +1.8) / -0.05 (t -1.2) |
| WR2 | 10.1 | +0.83 | +0.15 (t +0.8) | +0.33 (t +1.9) | +0.40 (t +1.3) | +0.04 (t +1.7) / +0.04 (t +1.1) |
| TE1 | 9.4 | +0.83 | +0.30 (t +1.6) | +0.44 (t +2.6) | +0.38 (t +1.4) | +0.07 (t +3.1) / +0.08 (t +2.0) |
| DST | 6.8 | +2.28 | +1.11 (t +6.8) | +0.04 (t +0.3) | +0.46 (t +1.8) | +0.20 (t +8.2) / +0.29 (t +6.8) |

## 7. The mechanism

Pass rate by the offense's score at the snap and its pre-game spread (2014-2025 designed plays). Per +1 point of pre-game spread the team-game pass rate moves -0.22 points (t -10); per +1 point of average lead on offense -0.67; per +1 point of final margin -0.39.

| Offense's score at the snap | All teams | big dog | small dog | small fav. | big fav. | Share of snaps |
|---|---|---|---|---|---|---|
| trail 15+ | 75.1% | 72.6% | 75.2% | 77.7% | 76.7% | 9% |
| trail 9-14 | 70.2% | 68.6% | 70.8% | 70.5% | 69.4% | 12% |
| trail 1-8 | 63.5% | 61.8% | 63.5% | 64.0% | 64.5% | 26% |
| tied | 57.6% | 55.5% | 57.1% | 58.1% | 59.3% | 19% |
| lead 1-8 | 55.7% | 54.5% | 54.7% | 56.0% | 57.4% | 20% |
| lead 9-14 | 51.3% | 48.8% | 50.6% | 51.0% | 53.3% | 8% |
| lead 15+ | 42.8% | 38.5% | 40.5% | 42.2% | 45.2% | 5% |

Volume by pre-game spread bucket and by final margin:

| Per team-game | big dog | small dog | small fav. | big fav. | per +10 spread | t | per +10 final margin | t  |
|---|---|---|---|---|---|---|---|---|
| pass attempts | 33.5 | 34.6 | 34.0 | 33.8 | -0.03 | -0.1 | -1.54 | -20.8 |
| RB carries | 20.3 | 21.5 | 22.6 | 23.6 | +1.69 | +11.0 | +2.41 | +40.9 |
| WR + TE targets | 25.9 | 27.0 | 26.6 | 26.5 | +0.16 | +1.1 | -1.03 | -16.8 |
| RB targets | 6.6 | 6.5 | 6.4 | 6.4 | -0.08 | -1.2 | -0.42 | -15.0 |

The final margin and the surprises (quadratic in the margin of role A's team removed from both):

| Pair | n | r | r after removing the margin | A vs margin | B vs margin |
|---|---|---|---|---|---|
| RB1 - DST | 6194 | +0.063 | -0.131 | +0.254 | +0.633 |
| RB1 - DST (opp.) | 6194 | -0.245 | -0.112 | +0.254 | -0.635 |
| QB1 - WR1 | 6086 | +0.322 | +0.302 | +0.285 | +0.109 |
| QB1 - WR2 | 5917 | +0.290 | +0.276 | +0.285 | +0.087 |
| QB1 - TE1 | 5391 | +0.263 | +0.250 | +0.283 | +0.073 |
| QB1 - WR1 (opp.) | 6084 | +0.085 | +0.118 | +0.286 | -0.110 |
| WR1 - WR2 | 6069 | +0.005 | -0.007 | +0.110 | +0.087 |
| RB1 - WR1 (opp.) | 6182 | +0.017 | +0.045 | +0.254 | -0.110 |
| RB1 - WR2 (opp.) | 6000 | +0.004 | +0.027 | +0.253 | -0.088 |
| RB1 - TE1 (opp.) | 5457 | +0.031 | +0.048 | +0.250 | -0.066 |
| QB1 - RB1 (opp.) | 6029 | +0.030 | +0.108 | +0.286 | -0.254 |
| QB1 - DST | 6096 | -0.084 | -0.360 | +0.286 | +0.634 |
| QB1 - DST (opp.) | 6096 | -0.363 | -0.246 | +0.286 | -0.633 |

## 8. Tails: do the booms of a pair cluster more than a Gaussian copula says?

| Pair | Both above | n | r | Observed | Gaussian, same r | Ratio | 90% interval of ratio |
|---|---|---|---|---|---|---|---|
| QB1 - WR1 | +1 sd | 6086 | +0.322 | 0.0590 | 0.0472 | 1.25 | [1.13, 1.36] |
| QB1 - WR1 | +1.5 sd | 6086 | +0.322 | 0.0171 | 0.0120 | 1.43 | [1.18, 1.70] |
| QB1 - WR2 | +1 sd | 5917 | +0.290 | 0.0537 | 0.0447 | 1.20 | [1.10, 1.31] |
| QB1 - WR2 | +1.5 sd | 5917 | +0.290 | 0.0145 | 0.0110 | 1.32 | [1.10, 1.59] |
| QB1 - TE1 | +1 sd | 5391 | +0.263 | 0.0558 | 0.0427 | 1.31 | [1.17, 1.43] |
| QB1 - TE1 | +1.5 sd | 5391 | +0.263 | 0.0145 | 0.0103 | 1.41 | [1.14, 1.72] |
| QB1 - QB1 (opp.) | +1 sd | 5934 | +0.209 | 0.0502 | 0.0387 | 1.30 | [1.13, 1.46] |
| QB1 - QB1 (opp.) | +1.5 sd | 5934 | +0.209 | 0.0138 | 0.0088 | 1.57 | [1.18, 1.99] |
| QB1 - RB1 | +1 sd | 6029 | +0.070 | 0.0343 | 0.0294 | 1.17 | [1.03, 1.29] |
| QB1 - RB1 | +1.5 sd | 6029 | +0.070 | 0.0063 | 0.0057 | 1.10 | [0.81, 1.41] |
| RB1 - DST | +1 sd | 6194 | +0.063 | 0.0326 | 0.0290 | 1.13 | [1.01, 1.25] |
| RB1 - DST | +1.5 sd | 6194 | +0.063 | 0.0060 | 0.0056 | 1.07 | [0.81, 1.36] |
| QB1 - WR1 (opp.) | +1 sd | 6084 | +0.085 | 0.0344 | 0.0304 | 1.13 | [0.99, 1.27] |
| QB1 - WR1 (opp.) | +1.5 sd | 6084 | +0.085 | 0.0084 | 0.0060 | 1.39 | [1.04, 1.69] |

## 9. Pooled correlation against independence, out of sample

| pair | train_rho | test_r | n | gain | t |
|---|---|---|---|---|---|
| same_team RB1-DST | +0.059 | +0.072 | 1609 | +3.0 | +1.3 |
| opp RB1-DST | -0.226 | -0.272 | 1609 | +61.2 | +6.9 |
| same_team QB1-WR1 | +0.320 | +0.291 | 1580 | +71.7 | +5.5 |
| same_team QB1-WR2 | +0.272 | +0.309 | 1524 | +73.8 | +6.7 |
| same_team QB1-TE1 | +0.248 | +0.275 | 1413 | +55.1 | +5.9 |
| opp QB1-WR1 | +0.082 | +0.085 | 1579 | +6.2 | +2.0 |
| same_team WR1-WR2 | +0.003 | +0.010 | 1558 | +0.0 | +0.3 |
| opp RB1-WR1 | +0.019 | +0.007 | 1602 | +0.2 | +0.3 |
| opp RB1-WR2 | +0.008 | -0.006 | 1539 | -0.1 | -0.4 |
| opp RB1-TE1 | +0.039 | -0.002 | 1429 | -0.0 | -0.0 |
| opp QB1-RB1 | +0.024 | +0.046 | 1567 | +1.6 | +1.6 |

(Shuffled-context control for the named pairs: spread mean -0.047, 95th pct +0.222; implied mean +0.004, 95th pct +0.315. Real: spread +0.265, implied +0.078.)
