# Part 7 — Strategy additions (decided 2026-09-17)

> **Insert this into `docs/planning/PROMPT_PHASE6.md`, immediately before
> `# Part 4 — Tab strip`.**

These come from `docs/planning/EXPERT_REVIEW.md`, which has the sourcing. Sam made
every call below explicitly. **Do not reopen them.** Build these AFTER
Part 1's bug fixes, alongside Parts 2–6.

## Context you need about how Sam actually plays

This was not written down before and it changes what the tool is for:

- **Small-field / single-entry GPPs (under 5,000 entries) and cash games
  (50/50, double-up, H2H). No large-field tournaments.**
- **Roughly 50/50 between cash and GPP.** Two co-equal modes, not a GPP
  tool with a cash afterthought.
- 4–8 lineups a week.

## 7.1 — Demote Leverage; stop implying precision we don't have

`Leverage` and `OwnPct` come off the spine and go into the collapsed
**Ceiling detail** group with `ProjOwn` and `LevBasis` (Part 2 already
puts them there — this confirms it rather than changing it). `Leverage` is
no longer a primary sort anywhere and no longer drives a `Board` panel.

**Do not rewrite Leverage into probability units.** The review recommends
it and Sam has decided against it for now, for good reasons: the metric is
demoted, we are not calibrating ownership, and TFFB almost certainly ships
a *large-field* ownership projection while Sam plays small-field — so the
input is systematically wrong for his contests.

**Do add a one-line note** on the `Board` and in `docs/CALCULATIONS.md`
saying exactly that: ownership is a large-field projection being used in
small-field contests, so treat leverage as directional at best. An honest
caveat beats precise-looking wrong numbers.

## 7.2 — Replace `Val`, and make it the default sort

`Val = ProjPts / (Salary/1000)` is salary-dependent (cheap players outrank
expensive ones even when the expensive ones are better plays) and
position-dependent (QBs dominate any points-per-dollar leaderboard).

**New column `ValAdj` = `ProjPts − E[ProjPts | Salary, Position]`.**

**Version 1 needs no history and ships now.** Fit the baseline on the
*current slate's own projections*: for each position, regress `ProjPts` on
`Salary` across that slate (linear is fine over the usable range) and take
the residual. That answers "is this player projected above what this
slate's pricing implies for his position," which is exactly the lineup
question, and it needs zero accumulated data.

Version 2, once the results loop exists, refits the baseline on *realized*
points instead — which additionally tells you where the market is
systematically wrong. Leave a clear seam for that; don't build it yet.

- `ValAdj` joins the spine, immediately after `Val`.
- `ValAdj` becomes **EdgeRaw's default sort**, replacing whatever it uses
  now.
- **Keep `Val`.** It has exactly one remaining job: `Val >= 3.0` is a real
  cash threshold (3 points per $1,000 → ~150 points, which wins DK cash
  roughly 90% of the time). Bad sort key, good filter line. Use it in 7.3.

## 7.3 — Cash and GPP as co-equal views

The tool currently treats these identically. At a 50/50 split that is the
biggest functional gap in the sheet — bigger than anything leverage.

Implement as **two saved column/sort/filter presets**, driven by a CLI
command (`dfs view cash` / `dfs view gpp`) plus a clearly-labelled control
cell on `EdgeRaw`. No new tabs.

| | **Cash view** | **GPP view** |
|---|---|---|
| Sort | `ValAdj`, then floor if available | `ValAdj`, then `Ceil` |
| `Val` | **Shown, with `>= 3.0` highlighted** as the threshold line | Not emphasized |
| `Leverage` / `OwnPct` / `ProjOwn` | **Hidden entirely** — actively harmful here | Collapsed group, available |
| `Ceil` / `CeilVal` | Collapsed | Shown |
| Stack columns (7.4) | Collapsed | Shown |
| `Flags` | Shown | Shown |

Ask nothing further about this table — it is the decision.

## 7.4 — Make stacks visible (no optimizer)

This is the largest gap in the tool and the fix needs no solver.

**Add to `EdgeRaw`** (append-only in the data contract; spine position per
Part 2):

- **`GameID`** — from `games.csv`, which is already synced. This is the
  column everything else here depends on.
- **`TmRank`** — the player's salary rank within his own team and position
  group (WR1/WR2/WR3, TE1, RB1). DK salary rank within team is a crude but
  serviceable proxy for target hierarchy. Label it as a proxy in
  `CALCULATIONS.md`; do not imply it is measured target share.

**Add a game-grouped view** — a `Games` section or tab where players are
grouped by `GameID`, games sorted by total descending, so every candidate
stack is physically adjacent. This is the whole point: the sheet currently
cannot show you a stack.

**Add three hard rules** as `Issues`-column guardrails on `Lineups`:

1. **Never roster a DST against your own QB's team.** The correlation is
   −0.46, the largest coefficient in the data, and simulation shows this
   single mistake costs about a third of the stacking gain. One line, best
   effort-to-benefit ratio in the entire review.
2. **Max one RB per game.**
3. **Warn when a GPP lineup has a QB with fewer than 2 same-team pass
   catchers.** The evidence is specific: QB+1 appears in 49.1% of Milly
   Maker winners and 49.0% of the field — *zero edge*. QB+2 is 41.2% vs
   28.9%. QB+3 underperforms. So the warning fires at 0 or 1, and **also**
   at 3 or more. Cash view: suppress this warning entirely.

**Do not build a QB+RB stack rule.** Sources disagree wildly (0.07 to
0.43) and the two that measured it carefully call it functionally zero.

## 7.5 — Lineup-level metrics block

Every published target is a *lineup* property; our tool is entirely
player-level. This was independently flagged as the highest
impact-per-effort item available, and it is pure spreadsheet work against
data already in the sheet.

A block on `Lineups` (or a compact panel per lineup block) reporting:

- Salary used, and remaining
- **Stack signature** — e.g. `QB+2 (KC) + 1 bring-back`
- **Distinct games represented**
- Bring-back present, yes/no
- RBs-per-game violations
- Summed projected ownership
- Count of players under 10% owned
- Minimum unique players vs each of the other lineups

**Portfolio-level, across all of Sam's lineups this week:**

- **Distinct QBs used** and **distinct primary games used.** This is the
  headline number. Simulation at 6 lineups: six distinct stacks give ~3x
  the tail probability of six lineups on one stack, while player-level
  uniqueness beyond that is worth ~3%.
- Flag when two lineups share a QB.

**Do not build player-level exposure caps.** At 4–8 lineups they are
actively harmful — they force Sam off his best plays for no portfolio
benefit. The existing Exposure tab stays as a *report*, not a constraint.

## 7.6 — Board revision

Part 3's design stands with two changes, both following from the above:

- **No leverage panel.** Replace it with the **game-grouped stack view**
  (7.4) as a top-level section — for a small-field GPP player, "what are
  this week's stackable games" is the question the Board should answer.
- **Per-position leaders sort by `ValAdj`**, not `CeilVal`. This is the
  real fix for the 11-QBs-of-12 bug: ranking within position AND on a
  salary-adjusted metric.

## 7.7 — Instrument the Ceiling question

Sam does not know what percentile TFFB's `Ceiling` represents, and has
decided to **measure it** rather than assume.

Do not guess and do not build on an assumed value. Instead, add to the
results loop's spec in `docs/planning/PROMPT_DATA.md`: once actual points exist,
compute **what fraction of players exceeded their published `Ceiling`**,
per position, per week. If it converges near 15%, `Ceiling` is an
85th-percentile quantile. Whatever it converges to, write it down in
`docs/CALCULATIONS.md` — it is load-bearing for any future simulation, and
for scoring the ceiling with the correct rule (**pinball loss, not RMSE** —
evaluating a quantile with a mean-consistent rule is a category error that
will call a correct ceiling model bad).

## 7.8 — Deliberately NOT doing

Say so in `docs/planning/ROADMAP.md` so a future session doesn't rediscover these:

- **Leverage in probability units** — see 7.1.
- **Logging actual DK ownership weekly** — Sam declined the manual step.
- **An optimizer** — stack rules plus lineup metrics capture most of the
  gain at a fraction of the effort. Revisit later.
- **A standalone ownership model** — high effort, low value, we buy one.
- **A contest-selection tab** — downgraded. Sam already plays the
  small-field single-entry and cash structures the research recommends, so
  this would be rake hygiene rather than a change of behaviour.
- **Player-level exposure caps** — see 7.5.
