"""Retro of the stack ranker (`dfs.stacks`) on Sam's real contests: did the stacks it flagged as high leverage
show up in the real top-1% lineups more than the real field played them?

For a past week, rebuild the ranking from what was known at the lock (the last pre-lock EdgeRaw snapshot:
CalPts, salaries, kickoffs, the published ownership; the field's construction rates and chalk herd fitted on
the OTHER weeks' real fields, as `sim.contest_retro` does), take the stacks it puts first, then read the
week's big GPPs (`MIN_ENTRIES` entries or more, the fields the Top 1% line is measured on): the share of the
contest's real lineups that play a flagged stack against the share of its real top-1% lineups that do.

**The number** (one per week, `lift`): (share of the real top-1% lineups on a flagged stack) / (share of ALL
the week's real lineups on one), pooled over the week's big GPPs. 1.0 is "no better than the field's rate",
above is the stacks showing up where the leverage said. `z` is the binomial z-score of the top-1% count
against the field rate; lineups of one week share the week's outcomes, so it is an optimistic one.

**The baseline.** Winning lineups are stacked more often than the field's, so ANY set of stacks has a lift
above 1. The fair yardstick is the same count for stacks picked without the ranking: `lift_all` (every
candidate stack the ranking considered) and `pctile`, where the flagged set's lift falls among 300 random
sets of the same size drawn from the candidates (0.5 = no better than chance). The weeks' percentiles are
combined as normal scores (`combined_z`, Stouffer) for the verdict: `VERDICT_TESTED` needs a one-sided
p < 0.05 and a lift above the baseline's, `VERDICT_WEAK` a lift above the baseline's in most weeks without
that, anything else `VERDICT_UNTESTED`.

A flagged stack is a row of the ranking's table; "plays it" is the exact definition of `dfs.stacks` (the QB
and exactly those teammates of his team, bring-back or not). Besides the leverage ranking the retro scores two
others on the same real lineups, as controls: the stacks with the highest simulated `top1` (upside alone,
whatever the field plays) and the stacks the field plays most (popularity alone).

Local and offline: the standings and snapshots are Sam's files under `data/raw`, nothing here writes a sheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs import stacks
from dfs.contests import winners
from dfs.sim import contest_retro as cr
from dfs.sim import field_gen

TOP_K = 20  # the stacks a ranking flags (the tab's list)
MIN_ENTRIES = winners.MIN_ENTRIES
RANKINGS = {"lev": "leverage", "top1": "upside alone", "field": "popularity alone"}
BASELINE = "all"  # every candidate stack: the lift any stack set gets for winning lineups being stacked
RANDOM_SETS = 300
TESTED_Z = 1.645  # one-sided p < 0.05
POSITIVE_WEEKS_SHARE = 0.5
VERDICT_TESTED, VERDICT_WEAK, VERDICT_UNTESTED = "tested", "weak", "untested"


@dataclass
class FieldModel:
    """What the field generator needs, fitted on the real GPP fields of the weeks other than
    `exclude_week`."""

    rates: field_gen.FieldRates
    herd: field_gen.HerdModel
    weeks: tuple[int, ...]

    def to_dict(self) -> dict:
        r, h = self.rates, self.herd
        return {
            "rates": {
                "stack1": r.stack1,
                "stack2": r.stack2,
                "bringback_stacked": r.bringback_stacked,
                "bringback_unstacked": r.bringback_unstacked,
                "unspent": list(r.unspent),
            },
            "herd": {"share": h.share, "decay": h.decay, "k": h.k, "lead_decay": h.lead_decay},
            "weeks": list(self.weeks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> FieldModel:
        return cls(
            rates=field_gen.FieldRates(**{**d["rates"], "unspent": tuple(d["rates"]["unspent"])}),
            herd=field_gen.HerdModel(**d["herd"]),
            weeks=tuple(d["weeks"]),
        )


def chalk_by_week(contests: list[cr.RealContest]) -> tuple[dict[int, np.ndarray], dict[int, list]]:
    """Each week's chalk lineups (highest ownership product) and their name keys."""
    chalks, keys = {}, {}
    for wk in sorted({c.week for c in contests}):
        slate = cr.slate_for(cr.week_slate_frame(wk), "tffb")
        chalks[wk] = field_gen.chalk_lineups(slate)
        keys[wk] = cr.chalk_keys(slate, chalks[wk])
    return chalks, keys


def fit_field_model(
    contests: list[cr.RealContest], keys: dict[int, list], *, exclude_week: int | None = None
) -> FieldModel:
    """The GPP field's construction rates and herd from the real fields of every week but `exclude_week`."""
    herd_keys = {w: set(v) for w, v in keys.items()}
    rates = cr.rates_for(contests, "gpp", exclude_week=exclude_week, herd_keys=herd_keys)
    h = cr.fit_herd(contests, keys, "gpp", exclude_week=exclude_week)
    lead = cr.fit_lead_decay(cr.lead_ranks(contests, keys), exclude_week=exclude_week)
    herd = field_gen.HerdModel(share=h.share, decay=h.decay, k=h.k, lead_decay=lead)
    weeks = tuple(sorted({c.week for c in contests if c.fmt == "gpp" and c.week != exclude_week}))
    return FieldModel(rates=rates, herd=herd, weeks=weeks)


# ---------------------------------------------------------------------------------------------------------
# The ranking as it stood at a past lock
# ---------------------------------------------------------------------------------------------------------


def rank_week(
    week: int,
    model: FieldModel,
    chalk: np.ndarray,
    *,
    n_sims: int = stacks.DEFAULT_SIMS,
    seed: int = 0,
):
    """`stacks.rank_stacks` on the week's pre-lock inputs. Returns (result, slate)."""
    frame = cr.week_slate_frame(week)
    edge = cr.pre_lock_edge(week)
    slate = cr.slate_for(frame, "tffb")
    specs = cr.slate_specs(slate, frame, edge)
    gpp = stacks.build_gpp_field(slate, model.rates, model.herd, chalk=chalk[: stacks.CHALK_K], seed=seed)
    return stacks.rank_stacks(slate, specs, gpp, n_sims=n_sims, seed=seed), slate


def flagged(table: pd.DataFrame, ranking: str, k: int = TOP_K) -> set[tuple[int, tuple[int, ...]]]:
    """The `(qb, mates)` keys the ranking puts first."""
    if table.empty:
        return set()
    if ranking == BASELINE:
        return {(int(q), tuple(m)) for q, m in zip(table["qb_i"], table["mates_i"], strict=True)}
    top = table.sort_values(ranking, ascending=False, kind="stable").head(k)
    return {(int(q), tuple(m)) for q, m in zip(top["qb_i"], top["mates_i"], strict=True)}


# ---------------------------------------------------------------------------------------------------------
# The real contests
# ---------------------------------------------------------------------------------------------------------


def contest_stacks(c: cr.RealContest, slate: field_gen.Slate) -> tuple[list, np.ndarray]:
    """Each parsed real lineup's `(qb, exact mates)` stack key (`(-1, ())` when a player does not resolve to
    the slate) and its points."""
    index = {n: i for i, n in enumerate(slate.names)}
    rows = c.standings.lineups
    lineups = [cr.real_lineup_indices(slots, index) for slots in rows["slots"]]
    keys = []
    for idx in lineups:
        if idx is None:
            keys.append((-1, ()))
        else:
            q, m, _ = stacks.stack_keys(slate, idx.reshape(1, -1))[0]
            keys.append((q, m))
    return keys, rows["points"].to_numpy(float)


def top_mask(points: np.ndarray) -> np.ndarray:
    """The best `ceil(1%)` lineups of a contest, ties at the cut kept."""
    n = len(points)
    cut_n = max(1, math.ceil(stacks.TOP_FRACTION * n))
    cut = np.sort(points)[::-1][cut_n - 1]
    return points >= cut


@dataclass
class WeekRetro:
    week: int
    contests: int  # big GPPs read
    lineups: int  # real lineups in them
    top: int  # the top-1% lineups among them
    # per ranking (and the baseline): top-1% lineups on a flagged stack, all lineups on one
    x_top: dict[str, int] = field(default_factory=dict)
    x_field: dict[str, int] = field(default_factory=dict)
    predicted: dict[str, float] = field(default_factory=dict)  # the sim's own lift of the flagged set
    field_pred: dict[str, float] = field(default_factory=dict)  # the sim's field rate of the flagged set
    pctile: float = float("nan")  # the leverage set's lift among random sets of the same size

    def lift(self, ranking: str) -> float:
        p = self.x_field[ranking] / max(self.lineups, 1)
        q = self.x_top[ranking] / max(self.top, 1)
        return q / p if p > 0 else float("nan")

    def z(self, ranking: str) -> float:
        p = self.x_field[ranking] / max(self.lineups, 1)
        if p <= 0 or p >= 1 or self.top == 0:
            return float("nan")
        return (self.x_top[ranking] - self.top * p) / math.sqrt(self.top * p * (1 - p))

    def field_rate(self, ranking: str) -> float:
        return self.x_field[ranking] / max(self.lineups, 1)

    def over_baseline(self, ranking: str = "lev") -> float:
        """The ranking's lift over the lift of every candidate stack."""
        base = self.lift(BASELINE)
        return self.lift(ranking) / base if base == base and base > 0 else float("nan")


def score_week(
    week: int,
    result: stacks.StackResult,
    slate: field_gen.Slate,
    contests: list[cr.RealContest],
    *,
    seed: int = 0,
) -> WeekRetro:
    """The retro numbers of one week's ranking against its big real GPPs."""
    big = [c for c in contests if c.week == week and c.fmt == "gpp" and c.entries >= MIN_ENTRIES]
    flags = {r: flagged(result.table, r) for r in (*RANKINGS, BASELINE)}
    out = WeekRetro(week=week, contests=len(big), lineups=0, top=0)
    seen: dict[tuple, int] = {}
    in_top: dict[tuple, int] = {}
    for c in big:
        keys, points = contest_stacks(c, slate)
        top = top_mask(points)
        out.lineups += len(keys)
        out.top += int(top.sum())
        for k, t in zip(keys, top, strict=True):
            seen[k] = seen.get(k, 0) + 1
            in_top[k] = in_top.get(k, 0) + int(t)
    for r, fl in flags.items():
        out.x_field[r] = sum(seen.get(k, 0) for k in fl)
        out.x_top[r] = sum(in_top.get(k, 0) for k in fl)
        rows = result.table[
            [
                (int(q), tuple(m)) in fl
                for q, m in zip(result.table["qb_i"], result.table["mates_i"], strict=True)
            ]
        ]
        out.field_pred[r] = float(rows["field"].sum())
        out.predicted[r] = (
            float(rows["boom_share"].sum() / rows["field"].sum()) if len(rows) else float("nan")
        )
    out.pctile = random_percentile(out, flags["lev"], flags[BASELINE], seen, in_top, seed=seed)
    return out


def random_percentile(
    week: WeekRetro, flagged_set: set, candidates: set, seen: dict, in_top: dict, *, seed: int = 0
) -> float:
    """Where the flagged set's lift falls among `RANDOM_SETS` random sets of its size from the candidates
    (ties count half): 0.5 is no better than a random pick, 1.0 better than every one."""
    pool = sorted(candidates)
    if week.top == 0 or len(pool) <= len(flagged_set) or not flagged_set:
        return float("nan")

    def lift_of(keys) -> float:
        n = sum(seen.get(k, 0) for k in keys)
        t = sum(in_top.get(k, 0) for k in keys)
        return (t / week.top) / (n / week.lineups) if n else float("nan")

    mine = lift_of(flagged_set)
    rng = np.random.default_rng(seed + week.week)
    draws = np.array(
        [
            lift_of([pool[i] for i in rng.choice(len(pool), len(flagged_set), replace=False)])
            for _ in range(RANDOM_SETS)
        ]
    )
    draws = draws[draws == draws]
    return (
        float(((draws < mine).sum() + 0.5 * (draws == mine).sum()) / len(draws))
        if len(draws)
        else float("nan")
    )


def pooled(weeks: list[WeekRetro], ranking: str) -> dict[str, float]:
    """Counts added over weeks (a week weighs by its lineups): lift, z, and the weeks with lift above 1."""
    n = sum(w.lineups for w in weeks)
    t = sum(w.top for w in weeks)
    xf = sum(w.x_field[ranking] for w in weeks)
    xt = sum(w.x_top[ranking] for w in weeks)
    p = xf / n if n else float("nan")
    lift = (xt / t) / p if t and p else float("nan")
    z = (xt - t * p) / math.sqrt(t * p * (1 - p)) if t and 0 < p < 1 else float("nan")
    lifts = [w.lift(ranking) for w in weeks if w.top > 0]
    return {
        "lift": lift,
        "z": z,
        "weeks": len(lifts),
        "weeks_above_1": sum(1 for x in lifts if x == x and x > 1.0),
        "x_top": xt,
        "top": t,
        "field_rate": p,
    }


def combined_z(weeks: list[WeekRetro]) -> float:
    """The weeks' percentiles as one normal score: each percentile to a z (kept inside 1%..99%), summed and
    divided by the square root of the number of weeks."""
    from scipy.stats import norm

    ps = [min(max(w.pctile, 0.01), 0.99) for w in weeks if w.pctile == w.pctile]
    return float(norm.ppf(ps).sum() / math.sqrt(len(ps))) if ps else float("nan")


def verdict(weeks: list[WeekRetro]) -> str:
    """`VERDICT_TESTED`, `VERDICT_WEAK` or `VERDICT_UNTESTED` (see the module docstring)."""
    scored = [w for w in weeks if w.top > 0]
    if not scored:
        return VERDICT_UNTESTED
    over = [w.over_baseline() for w in scored]
    above = sum(1 for x in over if x == x and x > 1.0)
    p, base = pooled(scored, "lev"), pooled(scored, BASELINE)
    pooled_above = p["lift"] == p["lift"] and base["lift"] == base["lift"] and p["lift"] > base["lift"]
    if not pooled_above or above / len(scored) <= POSITIVE_WEEKS_SHARE:
        return VERDICT_UNTESTED
    return VERDICT_TESTED if combined_z(scored) >= TESTED_Z else VERDICT_WEAK


def tested(weeks: list[WeekRetro]) -> bool:
    return verdict(weeks) == VERDICT_TESTED


VERDICT_WORDS = {
    VERDICT_TESTED: "A tested signal.",
    VERDICT_WEAK: "A weak signal: above the baseline in most weeks, not yet beyond chance.",
    VERDICT_UNTESTED: "UNTESTED signal: no better than stacks picked at random.",
}


def track_record_line(weeks: list[WeekRetro]) -> str:
    """The sentence the tab shows on top."""
    scored = [w for w in weeks if w.top > 0]
    if not scored:
        return "Track record: no big GPP standings to test the leverage on yet."
    p, base = pooled(scored, "lev"), pooled(scored, BASELINE)
    per_week = ", ".join(f"W{w.week} {w.lift('lev'):.1f}x" for w in scored)
    pct = [w.pctile for w in scored if w.pctile == w.pctile]
    chance = f", {min(pct):.0%}-{max(pct):.0%} of random picks beaten" if pct else ""
    return (
        f"Track record (weeks {', '.join(str(w.week) for w in scored)}): the 20 highest-leverage stacks "
        f"were in {p['lift']:.1f}x as many real top-1% lineups as the field's share ({per_week}); any "
        f"candidate stack gets {base['lift']:.1f}x{chance}. {VERDICT_WORDS[verdict(scored)]}"
    )


def run(
    weeks: list[int] | None = None,
    *,
    contests: list[cr.RealContest] | None = None,
    n_sims: int = stacks.DEFAULT_SIMS,
    log=print,
) -> list[WeekRetro]:
    """The retro over every week with big GPP standings (or `weeks`)."""
    contests = contests if contests is not None else cr.load_real_contests()
    chalks, keys = chalk_by_week(contests)
    have = sorted({c.week for c in contests if c.fmt == "gpp"})
    out = []
    for wk in weeks or have:
        model = fit_field_model(contests, keys, exclude_week=wk)
        result, slate = rank_week(wk, model, chalks[wk], n_sims=n_sims)
        scored = score_week(wk, result, slate, contests)
        out.append(scored)
        log(
            f"week {wk}: {scored.contests} big GPPs, {scored.lineups} lineups, top-1% {scored.top}; "
            f"leverage lift {scored.lift('lev'):.2f} (z {scored.z('lev'):+.1f}), "
            f"sim {result.meta['seconds']:.0f}s"
        )
    return out


# ---------------------------------------------------------------------------------------------------------
# What the tab reads
# ---------------------------------------------------------------------------------------------------------


def retro_payload(weeks: list[WeekRetro]) -> dict:
    """The retro as the tab keeps it (`stacks_inputs.RETRO_FILE`): the track-record sentence, whether the
    leverage signal tested positive, and the numbers per week and ranking."""
    scored = [w for w in weeks if w.top > 0]
    return {
        "line": track_record_line(weeks),
        "verdict": verdict(scored),
        "tested": tested(scored),
        "combined_z": combined_z(scored) if scored else float("nan"),
        "weeks": [
            {
                "week": w.week,
                "contests": w.contests,
                "lineups": w.lineups,
                "top": w.top,
                "pctile": w.pctile,
                **{
                    r: {
                        "lift": w.lift(r),
                        "z": w.z(r),
                        "x_top": w.x_top[r],
                        "field_rate": w.field_rate(r),
                        "predicted_lift": w.predicted[r],
                    }
                    for r in (*RANKINGS, BASELINE)
                },
            }
            for w in weeks
        ],
        "pooled": {r: pooled(scored, r) for r in (*RANKINGS, BASELINE)} if scored else {},
    }


def refit_field_model(contests: list[cr.RealContest] | None = None) -> FieldModel:
    """Fit the GPP field's rates and herd on every week's real fields and save it for the tab (about two
    minutes: it parses the standings)."""
    from dfs import stacks_inputs

    contests = contests if contests is not None else cr.load_real_contests()
    _, keys = chalk_by_week(contests)
    model = fit_field_model(contests, keys)
    stacks_inputs.save_json(stacks_inputs.FIELD_MODEL_FILE, model.to_dict())
    return model


def refit_and_save(*, n_sims: int = stacks.DEFAULT_SIMS, log=print) -> dict:
    """The weekly refit: the field model, then the retro, both saved. Returns the retro payload."""
    from dfs import stacks_inputs

    contests = cr.load_real_contests()
    model = refit_field_model(contests)
    log(f"field model fitted on weeks {', '.join(map(str, model.weeks))}")
    payload = retro_payload(run(contests=contests, n_sims=n_sims, log=log))
    stacks_inputs.save_json(stacks_inputs.RETRO_FILE, payload)
    return payload
