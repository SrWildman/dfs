"""The optimizer's curse: how far the builder's chosen lineups fall short of their promise, and three fixes.

Session A's retro saw Sam's own double-up entries promised 62% to cash and cash 20% (n 10), while random real
field lineups were calibrated. The likely reason is selection: the builder keeps the lineups the projection
likes most, and those carry the most projection error in their favour. This study sizes that on every 2026
week with a pre-lock snapshot and tests three fixes, each held out by week. Shadow only: nothing here changes
a default, the builder or a sheet.

**Each week as it stood at the lock.** The last EdgeRaw snapshot before Sunday 1 pm ET
(`retro.edge_snapshot_before`) with a `CalPts` for every row (`contest_retro.pre_lock_edge`: the snapshot's
own, else the calibration fitted on the weeks before), cut to the main slate (the week's DraftKings salary
file). Sam's real pool is not stored, so the pool is `retro.pool_tags`' constructed one (his Week 6 counts,
top CalPts per position, all tagged Both); the builder adds every slate defense as it does live. The lines are
today's (`sim_inputs.resolve_lines` with the shipped `[sim.lines]` defaults: relative to the projected field
when the snapshot has ownership, else the fixed fallback, Week 1).

**The sets compared.** Today's builder (`lineup_suggest.suggest_builds`, `BUILDS` cash and `BUILDS` GPP
builds); `RANDOM_LINEUPS` random salary-legal lineups from the same pool (same players, no selection); and a
sample of the real field's lineups from the week's standings (weeks with standings only). Every lineup is
simulated with today's simulator and inputs, so its predicted points (the sum of CalPts), Cash % and Top 1%
are what the tool would have shown.

**Realized.** Points are DraftKings' own (the standings' `FPTS`) where the week has standings, else nflverse's
`DkActual` (`results_update.load_all_scored`); a player with no game scores 0, and a lineup holding a player
with neither is dropped. Cashed = points at or above the week's real double-up cash line (the `Cash Line` Sam
typed on Results: the GIANT $25 Double Up's from Week 4); Top 1% = at or above the real top-1% line of the
week's biggest GPP (700+ entries), or, where the week has no such standings, the cash line plus the measured
gap between the two relative lines (`TOP1_GAP`, 63.2 - 0.6), marked a proxy.

**Fixes** (every fitted number comes from the OTHER weeks only):

- *shrink*: each player's projection pulled toward a prior by a per-position weight fitted by least squares on
  the other weeks' players (`fit_shrink`); priors: the position's salary line (`salary`) or TFFB (`tffb`);
- *noise*: the builder run on `NOISE_DRAWS` noisy copies of the projections (multiplicative, sd `sigma`);
  the builds are the lineups chosen most often across the draws (`robust_pick`), shown with today's odds;
- *display calibration*: only the shown Cash % / Top 1% change (`shift` moves every build's simulated score
  by the points the builds fell short by on the other weeks; `platt` refits the logit).

The ship bar is `SHIP_BAR`. Local and read-only: everything comes from Sam's files under `data/`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import lineup_suggest as ls
from dfs import sim_inputs
from dfs.config import SimConfig
from dfs.contests import week_frames
from dfs.kickoff import parse_kickoff
from dfs.late_swap_search import SwapPlayer, swap_pool
from dfs.player_join import normalize_name
from dfs.sim import retro
from dfs.sim.simulate import simulate_lineups

SEASON = retro.SEASON
METRIC = "CalPts"
BUILDS = 20  # cash builds and GPP builds per week (the brief's "top 20")
N_SIMS = 5000  # draws per scoring run (the sheet uses sim_inputs.N_SIMS; 5,000 keeps 100+ builder runs quick)
TIME_LIMIT = 120.0  # the builder's own default search budget
RANDOM_LINEUPS = 300
FIELD_LINEUPS = {"cash": 150, "gpp": 150}
RANDOM_MIN_SALARY = 48_000  # a random lineup must spend at least this much (real lineups leave little)
TOP1_GAP = 63.2 - 0.6  # docs/SIM.md "Relative lines": top 1% and double-up cash, above the field mean
BIG_GPP_ENTRIES = 700
SHRINK_PRIORS = ("salary", "tffb")
SHRINK_MIN_CALPTS = 4.0  # the players the shrink weight is fitted on (the ones a lineup can hold)
NOISE_DRAWS = 8
NOISE_SIGMAS = (0.10, 0.20)
RESULTS_CACHE = Path("data/research/curse/results_lines.json")
OUT_DIR = Path("data/research/curse")
SHIP_BAR = (
    "A build-changing fix ships (behind a config switch, default off, Sam's OK) only if, held out by week, "
    "it beats today's builds on realized points or cash rate by more than two standard errors over weeks "
    "AND in most weeks; the display-only calibration ships on a held-out Brier gain alone"
)


# ---------------------------------------------------------------------------------------------------------
# One week, as it stood at the lock
# ---------------------------------------------------------------------------------------------------------


@dataclass
class WeekInputs:
    week: int
    edge: pd.DataFrame  # main slate, pre-lock, CalPts on every rated row
    tags: dict[str, str]
    now: datetime
    actual: dict[str, float]  # edge Name -> realized DK points (NaN: unknown)
    cash_line: float  # the real double-up cash line
    top1_line: float
    top1_proxy: bool
    typed_cash: sim_inputs.CashLine  # the season median before the week (the fixed fallback, Week 1)


def results_lines(client=None, path: Path = RESULTS_CACHE) -> dict[int, float]:
    """`{week: Cash Line}` as Sam typed it on Results, read once from the sheet (read-only) and cached."""
    if path.exists():
        return {int(k): float(v) for k, v in json.loads(path.read_text()).items()}
    from dfs.config import load_config
    from dfs.sheets import SheetsClient

    cfg = load_config()
    rows = (client or SheetsClient(cfg.google_sheets)).read_range(cfg.results.tab, "A1:J40")
    names = [str(h).strip() for h in rows[0]]
    w, c = names.index("Week"), names.index("Cash Line")
    out = {}
    for row in rows[1:]:
        try:
            out[int(float(row[w]))] = float(str(row[c]).replace(",", ""))
        except (IndexError, ValueError):
            continue
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    return out


def lines_through(season: int, through: int, cache: Path = RESULTS_CACHE) -> dict[int, float]:
    """Each week's real cash line up to `through`, without the sheet: Sam's typed values as cached by
    `results_lines`, and for a week the cache does not hold, the double-up's own standings
    (`contests_cash_line.standings_cash_lines`, the same series `dfs week close` fills Results from)."""
    from dfs import contests_cash_line
    from dfs.config import load_config
    from dfs.sheet_contests import load_tables

    typed = results_lines(path=cache) if cache.exists() else {}
    series = load_config().contests.cash_line_series
    profiles = load_tables(Path("data/contests")).profiles
    found = contests_cash_line.standings_cash_lines(profiles, season, series)
    return {w: typed.get(w, found.get(w)) for w in range(1, through + 1) if w in typed or w in found}


def main_slate(edge: pd.DataFrame, week: int, season: int = SEASON) -> pd.DataFrame:
    slate = retro.main_slate_names(week, season)
    if slate is None:
        raise FileNotFoundError(f"no DraftKings salary snapshot for Week {week}")
    keep = [
        (normalize_name(n), str(t).upper()) in slate for n, t in zip(edge["Name"], edge["Team"], strict=True)
    ]
    return edge[keep].reset_index(drop=True)


def realized_points(
    edge: pd.DataFrame, scored: pd.DataFrame, fpts: dict[str, float], week: int
) -> dict[str, float]:
    """`{Name: points}`: the standings' FPTS by normalised name, else nflverse's DkActual by DK Id, 0 for a
    player listed as having no game (`dnp`), NaN when neither knows him."""
    rows = scored[pd.to_numeric(scored["week"], errors="coerce") == week]
    key = retro._id_key  # noqa: SLF001 - the one DK-id normaliser
    by_id = rows.assign(_k=key(rows["Id"])).drop_duplicates("_k").set_index("_k")
    actual = pd.to_numeric(by_id["DkActual"], errors="coerce")
    status = by_id["Status"].astype(str)
    out = {}
    for name, k in zip(edge["Name"], key(edge["Id"]), strict=True):
        n = normalize_name(name)
        if n in fpts:
            out[str(name)] = fpts[n]
        elif k in actual.index and pd.notna(actual.get(k)):
            out[str(name)] = float(actual[k])
        elif k in status.index and status[k] == "dnp":
            out[str(name)] = 0.0
        else:
            out[str(name)] = float("nan")
    return out


def week_inputs(week: int, *, lines: dict[int, float], scored: pd.DataFrame, contests: list) -> WeekInputs:
    """Everything the study needs for `week` (module docstring). `contests`: `retro.load_contests()`."""
    from dfs.sim import contest_retro

    edge = main_slate(contest_retro.pre_lock_edge(week), week)
    tags = retro.pool_tags(edge, slate=retro.main_slate_names(week))
    _, snap = retro.edge_snapshot_before(week)
    week_cs = [c for c in contests if c.week == week]
    fpts: dict[str, float] = {}
    for c in week_cs:
        fpts.update(c.fpts)
    big = [c for c in week_cs if c.fmt == "gpp" and c.entries >= BIG_GPP_ENTRIES]
    cash = lines[week]
    if big:
        c = max(big, key=lambda c: c.entries)
        top1, proxy = float(c.points[max(1, round(retro.TOP_FRACTION * len(c.points))) - 1]), False
    else:
        top1, proxy = cash + TOP1_GAP, True
    typed = sim_inputs.cash_line_from_results(
        ["Week", "Cash Line"], [[w, v] for w, v in lines.items()], before_week=week
    )
    return WeekInputs(
        week=week,
        edge=edge,
        tags=tags,
        now=min(snap, week_frames.lock_utc(SEASON, week) - timedelta(minutes=1)),
        actual=realized_points(edge, scored, fpts, week),
        cash_line=cash,
        top1_line=top1,
        top1_proxy=proxy,
        typed_cash=typed,
    )


def sim_lines(edge: pd.DataFrame, typed: sim_inputs.CashLine) -> tuple[dict, sim_inputs.SimLines]:
    """Today's specs and lines for `edge` (the shipped `[sim]` defaults)."""
    specs = sim_inputs.build_specs(edge)
    cfg = SimConfig()
    return specs, sim_inputs.resolve_lines(edge, specs, typed, cfg.gpp_target, cfg.lines)


# ---------------------------------------------------------------------------------------------------------
# Projections the fixes build on
# ---------------------------------------------------------------------------------------------------------


def with_projection(edge: pd.DataFrame, values: pd.Series | np.ndarray) -> pd.DataFrame:
    """`edge` with `CalPts` replaced (kept positive: the simulator needs a projection above 0)."""
    out = edge.copy()
    v = pd.Series(np.asarray(values, dtype=float), index=out.index)
    base = pd.to_numeric(out["CalPts"], errors="coerce")
    out["CalPts"] = v.where(base.isna() | (v > 0.1), 0.1).where(base.notna())
    return out


def prior_values(
    edge: pd.DataFrame, prior: str, salary_fit: dict[str, tuple[float, float]] | None = None
) -> pd.Series:
    """Each row's prior: the position's salary line `a + b * salary` (`salary_fit`) or TFFB's `ProjPts`."""
    if prior == "tffb":
        return pd.to_numeric(edge["ProjPts"], errors="coerce").fillna(
            pd.to_numeric(edge["CalPts"], errors="coerce")
        )
    if prior != "salary" or salary_fit is None:
        raise ValueError(f"prior {prior!r} needs {SHRINK_PRIORS} (and a salary fit for 'salary')")
    sal = pd.to_numeric(edge["Salary"], errors="coerce")
    out = pd.Series(np.nan, index=edge.index)
    for pos, (a, b) in salary_fit.items():
        m = edge["Position"] == pos
        out[m] = a + b * sal[m]
    return out.fillna(pd.to_numeric(edge["CalPts"], errors="coerce"))


def training_rows(weeks: dict[int, WeekInputs]) -> pd.DataFrame:
    """One row per rated main-slate player per week: position, salary, CalPts, ProjPts, actual."""
    parts = []
    for w, wi in weeks.items():
        e = wi.edge
        parts.append(
            pd.DataFrame(
                {
                    "week": w,
                    "Position": e["Position"].to_numpy(),
                    "Salary": pd.to_numeric(e["Salary"], errors="coerce").to_numpy(),
                    "CalPts": pd.to_numeric(e["CalPts"], errors="coerce").to_numpy(),
                    "ProjPts": pd.to_numeric(e["ProjPts"], errors="coerce").to_numpy(),
                    "actual": [wi.actual.get(str(n), np.nan) for n in e["Name"]],
                }
            )
        )
    f = pd.concat(parts, ignore_index=True)
    return f[(f["CalPts"] >= SHRINK_MIN_CALPTS) & f["actual"].notna()].reset_index(drop=True)


def fit_salary(rows: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """Per position, the least-squares line actual = a + b * salary."""
    out = {}
    for pos, g in rows.groupby("Position"):
        b, a = np.polyfit(g["Salary"].to_numpy(float), g["actual"].to_numpy(float), 1)
        out[str(pos)] = (float(a), float(b))
    return out


def fit_shrink(rows: pd.DataFrame, prior: pd.Series) -> dict[str, float]:
    """Per position, the weight `lam` in [0, 1] minimising the squared error of `(1 - lam) * CalPts + lam *
    prior` against the actual points: `lam = sum(r d) / sum(d d)` with `r = actual - CalPts`, `d = prior -
    CalPts`."""
    out = {}
    for pos, g in rows.assign(_prior=prior.to_numpy()).groupby("Position"):
        r = g["actual"] - g["CalPts"]
        d = g["_prior"] - g["CalPts"]
        dd = float((d * d).sum())
        out[str(pos)] = float(np.clip((r * d).sum() / dd, 0.0, 1.0)) if dd > 0 else 0.0
    return out


def shrunk_edge(edge: pd.DataFrame, prior: pd.Series, lam: dict[str, float]) -> pd.DataFrame:
    cal = pd.to_numeric(edge["CalPts"], errors="coerce")
    weight = edge["Position"].map(lam).fillna(0.0)
    return with_projection(edge, (1 - weight) * cal + weight * prior)


def noisy_edge(edge: pd.DataFrame, sigma: float, rng: np.random.Generator) -> pd.DataFrame:
    """`edge` with every projection times `exp(sigma z - sigma^2 / 2)` (mean one), z standard normal."""
    cal = pd.to_numeric(edge["CalPts"], errors="coerce")
    z = rng.standard_normal(len(edge))
    return with_projection(edge, cal * np.exp(sigma * z - sigma**2 / 2))


# ---------------------------------------------------------------------------------------------------------
# Building and scoring
# ---------------------------------------------------------------------------------------------------------


def run_builder(
    wi: WeekInputs,
    edge: pd.DataFrame | None = None,
    *,
    cash: int = BUILDS,
    gpp: int = BUILDS,
    n_sims: int = N_SIMS,
    time_limit: float = TIME_LIMIT,
) -> list[ls.Build]:
    """Today's `suggest_builds` on `edge` (default the week's own), with the specs and lines built from it."""
    edge = wi.edge if edge is None else edge
    specs, lines = sim_lines(edge, wi.typed_cash)
    report = ls.suggest_builds(
        edge,
        wi.tags,
        specs,
        now=wi.now,
        cap=retro_cap(),
        cash_line=lines.cash_line,
        gpp_target=lines.gpp_target,
        metric=METRIC,
        cash_builds=cash,
        gpp_builds=gpp,
        n_sims=n_sims,
        time_limit=time_limit,
        field=lines.field,
    )
    return report.builds


def retro_cap() -> int:
    from dfs.config import LineupsConfig

    return LineupsConfig().salary_cap


def _players(edge: pd.DataFrame, now: datetime) -> dict[str, SwapPlayer]:
    return {
        p.name: p for p in swap_pool(edge, now=now, metric=METRIC, pool_names=set(edge["Name"].astype(str)))
    }


def score_names(
    wi: WeekInputs,
    lineups: Sequence[Sequence[str]],
    edge: pd.DataFrame | None = None,
    *,
    n_sims: int = N_SIMS,
) -> list[ls.Scored]:
    """Today's odds for lineups given by name (on `edge`'s projections, default the week's own); a lineup
    with a player the week cannot rate or seat is left out (the result lines up with what is kept: see
    `Scored.players`)."""
    edge = wi.edge if edge is None else edge
    specs, lines = sim_lines(edge, wi.typed_cash)
    by_name = _players(edge, wi.now)
    kickoff = {str(n): parse_kickoff(s) for n, s in zip(edge["Name"], edge["GameStart"], strict=True)}
    lus = [[by_name[n] for n in lu] for lu in lineups if all(n in by_name for n in lu)]
    return ls.score(
        lus,
        specs,
        kickoff,
        cash_line=lines.cash_line,
        gpp_target=lines.gpp_target,
        n_sims=n_sims,
        field=lines.field,
    )


def margins(
    wi: WeekInputs, lineups: Sequence[Sequence[str]], *, n_sims: int = N_SIMS
) -> tuple[np.ndarray, np.ndarray]:
    """Per draw and lineup, the simulated score minus the draw's cash line and minus its top-end line (draws x
    lineups each), on the week's own projections: what `shift` recalibration reads."""
    specs, lines = sim_lines(wi.edge, wi.typed_cash)
    rated = [s for lu in lineups if (s := sim_inputs.lineup_from_names(list(lu), specs)) is not None]
    res = simulate_lineups(
        rated,
        n_sims=n_sims,
        seed=sim_inputs.SEED,
        cash_line=lines.cash_line,
        gpp_target=lines.gpp_target,
        field=lines.field,
        keep_scores=True,
    )
    cash = res.cash_lines[:, None] if res.cash_lines is not None else lines.cash_line
    top = res.top_lines[:, None] if res.top_lines is not None else lines.gpp_target
    return res.scores - cash, res.scores - top


def random_lineups(wi: WeekInputs, n: int, rng: np.random.Generator) -> list[list[str]]:
    """`n` distinct random legal lineups from the week's pool (plus every slate defense, as the builder
    has): QB, 2 RB, 3 WR, TE, FLEX (RB / WR / TE), DST, uniform over players, kept when the salary is between
    `RANDOM_MIN_SALARY` and the cap."""
    pool = ls.pool_for(wi.edge, wi.tags, ls.CASH_TAGS, now=wi.now, metric=METRIC, any_dst=True)
    by_pos: dict[str, list[SwapPlayer]] = {}
    for p in pool:
        by_pos.setdefault(p.position, []).append(p)
    flex = [p for pos in ("RB", "WR", "TE") for p in by_pos.get(pos, [])]
    cap, out, seen = retro_cap(), [], set()
    for _ in range(n * 400):
        if len(out) == n:
            break
        pick = [rng.choice(by_pos["QB"]), *rng.choice(by_pos["RB"], 2, replace=False)]
        pick += [
            *rng.choice(by_pos["WR"], 3, replace=False),
            rng.choice(by_pos["TE"]),
            rng.choice(by_pos["DST"]),
        ]
        names = {p.name for p in pick}
        rest = [p for p in flex if p.name not in names]
        pick.append(rest[rng.integers(len(rest))])
        salary = sum(p.salary for p in pick)
        key = frozenset(p.name for p in pick)
        if RANDOM_MIN_SALARY <= salary <= cap and key not in seen:
            seen.add(key)
            out.append([p.name for p in pick])
    return out


def field_lineups(
    wi: WeekInputs, contests: list, fmt: str, n: int, rng: np.random.Generator
) -> list[list[str]]:
    """Up to `n` distinct real lineups from the week's `fmt` ("cash" / "gpp") standings, by edge Name."""
    by_norm = {normalize_name(x): str(x) for x in wi.edge["Name"]}
    pool = [key for c in contests if c.week == wi.week and c.fmt == fmt for key in c.copies]
    out, seen = [], set()
    for j in rng.permutation(len(pool)):
        names = [by_norm.get(k) for k in pool[j]]
        if None in names or pool[j] in seen:
            continue
        seen.add(pool[j])
        out.append(names)
        if len(out) == n:
            break
    return out


def rows_for(
    wi: WeekInputs, scored: Sequence, *, variant: str, kind: str, base: dict | None = None
) -> pd.DataFrame:
    """One row per lineup: what was shown (projection, Cash %, Top 1%) and what happened. `scored` holds
    `Build`s or `Scored`s; `base` maps a lineup's sorted names to its odds on the week's own projections
    (`base_*` columns, for a fix that changes the projection)."""
    rows = []
    for rank, s in enumerate(scored, start=1):
        names = sorted(p.name for p in s.players)
        pts = [wi.actual.get(n, np.nan) for n in names]
        realized = float(np.sum(pts)) if not any(np.isnan(pts)) else np.nan
        b = (base or {}).get(tuple(names))
        rows.append(
            {
                "week": wi.week,
                "variant": variant,
                "kind": kind,
                "rank": rank,
                "names": "|".join(names),
                "proj": float(s.projection),
                "p_cash": float(s.p_cash),
                "p_gpp": float(s.p_gpp),
                "base_proj": np.nan if b is None else b.projection,
                "base_p_cash": np.nan if b is None else b.p_cash,
                "base_p_gpp": np.nan if b is None else b.p_gpp,
                "salary": int(s.salary),
                "realized": realized,
                "cashed": np.nan if np.isnan(realized) else float(realized >= wi.cash_line - 1e-9),
                "top1": np.nan if np.isnan(realized) else float(realized >= wi.top1_line - 1e-9),
                "cash_line": wi.cash_line,
                "top1_line": wi.top1_line,
                "top1_proxy": wi.top1_proxy,
            }
        )
    return pd.DataFrame(rows)


def base_odds(wi: WeekInputs, builds: Sequence) -> dict[tuple, ls.Scored]:
    """`{sorted names: Scored}` for `builds` on the week's own projections."""
    lineups = [[p.name for p in b.players] for b in builds]
    return {tuple(sorted(p.name for p in s.players)): s for s in score_names(wi, lineups)}


def robust_pick(
    wi: WeekInputs, draws: Sequence[Sequence[ls.Build]], kind: str, count: int = BUILDS
) -> list[ls.Scored]:
    """The `count` lineups chosen most often across the noisy draws' builds of `kind`, shown with their odds
    on the week's own projections; ties go to the higher shown Cash % (cash) or Top 1% (GPP). The builder's
    own rules still hold: `MIN_DIFFERENCE` players apart and, for GPP, the exposure cap."""
    freq = Counter(frozenset(p.name for p in b.players) for d in draws for b in d if b.kind == kind)
    scored = score_names(wi, [sorted(k) for k in freq])
    times = {frozenset(p.name for p in s.players): freq[frozenset(p.name for p in s.players)] for s in scored}

    def key(s: ls.Scored):
        p = s.p_cash if kind == "cash" else s.p_gpp
        return (
            -times[frozenset(q.name for q in s.players)],
            -round(p, ls.PROB_DECIMALS),
            -round(s.projection, 6),
        )

    cap = ls.exposure_cap(count) if kind == "gpp" else None
    return ls.choose(scored, count, key=key, max_per_player=cap)


# ---------------------------------------------------------------------------------------------------------
# Display calibration (shown numbers only)
# ---------------------------------------------------------------------------------------------------------


def _logit(p: np.ndarray, eps: float = 1e-3) -> np.ndarray:
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


def fit_platt(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """`(a, b)` of logit(P) = a + b logit(p) by maximum likelihood (a few Newton steps from (0, 1))."""
    x = _logit(np.asarray(p, float))
    y = np.asarray(y, float)
    a, b = 0.0, 1.0
    for _ in range(50):
        q = 1 / (1 + np.exp(-(a + b * x)))
        w = q * (1 - q) + 1e-9
        g = np.array([(y - q).sum(), ((y - q) * x).sum()])
        h = np.array([[w.sum(), (w * x).sum()], [(w * x).sum(), (w * x * x).sum()]]) + np.eye(2) * 1e-6
        step = np.linalg.solve(h, g)
        a, b = a + step[0], b + step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


def apply_platt(p: np.ndarray, ab: tuple[float, float]) -> np.ndarray:
    return 1 / (1 + np.exp(-(ab[0] + ab[1] * _logit(np.asarray(p, float)))))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = ~np.isnan(y)
    return float(np.mean((p[ok] - y[ok]) ** 2)) if ok.any() else float("nan")


# ---------------------------------------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------------------------------------


def curse_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Per week, variant and kind: lineups, mean shown projection / realized points and their gap (the curse
    in points), mean shown Cash % / realized cash rate and their gap, the same for Top 1%."""
    g = rows.groupby(["week", "variant", "kind"])
    out = g.agg(
        n=("realized", "count"),
        proj=("proj", "mean"),
        realized=("realized", "mean"),
        p_cash=("p_cash", "mean"),
        cashed=("cashed", "mean"),
        p_gpp=("p_gpp", "mean"),
        top1=("top1", "mean"),
    ).reset_index()
    out["pts_gap"] = out["realized"] - out["proj"]
    out["cash_gap"] = out["cashed"] - out["p_cash"]
    out["top1_gap"] = out["top1"] - out["p_gpp"]
    return out


def paired(per_week: pd.DataFrame, column: str, fix: str, base: str = "today", kind: str = "cash") -> dict:
    """The fix minus today, per week, on `column`: mean, standard error over weeks, weeks better, z."""
    t = per_week[per_week["kind"] == kind].pivot(index="week", columns="variant", values=column)
    if fix not in t or base not in t:
        return {}
    d = (t[fix] - t[base]).dropna()
    n = len(d)
    se = float(d.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    mean = float(d.mean()) if n else float("nan")
    return {
        "weeks": n,
        "mean": mean,
        "se": se,
        "z": mean / se if se and np.isfinite(se) and se > 0 else float("nan"),
        "better": int((d > 0).sum()),
        "by_week": {int(w): float(v) for w, v in d.items()},
    }


def passes(c: dict) -> bool:
    return bool(c) and np.isfinite(c["z"]) and c["z"] > 2 and c["better"] > c["weeks"] / 2


# ---------------------------------------------------------------------------------------------------------
# The runs (each job saves its builds; `assemble` turns them into the tables)
# ---------------------------------------------------------------------------------------------------------


class Study:
    """The weeks' inputs, built once per process."""

    def __init__(self, weeks: Sequence[int], lines: dict[int, float] | None = None):
        from dfs import results_update

        # the cached typed lines stop at the last week they were read; a week closed since has its line in the
        # standings (Week 7: the cache made every `dfs backtest --weeks ...7` fail with KeyError: 7)
        self.lines = lines_through(SEASON, max(weeks)) if lines is None else lines
        self.scored = results_update.load_all_scored(SEASON)
        self.contests = retro.load_contests()
        self.weeks = {
            w: week_inputs(w, lines=self.lines, scored=self.scored, contests=self.contests) for w in weeks
        }

    def train(self, held_out: int) -> pd.DataFrame:
        return training_rows({w: wi for w, wi in self.weeks.items() if w != held_out})

    def shrink_edge(self, week: int, prior: str) -> tuple[pd.DataFrame, dict[str, float]]:
        """The week's edge shrunk toward `prior` with weights fitted on the other weeks."""
        rows = self.train(week)
        fit = fit_salary(rows) if prior == "salary" else None
        lam = fit_shrink(rows, prior_values(rows, prior, fit))
        edge = self.weeks[week].edge
        return shrunk_edge(edge, prior_values(edge, prior, fit), lam), lam


def job_name(week: int, variant: str) -> str:
    return f"w{week:02d}_{variant}"


def builds_record(builds: Sequence[ls.Build]) -> list[dict]:
    return [
        {
            "kind": b.kind,
            "names": sorted(p.name for p in b.players),
            "proj": b.projection,
            "p_cash": b.p_cash,
            "p_gpp": b.p_gpp,
        }
        for b in builds
    ]


def run_job(study: Study, week: int, variant: str, out_dir: Path = OUT_DIR, seed: int = 0) -> Path:
    """One builder run: `today`, `shrink_<prior>` or `noise_<sigma>_<draw>`; saves its builds as JSON."""
    path = out_dir / "jobs" / f"{job_name(week, variant)}.json"
    if path.exists():
        return path
    wi = study.weeks[week]
    extra: dict = {}
    if variant == "today":
        edge = wi.edge
    elif variant.startswith("shrink_"):
        edge, lam = study.shrink_edge(week, variant.removeprefix("shrink_"))
        extra["lam"] = lam
    elif variant.startswith("noise_"):
        _, sigma, draw = variant.split("_")
        rng = np.random.default_rng([seed, week, int(float(sigma) * 1000), int(draw)])
        edge = noisy_edge(wi.edge, float(sigma), rng)
    else:
        raise ValueError(f"unknown variant {variant!r}")
    builds = run_builder(wi, edge)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"week": week, "variant": variant, "builds": builds_record(builds), **extra}))
    return path


def all_jobs(weeks: Sequence[int]) -> list[tuple[int, str]]:
    variants = ["today", *(f"shrink_{p}" for p in SHRINK_PRIORS)]
    variants += [f"noise_{s:.2f}_{d}" for s in NOISE_SIGMAS for d in range(NOISE_DRAWS)]
    return [(w, v) for v in variants for w in weeks]


_STUDY: Study | None = None


def _worker(args: tuple[tuple[int, ...], dict | None, int, str]) -> str:
    global _STUDY
    weeks, lines, week, variant = args
    if _STUDY is None:
        _STUDY = Study(weeks, lines)
    import time

    t = time.monotonic()
    run_job(_STUDY, week, variant)
    return f"{job_name(week, variant)} {time.monotonic() - t:.0f}s"


def run_all(
    weeks: Sequence[int],
    workers: int = 6,
    log=print,
    jobs: Sequence[tuple[int, str]] | None = None,
    lines: dict[int, float] | None = None,
) -> None:
    """Every job not saved yet (`all_jobs`, or `jobs`), `workers` processes at a time. `lines`: the cash
    lines the workers use (default `results_lines()`)."""
    import multiprocessing as mp

    todo = [
        (tuple(weeks), lines, w, v)
        for w, v in (all_jobs(weeks) if jobs is None else jobs)
        if not (OUT_DIR / "jobs" / f"{job_name(w, v)}.json").exists()
    ]
    log(f"{len(todo)} jobs")
    if not todo:
        return
    with mp.get_context("spawn").Pool(workers) as pool:
        for line in pool.imap_unordered(_worker, todo):
            log(line)


# ---------------------------------------------------------------------------------------------------------
# Assembly: the saved builds, the comparison sets and the held-out fixes, as tables
# ---------------------------------------------------------------------------------------------------------


def record_rows(wi: WeekInputs, records: Sequence[dict], *, variant: str, kind: str) -> pd.DataFrame:
    """`rows_for` on saved records (`{names, proj, p_cash, p_gpp}`), ranked by the shown odds of `kind`."""

    @dataclass(frozen=True)
    class _P:
        name: str

    @dataclass(frozen=True)
    class _S:
        players: tuple
        projection: float
        p_cash: float
        p_gpp: float
        salary: int = 0

    objs = [_S(tuple(_P(n) for n in r["names"]), r["proj"], r["p_cash"], r["p_gpp"]) for r in records]
    objs.sort(
        key=lambda s: (-(s.p_cash if kind == "cash" else s.p_gpp), -s.projection, [p.name for p in s.players])
    )
    return rows_for(wi, objs, variant=variant, kind=kind)


def as_records(scored: Sequence) -> list[dict]:
    return [
        {
            "names": sorted(p.name for p in s.players),
            "proj": s.projection,
            "p_cash": s.p_cash,
            "p_gpp": s.p_gpp,
        }
        for s in scored
    ]


def load_jobs(out_dir: Path = OUT_DIR) -> dict[tuple[int, str], dict]:
    return {
        (d["week"], d["variant"]): d
        for d in (json.loads(p.read_text()) for p in sorted((out_dir / "jobs").glob("*.json")))
    }


def assemble(study: Study, out_dir: Path = OUT_DIR, seed: int = 0, log=print) -> dict:
    """Every table of the study from the saved jobs: `rows` (one per lineup, every variant and comparison
    set), `margins` (today's builds' simulated margins over the lines), `lams` (the shrink weights)."""
    jobs = load_jobs(out_dir)
    parts, margin_parts, lams = [], [], []
    for week, wi in study.weeks.items():
        rng = np.random.default_rng([seed, week])
        today = jobs[(week, "today")]["builds"]
        for kind in ("cash", "gpp"):
            recs = [r for r in today if r["kind"] == kind]
            parts.append(record_rows(wi, recs, variant="today", kind=kind))
            cash_m, top_m = margins(wi, [r["names"] for r in recs])
            m = cash_m if kind == "cash" else top_m
            for r, col in zip(recs, m.T, strict=True):
                margin_parts.append(
                    {"week": week, "kind": kind, "names": "|".join(r["names"]), "margin": col}
                )
        for prior in SHRINK_PRIORS:
            d = jobs.get((week, f"shrink_{prior}"))
            if d is None:
                continue
            lams.append({"week": week, "prior": prior, **d["lam"]})
            base = base_odds(wi, [_named(r) for r in d["builds"]])
            for kind in ("cash", "gpp"):
                rows = record_rows(
                    wi, [r for r in d["builds"] if r["kind"] == kind], variant=f"shrink_{prior}", kind=kind
                )
                parts.append(_with_base(rows, base))
        # each sigma's draws 0..7; the first sigma's draws 8..15 (`main replicate`) are an independent rerun
        sets = [(f"noise_{s:.2f}", s, range(NOISE_DRAWS)) for s in NOISE_SIGMAS]
        sets.append(
            (f"noise_{NOISE_SIGMAS[0]:.2f}_rep", NOISE_SIGMAS[0], range(NOISE_DRAWS, 2 * NOISE_DRAWS))
        )
        for variant, sigma, numbers in sets:
            draws = [jobs.get((week, f"noise_{sigma:.2f}_{k}")) for k in numbers]
            draws = [[_named(r) for r in d["builds"]] for d in draws if d is not None]
            if not draws:
                continue
            for kind in ("cash", "gpp"):
                picks = robust_pick(wi, draws, kind)
                parts.append(record_rows(wi, as_records(picks), variant=variant, kind=kind))
        rand = score_names(wi, random_lineups(wi, RANDOM_LINEUPS, rng))
        for kind in ("cash", "gpp"):
            parts.append(record_rows(wi, as_records(rand), variant="random", kind=kind))
        for fmt, n in FIELD_LINEUPS.items():
            lus = field_lineups(wi, study.contests, fmt, n, rng)
            if lus:
                parts.append(
                    record_rows(wi, as_records(score_names(wi, lus)), variant=f"field_{fmt}", kind=fmt)
                )
        log(f"week {week} assembled")
    rows = pd.concat(parts, ignore_index=True)
    return {"rows": rows, "margins": pd.DataFrame(margin_parts), "lams": pd.DataFrame(lams)}


@dataclass(frozen=True)
class _Named:
    """A saved build in the shape `robust_pick` / `base_odds` read: `.kind` and `.players[i].name`."""

    kind: str
    players: tuple


@dataclass(frozen=True)
class _NamePlayer:
    name: str


def _named(record: dict) -> _Named:
    return _Named(record["kind"], tuple(_NamePlayer(n) for n in record["names"]))


def _with_base(rows: pd.DataFrame, base: dict) -> pd.DataFrame:
    out = rows.copy()
    got = [base.get(tuple(n.split("|"))) for n in out["names"]]
    out["base_proj"] = [np.nan if s is None else s.projection for s in got]
    out["base_p_cash"] = [np.nan if s is None else s.p_cash for s in got]
    out["base_p_gpp"] = [np.nan if s is None else s.p_gpp for s in got]
    return out


def held_out_noise(per_week: pd.DataFrame, kind: str, column: str = "realized") -> pd.DataFrame:
    """The noise fix with its sigma chosen on the other weeks (the sigma whose builds did best there on
    `column`): one row per week, variant `noise_cv`."""
    t = per_week[(per_week["kind"] == kind) & per_week["variant"].str.fullmatch(r"noise_\d\.\d+")]
    out = []
    for week in sorted(t["week"].unique()):
        train = t[t["week"] != week].groupby("variant")[column].mean()
        if train.empty:
            continue
        best = train.idxmax()
        row = t[(t["week"] == week) & (t["variant"] == best)].iloc[0].copy()
        row["variant"], row["chosen"] = "noise_cv", best
        out.append(row)
    return pd.DataFrame(out)


def display_calibration(rows: pd.DataFrame, margin_frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Today's builds of `kind` with their shown probability and two held-out recalibrations of it: `shift`
    (each build's simulated margin moved by the points the builds missed their projection by on the other
    weeks) and `platt` (logit refit on the other weeks). One row per build: p_today, p_shift, p_platt, y."""
    p_col, y_col = ("p_cash", "cashed") if kind == "cash" else ("p_gpp", "top1")
    t = rows[(rows["variant"] == "today") & (rows["kind"] == kind)].merge(
        margin_frame[margin_frame["kind"] == kind][["week", "names", "margin"]], on=["week", "names"]
    )
    out = []
    for week in sorted(t["week"].unique()):
        train, test = t[(t["week"] != week) & t[y_col].notna()], t[t["week"] == week]
        shift = float((train["realized"] - train["proj"]).mean())
        ab = fit_platt(train[p_col].to_numpy(), train[y_col].to_numpy())
        for _, r in test.iterrows():
            out.append(
                {
                    "week": week,
                    "names": r["names"],
                    "y": r[y_col],
                    "p_today": r[p_col],
                    "p_shift": float((np.asarray(r["margin"]) + shift >= 0).mean()),
                    "p_platt": float(apply_platt(np.array([r[p_col]]), ab)[0]),
                    "shift": shift,
                    "platt_a": ab[0],
                    "platt_b": ab[1],
                }
            )
    return pd.DataFrame(out)


def brier_by_week(cal: pd.DataFrame) -> pd.DataFrame:
    g = cal.dropna(subset=["y"]).groupby("week")
    return pd.DataFrame(
        {
            f"brier_{m}": g.apply(lambda f, m=m: brier(f[f"p_{m}"], f["y"]))
            for m in ("today", "shift", "platt")
        }
    ).reset_index()


def summarize(tables: dict) -> dict:
    """The study's numbers: the per-week curse table (`per_week`, with the held-out noise pick as
    `noise_cv`), each build-changing fix against today (`fixes`), and the display calibration's Brier by week
    (`calibration`, per kind)."""
    rows = tables["rows"]
    per_week = curse_table(rows)
    cv = [held_out_noise(per_week, kind) for kind in ("cash", "gpp")]
    cv = [c.drop(columns="chosen") for c in cv if not c.empty]
    per_week = pd.concat([per_week, *cv], ignore_index=True)
    fixes = {}
    for kind in ("cash", "gpp"):
        hit = "cashed" if kind == "cash" else "top1"
        for fix in ("shrink_salary", "shrink_tffb", "noise_0.10", "noise_0.20", "noise_0.10_rep", "noise_cv"):
            fixes[(kind, fix)] = {col: paired(per_week, col, fix, kind=kind) for col in ("realized", hit)}
    calibration = {}
    for kind in ("cash", "gpp"):
        cal = display_calibration(rows, tables["margins"], kind)
        by_week = brier_by_week(cal)
        gains = {}
        for m in ("shift", "platt"):
            # Brier gain (today minus the recalibration: positive = better), in `paired`'s long shape
            long = pd.DataFrame(
                {
                    "week": list(by_week["week"]) * 2,
                    "variant": ["today"] * len(by_week) + [m] * len(by_week),
                    "kind": kind,
                    "gain": [*(-by_week["brier_today"]), *(-by_week[f"brier_{m}"])],
                }
            )
            gains[m] = paired(long, "gain", m, kind=kind)
        calibration[kind] = {"rows": cal, "brier": by_week, "gain": gains}
    return {"per_week": per_week, "fixes": fixes, "calibration": calibration}


# ---------------------------------------------------------------------------------------------------------
# The weekly rerun (`dfs research refresh`)
# ---------------------------------------------------------------------------------------------------------

REFRESH_FIXES = ("shrink_salary", "shrink_tffb", "noise_cv", "noise_0.10_rep")


def cached_tables(study: Study, out_dir: Path = OUT_DIR, log=print) -> dict:
    """`assemble`'s tables, reused while the weeks and the saved jobs are the ones they were built from."""
    import pickle

    path = out_dir / "tables.pkl"
    key = {"weeks": sorted(study.weeks), "jobs": sorted(p.stem for p in (out_dir / "jobs").glob("*.json"))}
    if path.exists():
        tables = pickle.loads(path.read_bytes())
        if tables.get("key") == key:
            return tables
    tables = {**assemble(study, out_dir, log=log), "key": key}
    path.write_bytes(pickle.dumps(tables))
    return tables


def refresh_summary(season: int, through: int) -> dict:
    """The weekly hook for `dfs research refresh` (session B's contract): runs the builder jobs of any week
    not run yet (`run_all`, resumable: about 15 minutes for a new week, nothing for the others), reassembles
    when the weeks changed, and returns `{"headline": [...], "metrics": {...}, "ship_bar": str}`."""
    if season != SEASON:
        return {
            "headline": [f"The curse study is wired for {SEASON} only."],
            "metrics": {},
            "ship_bar": SHIP_BAR,
        }
    lines = lines_through(season, through)
    weeks = sorted(lines)
    if len(weeks) < 3:
        return {
            "headline": [f"The curse study needs 3 weeks with a cash line; it has {len(weeks)}."],
            "metrics": {},
            "ship_bar": SHIP_BAR,
        }
    run_all(weeks, log=lambda _m: None, lines=lines)
    tables = cached_tables(Study(weeks, lines), log=lambda _m: None)
    return refresh_from(summarize(tables), weeks)


def refresh_from(summary: dict, weeks: Sequence[int]) -> dict:
    """`refresh_summary`'s dictionary from `summarize`'s output: the curse (shown minus realized, builds and
    random lineups, and the part due to selection), each fix against today, and the display shift."""
    pw = summary["per_week"]
    cash = pw[pw["kind"] == "cash"].pivot(index="week", columns="variant", values=["cash_gap", "pts_gap"])

    def mean_se(d: pd.Series) -> tuple[float, float]:
        d = d.dropna()
        return float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan")

    gap, gap_se = mean_se(cash["cash_gap"]["today"])
    rnd, rnd_se = mean_se(cash["cash_gap"]["random"])
    sel, sel_se = mean_se(cash["cash_gap"]["today"] - cash["cash_gap"]["random"])
    sel_pts, sel_pts_se = mean_se(cash["pts_gap"]["today"] - cash["pts_gap"]["random"])
    metrics: dict[str, float | bool] = {
        "weeks": len(weeks),
        "cash builds cash gap": round(gap, 4),
        "random cash gap": round(rnd, 4),
        "selection cash gap": round(sel, 4),
        "selection pts gap": round(sel_pts, 2),
    }
    lines = [
        f"- Cash builds: shown Cash % minus cashed {gap:+.1%} ({gap_se:.1%}); random same-pool lineups "
        f"{rnd:+.1%} ({rnd_se:.1%}); the part due to selection {sel:+.1%} ({sel_se:.1%}), "
        f"{sel_pts:+.1f} ({sel_pts_se:.1f}) points a week",
    ]

    def cleared(fix: str) -> list[str]:
        c = summary["fixes"].get(("cash", fix), {})
        return [
            label
            for col, label in (("realized", "points"), ("cashed", "cash rate"))
            if passes(c.get(col, {}))
        ]

    rep_runs = bool(summary["fixes"].get(("cash", "noise_0.10_rep"), {}).get("realized"))
    clearing = []
    for fix in REFRESH_FIXES:
        pts, rate = (summary["fixes"].get(("cash", fix), {}).get(c, {}) for c in ("realized", "cashed"))
        gpp = summary["fixes"].get(("gpp", fix), {}).get("realized", {})
        if not pts:
            continue
        on = cleared(fix)
        # the noise pick counts only when its independent rerun (other draws) clears too: one pass is luck
        unreplicated = fix == "noise_cv" and on and rep_runs and not cleared("noise_0.10_rep")
        ok = bool(on) and not unreplicated
        metrics.update(
            {
                f"{fix} cash pts": round(pts["mean"], 2),
                f"{fix} cash rate": round(rate["mean"], 4),
                f"{fix} gpp pts": round(gpp.get("mean", float("nan")), 2),
                f"{fix} clears": ok,
            }
        )
        clearing += [f"{fix} ({', '.join(on)})"] if ok else []
        note = ""
        if ok:
            note = f"; clears the bar on {' and '.join(on)}"
        elif unreplicated:
            note = f"; clears on {' and '.join(on)}, but its rerun does not"
        lines.append(
            f"- {fix}: cash builds {pts['mean']:+.1f} pts ({pts['se']:.1f}, {pts['better']}/{pts['weeks']} "
            f"weeks), cash rate {rate['mean']:+.1%} ({rate['se']:.1%}, {rate['better']}/{rate['weeks']}); "
            f"GPP builds {gpp.get('mean', float('nan')):+.1f} pts{note}"
        )
    shift = summary["calibration"]["cash"]["gain"]["shift"]
    metrics.update(
        {"display shift brier gain": round(shift["mean"], 4), "display shift clears": passes(shift)}
    )
    lines.append(
        f"- Display shift: cash Brier gain {shift['mean']:+.3f} ({shift['se']:.3f}, "
        f"{shift['better']}/{shift['weeks']} weeks){'; clears the bar' if passes(shift) else ''}"
    )
    head = (
        f"Optimizer's curse, weeks {', '.join(map(str, weeks))}: shown Cash % {gap:+.1%} against cashed, "
        f"{sel:+.1%} of it from selection; "
        + (
            f"clearing the bar (a candidate, not a ship: Sam decides): {', '.join(clearing)}."
            if clearing
            else "no fix clears the bar."
        )
    )
    return {"headline": [head, "", *lines], "metrics": metrics, "ship_bar": SHIP_BAR}


def main(argv: Sequence[str] | None = None) -> None:
    """`python -m dfs.research.curse run|replicate|assemble|refresh [weeks]`: the builder jobs (resumable),
    then the tables."""
    import pickle
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    step = args.pop(0) if args else "assemble"
    weeks = [int(w) for w in args[0].split(",")] if args else list(range(1, 7))
    if step == "run":
        run_all(weeks)
        return
    if step == "refresh":  # what `dfs research refresh` runs, through the given last week
        out = refresh_summary(SEASON, max(weeks))
        print("\n".join(out["headline"]))
        return
    if step == "replicate":  # a second, independent set of noise draws for the sigma that was chosen
        sigma = NOISE_SIGMAS[0]
        draws = range(NOISE_DRAWS, 2 * NOISE_DRAWS)
        run_all(weeks, jobs=[(w, f"noise_{sigma:.2f}_{d}") for d in draws for w in weeks])
        return
    tables = assemble(Study(weeks))
    (OUT_DIR / "tables.pkl").write_bytes(pickle.dumps(tables))
    tables["rows"].to_csv(OUT_DIR / "rows.csv", index=False)
    print(f"wrote {OUT_DIR / 'tables.pkl'}")


if __name__ == "__main__":
    main()
