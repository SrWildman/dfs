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
- `offset`: field mean + an offset in the simulator's own points, calibrated so that the real double-up
  lineups' shown Cash % adds up to how many of them cashed (`calibrated_value`);
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
    "The shown Cash % changes (display only, behind `[sim.lines] du_line_model`) only if, held out by week, "
    "its Brier score AND its calibration gap (|shown - cashed|) are both better than today's by more than "
    "2 SE of the per-week paired difference, over every lineup set."
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
    return pd.DataFrame(rows, columns=["set", "names"])


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
# that the real double-up lineups' shown Cash % adds up to how many of them cashed. Those lineups are the one
# set whose cash rate is pinned by the contest itself (43.5% of a double-up is paid), so they calibrate the
# level without using the builds the number is shown on.
CALIBRATION_SET = "field (double-up)"
OFFSET_GRID = np.arange(-10.0, 30.01, 0.5)


def calibrated_value(
    weeks: dict[int, tuple[pd.DataFrame, WeekDraws]], exclude: int, line_for, grid: np.ndarray
) -> float:
    """The `grid` value whose lines (`line_for(draws, value)`) make the calibration set's shown Cash % sum to
    its cashed count over every week but `exclude` (the first such value when none has a calibration set:
    nothing to fit)."""
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
            miss += float(p.sum() - frame.loc[mask, "cashed"].sum())
        if best_miss is None or abs(miss) < best_miss:
            best, best_miss = float(value), abs(miss)
    return best


# ---------------------------------------------------------------------------------------------------------
# The retro
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Retro:
    rows: pd.DataFrame  # week, set, names, realized, cashed, p_<setting>
    lines: pd.DataFrame  # week, setting, real line, the line's p10 / p50 / p90 over draws
    models: dict[int, du_line.DuLineModel]
    offsets: dict[
        int, dict[str, float]
    ]  # week -> the calibrated offset and the herd model's calibrated shift


def run(weeks: list[int], *, n_sims: int = 5000, seed: int = 0, log=print) -> Retro:
    from dfs.research import curse
    from dfs.sim import contest_retro

    study = curse.Study(weeks)
    jobs = curse.load_jobs()
    real = contest_retro.load_real_contests()
    frames = {}
    for w in sorted({c.week for c in real if c.fmt == "cash"}):
        try:
            frames[w] = contest_retro.week_slate_frame(w)
        except FileNotFoundError:
            continue
    stats = contest_stats(real, frames)
    rng = np.random.default_rng(seed)
    simulated: dict[int, tuple[pd.DataFrame, WeekDraws]] = {}
    for week in weeks:
        wi = study.weeks[week]
        lus = week_lineups(wi, study.contests, jobs, rng)
        draws = simulate_week(wi, list(dict.fromkeys(lus["names"])), herd_names(wi.edge), n_sims=n_sims)
        if draws is None:
            log(f"week {week}: no ownership, skipped")
            continue
        index = {k: i for i, k in enumerate(draws.kept)}
        frame = lus[lus["names"].isin(index)].copy()
        frame["week"] = week
        frame["realized"] = [sum(wi.actual.get(n, np.nan) for n in k) for k in frame["names"]]
        frame = frame[frame["realized"].notna()].copy()
        frame["cashed"] = (frame["realized"] >= wi.cash_line).astype(float)
        frame["line"] = wi.cash_line
        frame["col"] = frame["names"].map(index)
        simulated[week] = (frame, draws)
        log(f"week {week}: {len(frame)} lineups, herd {'found' if draws.herd is not None else 'none'}")

    parts, line_rows, models, offsets = [], [], {}, {}
    for week, (frame, draws) in simulated.items():
        model = du_line.fit(stats, exclude_week=week)
        no_herd = replace(model, herd_share=0.0)
        offset = calibrated_value(simulated, week, lambda d, v: d.field_mean + v, OFFSET_GRID)

        def herd_line(d: WeekDraws, v: float, model=model) -> np.ndarray:
            return du_line.lines(d.field_mean, d.herd, replace(model, shift=v, shift_sd=0.0))

        shift = calibrated_value(simulated, week, herd_line, OFFSET_GRID)
        models[week], offsets[week] = model, {"offset": offset, "herd_shift": shift}
        line_by = {
            "today": today_line(draws),
            "shift": du_line.lines(draws.field_mean, None, no_herd),
            "du_line": du_line.lines(draws.field_mean, draws.herd, model),
            "offset": draws.field_mean + offset,
            "herd_offset": herd_line(draws, shift),
        }
        frame = frame.copy()
        cols = frame["col"].to_numpy()
        for setting in SETTINGS:
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
    return Retro(pd.concat(parts, ignore_index=True), pd.DataFrame(line_rows), models, offsets)


def brier(p: pd.Series, y: pd.Series) -> float:
    return float(((p - y) ** 2).mean())


def calibration(rows: pd.DataFrame) -> pd.DataFrame:
    """Per setting and set (and all sets together): lineups, Cash % shown, cash rate, the gap and its SE over
    weeks, Brier."""
    out = []
    for setting in SETTINGS:
        p = f"p_{setting}"
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
    for setting in SETTINGS[1:]:
        for group in ("all", *SETS):
            for p in paired_weeks(rows, setting, group):
                lines.append(f"- {p.line()}")
    lines += ["", "The real line against each setting's 10-90% band of lines:", ""]
    lines.append(retro.lines.round(1).to_string(index=False))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - the study, run by hand
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", default="2,3,4,5,6,7")
    parser.add_argument("--sims", type=int, default=5000)
    args = parser.parse_args(argv)
    weeks = [int(w) for w in args.weeks.split(",")]
    retro = run(weeks, n_sims=args.sims)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    retro.rows.assign(names=retro.rows["names"].map("|".join)).to_csv(OUT_DIR / "rows.csv", index=False)
    text = report(retro)
    (OUT_DIR / "report.md").write_text(text)
    print(text)


if __name__ == "__main__":  # pragma: no cover
    main()
