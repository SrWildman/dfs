# The lineup simulator

`dfs sim ...` answers three questions about a set of DraftKings lineups that no player-level number can:

- **Cash:** the probability a lineup clears the cash line.
- **GPP:** how often a lineup reaches a tournament-winning score.
- **Across lineups:** how often **at least one** of them gets there.

Every edge elsewhere in the tool is per player. Lineups are won and lost on how players move *together* -- a QB
and his WR1 boom together -- and that is what this adds. It uses **public nflverse / ffverse history only**,
through the UM model's existing loaders (`dfs.model`); no sheet, no credentials, no TFFB. Nothing in
`src/dfs/sim/` writes to a sheet. See [MODEL.md](MODEL.md) for the distribution engine it builds on.

`dfs sim` is a **visible command group** (`fit`, `backtest`, `script`, `demo`; the launcher's `MORE_LABELS`,
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
| Relative lines | `field.py` | The field mean per draw: ownership weights, the ownership curve, the 95% cover, the tail's expected points. See "Relative lines". |
| Back-test | `backtest.py` | Random legal lineups for 2023-2025, predicted with correlations fitted on 2014-2022 only. |
| Text | `report.py`, `cli.py` | The tables printed by `dfs sim fit / backtest / demo` and used in this file. |
| Game script | `script.py`, `script_pbp.py`, `script_backtest.py`, `script_report.py` | Spread and implied total against the pooled table, the play-by-play mechanism, the lineup-level bar (`dfs sim script`). |
| Team shape | `shape_table.py`, `script_shape.py` | The one shipped modifier: QB1-TE1 by the tight end's target share (`correlations_shape.csv`), a layer on the pooled table that `simulate_lineups` reads by default. `script_shape.py` is the research behind it. |
| Duplicates | `dups.py` | Expected identical entries, the Top 1% share, the fit. Used by `lineup_suggest.py`. |
| Contest sim (shadow) | `payouts.py`, `field_gen.py`, `contest_sim.py`, `contest_retro.py` | Payout curves, a generated field, E[$] / ROI / P(cash) per lineup per contest, and the retro against his real contests. Not wired into anything but `dfs research refresh`. See "Contest sim (shadow)". |
| Ownership uncertainty (shadow) | `own_error.py`, `own_error_retro.py` | A random draw of the field's ownership around the forecast, fitted and checked on his real contests, held out by week. Not wired into anything yet. See "Ownership uncertainty (shadow)". |

## Using it

```
dfs model fetch      # the cache (once; shared with dfs model)
dfs sim fit          # rebuild models/sim/correlations.csv (about 80 s)
dfs sim backtest --jobs 4   # the back-test below (about 5 minutes on 4 cores, 35 on one; --lineups-per-week 20 for a look)
dfs sim demo         # two lineups from 2025 week 12, one stacked, one not, plus a swap
dfs sim script       # game script: spread and implied total against the pooled table (about 30 minutes; --lineups 0 skips the slowest part)
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
games; the table's `same_team` or `opp` value for the two roles within a game), repair it if needed, draw `z ~ N(0, R)` (as `g @ R^(1/2)` with the PRINCIPAL square root `V sqrt(W) V^T`, not `V sqrt(W)`: the eigenvector signs `eigh` returns differ between LAPACK builds, and with them the draws for one seed, whereas the principal root is the same matrix whichever signs or basis it was built from, so a seed means the same draws on every machine), take `u = Phi(z)`, and read each player's
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
the pooled value of every pair, the simulator takes no game total (a later round added one team-shape layer, QB1-TE1 by the tight end's
target share: "Game script, team shape and the bring-back, tested" below), and this table is the whole of what the
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
| selectivity 1 | 3,562 | 109.3 | 106.1 | 106.3 | 8.3% | 8.8% |
| selectivity 3 | 3,642 | 130.4 | 124.3 | 123.8 | 22.8% | 21.7% |
| selectivity 6 | 3,596 | 149.9 | 140.0 | 138.7 | 41.8% | 39.5% |
| all players in range | 8,710 | 125.7 | 120.2 | 120.0 | 20.8% | 20.4% |
| a player past the top bucket | 2,090 | 147.5 | 137.5 | 135.3 | 39.3% | 35.8% |

"Past the top bucket" means at least one player is projected above where the engine's table ends: the 99.5th
percentile of his position's projections (QB 28.9, RB 26.3, WR 21.7, TE 20.2, DST 13.4), beyond which the
ratio is held flat. The first release's tables ended at the mean projection of their top bucket (QB 24.3, RB
21.2, WR 19.8, TE 14.0, DST 10.5), which put 7,887 of the 10,800 lineups past the end and left 2,913 in
range. With the smooth tables 2,090 are past it, so "all players in range" is no longer the small control
group it was (it fails like everything else); the comparison that isolates the first release's failure is kept
under the verdict, split at the *old* centres. The sum of projections is far above the realized score for the
selective lineups, which is expected (the distribution is skewed and the highest projections regress).

### Coverage (shipped marginals)

Share of realized scores below the predicted quantile. `(+x)` is the gap from the target in points.

All lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.2% (+0.2) | 11.1% (+1.1) | 0.3 |
| p25 | 25% | 25.8% (+0.8) | 26.6% (+1.6) | 0.4 |
| p50 | 50% | 51.4% (+1.4) | 51.5% (+1.5) | 0.5 |
| p75 | 75% | 75.4% (+0.4) | 74.9% (-0.1) | 0.4 |
| p90 | 90% | 90.2% (+0.2) | 89.4% (-0.6) | 0.3 |
| p99 | 99% | 98.9% (-0.1) | 98.6% (-0.4) | 0.1 |

Stacked lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.3% (+0.3) | 12.0% (+2.0) | 0.4 |
| p25 | 25% | 25.8% (+0.8) | 27.3% (+2.3) | 0.6 |
| p50 | 50% | 51.5% (+1.5) | 51.7% (+1.7) | 0.7 |
| p75 | 75% | 74.7% (-0.3) | 73.7% (-1.3) | 0.6 |
| p90 | 90% | 89.6% (-0.4) | 88.0% (-2.0) | 0.4 |
| p99 | 99% | 98.8% (-0.2) | 98.3% (-0.7) | 0.1 |

Random lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.1% (+0.1) | 10.2% (+0.2) | 0.4 |
| p25 | 25% | 25.8% (+0.8) | 25.8% (+0.8) | 0.6 |
| p50 | 50% | 51.4% (+1.4) | 51.3% (+1.3) | 0.7 |
| p75 | 75% | 76.1% (+1.1) | 76.1% (+1.1) | 0.6 |
| p90 | 90% | 90.9% (+0.9) | 90.9% (+0.9) | 0.4 |
| p99 | 99% | 98.9% (-0.1) | 99.0% (-0.0) | 0.1 |

Out-of-time marginals (train-only), all lineups:

| Predicted quantile | Target | Correlated | Independent | SE |
|---|---|---|---|---|
| p10 | 10% | 10.8% (+0.7) | 11.5% (+1.5) | 0.3 |
| p25 | 25% | 26.5% (+1.5) | 27.2% (+2.2) | 0.4 |
| p50 | 50% | 52.3% (+2.3) | 52.5% (+2.5) | 0.5 |
| p75 | 75% | 76.2% (+1.2) | 75.8% (+0.8) | 0.4 |
| p90 | 90% | 90.7% (+0.7) | 89.8% (-0.2) | 0.3 |
| p99 | 99% | 98.9% (-0.1) | 98.8% (-0.2) | 0.1 |

### Reliability of P(score >= cash line) (shipped marginals)

Ten equal-count bins of the predicted probability. `*` marks a gap beyond +/-4 points.

All lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 1,080 | 1.9% | 2.5% | +0.6 | 1.6% | 2.7% | +1.0 | 27 |
| 2 | 1,080 | 5.3% | 6.5% | +1.2 | 4.7% | 6.1% | +1.4 | 70 |
| 3 | 1,080 | 9.1% | 9.2% | +0.1 | 8.3% | 10.1% | +1.8 | 99 |
| 4 | 1,080 | 13.5% | 14.3% | +0.8 | 12.6% | 13.1% | +0.5 | 154 |
| 5 | 1,080 | 18.6% | 19.4% | +0.8 | 17.7% | 20.5% | +2.8 | 209 |
| 6 | 1,080 | 24.4% | 24.4% | +0.1 | 23.7% | 24.0% | +0.3 | 264 |
| 7 | 1,080 | 30.3% | 27.4% | -2.9 | 29.7% | 27.5% | -2.2 | 296 |
| 8 | 1,080 | 37.1% | 36.7% | -0.4 | 36.7% | 36.6% | -0.1 | 396 |
| 9 | 1,080 | 45.1% | 40.9% | -4.2* | 45.0% | 41.1% | -3.9 | 442 |
| 10 | 1,080 | 58.3% | 52.9% | -5.4* | 58.5% | 52.4% | -6.1* | 571 |

### Reliability of P(score >= GPP target) (shipped marginals)

All lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 1,080 | 0.0% | 0.0% | -0.0 | 0.0% | 0.0% | -0.0 | 0 |
| 2 | 1,080 | 0.1% | 0.2% | +0.1 | 0.0% | 0.3% | +0.2 | 2 |
| 3 | 1,080 | 0.2% | 0.5% | +0.3 | 0.1% | 0.5% | +0.3 | 5 |
| 4 | 1,080 | 0.4% | 0.3% | -0.1 | 0.3% | 0.3% | -0.0 | 3 |
| 5 | 1,080 | 0.8% | 0.7% | -0.0 | 0.6% | 0.7% | +0.1 | 8 |
| 6 | 1,080 | 1.4% | 1.0% | -0.3 | 1.1% | 1.5% | +0.4 | 11 |
| 7 | 1,080 | 2.2% | 2.2% | +0.1 | 1.8% | 1.9% | +0.0 | 24 |
| 8 | 1,080 | 3.4% | 2.8% | -0.6 | 2.9% | 2.8% | -0.1 | 30 |
| 9 | 1,080 | 5.3% | 4.6% | -0.6 | 4.7% | 4.5% | -0.2 | 50 |
| 10 | 1,080 | 10.2% | 8.1% | -2.2 | 9.7% | 8.0% | -1.7 | 87 |

Stacked lineups:

| Bin | n | Corr. predicted | Corr. realized | Gap | Indep. predicted | Indep. realized | Gap  | Hits (corr.) |
|---|---|---|---|---|---|---|---|---|
| 1 | 540 | 0.0% | 0.0% | -0.0 | 0.0% | 0.0% | -0.0 | 0 |
| 2 | 540 | 0.1% | 0.4% | +0.3 | 0.0% | 0.4% | +0.3 | 2 |
| 3 | 540 | 0.2% | 0.6% | +0.3 | 0.1% | 0.6% | +0.4 | 3 |
| 4 | 540 | 0.4% | 0.6% | +0.1 | 0.3% | 0.4% | +0.1 | 3 |
| 5 | 540 | 0.8% | 0.6% | -0.2 | 0.5% | 0.9% | +0.4 | 3 |
| 6 | 540 | 1.2% | 1.3% | +0.1 | 0.8% | 0.7% | -0.1 | 7 |
| 7 | 540 | 1.9% | 2.6% | +0.7 | 1.4% | 3.1% | +1.8 | 14 |
| 8 | 540 | 2.9% | 2.2% | -0.6 | 2.1% | 2.0% | -0.1 | 12 |
| 9 | 540 | 4.3% | 2.8% | -1.6 | 3.4% | 3.0% | -0.4 | 15 |
| 10 | 540 | 8.5% | 7.2% | -1.2 | 7.2% | 7.0% | -0.1 | 39 |

### Spread: does the correlation matter?

| Lineups | n | Independent mean sd | Independent E[z^2] | Correlated mean sd | Correlated E[z^2] |
|---|---|---|---|---|---|
| all | 10,800 | 25.27 | 1.082 | 26.18 | 1.003 |
| stacked | 5,400 | 25.03 | 1.188 | 26.75 | 1.039 |
| random | 5,400 | 25.51 | 0.977 | 25.62 | 0.968 |
| in-range | 8,710 | 24.94 | 1.085 | 25.87 | 1.005 |

The mean of ((realized - mean) / sd)^2 is 1 for a calibrated spread. And as paired differences (correlated
against independent; weeks are the unit, because lineups in a week share players and games):

| Lineups | Measure | Independent | Correlated | Improvement | Better by |
|---|---|---|---|---|---|
| all | Pinball loss (p10-p99) | 6.187 | 6.176 | +0.011 (SE 0.003) | +0.18% |
| all | Brier, P(>= 145) | 0.15658 | 0.15627 | +0.00031 (SE 0.00014) | +0.20% |
| all | Brier, P(>= 190) | 0.01959 | 0.01961 | -0.00003 (SE 0.00004) | -0.14% |
| stacked | Pinball loss (p10-p99) | 6.438 | 6.418 | +0.020 (SE 0.006) | +0.31% |
| stacked | Brier, P(>= 145) | 0.15549 | 0.15511 | +0.00038 (SE 0.00026) | +0.24% |
| stacked | Brier, P(>= 190) | 0.01753 | 0.01757 | -0.00004 (SE 0.00006) | -0.22% |
| random | Pinball loss (p10-p99) | 5.936 | 5.934 | +0.003 (SE 0.002) | +0.04% |
| random | Brier, P(>= 145) | 0.15767 | 0.15742 | +0.00024 (SE 0.00006) | +0.15% |
| random | Brier, P(>= 190) | 0.02164 | 0.02166 | -0.00002 (SE 0.00002) | -0.08% |
| in-range | Pinball loss (p10-p99) | 6.110 | 6.100 | +0.012 (SE 0.004) | +0.19% |
| in-range | Brier, P(>= 145) | 0.14425 | 0.14393 | +0.00033 (SE 0.00016) | +0.23% |
| in-range | Brier, P(>= 190) | 0.01374 | 0.01376 | -0.00001 (SE 0.00003) | -0.07% |

### Verdict

Shipped marginals:

| Lineups | Simulation | Max coverage gap | Coverage within 3 | Max cash-bin gap | Max GPP-bin gap | Reliability within 4 |
|---|---|---|---|---|---|---|
| all | Correlated | 1.4 | yes | 5.4 | 2.2 | NO |
| all | Independent | 1.6 | yes | 6.1 | 1.7 | NO |
| stacked | Correlated | 1.5 | yes | 9.0 | 1.6 | NO |
| stacked | Independent | 2.3 | yes | 9.6 | 1.8 | NO |
| random | Correlated | 1.4 | yes | 4.6 | 3.8 | NO |
| random | Independent | 1.3 | yes | 4.8 | 3.0 | NO |
| in-range | Correlated | 0.9 | yes | 4.5 | 2.3 | NO |
| in-range | Independent | 1.1 | yes | 5.1 | 1.5 | NO |

Out-of-time marginals (train-only):

| Lineups | Simulation | Max coverage gap | Coverage within 3 | Max cash-bin gap | Max GPP-bin gap | Reliability within 4 |
|---|---|---|---|---|---|---|
| all | Correlated | 2.3 | yes | 7.1 | 3.0 | NO |
| all | Independent | 2.5 | yes | 7.6 | 2.5 | NO |
| stacked | Correlated | 2.2 | yes | 10.1 | 2.1 | NO |
| stacked | Independent | 3.0 | NO | 11.4 | 1.5 | NO |
| random | Correlated | 2.3 | yes | 6.0 | 4.2 | NO |
| random | Independent | 2.5 | yes | 6.1 | 3.8 | NO |
| in-range | Correlated | 1.7 | yes | 5.4 | 2.7 | NO |
| in-range | Independent | 1.9 | yes | 5.9 | 1.9 | NO |

(The full set of tables, including every group for both marginal sets, is `models/sim/backtest.md`.)

Against the first release (correlated simulation; max coverage gap / max cash-bin gap / max GPP-bin gap, in points):

| Lineups | Shipped marginals, before | Shipped marginals, after | Out-of-time marginals, before | Out-of-time marginals, after |
|---|---|---|---|---|
| all | 2.5 / 9.3 / 5.5 | 1.4 / 5.4 / 2.2 | 3.4 / 9.8 / 5.7 | 2.3 / 7.1 / 3.0 |
| stacked | 2.6 / 12.1 / 4.2 | 1.5 / 9.0 / 1.6 | 3.3 / 12.8 / 4.3 | 2.2 / 10.1 / 2.1 |
| random | 2.9 / 6.2 / 6.4 | 1.4 / 4.6 / 3.8 | 3.5 / 7.5 / 6.5 | 2.3 / 6.0 / 4.2 |

**Coverage: accepted**, for the shipped marginals (largest gap 2.9 before, 1.5 now) and now for the out-of-time
marginals too: the first release's failed at 3.3 to 3.5 for the correlated simulation, they are at 2.2 to 2.3
now (the independent stacked run, 3.0, is at the line). Realized scores fall below the predicted median 51.4%
of the time, not 52.5%.

**Reliability: much improved, still not accepted as specified, and what is left is not the engine's top end.**

- *The GPP target is accepted* with the shipped marginals for all, stacked and random lineups: no bin is beyond
  4 points (the largest gaps are 2.2, 1.6 and 3.8; they were 5.5, 4.2 and 6.4). With the out-of-time marginals
  the largest are 3.0, 2.1 and 4.2, random lineups 0.2 over.
- *The cash line is not*: with the shipped marginals the bins beyond 4 points are the top two for all lineups
  (-4.2, -5.4), the top two for stacked lineups (-4.6, -9.0) and bin 6 for random lineups (-4.6). Before they
  were the top two or three deciles, over-predicted by 5.8 to 12.1 points.
- *The lineups that failed before no longer do, to the extent the engine can fix it.* Split at the first
  release's top-bucket centres (correlated, max coverage gap / max cash-bin gap / max GPP-bin gap):

| Lineups | n | First release | Smooth tables |
|---|---|---|---|
| no player past the old top-bucket centre, shipped | 2,913 | 1.6 / 2.0 / 1.2 | 1.5 / 2.7 / 0.7 |
| a player past the old top-bucket centre, shipped | 7,887 | 3.7 / 9.4 / 6.6 | 2.2 / 6.7 / 3.3 |
| a player past the old top-bucket centre, out-of-time | 7,887 | 4.3 / 9.9 / 6.8 | 2.9 / 8.4 / 4.2 |

- *What is left is a level, not a shape.* In the top decile of P(cash), the predicted standard deviation is
  28.6 points and the realized root-mean-square residual 29.2: the spread is right. The mean is not: realized
  scores sit 3.9 points under the simulated means (0.4 a player), where the first release's sat 7.3 under. By
  season, the top-decile cash gap is -3.1 (2023), +2.1 (2024) and -9.3 (2025), the first two inside +/-4.
  2025 is the whole story: its lineups ran 3.7 points under the simulated means across the board (2023: +0.5,
  2024: +1.5), the same season-level shift [MODEL.md](MODEL.md) finds in the engine itself (2025 scored 3.6%
  under the twelve-season tables, WR and DST 7.6%). A table fitted to twelve seasons cannot see a season's
  scoring level; the recency weighting MODEL.md names as the next step is the fix, and it is not done here.
- *The ±4 line is at the noise level for the top decile.* The chalk lineups of a week share the same few
  players, so 1,080 lineups carry far less information than 1,080 independent ones. Resampling whole weeks
  (2,000 draws), the top-decile gap's standard error is 4.5 points for the cash line and 2.2 for the GPP
  target: the remaining cash gaps are 1.2 standard errors (all lineups) and 2.0 (stacked), the GPP gaps 1.0
  and 0.7.

**Does the correlated simulation do better? Yes on spread and the lower and middle quantiles; clearly on the
pinball loss now; not on the upper-tail GPP probability.** The honest numbers:

- *Spread:* for stacked lineups the independent simulation is too narrow (mean squared standardized residual
  1.19) and the correlated one is right (1.04). For random lineups the two are the same (0.98 independent, 0.97
  correlated), as they should be. The mean predicted sd of a stack grows from 25.0 to 26.8 points.
- *Quantiles of stacked lineups:* the correlated simulation is closer to the target at every level: p10 gap +0.3
  against +2.0 independent, p25 +0.8 against +2.3, p90 -0.4 against -2.0, p99 -0.2 against -0.7.
- *Scores:* pinball loss over the quantiles improves by 0.31% for stacked lineups (3.3 standard errors) and
  0.18% overall (3.7); the Brier score of P(>= 145) by 0.24% (stacked, 1.5 SE) and 0.20% (overall, 2.2 SE). **The
  Brier score of P(>= 190) is slightly worse with the correlation** (-0.22% stacked, -0.14% overall, under one
  standard error either way; it was -0.85%, about 2.5 SE, before the engine was fixed): the extra spread raises
  the top decile's predicted GPP probability for stacks from 7.2% (independent) to 8.5% (correlated), against
  7.0% and 7.2% realized in the two runs' top bins, so the independent simulation is closer there.

Taken together: with the engine's top end fixed, the correlation earns a clear, if small, improvement in the
lineup distribution (spread, quantiles, pinball loss and the cash probability) and is neutral for the GPP
probability. What stands between the simulator and the cash-line acceptance rule is the season-level scoring
shift, which neither the copula nor a static table can model.

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
- **The distribution engine's known weak spot carries straight through: ratios drift between eras and
  seasons.** The tables pool twelve seasons, so a season that scores low as a whole shifts every probability:
  2025 ran 3.7 points per lineup under the simulated means (2023: +0.5, 2024: +1.5), and the out-of-time marginals
  are about 1 point worse on coverage than the shipped ones (2.3 against 1.4). The first release's other two weak
  spots (the lowest QB and DST deciles understated, the highest projections overstated) were fixed in the
  engine ([MODEL.md](MODEL.md)), except one that is not a table-shape problem: the tables include one- and
  two-game backup QBs, who score far less than the starters-with-history the back-test draws from, so a QB
  projected under 14 with three prior games has actual / engine 1.22 (MODEL.md, Limitations). Past the
  engine's first and last quantile levels (0.01 and 0.99) the CDF is extrapolated linearly.
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
- **No game-total, spread or implied-total dependence is modelled.** The tercile analysis found only what chance
  produces (see above) and the spread / implied-total study did not beat the pooled table out of sample ("Game
  script"), so every pair has one pooled value. **The Gaussian copula under-states joint booms** of stack pairs by
  30-45% (same section).

## Files

| File | What |
|---|---|
| `models/sim/correlations.csv` | The shipped table, pooled values only (`relation, role_a, role_b, total_bucket, rho, n, ci_lo, ci_hi`; `total_bucket` is always `all`). |
| `models/sim/correlations_detail.csv` | Every fitted row, including the report-only tercile rows, with the unshrunk `r_raw`, its interval and the `conditional` flag. |
| `models/sim/correlations_meta.json` | Seed, bootstrap size, shrinkage prior, total cutoffs, the cross-game check. |
| `models/sim/backtest.md` | The complete back-test report (`dfs sim backtest --save`). |
| `models/sim/script.md` | The complete game-script report (`dfs sim script --save`). |
| `src/dfs/sim/` | `marginal.py`, `roles.py`, `correlation.py`, `simulate.py`, `field.py`, `backtest.py`, `pipeline.py`, `report.py`, `cli.py`. |
| `tests/sim/` | Normal scores ~ N(0,1), PSD repair, the copula reproducing a known correlation, a shared player being one variable, cross-game independence, determinism, back-test legality, speed; `test_field.py`: the field-mean identity, relative lines, the ownership curve. |

Every random draw is seeded (`--seed`, default 0). `dfs sim fit` takes about 80 s, `dfs sim backtest --jobs 4`
about 5 minutes on four cores (about 35 on one); the results do not depend on `--jobs`.

## Relative lines

**The problem.** The simulator used to score every lineup against two fixed numbers: `Cash %` = P(lineup >= the
season-median typed cash line, 147.56 at the time) and `190+ %` = P(lineup >= 190). A contest's lines are not fixed:
they move with the slate, because **the field holds the same players you do**. The field mean (the mean entry
score) is exactly `sum over players of %Drafted x FPTS`, and the lines that pay sit a stable distance above it.
Measured on Sam's 20 real standings files (Weeks 2, 4 and 5, 2026), the line minus *that contest's* field mean:

| Line, minus that contest's field mean | n contests | mean | sd |
|---|---|---|---|
| Double-up cash line | 7 | **+0.6** | 4.9 |
| GPP min-cash (21-26% paid) | 13 | **+17.9** | 1.9 |
| GPP top 10% | 9 | **+31.9** | 1.2 |
| GPP top 1% (fields of 700+) | 9 | **+63.2** | 5.7 |

The field mean itself swung 33 points between weeks: about 110 in Week 4 (the chalk busted; the double-up line was
106) against about 143 in Week 5. So a fixed line is wrong by up to 40 points in a given week.

It is also wrong in a way that changes the *ranking*. When the fixed line sits above a lineup's median (it did: the
Suggested cash builds showed Typical about 129 against a line of 147.6, Cash % 22-25%), maximising P(score > line)
**rewards variance**, the opposite of a cash build, and it is why the GPP builds showed higher Cash % than the
cash builds. Against a line that moves with the field the lineup that wins is the one that beats the field.

**The method** (`field.py`, `simulate_lineups(..., field=...)`; pure functions, deterministic under the seed):

1. **Weights.** Each player's projected ownership (EdgeRaw's `Own%`, a fraction) goes through the optional
   per-contest-type curve (below) and is normalised to sum to **9**, one lineup's worth. Only players the simulator
   can rate (a projection) and that have an ownership number are in the field; ownership on a player it cannot rate
   is dropped before normalising, never guessed.
2. **The simulated set** is the union of the lineups' players and the **top-owned players, enough to cover at least
   95% of the total ownership** (`[sim.lines] own_coverage`), taking the higher of the two contest types' needs. They
   join the same Gaussian-copula draw, so a player in both a lineup and the field is **one random variable**: a lineup
   of chalk is correlated with the line it is measured against, and a lineup of strangers is not.
3. **The field mean, per draw** = `sum of own_i x X_i` over the simulated field players, plus the **tail** (the
   remaining ownership, under 5%) at each tail player's *expected* points (the mean of his own quantile function; a
   constant per run). Leaving the tail out would bias the line down by about 5% of the slate's points; rescaling the
   covered weights up to 9 would bias it up (the uncovered are the low scorers).
4. **The lines, per draw:** `Cash %` = P(lineup >= field mean + `double_up_offset`) and the top-end column =
   P(lineup >= field mean + `gpp_top1_offset`), from `[sim.lines]` (defaults: the table above). The min-cash and
   top-10% offsets are stored in the same block for reference; the simulator does not report them yet
   (`SimResult` keeps each draw's lines with `keep_scores`, so any other offset is one subtraction away).
   The per-draw thresholds also drive `p_any_cash`, `p_any_gpp` and `expected_cashing` (the portfolio line).

The column formerly `190+ %` is **`Top 1%`** in code, notes, docs and on the sheet (Lineups, Suggested and the
Instructions tab, template and Week 6, relabelled in the local round after this one landed); its numbers are
P(top 1% line), not P(>= 190), except with the fixed lines, where the hover note says it is the chance of reaching
`[sim] gpp_target`. `GPP_HEADER_RE` stays only to recognise the retired far-right `P(190+)` column.

**Column notes.**

- **`Cash %`**: the chance the lineup beats the double-up cash line. That line moves with the slate: it sits about 0.6
  points above the field's average score, which the simulator works out in every draw from projected ownership.
  Green at 50% or more. (Before: "beats a typical cash line, the median of your typed Cash Lines".)
- **`Top 1%`**: the chance the lineup finishes in the top 1% of a large-field GPP, the field's average score plus
  63.2 points (the line moves with the slate). An average entry has about 1%; the more above that, the better a GPP
  build. (Before: P(score >= 190), "a typical tournament-winning pace".) Green at 2% or more (twice that).

**Ownership is a large-field projection.** TFFB's `Own%` describes a large-field contest. Double-ups are much chalkier:
in Sam's files, players projected over 15% were actually owned about 39% on average, about 15 points above projection.
`[sim.lines] double_up_own_curve` and `gpp_own_curve` take a piecewise-linear `[projected, expected]` curve (fractions,
anchored at (0, 0) and (1, 1), strictly increasing in `projected`, non-decreasing in `expected`) that maps projected
Own% to the Own% each contest type is expected to have; **both default to empty, the identity.** The cash line uses the
double-up field, the top-end line the GPP field. The local session fitted them on Sam's files (next paragraph): both stay empty.

**Fitted on Sam's files (Weeks 4 and 5): both curves stay empty, the identity.** Projected `Own%` is TFFB's last
snapshot before the slate's kickoff (`data/raw/projections`); actual is the contest's `%Drafted` summed over a player's
slot rows (`data/ownership_log.csv`; it sums to 9.0 per contest, and actual ownership x actual points reproduces the
contest's real field mean to within 0.05 points in 17 of 20 files, 1.5 in the three 100-entry ones). The
double-up files exist only for Weeks 4 and 5 (Week 2 has one 100-entry GPP), so that curve rests on two slates.

- *GPP fields of 700+ (9 contests): the fit is the identity.* Binned means sit within about 10% of projection at every
  level above 1%; a four-knot fit beats the identity by 6% of squared error in sample and is no better held out by week.
  Field mean from projected ownership x actual points minus the real field mean: mean +2.3, mean absolute 2.5.
- *Double-ups (7 contests): the fit is a steep convex curve.* A player projected 4% / 10% / 20% / 35% was owned
  about 1.9% / 4.0% / 31% / 67% (scaled so the mapped ownership sums to 9; Week 4 and Week 5 fitted alone agree to
  within 0.08), and the curve beats the identity out of sample on the players' own ownership (held-out squared error 2.7 vs
  4.9 and 1.2 vs 3.1). **It still makes the field mean worse**, so it is not saved:

| Week | Contest (entries) | Real field mean | Identity | Gap | With the double-up curve |
|---|---|---|---|---|---|
| 4 | Double Up (4,597) | 110.4 | 122.0 | +11.7 | +18.0 |
| 4 | Double Up (2,758) | 110.0 | 122.0 | +12.1 | +18.4 |
| 4 | Double Up (2,873) | 110.0 | 122.0 | +12.1 | +18.4 |
| 4 | Double Up (3,448) | 110.5 | 122.0 | +11.6 | +17.9 |
| 5 | Double Up (4,433) | 142.2 | 135.9 | -6.2 | +9.8 |
| 5 | Double Up (2,758) | 144.8 | 135.9 | -8.9 | +7.2 |
| 5 | Double Up (3,995) | 143.1 | 135.9 | -7.2 | +8.9 |

  The curve gets the chalk's total share right (Week 4: players projected at 20% or more held 3.54 of the 9 slots,
  the curve says 3.54, identity 1.9) but spreads it by projection, and the double-up field does not: it piles into
  **specific value plays** (Week 4: Trevor Lawrence owned 62% against 16% projected; Week 5: Jacoby Brissett 74% against
  25%), so a level curve over-weights the projected chalk that the field passed over (Tee Higgins 19% projected, 6.5%
  owned, 30 points). **The remaining field-mean error under the identity (Week 4 +12, Week 5 -7) comes from that
  player-specific double-up chalk, not from a level.** Revisit at the Week 8 recalibration as a per-player double-up
  ownership model (value, salary, closeness to the field's mega-chalk build), using the standings files; until then
  `double_up_own_curve` and `gpp_own_curve` stay empty and Cash % is measured against a field mean that is right on
  average and wrong by about 10 points in either direction in a given week.

**Before ownership publishes** (`Own%` is blank until TFFB computes it midweek) there is no field to measure
against, so there are no relative lines. The simulator falls back to the FIXED lines, **and says so** (the sync summary,
`dfs lineups suggest` and `late-swap` print "Ownership has not published ... using FIXED lines", and the Lineups hover
note stops describing relative lines): `[sim.lines] fallback_cash_line` and `fallback_top1_line`. Left unset (the
default) these are the old fixed numbers: the median of the typed Cash Lines in Results (147.56 now, else the 145
placeholder) and `[sim] gpp_target` (190). Set a number to pin either.

**`--fixed-lines`** (on `dfs sync`, `dfs lineups suggest` and `dfs lineups late-swap`) forces the old fixed lines even
when ownership is published, for one season so the old and new numbers can be compared. `simulate_lineups` without a
`field` is the old simulator unchanged (a test pins today's numbers: the same draws, the same P(cash) and P(GPP)).

**What a run costs.** The simulated set grows from the lineups' players to the union with the field's cover. On a
synthetic 14-team slate (154 rated players, ownership spread so that 95% takes 129 of them) that was 111 extra
players beyond the 18 in four lineups: 0.06 s became 0.45 s for the four lineups. The Suggested builds simulate
hundreds of lineups at once (300 cash candidates over 61 distinct players, 1,120 stacked GPP candidates over 113), where
the per-lineup work dominates and the time is unchanged (1.2 s and 4.9 s either way). A real slate's ownership is more
concentrated than that synthetic one, so its cover is smaller. Ownership on EdgeRaw is read from the last sync; nothing
here makes a network call.

**The two assumptions that matter, stated plainly:** the offsets are measured on 20 contests from three weeks (sd 4.9
for the double-up, 5.7 for the top 1%), so they will move when the local session adds weeks; and a player's ownership
is taken as given by the projection (or the curve), not conditioned on how the week actually went, which is the same
information a lineup builder has before kickoff.

**Why equal means no longer reward variance (test).** Two lineups with the same nine marginals, one independent and
one built from three correlated QB-WR pairs, have means 131.0 and 131.3 and standard deviations 25.3 and 30.2. Against
a fixed line 25 points above the mean, P(cash) is 16.1% and 20.3%: the risk-taker wins, by construction. Against a
field of the same size and shape with `double_up_offset = 0`, it is 50.1% and 49.6%: it does not
(`tests/sim/test_field.py::test_variance_is_rewarded_by_a_fixed_line_above_the_median_but_not_by_the_relative_line`).

## In the sheet (the local integration)

**Players.** `sim_inputs.build_specs` makes one `PlayerSpec` per EdgeRaw player, keyed by the name typed on Lineups.
The projection is `CalPts` where present, else TFFB's `ProjPts`; the game id, team and opponent are EdgeRaw's
`GameID`, `Team` and `Opp`. The role is QB1 for a quarterback and DST for a defense; backs, receivers and tight ends
are ranked within their team and position by the depth chart's `pos_rank`, ties and players the chart lacks by current
usage (`Tgt% + Rush%`, the same tiebreak the injury code uses), skipping anyone listed OUT or IR, with ranks past the last
named role folded into it.

**Lines.** Cash % and the top-end number are measured against lines that move with the slate (see "Relative
lines"): the field's average score plus 0.6 (double-up cash line) and plus 63.2 (top 1%), from `[sim.lines]`. With
`--fixed-lines`, or until ownership publishes, they are the old fixed lines: the cash line is the median of every typed
`Cash Line` in Results this season (all weeks before the slate's week; read only; Results' Cash columns are cash
contests by construction, so no GPP value can be in it; with none typed the placeholder 145 is used and the sync says
so) and the GPP target is `[sim] gpp_target` in `config.toml`, default 190 (the label stays `Top 1%`).

**Lineups tab.** `dfs sync` (the full sync and `--live`) simulates every complete lineup together (20,000 draws, seed 0; with relative lines, the top-owned players of the whole
slate join the run, so EdgeRaw's specs are built for every team, not only the lineups')
and writes `Typical`, `Big night`, `Cash %` and `Top 1%` onto each lineup's Total row (under `Val`..`Boom%` since 2026-10-17, found by header name: `sheet_lineup_sim.SIM_SLOT_HEADERS`; labels on the Remaining row), plus the portfolio line (now on the Board) from the first
lineup's `Remaining` row (`Portfolio`, expected cashes under `p90`, P(at least one cashes) under `P(cash)`, P(at least one
reaches the target) under the GPP column). A half-built lineup or an unknown name is left blank. The numbers are not
conditioned on games already played. Changing `gpp_target` no longer renames anything: the label is always `Top 1%`.

**Late swap.** `dfs lineups late-swap` scores every swap it finds (the full re-fill, the 2-for-2 swaps and the 1-for-1
swaps) with the change in P(cash) and P(GPP): the lineup and all its candidate versions are simulated in ONE run, so the
players they share have identical draws (this is `swap_impact`, generalised from one player to several). `--goal cash|gpp`
(default cash) ranks by the matching change, projection gain breaking ties; the search keeps four times as many candidates
as it shows so a swap that wins on probability but not on projection can surface.

## Game script, team shape and the bring-back, tested

Cloud task G, 2026-10-17/18. The tercile analysis above tested only the game **total**. This tests three more things,
fit on 2014-2022 (38,332 player-games) and judged on 2023-2025 (12,935), on the same normal scores:

- the closing **spread** (team-relative: positive = this team favoured; buckets BD / SD / SF / BF = big dog, small dog,
  small favourite, big favourite, the line at a touchdown) and the team's **implied total** (training terciles 21.0 and
  24.5) as modifiers of every one of the 110 role pairs that has a team-relative orientation;
- **team shape**: the receiver's or back's target share, a QB's carry share, the team's pass rate over expected (PROE)
  and its target concentration, as modifiers of the QB's pairs (and WR1-WR2), three previous games only;
- the **bring-back's lift** to the stack's tail, by the stacked team's spread (Sam's question: does it vanish or turn
  negative when the stacked team is a big favourite?).

Code: `script.py` (spread and implied), `script_shape.py` (team shape), `script_bringback.py`, `script_pbp.py` (the
mechanism), `script_backtest.py` (the lineup-level bar), `script_report.py`; **`dfs sim script`** reruns all of it (the
report is `models/sim/script.md`).

### Ruling: nothing conditional passes the bar. The pooled table stands, plus one pair Sam chose to ship (QB1-TE1).

The bar (`script_backtest.py`): a conditional table ships only if, out of sample, (1) it beats the pooled one on the
pinball loss of the lineup quantiles by more than two paired SE under BOTH marginal sets, (2) it does not lose on
either Brier score by more than one SE, and (3) its held-out pair-likelihood gain is positive and beats the
shuffled-context (or shuffled-feature) control with the 5% error shared across the six tables examined: 0.83% each.
16,200 lineups (50 each of random, stack, RB1 + own DST, game stack, QB + own TE1 and QB + own RB1, per week, 2023-2025),
every table on the same draws. `script_report.ruling` prints this table:

| Conditional table | Pinball t (shipped / train-only) | Control share | (1) pinball | (2) no Brier loss | (3) control | Ships |
|---|---|---|---|---|---|---|
| spread, all 110 pairs | +1.4 / +1.7 | 0.017 | NO | NO | NO (gain -0.05) | NO |
| spread, the 11 named pairs | +1.8 / +2.4 | 0.017 | NO | yes | NO (1.7% against 0.83%) | NO |
| implied total, all pairs | +0.2 / +0.7 | 0.007 | NO | NO | yes | NO |
| **team shape: receiver target share** (6 tests) | +0.4 / +0.6 | **0.000** | NO | yes | **yes** | NO |
| team shape: all 22 tests | +0.3 / +0.4 | **0.000** | NO | yes | **yes** | NO |

**The spread and the implied total are not worth carrying.** Scanning the training seasons, 11 of 110 pairs have a spread
slope with |t| > 1.96 and 13 have an implied-total slope, against 5.8 and 5.6 when the games' contexts are shuffled among
games of the same season; the largest |t| (2.8 and 3.6) is inside the shuffled 95th percentile. The fitted tables carry
a faint real component (a table fitted on shuffled contexts loses 0.22 / 0.18 log likelihood per 1,000 held-out pairs
against the pooled value, the real ones lose 0.05 / gain 0.02) and no net gain. The best of them, spread on the 11 named
pairs, gains +0.265 per 1,000 held-out pairs (SE 0.149, better than 98.3% of 300 shuffled tables) and improves the
lineup pinball loss by 0.004-0.005%, 40 to 50 times smaller than the 0.18% the correlation itself buys over independence.

**Team shape is real, and the lineup-level measures cannot see it.** The receiver-target-share table is better than
**all 300** shuffled-feature tables (held-out gain +1.28 per 1,000 pairs, SE 0.49; the shuffled ones average +0.05, 95th
percentile +0.58), driven by the tight end: **QB1-TE1 by the TE's target share gains +4.47 per 1,000 (SE 1.30, 3.4 SE)**.
But a correlation that moves from +0.19 to +0.31 for one of nine players is a rounding error in a 25-point-sd
lineup, so the pinball loss moves by 0.002-0.011% (t +0.3 to +1.1; the QB + TE family alone: +0.006%, t +0.9 shipped,
+0.007%, t +1.1 train-only) and the top-end Brier score is a hair worse (-0.05%, t -1.7 / -1.4). **By the task's bar it
does not ship, and the bar is the problem, not the effect**: a pair-level likelihood is the sensitive test of a
correlation, and it says yes; the lineup-level measures are blunt.

**Decision (Sam, 2026-10-18): ship QB1-TE1 only.** The other five receiver pairs (QB1 with RB1, RB2, WR1, WR2 and WR3, by the
receiver's target share) **stay at the pooled value and are to be re-tested later, not dropped**: the research code still
carries all 22 tests (`script_shape.TESTS`, rerun with `dfs sim script`), and a pair is added to the shipped table by
adding it to `shape_table.SHIPPED_TESTS` and refitting. What ships:

- `models/sim/correlations_shape.csv` (three rows, written by `dfs sim fit` beside `correlations.csv`): the raw
  normal-score correlation of QB1 and TE1 in each third of the tight end's `Tgt%` -- low (under 12.9%) +0.19 on 1,683
  pairs, mid +0.29, high (18.3% and up) +0.33 on 1,693 -- and the two cut points;
- `shape_table.py`: the fit, the file, and `default_lookup()`, the pooled table with the `ShapeLookup` layer on top, which
  `simulate_lineups` now reads when it is not handed a table. Each third is shrunk toward the pooled value with the pooled
  table's prior, so the loaded QB1-TE1 correlation is +0.20 at a 5% target share, +0.28 at 15% and +0.32 at 25%, against
  the pooled +0.25. A tight end with no `Tgt%` (week 1, a rookie) is the pooled +0.25;
- `sim_inputs.build_specs` sets `PlayerSpec.tshare` from EdgeRaw's `Tgt%`. No sheet column, tab or formula changed.

**The shipped feature is the live one, and it tests weaker than the research one.** The research feature is the mean of a
player's per-game target shares over his three previous games, across the season boundary (+4.47 per 1,000 held-out pairs,
3.4 SE). EdgeRaw's `Tgt%` is a ratio of window sums over his last three games played THIS season, blank in week 1 (94% of TE1
rows have it). The table is fitted on that quantity (it is what `build_specs` hands the simulator), fitted on 2014-2022 and
judged on 2023-2025: **+3.25 per 1,000 held-out pairs (SE 1.59, 2.0 SE); 1.5% of 200 shuffled-feature tables do as well.**
Same shape, still positive and still beyond chance, but not the 3.4 SE the write-up quoted for the research feature: the
case for the pair is firm, its size is not. (Whether the research feature is worth computing live is for the re-test.)

### The named pairs, by bucket (training seasons, unshrunk r)

| Pair | pooled | BD | SD | SF | BF | t (spread) | t (implied) |
|---|---|---|---|---|---|---|---|
| RB1 with his own DST | +0.061 | +0.093 | +0.068 | +0.050 | -0.006 | -0.5 | -1.0 |
| RB1 with the opposing DST | -0.236 | -0.195 | -0.246 | -0.247 | -0.188 | -1.1 | -2.1 |
| QB1 with WR1 | +0.333 | +0.314 | +0.343 | +0.327 | +0.338 | -0.2 | +0.8 |
| QB1 with WR2 | +0.284 | +0.243 | +0.272 | +0.302 | +0.302 | +1.7 | +3.0 |
| QB1 with TE1 | +0.260 | +0.255 | +0.254 | +0.248 | +0.291 | +1.2 | +2.0 |
| QB1 with the opposing WR1 (bring-back) | +0.085 | +0.051 | +0.085 | +0.108 | +0.078 | +1.4 | +0.5 |
| WR1 with WR2 (cannibalisation) | +0.003 | +0.065 | +0.007 | -0.020 | -0.012 | -1.6 | -1.3 |
| favourite's RB1 with the dog's WR1 | +0.020 | -0.001 | +0.027 | +0.025 | +0.026 | +0.4 | +0.2 |
| favourite's RB1 with the dog's WR2 | +0.008 | -0.062 | +0.006 | +0.037 | +0.023 | +1.8 | +2.3 |
| favourite's RB1 with the dog's TE1 | +0.041 | +0.032 | +0.057 | +0.025 | +0.067 | +0.3 | +0.5 |
| QB1 with the opposing RB1 | +0.025 | +0.040 | +0.033 | +0.040 | -0.011 | -0.2 | +0.1 |

(`models/sim/script.md` has the implied-total buckets and the multiplicity-adjusted p.) The two ideas the task named do
not show up: **RB1 + own DST is not stronger for favourites** (+0.09 for big dogs, -0.01 for big favourites; the 4for4 claim
is a statement about the DST scoring 15+, which is a tail event, not a bucket correlation), and **the favourite's RB1 is
not tied to the dog's receivers** (+0.02 to +0.04 in every bucket).

### What a bring-back adds, by the stacked team's spread (Sam's question)

Empirical, 2014-2025 actual DK points (`script_bringback.py`). Lift = P(stack + the other side's player reaches the tail)
/ P(stack + an unrelated player of the same role, drawn from games with the same spread bucket and season, does); tail
thresholds are those of the unrelated pairing over all games. Above 1 the bring-back helps, at 1 it adds nothing, below 1
it hurts. 90% intervals resample games; (hits) are the top-1% counts, which are small.

| Stack + the other side's | Tail | BD | SD | SF | **BF (big favourite)** | all |
|---|---|---|---|---|---|---|
| QB+WR1+WR2 + WR1 | top 10% | 1.16 | 1.02 | 1.04 | **1.05** [0.97, 1.12] | 1.05 |
| QB+WR1+WR2 + WR1 | top 5% | 1.03 | 1.22 | 1.20 | **1.10** [0.95, 1.23] | 1.17 |
| QB+WR1+WR2 + WR1 | top 1% | 1.93 (7) | 1.44 (30) | 1.35 (32) | **1.44** [0.94, 2.01] (15) | 1.44 [1.24, 1.67] (84) |
| QB+WR1+WR2 + TE1 | top 5% | 0.98 | 1.01 | 1.08 | **1.05** [0.96, 1.15] | 1.04 |
| QB+WR1+WR2 + TE1 | top 1% | 0.00 (0) | 1.16 (21) | 1.16 (22) | **0.79** [0.51, 1.08] (10) | 1.00 [0.84, 1.16] (53) |
| QB+WR1 + WR1 | top 1% | 1.69 (8) | 1.24 (27) | 1.36 (33) | **1.61** [1.08, 2.18] (17) | 1.39 [1.15, 1.62] (85) |
| QB+RB1 + WR1 | top 5% | 1.00 | 1.15 | 1.13 | **0.97** [0.80, 1.15] | 1.10 |
| QB+RB1 + WR1 | top 1% | 1.51 (8) | 1.38 (23) | 1.52 (42) | **2.02** [1.33, 2.79] (17) | 1.55 [1.32, 1.79] (90) |

Pearson r of the stack's points with the opposing WR1's: QB+WR1+WR2 +0.03 (BD), +0.12 (SD), +0.13 (SF), **+0.11 (BF)**;
QB+WR1 +0.05 / +0.10 / +0.13 / **+0.10**; QB+RB1 +0.02 / +0.08 / +0.12 / **+0.15**.

**It does not vanish and does not turn negative for a big favourite.** The opposing WR1's lift is +44% in the stack's
top 1% (interval 0.94-2.01, 15 hits), +10% in the top 5%, +5% in the top 10% for a big-favourite QB+WR1+WR2, the same as in the
other buckets (interval always includes them), and for a QB+RB1 stack a big favourite's is the largest (2.02). The only
estimates below 1 are the opposing **TE1** against a big favourite's stack (0.79, interval 0.51-1.08) and a big dog's top-10% RB stack
(0.94), both inside noise, and **the opposing TE1 adds much less than the opposing WR1** (1.0 for a QB+WR1+WR2 stack in the top 1%, where the
WR1 adds 1.4; 1.15 to 1.3 for the thinner stacks, where the WR1 adds 1.4 to 1.5). The bring-back that does weaken is a **big underdog's**: the stack's correlation with the other side's WR1
falls to +0.03, though its top-1% lift is the highest (1.93 on 7 hits). The counts are the caveat: the top 1% of a
stack's points has 7-42 hits per bucket, so a lift of 1.0 cannot be ruled out in any single bucket; pooled over buckets it
is solid (1.44, 1.24-1.67). For building: if you bring back, bring back the **WR1**; a TE bring-back is a coin flip; and
nothing here says to skip the bring-back against a blowout. The simulator's Gaussian copula under-states this lift
(its pooled opposing QB1-WR1 correlation is +0.08, and the joint tail is 1.4 times a Gaussian's, below).

### Team shape: who the players are, as a modifier on the pair

Terciles of each modifier (cut on the training seasons), three previous games only, unshrunk correlation of the surprises;
`p adj.` is the share of shuffled scans whose largest |t| over all 22 tests is at least this one's. Route participation is
not in the weekly data this repo loads; target share stands in for it.

| Pair, by modifier | pooled | low | mid | high | tercile cuts | t | p adj. | held-out gain per 1,000 (SE) |
|---|---|---|---|---|---|---|---|---|
| QB1 with **TE1**, by the TE's target share | +0.260 | +0.189 | +0.283 | +0.310 | 0.127 / 0.179 | +2.9 | 0.07 | **+4.47 (1.30)** |
| QB1 with WR1, by his target share | +0.333 | +0.281 | +0.327 | +0.390 | 0.221 / 0.271 | +3.3 | 0.03 | +0.27 (1.41) |
| QB1 with WR2, by his target share | +0.284 | +0.205 | +0.317 | +0.330 | 0.151 / 0.191 | +3.6 | 0.00 | +0.84 (1.48) |
| QB1 with **RB1**, by the back's target share | +0.069 | +0.021 | +0.082 | +0.102 | 0.069 / 0.117 | +1.3 | 0.99 | -0.12 (0.68) |
| QB1 with RB2, by the back's target share | +0.063 | +0.006 | +0.089 | +0.094 | 0.051 / 0.094 | +2.5 | 0.19 | +1.70 (0.92) |
| QB1 with WR1, by target concentration | +0.333 | +0.285 | +0.314 | +0.397 | 0.269 / 0.305 | +2.7 | 0.14 | +0.99 (1.37) |
| QB1 with RB1, by the QB's carry share | +0.069 | +0.109 | +0.039 | +0.058 | | -0.8 | 1.00 | -0.81 (0.59) |
| QB1 with WR1, by the team's PROE | +0.333 | +0.328 | +0.342 | +0.332 | | +0.2 | 1.00 | -0.22 (0.43) |
| WR1 with WR2, by target concentration | +0.003 | +0.017 | -0.014 | +0.008 | | -0.1 | 1.00 | -0.24 (0.26) |

- **Tight ends (Sam's addition): yes, and it holds out of sample.** A receiving TE moves with his QB (+0.31 in the top third
  by target share) and a blocking TE hardly does (+0.19 in the bottom third; the pooled +0.26 averages the two). It is the
  one modifier that is both significant in training and strongly confirmed in 2023-2025.
- **Running backs: the direction is right and the size is small.** A back in the top third of his team's target share moves
  with his QB at +0.10, a two-down back at +0.02 (t +1.3, not significant, no held-out gain), against +0.28 to +0.39 for a WR1.
  "QB + RB stacks only score well when the back is a pass catcher" holds as a ranking and not as a size: even a receiving back
  is a third of a receiver as a stack partner. (The opposing-WR1 bring-back is the bigger lever for a QB + RB stack: lift 1.55.)
- **Wide receivers**: the QB's link grows with the receiver's share (t +3.3 and +3.6, adjusted p 0.03 and 0.00) but the
  held-out gain is small (+0.3 and +0.8 per 1,000, 1 SE or less each).
- **A running QB's carry share and the team's PROE do nothing** (t -0.8 and +0.2; both held-out gains negative). Target
  concentration is borderline for QB1-WR1 and is not confirmed.

### Why: the mechanism, in play-by-play

nflverse play-by-play, 2014-2025, 398,362 designed plays (dropbacks and designed runs).

| Offense's score at the snap | Pass rate | Share of snaps |
|---|---|---|
| trail 15+ | 75.1% | 9% |
| trail 9-14 | 70.2% | 12% |
| trail 1-8 | 63.5% | 26% |
| tied | 57.6% | 19% |
| lead 1-8 | 55.7% | 20% |
| lead 9-14 | 51.3% | 8% |
| lead 15+ | 42.8% | 5% |

The in-game score moves the pass rate by 32 points; the **pre-game spread, at the same score, by 2-7 points and the
wrong way** (big favourites pass slightly more than big dogs at the same score). The spread works by changing how
often a team is in each state, and the state is realized, not forecast: per +1 point of pre-game spread the team-game
pass rate moves -0.22 points (t -10); per +1 point of final margin -0.39; per +1 point of average lead on offense
-0.67. By volume, per team-game:

| Per +10 points | pass attempts | RB carries | WR + TE targets | RB targets |
|---|---|---|---|---|
| pre-game spread | -0.03 (t -0.1) | **+1.69 (t +11)** | +0.16 (t +1.1) | -0.08 (t -1.2) |
| final margin | -1.54 (t -21) | +2.41 (t +41) | -1.03 (t -17) | -0.42 (t -15) |

**The spread moves rushing, not passing**: a favourite runs about 1.7 more times per ten points of spread and throws
and targets the same; the dog's receivers are not fed by the spread. The realized margin moves everything, and it is the
common factor behind the pairs. The surprises (z-scores) correlate with the team's own final margin at +0.63 for the DST,
+0.29 for the QB1, +0.25 for the RB1, and **-0.11 / -0.09 / -0.07 for the opposing WR1 / WR2 / TE1**: a team that loses
big does worse than its projection, trailing volume notwithstanding. Removing the margin from both z-scores
(a quadratic) moves RB1-DST from +0.06 to -0.13, QB1-DST from -0.08 to -0.36, QB1-opposing RB1 from +0.03 to +0.11 and
leaves QB1-WR1 at +0.30: **the margin is what makes the DST pairs positive or negative, and a pair's pooled value is
already the average over the margins the games produced.** The spread is an unbiased
forecast of the margin (slope 1.05) but a modest one: the final margin has a standard deviation of 14.8 points and
the spread explains a fifth of its variance (r 0.45), which is why conditioning on it adds so little to the pair.

### Do the projections already price it? Mostly.

Change in (actual - projection), DK points per +10 points of spread; a projection that priced the script fully would
miss by 0. The naive baseline is each player's mean over his previous three games. "z" is where the actual lands in
the projection's own distribution, per +10 points (the engine's tables, which the copula reads).

| Role | Projection moves per +10 | Naive misses (2014-22) | Projection misses (2014-22) | Projection misses (2023-25) | z per +10 (2014-22 / 2023-25) |
|---|---|---|---|---|---|
| QB1 | +2.40 | +0.27 (t 1.4) | +0.68 (t 4.0) | +0.00 (t 0.0) | +0.15 (7.6) / +0.06 (1.5) |
| RB1 | +1.01 | +0.43 (t 2.0) | +0.73 (t 3.7) | +1.34 (t 3.6) | +0.09 (3.9) / +0.16 (3.9) |
| WR1 | +1.78 | +0.23 (t 1.0) | +0.31 (t 1.5) | -0.44 (t -1.3) | +0.04 (1.8) / -0.05 (-1.2) |
| WR2 | +0.83 | +0.15 (t 0.8) | +0.33 (t 1.9) | +0.40 (t 1.3) | +0.04 (1.7) / +0.04 (1.1) |
| TE1 | +0.83 | +0.30 (t 1.6) | +0.44 (t 2.6) | +0.38 (t 1.4) | +0.07 (3.1) / +0.08 (2.0) |
| DST | +2.28 | +1.11 (t 6.8) | +0.04 (t 0.3) | +0.46 (t 1.8) | +0.20 (8.2) / +0.29 (6.8) |

(These are the shipped UM projections, the only ones in the public data; TFFB / Sleeper / FantasyPros history is on Sam's
machine.) The projections do move with the spread, by 1-2.4 points per ten, and in points they are close: the
numbers that hold in **both** eras are a **RB1** under-projection of +0.7 then +1.3 points per ten points of spread (a
big favourite's RB1 outscores the projection by a point or two; the projection already adds a point) and a
**DST that lands higher in its own distribution** the more it is favoured (+0.2 and +0.3 sd per ten; its mean is on target
in points, so favourites' defenses have a fatter right tail than the engine's per-projection table gives them). QB1 (+0.7,
then 0.0) and TE1 (+0.4, then n.s.) did not hold. Since the projection already carries the spread, the gain from
modelling the script is in the correlation, and the correlation does not move with it. The two marginal effects are the
engine's and the Week 8 recalibration's to take up; they are not touched here.

### Tails: the one thing the copula misses

Both z-scores above +1 or +1.5 sd, against what a bivariate normal with the pair's own correlation gives:

| Pair | Both above | Observed | Gaussian, same r | Ratio | 90% interval |
|---|---|---|---|---|---|
| QB1 - WR1 | +1.5 sd | 0.0171 | 0.0120 | 1.43 | [1.18, 1.70] |
| QB1 - WR2 | +1.5 sd | 0.0145 | 0.0110 | 1.32 | [1.10, 1.59] |
| QB1 - TE1 | +1.5 sd | 0.0145 | 0.0103 | 1.41 | [1.14, 1.72] |
| QB1 - opposing QB1 | +1.5 sd | 0.0138 | 0.0088 | 1.57 | [1.18, 1.99] |
| QB1 - opposing WR1 | +1.5 sd | 0.0084 | 0.0060 | 1.39 | [1.04, 1.69] |
| QB1 - RB1 | +1.5 sd | 0.0063 | 0.0057 | 1.10 | [0.81, 1.41] |
| RB1 - own DST | +1.5 sd | 0.0060 | 0.0056 | 1.07 | [0.81, 1.36] |

(At +1 sd: 1.25, 1.20, 1.31, 1.30, 1.13, 1.17, 1.13.) **Stack booms happen together 30-45% more often than a Gaussian
copula with the same correlation says** (the bring-back 39%), and a shootout (QB against QB) 57% more. RB1 with his own DST is about Gaussian,
which is also the answer to the "RB1 gets a bump when his DST scores 15+" idea: there is no excess joint tail there.
This is the finding that matters most for a tournament and the one this study did not act on: the dependence is a
copula-shape question (a t-copula or a shared game factor), the simulator's engine, and a separate decision. A Gaussian
copula under-states exactly the event a GPP is built to hit.

### What survived, in plain English

| When ... | ... moves by about | Evidence |
|---|---|---|
| a tight end's share of his team's targets is in the top third rather than the bottom third | his correlation with his QB goes from +0.19 to +0.31 | t +2.9 in training; +4.5 log-likelihood per 1,000 pairs held out (3.4 SE); 0 of 300 shuffled tables as good |
| a WR1 / WR2's target share is in the top third rather than the bottom third | his correlation with his QB rises from +0.28 / +0.21 to +0.39 / +0.33 | t +3.3 / +3.6 in training; held-out gain under 1 SE |
| a running back's target share is in the top third rather than the bottom third | his correlation with his QB rises from +0.02 to +0.10 | t +1.3; no held-out gain: small, not confirmed |
| the stack's QB is paired with the other side's WR1 (or WR2) rather than an unrelated player | the stack reaches its top 1% about 1.4 times as often; the other side's TE1: 1.0 to 1.3 times | 85 hits, 90% interval 1.2-1.65; the same in every spread bucket that has the hits, big favourites included |
| a team is favoured by 10 more points | its RB carries rise by 1.7 a game; its pass attempts and WR + TE targets do not change | t +11 / -0.1 / +1.1, 2014-2025 |
| a team is favoured by 10 more points | its RB1 outscores a spread-aware projection by 0.7 to 1.3 points, and its DST lands 0.2 to 0.3 sd higher in its distribution | both eras, t 3.6 to 8 |
| a team is leading by 15+ rather than trailing by 15+ | its pass rate falls from 75% to 43% | 398,362 plays; the pre-game spread adds 2-7 points at a given score, the wrong way |
| a team's final margin is 10 points higher | its DST scores +0.6 sd, its QB1 +0.3, its RB1 +0.25, the other side's WR1 -0.1 | realized, not forecast |
| a QB1 booms | his WR1 / WR2 / TE1 booms with him 30-45% more often than the correlation says | 90% intervals exclude 1 |
| (a pair's correlation, by spread, implied total, a QB's carry share or PROE) | nothing that survived | the ruling above |

**For the local session and Sam:**

1. **QB1-TE1 shipped (decided); the other five receiver pairs are to be re-tested.** `dfs sim fit` writes
   `correlations_shape.csv` next to `correlations.csv`. The committed `correlations.csv` came from an earlier run: a refit
   in the cloud session reproduced the same counts but different correlations (up to 0.010 apart), so it was left alone and
   the shape table was fitted from that refit's scores; refit both in one `dfs sim fit` locally if you want them from one
   run. Re-test the five: RB1 (+0.02 to +0.10 by target share, t +1.3, no held-out gain) and WR1/WR2 (significant in
   training, small held out) are the ones the data does not yet support; to add one, extend `shape_table.SHIPPED_TESTS`.
   Nothing from `qb_rush`, `proe` or `conc` (they did nothing).
2. Rerun "Do the projections already price it?" on the scored tables (Model Check) with CalPts / TFFB in place of the UM
   projection: the RB1 and DST rows say where a spread-aware adjustment might pay; it is a calibration question for the
   Week 8 round.
3. The named-pairs spread table: do not carry it (it fails two of the three criteria now).
4. The tail finding wants a decision on a t-copula / shared game factor before it is a task. The bring-back lift above is
   the same fact seen from the lineup: the simulator under-credits a bring-back.

## Duplicates, the Top 1% share and build shapes

Cloud task G, part B. The Suggested GPP builds used to be ranked by the chance of reaching the top-1% line
(`Top 1%`) among stacks with a bring-back. A top-1% score that is one of 50 identical entries pays a fraction, so the
GPP candidates are now drawn from several shapes and can be ranked by the **Top 1% share** (`dfs lineups suggest --rank share`;
the default, `--rank top1`, ranks by the Top 1% chance alone until Sam has seen the share retro-tested on his own weeks). Every
build shows its copies expected whichever way it is ranked. Cash builds are unchanged.

**Product ownership, not the sum.** Every build reports both the ownership **sum** (the nine players' shares added up) and
the **product** (multiplied): the product is the stronger predictor of duplicates (ETR: R-squared 0.43).
Pure functions in `sim/dups.py`, shares from `FieldModel.shares("top" | "cash")`, which are `sim/field.py`'s field
weights (projected Own% through the optional per-contest-type curve, summing to 9).

**Expected duplicates.** If a field lineup chose its players independently, the chance it equals ours is the
product of our nine shares and a field of N holds `N * product` copies. That is the *independence approximation* and
it is biased low, for three reasons: chalk is correlated (the field herds into the same stacks and value plays); the
salary cap removes most of the independent draws; and (the other way, by a factor of 5-10 over a lineup) a fixed number
of slots makes inclusion negatively dependent. Sam's Week 4 double-up lineup was one of about 500 identical
entries in 4,597: that needs a product of 0.109, and a lineup of nine 50%-owned players gives 0.002, an estimate of 9
copies, 50 times short. **The stated correction is a power on the product**, `E[dups] = N * product ** 0.5`
(`DUP_EXPONENT`): about half as many independent choices as players. It lands a lineup of 55-60% average ownership in a
4,597-entry field at 310-460 copies (independence: 20-50), a typical GPP build (summed ownership 120%) at a tenth of
a copy in 1,000 entries, and a chalky one (30% a player) at several. **It is set from that one anchor and the
structure, not fitted**, because the standings files are Sam's: `dups.fit_dup_model(products, entries, copies)` fits
`(scale, exponent)` to them. A player with no ownership number counts at 0.5%.

**The objective.** `top1_share = P(score >= top-1% line) / (1 + E[dups])`, the mean over draws of the top-1% indicator
discounted by the copies expected. `Top 1%` stays a column. **`--rank share`** ranks the GPP candidates by this objective
(then `Top 1%`, projection, salary, names); **`--rank top1`, the default**, ranks them by `Top 1%` alone (then projection,
salary, names) and only reports the copies. The share becomes the default only after Sam has seen it on a local retro-test. **Contest size is an input**: `--gpp-entries`, default **1,000** (Sam's typical SE GPP is
800-1,500; this is the one number to confirm). With no ownership published the objective is the plain `Top 1%` and a note
says so.

**What a stack is (Sam, 2026-10-18).** The QB plus **one to three of his teammates at any skill position** (RB, WR or TE, in any
mix: QB+RB, QB+RB+WR, QB+WR+TE all count), **with or without a bring-back**. The bring-back is no longer forced:
`is_stacked` has no opponent clause, the naked stack and the stack with a bring-back are both generated, and the simulator
ranks them. (The evidence above says a WR1 bring-back is worth having, lift 1.4, and a TE1 bring-back is not, 1.0-1.3.)

**Shapes** (`lineup_shapes.py`; soft shapes, rules on an exact search, never hard-coded picks). A lineup can match several;
it is kept once and tagged with all of them.

| Shape | Rule | Lineups kept per QB | Part A evidence |
|---|---|---|---|
| `stack` | a stack with a bring-back (a RB / WR / TE of the other team) | 40 | QB1-WR1/WR2/TE1 +0.25 to +0.31 held out; the opposing WR1 lifts the stack's top 1% by 1.4 |
| `naked` | a stack with none | 20 | the baseline the bring-back is read against |
| `rb_stack` | a stack that holds a RB of the QB's team (QB+RB, QB+RB+WR, ...) | 20 | QB1-RB1 +0.07, higher for a receiving back (+0.10 against +0.02, not confirmed); lift of an opposing WR1 on QB+RB1: 1.55 |
| `game` | a stack and at least two RB / WR / TE of the other team | 10 | the bring-back pairs; QB1-QB1 +0.19; tail ratio 1.3-1.6 |
| `rb_dst` | a stack, and a RB with his own team's DST | 10 (per QB, DST and RB) | RB1-DST +0.06 (+0.07 held out, t 1.3); weak, not larger for favourites |
| `contrarian` | a stack whose summed ownership is in the bottom third of the stacks found so far | 10 | about the field, not the correlation |

A build's title names the shape, the make-up and the copies expected: "GPP build 5  ·  T01 stack QB+RB+TE + T00 bring-back  ·
~0.1 dupes", "GPP build 8  ·  T03 naked stack QB+RB+WR  ·  ~0.1 dupes", "GPP build 12  ·  T05 contrarian naked stack QB+WR+TE + RB/DST".
`GPP_SHAPES` takes any subset. Selection is `choose_per_qb`, unchanged (per-QB coverage, diversity, exposure cap), on the new key.

**Cash builds** keep `Cash %` against the relative line. Each reports its expected copies among `--double-up-entries`
(default 3,000; his double-ups ran 2,758-4,597) under the cash weights, and a build expected to be shared by 25 or more
entries gets a **warning, never a filter** (the Week 4 lesson: a duplicate cashes or busts with you).

**Cost.** On the worked slate below (64 players, 8 teams, 20,000 draws, nothing else running), `suggest_builds` takes
**19.9 s on main's code** (stack = QB + two pass catchers + a bring-back; search 15.0 s, simulation 4.7 s), **7.0 s on
this branch with only the stack-with-a-bring-back shape** (the looser stack is much cheaper to search) and **21.8 s with all
six shapes** (search 16.7 s, simulation 4.7 s): **+1.9 s, 10%, over main, complete, inside the 60 s budget** (`TIME_LIMIT_SECONDS`).
The searches run in the order above, each in its weight's share of the time left (the stack with a bring-back first, the
biggest share; `GPP_PLAN`), and the other shapes are best-effort inside their boxes: a note says so when one is cut, and
`--seconds` lengthens them. Two shapes whose rule the search's bound cannot see were slow when searched per QB (a RB with his
DST 35-51 s, a stack under an ownership cut 160-177 s, on the older definition): they fix more than the QB instead
(`rb_dst`: his best three DSTs and a RB of the DST's team; `contrarian`: his three best stack cores under the cut), exact inside
each anchor, with which anchors are tried the heuristic. **A real slate was not measured here**: main's own note is that a real
63-player pool needed about 45 s for its stack search; the looser stack should help, but run `dfs lineups suggest --no-write` and read
the phase times it prints.

### Worked slate

A synthetic 8-team slate (`tests/test_lineup_suggest.py::slate`, seed 4), a 64-player pool (each team's QB1, RB1-2, WR1-3, TE1,
DST, all marked Both), ownership from value (position totals QB 100%, RB 220%, WR 340%, TE 110%, DST 100%), 1,000 entries, 20,000
draws, cap 50,000. All 772 candidates are simulated in ONE call, so the old and new views below share draws. **Old** = the
candidates that fit the previous definition of a stack (QB, two WR/TE and a bring-back: 96 of the 772), ranked by Top 1% alone;
**new** = every shape, ranked by the Top 1% share (`--rank share`; the table after the next paragraph is the default
`--rank top1` on the same candidates).

| # | Old ranking | Top 1% | own sum / product | E[dups] | share |
|---|---|---|---|---|---|
| 1 | T02 stack QB+3WR + T03 bring-back | 0.56% | 153% / 4.0e-08 | 0.20 | 0.462% |
| 2 | T02 stack QB+2WR + T03 bring-back + RB/DST | 0.46% | 146% / 4.6e-08 | 0.21 | 0.383% |
| 3 | T02 game stack QB+2WR + T03 bring-back | 0.46% | 162% / 1.3e-07 | 0.37 | 0.337% |
| 4 | T02 stack QB+2WR + T03 bring-back | 0.45% | 141% / 3.8e-08 | 0.20 | 0.377% |
| 5 | T02 game stack QB+2WR + T03 bring-back | 0.45% | 153% / 4.9e-08 | 0.22 | 0.369% |
| 6 | T02 game stack QB+2WR + T03 bring-back | 0.45% | 168% / 1.3e-07 | 0.36 | 0.330% |

Ranked by the share, the same old-definition candidates move the 162% and 168% game stacks (0.36-0.37 copies) out of the top
five in favour of a 123%-owned T00 game stack (0.08 copies, Top 1% 0.40%).

| # | New ranking (all shapes) | Top 1% | own sum / product | E[dups] | share |
|---|---|---|---|---|---|
| 1 | T02 stack QB+3WR + T03 bring-back | 0.56% | 153% / 4.0e-08 | 0.20 | 0.462% |
| 2 | T02 stack QB+2WR + T03 bring-back + RB/DST | 0.46% | 146% / 4.6e-08 | 0.21 | 0.383% |
| 3 | T02 stack QB+2WR + T03 bring-back | 0.45% | 141% / 3.8e-08 | 0.20 | 0.377% |
| 4 | T00 game stack QB+2WR + T01 bring-back | 0.40% | 123% / 7.0e-09 | 0.08 | 0.374% |
| 5 | **T02 naked stack QB+RB+2WR** | 0.42% | 133% / 1.7e-08 | 0.13 | 0.372% |
| 6 | T02 game stack QB+2WR + T03 bring-back | 0.45% | 153% / 4.9e-08 | 0.22 | 0.369% |
| 7 | **T00 contrarian naked stack QB+WR** | 0.41% | 136% / 1.7e-08 | 0.13 | 0.363% |
| 8 | **T05 contrarian naked stack QB+WR+TE + RB/DST** | 0.41% | 137% / 1.9e-08 | 0.14 | 0.361% |

The chosen builds (`choose_per_qb`, one or two per QB): **12 old, 13 new**. Mean Top 1% 0.310% old, 0.317% new; mean E[dups]
0.150 old, 0.117 new; mean share 0.264% old, 0.281% new. The 13 new builds are 11 with a bring-back and 2 naked; by
make-up QB+2WR 4, QB+RB+TE 2, QB+RB 2, QB+WR+TE 2, QB+3WR, QB+TE and QB+RB+WR once each; tagged contrarian 7, RB stack 5,
RB/DST 3, game stack 3. **Read it for what it is.** On a synthetic slate whose ownership is mild the copies expected are
0.04 to 0.4, so the discount moves a candidate's rank by up to 30%, about the size of the simulator's own noise (a Top 1% of 0.5%
on 20,000 draws is 100 hits); the objective earns its keep on a chalky slate, where a few candidates are several copies and a
quarter of their value, and it costs nothing (the mean share is level) on a flat one. The shapes are what changes the builds: the
old set is one kind of stack; the new one gives Sam naked stacks, QB+RB and QB+TE stacks, low-owned stacks and RB + DST
builds, and the simulator ranks them against each other.

**The default, `--rank top1`, on the same 772 candidates**: it picks 12 builds (mean Top 1% 0.344%, mean copies 0.172, mean share
0.291%) against 12 for the old definition (0.310%, 0.150, 0.264%) and 13 for `--rank share` (0.317%, 0.117, 0.281%); 8 of the
builds are the same either way. The top of the ranking is the first table above (it is the old ranking, now over every
shape): the stack with a bring-back at 0.56%, the game stacks at 0.45-0.46%, a QB+TE game stack at 0.45%. As expected, ranking by
the chance alone buys a higher Top 1% and more copies, and ranking by the share buys fewer copies; on this mild-ownership slate
the mean shares are within the simulator's noise (0.291% vs 0.281%), so the slate cannot say which is better. That is what
the retro-test on Sam's weeks is for.

### Tests

`tests/sim/test_dups.py` (the maths, the objective on equal-Top-1% examples, the Week 4 order of magnitude with a stated
stand-in lineup, the fit), `tests/test_lineup_shapes.py` (the rules on hand-built lineups including the new stack, each
generator legal and matching its shape, the best lineup found exactly against an enumeration of the top 3,000, determinism,
the prune), `tests/test_lineup_suggest.py` (objective ordering, sum / product / copies on every build, contest size, cash
builds unchanged by ownership, the double-up warning, determinism under a seed, the time budget, the new `is_stacked`),
`tests/sim/test_shape_table.py` (the live `Tgt%` feature -- a ratio of window sums, this season only, never the game itself
-- the fit, the layer moving only QB1-TE1 and only with the tight end's share, the simulator reading it by default, the
committed file), the `--rank` cases in `tests/test_lineup_suggest.py` and `tests/test_lineups_suggest_cli.py`,
`tests/sim/test_script.py` and `tests/sim/test_script_shape.py` (buckets, the clustered slope, the scans finding a planted
dependence and not a null one, the features using only three previous games, the lookups, the out-of-sample scores, the
simulator hook, the bring-back lift on a planted link).

### What the local session must do after merge

1. `dfs lineups suggest --no-write` on the live week: confirm the run is inside the budget on a real pool (read the phase
   times; `--seconds` lengthens the shape searches) and read the titles ("~0.3 dupes") and the notes. The contest sizes are
   settled (Sam, 2026-10-18): `--gpp-entries` 1,000 and `--double-up-entries` 3,000. Run it once with the default
   `--rank top1` and once with `--rank share`; the cloud session could only time the synthetic pool.
2. **Calibrate the duplicate model on Sam's standings files** (13 GPP, 7 double-up): for each lineup in a file, its
   players' `%Drafted` product and the number of identical lineups in that file, then
   `dfs.sim.dups.fit_dup_model(products, entries, copies)` to replace `DUP_EXPONENT`; check the Week 4 lineup's actual
   product against the 500 copies. Until then 0.5 is a stated placeholder.
   **Retro-test `--rank share` against `--rank top1` on Sam's weeks** with the calibrated model: the share becomes the default
   only after Sam has seen that result (`lineup_suggest.DEFAULT_RANK`, one constant; the CLI help and docs say "default top1").
3. The sheet does not change in this PR (the Suggested tab's columns are untouched): the shape and the copies are in
   the build's title, and the sum, product and copies in the terminal. A column for the product would be a structural
   edit (template first, `dfs doctor`, a changelog row). The Suggested tab's how-to line and the Instructions tab
   (live and template) could mention that the titles now carry the shape and the copies, and that a stack no longer
   needs a bring-back.
4. The projection-calibration check on the scored tables ("Do the projections already price it?" with CalPts / TFFB in place
   of the UM projection) and the t-copula decision. Both, with items 1 and 2, run locally after merge and do not hold up the PR.
5. Re-test the five QB1-with-receiver pairs that stayed pooled (see "Decision (Sam, 2026-10-18)" above), and, if the live
   `Tgt%` is the weaker feature, whether the research feature (mean of per-game shares, across seasons) is worth computing live.

## Local results after PR #10 (2026-10-18)

Run on Sam's machine from the files in `data/raw` (never in git). The pool, the contests and the weeks are small; read every
number as directional.

### Do the projections price it? CalPts and TFFB (2026 Weeks 1-5, n = 122 team-weeks per role)

Change in (actual - projection), DK points per +10 points of the team's spread in its favour (Spread is negative for the
favourite; pre-lock `nflverse_games` line; roles are the team's top player at the position by `AggPts`), game-clustered t:

| Role | TFFB | CalPts | UM |
|---|---|---|---|
| QB1 | -1.60 (t -1.4) | -1.19 (-1.0) | -0.14 (-0.1) |
| RB1 | +1.56 (+1.2) | +1.17 (+0.9) | +2.18 (+1.6) |
| WR1 | +0.31 (+0.2) | -0.41 (-0.3) | -0.01 (0.0) |
| WR2 | -1.76 (-2.0) | -1.79 (-2.0) | -0.98 (-1.0) |
| TE1 | +0.87 (+0.7) | +1.06 (+0.9) | +1.14 (+0.9) |
| DST | -0.68 (-0.9) | -0.15 (-0.2) | +0.14 (+0.2) |

RB1 has the same sign and size as the 2014-25 result (+0.7 then +1.3 on UM) but is not significant on five weeks. The DST's
mean is on target in points (G's finding was in its own distribution, which this table does not measure).

**The RB1 spread shadow** (`calibration.RB1_SPREAD_PER_POINT` = 0.1 point per point of favouritism, each team's RB1 only), leave
one week out: MAE 6.739 against CalPts 6.733 (paired difference +0.007, SE 0.049, n = 122); per-week slopes 0.4 to 1.8 per 10.
Out of sample on Weeks 2-5 (98 RB1s) the Model Check row reads MAE 6.22 against CalPts 6.24, rho .325 against .328. A wash, so
it stays a Model Check row; it joins CalPts only after 3 scored weeks and only if it beats CalPts.

### Duplicate model, fitted to Sam's standings

58,429 lineups in 20 files (13 GPP, 7 double-up; Weeks 2, 4, 5). `fit_dup_model` on each lineup's ownership product and the
count of identical lineups:

| | on the contest's own %Drafted | on the tool's projected shares (what the tool applies it to) |
|---|---|---|
| GPP (33,576 lineups) | scale 3.30, exponent 0.657 | **scale 10.02, exponent 0.712** |
| Double-up (24,853 lineups) | scale 17.6, exponent 0.884 | scale 141, exponent 0.599 |

The placeholder (scale 1, exponent 0.5) predicts 27,495 copies in the GPPs against 11,536 observed (2.4x), and 70,542 against
2,747,334 in the double-ups (39x too few). On the projected shares the GPP fit tracks the top decile of products (observed 2.65
copies, fitted 2.66; the placeholder says 4.7) and over-predicts the low deciles less than the placeholder did (observed 0.04,
fitted 0.0002 to 0.04, placeholder 0.006 to 0.18; the floor is the same user entering one lineup twice in a multi-entry
contest). **`dups.DUP_MODEL_GPP` is the fitted GPP model.** The double-up fit on projected shares is poor (a few heavily
duplicated lineups carry most copies, and double-ups are chalkier than projected: Week 4's 724-copy lineup fits 198, the
placeholder says 5, low products over-predict 10 to 40 times), so `DUP_MODEL_CASH` stays the placeholder until the per-player
double-up ownership model (Contests Phase 2). Cash builds only warn on the number.

### Retro-test of `--rank` on Sam's contests (Weeks 4 and 5, plus the Week 2 Hundo)

Week 6 has no standings yet. Sam's real past pools are not stored, so the pool is constructed (his Week 6 counts, QB 9, RB 13,
WR 17, TE 10, DST 8): the main-slate players with the highest CalPts, or by `Val` among the top 2N. Builds come from the last
pre-lock EdgeRaw snapshot, every shape searched to completion, each build scored against every GPP of its week with the
contest's own `FPTS` table (`sim/retro.py`). Mean finish percentile (lower is better) / share of builds in the top 10% / in
the top 1%:

| Arm | CalPts pool | Value pool |
|---|---|---|
| top stacks by CalPts only | 0.256 / 35% / 5.7% | 0.314 / 30% / 4.7% |
| `--rank top1` | 0.238 / 50% / 6.0% | 0.262 / 52% / 13.0% |
| `--rank share`, 1,000 entries, fitted model | 0.239 / 43% / 6.6% | 0.287 / 42% / 13.9% |
| `--rank share`, 12,000 entries | 0.313 / 26% / 0% | 0.352 / 34% / 17.9% |

The smart builds beat points-max by 10 to 19 points of top-10% rate but the standard error by build is 0.11 to 0.12 (about 30
builds an arm, 5 to 7 correlated contests a week). `share` against `top1` at 1,000 entries is a wash (-0.001 +/- 0.063 and
+0.032 +/- 0.066 in mean percentile): the chosen builds' fitted E[dups] is 0.01 to 0.08 and the real fields held about no copies of
them, so the discount rarely changes a pick; at 12,000 entries it costs about nine real points a build. `top1` stays the default.

### Timing on real slates

A full search (every shape to completion) takes 53 to 152 s on the replayed pre-lock pools (Week 4 113 s and 53 s, Week 5 109 s
and 152 s, Week 2 94 s and 64 s for the CalPts and value pools). Per shape on Week 5: stack 19 s, naked 7, rb_stack 40, game 30,
rb_dst 3, contrarian 2 (102 s). At the old 60 s default the rb_stack and game searches hit their time boxes; the default is now 120 s (Sam, 2026-10-18).

### `dfs sim fit`, refitted locally

`correlations.csv` differs from the committed table by at most 0.0104 (opposing QB1-DST, -0.342 to -0.352) and 0.0016 on average;
QB1-WR1, WR2 and TE1 move by about +0.006. `correlations_shape.csv` is identical.

## Week 8 round: the five receiver pairs re-tested, and the copula against two alternatives (2026-10-20)

Offline, from the cached 2014-2026 history (2026 through Week 6). Code: `sim/shape_retest.py` and `sim/copula_test.py`
(`python -m dfs.sim.shape_retest --frame <parquet>`, `python -m dfs.sim.copula_test --frame <parquet>`, the frame being
`pipeline.load_frame(2026)` saved as parquet). **Nothing in `models/sim/` was refitted.** Rerun both after Week 7 is added.

### QB1 with RB1, RB2, WR1, WR2, WR3 by target share: none clears the bar

The bar QB1-TE1 cleared: fit 2014-2023 (cut points, pooled value, tercile table), judge on 2024-2026 held out, gain over the pooled
value per 1,000 pairs, more than 2 game-clustered SE. The feature is the live one (`shape_table.live_tshare`). The last column is
the share of 200 shuffled-feature tables (`tshare` permuted within role and season, refitted) that gain at least as much.

| Pair | pooled r | low / mid / high | train t | held-out pairs | gain per 1,000 (SE) | z | shuffled as good | ships |
|---|---|---|---|---|---|---|---|---|
| QB1-RB1 | +0.067 | +0.041 / +0.065 / +0.091 | +1.6 | 1,128 | -0.26 (0.49) | -0.5 | 0.72 | no |
| QB1-RB2 | +0.060 | -0.021 / +0.110 / +0.093 | +3.8 | 772 | +1.04 (1.71) | +0.6 | 0.055 | no |
| QB1-WR1 | +0.328 | +0.300 / +0.297 / +0.383 | +3.2 | 1,129 | +1.30 (1.36) | +1.0 | 0.010 | no |
| QB1-WR2 | +0.278 | +0.196 / +0.303 / +0.336 | +3.9 | 1,075 | +1.41 (1.99) | +0.7 | 0.040 | no |
| QB1-WR3 | +0.222 | +0.190 / +0.253 / +0.227 | +0.8 | 794 | -0.15 (0.84) | -0.2 | 0.525 | no |
| *QB1-TE1 (control)* | +0.267 | +0.186 / +0.301 / +0.315 | +3.4 | 1,007 | +3.04 (1.81) | +1.7 | 0.005 | *no, on this split* |

WR1 and WR2 point the same way as the training slope and WR1 beats its shuffles (p 0.01), but their held-out gains are 1.0 and 0.7 SE:
a real, small effect the data cannot yet price. RB1 and WR3 are nothing. **The control slipped:** on the original split (fit 2014-2022,
judge 2023-2025) the module reproduces the shipped QB1-TE1 number (+3.23 per 1,000, SE 1.65, 2.0 SE; the write-up has +3.25, SE 1.59),
but on fit 2014-2023 / judge 2024-2026 it is +3.04 (SE 1.81, 1.7 SE), by season +0.19 (2024), +6.14 (2025), +1.67 (2026, 137 pairs).
Beats-its-shuffles (0.005) still holds, so the shipped pair stays; it is worth knowing it is not as clean as 3.4 SE said.

### A t-copula or a shared game factor against the Gaussian: no clear winner

Per game, the vector of normal scores of every (team, role) on the field (up to 22, or the passing core QB1, WR1-3, TE1 of each
team), the correlation matrix assembled from the pooled pair table exactly as the simulator does and repaired the same way. Fit on
2,591 games of 2014-2023, scored on 637 held-out games (2024-2026). Three models share the matrix: the Gaussian copula (shipped); a
t-copula (one chi-square mixing draw per game scales everyone in it), with the shipped matrix or the Kendall-tau one (`sin(pi tau / 2)`);
and a game factor (`R(m) = (1 - m) I + m R`, a random per-game scale `m = m0 exp(tau s - tau^2 / 2)`). A game's repaired `R` is
nearly singular, so `R(m)` is a correlation matrix only for `m` up to about 1: **the factor can dampen the shipped correlations in
some games but cannot exceed them**, which is why its base `m0` is fitted (it chose 1). `nu`, `tau` and `m0` are chosen on the training games.

Held-out copula log-likelihood per game, minus the Gaussian's (SE across games):

| Model | all 22 roles | passing core |
|---|---|---|
| t-copula, shipped R, nu chosen on training (50) | +0.0107 (0.0071), 1.5 SE | +0.0091 (0.0042), 2.2 SE |
| t-copula, Kendall R, nu = 50 | +0.0086 (0.0078), 1.1 SE | +0.0110 (0.0046), 2.4 SE |
| game factor, tau = 0.2, m0 = 1 | +0.0066 (0.0042), 1.6 SE | +0.0061 (0.0022), 2.7 SE |
| Gaussian with the Kendall R | -0.0025 (0.0035) | +0.0016 (0.0022) |

**Heavy tails lose outright:** on the 22-role vector a t-copula with nu = 10 is -0.095 per game (SE 0.023), nu = 15 -0.034 (0.017),
nu = 25 -0.001, and the training seasons pick nu = 50, which is the Gaussian to within noise. A base `m0` below 1 loses 0.007 to 0.085. The
gains that exist are 0.006 to 0.011 nats per game on a 20-dimensional vector, 1.1 to 2.7 SE, and are what one would expect from
slight over-shrinkage of some pairs and some games having lower correlation; none is a case for changing the engine. The same table
with the normal scores clipped at 0.0005 instead of 0.005 (the clip hides the very tail being tested) gives the same picture.

**The joint-boom excess is not what a heavier copula fixes.** Players above the 90th percentile of their own distribution together
(QB1 and his WR1; plus the other team's WR1), observed against what each fitted model says for the same games:

| Event | sample | observed | Gaussian expects | t(50), Kendall R | game factor |
|---|---|---|---|---|---|
| QB1 + WR1 | all seasons (6,268 team-games) | 193 | 140.5 (ratio 1.37, SE 0.10) | 147.4 (1.31) | 141.6 (1.36) |
| QB1 + WR1 + opp WR1 | all seasons | 31 | 17.7 (1.76, SE 0.36) | 19.7 (1.57) | 18.2 (1.70) |
| QB1 + WR1 | held out (1,231) | 33 | 27.8 (1.19, SE 0.20) | 29.4 (1.12) | 27.6 (1.20) |
| QB1 + WR1 + opp WR1 | held out | 7 | 3.5 (2.0, SE 0.76) | 3.9 (1.80) | 3.5 (2.0) |

Two things sit in that ratio. **First, the margins** (corrected below, "The top of the marginal tables"): scored through the shipped tables the
normal scores exceed their own 90th percentile 10.65% of the time for QB1 and 10.82% for WR1, which would be 1.15 times the joint rate by itself; that
turned out to be table population and role mix, not a light top, and it explains much less than it seemed. **Second, the rest** (about 1.2 on the pair,
1.4 on the three-player event, 1.4 to 2.2 SE) no copula tried closes: the t-copula at the best nu takes off about a quarter of it. So the held-out
evidence for switching is nothing, and **the Top 1% effect for stacked versus unstacked builds was not recomputed** here (see the next section).

## The top of the marginal tables, tested (2026-10-20)

Offline; code `dfs/tail_check.py`, `sim/tail_backtest.py` (`python -m dfs.tail_check --frame <parquet>`, `python -m dfs.sim.tail_backtest --frame <parquet>`),
tables and every fit from 2014-2022 only, judged on 2023-2026 (2026 through Week 6). The starting point was QB1 and WR1 beating their own 90th percentile
10.7-10.8% of the time in the copula study. **It does not survive a held-out check, and no tail adjustment ships.**

**Where the 10.7% came from.** (1) The shipped QB table is fitted on every quarterback who played, backups included (6,886 rows against 6,281 QB1s);
QB1s scored against it beat the 90th percentile 10.75% of the time in 2014-2022, but with a QB table fitted that way on 2014-2022 and scored on
2023-2026 the share is **10.23% (z +0.3)**. (2) WR is one pooled table for WR1 to WR4, and the roles are not alike (below). (3) Elsewhere, in the held-out
seasons the tables are slightly **heavy** at the top, not light.

Share of actuals above the predicted 90th / 95th / 99th percentile, 2023-2026 (z against the nominal rate for 90th):

| | n | >90th | z | >95th | >99th |
|---|---|---|---|---|---|
| QB (table on all QBs) | 1,770 | 10.23% | +0.3 | 5.14% | 0.90% |
| RB | 3,327 | 9.02% | -1.9 | 4.06% | 0.96% |
| WR | 5,334 | 8.83% | -2.9 | 4.67% | 1.09% |
| TE | 2,174 | 10.81% | +1.3 | 5.15% | 0.78% |
| DST | 1,817 | 8.31% | -2.4 | 3.41% | 0.83% |

The top-decile projection band of every position is at or under nominal (QB 6.1%, RB 9.2%, WR 7.8%, TE 6.4%, DST 8.5% above the 90th percentile);
the one in-sample misfit is the LOW end of QB (12.8% above the 90th in the bottom projection band, 3.3 SE), not the top. 2026 alone (shipped tables,
183 to 546 rows a position) reads QB 9.8%, RB 10.1%, WR 9.7%, TE 11.5%, DST 5.9%: nothing there.

**What is real is a role effect**, which a table per position cannot see (share above the 90th percentile, 2014-2022 / 2023-2026, role-frame tables):
RB1 **12.3% / 12.4%** (z +5.3 / +3.3), TE1 10.9 / 11.5, WR1 10.8 / 9.7, QB1 9.95 / 9.2, DST 10.0 / 8.3 against RB2 8.9 / **5.6**, RB3 3.9 / 3.0, WR4 6.0 / 6.4,
TE2 7.5 / 8.7. The starters sit a little above their projection's table and the depth players well below it (mean actual minus projection: RB1 +0.4, RB3 -1.6,
WR4 -1.4 in 2023-2026).

**The adjustment tested** (fitted 2014-2022 by pinball loss at the 90/95/99 quantiles, judged 2023-2026 by pinball loss and by the conditional log-likelihood
of the rows beyond the old 90th percentile; the paired SE is clustered by game): the ratio quantiles above the median stretched about it by `1 + delta`,
one `delta` per group.
- **Per position:** the fitted `delta` is -0.001 to -0.009 for every position (the position tables are already at their 90/95/99 in sample, by
  construction); the held-out gain is 0.0005 points of pinball (SE 0.0001) and no position clears both measures. Nothing to ship.
- **Per role:** the only roles that clear 2 SE on both measures are **RB2** (`delta` -0.053; pinball +0.057, SE 0.008; tail log-likelihood +0.067, SE 0.023)
  and **WR4** (-0.213; +0.126, SE 0.046; +0.178, SE 0.062), both lowering the top. RB3 (-0.344, pinball +0.37 SE 0.09) and TE2 (-0.111, log-likelihood
  +0.14 SE 0.05) point the same way on one measure. RB1 (+0.073) is the other direction and is worse on the tail log-likelihood (-0.078, SE 0.018). A level
  scale per role added on top fits the 2014-2022 data into odd corners (RB1 `delta` 0.55 with scale 0.79) and does no better held out.

**Top 1% for stacked and unstacked builds** (`sim/backtest.py`'s random legal lineups, half stacked, 100 a week, 60 weeks of 2023-2026, 6,000 lineups; the simulator
takes a new `tail_delta` role map, default off). P(lineup >= 190 points): realized 2.65%, as shipped 2.51%, with every role's `delta` 2.84%, with the depth
roles' (RB2, RB3, WR4, TE2) only 2.49%. The shipped simulator was already calibrated at these lines (150: 20.4% against 19.8%; 170: 8.1% against 8.0%).
Paired Brier gain over shipped (x1e-4, positive = better): every role -0.45 (SE 0.60) at 190 and -0.23 (SE 0.09) at 210; depth roles only -0.03 (SE 0.04). The
stacked/unstacked ratio of P(>= 190) is 0.71 realized, 0.69 as shipped, 0.70 and 0.69 adjusted: **an adjustment does not move how stacks compare with
non-stacks.** The depth roles sit in a lineup's FLEX and WR3, and lowering their tops changes a lineup's tail by about 1% of itself.

**How much of the remaining stack-boom ratio it closes: about a tenth.** Observed over Gaussian-expected top-decile joint rates with margins from tables fitted
2014-2022 and the pair table fitted 2014-2022: QB1 + WR1 **1.29** (183 against 142; 1.24 on 2023-2026), + the other team's WR1 1.48 (27 against 18). With the
role-adjusted margins 1.26 and 1.42; with each role's margin forced exactly uniform (an in-sample ceiling for ANY marginal correction) 1.26 and 1.43 (held out
1.15, on 46 events, SE about 0.18). The excess is a dependence effect between QB and receiver, not one in either's marginal.

**Standing.** No change to `models/um/` or the simulator's default. Two things to know: the shipped QB table includes backups and QB1s are fitted against it
(held-out calibration is fine, 10.2%), and RB2, RB3, WR4 and TE2 are over-credited with upside by the pooled position tables, which matters for the value
column more than for lineup probabilities. A role dimension in the tables (or an RB2/WR4 stretch of -0.05/-0.21 as a shadow) is the next step if wanted;
rerun after Week 7.


## Contest sim (shadow) (2026-10-20)

**What it is.** A contest-level layer on the simulator: for each of his lineups and each contest he could enter, the expected
dollars (E[$]), ROI, P(cash), P(top 1%) and expected copies, against a *generated* field scored on the same draws. It answers
"which contest is this lineup best in" and "does a field-aware number rank lineups better than today's Cash % / Top 1%".
**It is shadow only:** no sheet column, no CLI command, nothing in `lineup_suggest.py` or EdgeRaw reads it. Its only consumer is
`dfs research refresh` (study `contest_retro`), which reruns the retro each week. Nothing ships until a ranking clears the bar below.

| Module | What it does |
|---|---|
| `payouts.py` | Payout tables from the contest log. Flat formats (Double Up, 50/50, Multiplier) pay prize pool / places paid, exact on all 94 of his paid entries. GPPs: a shifted power law `c * fee + A * (r^-b - M^-b)` over the paid places, `PAYOUT_EXPONENT = 1.011` (1.002-1.028 held out by week), the min cash per series from his own entries in the bottom half of paid places (then the fee's median, then the format's). Held out by week on 267 paid GPP entries: $2,226 predicted against $2,149 paid (+3.6%), $1.03 mean absolute error per entry, median error 7.3% (a floored form: +7.8%, $1.42). |
| `field_gen.py` | The field: a **chalk herd** on the top `HERD_K = 20` lineups by ownership product (exact, MILP with no-good cuts), with a share and a geometric decay, plus a sampler at the real fields' stack / bring-back / salary rates, raked so the whole field matches the ownership forecast (TFFB Own%; for cash fields through the double-up curve, `dups.double_up_ownership`). `OWN_FLOOR = 0.0001`: a 0.2% floor put about a full slot on the ~300 near-unowned players and dragged the field mean down. |
| `contest_sim.py` | One set of draws for every player; each candidate placed in each simulated field; prizes split with its copies (the dup model's, or the herd's own count when the lineup is a herd lineup). The herd's lead lineup is drawn per draw; E[$] is also reported without the top 0.1% of prizes, and with herd copies only. Opt% / SimLev: each player's share of the simulated top 1% of the GPP field, minus his ownership. About 110 lineups x 10 contests x 4,000 draws in 8 s, plus about 45 s to build the two fields. |
| `contest_retro.py` | The retro: his real contests (standings, payouts, pre-lock ownership) for each closed week, every number held out by week, and `refresh_summary(season, through)` for `dfs research refresh`. |

**The finding behind the herd.** In every double-up, one lineup holds 6-21% of the field. It is the legal lineup with the largest
ownership product from pre-lock TFFB Own%, matching 7-9 of its 9 players. The dup model alone overstates the copies of the other
top-product lineups 5-100x, so the herd carries that mass explicitly. The lead's rank by week was 4, 1 and 0 (Weeks 4-6); the
retro draws it per draw with a decay fitted on the other weeks.

### STOP 1: the generated field against the real ones

Fields of 700+ entries, Weeks 4-6. Mean absolute miss in points, the generated field against the fixed offsets the Lineups tab
uses today:

| | Field mean | Cash line | Top-1% line |
|---|---|---|---|
| Double-ups | 6.5 vs 9.3 | 9.0 vs 12.7 | 6.8 vs 17.8 |
| GPPs | 3.5 vs 3.3 | 3.9 vs 3.2 | 3.6 vs 7.0 |

- The top-1% line is where it helps: Week 6 misses by -2 to -5 points against +12 to +16 for the fixed offsets.
- With the real (post-lock) ownership plugged in, the misses fall to 1-3 points, so **what is left is the ownership forecast**,
  not the generator. GppOwn / CashOwn (Weeks 5-6 only) did no better than TFFB with the double-up curve.
- Week 4's double-ups miss by about 19: that week's megadup was product rank 5, and it busted.

### STOP 2: the retro (Weeks 2, 4, 5, 6)

His 34 lineups plus 360 field lineups, 29 contests, 3,168 lineup-contest pairs, everything held out by week. The measure is the
within-contest Spearman of each ranking with the realized dollars, averaged over contests.

| Ranking | GPP | Cash |
|---|---|---|
| **Today** (Top 1% for GPPs, Cash % for cash) | **0.118** | **0.023** |
| Today's Cash % / Top 1% on the generator's per-draw lines | 0.112 (-0.006, SE 0.017; better in 6 of 19 contests, 1 of 4 weeks) | 0.017 |
| E[$] | 0.062 (-0.056, SE 0.017) | 0.011 |
| E[$], herd copies only (no dup-model discount off the herd) | 0.059 | |
| E[$] without the top 0.1% of prizes | 0.070 | |

Only Week 6 favours the generator's lines (0.252 against 0.158). **Nothing clears the bar.**

| Check | Result |
|---|---|
| Calibration, GPP | ROI -7.2% predicted, -7.7% realized; P(cash) 25.2% against 24.0% |
| Calibration, cash | ROI -11.4% predicted, -4.1% realized |
| Cash recalibration (logistic, held out by week) | Brier 0.259 -> 0.276 over all pairs (worse); his 10 entries 0.369 -> 0.233. Not shippable. |
| SimLev against the real top 1% | correlation 0.13 (n 367); top fifth +2.3 points over ownership, bottom fifth -2.0; by week 0.10, 0.29, 0.00 |
| Line coverage (real line inside the sim's 10-90% band) | GPP 13/13 for both lines; double-up top-1% 10/10; double-up cash line 5/10. The per-draw herd lead moves the lines by under a point. |
| Top 0.1% of prizes as a share of GPP E[$] | 9.1% overall; 18-27% in the $1 contests of 4,756-11,890 entries |

"Best in" (each lineup's top-E[$] contest) against where he entered, realized ROI over his 34 lineups:

| Best in, ranked by | Realized ROI | Where he entered | Difference |
|---|---|---|---|
| ROI | +16.3% | +18.1% | -1.9 (SE 17.6) |
| ROI without the top 0.1% | +35.6% | +18.1% | +17.5 (SE 18; the picks flip) |
| Herd-copies ROI | +22.2% | +18.1% | +4.0 (SE 17.8) |

The ROI picks lean on the big $1 fields, where a fifth of the E[$] is the thin top of the payout curve (only 3 of his entries
ever finished in the top 1% of paid places, so that end of the curve is the least known).

### The ship bar

`contest_retro.SHIP_BAR`: a ranking ships only if, held out by week, its within-contest Spearman with realized dollars beats
today's (Top 1% for GPPs, Cash % for cash) **by more than two standard errors over contests AND in a majority of the weeks,
with at least 4 weeks scored.** Double-ups have 3 weeks so far. `dfs research refresh` reports the bar and the numbers each week.

### Parked leads (not built)

- **Per-player double-up ownership.** The main error source: the oracle run says the generator is within 1-3 points once the
  ownership is right, and about 25 points of double-up duplicate share sit outside the chalk top 20.
- **His own cash overconfidence.** On his 10 double-up entries the sim predicted P(cash) 62% against a 20% cash rate. Ten entries
  is too few to fit a correction (the general recalibration made the other pairs worse).
- **GPP bands too wide.** 13 of 13 real lines inside an 80% band over 3 weeks: the band should miss about one in five.
- Smaller: stack rates move week to week; generated GPPs leave about $25 less salary unspent than real fields; the retro's
  min cash comes from a fit on all weeks, not a held-out one.

**If a ranking ever clears the bar:** OptPct / SimLev at EdgeRaw's end, a `dfs contests sim` command, the docs (CALCULATIONS.md,
SHEET_REFERENCE.md, COMMANDS.md), launcher entries. Tests: `tests/sim/test_payouts.py`, `test_field_gen.py`,
`test_contest_sim.py`, `test_contest_retro.py`. Rerun the retro after each week closes (`dfs research refresh`, or
`contest_retro.run_retro(<week>)`).

## Flashback: the settings backtest (2026-10-21)

`dfs backtest` answers "would these build settings have made money?" by rebuilding past slates with today's code and scoring
the builds in the contests Sam actually entered, against those contests' real fields and payouts. It is the proof harness for
any change to how lineups are built (the ownership-uncertainty field, portfolio selection, a rank, a rules-box line); it ships
nothing and writes no sheet. Code: `backtest.py` (inputs, builds, scoring), `backtest_settings.py` (settings file and flag
registry), `backtest_report.py` (text), `pool_store.py` (Sam's saved pool). Tests: `tests/test_backtest*.py`,
`tests/test_pool_store.py`.

**Method.**

- *No lookahead.* Each week is `research.curse.week_inputs`: the last EdgeRaw snapshot before Sunday 1 pm ET, `CalPts` on every
  rated row, the main slate. The builder is today's `lineup_suggest.suggest_builds` with the specs and lines
  `dfs lineups suggest` would use (3 cash builds, every QB stack in several GPP shapes, rank `top1`, the 120-second search
  budget), on **Sam's pool** where the week has one (`data/pools/<season>-wk<NN>.json`, saved by every `dfs sync` and
  `dfs lineups suggest` from now on) and otherwise the constructed pool of `sim/retro.pool_tags` (his Week 6 counts, top `CalPts`
  per position). Weeks 2, 4, 5 and 6 predate the saved pool, so they are constructed, and the report says so per week.
- *Entering.* For each contest he entered that week with a standings file (the standings' `srwildman` rows say how many entries,
  the history log gives the series, fee and size) the best `k` builds of the contest's kind go in, ranked as the tool ranks them
  (Cash %, or Top 1%; `--rank share`: that chance over 1 + the duplicates expected). The report also scores **every** build in
  every contest ("any build"): the same question with far less luck, since a single-entry double-up is one coin flip a week.
- *Paying.* A build's points are DraftKings' own (the standings' `FPTS`); its place is among the contest's real final scores
  (the build is an extra entry); it shares the payout with the real copies of the identical lineup in the field
  (`contest_sim.block_payout`); the payout table is `payouts.payout_table` with the min-cash tier fitted on his own results.
  Cashed = paid anything; Top 1% = at or above the contest's real top-1% score.
- *Beside Sam.* His real lineups in the same contests, twice: as DraftKings paid them (the history log) and re-paid on the same
  fitted table as the builds. The gap between the two is the table's error, printed as a check ("Payout table check").
- *Standard errors* are across **weeks**, the unit that is roughly independent: contests of one week share their players and the
  slate's luck (a week's three double-ups score the same build the same way). "ROI without the best ticket" removes the single
  biggest payout, because one GPP hit decides most week-level ROIs.

**Settings file** (TOML, every key optional; a plain `dfs backtest` is today's defaults): `[builds]` `cash`, `gpp`, `rank`,
`seconds`; `[rules]` the rules-box lines (applied: `Adjust`, `Ownership` as your frame, `Never use` as a pool removal; the
others need a built tab to iterate on and are reported as not applied); `[flags]` named switches. Unknown keys are errors.

**The registry for other sessions.** `backtest_settings.register(Flag(name, doc, prepare=, build=, assign=, choose=, version=))` from the
module that implements the switch, and add the module's dotted path to `backtest_settings.PLUGIN_MODULES` (imported when a
settings file names a flag nothing has registered; a module that is not on this branch is skipped). `prepare(ctx, value)`
returns the `BuildContext` the builder is given (change `ctx.edge`, `ctx.specs`, or `ctx.lines` to swap the simulated field);
`build(ctx, value, inner)` replaces or wraps the builder (`inner(ctx)` is today's, so a portfolio flag can take its candidates and
choose a set); `assign(builds, contest, k, flags)` chooses which builds enter a contest; `choose(ctx, builds, contests, value)`
chooses the week's entries as one set across every contest (`contests`: `WeekContest`s, the payout table and his entry count,
never the standings), runs once per week right after the builds on the same context, and is saved with them (`"chosen"` in the
run). A flag that chooses entries (`assign` / `choose`) leaves the "any build" rows alike on both sides, so `--compare` judges it
on the ship bar **as entered**. Bump `version` when the implementation
changes: saved builds are keyed by the settings, the active flags' versions and the code (HEAD plus a hash of uncommitted
`src/` changes), so a rerun of the same thing is free and a changed thing is rebuilt.

**Noise.** The live builder is time-boxed at 120 s. Two runs of one week at 120 s agree on 13 of 14 builds, only 5 of 13 match a
search that finishes, and under load (eight builds at once) the Week 2 result moved from -100% to +1,402% ROI on a different
handful of builds. So the backtest's default is `seconds = 600`: the search finishes (about 5 minutes a week, four weeks in
parallel about 6 minutes) and gives the same builds every run, which is what a comparison of two settings needs. `seconds = 120`
mimics the live clock; the report prints a caution when a run still hit the clock (a bigger pool than the constructed one).

**Limits.** Four weeks of real fields (Week 2 has one contest); the constructed pool is not his pool; the payout table is a fit
(about 14% rich on his GPP lineups); the extra-entry assumption moves a place by one; his multipliers, H2H and the $3.5M
Millionaire have no standings and are out (the report prints the share of his fees the scored contests cover).

**First result: today's defaults on Weeks 2, 4, 5, 6** (constructed pool, search run to the end; 29 of his contests, 53 entries,
85% of his main-slate Classic fees; identical on two runs).

| | entries | ROI | cash rate | top 1% | ROI without the best ticket |
|---|---|---|---|---|---|
| Builds, as entered | 53 | +19% | 49% | 1.9% | +14% |
| Builds, any build | 218 | +145% | 45% | 3.7% | +106% |
| Sam, as DraftKings paid | 53 | -8% | 28% | 1.9% | -28% |
| Sam, on the same fitted table | 53 | +4% | 28% | 1.9% | -23% |

By week (builds / Sam as paid): W2 -100% / +700%, W4 +99% / +32%, W5 +53% / -35%, W6 -56% / -54%. Builds minus Sam: -161% by
week (SE 214%, driven by his Week 2 $10 Hundo), ahead in 2 of 4 weeks. Not distinguishable from Sam on this evidence; the builds
are positive in both ROI measures and ahead on cash rate (his double-ups cashed 2 of 10, the builds' best cash build 3 of 3 weeks),
but "any build" GPP ROI rests on one Week 5 lineup (206 points), and three weeks of cash is three coin flips.
At the live 120 s clock the same code gave +50% and +103% ROI as entered on two runs (Week 2 -100% vs +1,402%): the noise that
`seconds = 600` removes.

## Deterministic search budget (2026-10-21)

The builder's search budget (`dfs lineups suggest --seconds`, default 120) used to be wall-clock seconds, so a busy machine gave
different builds. Measured with `dfs backtest`'s Weeks 4, 5, 6 on an almost idle machine (one other session's core busy): the
120 s search did **not** finish in any of them (the RB-stack and game-stack searches ran out of their boxes; Week 6 and Week 4 also the
plain stack search), the search phase took 90-95 s of the 120, and two runs of Week 6 agreed on 13 of 14 builds while only 5-7 of 14
matched a search that finishes (about 290 s). Under load (eight builds at once) the result moved much more. So the non-determinism was
mostly the clock, and a 120 s search is a cut one either way.

`search_clock.py` now counts the budget in search work: `late_swap_search._Search` reports every 512 nodes, `now()` is nodes divided by
`NODES_PER_SECOND` (33,000, measured: a 120 s wall-clock Week 6 search visited 3.27 million nodes in 99 s), and every deadline
(`lineup_suggest`, `lineup_shapes`, `lineup_iterate`, late swap's `top_lineups`) is measured in it. Three simultaneous Week 6 runs on a busy
machine gave identical builds; a solo run takes 97-112 s of wall for Weeks 5-6 (about two minutes), 170 s when three run at once. The phase
timers still show wall seconds. `search_clock.wall_clock()` restores real seconds. Nothing else about the builder changed.

## Ownership uncertainty (shadow) (2026-10-21)

**Why.** Every sim above treats projected ownership as known. It is a forecast: TFFB misses by about 3.4 points a player, and
his double-ups run far chalkier than projected and swing with one megadup lineup. Haugh & Singal (Management Science 2021) put
the field's ownership at the centre of their model as a random draw and report the largest documented DFS edge from it.
`own_error.py` is that draw; `own_error_retro.py` fits it on his real contests and judges it held out by week
(`python -m dfs.sim.own_error_retro`, about a minute). Part 1 only: nothing in the sim reads it yet.

**The data.** The 29 Classic contests of Weeks 2, 4, 5 and 6 (19 GPPs, 10 double-ups; Week 7's standings land Monday), each with the
pre-lock EdgeRaw snapshot (`contest_retro.week_slate_frame`). A player held by `c` of a contest's `N` lineups has
`c ~ Binomial(N, theta)`, `theta` his true share, so the contest's own finite size is NOT counted as ownership error (a generated
field reproduces that by itself). Everything below is the composite likelihood of those counts over the players forecast at 1%+,
the fit on the other weeks, the judging on the held-out one.

**The model.**

    mean   = field_own's sharpened TFFB (exp(k log Own%) within position, scaled to the position's total), k per format
    theta  = mean * exp(sigma * e - sigma^2/2),   sigma^2 = ln(1 + c0 + c1 / mean)     then scaled back to the position total

`c0` is the relative variance every player carries, `c1 / mean` the Poisson-like part that grows as a share shrinks (a Dirichlet is
the `c0 = 0` case); the QB, WR and TE of one team share a factor (`team_corr`); a week can tilt toward or away from the chalk
(`tilt_sd`). RB and DST errors are independent.

### The families, held out by week

Composite log-likelihood summed over the held-out weeks (higher is better; the same mean in every row):

| | Point forecast (today) | Dirichlet | Log-normal | **Hybrid** (`c0`, `c1`) |
|---|---|---|---|---|
| GPP (Weeks 2, 4, 5, 6; leave one week out) | -41,828 | -8,721 (alpha 118-141) | -8,796 (sigma 0.45-0.52) | **-8,690** (c0 0.06-0.09, c1 0.006-0.009) |
| Double-up (Weeks 4, 5, 6) | -56,091 | -4,950 (alpha 40-43) | **-4,760** (sigma 0.76-0.80) | -4,772 (c0 0-0.56, c1 0.014-0.057) |
| GPP, fitted on EARLIER weeks only (Weeks 4-6) | -47,085 | -8,288 | -8,336 | **-8,279** |
| Double-up, earlier weeks only (Weeks 5-6) | -29,281 | -2,669 | -2,605 | **-2,594** |

- **Treating ownership as known is wrong by tens of thousands of nats**, not a close call: a 5,000-entry contest's counts are so
  tight that any forecast miss is hopeless for a point forecast.
- **Log-normal beats Dirichlet** for double-ups (190 nats), **the Dirichlet is close in GPPs** (75 nats better than the plain
  log-normal); the hybrid is within 12 nats of the best in every row and best in three of four, so it is the one family kept. The cash
  `c0` is poorly identified (three weeks: 0, 0.56, 0.44); the likelihood is flat along that ridge.
- ~~The mean matters as much as the spread: field_own's softmax beats raw TFFB by a wide margin.~~ **Withdrawn (Part 2):** these
  numbers scored only the players each mean itself forecast at 1%+, so a sharper mean dropped players from the score and won by it. On a
  fixed set (TFFB 1%+) field_own's GppOwn / CashOwn are no better than TFFB as is; see "The mean, chosen by held-out fit" below. The
  family ranking above is unaffected (every family there shares one mean).
- Fitted on all four weeks: **GPP `c0` 0.074, `c1` 0.0082** (sigma 0.80 at a 1% share, 0.46 at 5%, 0.33 at 20%, 0.30 at 40%);
  **double-up `c0` 0.46, `c1` 0.017** (sigma 1.08, 0.77, 0.66, 0.64). Observed log-residual sd by level (pooled big contests, mean
  model fitted without the week): GPP 0.50 / 0.43 / 0.42 / 0.34 / 0.23 / 0.20 for forecasts of 2-4 / 4-7 / 7-12 / 12-20 / 20-35 / 35%+,
  against the model's 0.57 / 0.47 / 0.40 / 0.35 / 0.32 / 0.29: the model is too wide at the very top. Double-ups 0.83 / 0.73 / 0.56 /
  0.68 / 0.67 / **0.29** against 0.80 / 0.71 / 0.67 / 0.63 / 0.61 / **0.60**: the same, more so.

### What the marginal likelihood cannot see: errors move together

| Residual (log actual - log forecast, minus the week-position mean) | GPP | Double-up |
|---|---|---|
| QB with his WR / TE | **+0.33** (75 pairs) | **+0.46** (47 pairs) |
| WR with TE | +0.22 | +0.18 |
| WR with WR | -0.34 (position competition) | not enough pairs |
| RB with anyone, DST with anyone | -0.25 to +0.11, noise | noise |
| Week-wide tilt toward the chalk (slope on log forecast), by week | -0.19, -0.16, -0.16 | +0.10, +0.18, +0.01 |

A QB's popularity does pull his receivers (`team_corr` 0.33 / 0.46 fitted). The GPP tilt is the **same sign every week**: it is not
a random effect but the mean model forecasting the chalk about 25% too high in log terms at 35%+ and the longshots too low; the
double-up tilt does vary by week (`tilt_sd` 0.062; the GPP's 0.022 is its scatter about that bias). Fixing the GPP mean's shape is a lead
(the quadratic helps cash only).

### The end-to-end test: where does the real field mean fall?

A contest's field mean is `sum(share_i * points_i)`. With the week's real points and the ownership drawn from the model (plus the
contest's own finite-size noise), the real field mean should fall inside the 10-90% band four times in five (z = real minus model,
over the model's sd; 1.0 = calibrated). Dependence parameters from the other weeks:

| | Contests | Inside 80% band | rms z | Model sd (points) | Mean abs miss (points) |
|---|---|---|---|---|---|
| GPP, ownership known | 19 | 21% | 5.31 | 2.0 | 4.74 |
| GPP, independent errors | 19 | 63% | 1.19 | 4.35 | 4.65 |
| GPP, + team factor | 19 | 63% | 1.19 | 4.46 | 4.73 |
| GPP, + team factor + tilt | 19 | 63% | 1.17 | 4.48 | 4.70 |
| Double-up, ownership known | 10 | 0% | 15.7 | 0.76 | 10.46 |
| Double-up, independent errors | 10 | 60% | 1.85 | 6.98 | 8.27 |
| Double-up, + team factor | 10 | 60% | 1.86 | 7.13 | 8.38 |
| Double-up, + team factor + tilt | 10 | 60% | 1.85 | 7.17 | 8.24 |

By week (real field mean vs the model's, sd): double-ups Week 4 **110.2 vs 127.3 (5.9), z -2.9**, Week 5 143.4 vs 147.4 (8.7), z -0.5, Week 6
126.6 vs 127.7 (7.2), z -0.2; GPPs Week 4 117.0 vs 122.8 (z -1.4), Week 5 136.2 vs 140.9 (-1.0), Week 6 125.3 vs 122.8 (+0.6).

- **A known-ownership sim claims a field mean good to 0.8 points (double-up) or 2 (GPP) and is wrong by 10 and 5.** With the error the
  band is honest to within the data: GPPs are slightly under-dispersed (63% inside; z 1.19), double-ups over-dispersed by one bad week (Week 4,
  the megadup week, is 17 points off; Weeks 5-6 are inside by 0.2-0.5 sd).
- **The team factor and the tilt barely move the field mean's spread** (4.35 -> 4.48 points). Their effect is on the lineup side: a stack of
  a QB and his receivers is correlated with the field's mean through the same players, which Part 2 measures.
- The draws do not change the model's MEAN (abs miss 4.65 vs 4.74): the error model widens the lines, it does not move them. A better mean
  is the mean-model lead above.
- **Caveats.** Four weeks; the contests of a week share one realisation of the field's error, so the real sample is 3-4 weeks and the band
  rates are +/- 20 points; Week 2 is a single 100-entry contest; `tilt_sd` rests on 2-3 weekly slopes.

### The mean, chosen by held-out fit (Part 2, step 1; Part 3's stake and size terms)

**Reconciling with the ownership research (2026-10-20), which said TFFB runs LIGHT on GPP chalk.** Both are true; they condition on
different players. Selected on the OUTCOME (players who finished 20%+) any forecast looks light, because those are the players who beat it;
selected on the FORECAST, the question is whether what it calls chalk shows up. Scored per contest: TFFB as is is -8.0 points on actual
20%+ and -7.3 on forecast 20%+ (light both ways); field_own's GppOwn (k 1.29) is -0.4 / **+2.6**; in Part 1's pooled numbers, weighted to the
$1 fields of 4,756-11,890 entries (the least chalky), GppOwn was **+10.3 points on the players it forecast at 35%+** (actual 0.77 of
the forecast: Gibbs 49.6% forecast vs 29.0% real, Addison 37.6 vs 24.5, Nacua 53.6 vs 42.2). That is the "25% too high": GppOwn, on its own
35%+ calls, in big cheap fields.

`MeanModel` (`own_error.py`): within position `exp(k_c L + curve (L - log 5%)^2)`, `L = log Own%`, scaled to the position totals, with
`k_c = k + k_fee log(fee / $5) + k_size log(entries / 2,000)`. Each spec fitted jointly with the error by composite likelihood, every contest
on its own stake and size, scored on the SAME players for every spec (TFFB 1%+). Leave one week out:

| | Spec | Log-lik vs TFFB | Weeks better | MAE (pts) | Chalk bias, on actual / on forecast |
|---|---|---|---|---|---|
| GPP | TFFB as is | 0 | | 3.92 | -8.0 / -7.3 |
| | GppOwn (k 1.29, cross-entropy) | -149 | 1/4 | 3.80 | -0.4 / +2.6 |
| | power (k by likelihood, ~1.08) | +19 | 3/4 | 3.76 | -5.6 / -4.0 |
| | fee (k ~1.08, +0.08 to +0.12 per log fee) | +38 | 3/4 | 3.65 | -5.0 / -3.0 |
| | **size** (k ~1.05, **-0.08 to -0.10** per log entries) | **+70** | **4/4** | **3.59** | -4.9 / -2.6 |
| | fee + size | +62 | 4/4 | 3.60 | -5.0 / -2.6 |
| Double-up | TFFB as is | 0 | | 8.65 | -30.2 / -32.1 |
| | CashOwn (k 1.8) | -7 | 1/3 | 5.76 | -10.3 / -6.8 |
| | power (k ~1.5) | +82 | 3/3 | 6.56 | -16.8 / -13.2 |
| | **quad** (k ~1.36, curvature ~0.2) | **+121** | **3/3** | **5.69** | -15.5 / -10.3 |
| | fee + size | +88 | 3/3 | 6.49 | -16.4 / -12.7 |

- Fitted on EARLIER weeks only nothing in GPPs beats TFFB (best: size -26, 1 of 3; Week 4 has only the Week 2 Hundo to learn from); in
  double-ups size / stake win 2 of 2 by +23 to +26.
- The likelihood wants LESS sharpening than the chalk bias does (GppOwn's k 1.29 fixes the chalk and starves the tail): a mild exponent that
  flattens as the field grows is what holds up across all the players.
- **Part 3, stake and field size.** GPPs: the size term is the same sign in every fold (-0.08 to -0.10): an 11,890-entry field is about
  0.2 flatter in exponent than a 784-entry one (k 0.91 vs 1.13). Fee alone is positive, but fee and size are confounded in his contests
  (the $1 contests are the big ones) and the size term fits better. Double-ups: no stable stake or size term (all are 2,758-4,597 entries).
- Chosen (`own_error_retro.CHOSEN_SPECS`): **GPP `size`, double-up `quad`**. Neither clears 2 SE on held-out weeks; both ride the switch.

### Into the sim (Part 2)

- `field.with_ownership_draws`: the relative lines' field takes the fitted mean (at a representative contest: double-up $10 / 3,000,
  GPP $5 / 2,000) and `ownership_scenarios` (200) drawn ownerships; draw d of the simulator is scored against scenario d mod 200
  (`simulate._field_lines`). Two switches, both default **false**: `[sim.lines] ownership_mean` (the fitted mean only, no draws: most
  of the Cash % gain below) and `ownership_uncertainty` (the mean and the draws). `sim_inputs.resolve_lines` names the one that is on in
  its note, and says when no model is fitted.
- The contest sim: `contest_sim.with_scenarios` reweights the SAME generated lineups to each drawn ownership (`field_gen.scenario_weights`,
  an exponential tilt by damped iterative scaling: drawn ownerships met to 0.25 share points against 2.5 unweighted, 857 effective lineups
  of 5,000 per scenario), and a field keyed by a contest's series is that contest's own (Part 3: each contest reweighted to the mean at its
  own stake and size, its duplicate estimate on that mean). Regenerating per scenario was not needed.
- Backtest: `own_error_flag` registers the same two as Flashback flags (`ownership_mean`, `ownership_uncertainty`); a backtest of week N
  uses the model fitted on the weeks before N only (`own_error_retro.model_before`, cached in `data/model_cache/own_error/`). After each
  close: one `dfs backtest --compare` per flag, and `python -m dfs.sim.own_error_sim_retro` with the new week
  (`dfs week close --research` does both, from `research/settings/*.toml`, and folds them into the week's close report).

### The measures, held out by week

Every model fitted without the week it is judged on (`own_error_sim_retro`, `python -m dfs.sim.own_error_sim_retro`). Settings: **today**
(ownership known), **mean** (the fitted mean, no draws), **draws** (mean + draws), **per contest** (draws, each contest's own field).

A's contest retro (his lineups and real field lineups in every real contest, Weeks 2 and 4-6):

| | today | mean | draws | per contest |
|---|---|---|---|---|
| Double-up cash line inside the 10-90% band (10 contests) | 5/10 | **8/10** | 7/10 | 7/10 |
| Double-up top-1% line / GPP min-cash / GPP top-1% inside | 10/10, 13/13, 13/13 | same | same | same |
| Cash % in double-ups: shown vs realized 48.0% (1,107 pairs) | 52.7% | 41.7% | 44.2% | 44.2% |
| Brier, Cash % in double-ups | 0.2555 | 0.2493 | 0.2490 | 0.2490 |
| Top 1% in GPPs of 700+: shown vs realized 1.32% (1,442 pairs); Brier | 0.83%; 0.0130 | 0.77%; 0.0130 | 0.89%; 0.0130 | 0.89%; 0.0130 |
| Ranking: today's number vs dollars, GPP (19 contests) | 0.118 | -0.003 (SE 0.003), 3/4 wks | +0.002 (0.003), 2/4 | +0.002 (0.003), 2/4 |
| Ranking, cash (10 contests) | 0.023 | +0.013 (0.010), 2/3 | +0.009 (0.008), 2/3 | +0.009 (0.008), 2/3 |
| E[$] ranking, GPP (vs today's Top 1% ranking) | 0.062 (-0.056, SE 0.017) | 0.079 | 0.093 (-0.025, 0.016) | **0.101 (-0.017, 0.013)** |

F's curse rows (today's builds, random lineups from the same pool, real field lineups; Weeks 1-6, the real cash and top-1% lines):

| Cash % shown minus cashed (SE over weeks) | today | mean | draws |
|---|---|---|---|
| Cash builds (120) | **+15.5 (6.9)** | +7.1 (6.0) | **+8.4 (6.0)** |
| GPP builds (82) | +11.1 (10.5) | +3.2 (9.9) | +4.6 (9.9) |
| Random lineups (1,800) | +14.3 (11.2) | +7.4 (10.4) | +9.0 (10.6) |
| Real field lineups (999) | +12.4 (10.3) | +2.0 (10.3) | +4.4 (10.4) |

| Brier of Cash %, draws minus today (SE; weeks better) | Cash builds -0.027 (0.021; 3/5) | GPP builds -0.022 (0.026; 3/5) | Random **-0.024 (0.012; 4/5)** | Field -0.012 (0.012; 2/4) |
|---|---|---|---|---|

| Top 1%: shown summed / hits | today | draws |
|---|---|---|
| GPP builds | 2.12 / 2 | 2.21 / 2 |
| Random | 16.3 / 14 | 17.4 / 14 |
| Field | 8.7 / 9 | 9.4 / 9 |

| Ranking inside a week (Spearman of the shown number with realized points), draws minus today | |
|---|---|
| GPP builds (Top 1%) | **-0.057 (SE 0.062), better in 1 of 6 weeks** (mean only: -0.096, 0.055, 2/6) |
| Cash builds (Cash %) | -0.015 (0.035), 2/6 |
| Random lineups | -0.002 (0.006) |

**Verdict: nothing clears the ship bar; the code ships default off.**

- **What it fixes is the Cash % level.** About half of the 16-point overstatement F found goes away (cash builds +15.5 -> +8.4), and the
  Brier improves in every set (random lineups 4 of 5 weeks, 2.0 SE: the closest thing to a pass). It is almost all the MEAN (the chalkier
  double-up field raises the field mean the line sits on); the draws give back a little of it (they add spread, and a lineup's Cash % moves
  toward 50%).
- **The double-up cash line lands in its band 7-8 times in 10, up from 5.** The GPP lines were already always inside (too wide, A's finding).
- **Top 1% is unchanged** (calibrated before and after): not worse.
- **The build ranking is the thing to watch.** Inside 20 GPP builds the Top 1% ranking's correlation with points falls 0.06 (under 1 SE,
  but 5 of the 6 weeks it changed in were not better); in A's retro over his and the field's lineups it does not move. The bar says "must not
  get worse": not proven worse, not shown safe.
- **Part 3:** per-contest fields leave Cash % / Top 1% (the lineup sim's numbers) and the lines where they were; they help E[$]'s ranking
  (0.062 -> 0.101, now 1.3 SE from today's Top 1% where it was 3.3 SE behind), not enough to beat it.
- Caveats: 4 weeks of standings (3 of double-ups), 6 of builds; contests of a week share one realisation; Week 1 has no ownership, so it is
  identical in every setting; F's rows use a constructed pool, not his.

**Files.** `own_error.py` (pure: the families, `draw_shares`, `OwnershipErrorModel` saved to `data/model_cache/own_error.json`),
`own_error_retro.py` (loading, folds, fits, the field-mean check, `fit_model`); tests `tests/sim/test_own_error.py`,
`test_own_error_retro.py`; Parts 2-3: `own_error_sim_retro.py` (the measures), `own_error_flag.py` (the backtest switch), the
hooks in `field.py`, `simulate.py`, `field_gen.py`, `contest_sim.py`, `contest_retro.py`, `sim_inputs.py` and `config.py`.

## Portfolio selection (shadow, default off) (2026-10-21)

`portfolio.py` chooses the lineups for all of a week's entries as one set: greedy marginal gain on the contest
simulator's shared draws, his entries in one N-max contest placed together (each one's place counts the others
that scored more; an identical lineup twice splits its own prize). Math: `docs/CALCULATIONS.md`, "Portfolio
selection". Live hook: `dfs lineups suggest --portfolio` (GPP entries only, from DK's entries file), saved for
`dfs entries fill`'s grey hint; `--portfolio-wide N` gives it the retro's wide pool (N noisy builder runs; a chosen lineup
that is not a build joins the round as a new GPP build). **Off by default: the retro below does not clear the ship bar.**
The live wide pool is smaller than the retro's (8 runs of 15 s against the curse study's 24 of 120 s).

**What it can and cannot do.** Expected dollars add across contests, so with a correct model joint selection
cannot raise E[$] in single-entry contests; it can only matter inside N-max contests, for objectives that are not
a plain sum (P(any top-1%), log growth), and when the model is wrong in a correlated way.

### The retro (`sim/portfolio_retro.py`; Weeks 2, 4, 5, 6; 53 entries, $454 in fees)

Each week's real contests with Sam's real entry counts; candidates are today's builder's builds at the lock (the
curse study's saved `today` jobs), and for the `_wide` arms also the 100 best (quick E[$]) of the builder's runs
on noisy projections (~330 lineups a week). Every arm is chosen on 8,000 draws and its model numbers are
reported on a fresh 8,000; the field, herd and construction rates are fitted on the other weeks. Realized =
the arm's lineups placed among the real field (his own entries removed), real copies, the contest's payout table.
`today` = `entries.suggest_pick`: the best build (Top 1% / Cash %) in every contest.

| Arm | Profit | ROI | Profit without its best contest | Worst week | Top-1% / top-10% / paid entries | Mean finish (share of field) |
|---|---|---|---|---|---|---|
| Sam's actual | +$17 | +4% | -$183 | -$91 | 1 / 7 / 15 | 0.46 |
| today | +$39 | +9% | -$52 | -$100 | 1 / 10 / 23 | 0.44 |
| edollars | +$93 | +21% | +$6 | -$180 | 1 / 14 / 24 | 0.44 |
| top1 | +$139 | +31% | +$9 | -$88 | 2 / 12 / 23 | 0.42 |
| robust (B = 10 weeks of fees, shocked draws) | +$754 | +166% | +$429 | -$150 | 3 / 11 / 26 | 0.36 |
| robust_tight (B = 2 weeks) | +$556 | +123% | +$232 | -$160 | 3 / 17 / 24 | 0.36 |
| edollars_wide | +$110 | +24% | -$6 | -$180 | 0 / 13 / 22 | 0.44 |
| top1_wide | +$125 | +28% | -$58 | -$108 | 0 / 12 / 19 | 0.45 |
| robust_wide | +$1,153 | +254% | +$493 | -$70 | 2 / 21 / 30 | 0.31 |

(`edollars_capped`, a 75% player and 40% lineup cap on GPP entries, left 8 slots empty under the caps: the
builds share their core too much to fill 18 GPP entries; +$57.)

**Paired against today, weeks as units (n = 4):** profit: robust_wide +$278/week, SE $234, z 1.2, 4 of 4 weeks;
robust +$179 (z 1.1, 3 of 4); top1 +$25 (z 0.4); edollars +$14 (z 0.4). Paid entries: robust_wide +1.75/week
(z 3.7, 4/4); top-10% entries +2.75 (z 2.3, 4/4); mean finish -0.20 of the field (z -2.0). Nothing else reaches
z 2 on anything. Per contest (n = 29, correlated within a week): robust_wide +$38 (SE $24), better in 13, worse in
7. Most of every robust arm's profit is Week 5's single-entry GPPs.

**In the model (fresh draws):** edollars buys +$3 of E[$] a week over today (+2%), as the arithmetic says; top1
doubles P(any top-1%) (8.9% -> 16.3%; 20% wide) for -5% of E[$]; robust and robust_wide are slightly below
today's E[$] even on fresh shocked draws (171 / 164 vs 175; shocked 179 / 171 vs 174): their in-sample shocked
E[$] was flattered by $9-32 (8 worlds is a small sample of "how the projections are wrong"). Selection bias on
nominal draws is under $1 for the narrow arms, $5 for edollars_wide.

**Concentration (GPP entries a week, 10.75 on average):** today puts one player in 100% of them, on 4 distinct
lineups, two entries of one contest sharing 4.7 players; Sam's own: 66%, 8.5 lineups, 1.3 players. top1_wide
spreads most (87%, all distinct, 2.6 shared); robust_wide 94%, 8 lineups, 3.9.

### Verdict

**Not proven; ships default off.** No arm beats today on realized profit by 2 SE (best z 1.2), the comparison
spans 4 weeks with one dominant week, and nine arms were tried (the best of nine z's near 1 is what noise gives).
The one consistent sign: **robust_wide** (risk-adjusted, chosen on correlated-shock draws, from the wider pool)
won all 4 weeks on profit, paid entries and top-10% entries and finished best by mean place, while the model
itself does not prefer it. That pattern (better in reality than in the model) fits "the real projection error is
larger and more correlated than the simulator's", which is the Week 6 story, but four weeks cannot separate that
from luck. **Next:** rerun with Week 7 when its standings land (`run_retro`), and keep `robust_wide` as the
candidate; the bar stays >2 SE over weeks and most weeks.

**As a backtest flag (2026-10-22).** `sim/portfolio_flag.py` registers `portfolio` with Flashback (a `choose` hook), so the
Week 7 decision is `dfs backtest --weeks 2,4,5,6,7 --settings portfolio.toml --compare today.toml`, with `portfolio.toml`:

    name = "portfolio (robust wide)"
    [flags]
    portfolio = true          # or { objective = "robust", wide = 8, sims = 4000 }

`true` is `robust_wide` as the live hook would run it: robust on shocked draws, the builds plus the wide pool of 8 noisy builder
runs (`--portfolio-wide 8`), GPP contests only (cash keeps today's pick, as live). Two differences from the retro above, both
toward the live hook: the wide pool is the live one (not the curse study's 24 longer runs), and the generated GPP field is fitted
on the weeks **before** the backtested one only (`field_model_before`; the retro fitted on every other week), so Week 2, with no
GPP field before it, keeps today's pick. The backtest also scores against its own fitted payout tables and the backtest's own
builds (Sam's saved pool from Week 7 on), so its numbers are not the retro's.

**First backtest run (Weeks 2, 4, 5, 6; 29 contests, 53 entries, $454).** As entered, `portfolio = true` returned -8% ROI
against today's +19%: -23.0 ROI points by week (SE 13.4), winning 0 of 4 weeks (Week 2 is identical by construction, Week 6
tied at -56%; Weeks 4 and 5 lost, +57% vs +99% and +3% vs +53%). The model preferred the plan every week (E[$] +13% to +26%,
P(a top-1%) about double). So on the backtest's own builds and earlier-weeks field the retro's `robust_wide` edge does not
reproduce: **not proven, stays default off**; Week 7 is the next point, and a second straight loss argues for dropping it.

## Late swap by position, and the projection-shrink flags (Week 8 round, 2026-10-27)

**Late swap by position** (`late_swap_position.py`). "Late swap isn't always about best projection": Sam swaps to get *different*
when he is behind and to get on chalk (*hold position*) when his different pieces already hit. On a live state (`--state live` or
`--field`) each GPP lineup now prints its position (`Position: top 12%, 18 pts behind the top-1% line (Top 1% 3.2%)`: the real
field's own order statistics when an export is loaded, `about top X%` from the measured line offsets (top 1% = field mean + 63.2, top
10% = + 31.9, Double Up = + 0.6 at about 45%) when it is not), and every swap is headed **LEVERAGE** (the players in are less owned than
the ones out), **HOLD** (more owned) or **EVEN** (the summed ownership moves under a point), with the in/out ownership; ownership is
the real %Drafted under `--field`, else Own%. The Cockpit's swap block shows the label and the position. Nothing about the ranking
changed: swaps are still ordered by the change in P(Top 1%); the label only says what kind of swap it is.

**The retro** (`python -m dfs.late_swap_retro --weeks 4,5,6,7`, the 1 pm state; 2,542 sampled GPP lineups, 739 swaps by today's
projected line). By the lineup's chance of finishing above the real top-1% line it is `behind` (under 1%: 90% of the sample, a median 60
points under the line), `near` (1-10%) or `ahead` (10%+). Does the Top-1%-maximising swap go LEVERAGE when behind and HOLD when ahead?

| mode | behind: LEVERAGE / HOLD | near | ahead (n = 12-13 swaps) | all swaps LEVERAGE | pool LEVERAGE |
|---|---|---|---|---|---|
| P, today (projected line) | 30% / 58% | 33% / 56% | 23% / 69% | 30% | 25% |
| PS (+ the started games known) | 33% / 52% | 36% / 56% | 15% / 69% | 33% | 25% |
| PSK (PS, line swings x1.5) | 42% / 43% | 41% / 48% | 17% / 67% | 41% | 25% |
| R (the real field's line) | 45% / 40% | 40% / 47% | 17% / 75% | 43% | 25% |

(labelled by the real Own%; the projected-Own% labels differ by a few points.) **Partly.** Ahead of the line every mode goes HOLD
(67-75%, from a pool that is 12-18% leverage), as Sam does; but only the real-field line goes LEVERAGE when behind (R 45% against 40%
HOLD; the near-the-line lineups 40%), and today's projected line holds on chalk everywhere, 58-62% HOLD whatever the position, and the
deeper behind the more (the farthest third of lineups: 65% HOLD). **Why:** the projected top-1% line is the field mean plus a constant,
and it swings less with the slate than the real line does (SD of the line over the draws in Weeks 5-7: real 10-14 points, projected 7-10),
which plausibly understates what a slate-wide swing does to a lineup that shares players with the field (the mechanism is a reading of the
SD, not a measured one: per-player slopes of the line on a player's points were too noisy to separate). Widening the projected line's swings to the real
ones (PSK, x1.5, a measured ratio of about 1.1-1.7) moves its choices to the real field's (mean ownership change of the chosen swap
+0.039 -> +0.015 against R's +0.013; LEVERAGE 33% -> 41%). **But the realised result does not move:** PSK minus PS is points -0.03
(SE 0.04), top 1% +0.000 (SE 0.000), finish percentile +0.000 (SE 0.001) over 4,338 lineups, and the retro has little power at a 0.6%
base rate. So the default is unchanged (`late_swap_field.LiveLines.amplified` exists for the test only); the label and the position
are what ship. The sampled lineups are the contests' FINAL lineups, which already hold whatever late swapping their owners did.

**Backtest flags added** (`sim/shrink_flag.py`, `sim/own_error_flag.py`; `dfs backtest --compare`, Weeks 2, 4-7, 38 contests; all default
off): `shrink_salary` / `shrink_tffb` pull each CalPts toward the position's salary line / TFFB's projection with the curse study's
per-position weights, fitted on the weeks **before** the backtested one only (Week 2 on Week 1 alone); `ownership_uncertainty_gpp` is
`ownership_uncertainty` for the GPP builds only (the cash builds are scored on the plain field). ROI points, flag minus today:

| flag | as entered, cash | as entered, GPP | any build, cash | any build, GPP | any build, all (by week, SE; weeks won) |
|---|---|---|---|---|---|
| `shrink_salary` | -50 (SE 50, 0/4) | +105 (SE 86, 2/5) | -17 (SE 42, 1/4) | -85 (SE 98, 2/5) | -80 (SE 81), 3/5 |
| `shrink_tffb` | -50 (SE 50, 0/4) | +51 (SE 34, 2/5) | -10 (SE 20, 1/4) | -35 (SE 15, 0/5) | -28 (SE 9), 0/5 |
| `ownership_uncertainty_gpp` | 0 (identical) | +74 (SE 74, 2/5) | 0 (identical) | +144 (SE 121, 3/5) | +120 (SE 102), 4/5 |

Neither shrink clears the ship bar; both lose on cash as entered (0 of 4 weeks) and on any-build ROI. The GPP-only ownership variant
matches `ownership_uncertainty`'s Week 7 result (+121, SE 102; all of its gain was GPP, and here cash is identical to today by construction); not proven either.
