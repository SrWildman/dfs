"""Choose a week's lineups as a set, not one at a time: portfolio selection on shared simulated draws.

`lineup_suggest` ranks every build alone, and `entries.suggest_pick` then hands each entry the best build its
contest does not already hold, so the best build lands in every contest. This module picks for ALL of Sam's
entries at once: a greedy search (Hunter-Vielma-Zaman, Haugh-Singal: the portfolio objectives below are
submodular or close, so greedy keeps most of the best set's value) that adds, one entry at a time, the lineup
with the largest marginal gain in a portfolio objective, every candidate scored on the same draws of the slate
and the same generated fields (`sim.contest_sim`, kept per draw with `keep_draws`).

**What can and cannot help.** Expected dollars add up across contests, so for single-entry contests joint
selection cannot raise E[$] under a correct model: it is only separable arithmetic. Joint selection matters

- *inside one contest* with several of his entries (an N-max GPP): they compete for the same places, so each
  entry's place counts the others' lineups that scored more, and an identical lineup twice splits its prize
  with itself. This is exact here (`Contest.pay`);
- *across contests*, for objectives that are not a plain sum: the chance that at least one entry reaches the
  top 1% (`top1`, a coverage objective) and expected log growth of the week's profit (`robust`, concave:
  a portfolio that all rides on one player's game is worth less than its E[$] says);
- *when the model is wrong in a correlated way* (Week 6: the same four players in 10-13 of 15 lineups, all
  busted): the `robust` objective is meant to run on draws whose projections were shocked per player and per
  team (`portfolio_retro.shocked_specs`), so that concentration is punished where the shocks hurt it.

**Objectives** (`OBJECTIVES`), all on the draws the problem carries:

- `edollars`: the portfolio's expected payout;
- `top1`: P(at least one of his entries finishes in the top 1% of its contest), the union over every entry of
  every contest (ties broken by E[$]);
- `robust`: E[log(1 + (payout - fees) / B)] with `B` the bankroll scale (default ten times the week's fees).

**Options.** `max_lineup_share` (a lineup in at most that share of a group's entries), `max_player_share` (a
player likewise), `max_overlap` (players two entries of one contest may share), `allow_repeat` (the same
lineup twice in one contest; off); the share caps apply to the groups in `cap_groups` (GPP entries by default:
a cap on three cash entries only forbids sharing a QB). A slot no candidate can fill under those rules is left
empty and reported: a rule is never silently relaxed.

Pure functions; deterministic. No network, no sheet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from dfs.sim.contest_sim import TOP_FRACTION, _cumulative, block_payout  # noqa: PLC2701
from dfs.sim.payouts import ContestKind, payout_table

OBJECTIVES = ("edollars", "top1", "robust")
BANKROLL_MULTIPLE = (
    10.0  # B, in weeks of fees: log growth is nearly linear (E[$]) at this scale, mildly concave
)
TIE = 1e-9  # the E[$] tie-break weight of the top1 objective


@dataclass
class Contest:
    """One contest he has `slots` entries in: its payout table and, per draw and candidate, the place among
    the field alone and the identical field entries expected (`ContestSimResult.detail`). `group` ("cash" /
    "gpp") scopes the exposure rules; `eligible[j]` says whether candidate j may be entered here."""

    label: str
    kind: ContestKind
    slots: int
    place: np.ndarray  # draws x candidates
    copies: np.ndarray  # draws x candidates
    eligible: np.ndarray  # candidates, bool
    group: str = "gpp"
    cum: np.ndarray = field(init=False, repr=False)
    top_cut: float = field(init=False)

    def __post_init__(self) -> None:
        self.cum = _cumulative(payout_table(self.kind))
        self.top_cut = max(1.0, TOP_FRACTION * self.kind.entries)

    def pay(self, scores: np.ndarray, keys: Sequence, chosen: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
        """`(payout, hit)` per draw of his entries `chosen` (candidate indices, repeats allowed) taken
        together: each entry's place is its place among the field plus his other entries that scored more,
        its copies the field's expected plus his identical ones; `hit` is any entry in the top 1%."""
        n_draws = self.place.shape[0]
        if not len(chosen):
            return np.zeros(n_draws), np.zeros(n_draws, dtype=bool)
        idx = list(chosen)
        s = scores[:, idx]
        higher = (s[:, None, :] > s[:, :, None]).sum(axis=2)
        same = np.array([sum(keys[a] == keys[b] for b in idx) - 1 for a in idx], dtype=float)
        place = self.place[:, idx] + higher
        copies = self.copies[:, idx] + same[None, :]
        return block_payout(self.cum, place, copies).sum(axis=1), (place <= self.top_cut).any(axis=1)

    def pay_with_each(
        self, scores: np.ndarray, keys: Sequence, chosen: Sequence[int], cand: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """`pay` of `chosen` plus each candidate in `cand` in turn: `(payout, hit)`, draws x len(cand)."""
        s_new = scores[:, cand]
        n_draws, m = s_new.shape
        same_new = np.array([sum(keys[a] == keys[b] for b in chosen) for a in cand], dtype=float)
        higher_new = (
            (scores[:, list(chosen)][:, :, None] > s_new[:, None, :]).sum(axis=1)
            if len(chosen)
            else np.zeros((n_draws, m))
        )
        place_new = self.place[:, cand] + higher_new
        pay = block_payout(self.cum, place_new, self.copies[:, cand] + same_new[None, :])
        hit = place_new <= self.top_cut
        if len(chosen):
            idx = list(chosen)
            s_old = scores[:, idx]
            higher_old = (s_old[:, None, :] > s_old[:, :, None]).sum(axis=2)
            same_old = np.array([sum(keys[a] == keys[b] for b in idx) - 1 for a in idx], dtype=float)
            overtaken = s_new[:, None, :] > s_old[:, :, None]  # draws x old x new
            same_pair = np.array([[keys[a] == keys[b] for b in cand] for a in idx], dtype=float)  # old x new
            base_place = (self.place[:, idx] + higher_old)[:, :, None]
            base_copies = (self.copies[:, idx] + same_old[None, :])[:, :, None]
            place_old = base_place + overtaken
            pay = pay + block_payout(self.cum, place_old, base_copies + same_pair[None, :, :]).sum(axis=1)
            hit = hit | (place_old <= self.top_cut).any(axis=1)
        return pay, hit


@dataclass
class Problem:
    """Everything the selection reads: his contests, every candidate's points per draw, each candidate's
    identity (`keys`, its sorted names) and players (`members`, slate indices, for the exposure rules)."""

    contests: list[Contest]
    scores: np.ndarray  # draws x candidates
    keys: list[tuple]
    members: np.ndarray | None = None  # candidates x 9
    bankroll: float | None = None  # B of `robust`; default BANKROLL_MULTIPLE x the fees

    @property
    def n_draws(self) -> int:
        return self.scores.shape[0]

    @property
    def fees(self) -> float:
        return float(sum(c.slots * c.kind.fee for c in self.contests))

    @property
    def scale(self) -> float:
        """B of `robust`. A bankroll no larger than the fees makes a total loss infinitely bad (`log(0)`), so
        it is refused rather than clipped."""
        if not self.bankroll:
            return BANKROLL_MULTIPLE * max(self.fees, 1.0)
        if self.bankroll <= self.fees:
            raise ValueError(f"bankroll {self.bankroll:g} does not cover the week's fees ({self.fees:g})")
        return self.bankroll


@dataclass
class Portfolio:
    """The result: `assignment` maps a contest label to the candidate indices in the order they were picked
    (None for a slot no candidate could fill), with the order of every pick and why a slot stayed empty."""

    objective: str
    assignment: dict[str, list[int | None]]
    order: list[tuple[str, int]] = field(default_factory=list)  # (contest label, candidate), pick order
    gains: list[float] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)  # one line per empty slot

    def chosen(self, label: str) -> list[int]:
        return [j for j in self.assignment[label] if j is not None]


def _utility(total: np.ndarray, fees: float, scale: float) -> float:
    return float(np.log1p((total - fees) / scale).mean())


def select(
    problem: Problem,
    objective: str = "edollars",
    *,
    max_lineup_share: float | None = None,
    max_player_share: float | None = None,
    max_overlap: int | None = None,
    allow_repeat: bool = False,
    cap_groups: Sequence[str] = ("gpp",),
) -> Portfolio:
    """Greedy portfolio selection (module docstring): one entry per step, the (contest, lineup) pair with the
    largest marginal gain in `objective`, until every slot is filled or no candidate may take it."""
    if objective not in OBJECTIVES:
        raise ValueError(f"objective {objective!r} is not one of {OBJECTIVES}")
    p = problem
    n_draws = p.n_draws
    fees, scale = p.fees, p.scale
    chosen: dict[str, list[int]] = {c.label: [] for c in p.contests}
    pay = {c.label: np.zeros(n_draws) for c in p.contests}
    hit = {c.label: np.zeros(n_draws, dtype=bool) for c in p.contests}
    total_payout = np.zeros(n_draws)
    open_slots = {c.label: c.slots for c in p.contests}
    out = Portfolio(objective, {c.label: [] for c in p.contests})
    group_slots: dict[str, int] = {}
    for c in p.contests:
        group_slots[c.group] = group_slots.get(c.group, 0) + c.slots
    used: dict[tuple[str, int], int] = {}  # (group, candidate) -> entries
    player_used: dict[tuple[str, int], int] = {}
    by_label = {c.label: c for c in p.contests}

    def allowed(c: Contest) -> np.ndarray:
        ok = c.eligible.copy()
        mine = chosen[c.label]
        if mine and not allow_repeat:
            ok[mine] = False
        if max_lineup_share is not None and c.group in cap_groups:
            cap = max(1, math.ceil(max_lineup_share * group_slots[c.group]))
            for j in np.nonzero(ok)[0]:
                if used.get((c.group, int(j)), 0) >= cap:
                    ok[j] = False
        if p.members is not None:
            if max_player_share is not None and c.group in cap_groups:
                cap = max(1, math.ceil(max_player_share * group_slots[c.group]))
                for j in np.nonzero(ok)[0]:
                    if any(player_used.get((c.group, int(m)), 0) >= cap for m in p.members[j]):
                        ok[j] = False
            if max_overlap is not None and mine:
                for j in np.nonzero(ok)[0]:
                    if any(len(set(p.members[j]) & set(p.members[i])) > max_overlap for i in mine):
                        ok[j] = False
        return ok

    def any_hit(skip: str | None, extra: np.ndarray | None) -> np.ndarray:
        h = np.zeros(n_draws, dtype=bool) if extra is None else extra.copy()
        for label, v in hit.items():
            if label != skip:
                h |= v
        return h

    total_slots = sum(open_slots.values())
    for _ in range(total_slots):
        best: tuple[float, float, str, int] | None = None
        cur_util = _utility(total_payout, fees, scale) if objective == "robust" else 0.0
        cur_any = float(any_hit(None, None).mean()) if objective == "top1" else 0.0
        for c in p.contests:
            if open_slots[c.label] <= 0:
                continue
            cand = np.nonzero(allowed(c))[0]
            if not len(cand):
                continue
            new_pay, new_hit = c.pay_with_each(p.scores, p.keys, chosen[c.label], cand)
            d_pay = new_pay - pay[c.label][:, None]
            e_gain = d_pay.mean(axis=0)
            if objective == "edollars":
                gain = e_gain
                second = np.zeros_like(e_gain)
            elif objective == "top1":
                others = any_hit(c.label, None)
                union = (new_hit | others[:, None]).mean(axis=0)
                gain = union - cur_any
                second = e_gain * TIE
            else:
                gain = np.log1p((total_payout[:, None] + d_pay - fees) / scale).mean(axis=0) - cur_util
                second = np.zeros_like(e_gain)
            k = int(np.argmax(gain + second))
            cand_best = (float(gain[k]), float(second[k]), c.label, int(cand[k]))
            if best is None or (cand_best[0] + cand_best[1]) > (best[0] + best[1]) + 1e-15:
                best = cand_best
        if best is None:
            break
        gain, _, label, j = best
        c = by_label[label]
        chosen[label].append(j)
        pay[label], hit[label] = c.pay(p.scores, p.keys, chosen[label])
        total_payout = sum(pay.values())
        open_slots[label] -= 1
        out.assignment[label].append(j)
        out.order.append((label, j))
        out.gains.append(gain)
        used[(c.group, j)] = used.get((c.group, j), 0) + 1
        if p.members is not None:
            for m in p.members[j]:
                player_used[(c.group, int(m))] = player_used.get((c.group, int(m)), 0) + 1
    for c in p.contests:
        for _ in range(open_slots[c.label]):
            out.assignment[c.label].append(None)
            out.unfilled.append(f"{c.label}: no lineup left that the rules allow")
    return out


# ---------------------------------------------------------------------------------------------------------
# Evaluating an assignment (any assignment: the portfolio, today's picks, what he entered)
# ---------------------------------------------------------------------------------------------------------


def evaluate(problem: Problem, assignment: dict[str, Sequence[int | None]]) -> dict[str, float]:
    """The portfolio's numbers on the problem's draws: `e_dollars`, `profit` (minus fees), `p_any_top1`,
    `e_top1` (expected entries in a top 1%), `log_growth`, `p_profit`, `cvar` (mean profit of the worst 20%
    of draws) and the concentration measures `max_lineup_share` and `max_player_share`."""
    p = problem
    total = np.zeros(p.n_draws)
    union = np.zeros(p.n_draws, dtype=bool)
    fees = 0.0
    e_top = 0.0
    counts: dict[int, int] = {}
    players: dict[int, int] = {}
    entries = 0
    for c in p.contests:
        mine = [j for j in assignment.get(c.label, []) if j is not None]
        pay, h = c.pay(p.scores, p.keys, mine)
        total += pay
        union |= h
        fees += len(mine) * c.kind.fee
        s = p.scores[:, mine] if mine else None
        if s is not None:
            higher = (s[:, None, :] > s[:, :, None]).sum(axis=2)
            e_top += float(((c.place[:, mine] + higher) <= c.top_cut).sum(axis=1).mean())
        for j in mine:
            counts[j] = counts.get(j, 0) + 1
            entries += 1
            if p.members is not None:
                for m in p.members[j]:
                    players[int(m)] = players.get(int(m), 0) + 1
    profit = total - fees
    worst = np.sort(profit)[: max(1, int(0.2 * p.n_draws))]
    return {
        "entries": float(entries),
        "fees": fees,
        "e_dollars": float(total.mean()),
        "profit": float(profit.mean()),
        "p_any_top1": float(union.mean()),
        "e_top1": e_top,
        "log_growth": float(np.log1p(profit / max(p.scale, 1.0)).mean()) if entries else 0.0,
        "p_profit": float((profit > 0).mean()),
        "cvar": float(worst.mean()),
        "max_lineup_share": max(counts.values()) / entries if entries else 0.0,
        "max_player_share": max(players.values()) / entries if players else 0.0,
    }


def ranked_assignment(
    problem: Problem, value: dict[str, np.ndarray], order: Sequence[str] | None = None
) -> dict[str, list[int | None]]:
    """What `entries.suggest_pick` does today, as an assignment: each entry in turn takes the best candidate
    by `value[group]` (a score per candidate, higher is better; the builds' own Cash % for a cash contest, Top
    1% for a GPP) that its contest does not hold yet, so the best lineup lands in every contest."""
    out: dict[str, list[int | None]] = {}
    by_label = {c.label: c for c in problem.contests}
    for label in order or [c.label for c in problem.contests]:
        c = by_label[label]
        v = value[c.group]
        ranked = [int(j) for j in np.argsort(-v, kind="stable") if c.eligible[j]]
        out[label] = [ranked[i] if i < len(ranked) else None for i in range(c.slots)]
    return out
