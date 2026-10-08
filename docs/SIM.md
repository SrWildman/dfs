# The lineup simulator

`dfs sim ...` answers three questions about a set of DraftKings lineups that no player-level number can:

- **Cash:** the probability a lineup clears the cash line.
- **GPP:** how often a lineup reaches a tournament-winning score.
- **Across lineups:** how often **at least one** of them gets there.

Every edge elsewhere in the tool is per player. Lineups are won and lost on how players move *together* -- a QB
and his WR1 boom together -- and that is what this adds. It uses **public nflverse / ffverse history only**,
through the UM model's existing loaders (`dfs.model`); no sheet, no credentials, no TFFB. Nothing in
`src/dfs/sim/` writes to a sheet. See [MODEL.md](MODEL.md) for the distribution engine it builds on.

`dfs sim` is a **visible command group** (`fit`, `backtest`, `demo`; the launcher's `MORE_LABELS`,
`commands_doc.SECTIONS` and `docs/COMMANDS.md` list them). **In the sheet** (PROMPT_EDGE_V3 Part 5, 2026-10-08), the
simulator runs on every `dfs sync` and fills the Lineups tab, and `dfs lineups late-swap` scores swaps with it:
see "In the sheet" at the end of this file. Nothing in `src/dfs/sim/` itself writes to a sheet; the integration is in
`src/dfs/sim_inputs.py`, `sheet_lineup_sim.py` and `late_swap_sim.py`.

| Part | Module | What it does |
|---|---|---|
| Marginals | `marginal.py` | One player's outcome distribution as a CDF and a quantile function (the engine's ratio quantiles, with the zero atom explicit). |
| Roles | `roles.py` | A player's slot on his team (QB1, RB1-3, WR1-4, TE1-2, DST) from his **prior three games only**; builds the frame the correlations are fitted on. |
| Correlations | `correlation.py` | Normal scores, role-pair correlations with bootstrap intervals and shrinkage, the report-only game-total analysis, the cross-game check. |
| Simulator | `simulate.py` | `simulate_lineups` (Gaussian copula) and `swap_impact`. |
| Back-test | `backtest.py` | Random legal lineups for 2023-2025, predicted with correlations fitted on 2014-2022 only. |
| Text | `report.py`, `cli.py` | The tables printed by `dfs sim fit / backtest / demo` and used in this file. |

## Using it

```
dfs model fetch      # the cache (once; shared with dfs model)
dfs sim fit          # rebuild models/sim/correlations.csv (about 80 s)
dfs sim backtest --jobs 4   # the back-test below (about 5 minutes on 4 cores, 35 on one; --lineups-per-week 20 for a look)
dfs sim demo         # two lineups from 2025 week 12, one stacked, one not, plus a swap
```

```python
from dfs.sim.simulate import PlayerSpec, simulate_lineups, swap_impact

qb = PlayerSpec(
    id="mahomes",
    position="QB",
    team="KC",
    opp="IND",
    game_id="2025_12_IND_KC",
    role="QB1",
    projection=20.9,
    salary=6600,
)
...
result = simulate_lineups([lineup_a, lineup_b], n_sims=20000, seed=0, cash_line=148.0, gpp_target=200.0)
result.table()  # mean, p10 ... p99, P(cash), P(GPP target) per lineup
result.p_any_gpp, result.p_any_cash, result.expected_cashing
result.score_correlation  # lineup x lineup
swap_impact(lineup_a, out_id="pittman", in_spec=other_wr, cash_line=148.0, gpp_target=200.0)
```

`DEFAULT_CASH_LINE = 145.0` and `DEFAULT_GPP_TARGET = 190.0` are **placeholders**, not advice: the local
integration passes Sam's own typed cash lines. `projection` can be any number > 0 (UM's, a calibrated TFFB
number): the distribution engine works for any projection, and, as [MODEL.md](MODEL.md) says, the tables expect
one that is roughly unbiased at its level. A projection <= 0 simulates as exactly 0 points. `role` is one of
QB1, RB1-RB3, WR1-WR4, TE1-TE2, DST ("QB" and "TE" read as QB1 and TE1; RB4 and beyond read as RB3, WR5 and
beyond as WR4, TE3 and beyond as TE2). `game_id` is how the simulator knows who shares a game: players in
different games are independent.

## Method

**1. Each player's own distribution.** `dfs.model.distribution` stores, per position and projection, the
quantiles of `actual / projection` at 25 levels and the share of games scoring exactly 0. `marginal.py` reads
that as a CDF (linear between the quantile knots, end segments extrapolated to probability 0 and 1 -- the same
knots `prob_at_least` uses) and its inverse, with the zero atom made explicit: an actual of exactly 0 can be
placed anywhere inside the atom's probability mass, and a simulated draw inside the atom is exactly 0.

**2. Normal scores.** For every out-of-fold UM prediction of 2014-2025 (the training pipeline's own
`oof_predictions` of the *shipped* method per position: 51,267 player-games after the role filter), the actual
DK points go through that player's predicted distribution to a uniform `u`, which is clipped to
[0.005, 0.995] and mapped to `z = Phi^-1(u)`. A zero is placed uniformly at random within the atom (seeded).
`z` is "how surprising was this game, given the projection", so a correlation of `z` is a correlation of
*surprises* -- what a copula laid over the projections needs. (It is lower than the correlation of raw points,
which also contains what the projections already explain; both are shown below.) The z-scores come out
standard normal: mean 0.00 to 0.08, standard deviation 0.95 to 0.98 by position (QB's +0.08 means
quarterbacks score a little above their predicted distribution on average).

**3. Roles.** Within a team-game: QB1 = the quarterback with the most pass attempts, RB1-RB3 = running backs by
carries + targets, WR1-WR4 and TE1-TE2 = pass catchers by targets, DST = the team's defense. Usage is the mean
over the player's **three previous games played** (continuing across the season boundary, the convention of
`dfs.model.features`); the game itself is never used. Ranking is among the players who appeared in the game
(the data does not say who was inactive), so "WR1" means the top prior-usage receiver *who played*. Ranks past
the last named role fold into it; a team's second quarterback has no role and is not used. RB3, WR4 and TE2
are an addition to the roles the task named, because a lineup's FLEX spot needs one.

**4. Correlations.** For every pair of roles on the **same team** (55 pairs) and **across the two teams of a
game** (66 pairs, including a role against itself: QB1 against the opposing QB1) -- 121 pairs in all -- the
Pearson correlation of `z`, with `n` the number of pairs. The interval is a 90% **game-level bootstrap** (1,000
resamples of whole games, so the two teams of a game and every pair inside it stay together), and every value
is **shrunk toward 0 in Fisher-z space with a prior worth 200 observations**:
`z' = z * (n - 3) / (n - 3 + 200)`. The interval's endpoints go through the same map, so `rho` always sits
inside it. **Players in different games are assumed independent**; the check is below.

**5. Game total (report only).** The same fit within terciles of the Vegas game total (cut at 43.5 and 47.0
points), flagging a pair when two terciles' *unshrunk* intervals do not overlap. The flagged pairs turned out to be
what chance produces (see below), so **nothing from the tercile fit is shipped**: `correlations.csv` has the
pooled value of every pair, columns `relation, role_a, role_b, total_bucket, rho, n, ci_lo, ci_hi`, with
`total_bucket` always `all` (the loader refuses a file with any other value), and the simulator takes no game
total. `correlations_detail.csv` keeps every row of the analysis, with the unshrunk values and the flag;
`correlations_meta.json` records the cutoffs and checks.

**6. The copula.** For the union of every player in every lineup: build the correlation matrix `R` (0 across
games; the table's `same_team` or `opp` value for the two roles within a game), repair it if needed, draw `z ~ N(0, R)`, take `u = Phi(z)`, and read each player's
points off his own quantile function at `u`. Every player therefore keeps exactly his own marginal and every
pair inherits `R`. **The same player in two lineups is one variable**, so lineups that share players are
correlated with each other the way real ones are. `R` is assembled from separately estimated pairs, so it can in
principle fail to be a valid correlation matrix: it is repaired to the nearest positive semi-definite matrix by
**eigenvalue clipping and rescaling** (`SimResult.repair` records whether that happened and the largest entry
it moved). Two different players in the same role on one team (two RB3s) borrow the neighbouring role's pair
(RB2-RB3) and say so in `SimResult.notes`; two DSTs or QBs cannot occur.


## The correlation table

`models/sim/correlations.csv` (121 rows, one per role pair) is fitted on all of 2014-2025: 51,267 out-of-fold player-games
in 3,151 games.

### Against the literature

The figures widely cited for DraftKings are approximate; the QB against the defense facing him is the repo's
strategy review's -0.46, the largest coefficient in its sources. Ours, pooled over all game totals:

| Pair | Cited | Ours (z, shrunk) | n | Raw DK points r |
|---|---|---|---|---|
| QB1-WR1 | +0.4 to +0.5 | +0.31 [+0.29, +0.32] | 6,086 | +0.37 |
| QB1-TE1 | +0.3 | +0.25 [+0.23, +0.27] | 5,391 | +0.31 |
| QB1-RB1 | near +0.1 | +0.07 [+0.05, +0.09] | 6,029 | +0.07 |
| opp. QB1-QB1 | +0.2 | +0.19 [+0.16, +0.22] | 2,967 | +0.21 |
| opp. QB1-DST | -0.46 (strategy review) | -0.34 [-0.36, -0.32] | 6,096 | -0.41 |

`Ours` is the correlation of normal scores, shrunk, with its 90% game-bootstrap interval. `Raw DK points r` is
the plain Pearson correlation of the two players' DK points for the same pairs -- what the literature measures,
and the like-for-like comparison, since it also contains everything the projections already explain.

- **The two well-established signs agree:** QB1-WR1 is positive (+0.31) and QB1 against the opposing DST is
  strongly negative (-0.34; -0.41 in raw points), and that pair is the largest-magnitude value in the whole
  table. No investigation was needed.
- The other signs agree too, and the opposing-QB figure (+0.19, raw +0.21) matches the cited +0.2.
- The receiving-pair magnitudes are lower than cited (QB1-WR1 +0.31 against +0.4 to +0.5, QB1-TE1 +0.25 against
  +0.3, QB1-RB1 +0.07 against +0.1). Part of that is the definition: the raw-point correlations (+0.37, +0.31,
  +0.07) sit at or near the cited figures, so the cited numbers are about what a correlation of *points*
  looks like, while the copula needs the correlation of *surprises given the projection*. Part is shrinkage, which
  costs about 0.01 at these sample sizes. A role average also dilutes the elite QB-WR1 pairs the literature
  is usually quoting. The gaps are reported, not tuned away.

### Same team

n is the number of pairs; `raw r` is before shrinkage. Sorted by `rho`.

| Pair | n | raw r | rho (shrunk) | 90% interval |
|---|---|---|---|---|
| QB1 - WR1 | 6,086 | +0.316 | +0.306 | [+0.287, +0.324] |
| QB1 - WR2 | 5,917 | +0.284 | +0.275 | [+0.255, +0.295] |
| QB1 - TE1 | 5,391 | +0.257 | +0.248 | [+0.227, +0.269] |
| QB1 - WR3 | 4,585 | +0.222 | +0.213 | [+0.190, +0.235] |
| QB1 - TE2 | 2,023 | +0.190 | +0.173 | [+0.138, +0.203] |
| QB1 - WR4 | 1,921 | +0.138 | +0.125 | [+0.093, +0.158] |
| QB1 - RB1 | 6,029 | +0.072 | +0.070 | [+0.050, +0.091] |
| RB1 - DST | 6,194 | +0.063 | +0.061 | [+0.040, +0.081] |
| QB1 - RB2 | 4,507 | +0.055 | +0.053 | [+0.030, +0.076] |
| QB1 - RB3 | 1,414 | +0.056 | +0.049 | [+0.011, +0.087] |
| RB3 - WR4 | 406 | +0.049 | +0.033 | [-0.020, +0.087] |
| RB3 - WR2 | 1,410 | +0.031 | +0.027 | [-0.012, +0.067] |
| WR4 - TE1 | 1,693 | +0.022 | +0.020 | [-0.016, +0.057] |
| WR4 - TE2 | 602 | +0.024 | +0.018 | [-0.029, +0.066] |
| RB3 - WR3 | 1,092 | +0.021 | +0.018 | [-0.021, +0.059] |
| RB2 - DST | 4,632 | +0.017 | +0.016 | [-0.007, +0.039] |
| RB2 - TE2 | 1,618 | +0.014 | +0.013 | [-0.023, +0.046] |
| WR3 - TE1 | 4,142 | +0.010 | +0.009 | [-0.016, +0.033] |
| RB2 - WR4 | 1,395 | +0.006 | +0.005 | [-0.031, +0.041] |
| WR1 - WR2 | 6,069 | +0.005 | +0.005 | [-0.015, +0.025] |
| WR1 - TE1 | 5,509 | +0.003 | +0.003 | [-0.017, +0.025] |
| TE1 - TE2 | 2,045 | +0.003 | +0.003 | [-0.031, +0.038] |
| RB2 - TE1 | 4,089 | +0.002 | +0.002 | [-0.023, +0.026] |
| RB1 - TE1 | 5,465 | +0.002 | +0.002 | [-0.019, +0.023] |
| WR2 - TE1 | 5,346 | +0.001 | +0.001 | [-0.022, +0.023] |
| WR3 - TE2 | 1,463 | -0.000 | -0.000 | [-0.037, +0.036] |
| WR2 - WR3 | 4,681 | -0.003 | -0.003 | [-0.025, +0.021] |
| WR1 - WR3 | 4,694 | -0.009 | -0.008 | [-0.031, +0.015] |
| WR2 - TE2 | 1,989 | -0.011 | -0.010 | [-0.043, +0.026] |
| RB3 - DST | 1,451 | -0.013 | -0.011 | [-0.049, +0.029] |
| RB3 - WR1 | 1,447 | -0.013 | -0.012 | [-0.050, +0.028] |
| RB1 - WR2 | 6,003 | -0.014 | -0.014 | [-0.034, +0.007] |
| RB2 - WR2 | 4,478 | -0.015 | -0.014 | [-0.039, +0.008] |
| WR2 - WR4 | 1,963 | -0.021 | -0.019 | [-0.054, +0.016] |
| RB1 - TE2 | 2,057 | -0.021 | -0.019 | [-0.053, +0.013] |
| RB3 - TE2 | 490 | -0.028 | -0.020 | [-0.074, +0.037] |
| RB1 - WR1 | 6,184 | -0.021 | -0.020 | [-0.043, +0.001] |
| WR1 - TE2 | 2,075 | -0.024 | -0.022 | [-0.052, +0.011] |
| WR4 - DST | 1,965 | -0.026 | -0.024 | [-0.056, +0.008] |
| RB2 - WR3 | 3,441 | -0.025 | -0.024 | [-0.050, +0.001] |
| RB1 - WR4 | 1,937 | -0.030 | -0.027 | [-0.061, +0.006] |
| RB3 - TE1 | 1,295 | -0.032 | -0.028 | [-0.066, +0.013] |
| RB2 - RB3 | 1,343 | -0.034 | -0.030 | [-0.071, +0.009] |
| RB1 - WR3 | 4,644 | -0.031 | -0.030 | [-0.052, -0.007] |
| TE2 - DST | 2,080 | -0.034 | -0.031 | [-0.064, +0.003] |
| RB2 - WR1 | 4,623 | -0.037 | -0.035 | [-0.059, -0.012] |
| WR3 - WR4 | 1,923 | -0.044 | -0.040 | [-0.073, -0.007] |
| WR1 - WR4 | 1,965 | -0.054 | -0.049 | [-0.081, -0.014] |
| WR1 - DST | 6,254 | -0.057 | -0.055 | [-0.075, -0.036] |
| RB1 - RB3 | 1,448 | -0.067 | -0.059 | [-0.099, -0.018] |
| WR2 - DST | 6,068 | -0.063 | -0.061 | [-0.082, -0.040] |
| TE1 - DST | 5,521 | -0.069 | -0.066 | [-0.089, -0.044] |
| WR3 - DST | 4,692 | -0.070 | -0.067 | [-0.087, -0.046] |
| RB1 - RB2 | 4,620 | -0.082 | -0.078 | [-0.101, -0.054] |
| QB1 - DST | 6,096 | -0.085 | -0.082 | [-0.104, -0.061] |

### Opposing teams

| Pair | n | raw r | rho (shrunk) | 90% interval |
|---|---|---|---|---|
| QB1 - QB1 | 2,967 | +0.205 | +0.193 | [+0.164, +0.219] |
| QB1 - WR2 | 5,902 | +0.090 | +0.087 | [+0.064, +0.107] |
| QB1 - WR1 | 6,084 | +0.084 | +0.081 | [+0.060, +0.103] |
| RB3 - WR4 | 465 | +0.114 | +0.079 | [+0.033, +0.130] |
| QB1 - WR3 | 4,564 | +0.076 | +0.073 | [+0.049, +0.097] |
| QB1 - TE2 | 2,027 | +0.069 | +0.063 | [+0.031, +0.097] |
| WR2 - TE2 | 2,018 | +0.067 | +0.061 | [+0.025, +0.095] |
| QB1 - TE1 | 5,375 | +0.060 | +0.058 | [+0.036, +0.079] |
| WR2 - WR3 | 4,541 | +0.058 | +0.056 | [+0.031, +0.078] |
| WR2 - WR2 | 2,939 | +0.044 | +0.041 | [+0.011, +0.068] |
| WR2 - WR4 | 1,903 | +0.045 | +0.041 | [+0.007, +0.074] |
| WR1 - WR2 | 6,057 | +0.042 | +0.041 | [+0.021, +0.061] |
| WR1 - WR1 | 3,121 | +0.043 | +0.040 | [+0.011, +0.068] |
| WR1 - TE2 | 2,078 | +0.042 | +0.038 | [+0.007, +0.071] |
| WR1 - WR3 | 4,682 | +0.040 | +0.038 | [+0.015, +0.060] |
| WR3 - WR3 | 1,755 | +0.038 | +0.034 | [+0.002, +0.068] |
| RB1 - TE1 | 5,457 | +0.030 | +0.029 | [+0.007, +0.052] |
| RB2 - RB3 | 1,055 | +0.034 | +0.028 | [-0.016, +0.071] |
| QB1 - RB1 | 6,029 | +0.027 | +0.026 | [+0.005, +0.047] |
| RB2 - WR3 | 3,487 | +0.028 | +0.026 | [+0.000, +0.053] |
| WR4 - TE2 | 661 | +0.027 | +0.020 | [-0.028, +0.069] |
| RB3 - RB3 | 171 | +0.041 | +0.019 | [-0.035, +0.067] |
| QB1 - WR4 | 1,908 | +0.020 | +0.018 | [-0.014, +0.054] |
| TE1 - TE2 | 1,863 | +0.019 | +0.017 | [-0.019, +0.055] |
| WR3 - WR4 | 1,476 | +0.019 | +0.017 | [-0.021, +0.055] |
| RB1 - WR1 | 6,182 | +0.017 | +0.016 | [-0.005, +0.036] |
| QB1 - RB2 | 4,503 | +0.016 | +0.015 | [-0.007, +0.039] |
| WR1 - WR4 | 1,962 | +0.015 | +0.014 | [-0.021, +0.049] |
| WR2 - TE1 | 5,348 | +0.014 | +0.013 | [-0.007, +0.033] |
| WR3 - TE1 | 4,132 | +0.012 | +0.012 | [-0.015, +0.037] |
| RB1 - TE2 | 2,062 | +0.012 | +0.011 | [-0.020, +0.045] |
| WR1 - TE1 | 5,512 | +0.010 | +0.010 | [-0.011, +0.029] |
| RB2 - WR2 | 4,482 | +0.009 | +0.009 | [-0.014, +0.031] |
| RB2 - TE2 | 1,563 | +0.008 | +0.007 | [-0.026, +0.043] |
| RB1 - WR4 | 1,946 | +0.006 | +0.006 | [-0.032, +0.037] |
| RB1 - WR3 | 4,644 | +0.004 | +0.004 | [-0.019, +0.027] |
| RB1 - WR2 | 6,000 | +0.003 | +0.003 | [-0.018, +0.024] |
| TE1 - TE1 | 2,441 | +0.002 | +0.002 | [-0.030, +0.032] |
| RB2 - TE1 | 4,095 | -0.005 | -0.005 | [-0.029, +0.022] |
| RB2 - WR4 | 1,491 | -0.007 | -0.006 | [-0.043, +0.033] |
| QB1 - RB3 | 1,413 | -0.013 | -0.012 | [-0.049, +0.025] |
| RB2 - RB2 | 1,708 | -0.015 | -0.013 | [-0.051, +0.024] |
| RB3 - WR1 | 1,448 | -0.017 | -0.015 | [-0.054, +0.026] |
| WR3 - TE2 | 1,607 | -0.018 | -0.016 | [-0.052, +0.021] |
| WR4 - WR4 | 312 | -0.028 | -0.017 | [-0.068, +0.037] |
| RB3 - WR2 | 1,415 | -0.020 | -0.017 | [-0.058, +0.022] |
| RB1 - RB1 | 3,061 | -0.020 | -0.019 | [-0.046, +0.010] |
| TE2 - TE2 | 367 | -0.037 | -0.024 | [-0.080, +0.034] |
| WR4 - TE1 | 1,724 | -0.029 | -0.026 | [-0.061, +0.009] |
| RB2 - WR1 | 4,621 | -0.030 | -0.029 | [-0.052, -0.006] |
| WR4 - DST | 1,964 | -0.040 | -0.036 | [-0.067, +0.001] |
| RB3 - WR3 | 1,128 | -0.042 | -0.036 | [-0.076, +0.004] |
| RB1 - RB3 | 1,437 | -0.045 | -0.040 | [-0.078, +0.001] |
| RB1 - RB2 | 4,582 | -0.043 | -0.041 | [-0.064, -0.018] |
| RB3 - DST | 1,451 | -0.051 | -0.045 | [-0.088, -0.003] |
| RB3 - TE1 | 1,282 | -0.054 | -0.047 | [-0.085, -0.008] |
| RB3 - TE2 | 491 | -0.076 | -0.054 | [-0.105, -0.004] |
| WR3 - DST | 4,693 | -0.068 | -0.065 | [-0.089, -0.043] |
| TE2 - DST | 2,081 | -0.074 | -0.067 | [-0.098, -0.034] |
| TE1 - DST | 5,521 | -0.074 | -0.071 | [-0.093, -0.048] |
| RB2 - DST | 4,632 | -0.094 | -0.090 | [-0.112, -0.067] |
| WR2 - DST | 6,068 | -0.105 | -0.102 | [-0.122, -0.081] |
| WR1 - DST | 6,254 | -0.126 | -0.122 | [-0.141, -0.101] |
| DST - DST | 3,149 | -0.201 | -0.190 | [-0.215, -0.163] |
| RB1 - DST | 6,194 | -0.242 | -0.234 | [-0.255, -0.214] |
| QB1 - DST | 6,096 | -0.352 | -0.342 | [-0.362, -0.323] |

(An opposing pair of one role with itself, like QB1 - QB1, is each game's single pair.)

### Different games: the independence assumption

Players in different games are assumed independent (0). To check, each row of one role is matched with a randomly
chosen row of the other role from **the same week but a different game**, for all 66 role pairs
(including a role with itself), and the Pearson correlation of normal scores is taken:

| Check | Value |
|---|---|
| mean \|r\| over the 66 role pairs | **0.0117** |
| the same number if the games were truly independent (sqrt(2 / (pi n)) averaged) | 0.0125 |
| mean r (signed) | +0.0015 |
| largest \|r\| of any one pair | 0.0385 |
| limit | 0.03 |

Mean |r| is 0.012 against a limit of 0.03, which is what sampling noise alone would produce. The assumption holds.

### Does it depend on the game total?

The same fit within terciles of the Vegas game total (low below 43.5, mid up to 47.0, high above), as a
**report-only analysis**. A pair is flagged if two terciles' unshrunk 90% intervals fail to overlap, which
happens for **8 of 121 pairs**:

| Relation | Pair | low | mid | high |
|---|---|---|---|---|
| opp | WR1 - WR2 | +0.073 | -0.010 | +0.062 |
| opp | WR1 - WR4 | +0.109 | +0.018 | -0.043 |
| opp | WR3 - TE2 | -0.127 | +0.004 | +0.038 |
| same_team | RB1 - WR1 | +0.018 | -0.063 | -0.018 |
| same_team | RB3 - TE2 | -0.114 | +0.184 | -0.114 |
| same_team | RB3 - WR4 | -0.232 | +0.121 | +0.115 |
| same_team | WR3 - TE2 | -0.067 | +0.090 | -0.026 |
| same_team | WR3 - WR4 | +0.043 | -0.032 | -0.104 |

(unshrunk r by tercile). **Read this as "no": it is what chance produces.** Two 90% intervals missing each other
is a roughly 2% event for each of a pair's three tercile comparisons, so about 6% of pairs are flagged with no
dependence at all. To measure that on this data, the games' totals were shuffled among the games (breaking any
link between total and correlation) and the whole fit repeated five times: **7, 10, 7, 10 and 6 pairs were
flagged, mean 8.0 -- against 8 on the real totals.** None of the eight is a pair that matters for stacking
(QB1 with his receivers), the patterns jump up and down (mid unlike low and high) rather than rising or falling
with the total, and most rest on a few hundred pairs. **Ruling: pooled values only.** `correlations.csv` ships
the pooled value of every pair, the simulator takes no game total, and this table is the whole of what the
tercile analysis contributes (`correlations_detail.csv` keeps every row of it).

## Back-test

**Design.** For every regular-season week of 2023-2025 (54 weeks), 200 random **legal DraftKings classic
lineups** (QB, 2 RB, 3 WR, TE, FLEX, DST) from that week's out-of-fold player-games with roles from prior games,
half stacked (QB + 2 same-team pass catchers + 1 bring-back from the other team of his game) and half random:
10,800 lineups. Legal means nine distinct players, exactly one FLEX from RB/WR/TE, players from at least
two games, no more than eight from one team; **the salary cap is ignored** (there is no salary in this data).
Each lineup draws its players with probability proportional to projection to the power 1, 3 or 6, chosen per
lineup, so the set ranges from nearly random to nearly the chalk. Each lineup's distribution is predicted from
the UM projections by the simulator (10,000 draws) with **correlations fitted on 2014-2022 only**, and compared
with the realized score (the sum of its players' actual DK points). Everything is repeated with every
correlation forced to 0. Two sets of marginals are run:

- **shipped**: the distribution tables the simulator uses (built on 2014-2025, so in-sample for the test seasons);
- **train-only**: tables rebuilt from 2014-2022 out-of-fold rows alone (out-of-time; the stricter test).

**Acceptance:** every coverage figure within 3 points of its target and every reliability bin within 4 points.
Cash line 145, GPP target 190.

### What the lineups look like (shipped marginals)

| Lineups | n | Sum of projections | Simulated mean | Realized mean | P(>= 145) predicted | realized |
|---|---|---|---|---|---|---|
| selectivity 1 | 3,562 | 109.3 | 106.5 | 106.3 | 8.9% | 8.8% |
| selectivity 3 | 3,642 | 130.4 | 125.2 | 123.8 | 24.1% | 21.7% |
| selectivity 6 | 3,596 | 149.9 | 141.9 | 138.7 | 44.2% | 39.5% |
| all players in range | 2,913 | 110.4 | 107.9 | 108.8 | 10.0% | 10.1% |
| a player past the top bucket | 7,887 | 137.2 | 130.8 | 128.2 | 31.6% | 28.3% |

"Past the top bucket" means at least one player is projected above the mean projection of his position's highest
distribution bucket (QB 24.3, RB 21.2, WR 19.8, TE 14.0, DST 10.5), where the engine has no bucket of its own.
The sum of projections is far above the realized score for the selective lineups, which is expected (the
distribution is skewed and the highest projections regress).

### Coverage (shipped marginals)

Share of realized scores below the predicted quantile. `(+x)` is the gap from the target in points.

All lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.4% (+0.4) | 11.2% (+1.2) | 0.3 |
| p25 | 25% | 26.4% (+1.4) | 27.1% (+2.1) | 0.4 |
| p50 | 50% | 52.5% (+2.5) | 52.7% (+2.7) | 0.5 |
| p75 | 75% | 77.0% (+2.0) | 76.4% (+1.4) | 0.4 |
| p90 | 90% | 91.3% (+1.3) | 90.6% (+0.6) | 0.3 |
| p99 | 99% | 99.1% (+0.1) | 99.0% (-0.0) | 0.1 |

Stacked lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.4% (+0.4) | 12.1% (+2.1) | 0.4 |
| p25 | 25% | 26.4% (+1.4) | 27.9% (+2.9) | 0.6 |
| p50 | 50% | 52.6% (+2.6) | 52.9% (+2.9) | 0.7 |
| p75 | 75% | 76.0% (+1.0) | 74.9% (-0.1) | 0.6 |
| p90 | 90% | 90.8% (+0.8) | 89.2% (-0.8) | 0.4 |
| p99 | 99% | 98.9% (-0.1) | 98.6% (-0.4) | 0.1 |

Random lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.4% (+0.4) | 10.3% (+0.3) | 0.4 |
| p25 | 25% | 26.3% (+1.3) | 26.4% (+1.4) | 0.6 |
| p50 | 50% | 52.4% (+2.4) | 52.6% (+2.6) | 0.7 |
| p75 | 75% | 77.9% (+2.9) | 77.8% (+2.8) | 0.6 |
| p90 | 90% | 91.9% (+1.9) | 91.9% (+1.9) | 0.4 |
| p99 | 99% | 99.3% (+0.3) | 99.3% (+0.3) | 0.1 |

Out-of-time marginals (train-only), all lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.8% (+0.8) | 11.7% (+1.7) | 0.3 |
| p25 | 25% | 27.2% (+2.2) | 27.9% (+2.9) | 0.4 |
| p50 | 50% | 53.4% (+3.4) | 53.7% (+3.7) | 0.5 |
| p75 | 75% | 77.5% (+2.5) | 76.9% (+1.9) | 0.4 |
| p90 | 90% | 91.6% (+1.6) | 91.0% (+1.0) | 0.3 |
| p99 | 99% | 99.2% (+0.2) | 99.0% (-0.0) | 0.1 |

### Reliability of P(score >= cash line) (shipped marginals)

Ten equal-count bins of the predicted probability. `*` marks a gap beyond +/-4 points.

All lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 1,080 | 2.2% | 2.5% | +0.3 | 1.9% | 2.8% | +0.9 | 27 |
| 2 | 1,080 | 5.7% | 6.5% | +0.8 | 5.1% | 6.3% | +1.2 | 70 |
| 3 | 1,080 | 9.7% | 9.4% | -0.4 | 9.0% | 10.0% | +1.0 | 101 |
| 4 | 1,080 | 14.3% | 14.5% | +0.2 | 13.5% | 13.5% | +0.0 | 157 |
| 5 | 1,080 | 19.8% | 19.0% | -0.8 | 18.9% | 19.5% | +0.6 | 205 |
| 6 | 1,080 | 25.8% | 24.5% | -1.3 | 25.1% | 24.9% | -0.2 | 265 |
| 7 | 1,080 | 32.1% | 28.4% | -3.7 | 31.6% | 26.9% | -4.7* | 307 |
| 8 | 1,080 | 39.2% | 35.5% | -3.8 | 39.0% | 36.3% | -2.7 | 383 |
| 9 | 1,080 | 47.7% | 41.9% | -5.8* | 47.7% | 42.0% | -5.7* | 452 |
| 10 | 1,080 | 61.2% | 51.9% | -9.3* | 61.5% | 51.9% | -9.7* | 561 |

Lineups with no player past the top bucket:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 292 | 0.9% | 0.3% | -0.6 | 0.8% | 0.7% | -0.1 | 1 |
| 2 | 292 | 2.2% | 3.4% | +1.2 | 1.9% | 2.7% | +0.9 | 10 |
| 3 | 292 | 3.5% | 2.7% | -0.8 | 3.1% | 4.1% | +1.0 | 8 |
| 4 | 291 | 5.0% | 5.8% | +0.9 | 4.4% | 4.8% | +0.4 | 17 |
| 5 | 291 | 6.7% | 7.2% | +0.5 | 6.0% | 8.2% | +2.2 | 21 |
| 6 | 291 | 8.7% | 10.3% | +1.6 | 7.9% | 7.6% | -0.4 | 30 |
| 7 | 291 | 11.2% | 10.7% | -0.6 | 10.4% | 11.7% | +1.3 | 31 |
| 8 | 291 | 14.3% | 15.8% | +1.5 | 13.4% | 17.2% | +3.8 | 46 |
| 9 | 291 | 18.8% | 16.8% | -2.0 | 17.9% | 16.8% | -1.1 | 49 |
| 10 | 291 | 29.0% | 27.8% | -1.2 | 28.4% | 27.1% | -1.3 | 81 |

### Reliability of P(score >= GPP target) (shipped marginals)

All lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 1,080 | 0.0% | 0.0% | -0.0 | 0.0% | 0.0% | -0.0 | 0 |
| 2 | 1,080 | 0.1% | 0.3% | +0.2 | 0.1% | 0.3% | +0.2 | 3 |
| 3 | 1,080 | 0.3% | 0.4% | +0.1 | 0.2% | 0.4% | +0.2 | 4 |
| 4 | 1,080 | 0.5% | 0.4% | -0.2 | 0.4% | 0.4% | -0.0 | 4 |
| 5 | 1,080 | 1.0% | 0.8% | -0.1 | 0.8% | 0.6% | -0.1 | 9 |
| 6 | 1,080 | 1.7% | 1.3% | -0.4 | 1.4% | 1.7% | +0.3 | 14 |
| 7 | 1,080 | 2.7% | 1.6% | -1.1 | 2.3% | 1.6% | -0.7 | 17 |
| 8 | 1,080 | 4.2% | 2.8% | -1.5 | 3.7% | 2.7% | -1.1 | 30 |
| 9 | 1,080 | 6.7% | 5.1% | -1.6 | 6.1% | 5.0% | -1.1 | 55 |
| 10 | 1,080 | 13.3% | 7.8% | -5.5* | 12.8% | 7.8% | -5.0* | 84 |

Stacked lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 540 | 0.0% | 0.0% | -0.0 | 0.0% | 0.0% | -0.0 | 0 |
| 2 | 540 | 0.1% | 0.6% | +0.4 | 0.1% | 0.4% | +0.3 | 3 |
| 3 | 540 | 0.3% | 0.4% | +0.1 | 0.2% | 0.6% | +0.4 | 2 |
| 4 | 540 | 0.5% | 0.6% | +0.0 | 0.3% | 0.7% | +0.4 | 3 |
| 5 | 540 | 0.9% | 0.9% | -0.0 | 0.6% | 0.6% | -0.1 | 5 |
| 6 | 540 | 1.5% | 0.7% | -0.8 | 1.1% | 1.1% | +0.0 | 4 |
| 7 | 540 | 2.4% | 3.0% | +0.6 | 1.8% | 2.8% | +1.0 | 16 |
| 8 | 540 | 3.6% | 2.0% | -1.5 | 2.8% | 2.2% | -0.6 | 11 |
| 9 | 540 | 5.5% | 3.3% | -2.2 | 4.5% | 3.5% | -1.0 | 18 |
| 10 | 540 | 10.9% | 6.7% | -4.2* | 9.6% | 6.3% | -3.3 | 36 |

### Spread: does the correlation matter?

| Lineups | n | Independent mean sd | Independent E[z^2] | Correlated mean sd | Correlated E[z^2] |
|---|---|---|---|---|---|
| all | 10,800 | 26.03 | 1.030 | 26.95 | 0.957 |
| stacked | 5,400 | 25.76 | 1.132 | 27.48 | 0.994 |
| random | 5,400 | 26.31 | 0.927 | 26.42 | 0.920 |
| in-range | 2,913 | 24.04 | 1.039 | 24.95 | 0.962 |

The mean of ((realized - mean) / sd)^2 is 1 for a calibrated spread. And as paired differences (correlated
against independent; weeks are the unit, because lineups in a week share players and games):

| Lineups | Measure | Independent | Correlated | Improvement | Better by |
|---|---|---|---|---|---|
| all | Pinball loss (p10-p99) | 6.211 | 6.205 | +0.006 (SE 0.003) | +0.10% |
| all | Brier, P(>= 145) | 0.15756 | 0.15734 | +0.00022 (SE 0.00013) | +0.14% |
| all | Brier, P(>= 190) | 0.01992 | 0.02001 | -0.00009 (SE 0.00004) | -0.46% |
| stacked | Pinball loss (p10-p99) | 6.459 | 6.447 | +0.012 (SE 0.006) | +0.19% |
| stacked | Brier, P(>= 145) | 0.15665 | 0.15638 | +0.00026 (SE 0.00024) | +0.17% |
| stacked | Brier, P(>= 190) | 0.01771 | 0.01786 | -0.00015 (SE 0.00006) | -0.85% |
| random | Pinball loss (p10-p99) | 5.963 | 5.963 | +0.000 (SE 0.002) | +0.01% |
| random | Brier, P(>= 145) | 0.15847 | 0.15830 | +0.00017 (SE 0.00006) | +0.11% |
| random | Brier, P(>= 190) | 0.02213 | 0.02216 | -0.00003 (SE 0.00002) | -0.15% |
| in-range | Pinball loss (p10-p99) | 5.742 | 5.738 | +0.008 (SE 0.005) | +0.14% |
| in-range | Brier, P(>= 145) | 0.08492 | 0.08471 | +0.00028 (SE 0.00016) | +0.33% |
| in-range | Brier, P(>= 190) | 0.00412 | 0.00412 | +0.00000 (SE 0.00001) | +0.09% |

### Verdict

Shipped marginals:

| Lineups | Simulation | Max coverage gap | Coverage within 3 | Max cash-bin gap | Max GPP-bin gap | Reliability within 4 |
|---|---|---|---|---|---|---|
| all | Correlated | 2.5 | yes | 9.3 | 5.5 | NO |
| all | Independent | 2.7 | yes | 9.7 | 5.0 | NO |
| stacked | Correlated | 2.6 | yes | 12.1 | 4.2 | NO |
| stacked | Independent | 2.9 | yes | 13.0 | 3.3 | NO |
| random | Correlated | 2.9 | yes | 6.2 | 6.4 | NO |
| random | Independent | 2.8 | yes | 7.0 | 6.0 | NO |
| in-range | Correlated | 1.6 | yes | 2.0 | 1.2 | yes |
| in-range | Independent | 1.1 | yes | 3.8 | 0.9 | yes |

Out-of-time marginals (train-only):

| Lineups | Simulation | Max coverage gap | Coverage within 3 | Max cash-bin gap | Max GPP-bin gap | Reliability within 4 |
|---|---|---|---|---|---|---|
| all | Correlated | 3.4 | NO | 9.8 | 5.7 | NO |
| all | Independent | 3.7 | NO | 10.3 | 5.0 | NO |
| stacked | Correlated | 3.3 | NO | 12.8 | 4.3 | NO |
| stacked | Independent | 3.7 | NO | 13.3 | 3.4 | NO |
| random | Correlated | 3.5 | NO | 7.5 | 6.5 | NO |
| random | Independent | 3.8 | NO | 7.6 | 6.3 | NO |
| in-range | Correlated | 1.2 | yes | 2.5 | 1.2 | yes |
| in-range | Independent | 1.2 | yes | 2.9 | 0.9 | yes |

(The full set of tables, including every group for both marginal sets, is `models/sim/backtest.md`.)

**Coverage: accepted for the shipped marginals** -- all within 3 points (largest 2.9, random lineups). The
out-of-time marginals fall just outside (3.3 to 3.8 for all, stacked and random lineups: realized scores fall
below the predicted median 53.4% of the time), the era drift [MODEL.md](MODEL.md)'s stricter check already
found. Almost every gap is positive: the predicted quantiles run a little high.

**Reliability: not accepted as specified, and the cause is the distribution engine, not the copula.** The
bins beyond 4 points are the top two or three deciles of the cash line (over-predicted by 5.8 to 12.1 points) and
the top decile of the GPP target (by 5.5), in the correlated *and* the independent runs. They are the lineups
with the highest predicted probability: the near-chalk ones, which hold players projected above the centre of
their position's top bucket, where the engine reuses that bucket's outcome ratios however high the projection. Three pieces of evidence point there:
(1) the misses are confined to the most selective lineups and the highest bins; (2) the table below shows the
engine's mean overstating players projected well past the top bucket (actual / engine 0.81 to 0.98 for QB at
25 and up, RB at 26 and up, TE at 14 and up and DST at 11 and up; WR is unaffected); (3) **lineups with no such player pass**: 2,913 lineups, 291 per bin, largest gap 2.0
points for the cash line and 1.2 for the GPP target.

| Position | Projection | n | Mean projection | Engine mean | Actual mean | Actual / engine |
|---|---|---|---|---|---|---|
| QB | <14 | 1,209 | 11.0 | 11.8 | 14.0 | 1.19 |
| QB | 25-28 | 207 | 26.2 | 23.5 | 22.6 | 0.96 |
| QB | 28+ | 60 | 29.5 | 26.4 | 23.3 | 0.88 |
| RB | 22-26 | 268 | 23.7 | 21.7 | 21.9 | 1.01 |
| RB | 26+ | 78 | 27.8 | 25.5 | 23.4 | 0.92 |
| WR | 22+ | 71 | 22.9 | 22.4 | 22.7 | 1.01 |
| TE | 14-17 | 437 | 15.3 | 13.7 | 13.5 | 0.98 |
| TE | 17+ | 202 | 19.1 | 17.0 | 15.9 | 0.93 |
| DST | 11-13 | 254 | 11.8 | 9.8 | 9.0 | 0.92 |
| DST | 13+ | 61 | 13.8 | 11.4 | 9.2 | 0.81 |

(Out-of-fold 2014-2025, the data the tables were built on. "Engine mean" is the mean of the simulated
distribution at each projection. The lowest QB bucket is the other end of the same weakness, already documented
in [MODEL.md](MODEL.md): actuals do not scale down with a tiny projection.) **The remedy is in the engine** --
tables for projections above the current top bucket centre, e.g. an extra top bucket per position -- which
changes `dfs.model.distribution`'s bucket edges, outside this task's files; it is left for review.

**Does the correlated simulation do better? Yes on spread, barely on everything else, and not in the upper tail.**
The honest numbers:

- *Spread:* for stacked lineups the independent simulation is too narrow (mean squared standardized residual
  1.13) and the correlated one is right (0.99). For random lineups the two are the same (0.93 independent, 0.92 correlated), as they
  should be. The mean predicted sd of a stack grows from 25.8 to 27.5 points.
- *Lower and middle quantiles of stacked lineups:* p10 gap +0.4 correlated against +2.1 independent, p25 +1.4
  against +2.9.
- *Upper tail of stacked lineups:* p90 gap +0.8 correlated against -0.8 independent -- the same size, opposite
  side; p99 -0.1 against -0.4. Random lineups sit at +1.9 at p90 under both, so the predicted quantiles run
  about 1 to 2 points high everywhere (the top-end effect above) and the correlated stack is the closer of the two
  once that is allowed for -- but this is **not** the clear upper-tail improvement that was hoped for.
- *Scores:* pinball loss over the quantiles improves by 0.19% for stacked lineups (2 standard errors) and
  0.10% overall; the Brier score of P(>= 145) by 0.17% (stacked, 1 SE). **The Brier score of P(>= 190) is
  slightly worse with the correlation (-0.85% for stacked lineups, about 2.5 SE)**, because the extra spread
  raises the predicted GPP probability of lineups whose probability the engine already overstates: the top
  decile's prediction rises from 9.6% to 10.9% against 6.3% to 6.7% realized. On lineups without a player past the top bucket
  the two are indistinguishable (n is small: 12 realized hits at the GPP target).

Taken together: the simulator's lineup distributions are well calibrated where the engine is, correlation makes
a stack's spread right, and its benefit for tail *probabilities* is smaller than the engine's own error at the
top of the range. Whatever improves the top end of the engine will make the correlation matter more, not less.

## Speed and repair

- **Speed:** 8 lineups x 20,000 draws, 58 distinct players, in 0.21 to 0.29 s on this 4-core machine (budget 2 s);
  `tests/sim/test_simulate.py` asserts it under the `speed` marker (`pytest -m "not speed"` skips it).
- **PSD repair:** needed **0 times** with the shipped table: in 10,800 back-test lineups under each marginal set
  (largest change to any entry 0.0000), in 204 random portfolios of eight lineups from 2023-2025 weeks (50 to 60
  players each; smallest eigenvalue +0.42), and in the full two-team block of all 22 roles (smallest eigenvalue
  +0.39). The shrinkage toward 0 is what keeps the pairs consistent. The repair itself is covered by a test with
  a deliberately impossible matrix.


## Limitations

- **No salary in the back-test.** The lineups ignore the cap, so they are not what Sam builds: near-chalk lineups
  with no budget are far stronger than any legal one, which is why they reach the engine's top end. The pool is
  every player the model projects (offense with last-3 xFP of 4 or more, and every defense).
- **Roles are usage proxies.** Prior-three-game usage among the players who played; inactive players are
  unknowable from this data, a player back from injury or newly traded looks like his old usage, and a role is
  not a salary tier or an alignment. The role-average QB1-WR1 correlation (+0.31) averages elite and ordinary
  pairs.
- **The distribution engine's known weak spots carry straight through:** the lowest QB and DST deciles are
  understated ([MODEL.md](MODEL.md)); the highest projections are overstated (table above); and ratios drift
  between eras (the out-of-time marginals above are 1 to 1.5 points worse on coverage than the shipped ones).
  Past the engine's first and last quantile levels (0.01 and 0.99) the CDF is extrapolated linearly.
- **A Gaussian copula** has no tail dependence beyond what the normal scores imply, and the correlation is one
  number per role pair, fitted over 12 seasons. It captures the dependence of surprises; it knows nothing of
  weather, injuries during the game or garbage time except through the history they are in.
- **Different games are independent** -- checked, but a slate-wide effect (weather in one region, an officiating
  crew) is not modelled.
- **The back-test's projections:** the out-of-fold GBM projections for WR and DST were fit with every other
  season, later ones included, held out only for the season itself (the same data the distribution tables
  come from). The correlations themselves are strictly out-of-time (2014-2022 only).
- **Bins in a week are not independent draws:** lineups in one week share players and games, so a reliability bin
  of 1,080 lineups carries less information than 1,080 independent ones; the standard errors shown for the
  paired differences use weeks as the unit.
- **No game-total dependence is modelled.** The tercile analysis found only what chance produces (see above), so
  every pair has one pooled value whatever the game's total.

## Files

| File | What |
|---|---|
| `models/sim/correlations.csv` | The shipped table, pooled values only (`relation, role_a, role_b, total_bucket, rho, n, ci_lo, ci_hi`; `total_bucket` is always `all`). |
| `models/sim/correlations_detail.csv` | Every fitted row, including the report-only tercile rows, with the unshrunk `r_raw`, its interval and the `conditional` flag. |
| `models/sim/correlations_meta.json` | Seed, bootstrap size, shrinkage prior, total cutoffs, the cross-game check. |
| `models/sim/backtest.md` | The complete back-test report (`dfs sim backtest --save`). |
| `src/dfs/sim/` | `marginal.py`, `roles.py`, `correlation.py`, `simulate.py`, `backtest.py`, `pipeline.py`, `report.py`, `cli.py`. |
| `tests/sim/` | Normal scores ~ N(0,1), PSD repair, the copula reproducing a known correlation, a shared player being one variable, cross-game independence, determinism, back-test legality, speed. |

Every random draw is seeded (`--seed`, default 0). `dfs sim fit` takes about 80 s, `dfs sim backtest --jobs 4`
about 5 minutes on four cores (about 35 on one); the results do not depend on `--jobs`.

## In the sheet (the local integration)

**Players.** `sim_inputs.build_specs` makes one `PlayerSpec` per EdgeRaw player, keyed by the name typed on Lineups.
The projection is `CalPts` where present, else TFFB's `ProjPts`; the game id, team and opponent are EdgeRaw's
`GameID`, `Team` and `Opp`. The role is QB1 for a quarterback and DST for a defense; backs, receivers and tight ends
are ranked within their team and position by the depth chart's `pos_rank`, ties and players the chart lacks by current
usage (`Tgt% + Rush%`, the same tiebreak the injury code uses), skipping anyone listed OUT or IR, with ranks past the last
named role folded into it.

**Lines.** The cash line is the median of the last three typed `Cash Line` values in Results (before the slate's week;
read only; Results' Cash columns are cash contests by construction, so no GPP value can be in it). With none typed the
placeholder 145 is used and the sync says so. The GPP target is `[sim] gpp_target` in `config.toml`, default 190, shown in
the column header as `P(190+)`.

**Lineups tab.** `dfs sync` (the full sync and `--live`) simulates every complete lineup together (20,000 draws, seed 0)
and writes `Median`, `p90`, `P(cash)` and `P(<target>+)` onto each lineup's Total row, plus the portfolio line on the first
lineup's `Remaining` row (`Portfolio`, expected cashes under `p90`, P(at least one cashes) under `P(cash)`, P(at least one
reaches the target) under the GPP column). A half-built lineup or an unknown name is left blank. The numbers are not
conditioned on games already played. Changing `gpp_target` renames the header on the next sync; `dfs setup reorder-columns`
puts the canonical `P(190+)` back first so its exact-name comparison still works.

**Late swap.** `dfs lineups late-swap` scores every swap it finds (the full re-fill, the 2-for-2 swaps and the 1-for-1
swaps) with the change in P(cash) and P(GPP): the lineup and all its candidate versions are simulated in ONE run, so the
players they share have identical draws (this is `swap_impact`, generalised from one player to several). `--goal cash|gpp`
(default cash) ranks by the matching change, projection gain breaking ties; the search keeps four times as many candidates
as it shows so a swap that wins on probability but not on projection can surface.
