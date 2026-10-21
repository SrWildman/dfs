"""The portfolio retro: does choosing a week's lineups as a set pay, in the real contests they went into?

Per week with real standings (2026 Weeks 2, 4, 5, 6; a week joins when its standings are downloaded), every
real contest of Sam's is replayed with his real entry counts. Ways to fill those entries are compared on what
they would have been paid:

- `sam`: the lineups he actually entered;
- `today`: what the tool does now (`entries.suggest_pick`): each entry takes the best build its contest does
  not hold, by the build's own Top 1% (GPP) or Cash % (cash), so the best build lands in every contest;
- `edollars` / `top1` / `robust` / `robust_tight`: `portfolio.select` on the same builds, by each objective
  (`robust_tight` is `robust` with a bankroll of `ROBUST_TIGHT` weeks of fees, so it diversifies harder);
  `edollars_capped`: `edollars` with a player cap and a lineup cap (`CAP_PLAYER`, `CAP_LINEUP`);
- the `_wide` variants: the same, from a wider pool (today's GPP builds plus the `WIDE_POOL` best of the
  builder's runs on noisy projections, `load_noise_builds`).

**Candidates.** Today's builder's builds for the week as they stood at the lock (the optimizer's-curse study
saved them: `research/curse.py`'s `today` jobs, 20 cash and up to 20 GPP builds on the constructed pool). Cash
builds may go to cash contests, GPP builds to GPP contests. Sam's own lineups are simulated beside them (so
his arm has model numbers) but never chosen.

**Draws and fields.** Exactly `contest_retro.retro_week`'s: pre-lock projections, ownership and salaries, the
generated field per format with the herd and construction rates fitted on the OTHER weeks, payout tables from
the log. Every arm is **chosen** on one set of `N_SIMS` draws and **reported** on a fresh set of the same size
(the field's sampler is shared), so the model numbers below are not flattered by the selection. `nominal`
draws are the simulator's own; `shocked` draws (`robust`'s) move every player's mean by `exp(s_p z - s_p^2/2)`
and every offense's by `exp(s_t z - s_t^2/2)` (`SHOCK_PLAYER`, `SHOCK_TEAM`: assumptions, not fitted;
`curse.NOISE_SIGMAS` is where 0.10 / 0.20 come from), in `WORLDS` blocks, the field scored on the same shocked
outcomes. Nothing here uses the contest itself.

**Realized.** The lineup's DraftKings points (the contests' own player tables) placed among the real field
with
Sam's own entries removed from it, then his entries replaced by the arm's: each entry's place counts the
field's entries that scored more and the arm's other entries that did, its copies the real identical entries
of OTHER users plus the arm's own identical ones, paid from the contest's table (`portfolio.Contest.pay`'s
arithmetic on real scores). Besides dollars: entries in the top 1% / top 10% / paid places and the mean finish
as a share of the field, which are smoother than dollars.

Local and read-only. The honest limit: Sam's contests number about ten a week for three or four weeks, GPP
payouts are lumpy, and one week is one draw of the slate: the realized comparison is mostly noise, and the
report says where it is.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import portfolio as pf
from dfs.contests import history
from dfs.player_join import normalize_name
from dfs.sim import contest_retro as cr
from dfs.sim import contest_sim as csim
from dfs.sim import dups, field_gen, payouts

CURSE_JOBS = Path("data/research/curse/jobs")
N_SIMS = 8000
WORLDS = 8
WIDE_POOL = 100  # noise-run GPP lineups added to the wide pool, best by quick E[$]
SHOCK_PLAYER = 0.20
SHOCK_TEAM = 0.10
ROBUST_TIGHT = (
    2.0  # B of the `robust_tight` arm, in weeks of fees (the default is portfolio.BANKROLL_MULTIPLE)
)
ARMS = (
    "sam", "today", "edollars", "edollars_capped", "top1", "robust", "robust_tight",
    "edollars_wide", "top1_wide", "robust_wide",
)  # fmt: skip
CAPPED = "edollars_capped"
# the caps of the `edollars_capped` arm: no player in over 75% of the GPP entries and no lineup in over 40%
CAP_LINEUP = 0.4
CAP_PLAYER = 0.75
_ID = re.compile(r"\s+")


# ---------------------------------------------------------------------------------------------------------
# One week's setup
# ---------------------------------------------------------------------------------------------------------


@dataclass
class WeekSetup:
    week: int
    contests: list  # RealContest, the week's
    kinds: list[payouts.ContestKind]  # unique labels, aligned with `contests`
    slate: field_gen.Slate
    specs: list
    fields: dict
    index: dict[str, int]


def contest_kinds(week_cs: list, min_cash: payouts.MinCash) -> list[payouts.ContestKind]:
    out = []
    for c in week_cs:
        k = c.kind
        mc = min_cash.lookup(c.series, c.fee, c.format) if k.format in payouts.GPP_FORMATS else None
        out.append(
            payouts.ContestKind(
                f"{k.series} #{c.contest_id}", k.format, k.entries, k.places_paid, k.prize_pool, k.fee, mc,
                k.max_entries,
            )
        )  # fmt: skip
    return out


def week_setup(
    week: int,
    contests: list,
    *,
    keys: dict[int, list[tuple[str, ...]]],
    chalks: dict[int, np.ndarray],
    min_cash: payouts.MinCash,
    seed: int = 0,
) -> WeekSetup:
    """The week's slate, specs and generated fields, fitted on the other weeks (`contest_retro.retro_week`'s
    first half)."""
    week_cs = [c for c in contests if c.week == week]
    frame = cr.week_slate_frame(week)
    edge = cr.pre_lock_edge(week)
    slate = cr.slate_for(frame, "tffb")
    specs = cr.slate_specs(slate, frame, edge)
    lead = cr.fit_lead_decay(cr.lead_ranks(contests, keys), exclude_week=week)
    herd_keys = {w: set(v) for w, v in keys.items()}
    fields = {}
    for fmt in sorted({c.fmt for c in week_cs}):
        rates = cr.rates_for(contests, fmt, exclude_week=week, herd_keys=herd_keys)
        h = cr.fit_herd(contests, keys, fmt, exclude_week=week)
        herd = field_gen.HerdModel(share=h.share, decay=h.decay, k=h.k, lead_decay=lead)
        targets = slate.targets(dups.double_up_ownership if fmt == "cash" else None)
        fields[fmt] = csim.build_field(
            slate, rates, herd, fmt=fmt, chalk=chalks[week], targets=targets, seed=seed
        )
    return WeekSetup(
        week, week_cs, contest_kinds(week_cs, min_cash), slate, specs, fields,
        {n: i for i, n in enumerate(slate.names)},
    )  # fmt: skip


# ---------------------------------------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Candidates:
    lineups: np.ndarray  # n x 9 sorted slate indices
    keys: list[tuple[str, ...]]  # sorted slate names
    source: list[str]  # "build_cash" / "build_gpp" / "noise_gpp" / "sam"
    p_cash: np.ndarray  # the builder's own shown Cash %  (NaN where it is not a build of the week's own run)
    p_gpp: np.ndarray

    def mask(self, *sources: str) -> np.ndarray:
        return np.array([s in sources for s in self.source])


def load_builds(week: int, folder: Path = CURSE_JOBS) -> list[dict]:
    path = folder / f"w{week:02d}_today.json"
    return json.loads(path.read_text())["builds"]


def load_noise_builds(week: int, folder: Path = CURSE_JOBS) -> list[dict]:
    """The GPP builds the builder produced on the noisy-projection copies of the week (the curse study's
    `noise_*` jobs: pre-lock, legal, differing from today's builds): a wider pool for the portfolio."""
    out = []
    for path in sorted(folder.glob(f"w{week:02d}_noise_*.json")):
        out += [b for b in json.loads(path.read_text())["builds"] if b["kind"] == "gpp"]
    return out


def sam_lineups(setup: WeekSetup, me: str = cr.ME) -> dict[str, list[tuple[str, ...]]]:
    """Sam's real entries per contest id: each entry's normalised sorted names, in standings order."""
    out = {}
    for c in setup.contests:
        rows = c.standings.lineups
        mine = rows["entry_name"].astype(str).str.casefold() == me
        out[c.contest_id] = [
            tuple(sorted(normalize_name(n) for _, n in slots)) for slots in rows.loc[mine, "slots"]
        ]
    return out


def candidates(
    setup: WeekSetup,
    builds: list[dict],
    sam: dict[str, list[tuple[str, ...]]],
    noise: list[dict] | None = None,
) -> Candidates:
    """The builds the slate can rate (every player on the slate with a spec), then Sam's lineups, then the
    noise-run GPP builds, each lineup once and under the first source that has it."""
    rows: dict[tuple[str, ...], tuple[str, float, float]] = {}

    def usable(names: tuple[str, ...]) -> np.ndarray | None:
        idx = [setup.index.get(n) for n in names]
        if len(idx) != 9 or any(i is None or setup.specs[i] is None for i in idx):
            return None
        return np.sort(np.array(idx))

    for b in builds:
        key = tuple(sorted(normalize_name(n) for n in b["names"]))
        if key not in rows and usable(key) is not None:
            rows[key] = (f"build_{b['kind']}", float(b["p_cash"]), float(b["p_gpp"]))
    for entries in sam.values():
        for key in entries:
            if key not in rows and usable(key) is not None:
                rows[key] = ("sam", float("nan"), float("nan"))
    for b in noise or []:
        key = tuple(sorted(normalize_name(n) for n in b["names"]))
        if key not in rows and usable(key) is not None:
            rows[key] = ("noise_gpp", float("nan"), float("nan"))
    keys = list(rows)
    idx = np.array([usable(k) for k in keys], dtype=int).reshape(-1, 9)
    return Candidates(
        idx,
        keys,
        [r[0] for r in rows.values()],
        np.array([r[1] for r in rows.values()]),
        np.array([r[2] for r in rows.values()]),
    )


def prefilter(
    setup: WeekSetup, cands: Candidates, keep: int, *, n_sims: int = 2000, seed: int = 0
) -> Candidates:
    """Today's builds and Sam's lineups, plus the `keep` noise-run lineups with the highest mean E[$] over the
    week's GPP contests on a quick simulation: the candidate generation a portfolio search starts from."""
    noise = np.nonzero(cands.mask("noise_gpp"))[0]
    if len(noise) <= keep:
        return cands
    gpp = [k for c, k in zip(setup.contests, setup.kinds, strict=True) if c.fmt == "gpp"]
    res = csim.simulate_contests(
        setup.fields,
        setup.specs,
        cands.lineups[noise],
        gpp,
        n_sims=n_sims,
        seed=seed + 5,
        labels=[str(j) for j in noise],
    )
    mean = res.table.groupby("lineup")["e_dollars"].mean()
    best = {int(j) for j in mean.sort_values(ascending=False).index[:keep]}
    chosen = [j for j in range(len(cands.keys)) if j not in set(noise.tolist()) or j in best]
    return Candidates(
        cands.lineups[chosen],
        [cands.keys[j] for j in chosen],
        [cands.source[j] for j in chosen],
        cands.p_cash[chosen],
        cands.p_gpp[chosen],
    )


# ---------------------------------------------------------------------------------------------------------
# Draws and problems
# ---------------------------------------------------------------------------------------------------------


def shocked_specs(specs: list, rng: np.random.Generator, sigma_player: float, sigma_team: float) -> list:
    """The specs with every projection scaled by `exp(s_p z - s_p^2 / 2)` per player and `exp(s_t z - s_t^2 /
    2)` per offense (a defense takes no team shock): one world in which the projections were wrong the way
    a slate's can be, together."""
    teams = sorted({s.team for s in specs if s is not None})
    team = {t: float(np.exp(sigma_team * rng.standard_normal() - sigma_team**2 / 2)) for t in teams}
    out = []
    for s in specs:
        if s is None:
            out.append(None)
            continue
        f = float(np.exp(sigma_player * rng.standard_normal() - sigma_player**2 / 2))
        if s.position != "DST":
            f *= team[s.team]
        out.append(dataclasses.replace(s, projection=max(s.projection * f, 0.1)))
    return out


def nominal_points(specs: list, n_sims: int = N_SIMS, seed: int = 0) -> np.ndarray:
    return csim.draw_points(specs, n_sims, seed=seed)


def shocked_points(
    specs: list,
    n_sims: int = N_SIMS,
    worlds: int = WORLDS,
    seed: int = 0,
    sigma_player: float = SHOCK_PLAYER,
    sigma_team: float = SHOCK_TEAM,
) -> np.ndarray:
    """`n_sims` draws in `worlds` equal blocks, each block under its own shocked projections."""
    rng = np.random.default_rng(seed + 7)
    per_world = n_sims // worlds
    parts = [
        csim.draw_points(shocked_specs(specs, rng, sigma_player, sigma_team), per_world, seed=seed + 100 + w)
        for w in range(worlds)
    ]
    return np.concatenate(parts)


def build_problem(
    setup: WeekSetup,
    cands: Candidates,
    sam: dict[str, list[tuple[str, ...]]],
    points: np.ndarray,
    seed: int = 0,
) -> pf.Problem:
    """The portfolio problem on `points`: every contest of the week with Sam's real entry count in it, today's
    builds the eligible lineups (`with_pool` widens it)."""
    res = csim.simulate_contests(
        setup.fields,
        setup.specs,
        cands.lineups,
        setup.kinds,
        n_sims=len(points),
        seed=seed,
        points=points,
        keep_draws=True,
    )
    cash_ok = cands.mask("build_cash")
    gpp_ok = cands.mask("build_gpp")
    contests = []
    for c, k in zip(setup.contests, setup.kinds, strict=True):
        n_mine = len(sam[c.contest_id])
        if n_mine == 0:
            continue
        place, copies = res.detail[k.series]
        contests.append(
            pf.Contest(
                k.series, k, n_mine, place, copies, cash_ok if c.fmt == "cash" else gpp_ok, group=c.fmt
            )
        )
    return pf.Problem(contests, res.scores, list(cands.keys), cands.lineups)


def with_pool(problem: pf.Problem, cands: Candidates, *, wide: bool) -> pf.Problem:
    """The same problem with the eligible lineups today's builds (`wide` False) or those plus the noise-run
    GPP builds (GPP contests only)."""
    cash_ok = cands.mask("build_cash")
    gpp_ok = cands.mask("build_gpp", "noise_gpp") if wide else cands.mask("build_gpp")
    return dataclasses.replace(
        problem,
        contests=[
            dataclasses.replace(c, eligible=cash_ok if c.group == "cash" else gpp_ok)
            for c in problem.contests
        ],
    )


# ---------------------------------------------------------------------------------------------------------
# Realized
# ---------------------------------------------------------------------------------------------------------


@dataclass
class RealField:
    """A real contest with Sam's own entries taken out: what any arm's entries are placed among."""

    points: np.ndarray  # others' scores, descending
    copies: collections.Counter  # others' identical lineups
    cum: np.ndarray
    n: int
    cut: float


def real_field(c, kind: payouts.ContestKind, ME: str = cr.ME) -> RealField:
    rows = c.standings.lineups
    mine = (rows["entry_name"].astype(str).str.casefold() == ME).to_numpy()
    others = rows[~mine]
    cp = collections.Counter(tuple(sorted(normalize_name(n) for _, n in slots)) for slots in others["slots"])
    return RealField(
        np.sort(others["points"].to_numpy(float))[::-1], cp,
        csim._cumulative(payouts.payout_table(kind)),  # noqa: SLF001
        c.entries, max(1.0, csim.TOP_FRACTION * c.entries),
    )  # fmt: skip


def realized_set(
    rf: RealField, entries: list[tuple[str, ...]], points: list[float]
) -> tuple[float, int, list[int]]:
    """`(dollars, entries in the top 1%, places)` of `entries` (names) with `points` each, placed among the
    real field and each other."""
    if not entries:
        return 0.0, 0, []
    p = np.asarray(points, float)
    field_above = np.array([(rf.points > x + 1e-9).sum() for x in p], float)
    mine_above = np.array([(p > x + 1e-9).sum() for x in p], float)
    same = np.array([sum(e == f for f in entries) - 1 for e in entries], float)
    copies = np.array([rf.copies.get(e, 0) for e in entries], float) + same
    place = 1.0 + field_above + mine_above
    pay = csim.block_payout(rf.cum, place, copies)
    return float(pay.sum()), int((place <= rf.cut).sum()), [int(x) for x in place]


# ---------------------------------------------------------------------------------------------------------
# A week
# ---------------------------------------------------------------------------------------------------------


@dataclass
class WeekResult:
    week: int
    rows: pd.DataFrame  # one row per arm per contest: realized dollars, hits, finish, model E[$]
    model: pd.DataFrame  # one row per arm: the portfolio numbers on selection and on fresh draws
    exposure: pd.DataFrame  # one row per arm: how concentrated its entries were
    picks: dict[str, dict[str, list[str]]]  # arm -> contest label -> lineups
    n_candidates: int
    seconds: float


def _contest_id(setup: WeekSetup, label: str) -> str:
    return next(c.contest_id for c, k in zip(setup.contests, setup.kinds, strict=True) if k.series == label)


def arm_assignments(
    sel_nom: pf.Problem,
    sel_shk: pf.Problem,
    cands: Candidates,
    sam_assign: dict[str, list[int | None]],
    *,
    wide: bool = True,
) -> dict[str, dict[str, list[int | None]]]:
    """Every arm's assignment, chosen on the selection draws only."""
    narrow_nom = with_pool(sel_nom, cands, wide=False)
    narrow_shk = with_pool(sel_shk, cands, wide=False)
    value = {"cash": np.nan_to_num(cands.p_cash, nan=-1.0), "gpp": np.nan_to_num(cands.p_gpp, nan=-1.0)}
    out = {
        "sam": sam_assign,
        "today": pf.ranked_assignment(narrow_nom, value),
        "edollars": pf.select(narrow_nom, "edollars").assignment,
        CAPPED: pf.select(
            narrow_nom, "edollars", max_lineup_share=CAP_LINEUP, max_player_share=CAP_PLAYER
        ).assignment,
        "top1": pf.select(narrow_nom, "top1").assignment,
        "robust": pf.select(narrow_shk, "robust").assignment,
        "robust_tight": pf.select(
            dataclasses.replace(narrow_shk, bankroll=ROBUST_TIGHT * narrow_shk.fees), "robust"
        ).assignment,
    }
    if wide:
        wide_nom = with_pool(sel_nom, cands, wide=True)
        wide_shk = with_pool(sel_shk, cands, wide=True)
        out["edollars_wide"] = pf.select(wide_nom, "edollars").assignment
        out["top1_wide"] = pf.select(wide_nom, "top1").assignment
        out["robust_wide"] = pf.select(wide_shk, "robust").assignment
    return out


def run_week(
    setup: WeekSetup,
    builds: list[dict],
    noise: list[dict] | None = None,
    *,
    n_sims: int = N_SIMS,
    seed: int = 0,
    wide: int = WIDE_POOL,
    log=print,
) -> WeekResult:
    """One week: select on one set of draws, report every arm on a fresh set (and in sample), and replay each
    arm's entries in the real contests."""
    import time

    t0 = time.perf_counter()
    sam = sam_lineups(setup)
    cands = candidates(setup, builds, sam, noise if wide else None)
    if wide:
        cands = prefilter(setup, cands, wide, seed=seed)
    fresh = seed + 1000
    probs = {
        "sel_nom": build_problem(setup, cands, sam, nominal_points(setup.specs, n_sims, seed), seed),
        "sel_shk": build_problem(setup, cands, sam, shocked_points(setup.specs, n_sims, seed=seed), seed),
        "new_nom": build_problem(setup, cands, sam, nominal_points(setup.specs, n_sims, fresh), fresh),
        "new_shk": build_problem(setup, cands, sam, shocked_points(setup.specs, n_sims, seed=fresh), fresh),
    }
    log(f"  week {setup.week}: {len(cands.keys)} candidates, {len(probs['sel_nom'].contests)} contests")
    key_index = {k: i for i, k in enumerate(cands.keys)}
    sel_nom = probs["sel_nom"]
    sam_assign = {
        c.label: [key_index.get(k) for k in sam[_contest_id(setup, c.label)]] for c in sel_nom.contests
    }
    assigns = arm_assignments(sel_nom, probs["sel_shk"], cands, sam_assign, wide=bool(wide))

    points = {c.contest_id: dict(c.fpts) for c in setup.contests}
    fields = {c.contest_id: real_field(c, k) for c, k in zip(setup.contests, setup.kinds, strict=True)}
    contest_of = {c.contest_id: c for c in setup.contests}
    kind_of = {k.series: k for k in setup.kinds}
    by_label = {name: {c.label: c for c in p.contests} for name, p in probs.items()}
    rows, model_rows, exp_rows = [], [], []
    for arm, assign in assigns.items():
        for label, js in assign.items():
            cid = _contest_id(setup, label)
            c, kind = contest_of[cid], kind_of[label]
            mine = [j for j in js if j is not None]
            names = sam[cid] if arm == "sam" else [cands.keys[j] for j in mine]
            pts = [sum(points[cid].get(n, 0.0) for n in e) for e in names]
            dollars, top, places = realized_set(fields[cid], names, pts)
            pay_new, _ = by_label["new_nom"][label].pay(probs["new_nom"].scores, cands.keys, mine)
            pay_shk, _ = by_label["new_shk"][label].pay(probs["new_shk"].scores, cands.keys, mine)
            rows.append(
                {
                    "week": setup.week,
                    "arm": arm,
                    "contest": label,
                    "contest_id": cid,
                    "fmt": c.fmt,
                    "format": c.format,
                    "entries_field": c.entries,
                    "fee": kind.fee,
                    "mine": len(names),
                    "fees": len(names) * kind.fee,
                    "realized": dollars,
                    "profit": dollars - len(names) * kind.fee,
                    "top1": top,
                    "top10": sum(p <= 0.10 * c.entries for p in places),
                    "paid": sum(p <= c.places_paid for p in places),
                    "pct_sum": float(sum(p / c.entries for p in places)),
                    "model_e": float(pay_new.mean()),
                    "model_e_shocked": float(pay_shk.mean()),
                }
            )
        row = {"week": setup.week, "arm": arm}
        for tag, name in (("ins", "sel_nom"), ("nom", "new_nom"), ("shk", "new_shk"), ("shs", "sel_shk")):
            row |= {f"{tag}_{k}": v for k, v in pf.evaluate(probs[name], assign).items()}
        model_rows.append(row)
        exp_rows.append(
            {"week": setup.week, "arm": arm}
            | _real_exposure(assign, cands, sam if arm == "sam" else None, setup)
        )
    picks = {
        arm: {
            label: [" / ".join(cands.keys[j]) if j is not None else "-" for j in js]
            for label, js in assign.items()
        }
        for arm, assign in assigns.items()
    }
    return WeekResult(
        setup.week,
        pd.DataFrame(rows),
        pd.DataFrame(model_rows),
        pd.DataFrame(exp_rows),
        picks,
        len(cands.keys),
        time.perf_counter() - t0,
    )


def _real_exposure(assign, cands: Candidates, sam, setup: WeekSetup) -> dict[str, float]:
    """Over the arm's GPP entries: the entries, how many are distinct lineups, the largest player share of
    entries and the mean number of players two entries of the same contest share."""
    lineups, overlaps = [], []
    for label, js in assign.items():
        cid = _contest_id(setup, label)
        if next(x for x in setup.contests if x.contest_id == cid).fmt != "gpp":
            continue
        names = sam[cid] if sam is not None else [cands.keys[j] for j in js if j is not None]
        lineups += names
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                overlaps.append(len(set(names[a]) & set(names[b])))
    players = collections.Counter(n for e in lineups for n in e)
    return {
        "gpp_entries": len(lineups),
        "gpp_distinct": len(set(lineups)),
        "gpp_top_player_share": max(players.values()) / len(lineups) if lineups else 0.0,
        "gpp_overlap": float(np.mean(overlaps)) if overlaps else float("nan"),
    }


# ---------------------------------------------------------------------------------------------------------
# The whole retro and its summary
# ---------------------------------------------------------------------------------------------------------


def run_retro(
    weeks: list[int] | None = None,
    *,
    n_sims: int = N_SIMS,
    seed: int = 0,
    wide: int = WIDE_POOL,
    log=print,
) -> list[WeekResult]:
    contests = cr.load_real_contests()
    chalks, keys = {}, {}
    for wk in sorted({c.week for c in contests}):
        slate = cr.slate_for(cr.week_slate_frame(wk), "tffb")
        chalks[wk] = field_gen.chalk_lineups(slate)
        keys[wk] = cr.chalk_keys(slate, chalks[wk])
    mc = payouts.fit_min_cash(
        payouts.paid_entries(history.typed(history.read_log("data/contests/contest_log.csv")))
    )
    out = []
    for wk in weeks or sorted({c.week for c in contests}):
        if not (CURSE_JOBS / f"w{wk:02d}_today.json").exists():
            log(f"  week {wk}: no saved builds (run the curse study's `today` job); skipped")
            continue
        setup = week_setup(wk, contests, keys=keys, chalks=chalks, min_cash=mc, seed=seed)
        noise = load_noise_builds(wk) if wide else None
        r = run_week(setup, load_builds(wk), noise, n_sims=n_sims, seed=seed, wide=wide, log=log)
        log(f"  week {wk}: {r.seconds:.0f}s")
        out.append(r)
    return out


def paired(per_week: pd.DataFrame, column: str, arm: str, base: str = "today") -> dict:
    """`arm` minus `base` on `column` (one row per week and arm): mean, standard error, weeks won."""
    t = per_week.pivot(index="week", columns="arm", values=column)
    d = (t[arm] - t[base]).dropna()
    n = len(d)
    se = float(d.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    mean = float(d.mean()) if n else float("nan")
    return {
        "weeks": n,
        "mean": mean,
        "se": se,
        "z": mean / se if se and np.isfinite(se) and se > 0 else float("nan"),
        "won": int((d > 0).sum()),
        "by_week": {int(w): float(v) for w, v in d.items()},
    }


def week_table(results: list[WeekResult]) -> pd.DataFrame:
    """One row per week and arm: realized profit and ROI, entries, finish, hits, the model's E[$]."""
    rows = pd.concat([r.rows for r in results], ignore_index=True)
    out = (
        rows.groupby(["week", "arm"])
        .agg(
            entries=("mine", "sum"),
            fees=("fees", "sum"),
            realized=("realized", "sum"),
            profit=("profit", "sum"),
            top1=("top1", "sum"),
            top10=("top10", "sum"),
            paid=("paid", "sum"),
            pct_sum=("pct_sum", "sum"),
            model_e=("model_e", "sum"),
            model_e_shocked=("model_e_shocked", "sum"),
        )
        .reset_index()
    )
    out["roi"] = out["profit"] / out["fees"]
    out["finish"] = out["pct_sum"] / out["entries"]  # mean finish as a share of the field: lower is better
    return out
