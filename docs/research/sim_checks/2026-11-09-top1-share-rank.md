# Plan S, check 3: ranking GPP builds by Top 1% share (2026-11-09)

**Verdict: not proven, in either arm.** Neither arm comes near the house bar: more than 2 SE of the weekly paired
difference AND a win in most weeks.

- **"Any build"** (every build scored in every contest, the scope with less luck) has share ranking **behind** today's
  Top 1% ranking: −24.8 ROI points a week, SE 14.1, ahead in 1 of 6 weeks.
- **"As entered"** is +4.4 points, SE 18.2, ahead in 1 of 6 weeks: a coin flip.
- The single-entry and small-field GPPs read the same.
- `--rank share` stays an option and `top1` stays the default.
- **Week 9 is not in this test:** it has no standings yet (Sam, 2026-11-09: run Weeks 2 and 4-8 now).

## The two arms

Today's builder is `today_gpp.toml`: `rank = "top1"`, no cash builds, 600 s a week, the pool the week saved, else the
constructed one. Both arms change only how GPP builds are ranked.

| Arm | Settings | Copies in `Top 1% / (1 + expected copies)` |
|---|---|---|
| A | `research/settings/share_gpp.toml` (`rank = "share"`) | `dups.DUP_MODEL_GPP`, as the code has it today: scale 10.02, exponent 0.712, on the field's projected `top` shares |
| B | `research/settings/share_dupes_gpp.toml` (`rank = "share"`, `[flags] share_dupes_fitted = true`) | The `dfs contests dupes` kind of model, on GppOwn, fitted only on the weeks before the one being built (no lookahead). Code: `src/dfs/sim/share_dupes_flag.py`, default off. |

Arm B's fits by week:

| Week | Scale | Exponent | GPP entries fitted on |
|---|---|---|---|
| 5 | 0.901 | 0.591 | 18,403 |
| 6 | 0.816 | 0.592 | 33,576 |
| 7 | 0.557 | 0.559 | 42,759 |
| 8 | 0.643 | 0.564 | 46,805 |

Weeks 2 and 4 have nothing earlier to fit on (`field_own` needs a cash week and a GPP week before), so arm B uses today's
model there and its notes say so.

## Results

Contests are the GPPs Sam entered with a standings file: 27 GPPs in Weeks 2 and 4-8, 63 entries. The full Flashback
reports are in `docs/research/backtest/` (gitignored). The split by group is `python -m dfs.sim.share_rank_retro`.

Paired difference in ROI points, share minus today. Arms A and B came out the same at this precision.

| Contests | Scope | n | ROI share | ROI today | By week (SE) | Weeks ahead | By contest (SE) |
|---|---|---|---|---|---|---|---|
| All GPP | as entered | 27 | −36.8% | −37.9% | +4.4 (18.2) | 1 of 6 | −3.2 (7.6) |
| All GPP | any build | 27 | +72.0% | +106.0% | **−24.8 (14.1)** | 1 of 6 | −27.7 (6.7) |
| Single-entry | as entered | 16 | −63.2% | −59.6% | −3.9 (3.9) | 0 of 6 | −3.7 (2.7) |
| Single-entry | any build | 16 | +121.9% | +155.1% | −17.9 (12.8) | 1 of 6 | −27.1 (8.7) |
| Under 1,000 entrants | as entered | 17 | −42.9% | −44.8% | +3.8 (21.4) | 1 of 6 | −1.3 (11.4) |
| Under 1,000 entrants | any build | 17 | +59.4% | +93.2% | −25.5 (14.6) | 1 of 6 | −29.8 (9.6) |
| **SE or small (the target)** | as entered | 22 | −46.2% | −47.9% | +5.6 (20.3) | 1 of 6 | −1.4 (8.8) |
| **SE or small (the target)** | any build | 22 | +72.8% | +107.1% | −24.9 (14.2) | 1 of 6 | −29.0 (7.9) |

As entered, by week (share / today): Week 2 −100% / −100%, Week 4 +86% / +98%, Week 5 −14% / +35%, Week 6 −100% / −100%,
Week 7 +1% / −86%, Week 8 −100% / −100%. Three weeks tie, because both rankings entered the same lineups or lost
everything. Share is ahead in one week and behind in two.

## Why the arms barely differ, and why share is mostly noise here

**Expected copies are tiny where Sam plays.**

- The builder reads copies against a 1,000-entry GPP (`dups.DEFAULT_GPP_ENTRIES`).
- Every build in these six weeks expects 0.00-0.17 copies under today's model, and about the same under arm B's fit.
- Dividing by `1 + copies` therefore moves a build's score by at most 15%, and usually by 1-3%.
- That is enough to reorder near-ties in the Top 1% chance, which the builder rounds before comparing. It is not enough
  to change what kind of build gets picked.

**How many builds changed:**

- Share against today: 1, 4, 1, 1, 8 and 4 builds differ across Weeks 2, 4, 5, 6, 7 and 8. Week 7 also got two more
  builds (12 against 10).
- Arm B against arm A: one build differs, in Week 7. No entered lineup changed, so the two arms tie.

**The −25-point "any build" gap is not evidence that share is worse.**

- It comes from the handful of swapped builds landing on different GPP tails, and it is under 2 SE.
- For the same reason, a share win would not have been evidence for it either.

**When share ranking could matter:** large-field GPPs, where copies run into whole numbers. Sam does not play those
(`[contest_plan]` caps N-max GPPs with 2,000+ entrants at $10). It is not worth a switch for his contest mix.

## A fix found along the way

`backtest.run_week` named a week's cache file before it loaded the plugin flags. A spawned worker therefore saved a
flagged setting's builds under a fingerprint without the flag's version, and the parent then found nothing and rebuilt
every week serially. This affected `share_dupes_fitted` and every earlier plugin flag:

- **Results:** the same. The rebuilt builds matched the workers' builds exactly; the search runs to the end, so it is
  deterministic.
- **Time:** about double.

Fixed: the flags are loaded first. A test in `tests/test_backtest.py` covers it.

## What this does not test

- **Week 9:** no standings yet. The same three commands rerun with it once its standings are in:

  ```
  dfs backtest --weeks 2,4,5,6,7,8,9 --settings research/settings/share_gpp.toml --compare research/settings/today_gpp.toml
  dfs backtest --weeks 2,4,5,6,7,8,9 --settings research/settings/share_dupes_gpp.toml --compare research/settings/today_gpp.toml
  python -m dfs.sim.share_rank_retro --weeks 2,4,5,6,7,8,9 --settings research/settings/share_gpp.toml --compare research/settings/today_gpp.toml
  ```

- **Cash builds:** share applies only to GPP builds.
- **Copies against each contest's own field size:** the builder ranks against one size, 1,000 entries. A Hundo (100
  entries) would shrink the divisor further, and Sam's 5-max Singlebacks (about 4,750 entries) would grow it about
  fivefold, which is still well under one copy.
