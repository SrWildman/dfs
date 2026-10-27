"""Back-test of late swap against the real field: Weeks 4-6 replayed at the 1 pm ET state.

The question: when late swap scores a lineup's swap options against the line the contest's REAL field is
heading for (`late_swap_field`), do its choices beat the ones it makes against the projected-ownership line
it uses today?

**The state.** At the Sunday lock (1 pm ET) every game that kicked off by then is treated as finished with
its ACTUAL points (the standings' own `FPTS` column); every later game is open, simulated from the last pre-
lock EdgeRaw snapshot (`sim.contest_retro.pre_lock_edge`) and nothing else. A lineup's points so far are the
sum of its started players' actual points. That is the best case for the idea (at 1:30 pm the 1 pm games are
mid-game, with more of the field's score still to come); the live feature handles mid-game by the clock
(`GAME_HOURS`).

**Three ways to draw the line**, each choosing a swap for the same lineups:

- `P` projected: today's late swap. Every player at full-game draws (locked players too), against the
  projected line (`sim.field`: field mean from TFFB's `Own%` + the double-up / top-1% offset).
- `PS` projected + state: the same projected line, but on the state-aware draws (the started games are
  known). Isolates what knowing the score so far is worth.
- `R` real: the contest's own lineups with the early games' points known and the late players simulated; the
  cash line is the score at the contest's last paid place, the top line at the top 1%
  (`late_swap_field.real_lines`).

**The test lineups** are the contest's own FINAL lineups (a sample, plus Sam's own entries), each treated as
if its late slots were still open at 1 pm. The 1 pm lineups of the field are not stored; final ones are the
stand-in, and they already contain whatever late swapping was done, so a swap has less to find than in real
life (the same for all three modes). **The real field is the final one**, which also holds the late swaps
made after 1:30, so it is slightly better informed than a 1:30 export would be.

**The swaps** are the one-for-one improvements by projection (`late_swap_search.one_for_one`, the 12 best
per open slot: what the live command ranks by probability), rated by each mode's change in P(cash) for a
Double Up and in P(top 1%) for a GPP. A mode keeps the lineup when no swap raises that probability. **The
score** is what the swapped lineup actually scored: points, the share that cashed, reached the top 10% and
top 1% of the contest's real final scores, and its finish percentile. Differences are paired by lineup.

**By position (2026-10-27).** For each GPP lineup the real-field mode also gives where it stands at the 1 pm
state (`late_swap_position.lineup_position`: its median gap to the top-1% line in units of that gap's spread,
bucketed `behind` / `near` / `ahead`), and each mode's chosen swap is labelled LEVERAGE / HOLD / EVEN
(`late_swap_position.swap_overlap`: the ownership of the players in against the ones out, by the contest's
real final Own% and, beside it, the projected Own%). `leverage_table` reports, per bucket, how often the
swap that maximises Top 1% is a LEVERAGE one, against the share of all the candidates that are (so a bucket
reads "the objective picked leverage X% of the time from a pool that was Y% leverage").

Run: `python -m dfs.late_swap_retro [--weeks 4,5,6] [--lineups 120] [--sims 10000]`. Offline, read-only.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import late_swap_field as lf
from dfs import sim_inputs
from dfs.config import SimLinesConfig
from dfs.contests import series, week_frames
from dfs.late_swap import lineup_slot_status
from dfs.late_swap_position import EVEN, FINISH_LADDER, HOLD, LEVERAGE, lineup_position, swap_overlap
from dfs.late_swap_search import lineup_state, one_for_one, swap_pool
from dfs.models import ROSTER_SLOTS
from dfs.player_join import normalize_name
from dfs.sim import contest_retro as cr
from dfs.sim.simulate import PlayerSpec

SEASON = cr.SEASON
SALARY_CAP = 50000
ESPN_CACHE = Path("data/research_cache/espn_box")  # final box scores never change; kept (gitignored data/)
SWAPS_PER_SLOT = 12
MODES = ("P", "PS", "PSK", "R")
MODE_NAMES = {
    "P": "projected (today)",
    "PS": "projected + state",
    "PSK": "projected + state, swings x1.5",
    "R": "real field",
}
SWING_FACTOR = (
    1.5  # the real top-1% line swung 1.1-1.7x the projected one's SD in Weeks 5-7 (see leverage notes)
)
GAIN = 1e-9


def ordered_names(slots: tuple[tuple[str, str], ...]) -> list[str] | None:
    """A standings lineup's names in `ROSTER_SLOTS` order (the export lists slots alphabetically)."""
    pools: dict[str, list[str]] = {}
    for slot, name in slots:
        pools.setdefault(slot, []).append(name)
    out = []
    for slot in ROSTER_SLOTS:
        queue = pools.get(slot)
        if not queue:
            return None
        out.append(queue.pop(0))
    return out


@dataclass
class WeekSetup:
    week: int
    edge: pd.DataFrame
    specs: dict[str, PlayerSpec]
    contests: list[cr.RealContest]
    fpts: dict[str, float]  # every player's ACTUAL points: what a lineup is scored with afterwards
    state_fpts: dict[str, float]  # the started players' points the state is built from
    state: lf.SlateState
    draws: lf.SlateDraws
    draws_all: lf.SlateDraws  # every player at full-game draws, the way today's late swap simulates
    projected: lf.LiveLines  # P
    projected_state: lf.LiveLines  # PS


def setup_week(
    week: int, *, n_sims: int, seed: int = lf.SEED, points: str = "actual", espn_cache: Path | None = None
) -> WeekSetup:
    edge = cr.pre_lock_edge(week)
    specs = sim_inputs.build_specs(edge)
    lock = week_frames.lock_utc(SEASON, week)
    contests = cr.load_real_contests({week})
    fpts = cr.week_points(contests, week)
    kickoffs = lf.kickoffs_from_edge(edge)
    state_fpts = fpts
    if points == "espn":  # the no-download state: the started players' points as ESPN's box scores give them
        from dfs import live_scores

        scores = live_scores.fetch_scores(live_scores.kickoff_dates(kickoffs.values()), cache_dir=espn_cache)
        state_fpts = live_scores.points_for_specs(scores, specs.values())
    state = lf.slate_state(specs, kickoffs, lock, state_fpts, game_hours=None, out=lf.unavailable(edge))
    blank = lf.slate_state(specs, {}, lock, {}, game_hours=None)  # nobody started: today's behaviour
    draws = lf.simulate_remaining(state, n_sims=n_sims, seed=seed)
    draws_all = lf.simulate_remaining(blank, n_sims=n_sims, seed=seed)
    lines = SimLinesConfig()
    p_cash, p_top = lf.projected_lines(draws_all, edge, specs, lines)
    ps_cash, ps_top = lf.projected_lines(draws, edge, specs, lines)
    return WeekSetup(
        week,
        edge,
        specs,
        contests,
        fpts,
        state_fpts,
        state,
        draws,
        draws_all,
        lf.LiveLines(draws_all, p_cash, p_top, "projected"),
        lf.LiveLines(draws, ps_cash, ps_top, "projected + state"),
    )


def percentile_of(line: float, draws_line: np.ndarray) -> float:
    """Where the contest's ACTUAL line falls in a mode's per-draw distribution of it (0.5 = the median)."""
    return float((draws_line <= line).mean())


def realised(points: float, c: cr.RealContest) -> dict[str, float]:
    final = c.points  # descending
    profile = c.standings.profile
    place = float((final > points).sum()) + 1
    return {
        "pts": points,
        "cash": float(points >= profile.cash_line),
        "top10": float(points >= profile.top10),
        "top1": float(points >= profile.top1),
        "pct": place / len(final),
    }


def test_lineups(c: cr.RealContest, key_to_name: dict[str, str], n: int, seed: int) -> list[dict]:
    """A sample of the contest's final lineups (plus every entry of Sam's) that resolve to EdgeRaw names."""
    rows = []
    for rec in c.standings.lineups.itertuples(index=False):
        names = ordered_names(rec.slots)
        mapped = None if names is None else [key_to_name.get(normalize_name(x)) for x in names]
        if mapped is None or any(m is None for m in mapped) or len(set(mapped)) != len(mapped):
            continue
        rows.append({"names": mapped, "mine": rec.entry_name == series.MY_USERNAME})
    rng = np.random.default_rng(seed)
    picked = set(rng.permutation(len(rows))[:n].tolist())
    return [r for i, r in enumerate(rows) if i in picked or r["mine"]]


def best_swap(
    live: lf.LiveLines, lineup: list[PlayerSpec], swaps: list, specs: dict[str, PlayerSpec], goal: str
):
    """`(change in the goal probability, swap, names out, specs in)` for the swap that raises it most (and by
    more than noise), or None. A swap whose incoming player the draws do not hold is not rated."""
    rated = []
    for swap in swaps:
        out = [old.name for old, _ in swap.changes.values() if old is not None]
        ins = [new.name for _, new in swap.changes.values()]
        if all(n in live.draws.column for n in ins):
            rated.append((swap, out, [specs[n] for n in ins]))
    if not rated:
        return None
    deltas = live.swap_deltas(lineup, [(out, ins) for _, out, ins in rated])
    scored = [
        (d.delta_p_cash if goal == "cash" else d.delta_p_gpp, swap.delta, swap, out, ins)
        for (swap, out, ins), d in zip(rated, deltas, strict=True)
    ]
    top = max(scored, key=lambda t: (t[0], t[1]))
    return top if top[0] > GAIN else None


def position_columns(
    live: lf.LiveLines, lineup: list[PlayerSpec], swaps: list, own_real: dict, own_proj: dict
) -> dict:
    """Where the lineup stands on the real field's top-1% line, and how many of its candidate swaps are
    LEVERAGE (by the real Own% and by the projected one): the pool the objective chooses from."""
    try:
        pos = lineup_position(live, lineup)
    except KeyError:
        return {"bucket": "", "gap": np.nan, "p_top1": np.nan}
    out = {"bucket": pos.bucket, "gap": pos.gap, "p_top1": pos.p_top1}
    for tag, own in (("real", own_real), ("proj", own_proj)):
        labels = [swap_overlap(s, own).label for s in swaps]
        known = [x for x in labels if x]
        out[f"cand_{tag}_n"] = len(known)
        out[f"cand_{tag}_lev"] = sum(x == LEVERAGE for x in known)
    return out


def run_contest(
    setup: WeekSetup, c: cr.RealContest, *, n_lineups: int, seed: int, key_to_name: dict[str, str]
) -> tuple[list[dict], dict]:
    goal = "cash" if c.fmt == "cash" else "gpp"
    now = week_frames.lock_utc(SEASON, setup.week)
    real = lf.real_lines(
        c.standings, setup.draws, setup.state_fpts, places_paid=c.places_paid, ladder=FINISH_LADDER
    )
    lives = {
        "P": setup.projected,
        "PS": setup.projected_state,
        "PSK": setup.projected_state.amplified(SWING_FACTOR),
        "R": lf.LiveLines(
            setup.draws,
            real.cash,
            real.top,
            "real",
            ladder_fractions=real.ladder_fractions,
            ladder=real.ladder,
        ),
    }
    own_real = lf.real_ownership(c.standings, list(setup.edge["Name"]))
    own_proj = sim_inputs.ownership_by_name(setup.edge)
    profile = c.standings.profile
    actual = profile.cash_line if goal == "cash" else profile.top1
    line_of = {m: (lives[m].cash_line if goal == "cash" else lives[m].top_line) for m in MODES}
    lines_row = {
        "week": setup.week,
        "contest": c.contest_id,
        "series": c.series,
        "format": c.format,
        "goal": goal,
        "entries": c.entries,
        "actual_line": actual,
        **{f"{m}_mean": float(line_of[m].mean()) for m in MODES},
        **{f"{m}_sd": float(line_of[m].std()) for m in MODES},
        **{f"{m}_pit": percentile_of(actual, line_of[m]) for m in MODES},
    }
    edge, specs = setup.edge, setup.specs
    pool = swap_pool(edge, now=now, metric="CalPts", pool_names=set(), all_players=True)
    rows = []
    for number, item in enumerate(test_lineups(c, key_to_name, n_lineups, seed)):
        names = item["names"]
        statuses = lineup_slot_status(names, edge, now=now)
        state = lineup_state(statuses, edge, cap=SALARY_CAP, metric="CalPts")
        if not state.open or any(n not in specs for n in names):
            continue
        lineup = [specs[n] for n in names]
        swaps = [s for group in one_for_one(state, pool, top=SWAPS_PER_SLOT).values() for s in group]
        base = realised(sum(setup.fpts.get(normalize_name(n), 0.0) for n in names), c)
        row = {
            "week": setup.week,
            "contest": c.contest_id,
            "goal": goal,
            "lineup": number,
            "mine": item["mine"],
            "open": len(state.open),
            "options": len(swaps),
            **{f"base_{k}": v for k, v in base.items()},
        }
        if goal == "gpp":
            row.update(position_columns(lives["R"], lineup, swaps, own_real, own_proj))
        for mode in MODES:
            pick = best_swap(lives[mode], lineup, swaps, specs, goal) if swaps else None
            if pick is None:
                after, out, ins = base, [], []
            else:
                _, _, swap, out, ins = pick
                gone = set(out)
                variant = [n for n in names if n not in gone] + [p.id for p in ins]
                after = realised(sum(setup.fpts.get(normalize_name(n), 0.0) for n in variant), c)
            row[f"{mode}_swap"] = bool(ins)
            row[f"{mode}_in"] = ",".join(p.id for p in ins)
            if goal == "gpp":
                for tag, own in (("real", own_real), ("proj", own_proj)):
                    o = swap_overlap(pick[2], own) if pick is not None else None
                    row[f"{mode}_{tag}_label"] = o.label if o is not None else ""
                    row[f"{mode}_{tag}_ownchg"] = o.delta if o is not None and o.delta is not None else np.nan
            for k, v in after.items():
                row[f"{mode}_{k}"] = v
        rows.append(row)
    return rows, lines_row


def run(
    weeks: list[int], *, n_lineups: int, n_sims: int, seed: int = lf.SEED, points: str = "actual"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lineup_rows, line_rows = [], []
    for week in weeks:
        print(f"Week {week}: setting up ...", flush=True)
        setup = setup_week(week, n_sims=n_sims, seed=seed, points=points, espn_cache=ESPN_CACHE)
        key_to_name: dict[str, str] = {}
        for name in setup.specs:
            key_to_name.setdefault(normalize_name(name), name)
        for c in setup.contests:
            rows, lines_row = run_contest(setup, c, n_lineups=n_lineups, seed=seed, key_to_name=key_to_name)
            lineup_rows += rows
            line_rows.append(lines_row)
            print(
                f"  {c.format:9s} {c.contest_id} n={c.entries:>6,}  {len(rows)} lineups tested; "
                f"actual line {lines_row['actual_line']:.1f}, "
                f"P {lines_row['P_mean']:.1f}, PS {lines_row['PS_mean']:.1f}, R {lines_row['R_mean']:.1f}",
                flush=True,
            )
    return pd.DataFrame(lineup_rows), pd.DataFrame(line_rows)


# ---------------------------------------------------------------------------------------------------------
# Reading the results
# ---------------------------------------------------------------------------------------------------------

METRICS = ("pts", "cash", "top10", "top1", "pct")


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> tuple[float, float]:
    """Mean and standard error of `a - b` for one metric, paired by lineup (a lineup is a unit; the weeks'
    lineups of one contest share a slate, so the SE is a floor, not a promise)."""
    d = (df[f"{a}_{metric}"] - df[f"{b}_{metric}"]).to_numpy(float)
    if len(d) < 2:
        return float("nan"), float("nan")
    return float(d.mean()), float(d.std(ddof=1) / math.sqrt(len(d)))


def compare_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (group, comparison): the mean paired difference and its SE for each realised metric, and
    how often the two modes picked a different swap."""
    rows = []
    groups = {"all": df, **{f"goal={g}": d for g, d in df.groupby("goal")}}
    groups.update({f"Week {w}": d for w, d in df.groupby("week")})
    if df["mine"].any():
        groups["Sam's lineups"] = df[df["mine"]]
    for label, part in groups.items():
        if part.empty:
            continue
        for a, b in (("R", "P"), ("R", "PS"), ("PS", "P"), ("PSK", "PS"), ("R", "PSK")):
            row = {"group": label, "compare": f"{a} - {b}", "lineups": len(part)}
            for m in METRICS:
                mean, se = paired(part, a, b, m)
                row[m] = mean
                row[f"{m}_se"] = se
            row["differ"] = float((part[f"{a}_in"] != part[f"{b}_in"]).mean())
            rows.append(row)
    return pd.DataFrame(rows)


def leverage_table(df: pd.DataFrame, tag: str = "real") -> pd.DataFrame:
    """GPP lineups only. Per (mode, bucket): the lineups, the share where the mode swapped at all, the split
    of its swaps into LEVERAGE / HOLD / EVEN, the mean change in summed ownership of the swap it chose, and
    the LEVERAGE share of all the candidates it chose from. Buckets: where the lineup stands on the real
    top-1% line (`behind` / `near` / `ahead` by its chance of finishing above it), the same lineups split in
    thirds by their gap in points ("far", "mid", "close"), and "all"."""
    if "bucket" not in df:
        return pd.DataFrame()
    g = df[(df["goal"] == "gpp") & df["bucket"].astype(bool)].copy()
    if g.empty:
        return pd.DataFrame()
    g["third"] = pd.qcut(g["gap"], 3, labels=["far", "mid", "close"])
    groups = [(b, g[g["bucket"] == b]) for b in ("behind", "near", "ahead")]
    groups += [(f"gap {t}", g[g["third"] == t]) for t in ("far", "mid", "close")] + [("all", g)]
    rows = []
    for mode in MODES:
        for bucket, part in groups:
            if part.empty:
                continue
            swapped = part[part[f"{mode}_swap"].astype(bool)]
            labels = swapped[f"{mode}_{tag}_label"]
            n = max(len(swapped), 1)
            cand_n = part[f"cand_{tag}_n"].sum()
            rows.append(
                {
                    "mode": mode,
                    "bucket": bucket,
                    "lineups": len(part),
                    "swapped": len(swapped) / len(part),
                    "n swaps": len(swapped),
                    "LEVERAGE": float((labels == LEVERAGE).sum()) / n,
                    "HOLD": float((labels == HOLD).sum()) / n,
                    "EVEN": float((labels == EVEN).sum()) / n,
                    "mean own change": float(swapped[f"{mode}_{tag}_ownchg"].mean())
                    if len(swapped)
                    else np.nan,
                    "pool LEVERAGE": float(part[f"cand_{tag}_lev"].sum() / cand_n) if cand_n else np.nan,
                }
            )
    return pd.DataFrame(rows)


def line_table(lines: pd.DataFrame) -> pd.DataFrame:
    """How well each mode's per-draw line distribution brackets the contest's ACTUAL line: the error of its
    mean, its spread, and the percentile the actual line fell at (0.5 is calibrated; an extreme one means the
    actual line sat in the tail)."""
    rows = []
    for goal, part in lines.groupby("goal"):
        for m in MODES:
            err = part[f"{m}_mean"] - part["actual_line"]
            rows.append(
                {
                    "goal": goal,
                    "mode": MODE_NAMES[m],
                    "contests": len(part),
                    "mean error": float(err.mean()),
                    "mean abs error": float(err.abs().mean()),
                    "mean sd": float(part[f"{m}_sd"].mean()),
                    "mean z": float((-err / part[f"{m}_sd"]).mean()),
                    "mean percentile": float(part[f"{m}_pit"].mean()),
                }
            )
    return pd.DataFrame(rows)


def report(df: pd.DataFrame, lines: pd.DataFrame) -> str:
    out = ["LINES: the contest's actual line against each mode's per-draw distribution of it"]
    out.append(line_table(lines).round(3).to_string(index=False))
    out.append("")
    out.append("REALISED RESULT of the swap each mode chose (paired differences, mean +- SE per lineup)")
    table = compare_table(df)
    for _, r in table.iterrows():
        cells = "  ".join(
            f"{m} {r[m]:+.3f}+-{r[f'{m}_se']:.3f}" if m != "pts" else f"pts {r[m]:+.2f}+-{r[f'{m}_se']:.2f}"
            for m in METRICS
        )
        out.append(
            f"{r['group']:14s} {r['compare']:8s} n={int(r['lineups']):5d}  {cells}  differ {r['differ']:.0%}"
        )
    out.append("")
    if "bucket" in df and df["bucket"].astype(bool).any():
        gpp = df[(df["goal"] == "gpp") & df["bucket"].astype(bool)]
        mix = ", ".join(f"{b} {int((gpp['bucket'] == b).sum())}" for b in ("behind", "near", "ahead"))
        out.append(f"LEVERAGE OR HOLD, by where the GPP lineup stands on the real top-1% line ({mix})")
        for tag, what in (("real", "the contest's real Own%"), ("proj", "projected Own%")):
            out.append(
                f"  labelled by {what}; share of the swaps each mode chose, and the pool's LEVERAGE share"
            )
            out.append(leverage_table(df, tag).round(3).to_string(index=False))
        out.append("")
    swapped = {m: float(df[f"{m}_swap"].mean()) for m in MODES}
    out.append("Lineups where the mode swapped at all: " + ", ".join(f"{m} {swapped[m]:.0%}" for m in MODES))
    base = {k: float(df[f"base_{k}"].mean()) for k in METRICS}
    out.append(
        "Unswapped lineups: "
        + ", ".join(f"{k} {v:.3f}" if k != "pts" else f"pts {v:.1f}" for k, v in base.items())
    )
    for m in MODES:
        mean = {k: float(df[f"{m}_{k}"].mean()) for k in METRICS}
        out.append(
            f"After {MODE_NAMES[m]:20s}: "
            + ", ".join(f"{k} {v:.3f}" if k != "pts" else f"pts {v:.1f}" for k, v in mean.items())
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", default="4,5,6")
    parser.add_argument("--lineups", type=int, default=120, help="test lineups sampled per contest")
    parser.add_argument("--sims", type=int, default=10000)
    parser.add_argument(
        "--points",
        choices=("actual", "espn"),
        default="actual",
        help="where the started players' points come from: the contests' own FPTS (a downloaded export), or "
        "ESPN's box scores (`--state live`, no download; the R mode then uses them too)",
    )
    parser.add_argument("--out", default=None, help="also save the per-lineup rows to this CSV")
    args = parser.parse_args(argv)
    df, lines = run(
        [int(w) for w in args.weeks.split(",")], n_lineups=args.lineups, n_sims=args.sims, points=args.points
    )
    print()
    print(report(df, lines))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        lines.to_csv(Path(args.out).with_suffix(".lines.csv"), index=False)


if __name__ == "__main__":
    main()
