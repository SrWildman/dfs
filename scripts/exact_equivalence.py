"""Blueprint Phase E's proof: the old search run to completion, the exact search and the HiGHS checker on the
same weeks, shape by shape.

    python scripts/exact_equivalence.py --weeks 2,4,5,6,7,8,9 --out data/research/exact
    python scripts/exact_equivalence.py --weeks 2,4,5,6,7,8,9 --out data/research/exact --report

The first form runs one week x solver per process (`--solvers`, default all three) and saves what each found
(`<out>/w<NN>-<solver>.json`); a run that is already saved is skipped. `--report` prints the tables from the
saved runs: per shape the best projection sum by each solver, whether the top-K agree up to ties, and the
time each took.

Each week is built as `dfs backtest` builds it (the last pre-lock snapshot, Sam's saved pool where the week
has one, else the constructed pool): cash is the best `CASH_TOP_K` lineups, then every GPP shape of `GPP_PLAN`
for every quarterback. The old search gets no budget (`deadline=None`), so it finishes. The contrarian cut
depends on the stacks the earlier shapes found, and those can differ between solvers by a tie, so every solver
is given the cut the old search arrived at: the three then search the same question.

"Up to ties" is checked per quarterback: the three lists of (projection sum, salary) must be identical, so a
lineup one solver has and another has not is always matched by one of the same projection and the same price.

Files only: reads `data/`, writes `<out>`. No sheet.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from collections import Counter, defaultdict
from pathlib import Path

SOLVERS = ("search", "exact", "milp")
LABEL = {"search": "old search, finished", "exact": "exact", "milp": "MILP (HiGHS)"}
CASH = "cash"


def run(args: tuple[int, str, str]) -> str:
    week, solver, out = args
    from dfs import backtest, build_exact
    from dfs import backtest_settings as bs
    from dfs import lineup_shapes as sh
    from dfs import lineup_suggest as ls
    from dfs import search_clock as clock
    from dfs.research import curse

    path = Path(out) / f"w{week:02d}-{solver}.json"
    if path.exists():
        return f"week {week} {solver}: saved"
    reference = Path(out) / f"w{week:02d}-search.json"
    if solver != "search" and not reference.exists():
        return f"week {week} {solver}: SKIPPED, run the old search first (it sets the contrarian cut)"
    wi = curse.Study([week]).weeks[week]
    ctx = backtest.prepare_context(backtest.make_context(wi, bs.Settings()))
    pools = {
        kind: ls.pool_for(ctx.edge, ctx.tags, tags, now=ctx.now, metric=ctx.metric, any_dst=True)
        for kind, tags in (("cash", ls.CASH_TAGS), ("gpp", ls.GPP_TAGS))
    }
    shares = ls.ownership_shares(ctx.specs, ctx.edge, ctx.lines.field, "top")
    leaf = ls.not_all_cash(ctx.tags)
    result = {
        "week": week,
        "solver": solver,
        "pool": next((n[6:] for n in ctx.notes if n.startswith("pool: ")), ""),
        "players": {kind: len(pool) for kind, pool in pools.items()},
        "shapes": {},
        "seconds": {},
        "nodes": {},
        "complete": {},
    }

    def record(name: str, lineups: list, done: bool, started: float, nodes: int) -> None:
        result["shapes"][name] = [
            {
                "names": sorted(p.name for p in lu),
                "qb": next(p.name for p in lu if p.position == "QB"),
                "proj": round(sum(p.value for p in lu), 6),
                "salary": sum(p.salary for p in lu),
            }
            for lu in lineups
        ]
        result["seconds"][name] = round(time.monotonic() - started, 2)
        result["nodes"][name] = clock.total_nodes() - nodes
        result["complete"][name] = bool(done)

    with build_exact.use(solver):
        t, n = time.monotonic(), clock.total_nodes()
        lineups, done = ls.cash_lineups(pools["cash"], cap=ctx.cap, deadline=None)
        record(CASH, lineups, done, t, n)
        found: dict[frozenset[str], list] = {}
        for shape, per_qb, _ in ls.GPP_PLAN:
            kwargs = {}
            if shape == sh.CONTRARIAN:
                if solver == "search":
                    cut = sh.contrarian_cutoff(list(found.values()), shares) if shares is not None else None
                else:
                    cut = json.loads(reference.read_text()).get("contrarian_cut")
                result["contrarian_cut"] = cut
                if cut is None:
                    continue
                kwargs = {"own": shares, "max_stack_own": cut}
            t, n = time.monotonic(), clock.total_nodes()
            lineups, done = sh.shape_lineups(
                pools["gpp"], shape, cap=ctx.cap, per_qb=per_qb, deadline=None, leaf_ok=leaf, **kwargs
            )
            record(shape, lineups, done, t, n)
            for lu in lineups:
                found.setdefault(frozenset(p.name for p in lu), lu)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    return f"week {week} {solver}: {sum(result['seconds'].values()):.0f} s"


def by_qb(lineups: list[dict], whole: bool) -> dict[str, list[tuple[float, int]]]:
    """(projection sum, salary) of every lineup, best first, per quarterback (`whole`: one list, the cash
    search is not per quarterback)."""
    groups: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for lu in lineups:
        groups["" if whole else lu["qb"]].append((lu["proj"], lu["salary"]))
    return {qb: sorted(v, key=lambda t: (-t[0], t[1])) for qb, v in groups.items()}


def compare(ref: list[dict], other: list[dict], whole: bool) -> tuple[bool, int]:
    """(the two agree up to ties, lineups one has and the other has not)."""
    same = by_qb(ref, whole) == by_qb(other, whole)
    a, b = {tuple(lu["names"]) for lu in ref}, {tuple(lu["names"]) for lu in other}
    return same, len(a - b)


def report(weeks: list[int], out: str) -> int:
    runs = {}
    for week in weeks:
        for solver in SOLVERS:
            path = Path(out) / f"w{week:02d}-{solver}.json"
            if path.exists():
                runs[week, solver] = json.loads(path.read_text())
    bad = 0
    print(
        "| week | shape | lineups | old best | exact best | MILP best | exact = old | MILP = old | "
        "tie swaps (exact / MILP) | old s | exact s | MILP s |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for week in weeks:
        ref = runs.get((week, "search"))
        if ref is None:
            print(f"| {week} | (no old-search run saved) |")
            continue
        for shape, lineups in ref["shapes"].items():
            cells = [str(week), shape, str(len(lineups)), f"{max(lu['proj'] for lu in lineups):.1f}"]
            verdicts, swaps, times = [], [], [f"{ref['seconds'][shape]:.1f}"]
            for solver in ("exact", "milp"):
                r = runs.get((week, solver))
                if r is None or shape not in r["shapes"]:
                    cells.append("-")
                    verdicts.append("-")
                    swaps.append("-")
                    times.append("-")
                    continue
                theirs = r["shapes"][shape]
                best = max((lu["proj"] for lu in theirs), default=float("nan"))
                same, differ = compare(lineups, theirs, shape == CASH)
                ok = same and r["complete"][shape] and best >= max(lu["proj"] for lu in lineups) - 1e-6
                bad += not ok
                cells.append(f"{best:.1f}")
                verdicts.append("yes" if ok else "**NO**")
                swaps.append(str(differ))
                times.append(f"{r['seconds'][shape]:.1f}")
            print("| " + " | ".join([*cells, *verdicts, " / ".join(swaps), *times]) + " |")
    print()
    print("| week | pool | old search, finished | exact | MILP (HiGHS) | old search in budget seconds |")
    print("|---|---|---|---|---|---|")
    for week in weeks:
        cells = [str(week), runs[week, "search"]["pool"] if (week, "search") in runs else "-"]
        for solver in SOLVERS:
            r = runs.get((week, solver))
            cells.append("-" if r is None else f"{sum(r['seconds'].values()):.0f} s")
        r = runs.get((week, "search"))
        cells.append("-" if r is None else f"{sum(r['nodes'].values()) / 33_000:.0f}")
        print("| " + " | ".join(cells) + " |")
    incomplete = Counter(s for (_, s), r in runs.items() if not all(r["complete"].values()))
    print(f"\nsearches that did not finish: {dict(incomplete) or 'none'}; disagreements: {bad}")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--weeks", default="2,4,5,6,7,8,9")
    parser.add_argument("--out", default="data/research/exact")
    parser.add_argument("--solvers", default=",".join(SOLVERS))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    weeks = [int(w) for w in args.weeks.split(",")]
    if args.report:
        return report(weeks, args.out)
    solvers = [s for s in SOLVERS if s in args.solvers.split(",")]
    for solver in solvers:  # one solver at a time, so the times of one are not taken under another's load
        jobs = [(w, solver, args.out) for w in weeks]
        with mp.get_context("spawn").Pool(min(args.workers, len(jobs))) as pool:
            for line in pool.imap_unordered(run, jobs):
                print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
