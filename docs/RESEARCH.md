# Research pack: measured constants for the Edge Finder

`dfs research ...` replaces guessed constants with measured ones, using **twelve seasons of public nflverse /
ffverse history (2014-2025)** and nothing else: no sheet, no credentials, no TFFB. Nothing here writes to a
sheet, and nothing in `dfs.model` was changed -- this package imports from it.

Every claim is judged **out of sample**: thresholds, weights and tables are fit on **2014-2021** and scored once
on **2022-2025**. Every effect carries a 90% interval clustered by game (weather, rest) or by player (signals),
and every study says how big the effect is, not just whether it exists. A null result is a result: several of
the local round's starting constants should be dropped, not tuned.

```
dfs research fetch                 # one-time: download the extra public data (cached under data/)
dfs research run --study all       # rebuild every output below from the cache; or r1 .. r5
```

`dfs research` is registered hidden, like `dfs model`. A full run takes about 90 seconds from a warm cache; the
first run adds about a minute to build the UM baseline. The outputs, each with a metadata block (study, seasons,
n, date generated, code version):

| File | Study |
|---|---|
| `models/research/redistribution.csv` (+ `.csv.meta.json`) | R1: the learned redistribution table, with n |
| `models/research/redistribution_constants.json` | R1: recommended constants, prediction test, stability, by-reason |
| `models/research/matchup_weights.json` | R2: per-position matchup components, lookback, weights, holdout gain |
| `models/research/signal_thresholds.json` | R3: BUY / FADE / USAGE threshold sweeps and verdicts |
| `models/research/weather.json` | R4: wind / temperature / rain effects, the wind-flag threshold, Vegas pricing |
| `models/research/quick_checks.json` | R5: short week, divisional, home / away, back-to-back road |

## Headlines

1. **The hand-set "next man up gets 60%" rule is wrong for targets, and fits carries only roughly.** When a WR1 is
   out, WR2 gains 0.13 of the vacated targets (not 0.60). Out of sample the rule makes target predictions
   *worse* than doing nothing (next-man-up MAE 3.41 vs 2.41 targets). For carries it does as well as the learned
   table overall, but loses on the next man up, and 0.47 is closer than 0.60 (RB2 when RB1 is out).
2. **About 0.3 of a missing WR's volume goes nowhere** (0.27 mean, 0.41 median; 0.50 in 2022-2025): to players
   outside the prior rotation or into the team throwing less. The hand-set rule assigns all of it (nothing is left unassigned), so it has no place for a leak.
3. **Matchups are context, not edge.** Beyond UM, no position gains more than 0.04 points of MAE (QB 0.039,
   RB 0.015, WR 0.015, TE 0.003, DST 0.001; the bar for "edge" was 0.2).
4. **Drop BUY↑. Keep FADE↓ for TEs only. Keep only the RB carry-share jumps of USAGE.** Salary lag cannot be tested.
5. **The wind flag belongs at about 15 mph, not 20, and wind is an under-priced total.** QBs lose about one point
   at 10-19 mph (and about 0.5 per +5 mph); Vegas moves the closing total only about a quarter as much as the
   scoring actually falls. Rain (-1.7 for QBs) and cold matter as much.
6. **Home field is a real, unmodelled QB effect (+1.1 points)**; short week, divisional and back-to-back road
   do not hold up.

## What everything is measured against

**UM, out of fold** (`dfs.research.baseline`). Features, the shipped method per position (the GBM for WR and DST,
the `0.5 x L8 DK + 0.5 x L3 xFP` blend for QB, RB and TE) and the out-of-fold predictions are exactly
`dfs.model`'s: each season predicted by a model fit on the other eleven. The residual is `actual - UM`.
UM's population is offense with at least one prior game and last-3 xFP of 4 or more, plus every DST team-game
(51,860 residuals). Two things to know:

- UM's out-of-fold GBMs for 2022-2025 saw the other eleven seasons, including other test seasons. That should only
  make UM *harder* to beat on the test seasons, so a finding that holds there is conservative. The research
  fits (thresholds, ridge weights, tables) only ever touch 2014-2021.
- If `dfs model train` changes which method ships, delete `data/research_cache/baseline.parquet` (or run with
  `refresh`) so the baseline is rebuilt.

**Prior games only.** Every component, share and flag is computed from games strictly before the one being
predicted (`dfs.research.rolling`, `r1.prior_windows`). Tests prove it: appending the current game, or putting
absurd values in it and in every later game, changes nothing.

**Residuals are demeaned within (position, season)** before comparing flagged and unflagged rows, so an era's
level shift is not mistaken for an effect.

**Tested.** `tests/research/` (no network, no cache) covers: absence detection excludes byes; every share and component is
prior-only, with a leakage test that adds the current game (and absurd values in it and later games) and asserts
nothing changes; the ridge folds are whole seasons (and a random-row split would have flattered the CV); thresholds
are chosen on the fit seasons only; the redistribution bookkeeping balances; and the schemas of every JSON and CSV
output, both on freshly written files and on the ones shipped in `models/research/`.

**Verdicts** (R3, R5) use one rule. **keep**: the expected sign in both periods, a test-period effect of at least
0.5 points, and a test 90% interval that excludes zero. **borderline**: all of that except the size (real but
small: context, not something that should move a lineup). **drop**: anything else.

---

## R1. Who inherits a missing starter's volume?

### Question

When a regular is out, who gets his targets and carries -- and how much of it never reaches anyone?

### Method

All shares are from the team's **previous 3 games** in the same season, so weeks 1-3 are excluded. The unit is
the **team-game**, so a bye week has no game and can never produce an absence (a test covers this).

- **Regular**: at least 15% of the team's targets or 30% of its carries over those 3 games (a game he missed
  counts as 0, so a player already out for weeks stops being a "regular": what is measured is mostly the *first*
  missed game, against a baseline in which he played).
- **Absence**: a regular with no stats row for his team in a game the team played. A stats row exists for anyone
  who recorded a stat, so the only case not seen is a player on the field with no box-score line. Snap counts
  (keyed by a different player id) would catch it and add nothing else, so they were not used.
- **Clean event**: a team-game with exactly **one** regular out. Games with two or more are counted and excluded.
- **Reason** from the weekly injury report: `injury` (listed with an injury), `suspension` (the report's text says
  so), otherwise `other` (listed for a non-injury reason, or simply **unlisted**: released, traded, suspended,
  rested or unreported). Almost no suspension is ever on an injury report, so *suspension cannot be separated*:
  zero events carry that tag.
- **Vacated volume** `V` = the absent player's prior share x the team's mean volume per game over the window.
  The target channel is analysed for target-share regulars, the carry channel for carry-share regulars.
- **Gain** of a teammate who played = his actual count minus (his prior share x the same team baseline), as a
  fraction of `V`. Teammates are grouped by absent player's (position, rank) crossed with each beneficiary's
  (position, rank), where rank is **usage in the prior window** (targets for WR/TE, carries + targets for RB,
  attempts for QB): WR1, WR2, WR3, WR4+, RB1, RB2, RB3+, TE1, TE2+, QB1, QB2+.

**The bookkeeping.** With `T` the team's actual volume and `TB` its baseline, per event and exactly:

`gain(prior-rotation teammates who played) + gain(players outside the prior rotation) = V + (baseline volume of other rotation players who also missed) + (T - TB)`

so the vacated volume reaches a rotation teammate, reaches a newcomer, or leaks because the team simply ran
fewer plays. **Nowhere = 1 - (rotation teammates' gain / V).** The identity is asserted in a test and holds to
machine precision on all 1,063 events.

**Churn is subtracted.** Every game has churn: fringe players who do not play, newcomers who do, a team running
more or fewer plays than its last three games, a 3-game baseline that includes games a player missed. Run through
the identical machinery, 3,935 **no-absence control games** show it (a typical game: +1.1 targets to outsiders, +1.7
net to the rotation, 2.9 targets of baseline volume missing). All numbers below are **net of the control mean**
(`net_*` columns of `redistribution.csv`); the raw fractions are alongside (`mean_frac` ...).

### Result: where the vacated volume goes

1,063 clean events from 1,394 regular absences (152 team-games with two or more regulars out were excluded).

|  | n |
|---|---|
| Regular absences found | 1394 |
| Team-games with a regular out | 1215 |
| ... with two or more regulars out (excluded) | 152 |
| Clean single-regular events analysed | 1063 |
| No-absence control team-games | 3935 |
| Clean events by reason: injury | 751 |
| Clean events by reason: unlisted | 300 |
| Clean events by reason: listed_non_injury | 12 |

By absent player: RB1 355, WR1 239, WR2 196, RB2 111, TE1 111, WR3 39, QB1 5, TE2+ 4, RB3+ 2, QB2+ 1 (QB1 and the 2+ ranks are too thin to say anything: ignore them).

Each table: gain as a fraction of the vacated volume, net of churn. "Mean" is the mean of per-event fractions;
"pooled" is total gain over total vacated volume (it weights big absences more); the interval is for the mean.
**Per-player rows are conditional on that player playing**; rows labelled "(each)" are per player, and several
players share a label.

**WR1 out** (targets, n=239):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| WR2 | 221 | 0.13 | [+0.08, +0.18] | 0.09 | 0.11 |
| WR3 | 197 | 0.15 | [+0.11, +0.20] | 0.11 | 0.14 |
| WR4+ (each) | 382 | 0.17 | [+0.14, +0.20] | 0.09 | 0.16 |
| TE1 | 219 | 0.03 | [-0.01, +0.06] | 0.00 | 0.03 |
| TE2+ (each) | 269 | 0.04 | [+0.01, +0.07] | 0.01 | 0.04 |
| RB1 | 238 | -0.02 | [-0.05, +0.02] | -0.05 | -0.01 |
| All WRs | 239 | 0.65 | [+0.57, +0.74] | 0.57 | 0.61 |
| All TEs | 239 | 0.07 | [+0.03, +0.12] | 0.05 | 0.07 |
| All RBs | 239 | 0.00 | [-0.05, +0.05] | -0.02 | 0.01 |
| Outside prior rotation | 239 | 0.12 | [+0.08, +0.16] | -0.01 | 0.13 |
| Team throws more / (fewer) | 239 | -0.18 | [-0.30, -0.06] | -0.27 | -0.20 |
| Prior-rotation teammates, total | 239 | 0.73 | [+0.61, +0.84] | 0.59 | 0.69 |
| **Nowhere** (1 - rotation total) | 239 | 0.27 | [+0.16, +0.39] | 0.41 | 0.31 |

**WR2 out** (targets, n=196):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| WR1 | 196 | 0.16 | [+0.08, +0.24] | 0.06 | 0.12 |
| WR3 | 169 | 0.14 | [+0.08, +0.20] | 0.08 | 0.13 |
| WR4+ (each) | 312 | 0.15 | [+0.11, +0.19] | 0.07 | 0.15 |
| TE2+ (each) | 196 | 0.04 | [+0.01, +0.08] | 0.01 | 0.04 |
| RB1 | 194 | 0.08 | [+0.03, +0.13] | 0.04 | 0.07 |
| All WRs | 196 | 0.56 | [+0.44, +0.67] | 0.50 | 0.51 |
| All TEs | 193 | 0.07 | [+0.00, +0.14] | 0.00 | 0.06 |
| Outside prior rotation | 196 | 0.16 | [+0.10, +0.22] | -0.02 | 0.15 |
| Team volume | 196 | 0.01 | [-0.16, +0.18] | -0.03 | -0.08 |
| Rotation total | 196 | 0.72 | [+0.56, +0.88] | 0.71 | 0.65 |
| **Nowhere** | 196 | 0.28 | [+0.12, +0.44] | 0.29 | 0.35 |

**TE1 out** (targets, n=111):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| WR1 | 109 | 0.22 | [+0.12, +0.32] | 0.17 | 0.20 |
| WR2 | 102 | 0.16 | [+0.06, +0.26] | 0.08 | 0.14 |
| WR3 | 96 | 0.08 | [+0.00, +0.15] | -0.03 | 0.07 |
| TE2+ (each) | 155 | 0.15 | [+0.10, +0.21] | 0.12 | 0.15 |
| RB3+ | 121 | 0.04 | [+0.00, +0.08] | -0.01 | 0.04 |
| All WRs | 111 | 0.47 | [+0.32, +0.62] | 0.31 | 0.43 |
| All TEs | 100 | 0.29 | [+0.21, +0.37] | 0.20 | 0.29 |
| All RBs | 111 | 0.14 | [+0.06, +0.22] | 0.09 | 0.12 |
| Outside prior rotation | 111 | 0.13 | [+0.03, +0.24] | -0.12 | 0.10 |
| Team volume | 111 | 0.16 | [-0.07, +0.40] | 0.05 | 0.06 |
| Rotation total | 111 | 0.86 | [+0.66, +1.05] | 0.81 | 0.80 |
| **Nowhere** | 111 | 0.14 | [-0.05, +0.34] | 0.19 | 0.20 |

**RB1 out** (carries, n=352; targets in the next table, n=39 so thin):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| RB2 | 320 | 0.47 | [+0.42, +0.52] | 0.45 | 0.46 |
| RB3+ (each) | 444 | 0.22 | [+0.18, +0.25] | 0.06 | 0.20 |
| QB1 | 316 | 0.01 | [-0.01, +0.03] | -0.00 | 0.00 |
| All RBs | 350 | 0.79 | [+0.73, +0.85] | 0.76 | 0.75 |
| Outside prior rotation | 352 | 0.22 | [+0.18, +0.26] | 0.01 | 0.21 |
| Team carries more / (fewer) | 352 | 0.06 | [-0.00, +0.12] | -0.00 | 0.00 |
| Rotation total | 352 | 0.81 | [+0.74, +0.87] | 0.76 | 0.76 |
| **Nowhere** | 352 | 0.19 | [+0.13, +0.26] | 0.24 | 0.24 |

**RB2 out** (carries, n=102):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| RB1 | 101 | 0.52 | [+0.40, +0.63] | 0.49 | 0.48 |
| RB3+ (each) | 147 | 0.20 | [+0.13, +0.26] | 0.04 | 0.18 |
| All RBs | 102 | 0.80 | [+0.66, +0.94] | 0.85 | 0.75 |
| Outside prior rotation | 102 | 0.21 | [+0.11, +0.31] | -0.08 | 0.19 |
| Rotation total | 102 | 0.86 | [+0.71, +1.00] | 0.89 | 0.80 |
| **Nowhere** | 102 | 0.14 | [-0.00, +0.29] | 0.11 | 0.20 |

**RB1 out, targets** (n=39: directional only):

| Item | n | Mean | 90% CI of mean | Median | Pooled |
|---|---|---|---|---|---|
| RB2 | 33 | 0.24 | [+0.13, +0.34] | 0.20 | 0.23 |
| TE1 | 34 | 0.21 | [+0.08, +0.33] | 0.10 | 0.18 |
| All RBs | 38 | 0.46 | [+0.35, +0.58] | 0.47 | 0.44 |
| All WRs | 39 | 0.46 | [+0.24, +0.69] | 0.43 | 0.41 |
| All TEs | 38 | 0.18 | [+0.04, +0.32] | 0.05 | 0.14 |
| Outside prior rotation | 39 | 0.03 | [-0.05, +0.11] | -0.02 | 0.03 |
| **Nowhere** | 39 | -0.09 | [-0.39, +0.21] | -0.12 | 0.02 |

What it says:

- **A WR's targets are absorbed by whoever plays, not by "the next man up".** With WR1 out, WR2 gains 0.13 of the
  vacated targets [0.08, 0.18], WR3 0.15, and each WR4+ 0.17. All WRs together gain 0.65; TEs 0.07; RBs nothing.
- **About 0.27 of it (median 0.41) goes nowhere**: 0.12 to players outside the prior rotation (call-ups, returners)
  and a net team-volume drop of 0.18 targets per vacated target. A WR2 out looks the same (nowhere 0.28).
- **TE out is the opposite of the hand-set rule.** The WRs gain 0.47 [0.32, 0.62] (WR1 0.22, WR2 0.16), the
  other TEs only 0.29 in all (TE2+ 0.15 each). The rule gives the next TE 0.60, the other TEs 0.30 and the WRs 0.10.
- **RB1 out**: RB2 takes 0.47 of the carries [0.42, 0.52], RB3+ 0.22 each (all RBs 0.79), 0.19 nowhere [0.13, 0.26].
  Carries behave much more like the hand-set picture than targets do, only with 0.47 where the rule says 0.60
  (and 0.79 to all RBs where it says 1.00).
- The picture differs little by reason (injury-listed vs everything else: WR "nowhere" 0.27 vs 0.36, RB carries
  0.18 vs 0.19). The exception is TE (0.09 vs 0.30, on 78 vs 36 events). See `by_reason` in
  `redistribution_constants.json`.

### Result: is it stable? (2014-21 vs 2022-25)

| Channel | Absent | Item | n fit | n test | 2014-21 | 2022-25 | z of diff |
|---|---|---|---|---|---|---|---|
| carries | RB | GRP_RB | 331 | 122 | 0.80 | 0.78 | -0.3 |
| carries | RB | GRP_TE | 328 | 122 | 0.00 | -0.01 | -1.5 |
| carries | RB | GRP_WR | 332 | 123 | 0.02 | 0.01 | -1.0 |
| carries | RB | NEW | 332 | 123 | 0.19 | 0.27 | +1.3 |
| carries | RB | NOWHERE | 332 | 123 | 0.18 | 0.20 | +0.3 |
| carries | RB | ROTATION | 332 | 123 | 0.82 | 0.80 | -0.3 |
| carries | RB | TEAM_VOLUME | 332 | 123 | 0.08 | 0.10 | +0.2 |
| targets | TE | GRP_RB | 66 | 48 | 0.13 | 0.12 | -0.1 |
| targets | TE | GRP_TE | 63 | 40 | 0.35 | 0.17 | -1.9 |
| targets | TE | GRP_WR | 66 | 48 | 0.49 | 0.45 | -0.2 |
| targets | TE | NEW | 66 | 48 | 0.15 | 0.10 | -0.4 |
| targets | TE | NOWHERE | 66 | 48 | 0.05 | 0.31 | +1.1 |
| targets | TE | ROTATION | 66 | 48 | 0.95 | 0.69 | -1.1 |
| targets | TE | TEAM_VOLUME | 66 | 48 | 0.25 | 0.00 | -0.9 |
| targets | WR | GRP_RB | 286 | 188 | 0.10 | -0.04 | -2.8 |
| targets | WR | GRP_TE | 283 | 187 | 0.12 | 0.03 | -1.8 |
| targets | WR | GRP_WR | 286 | 188 | 0.63 | 0.51 | -1.5 |
| targets | WR | NEW | 286 | 188 | 0.14 | 0.16 | +0.7 |
| targets | WR | NOWHERE | 286 | 188 | 0.16 | 0.50 | +3.0 |
| targets | WR | ROTATION | 286 | 188 | 0.84 | 0.50 | -3.0 |
| targets | WR | TEAM_VOLUME | 286 | 188 | 0.01 | -0.26 | -2.3 |

The RB-carry picture is stable. **The WR-target one is not**: rotation capture fell from 0.84 to 0.50 and
"nowhere" rose from 0.16 to 0.50 (z = 3.0; the team's target volume drops by 2.0 below baseline in WR-absence
games in 2022-25, against 0.55 before). Control-game churn is essentially the same in both periods, so this is not
an artefact of the subtraction. Per-season pooled capture ranges 0.23-1.02 on 26-51 events a season, so some
of the gap is noise, but treat the WR "nowhere" constant as **about 0.3, rising to 0.5 on recent data**. A learned
constant is only as good as this.

### Result: the out-of-sample prediction test

Each rotation teammate who played in an absence game (2022-2025 events only), predicted three ways and compared
on his actual targets or carries: **(a)** his unchanged prior share of the team's baseline volume; **(b)** the
hand-set rule; **(c)** the learned table, fit on 2014-2021 only (net fractions, with the control-game churn as a
separate term, falling back to position-level and then pooled cells when a cell has fewer than 8 events).
Because (c) also corrects the churn that (a) ignores, two comparators isolate what the absence itself adds:
**(a2)** prior share + churn, and **(b3)** the hand rule + churn. **(d)** keeps the hand-set rule's *structure*
(one next man up + one spill group, no rest-of-position bucket) and refits its two constants on 2014-2021. MAE in targets / carries:

| Method | MAE, all rotation (n=2,267) | vs (a) | MAE, next man up (n=238) | vs (a) |
|---|---|---|---|---|
| (a) unchanged prior share | 1.600 | +0.000 | 2.413 | +0.000 |
| (a2) prior share + typical-game churn | 1.549 | +0.051 | 2.291 | +0.123 |
| **(b) hand-set rule** | 1.720 | -0.119 | 3.406 | -0.993 |
| (b2) hand rule, best-remaining reading | 1.741 | -0.140 | 3.155 | -0.742 |
| (b3) hand rule + churn | 1.739 | -0.139 | 3.680 | -1.267 |
| **(c) learned table** | 1.598 | +0.002 | 2.286 | +0.128 |
| (c2) learned, raw fractions | 1.599 | +0.002 | 2.291 | +0.123 |
| (d) hand-rule structure, 2 constants refit | 1.565 | +0.035 | 2.297 | +0.116 |

| Method | MAE, all rotation (n=1,165) | vs (a) | MAE, next man up (n=122) | vs (a) |
|---|---|---|---|---|
| (a) unchanged prior share | 1.503 | +0.000 | 6.413 | +0.000 |
| (a2) prior share + typical-game churn | 1.430 | +0.073 | 6.029 | +0.384 |
| **(b) hand-set rule** | 1.262 | +0.242 | 4.732 | +1.680 |
| (b2) hand rule, best-remaining reading | 1.258 | +0.246 | 4.684 | +1.729 |
| (b3) hand rule + churn | 1.326 | +0.177 | 5.037 | +1.376 |
| **(c) learned table** | 1.261 | +0.242 | 4.350 | +2.062 |
| (c2) learned, raw fractions | 1.257 | +0.246 | 4.346 | +2.066 |
| (d) hand-rule structure, 2 constants refit | 1.265 | +0.238 | 4.453 | +1.959 |

| Method | MAE, other pass-catching group (n=574) | vs (a) |
|---|---|---|
| (a) unchanged prior share | 1.749 | +0.000 |
| (a2) prior share + typical-game churn | 1.720 | +0.028 |
| **(b) hand-set rule** | 1.810 | -0.062 |
| (b2) hand rule, best-remaining reading | 1.810 | -0.062 |
| (b3) hand rule + churn | 1.791 | -0.043 |
| **(c) learned table** | 1.780 | -0.031 |
| (c2) learned, raw fractions | 1.782 | -0.034 |
| (d) hand-rule structure, 2 constants refit | 1.780 | -0.032 |

(a2) is the fair "do nothing" baseline. Out of sample:

- **Targets: no redistribution model beats prior-share-plus-churn.** The learned table (1.598) is no better than
  (a2) (1.549) and gains nothing over (a); the two-constant refit (d, 1.565) is the best absence-aware method but
  still not better than (a2). Whatever the fit-period table learned does not carry into 2022-25 (the stability
  drift above) and is small next to the noise in a single game's target count.
- **The hand-set rule is worse than not redistributing at all**: it overshoots the named next man up by about one
  target per player (MAE 3.41 vs 2.41), and is slightly worse for the other pass-catching group too (1.81 vs 1.75).
- **Carries: absence information is worth having.** The rule helps (1.50 to 1.26) and ties the learned table
  (1.26) over all rotation players; for the next man up the learned table is better by 0.38 carries
  (4.35 vs 4.73; 6.41 unchanged).

**The hand-set rule** is implemented as the local Edge Finder round codes it (as relayed by the planner; the code is
not in this repository). The next man up gets 0.60 of the vacated volume `V` -- the next-ranked player *behind* the
absent one at the same position. The other 0.40 splits 0.75 to the rest of the same position (0.30 of `V`) and 0.25
to the other pass-catching group (0.10 of `V`: TEs for a WR out, WRs for a TE out; targets only; there is no such
group for a RB), each pro rata to prior share. **Nothing is left unassigned**: inside the 0.40 an empty bucket hands
its weight to the other one (both empty: to the next man up); with no next man up the 0.60 goes to whatever buckets
remain. That last part is my reading of "an empty bucket hands its weight to the others" and only matters at the
edges. Reading "next player" as the best-ranked remaining player (b2) changes little (targets 1.74, carries 1.26).
`r1.rule_allocation` takes the constants, so the comparison can be rerun if the local rule changes.

### Recommendation

- Use **measured fractions** from `redistribution.csv` (net columns, by absent label x beneficiary label) rather
  than a 60% rule. Where one number per absent position is wanted: `refit_rule_constants_fit_seasons` in
  `redistribution_constants.json`: WR out: next-up 0.14, TE spill 0.10; TE out: next-up 0.19, WR spill 0.49;
  RB out: next-up 0.40 (carries) / 0.25 (targets).
- **For targets, prefer not to redistribute at all** unless the team can accept a small gain: against churn-aware
  prior shares the best constants buy nothing measurable (and the 2014-21 numbers do not survive to 2022-25).
- Budget a **leak**: 0.27 of a WR's vacated volume (0.5 recently), 0.19 of a RB's carries, 0.14 of a TE's.

---

## R2. What makes a matchup good, and how much is it worth?

### Question

Beyond UM (which already has implied total and a basic defence-vs-position term), do matchup components predict
the residual, and by how much?

### Method

Components, all from prior games, for each lookback **l4**, **l8**, or a **season-to-date + last-season blend**
(`(sum + 6 x last-season mean) / (games + 6)`):

- the opposing defence's DK points allowed to the position, **raw** and **schedule-adjusted** (each game's
  points minus the offence's own prior 8-game mean at the position);
- the opposing defence's EPA allowed per pass and per rush (play-by-play);
- this team's implied total; own and opponent pace (plays per game); own PROE;
- DST: the opposing offence's sacks allowed, giveaways, DST points conceded (raw / schedule-adjusted),
  EPA per pass / rush, pace and implied total.

Targets: `actual - UM`, and `actual - trailing-8` (the whole context effect, since trailing-8 knows no context).
Per position a ridge regression on standardized components; **folds are by season** (leave one season out
within 2014-2021; a test shows a random-row split would have flattered it); alpha, lookback and schedule
adjustment are chosen on that CV, refit on 2014-2021, and scored once on 2022-2025. Weights are in **points per
1 SD**. The gain is measured against UM plus the fit-period mean bias (the conservative baseline: it isolates
what the components add). "Context, not edge" means a holdout MAE gain under 0.2.

### Result

| Pos | Target | n fit / test | Lookback / sched. | Holdout MAE of baseline | MAE gain (bias-corrected) | 90% CI | R² gain | CV gain (fit) |
|---|---|---|---|---|---|---|---|---|
| QB | vs UM | 4,322 / 2,367 | l4 / adj | 6.718 | +0.039 | [+0.006, +0.072] | 1.28% | +0.051 |
| QB | vs trailing-8 | 4,322 / 2,367 | l4 / adj | 6.851 | +0.020 | [-0.007, +0.047] | 0.80% | +0.034 |
| RB | vs UM | 8,223 / 4,057 | l4 / adj | 5.763 | +0.015 | [-0.003, +0.034] | 1.02% | +0.025 |
| RB | vs trailing-8 | 8,223 / 4,057 | blend / adj | 5.864 | +0.007 | [-0.008, +0.022] | 0.72% | +0.019 |
| WR | vs UM | 12,560 / 6,425 | blend / adj | 5.806 | +0.015 | [+0.008, +0.021] | 0.16% | +0.005 |
| WR | vs trailing-8 | 12,560 / 6,425 | blend / adj | 5.914 | -0.003 | [-0.008, +0.003] | 0.10% | +0.008 |
| TE | vs UM | 5,029 / 2,575 | l4 / raw | 4.783 | +0.003 | [-0.013, +0.020] | 0.46% | +0.015 |
| TE | vs trailing-8 | 5,029 / 2,575 | blend / adj | 4.852 | +0.014 | [-0.000, +0.027] | 0.55% | +0.008 |
| DST | vs UM | 4,128 / 2,174 | l8 / raw | 4.541 | +0.001 | [+0.001, +0.001] | 0.01% | -0.000 |
| DST | vs trailing-8 | 4,096 / 2,174 | blend / adj | 4.636 | +0.094 | [+0.059, +0.129] | 4.59% | +0.089 |

**Every gain is below 0.04 points of MAE, against a bar of 0.2: matchups are context, not edge.** The R² gain is
1.3% at most (QB). Against the unconditional trailing-8, the *whole* context effect for offensive positions is
worth 0.02 points or less (DST 0.09: the opponent's offence matters for a DST, but UM's DST model already
uses it, so the gain over UM is 0.001).

The individual components are real but small (points per 1 SD, shown alone and in the ridge):

| Pos | Component | Ridge weight (pts per 1 SD) | Alone, fit | Alone, test |
|---|---|---|---|---|
| QB | def_dk | +0.34 | +0.64 | +0.83 (+4.6 SE) |
| QB | def_epa_pass | +0.19 | +0.58 | +0.41 (+2.3 SE) |
| QB | def_epa_rush | +0.11 | +0.26 | -0.04 (-0.2 SE) |
| QB | implied | +0.40 | +0.43 | +0.06 (+0.3 SE) |
| QB | team_pace | -0.57 | -0.70 | -0.73 (-4.2 SE) |
| QB | opp_pace | +0.05 | +0.03 | +0.17 (+1.0 SE) |
| QB | team_proe | -0.48 | -0.59 | -0.42 (-2.6 SE) |
| RB | def_dk | +0.32 | +0.44 | +0.44 (+3.6 SE) |
| RB | def_epa_pass | +0.10 | +0.32 | +0.42 (+3.3 SE) |
| RB | def_epa_rush | -0.05 | +0.17 | +0.28 (+2.5 SE) |
| RB | implied | +0.38 | +0.46 | +0.40 (+3.3 SE) |
| RB | team_pace | -0.47 | -0.44 | -0.58 (-4.8 SE) |
| RB | opp_pace | -0.04 | -0.12 | -0.24 (-2.0 SE) |
| RB | team_proe | +0.13 | +0.16 | -0.12 (-1.1 SE) |
| WR | def_dk | +0.11 | +0.16 | +0.22 (+2.0 SE) |
| WR | def_epa_pass | -0.01 | +0.07 | -0.25 (-2.2 SE) |
| WR | def_epa_rush | -0.15 | -0.12 | -0.20 (-1.8 SE) |
| WR | implied | +0.23 | +0.19 | +0.06 (+0.7 SE) |
| WR | team_pace | -0.21 | -0.17 | -0.09 (-0.8 SE) |
| WR | opp_pace | -0.02 | -0.01 | +0.30 (+2.6 SE) |
| WR | team_proe | +0.05 | +0.07 | +0.24 (+2.8 SE) |
| TE | def_dk | +0.23 | +0.24 | +0.18 (+1.4 SE) |
| TE | def_epa_pass | -0.03 | +0.13 | +0.51 (+3.9 SE) |
| TE | def_epa_rush | -0.08 | -0.01 | +0.08 (+0.7 SE) |
| TE | implied | +0.38 | +0.29 | +0.26 (+2.0 SE) |
| TE | team_pace | -0.22 | -0.19 | -0.27 (-2.2 SE) |
| TE | opp_pace | -0.04 | -0.04 | +0.02 (+0.2 SE) |
| TE | team_proe | -0.27 | -0.22 | -0.18 (-1.5 SE) |

| Pos | Component (vs trailing-8) | Ridge weight (pts per 1 SD) | Alone, fit | Alone, test |
|---|---|---|---|---|
| DST | opp_dst_conceded | +0.28 | +1.01 | +1.46 |
| DST | opp_sacks_allowed | +0.11 | +0.66 | +1.22 |
| DST | opp_giveaways | +0.22 | +0.74 | +0.69 |
| DST | opp_epa_pass | -0.28 | -1.00 | -1.14 |
| DST | opp_epa_rush | -0.19 | -0.65 | -0.89 |
| DST | opp_pace | -0.15 | -0.37 | -0.25 |
| DST | opp_implied | -0.31 | -0.91 | -1.14 |

- **Defence points allowed** is the most consistent positive component (QB +0.64 / +0.83 per SD in fit / test,
  RB +0.44 / +0.44, WR +0.16 / +0.22, TE +0.24 / +0.18), with defence EPA allowed to the pass pointing the same way
  for QB and RB.
- **Team pace is negative in both periods** (QB -0.70 / -0.73, RB -0.44 / -0.58): faster-tempo teams' players
  underperform UM. Not obviously causal (pace tracks trailing game scripts), but it is stable. Implied total, which UM
  already has, adds little on top (QB +0.43 fit, +0.06 test).
- A ridge on correlated inputs cannot split credit cleanly; read the "alone" columns for direction.

**Lookback and schedule adjustment hardly matter** (the six variants differ by at most 0.025 MAE in CV):

| Pos | CV MAE raw | CV MAE adjusted | Adjusted better in CV? | Holdout gain raw | Holdout gain adjusted |
|---|---|---|---|---|---|
| QB | 6.7785 | 6.7732 | True | +0.034 | +0.039 |
| RB | 5.9212 | 5.9185 | True | +0.031 | +0.015 |
| WR | 5.9564 | 5.9544 | True | +0.014 | +0.015 |
| TE | 4.8630 | 4.8636 | False | +0.003 | +0.020 |
| DST | 4.7324 | 4.7325 | False | +0.001 | +0.001 |

The best lookback is **l4 for QB, RB and TE, the season+prior blend for WR, l8 for DST**; schedule adjustment is
better in CV for QB, RB and WR by 0.002-0.005 MAE, which is not a reason to prefer it. Raw points allowed
over the last 4-8 games is as good as anything.

### Recommendation

Do not weight matchup factors into the projection. If they are shown, show them as context. If a number is wanted
anyway, the ridge weights in `matchup_weights.json` are the measured ones (the two components with the same sign in both
periods at all four offensive positions are the defence's points allowed, positive, and the team's own pace,
negative), but expect at most a few hundredths of a point of MAE.

---

## R3. Do the context signals carry information beyond the projection?

### Question and method

Each signal is a flag from prior games; the test is whether flagged players beat or miss UM relative to unflagged
players of the same position (mean residual difference, hit rate, n per season, fit vs test). Thresholds are
**chosen on 2014-2021 only** (largest t in the expected direction, with at least 150 flagged fit rows) and then
reported untouched on 2022-2025.

- **BUY↑**: last-3 DK/G at least X points (2, 3, 4, 5) or p% (15-35%) *below* last-3 xFP/G, with xFP/G in the top
  half of the position that week.
- **FADE↓**: the mirror (last-3 DK/G *above* xFP/G by the same sweep, same top-half condition), plus a TD excess over
  the last 3 games of at least Y touchdowns (1, 1.5, 2, 2.5; **actual TDs minus ffopportunity's expected TDs,
  counted in touchdowns, not points**). The mirror alone is reported too.
- **USAGE↑/↓**: the last 2 games against the 6 before them, for target share (WR/TE/RB), carry share (RB) and
  red-zone share (WR/TE/RB, from play-by-play). Each metric is judged among the positions it applies to.
- **Salary lag cannot be tested**: there is no historical salary in any public dataset. Judge it on Sam's own
  weekly sheets once enough weeks exist.

### Result

| Signal | Chosen on 2014-21 | n fit | Effect fit | n test | Effect test [90% CI] | Hit rate fit (flag vs not) | Hit rate test | Thresholds with right sign in test | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| BUY↑ | pct=0.35 | 813 | -0.01 | 436 | -0.57 [-1.20, +0.07] | 41.0% vs 41.2% | 38.5% vs 40.4% | 0/9 | **drop** |
| FADE↓ (with TD excess) | x=2.0, td_excess=1.0 | 2,120 | -0.44 | 1,117 | -0.15 [-0.61, +0.31] | 58.2% vs 58.8% | 57.1% vs 59.9% | 34/36 | **drop** |
| FADE↓ (gap only) | x=2.0 | 4,816 | -0.54 | 2,545 | +0.18 [-0.13, +0.50] | 58.8% vs 58.8% | 56.7% vs 60.3% | 0/9 | **drop** |
| USAGE↑ target_share | threshold=0.05 | 4,758 | +0.02 | 2,588 | -0.19 [-0.48, +0.09] | 40.7% vs 40.2% | 38.9% vs 39.6% | 0/4 | **drop** |
| USAGE↑ carry_share | threshold=0.1 | 1,756 | +1.06 | 865 | +0.56 [+0.04, +1.09] | 44.6% vs 38.8% | 42.3% vs 38.2% | 4/4 | **keep** |
| USAGE↑ rz_share | threshold=0.2 | 1,316 | -0.05 | 725 | +0.09 [-0.40, +0.58] | 40.3% vs 40.3% | 41.4% vs 39.3% | 2/4 | **drop** |
| USAGE↓ target_share | threshold=0.05 | 3,520 | -0.09 | 2,123 | -0.07 [-0.35, +0.21] | 59.7% vs 59.7% | 61.0% vs 60.5% | 4/4 | **drop** |
| USAGE↓ carry_share | threshold=0.05 | 2,300 | -0.88 | 1,226 | -0.70 [-1.16, -0.25] | 63.0% vs 58.8% | 62.0% vs 60.4% | 4/4 | **keep** |
| USAGE↓ rz_share | threshold=0.15 | 1,705 | -0.46 | 895 | +0.36 [-0.10, +0.83] | 62.1% vs 59.5% | 58.0% vs 60.8% | 1/4 | **drop** |

Hit rate = share of flagged player-games that went the signal's way (beat UM for ↑, miss it for ↓), against the
unflagged rate. n per season: BUY↑ flags 83-130 player-games a season (2014: 114, 2015: 86, 2016: 105, 2017: 97, 2018: 99, 2019: 83, 2020: 102, 2021: 127, 2022: 105, 2023: 130, 2024: 102, 2025: 99); the carry-share-up signal
113-270 (2014: 113, 2015: 220, 2016: 251, 2017: 227, 2018: 225, 2019: 203, 2020: 247, 2021: 270, 2022: 192, 2023: 241, 2024: 215, 2025: 217).

**BUY↑: drop.** No threshold works even in the fit seasons (the best, 35%, is -0.01), and in 2022-25 every one of
nine thresholds goes the *wrong* way. UM already leans on last-3 xFP (half the weight of the blend used for QB, RB and TE; an input to the WR GBM), so
the gap between DK and xFP is already priced in.

**FADE↓: drop as built, but it is a TE signal.** Pooled, the gap-only mirror looks real in the fit seasons (-0.54,
t = -3.7) and reverses in the test seasons (+0.18). Adding the TD-excess condition keeps the sign in 34 of 36
thresholds in the test seasons, but at -0.15 [-0.61, +0.31] it is noise. By position (the effect at the threshold
chosen on the pooled fit seasons):

| Signal | Pos | n fit | Effect fit | n test | Effect test [90% CI] | Verdict |
|---|---|---|---|---|---|---|
| BUY_up | QB | 51 | -0.04 | 27 | -2.61 [-4.83, -0.40] | drop |
| BUY_up | RB | 139 | -1.15 | 94 | +0.53 [-0.81, +1.87] | drop |
| BUY_up | WR | 432 | +0.12 | 233 | -0.90 [-1.83, +0.04] | drop |
| BUY_up | TE | 191 | +0.57 | 82 | -0.22 [-1.36, +0.92] | drop |
| FADE_down | QB | 443 | -0.73 | 230 | +0.00 [-1.00, +1.01] | drop |
| FADE_down | RB | 633 | -0.58 | 342 | -0.35 [-1.15, +0.45] | drop |
| FADE_down | WR | 807 | +0.10 | 403 | +0.56 [-0.27, +1.38] | drop |
| FADE_down | TE | 237 | -1.35 | 142 | -1.89 [-2.71, -1.08] | keep |
| FADE_down_gap_only | QB | 697 | -1.12 | 341 | -0.37 [-1.12, +0.38] | drop |
| FADE_down_gap_only | RB | 1,296 | -1.00 | 702 | +0.29 [-0.20, +0.77] | drop |
| FADE_down_gap_only | WR | 2,167 | +0.11 | 1,134 | +0.86 [+0.35, +1.37] | drop |
| FADE_down_gap_only | TE | 656 | -1.13 | 368 | -1.49 [-2.10, -0.87] | keep |
| USAGE_up carry_share | RB | 1,756 | +1.06 | 865 | +0.56 [+0.04, +1.09] | keep |
| USAGE_down carry_share | RB | 2,300 | -0.88 | 1,226 | -0.70 [-1.16, -0.25] | keep |

**TE** is consistent in both periods (gap-only: -1.13 fit, -1.49 test; with TD excess -1.35 / -1.89); **WR goes the
other way** (+0.86 in test: a WR who outproduced his xFP keeps outproducing UM). A position-level result found
after pooling deserves caution (four positions, several signal variants), but the TE one holds in two independent
periods. The TD-excess condition adds nothing measurable over the gap alone.

**USAGE: keep only the RB carry-share jumps.** Up (>= +10 points of team carry share): +1.06 fit, +0.56 test
[+0.04, +1.09], a small but consistent edge (hit rate 42.3% vs 38.2%). Down (>= -5 points): -0.88 fit, -0.70 test
[-1.16, -0.25]. Target-share and red-zone jumps do nothing out of sample (up, down, or either metric: wrong
sign or intervals through zero).

BUY↑ sweep, every threshold (the one chosen on 2014-21 is `pct=0.35`, the last row):

| BUY↑ threshold | n fit | Effect fit | n test | Effect test |
|---|---|---|---|---|
| x=2.0 | 3,792 | -0.26 | 1,936 | -0.74 |
| x=3.0 | 2,422 | -0.39 | 1,248 | -0.62 |
| x=4.0 | 1,406 | -0.67 | 726 | -0.63 |
| x=5.0 | 750 | -0.56 | 389 | -0.64 |
| pct=0.15 | 3,680 | -0.14 | 1,884 | -0.54 |
| pct=0.2 | 2,716 | -0.05 | 1,408 | -0.35 |
| pct=0.25 | 1,921 | -0.13 | 982 | -0.36 |
| pct=0.3 | 1,310 | -0.09 | 669 | -0.56 |
| pct=0.35 | 813 | -0.01 | 436 | -0.57 |

### Recommendation

Drop BUY↑. Replace FADE↓ with a **TE-only fade**: last-3 DK/G at least 2 points above xFP/G, top half of xFP (TD
excess optional). Replace USAGE with **RB carry-share jumps only**: up at +10 points, down at -5 points. Say in the
UI that these are worth about 0.6-1.9 points, not the 3-5 that a flag implies.

---

## R4. Weather, and the wind flag

### Question and method

Outdoor games only (`roof` outdoors or open). Wind (mph) and temperature are related to pass-game DK points
against UM (QB, WR, TE) and to total points against the closing total, by wind bin 0-9 / 10-14 / 15-19 / 20-24 /
25+, and by every `wind >= t` cut. Precipitation comes from nflfastR's weather text (indicative only).

**Data quality.** `games.parquet` has no wind for 91 of 187 outdoor games in 2022 and 38 of 191 in 2023. Those are
filled from the wind in play-by-play's weather text (11 games in 2021, a handful elsewhere). Where both exist the
two agree exactly in 86% of 1,984 games and within 2 mph in 93% (mean absolute difference 0.47 mph). Games with
neither reading are dropped (2-3 a season at worst). 23,885 QB/WR/TE player-games over 2,251 games are used.

### Result: effect by wind bin (residual `actual - UM`, points, demeaned within position and season)

| Wind (mph) | QB, all seasons [90% CI] | WR | TE | QB fit | QB test |
|---|---|---|---|---|---|
| 0-9 | +0.08 [-0.19, +0.35] (n=3,217) | -0.07 [-0.21, +0.06] (n=9,177) | +0.16 [-0.02, +0.34] (n=3,707) | +0.13 (n=2129) | -0.02 (n=1088) |
| 10-14 | -0.80 [-1.25, -0.36] (n=1,049) | -0.39 [-0.63, -0.16] (n=2,975) | -0.12 [-0.43, +0.18] (n=1,185) | -0.83 (n=682) | -0.75 (n=367) |
| 15-19 | -1.13 [-1.94, -0.32] (n=409) | -0.55 [-0.95, -0.15] (n=1,151) | -0.58 [-1.06, -0.11] (n=438) | -0.87 (n=289) | -1.76 (n=120) |
| 20-24 | -0.28 [-1.68, +1.12] (n=94) | -0.19 [-0.97, +0.60] (n=256) | -1.60 [-2.56, -0.64] (n=103) | -0.13 (n=72) | -0.78 (n=22) |
| 25+ | -5.52 [-7.59, -3.44] (n=25) | -1.94 [-3.76, -0.11] (n=68) | -2.32 [-4.15, -0.48] (n=31) | -6.09 (n=20) | -3.25 (n=5) |

- The residual **turns negative already at 10-14 mph** (QB -0.80, WR -0.39) and is about -1.1 at 15-19 (QB), with
  the same sign in both periods (QB fit -0.83 / test -0.75 at 10-14; -0.87 / -1.76 at 15-19). The 20-24 bin is *not*
  worse than 15-19 (-0.28, n=94 QB rows over 42 games: too thin to tell). 25+ is -5.5 but on 25 QB rows from 11 games.
- Pass rate and PROE fall steadily with wind (PROE -0.5 points at 0-9, -1.9 at 15-19, -3.3 at 20-24).

Cumulative flags `wind >= t` are better powered than bins (QB):

| Flag | QB rows flagged | All seasons | Fit 2014-21 | Test 2022-25 | n fit / test |
|---|---|---|---|---|---|
| ≥ 10 | 1,577 | -1.01 [-1.47, -0.55] | -1.03 [-1.59, -0.46] | -0.99 [-1.77, -0.21] | 1063 / 514 |
| ≥ 12 | 1,069 | -1.08 [-1.59, -0.56] | -0.96 [-1.61, -0.32] | -1.32 [-2.17, -0.47] | 726 / 343 |
| ≥ 15 | 528 | -1.05 [-1.77, -0.33] | -0.90 [-1.77, -0.04] | -1.46 [-2.76, -0.15] | 381 / 147 |
| ≥ 17 | 275 | -1.09 [-2.07, -0.10] | -1.22 [-2.32, -0.12] | -0.74 [-2.90, +1.43] | 207 / 68 |
| ≥ 20 | 119 | -1.16 [-2.43, +0.11] | -1.25 [-2.61, +0.11] | -0.92 [-4.09, +2.25] | 92 / 27 |
| ≥ 22 | 65 | -2.06 [-3.60, -0.53] | -2.16 [-3.82, -0.51] | -1.57 [-5.46, +2.32] | 58 / 7 |
| ≥ 25 | 25 | -5.29 [-7.28, -3.30] | -5.91 [-7.84, -3.99] | -2.93 [-7.93, +2.08] | 20 / 5 |

The effect on QBs is about **-1.0 point from 10 mph and flat out to 20** (and grows only in the unreliable 22+ tail).
The 20 mph flag fires on 119 QB-games (2.5% of outdoor QB games) and its interval includes zero in both periods;
15 mph flags 528 (11%), at -1.05 [-1.77, -0.33], and the effect holds in both periods (-0.90 / -1.46). 10 mph
flags 33% of outdoor QB games, so is not selective, even though it is the lowest cut that qualifies.

**Rule applied.** "About -1 or more (>= 0.9) for QBs with an interval that excludes 0, in both periods": the
qualifying flags are 10, 12 and 15 mph. `effect_begins_mph` = 10; the recommended selective flag is **15 mph** (the
lowest qualifying cut that fires on at most 15% of outdoor QB games). Judged bin by bin (first bin at -1.0 with an
interval below 0) the answer is 15-19 in all seasons.

**Wind, rain and cold fitted together** (they travel together; OLS, game-clustered; points):

| Term (points) | QB [90% CI] | WR | TE | QB fit / test |
|---|---|---|---|---|
| Wind, per +5 mph | -0.48 [-0.69, -0.28] | -0.20 [-0.32, -0.08] | -0.24 [-0.40, -0.09] | -0.49 / -0.45 |
| Rain (vs none) | -1.74 [-2.67, -0.82] | -0.80 [-1.25, -0.34] | -0.74 [-1.24, -0.24] | -1.73 / -1.79 |
| Snow (vs none) | +0.00 [-2.01, +2.01] | -0.93 [-1.86, +0.01] | -0.94 [-2.39, +0.52] | -0.21 / +0.24 |
| Cold, per 10°F below 50°F | -0.52 [-0.81, -0.24] | -0.10 [-0.27, +0.07] | -0.03 [-0.23, +0.18] | -0.42 / -0.66 |

For QBs: **-0.48 per +5 mph of wind** (stable: -0.49 fit / -0.45 test), **-1.7 in rain**, **-0.5 per 10°F below 50°F**,
each independent of the others. WR and TE lose about 0.2 per +5 mph and 0.7-0.8 in rain.

| Temperature (°F) | QB | WR | TE |
|---|---|---|---|
| <32 | -1.72 [-2.56, -0.88] (n=227) | -0.63 [-1.16, -0.09] (n=599) | -0.07 [-0.74, +0.59] (n=253) |
| 32-49 | -0.68 [-1.12, -0.23] (n=1,152) | -0.25 [-0.48, -0.02] (n=3,218) | -0.04 [-0.34, +0.26] (n=1,282) |
| 50-69 | -0.11 [-0.45, +0.22] (n=1,938) | -0.21 [-0.38, -0.04] (n=5,515) | +0.14 [-0.09, +0.37] (n=2,238) |
| 70+ | +0.12 [-0.29, +0.53] (n=1,477) | -0.07 [-0.28, +0.14] (n=4,295) | -0.17 [-0.43, +0.09] (n=1,691) |

| Precipitation (text) | QB | WR | TE |
|---|---|---|---|
| none | -0.13 [-0.36, +0.09] (n=4,451) | -0.13 [-0.25, -0.02] (n=12,662) | +0.05 [-0.10, +0.20] (n=5,067) |
| rain | -1.89 [-2.79, -0.99] (n=289) | -0.94 [-1.39, -0.50] (n=823) | -0.72 [-1.20, -0.24] (n=334) |
| snow | -1.25 [-3.22, +0.72] (n=54) | -1.32 [-2.24, -0.41] (n=142) | -1.03 [-2.45, +0.40] (n=63) |

### Result: does the closing total already price wind in?

Not much. Per +5 mph of wind, outdoors:

| Per +5 mph of wind | All seasons | Fit | Test |
|---|---|---|---|
| Closing total | -0.34 [-0.50, -0.17] | -0.26 [-0.43, -0.08] | -0.58 [-0.90, -0.27] |
| Points scored | -1.38 [-1.87, -0.89] | -1.35 [-1.90, -0.80] | -1.50 [-2.55, -0.44] |
| Miss (scored - closing) | -1.04 [-1.50, -0.59] | -1.09 [-1.61, -0.57] | -0.91 [-1.83, +0.01] |
| Points scored, wind above 10 only | -1.52 [-2.33, -0.70] | -1.51 [-2.33, -0.68] | -1.72 [-4.04, +0.59] |
| Miss, wind above 10 only | -1.10 [-1.82, -0.38] | -1.21 [-1.97, -0.46] | -0.64 [-2.39, +1.11] |

The closing total falls **0.34 points** per +5 mph; the points actually scored fall **1.38**; the miss is **-1.04**
per +5 mph [-1.50, -0.59]. Vegas prices about a quarter of the effect. By bin and by flag:

| Wind | Games | Mean closing total | Mean points scored | Miss (scored - closing) [90% CI] | Pass rate | PROE (pts) |
|---|---|---|---|---|---|---|
| 0-9 | 1521 | 45.0 | 45.8 | +0.73 [+0.2, +1.3] | 61.9% | -0.48 |
| 10-14 | 496 | 44.4 | 42.8 | -1.59 [-2.5, -0.7] | 61.6% | -0.90 |
| 15-19 | 191 | 44.3 | 42.3 | -1.99 [-3.5, -0.5] | 60.9% | -1.90 |
| 20-24 | 42 | 43.3 | 45.8 | +2.50 [-0.7, +5.7] | 58.9% | -3.25 |
| 25+ | 11 | 43.4 | 34.5 | -8.86 [-15.0, -2.7] | 57.9% | -5.08 |

| Flag | games flagged | Points scored minus closing total, flagged vs rest [90% CI] | Fit | Test |
|---|---|---|---|---|
| ≥ 10 | 740 | -2.30 [-3.23, -1.37] | -2.53 | -1.79 |
| ≥ 12 | 498 | -2.24 [-3.30, -1.17] | -2.08 | -2.56 |
| ≥ 15 | 244 | -1.68 [-3.10, -0.27] | -1.62 | -1.75 |
| ≥ 17 | 124 | -0.30 [-2.30, +1.69] | -1.09 | +2.19 |
| ≥ 20 | 53 | +0.17 [-2.81, +3.15] | -1.11 | +4.75 |
| ≥ 22 | 29 | -1.39 [-5.73, +2.95] | -2.80 | +11.85 |
| ≥ 25 | 11 | -8.88 [-14.75, -3.02] | -11.75 | +4.39 |

Compared with calmer outdoor games, games at 10+ mph finish **2.3 points further below the closing total** [-3.2, -1.4],
in both periods (-2.5 fit, -1.8 test).
The effect fades in the 20+ games (53 of them: no detectable miss), so the money is in the **10-19 mph band**, not the
extreme.

### Recommendation

Move the flag from 20 to **15 mph**, call 10 mph "windy" if a softer tier is wanted, and consider a **continuous
adjustment instead of a cliff**: about -0.5 QB points (-0.2 WR / TE) per +5 mph, -1.7 QB (-0.8 WR / TE) in rain, -0.5
QB per 10°F under 50°F. Treat the closing total itself as too high in wind: about -1 point per +5 mph, which is
informative for game-environment signals. (Snow is too rare to quantify: 54 QB rows.)

---

## R5. Quick checks

All four were run; none was skipped (each took seconds). Residual `actual - UM` for the flagged player-games
against the rest, all positions pooled, expected sign fixed in advance:

| Check | Definition | n fit | Effect fit (pts) | n test | Effect test [90% CI] | Verdict |
|---|---|---|---|---|---|---|
| short_week | own rest of 5 days or fewer | 2,075 | -0.00 | 1,195 | +0.50 [+0.08, +0.92] | **drop** |
| divisional | division opponent | 12,711 | -0.17 | 6,158 | -0.19 [-0.40, +0.03] | **drop** |
| home | home team (neutral-site games excluded) | 16,806 | +0.37 | 8,606 | +0.39 [+0.18, +0.59] | **borderline** |
| back_to_back_road | second consecutive road game (neutral-site games break the chain) | 4,678 | -0.30 | 2,488 | -0.25 [-0.55, +0.05] | **drop** |

By position:

| Check | Pos | Fit | Test [90% CI] | n test | Verdict |
|---|---|---|---|---|---|
| short_week | QB | -0.12 | +1.13 [-0.30, +2.55] | 156 | drop |
| short_week | RB | +0.12 | +0.03 [-0.63, +0.69] | 277 | drop |
| short_week | WR | +0.24 | +0.52 [-0.19, +1.22] | 436 | drop |
| short_week | TE | -0.30 | +1.54 [+0.62, +2.46] | 176 | drop |
| short_week | DST | -0.46 | -0.56 [-1.19, +0.08] | 150 | drop |
| divisional | QB | -0.53 | -0.80 [-1.44, -0.15] | 832 | keep |
| divisional | RB | -0.18 | -0.25 [-0.65, +0.15] | 1,422 | drop |
| divisional | WR | -0.12 | -0.05 [-0.40, +0.30] | 2,237 | drop |
| divisional | TE | -0.09 | -0.11 [-0.55, +0.32] | 899 | drop |
| divisional | DST | +0.00 | +0.12 [-0.28, +0.51] | 768 | drop |
| home | QB | +0.99 | +1.14 [+0.63, +1.66] | 1,164 | keep |
| home | RB | +0.71 | +0.45 [+0.04, +0.85] | 1,981 | borderline |
| home | WR | +0.10 | +0.21 [-0.07, +0.50] | 3,145 | drop |
| home | TE | +0.34 | +0.16 [-0.25, +0.57] | 1,252 | drop |
| home | DST | -0.12 | +0.24 [-0.21, +0.68] | 1,064 | drop |
| back_to_back_road | QB | -0.74 | -0.58 [-1.36, +0.20] | 332 | drop |
| back_to_back_road | RB | -0.44 | +0.14 [-0.44, +0.71] | 571 | drop |
| back_to_back_road | WR | -0.31 | -0.47 [-0.90, -0.04] | 920 | borderline |
| back_to_back_road | TE | -0.06 | +0.11 [-0.48, +0.70] | 357 | drop |
| back_to_back_road | DST | +0.19 | -0.36 [-0.92, +0.21] | 308 | drop |

- **Home field: keep for QB.** QBs gain +0.99 in the fit seasons and +1.14 in the test seasons [+0.63, +1.66] over
  what UM predicts: UM's blend has no home term for QB, RB or TE. RBs +0.45 (borderline). WR, TE and DST: nothing.
- **Short week: drop.** The effect is *positive* in the test seasons (+0.50 pooled), the opposite of the expected sign.
- **Divisional: drop** overall (-0.19 [-0.40, +0.03]). The QB-only result (-0.80 test) passes the verdict, but it is one of
  20 position cells, so it is a lead, not a finding.
- **Back-to-back road: drop** (-0.25 [-0.55, +0.05]); WR -0.47 is borderline.

---

## Anything that contradicts the local round's design

- The **60% next-man-up rule (0.30 to the rest of the position, 0.10 spill, nothing unassigned)** is contradicted for targets and approximate for carries (R1).
- **Equal-weight matchup factors** have nothing to weight: all of them together are worth less than 0.04 points (R2).
- **BUY↑** does nothing; **FADE↓** is a TE signal; only **RB carry-share jumps** survive USAGE (R3).
- **A 20 mph wind flag** is too high and too rare; wind (and rain, and cold) is an under-priced total (R4).
- Home field is unmodelled for QBs (R5), and UM's QB / RB / TE projection is the blend, which has no home term.

## Caveats

- **UM is the baseline.** No historical TFFB exists, so "beyond UM" is not "beyond TFFB": if TFFB already includes some
  of these effects, UM-based findings overstate the edge available on top of it.
- **R1's hand-set rule follows the local round's description as relayed**, not its code (not in this repository); the
  empty-bucket handling is my reading of it.
- **R1 cells are thin** outside WR1/WR2/RB1/RB2/TE1; the WR-target behaviour drifted between periods; the churn
  correction assumes a no-absence game is a fair control for an absence game.
- **R3 TD excess is counted in touchdowns** (the task did not say points).
- Absences are first misses (see R1), so a returning-from-injury or multi-week story is not what is measured.
- Each table cell is a separate test; position-level highlights (TE fade, QB home, QB divisional) were not corrected
  for the number of cells examined.

## Constants to change

| # | Constant (local round) | Current | Recommended | Evidence |
|---|---|---|---|---|
| 1 | Next-up share, WR out, **targets** (WR2 when WR1 is out) | 0.60 (+0.30 to the rest of the WRs) | **0.13** (WR3 0.15; each WR4+ 0.17; all WRs 0.65). Or do not redistribute targets | n=239; test MAE of next man up 3.41 (rule) vs 2.41 (no change): the rule hurts |
| 2 | Spill, WR out to TEs | 0.10 of the vacated total (0.25 of the 0.40 remainder) | **0.07** (TEs together; RBs 0.00) | n=239, [0.03, 0.12] |
| 3 | Next-up share, TE out (TE2) | 0.60 (+0.30 to the rest of the TEs) | **0.15** (all TEs 0.29) | n=111 |
| 4 | Spill, TE out to WRs | 0.10 of the vacated total | **0.47** (WR1 0.22, WR2 0.16, WR3 0.08) | n=111, [0.32, 0.62] |
| 5 | Next-up share, RB out, **carries** (RB2 when RB1 is out) | 0.60 (+0.40 to the rest of the RBs) | **0.47** (RB3+ 0.22 each; one-pick refit 0.40) | n=320, [0.42, 0.52]; test MAE of next man up 4.35 learned vs 4.73 rule vs 6.41 none |
| 6 | Next-up share, RB out, **targets** (RB2) | 0.60 (+0.40 to the rest of the RBs) | **0.24** (WRs 0.46 and TE1 0.21 also gain) | n=33 (thin) |
| 7 | Unassigned share ("nowhere"), WR out | 0.00 (the rule assigns everything) | **0.27** mean / 0.41 median (0.50 in 2022-25) | n=239, [0.16, 0.39]; drift z=3.0 |
| 8 | Unassigned share, RB out (carries) / TE out | 0.00 | **0.19** [0.13, 0.26] / **0.14** | n=352 / 111 |
| 9 | Matchup factor weights | equal | **~0**: show as context only. If used: ridge weights in `matchup_weights.json` | holdout MAE gain QB 0.039, RB 0.015, WR 0.015, TE 0.003, DST 0.001 (bar: 0.2) |
| 10 | Matchup lookback / schedule adjustment | (unspecified) | l4 (QB, RB, TE), blend (WR), l8 (DST); adjustment optional | variants differ by <= 0.025 MAE |
| 11 | BUY↑ threshold (X pts / p%) | sweep 2-5 / 15-35% | **drop the signal** | fit effect -0.01 at the best threshold; test -0.57, wrong sign at 9 of 9 |
| 12 | FADE↓ threshold + TD excess Y | sweep | **TE only**: DK/G >= 2 pts above xFP/G; drop for QB/RB/WR; TD excess adds nothing | TE fit -1.13, test -1.49 [-2.10, -0.87]; WR +0.86 |
| 13 | USAGE↑ / USAGE↓ thresholds | sweep | **RB carry share only**: up >= +10 pts, down >= -5 pts. Drop target-share and red-zone jumps | up +0.56 test [+0.04, +1.09]; down -0.70 [-1.16, -0.25] |
| 14 | WIND flag | 20 mph | **15 mph** (fires on 11% of QB games); 10 mph as a soft tier; or -0.5 QB pts per +5 mph | QB -1.05 [-1.77, -0.33], fit -0.90 / test -1.46; at 20 mph the interval includes 0 in both periods |
| 15 | Rain / cold adjustments (new) | none | QB **-1.7** in rain, **-0.5 per 10°F under 50°F**; WR / TE -0.8 in rain | QB rain n=289, [-2.67, -0.82]; stable fit / test |
| 16 | Closing total in wind (new) | taken as given | **about -1.0 per +5 mph** below the number | miss -1.04 [-1.50, -0.59]; the line moves only -0.34 |
| 17 | Home field (new) | none | **QB +1.1**; RB +0.45 (borderline) | QB fit +0.99 / test +1.14 [+0.63, +1.66] |
| 18 | Short week, divisional, back-to-back road | (flags) | **drop** (QB-only divisional -0.80 is a lead, not a finding) | tests above |
| 19 | Salary lag | (flag) | **untestable**: no historical salary exists; judge it on the weekly sheets | n/a |
