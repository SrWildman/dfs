"""Portfolio selection on a live week: from the builds and DK's entries file to "which build in which entry".

`dfs lineups suggest --portfolio` calls `plan_portfolio` after the builds exist, saves the plan beside the
week's suggest state (`data/suggest_state/<week>_portfolio.json`), and `dfs entries fill` reads it back as the
grey Suggested hint (`portfolio_suggester`) once the builds are on Lineups. Nothing here writes a sheet, and
nothing is ever assigned for Sam: the plan is a hint, like `entries.suggest_pick`.

**Scope.** The GPP entries only (SE and N-max GPPs): a double-up is single entry, and expected dollars add up
across contests, so joint selection has nothing to add there (`portfolio` module docstring); cash entries keep
today's pick. The field is the saved GPP field model (`stacks_inputs.load_field_model`, `dfs stacks --refit`
once), the draws and the builds' specs are the ones `suggest` just used, and each contest's payout table is
its
series' most recent contest in the history log (`payouts.recent_kinds`); an entry whose series the log has
never seen keeps today's pick, and the plan says so.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import portfolio as pf
from dfs import stacks_inputs
from dfs.contests import series
from dfs.paths import DATA_DIR
from dfs.sim import contest_sim as csim
from dfs.sim import payouts

PLAN_DIR = DATA_DIR / "suggest_state"
FLAG = "portfolio"  # the name `dfs backtest` registers this under (session J's settings registry)
DEFAULT_OBJECTIVE = "edollars"
DEFAULT_SIMS = 4000
WIDE_KEEP = 100  # wide-pool lineups that join the builds (portfolio_retro.WIDE_POOL)
WIDE_SECONDS = 15.0  # the builder's search budget per noisy run
GPP_FORMATS = payouts.GPP_FORMATS


def plan_path(week: int) -> Path:
    return PLAN_DIR / f"{week}_portfolio.json"


@dataclass
class PlannedContest:
    contest_id: str
    name: str
    fee: float
    lineups: list[list[str] | None]  # one per entry, in file order: the build's players (None: not filled)
    builds: list[int | None]  # the build number of each
    why: str = ""  # a contest the plan did not cover: the reason


@dataclass
class PortfolioPlan:
    week: int
    objective: str
    made_at: str
    contests: list[PlannedContest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # the plan against today's pick, on the same draws: the numbers `format_plan` prints
    portfolio: dict[str, float] = field(default_factory=dict)
    today: dict[str, float] = field(default_factory=dict)
    new_lineups: list[list[str]] = field(default_factory=list)  # chosen wide-pool lineups that are not builds

    def for_contest(self, contest_id: str) -> PlannedContest | None:
        return next((c for c in self.contests if c.contest_id == contest_id), None)


def save_plan(plan: PortfolioPlan, path: Path | None = None) -> Path:
    path = path or plan_path(plan.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(plan), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_plan(week: int, path: Path | None = None) -> PortfolioPlan | None:
    path = path or plan_path(week)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PortfolioPlan(**{**raw, "contests": [PlannedContest(**c) for c in raw.get("contests", [])]})
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Contests from the entries file
# ---------------------------------------------------------------------------------------------------------


def contest_kinds(
    entries: Sequence, log: pd.DataFrame, season: int, min_cash: payouts.MinCash | None = None
) -> tuple[dict[str, tuple[str, payouts.ContestKind, int]], dict[str, str]]:
    """`({contest id: (name, kind, entries)}, {contest id: why it is not covered})` for the GPP entries
    in `entries` (`entries.Entry`s). A kind is the series' latest contest in `log` (typed), else, for the same
    fee and format, the latest of those."""
    kinds = payouts.recent_kinds(log, season, min_cash=min_cash)
    by_series = {k.series: k for k in kinds}
    covered: dict[str, tuple[str, payouts.ContestKind, int]] = {}
    skipped: dict[str, str] = {}
    counts: dict[str, int] = {}
    first = {}
    for e in entries:
        if e.format not in GPP_FORMATS:
            continue
        counts[e.contest_id] = counts.get(e.contest_id, 0) + 1
        first.setdefault(e.contest_id, e)
    for cid, e in first.items():
        fee = float(str(e.file.entry_fee).replace("$", "").replace(",", "") or 0)
        key = series.classify(
            "Classic", e.file.contest_name, fee=fee, contest_entries=None, places_paid=None
        ).series
        kind = by_series.get(key)
        if kind is None:
            like = [k for k in kinds if k.format == e.format and abs(k.fee - fee) < 1e-9]
            kind = like[-1] if like else None
        if kind is None:
            skipped[cid] = (
                f"{e.file.contest_name}: no contest like it in your history log, so it keeps today's pick"
            )
            continue
        covered[cid] = (e.file.contest_name, kind, counts[cid])
    return covered, skipped


# ---------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------


def noisy_inputs(
    edge: pd.DataFrame, specs: Mapping[str, object], metric: str, sigma: float, rng: np.random.Generator
) -> tuple[pd.DataFrame, dict]:
    """`edge` and `specs` with every player's projection times `exp(sigma z - sigma^2 / 2)` (mean one), the
    same factor in both (`research/curse.py`'s `noisy_edge`, applied to the projection the builder searches
    on and the one the simulator scores with)."""
    names = edge["Name"].astype(str).tolist()
    factor = dict(zip(names, np.exp(sigma * rng.standard_normal(len(names)) - sigma**2 / 2), strict=True))
    out = edge.copy()
    base = pd.to_numeric(out[metric], errors="coerce")
    scaled = base * pd.Series([factor[n] for n in names], index=out.index)
    out[metric] = scaled.where(base.isna() | (scaled > 0.1), 0.1).where(base.notna())
    noisy = {
        n: dataclasses.replace(s, projection=max(s.projection * factor.get(n, 1.0), 0.1))
        for n, s in specs.items()
    }
    return out, noisy


def wide_lineups(
    edge: pd.DataFrame,
    specs: Mapping[str, object],
    metric: str,
    builder: Callable[[pd.DataFrame, dict], Sequence],
    runs: int,
    seed: int = 0,
) -> list[list]:
    """The GPP lineups (players) the builder chooses on `runs` noisy copies of the projections, alternating
    the curse study's two noise levels: the wide pool the retro's `_wide` arms drew from."""
    from dfs.research.curse import NOISE_SIGMAS

    out, seen = [], set()
    for run in range(runs):
        sigma = NOISE_SIGMAS[run % len(NOISE_SIGMAS)]
        rng = np.random.default_rng([seed, run, int(sigma * 1000)])
        noisy_edge, noisy_specs = noisy_inputs(edge, specs, metric, sigma, rng)
        for b in builder(noisy_edge, noisy_specs):
            key = tuple(sorted(p.name for p in b.players))
            if b.kind == "gpp" and key not in seen:
                seen.add(key)
                out.append(list(b.players))
    return out


def plan_portfolio(
    *,
    week: int,
    builds: Sequence,
    specs: Mapping[str, object],
    edge: pd.DataFrame,
    entries: Sequence,
    log: pd.DataFrame,
    season: int,
    objective: str = DEFAULT_OBJECTIVE,
    n_sims: int = DEFAULT_SIMS,
    seed: int = 0,
    max_lineup_share: float | None = None,
    max_player_share: float | None = None,
    max_overlap: int | None = None,
    min_cash: payouts.MinCash | None = None,
    now: datetime | None = None,
    wide: Sequence[Sequence] = (),
    wide_keep: int = WIDE_KEEP,
) -> PortfolioPlan:
    """Assign the GPP builds (and, with `wide`, the wide pool's lineups) to the GPP entries in `entries`
    (module docstring). `builds` are the report's `Build`s (`kind`, `number`, `players`, `p_gpp`), `specs` the
    simulator specs by player name, `edge` the (framed) edge the builds were scored on, `wide` lineups of
    players from `wide_lineups` (the `wide_keep` best of them by quick E[$] join the candidates). `robust` is
    chosen on shocked draws (`portfolio.shocked_points`), the others on the simulator's own. Today's pick, for
    comparison, uses the builds only. A chosen wide lineup that is not a build is in `plan.new_lineups`."""
    plan = PortfolioPlan(week, objective, (now or datetime.now(UTC)).isoformat(timespec="seconds"))
    model = stacks_inputs.load_field_model()
    if model is None:
        plan.notes.append("no fitted field model yet: run `dfs stacks --refit` once (about two minutes)")
        return plan
    covered, skipped = contest_kinds(entries, log, season, min_cash)
    plan.notes.extend(skipped.values())
    for cid, why in skipped.items():
        plan.contests.append(PlannedContest(cid, why.split(":")[0], 0.0, [], [], why))
    if not covered:
        plan.notes.append("no GPP entries to plan" if not skipped else "no GPP entry the plan can cover")
        return plan
    from dfs import stacks
    from dfs.sim import field_gen

    slate = field_gen.Slate.from_frame(stacks_inputs.slate_frame(edge), own_column="own")
    spec_list = [specs.get(n) for n in slate.names]
    index = {n: i for i, n in enumerate(slate.names)}

    def rated(players) -> list[int] | None:
        idx = [index.get(p.name) for p in players]
        ok = len(idx) == 9 and all(i is not None and spec_list[i] is not None for i in idx)
        return sorted(idx) if ok else None

    cands: list[tuple[tuple[str, ...], list, int | None, float]] = []  # (key, players, build number, p_gpp)
    rows, seen = [], set()
    for b in builds:
        key = tuple(sorted(p.name for p in b.players))
        if b.kind == "gpp" and key not in seen and (idx := rated(b.players)) is not None:
            seen.add(key)
            cands.append((key, list(b.players), b.number, float(b.p_gpp)))
            rows.append(idx)
    if not cands:
        plan.notes.append("no GPP build the simulator can rate: nothing to plan")
        return plan
    n_builds = len(cands)
    chalk = stacks_inputs.top_chalk(slate, model.herd)
    fields = {"gpp": stacks.build_gpp_field(slate, model.rates, model.herd, chalk=chalk, seed=seed).sim}
    kinds = [
        payouts.ContestKind(
            f"{name} #{cid}",
            k.format,
            k.entries,
            k.places_paid,
            k.prize_pool,
            k.fee,
            k.min_cash,
            k.max_entries,
        )
        for cid, (name, k, _n) in covered.items()
    ]
    extra, extra_rows = [], []
    for players in wide:
        key = tuple(sorted(p.name for p in players))
        if key not in seen and (idx := rated(players)) is not None:
            seen.add(key)
            extra.append((key, list(players), None, float("nan")))
            extra_rows.append(idx)
    if extra:
        best = pf.top_by_edollars(
            fields, spec_list, np.array(extra_rows, dtype=int), kinds, wide_keep, seed=seed + 5
        )
        cands += [extra[j] for j in best]
        rows += [extra_rows[j] for j in best]
        plan.notes.append(
            f"wide pool: {len(best)} of {len(extra)} lineups from the noisy runs joined the builds"
        )
    lineups = np.array(rows, dtype=int)
    points = (
        pf.shocked_points(spec_list, n_sims, seed=seed)
        if objective == "robust"
        else csim.draw_points(spec_list, n_sims, seed=seed)
    )
    res = csim.simulate_contests(
        fields, spec_list, lineups, kinds, n_sims=len(points), seed=seed, points=points, keep_draws=True
    )
    every = np.ones(len(cands), dtype=bool)
    only_builds = np.arange(len(cands)) < n_builds
    contests = []
    for (_cid, (_name, _k, n_mine)), k in zip(covered.items(), kinds, strict=True):
        place, copies = res.detail[k.series]
        contests.append(
            pf.Contest(k.series, k, n_mine, place.astype(float), copies.astype(float), every, group="gpp")
        )
    problem = pf.Problem(contests, res.scores.astype(float), [c[0] for c in cands], lineups)
    chosen = pf.select(
        problem,
        objective,
        max_lineup_share=max_lineup_share,
        max_player_share=max_player_share,
        max_overlap=max_overlap,
    )
    narrow = dataclasses.replace(
        problem, contests=[dataclasses.replace(c, eligible=only_builds) for c in problem.contests]
    )
    today = pf.ranked_assignment(narrow, {"gpp": np.nan_to_num(np.array([c[3] for c in cands]), nan=-1.0)})
    plan.portfolio = pf.evaluate(problem, chosen.assignment)
    plan.today = pf.evaluate(problem, today)
    plan.notes.extend(chosen.unfilled)
    for (cid, (name, k, _n)), kind in zip(covered.items(), kinds, strict=True):
        js = chosen.assignment[kind.series]
        plan.contests.append(
            PlannedContest(
                cid,
                name,
                k.fee,
                [[p.name for p in cands[j][1]] if j is not None else None for j in js],
                [cands[j][2] if j is not None else None for j in js],
            )
        )
        for j in js:
            if (
                j is not None
                and cands[j][2] is None
                and [p.name for p in cands[j][1]] not in plan.new_lineups
            ):
                plan.new_lineups.append([p.name for p in cands[j][1]])
    return plan


def add_new_builds(plan: PortfolioPlan, builds: Sequence, scored: Sequence, *, gpp_entries: int) -> list:
    """The plan's wide-pool picks as new GPP builds numbered after `builds` (`scored`: their `lineup_suggest.
    Scored`s, in any order), so Copy builds to Lineups can bring them in; the plan's build numbers are filled
    in to match. Returns the new builds."""
    from dfs import lineup_suggest as ls

    by_key = {tuple(sorted(p.name for p in s.players)): s for s in scored}
    number = max((b.number for b in builds), default=0)
    added, numbers = [], {}
    for names in plan.new_lineups:
        s = by_key.get(tuple(sorted(names)))
        if s is None:
            continue
        number += 1
        b = ls.build_from(number, "gpp", s, ls.stack_name(s.players), gpp_entries=gpp_entries)
        added.append(dataclasses.replace(b, note="portfolio pick (wide pool)"))
        numbers[tuple(sorted(names))] = number
    for c in plan.contests:
        c.builds = [
            numbers.get(tuple(sorted(n)), b) if n is not None and b is None else b
            for n, b in zip(c.lineups, c.builds, strict=True)
        ]
    return added


def format_plan(plan: PortfolioPlan) -> list[str]:
    """The terminal block: the plan by contest, then its numbers beside today's pick on the same draws."""
    lines = [f"Portfolio ({plan.objective}):"]
    for c in plan.contests:
        if c.why:
            continue
        picks = ", ".join(f"build {b}" if b is not None else "(none)" for b in c.builds)
        lines.append(f"  {c.name}: {picks}")
    if plan.portfolio:
        p, t = plan.portfolio, plan.today
        lines.append(
            "  on the model's draws, plan vs today's pick: "
            f"E[$] {p['e_dollars']:.2f} vs {t['e_dollars']:.2f}, "
            f"P(a top-1% finish) {p['p_any_top1']:.1%} vs {t['p_any_top1']:.1%}, "
            f"busiest player in {p['max_player_share']:.0%} vs {t['max_player_share']:.0%} of entries"
        )
        lines.append(
            "  (the model's dollars run high: read them against each other, never as a forecast; "
            "default off until it is proven)"
        )
    lines.extend(f"  note: {n}" for n in plan.notes)
    return lines


# ---------------------------------------------------------------------------------------------------------
# The hint `entries fill` shows
# ---------------------------------------------------------------------------------------------------------


def portfolio_suggester(plan: PortfolioPlan, fallback: Callable):
    """An `entries.Suggester` that hands each GPP entry the plan's next lineup for its contest, once that
    lineup is on Lineups (matched by its nine names); anything else goes to `fallback` (`suggest_pick`) and
    says why."""
    from dfs import entries as ent

    def suggest(entry, lineups, taken):
        planned = plan.for_contest(entry.contest_id)
        if planned is None or planned.why or entry.format not in GPP_FORMATS:
            return fallback(entry, lineups, taken)
        on_sheet = {tuple(sorted(ln.names)): ln for ln in lineups}
        missing = False
        for names, build in zip(planned.lineups, planned.builds, strict=True):
            if names is None:
                continue
            ln = on_sheet.get(tuple(sorted(names)))
            if ln is None:
                missing = True
            elif ln.number not in taken:
                return ent.Suggestion(ln.number, f"portfolio: build {build}")
        base = fallback(entry, lineups, taken)
        if not missing:  # every planned lineup is on Lineups and already in this contest
            return base
        why = "the portfolio's lineups are not on Lineups yet (Copy builds to Lineups)"
        return ent.Suggestion(base.lineup, f"{base.why}; {why}" if base.lineup else why)

    return suggest
