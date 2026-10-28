"""The double-up line model (`dfs.sim.du_line`) judged held out by week: does it make the SHOWN Cash % honest?

    python -m dfs.sim.du_line_retro [--weeks 2,3,4,5,6,7] [--sims 5000]

For each week with ownership (Week 2 on), the lineups the curse study scores (`dfs.research.curse`): today's
20 cash and 20 GPP builds (its saved jobs), 300 random legal lineups from the same pool and up to 150 real
lineups from each of his double-up and GPP fields. Every lineup is simulated ONCE with today's field (the
shipped `[sim.lines]`), the pre-lock herd lineup alongside; each draw then gets five cash lines:

- `today`: field mean + 0.6 (the number on Lineups and Suggested now);
- `shift`: the line model with no herd: the double-up field's shift and its week-to-week spread, and the
  paid quantile of a normal field;
- `du_line`: the full model (the herd's own score in the draw, its share, the shift);
- `offset`: field mean + an offset in the simulator's own points, calibrated so that every entry of the
  reference double-up, weighted by copies, has a shown Cash % adding up to the paid share
  (`reference_entries`, `calibrated_value`);
- `herd_offset`: the full model with its shift calibrated the same way (no week-to-week spread).

Every model and calibration uses the OTHER weeks only (`du_line.fit(..., exclude_week=week)`). A
lineup cashed when its real points reached that week's double-up line (the GIANT $25 Double Up, as Results
records it). Measures, per setting and set: Cash % shown against the cash rate, Brier, and the per-week paired
difference against today with its standard error over weeks. The ship bar (`SHIP_BAR`): Brier and calibration
both better than today by more than 2 SE across weeks. Results go to `data/research/du_line/`.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.sim import du_line, field_gen

OUT_DIR = Path("data/research/du_line")
SETTINGS = ("today", "shift", "du_line", "offset", "herd_offset")
SETS = ("cash builds", "GPP builds", "random", "field (double-up)", "field (GPP)")
SHIP_SE = 2.0
SHIP_BAR = (
    "`[sim.lines] calibrated_cash_offset` (display only) is worth turning on only if, held out by week, the "
    "calibrated Cash %'s Brier score AND its calibration gap (|shown - cashed|) are both better than today's "
    "by more than 2 SE of the per-week paired difference, over every lineup set."
)


# ---------------------------------------------------------------------------------------------------------
# The real double-ups: what the model is fitted on
# ---------------------------------------------------------------------------------------------------------


def forecast_mean(frame: pd.DataFrame, fpts: dict[str, float]) -> float:
    """The forecast field mean on the real points: projected ownership (TFFB `Own%`, normalised to one
    lineup's nine slots) times each player's real DK points (0 when he did not score)."""
    own = pd.to_numeric(frame["tffb"], errors="coerce").fillna(0.0).clip(lower=0.0)
    if own.sum() <= 0:
        return float("nan")
    own9 = own * 9.0 / own.sum()
    pts = frame["name"].map(fpts).fillna(0.0)
    return float((own9 * pts).sum())


def contest_stats(contests: list, frames: dict[int, pd.DataFrame]) -> list[du_line.ContestStats]:
    """`du_line.ContestStats` for every real double-up in `contests` (`contest_retro.RealContest`) whose week
    has a pre-lock frame in `frames` (`contest_retro.week_slate_frame`)."""
    out = []
    for c in contests:
        if c.fmt != "cash" or c.week not in frames:
            continue
        lineups = c.standings.lineups
        points = lineups["points"].to_numpy(float)
        if len(points) < 2:
            continue
        key, _ = c.copies().most_common(1)[0]
        keys = [tuple(sorted(_norm(n) for _, n in slots)) for slots in lineups["slots"]]
        herd = np.array([k == key for k in keys])
        week_fpts: dict[str, float] = {}
        for other in contests:
            if other.week == c.week:
                week_fpts.update(other.fpts)
        out.append(
            du_line.ContestStats(
                week=int(c.week),
                herd_share=float(herd.mean()),
                cv=float(points[~herd].std() / points.mean()),
                real_mean=float(points.mean()),
                forecast_mean=forecast_mean(frames[c.week], week_fpts),
                paid=float(c.places_paid / c.entries),
            )
        )
    return out


def _norm(name: str) -> str:
    from dfs.player_join import normalize_name

    return normalize_name(name)


# ---------------------------------------------------------------------------------------------------------
# The week's lineups, simulated once
# ---------------------------------------------------------------------------------------------------------


def herd_names(edge: pd.DataFrame) -> list[str] | None:
    """The pre-lock herd: the legal lineup with the largest product of projected ownership (EdgeRaw `Own%`),
    by edge Name. None when ownership has not published."""
    own = pd.to_numeric(edge.get("Own%"), errors="coerce")
    if own is None or own.isna().all():
        return None
    frame = pd.DataFrame(
        {
            "name": edge["Name"].astype(str),
            "position": edge["Position"].astype(str),
            "team": edge["Team"].astype(str),
            "opp": edge["Opp"].astype(str) if "Opp" in edge.columns else "",
            "salary": pd.to_numeric(edge["Salary"], errors="coerce"),
            "own": own.fillna(0.0) / 100.0,
        }
    )
    slate = field_gen.Slate.from_frame(frame, "own")
    chalk = field_gen.chalk_lineups(slate, k=1)
    if len(chalk) == 0:
        return None
    return [slate.names[i] for i in chalk[0]]


def week_lineups(wi, contests: list, jobs: dict, rng: np.random.Generator) -> pd.DataFrame:
    """`set`, `names` (a tuple of edge Names): today's builds, random lineups, real field lineups."""
    from dfs.research import curse

    rows = []
    job = jobs.get((wi.week, "today"))
    for b in job["builds"] if job else []:
        rows.append(("cash builds" if b["kind"] == "cash" else "GPP builds", tuple(sorted(b["names"]))))
    for lu in curse.random_lineups(wi, curse.RANDOM_LINEUPS, rng):
        rows.append(("random", tuple(sorted(lu))))
    for fmt, label in (("cash", "field (double-up)"), ("gpp", "field (GPP)")):
        for lu in curse.field_lineups(wi, contests, fmt, curse.FIELD_LINEUPS[fmt], rng):
            rows.append((label, tuple(sorted(lu))))
    out = pd.DataFrame(rows, columns=["set", "names"]).assign(weight=1.0)
    return pd.concat([out, reference_entries(wi, contests)], ignore_index=True)


def reference_entries(wi, contests: list) -> pd.DataFrame:
    """Every distinct lineup of the week's reference double-up (the one whose real line is the week's line,
    the GIANT $25 Results records; the largest on a tie), `weight` = its entries, by edge Name: the
    calibration set. Its entries cash at the contest's paid share exactly, so the target carries no
    sampling noise. A lineup with a player the edge does not name is left out. Empty when the week has no
    double-up."""
    from dfs.player_join import normalize_name

    cash = [c for c in contests if c.week == wi.week and c.fmt == "cash" and c.places_paid > 0]
    if not cash:
        return pd.DataFrame(columns=["set", "names", "weight"])
    ref = min(cash, key=lambda c: (abs(float(c.points[c.places_paid - 1]) - wi.cash_line), -len(c.points)))
    by_norm = {normalize_name(x): str(x) for x in wi.edge["Name"]}
    rows = []
    for key, count in ref.copies.items():
        names = [by_norm.get(k) for k in key]
        if None not in names:
            rows.append((CALIBRATION_SET, tuple(sorted(names)), float(count)))
    return pd.DataFrame(rows, columns=["set", "names", "weight"])


@dataclass
class WeekDraws:
    """One week's simulation: `scores` (draws x lineups, in `kept` order), each draw's forecast field mean,
    the herd's score per draw (None without a herd) and today's offset."""

    kept: list[tuple[str, ...]]
    scores: np.ndarray
    field_mean: np.ndarray
    herd: np.ndarray | None
    offset: float


def simulate_week(
    wi, names: list[tuple[str, ...]], herd: list[str] | None, *, n_sims: int
) -> WeekDraws | None:
    """Every lineup (and the herd, last) in ONE run against today's field. None when the week has no field
    (ownership not published)."""
    from dfs import lineup_suggest as ls
    from dfs.kickoff import parse_kickoff
    from dfs.research import curse
    from dfs.sim.simulate import simulate_lineups

    specs, lines = curse.sim_lines(wi.edge, wi.typed_cash)
    if lines.field is None:
        return None
    by_name = curse._players(wi.edge, wi.now)  # noqa: SLF001 - the curse harness's own player table
    kickoff = {str(n): parse_kickoff(s) for n, s in zip(wi.edge["Name"], wi.edge["GameStart"], strict=True)}
    kept, lists = [], []
    for lu in [*names, tuple(herd) if herd else None]:
        if lu is None or not all(n in by_name for n in lu):
            continue
        seated = ls.seat([by_name[n] for n in lu], kickoff)
        spec_list = ls.lineup_from_names([p.name for p in seated], specs)
        if spec_list is None:
            continue
        kept.append(tuple(sorted(lu)))
        lists.append(spec_list)
    has_herd = bool(herd) and kept and kept[-1] == tuple(sorted(herd))
    result = simulate_lineups(lists, n_sims=n_sims, seed=ls.SEED, field=lines.field, keep_scores=True)
    scores = result.scores
    offset = float(lines.field.cash_offset)
    return WeekDraws(
        kept=kept[:-1] if has_herd else kept,
        scores=scores[:, :-1] if has_herd else scores,
        field_mean=result.cash_lines - offset,
        herd=scores[:, -1] if has_herd else None,
        offset=offset,
    )


def today_line(draws: WeekDraws) -> np.ndarray:
    return draws.field_mean + draws.offset


# The level calibration: the line offset (points, in the simulator's own units) chosen on the OTHER weeks so
# that every entry of the reference double-up, weighted by its copies, has a shown Cash % that adds up to how
# many cashed: the contest's paid share, exactly (`reference_entries`). It calibrates the level without the
# builds the number is shown on, and without the sampling noise of a sample of distinct lineups.
CALIBRATION_SET = "entries (double-up)"
OFFSET_GRID = np.arange(-10.0, 30.01, 0.5)


def calibrated_value(
    weeks: dict[int, tuple[pd.DataFrame, WeekDraws]], exclude: int, line_for, grid: np.ndarray
) -> float:
    """The `grid` value whose lines (`line_for(draws, value)`) make the calibration set's shown Cash % sum to
    its cashed count, both weighted by entries, over every week but `exclude` (the first such value when none
    has a calibration set: nothing to fit)."""
    best, best_miss = float(grid[0]), None
    for value in grid:
        miss = 0.0
        for week, (frame, draws) in weeks.items():
            mask = (frame["set"] == CALIBRATION_SET).to_numpy()
            if week == exclude or not mask.any():
                continue
            p = du_line.cash_probability(
                draws.scores[:, frame["col"].to_numpy()[mask]], line_for(draws, value)
            )
            w = frame.loc[mask, "weight"].to_numpy(float)
            miss += float(w @ p - w @ frame.loc[mask, "cashed"].to_numpy(float))
        if best_miss is None or abs(miss) < best_miss:
            best, best_miss = float(value), abs(miss)
    return best


def offset_miss(frame: pd.DataFrame, draws: WeekDraws, grid: np.ndarray) -> np.ndarray | None:
    """For a plain offset (line = field mean + v), the calibration set's weighted shown-minus-cashed at every
    `grid` value in one pass: the shown Cash % at v is the weighted share of (draw, lineup) margins over the
    field mean that reach v, so one sort of those margins answers every v. None without a calibration set.
    Equal to `calibrated_value`'s per-value sum (a test pins it)."""
    mask = (frame["set"] == CALIBRATION_SET).to_numpy()
    if not mask.any():
        return None
    cols = frame["col"].to_numpy()[mask]
    w = frame.loc[mask, "weight"].to_numpy(float)
    margins = (draws.scores[:, cols] - draws.field_mean[:, None]).astype(float)
    n = margins.shape[0]
    order = np.argsort(margins, axis=None, kind="stable")
    flat = margins.ravel()[order]
    weights = np.broadcast_to(w, margins.shape).ravel()[order] / n
    below = np.concatenate([[0.0], np.cumsum(weights)])  # weight strictly below each sorted position
    shown = w.sum() - below[np.searchsorted(flat, grid, side="left")]
    return shown - float(w @ frame.loc[mask, "cashed"].to_numpy(float))


def calibrated_offset(misses: dict[int, np.ndarray | None], exclude: int, grid: np.ndarray) -> float:
    """The `grid` offset whose summed miss over every week but `exclude` is closest to 0 (`offset_miss`)."""
    total = sum((m for w, m in misses.items() if w != exclude and m is not None), np.zeros(len(grid)))
    return float(grid[int(np.argmin(np.abs(total)))])


# ---------------------------------------------------------------------------------------------------------
# The retro
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Retro:
    rows: pd.DataFrame  # week, set, names, realized, cashed, p_<setting>
    lines: pd.DataFrame  # week, setting, real line, the line's p10 / p50 / p90 over draws
    models: dict[int, du_line.DuLineModel]
    # week -> the calibrated offset and the herd model's calibrated shift
    offsets: dict[int, dict[str, float]]
    final_offset: float = float("nan")  # the offset calibrated on EVERY week (what the display switch uses)
    calibration_weeks: tuple[int, ...] = ()  # the weeks with real double-up lineups behind it
    entries: pd.DataFrame | None = None  # the calibration set's rows (weighted by entries), each fold's p


# Each simulated week is kept (`data/research/du_line/weeks/`, gitignored), so the weekly refresh only
# simulates the week just closed. Bump the version when anything that feeds a week's draws changes.
WEEK_CACHE_VERSION = 3


def _week_cache_path(week: int, n_sims: int, seed: int) -> Path:
    return OUT_DIR / "weeks" / f"w{week:02d}_n{n_sims}_s{seed}_v{WEEK_CACHE_VERSION}.pkl"


def save_week(path: Path, frame: pd.DataFrame, draws: WeekDraws) -> None:
    """The week's lineups and draws as plain arrays (no class of this module is pickled, so a file written by
    `python -m dfs.sim.du_line_retro` reads back under `dfs research refresh`)."""
    import pickle

    payload = {
        "frame": frame,
        "kept": [list(k) for k in draws.kept],
        "scores": np.asarray(draws.scores, dtype=np.float32),
        "field_mean": np.asarray(draws.field_mean, dtype=float),
        "herd": None if draws.herd is None else np.asarray(draws.herd, dtype=float),
        "offset": float(draws.offset),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(payload, fh)


def load_week(path: Path) -> tuple[pd.DataFrame, WeekDraws]:
    import pickle

    with open(path, "rb") as fh:
        d = pickle.load(fh)  # noqa: S301 - our own file under data/
    draws = WeekDraws([tuple(k) for k in d["kept"]], d["scores"], d["field_mean"], d["herd"], d["offset"])
    return d["frame"], draws


def simulated_week(
    week: int, inputs: dict, *, n_sims: int, seed: int, cache: bool
) -> tuple[pd.DataFrame, WeekDraws] | None:
    """The week's lineups (with `realized`, `cashed`, `line` and their `col` in the draws) and its draws, from
    the cache when `cache` and it is there. `inputs`: `lines`, `scored`, `contests`, `jobs` (built once)."""
    from dfs.research import curse

    path = _week_cache_path(week, n_sims, seed)
    if cache and path.exists():
        return load_week(path)
    wi = curse.week_inputs(week, lines=inputs["lines"], scored=inputs["scored"], contests=inputs["contests"])
    rng = np.random.default_rng([seed, week])
    lus = week_lineups(wi, inputs["contests"], inputs["jobs"], rng)
    draws = simulate_week(wi, list(dict.fromkeys(lus["names"])), herd_names(wi.edge), n_sims=n_sims)
    if draws is None:
        return None
    index = {k: i for i, k in enumerate(draws.kept)}
    frame = lus[lus["names"].isin(index)].copy()
    frame["week"] = week
    frame["realized"] = [sum(wi.actual.get(n, np.nan) for n in k) for k in frame["names"]]
    frame = frame[frame["realized"].notna()].copy()
    frame["cashed"] = (frame["realized"] >= wi.cash_line).astype(float)
    frame["line"] = wi.cash_line
    frame["col"] = frame["names"].map(index)
    draws.scores = draws.scores.astype(np.float32)
    save_week(path, frame, draws)
    return frame, draws


def run(
    weeks: list[int],
    *,
    n_sims: int = 5000,
    seed: int = 0,
    cache: bool = True,
    herd_offset: bool = True,
    log=print,
) -> Retro:
    """The retro over `weeks`. `herd_offset=False` skips the herd model's calibrated shift (a grid search over
    every week's draws, several minutes): the weekly refresh needs only the plain offset."""
    from dfs import results_update
    from dfs.research import curse
    from dfs.sim import contest_retro, retro

    inputs = {
        "lines": curse.lines_through(curse.SEASON, max(weeks)),
        "scored": results_update.load_all_scored(curse.SEASON),
        "contests": retro.load_contests(),
        "jobs": curse.load_jobs(),
    }
    real = contest_retro.load_real_contests()
    frames = {}
    for w in sorted({c.week for c in real if c.fmt == "cash"}):
        try:
            frames[w] = contest_retro.week_slate_frame(w)
        except FileNotFoundError:
            continue
    stats = contest_stats(real, frames)
    simulated: dict[int, tuple[pd.DataFrame, WeekDraws]] = {}
    for week in weeks:
        got = simulated_week(week, inputs, n_sims=n_sims, seed=seed, cache=cache)
        if got is None:
            log(f"week {week}: no ownership, skipped")
            continue
        simulated[week] = got
        log(f"week {week}: {len(got[0])} lineups, herd {'found' if got[1].herd is not None else 'none'}")

    misses = {w: offset_miss(f, d, OFFSET_GRID) for w, (f, d) in simulated.items()}
    settings = SETTINGS if herd_offset else tuple(s for s in SETTINGS if s != "herd_offset")
    parts, line_rows, models, offsets = [], [], {}, {}
    for week, (frame, draws) in simulated.items():
        model = du_line.fit(stats, exclude_week=week)
        no_herd = replace(model, herd_share=0.0)
        offset = calibrated_offset(misses, week, OFFSET_GRID)

        def herd_line(d: WeekDraws, v: float, model=model) -> np.ndarray:
            return du_line.lines(d.field_mean, d.herd, replace(model, shift=v, shift_sd=0.0))

        shift = calibrated_value(simulated, week, herd_line, OFFSET_GRID) if herd_offset else float("nan")
        models[week], offsets[week] = model, {"offset": offset, "herd_shift": shift}
        line_by = {
            "today": today_line(draws),
            "shift": du_line.lines(draws.field_mean, None, no_herd),
            "du_line": du_line.lines(draws.field_mean, draws.herd, model),
            "offset": draws.field_mean + offset,
            "herd_offset": herd_line(draws, shift) if herd_offset else None,
        }
        frame = frame.copy()
        cols = frame["col"].to_numpy()
        for setting in settings:
            line = line_by[setting]
            frame[f"p_{setting}"] = du_line.cash_probability(draws.scores[:, cols], line)
            q10, q50, q90 = np.quantile(line, [0.1, 0.5, 0.9])
            real_line = float(frame["line"].iloc[0])
            line_rows.append(
                {
                    "week": week,
                    "setting": setting,
                    "real": real_line,
                    "p10": q10,
                    "p50": q50,
                    "p90": q90,
                    "inside": bool(q10 <= real_line <= q90),
                }
            )
        parts.append(frame.drop(columns=["col", "line"]))
    final = calibrated_offset(misses, -1, OFFSET_GRID)
    calibration_weeks = tuple(
        w for w, (f, _) in sorted(simulated.items()) if (f["set"] == CALIBRATION_SET).any()
    )
    every = pd.concat(parts, ignore_index=True)
    return Retro(
        every[every["set"] != CALIBRATION_SET].reset_index(drop=True),
        pd.DataFrame(line_rows),
        models,
        offsets,
        final_offset=final,
        calibration_weeks=calibration_weeks,
        entries=every[every["set"] == CALIBRATION_SET].reset_index(drop=True),
    )


def brier(p: pd.Series, y: pd.Series) -> float:
    return float(((p - y) ** 2).mean())


def calibration(rows: pd.DataFrame) -> pd.DataFrame:
    """Per setting and set (and all sets together): lineups, Cash % shown, cash rate, the gap and its SE over
    weeks, Brier."""
    out = []
    for setting in SETTINGS:
        p = f"p_{setting}"
        if p not in rows.columns:
            continue
        for label in (*SETS, "all"):
            d = rows if label == "all" else rows[rows["set"] == label]
            if d.empty:
                continue
            gap = (d[p] - d["cashed"]).groupby(d["week"]).mean()
            out.append(
                {
                    "setting": setting,
                    "set": label,
                    "n": len(d),
                    "weeks": int(d["week"].nunique()),
                    "shown": float(d[p].mean()),
                    "cashed": float(d["cashed"].mean()),
                    "gap": float(gap.mean()),
                    "gap_se": float(gap.std(ddof=1) / math.sqrt(len(gap))) if len(gap) > 1 else math.nan,
                    "brier": brier(d[p], d["cashed"]),
                }
            )
    return pd.DataFrame(out)


@dataclass(frozen=True)
class PairedWeeks:
    """`setting` minus today, per week: mean, SE over weeks, weeks better (lower)."""

    measure: str
    setting: str
    group: str
    mean: float
    se: float
    weeks: int
    better: int

    @property
    def z(self) -> float:
        return self.mean / self.se if self.se > 0 else math.nan

    @property
    def passes(self) -> bool:
        return -self.z > SHIP_SE and self.better > self.weeks / 2

    def line(self) -> str:
        verdict = "passes" if self.passes else "does not pass"
        return (
            f"{self.measure}, {self.group}: {self.setting} - today {self.mean:+.4f} (SE {self.se:.4f}, "
            f"z {self.z:+.1f}), better in {self.better} of {self.weeks} weeks: {verdict}"
        )


def paired_weeks(rows: pd.DataFrame, setting: str, group: str = "all") -> list[PairedWeeks]:
    """Brier and |calibration gap| of `setting` minus today's, per week, over `group` (a set, or "all")."""
    d = rows if group == "all" else rows[rows["set"] == group]
    out = []
    for measure in ("Brier", "|gap|"):
        diffs = []
        for _, g in d.groupby("week"):
            if measure == "Brier":
                a, b = brier(g[f"p_{setting}"], g["cashed"]), brier(g["p_today"], g["cashed"])
            else:
                a = abs(float((g[f"p_{setting}"] - g["cashed"]).mean()))
                b = abs(float((g["p_today"] - g["cashed"]).mean()))
            diffs.append(a - b)
        diff = np.array(diffs)
        n = len(diff)
        se = float(diff.std(ddof=1) / math.sqrt(n)) if n > 1 else math.nan
        out.append(PairedWeeks(measure, setting, group, float(diff.mean()), se, n, int((diff < 0).sum())))
    return out


def report(retro: Retro) -> str:
    rows = retro.rows
    lines = ["# The double-up cash line, held out by week", ""]
    lines.append("Models (each fitted without its own week):")
    for week, m in sorted(retro.models.items()):
        o = retro.offsets[week]
        lines.append(
            f"- Week {week}: herd share {m.herd_share:.3f}, cv {m.cv:.3f}, shift {m.shift:+.1f} "
            f"(sd {m.shift_sd:.1f}), paid {m.paid:.3f}, fitted on weeks {list(m.weeks)}; calibrated offset "
            f"{o['offset']:+.1f}, herd model's calibrated shift {o['herd_shift']:+.1f}"
        )
    lines += ["", "Cash % shown against the cash rate (gap = shown - cashed, its SE over weeks):", ""]
    lines.append(calibration(rows).round(4).to_string(index=False))
    lines += ["", "Paired against today, per week:", ""]
    for setting in (s for s in SETTINGS[1:] if f"p_{s}" in rows.columns):
        for group in ("all", *SETS):
            for p in paired_weeks(rows, setting, group):
                lines.append(f"- {p.line()}")
    if retro.entries is not None and not retro.entries.empty:
        lines += ["", "The calibration set (every entry of the reference double-up, weighted by copies):", ""]
        for week, g in retro.entries.groupby("week"):
            w = g["weight"].to_numpy(float)
            cashed, today = np.average(g["cashed"], weights=w), np.average(g["p_today"], weights=w)
            held = np.average(g["p_offset"], weights=w)
            lines.append(
                f"- Week {week}: {int(w.sum())} entries in {len(g)} lineups; cashed {cashed:.1%}, "
                f"shown today {today:.1%}, calibrated (held out) {held:.1%}"
            )
    lines += ["", "The real line against each setting's 10-90% band of lines:", ""]
    lines.append(retro.lines.round(1).to_string(index=False))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------
# The weekly refresh and the display switch's calibration
# ---------------------------------------------------------------------------------------------------------

FIRST_WEEK = 2  # Week 1 has no ownership, so no relative line to calibrate


def verdict(retro: Retro) -> tuple[bool, PairedWeeks, PairedWeeks]:
    """(passes, Brier pair, |gap| pair) for the calibrated offset against today, over every lineup set."""
    pair_brier, pair_gap = paired_weeks(retro.rows, "offset", "all")
    return pair_brier.passes and pair_gap.passes, pair_brier, pair_gap


def refresh_summary(season: int, through: int) -> dict:
    """The weekly hook for `dfs research refresh`: the retro over Weeks 2 to `through` (only new weeks are
    simulated), the calibrated offset's verdict against today, and the offset fitted on every week saved for
    `[sim.lines] calibrated_cash_offset` (`du_line.save_calibration`)."""
    from datetime import date

    from dfs.research import curse

    if season != curse.SEASON or through < FIRST_WEEK + 2:
        return {
            "headline": [
                f"The double-up line study needs {curse.SEASON} and three weeks from Week {FIRST_WEEK}."
            ],
            "metrics": {},
            "ship_bar": SHIP_BAR,
        }
    retro = run(list(range(FIRST_WEEK, through + 1)), herd_offset=False, log=lambda _m: None)
    passes, pair_brier, pair_gap = verdict(retro)
    du_line.save_calibration(
        du_line.OffsetCalibration(
            offset=retro.final_offset,
            weeks=retro.calibration_weeks,
            passes=passes,
            z=pair_brier.z,
            fitted=date.today().isoformat(),
        )
    )
    table = calibration(retro.rows).set_index(["setting", "set"])
    gap_today, gap_offset = table.loc[("today", "all"), "gap"], table.loc[("offset", "all"), "gap"]
    weeks = ", ".join(str(w) for w in retro.calibration_weeks)
    action = (
        "it clears the bar: `[sim.lines] calibrated_cash_offset = true` can go on"
        if passes
        else "it does not clear the bar: leave `[sim.lines] calibrated_cash_offset` off"
    )
    headline = [
        f"Cash % against field average +{retro.final_offset:g} (calibrated on the double-ups of Weeks "
        f"{weeks}): {action}.",
        f"Shown minus cashed, all lineups: today {gap_today:+.1%}, calibrated {gap_offset:+.1%}.",
        f"- {pair_brier.line()}",
        f"- {pair_gap.line()}",
    ]
    metrics: dict[str, float | bool] = {
        "offset": round(retro.final_offset, 2),
        "gap today": round(float(gap_today), 4),
        "gap calibrated": round(float(gap_offset), 4),
        "Brier diff": round(pair_brier.mean, 4),
        "Brier z": round(pair_brier.z, 2),
        "gap z": round(pair_gap.z, 2),
        "clears the bar": passes,
    }
    return {"headline": headline, "metrics": metrics, "ship_bar": SHIP_BAR}


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - the study, run by hand
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", default="2,3,4,5,6,7")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--fresh", action="store_true", help="simulate every week again (ignore the cache)")
    args = parser.parse_args(argv)
    weeks = [int(w) for w in args.weeks.split(",")]
    retro = run(weeks, n_sims=args.sims, cache=not args.fresh)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    retro.rows.assign(names=retro.rows["names"].map("|".join)).to_csv(OUT_DIR / "rows.csv", index=False)
    text = report(retro)
    (OUT_DIR / "report.md").write_text(text)
    print(text)


if __name__ == "__main__":  # pragma: no cover
    main()
