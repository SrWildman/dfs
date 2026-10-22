"""Ownership uncertainty in the sim, measured held out by week (Part 2 of session H's round).

Three settings, everything else equal:

- `today`: ownership is known (TFFB's Own%; the double-up curve for the generated cash fields);
- `mean`: the fitted mean (`own_error_retro.CHOSEN_SPECS`) in its place, no draws;
- `draws`: the fitted mean with the field's ownership drawn per simulation (`dfs.sim.own_error`).

The model for a week is always fitted WITHOUT that week (`own_error_retro.model_without`). Two harnesses, both
reused: session A's contest retro (`contest_retro.run_retro`: his lineups and real field lineups in every real
contest of Weeks 2 and 4-6, the generated fields' line coverage, the within-contest ranking), and session F's
curse rows (`research.curse`: today's cash and GPP builds, random lineups from the same pool and real field
lineups for Weeks 1-6, rescored here under each setting against the real lines).

Run: `python -m dfs.sim.own_error_sim_retro` (about 20 minutes; results to `data/research/own_error/`).
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.sim import contest_retro, own_error_retro

SETTINGS = ("today", "mean", "draws")
CONTEST_SETTINGS = (*SETTINGS, "per_contest")  # the contest retro also gives each contest its own field
OUT_DIR = Path("data/research/own_error")
BIG_ENTRIES = 700
CURSE_ROWS = Path("data/research/curse/rows.csv")
CURSE_SETS = ("today", "random", "field_cash", "field_gpp")


def _model_for(week: int):
    return own_error_retro.model_without(week)


# ---------------------------------------------------------------------------------------------------------
# A's contest retro, three ways
# ---------------------------------------------------------------------------------------------------------


def contest_runs(*, n_sims: int = 4000, through: int = 99, log=print) -> dict[str, dict[int, dict]]:
    out = {}
    for setting in CONTEST_SETTINGS:
        log(f"contest retro: {setting}")
        out[setting] = contest_retro.run_retro(
            through,
            n_sims=n_sims,
            log=log,
            ownership=None if setting == "today" else _model_for,
            ownership_draws=setting in ("draws", "per_contest"),
            ownership_per_contest=setting == "per_contest",
        )
    return out


def coverage(runs: dict[str, dict[int, dict]]) -> pd.DataFrame:
    """Per setting, format and line, over contests of 700+ entries: the share of real lines inside the sim's
    10-90% band (0.80 is calibrated), the band's mean width and the mean miss of its median (points)."""
    rows = []
    for setting, out in runs.items():
        cover = pd.concat([o["cover"] for o in out.values()], ignore_index=True)
        cover = cover[(cover["entries"] >= BIG_ENTRIES) & (cover["mode"] == "per_draw")]
        for (fmt, line), g in cover.groupby(["fmt", "line"]):
            rows.append(
                {
                    "setting": setting,
                    "format": fmt,
                    "line": line,
                    "contests": len(g),
                    "inside": float(g["inside"].mean()),
                    "width": float((g["p90"] - g["p10"]).mean()),
                    "median_miss": float((g["real"] - g["p50"]).mean()),
                    "abs_miss": float((g["real"] - g["p50"]).abs().mean()),
                }
            )
    return pd.DataFrame(rows)


def _contest_rows(out: dict[int, dict]) -> pd.DataFrame:
    table = pd.concat([o["table"] for o in out.values()], ignore_index=True)
    cover = pd.concat([o["cover"] for o in out.values()], ignore_index=True)
    top = cover[(cover["line"] == "top1") & (cover["mode"] == "per_draw")].set_index("contest")["real"]
    table = table.assign(
        cashed=(table["realized"] > 0).astype(float),
        top1_hit=(table["points"] >= table["contest"].map(top)).astype(float),
    )
    return table


def brier(p: pd.Series, y: pd.Series) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def contest_calibration(runs: dict[str, dict[int, dict]]) -> pd.DataFrame:
    """Per setting: today's Cash % in his double-ups (shown against cashed) and Top 1% in the GPPs of 700+
    entries (shown against finishing in the top 1%), for his lineups and the sampled field lineups together:
    mean shown, the realized rate, Brier, and the same per week (SE of shown - realized over weeks)."""
    rows = []
    for setting, out in runs.items():
        t = _contest_rows(out)
        for label, d, p, y in (
            ("Cash %, double-ups", t[t["fmt"] == "cash"], "cash_pct", "cashed"),
            (
                "Top 1%, GPPs 700+",
                t[(t["fmt"] == "gpp") & (t["entries"] >= BIG_ENTRIES)],
                "top1_pct",
                "top1_hit",
            ),
        ):
            if d.empty:
                continue
            gap = (d[p] - d[y]).groupby(d["week"]).mean()
            rows.append(
                {
                    "setting": setting,
                    "measure": label,
                    "pairs": len(d),
                    "shown": float(d[p].mean()),
                    "realized": float(d[y].mean()),
                    "gap_se_weeks": float(gap.std(ddof=1) / np.sqrt(len(gap))) if len(gap) > 1 else np.nan,
                    "brier": brier(d[p], d[y]),
                }
            )
    return pd.DataFrame(rows)


def ranking(runs: dict[str, dict[int, dict]]) -> pd.DataFrame:
    """Each setting's within-contest Spearman of today's number (Top 1% / Cash %) with realized dollars,
    against `today`'s: the mean difference over contests, its SE, contests and weeks better."""
    ranks = {s: contest_retro.ranking_table(_contest_rows(o)) for s, o in runs.items()}
    base = ranks["today"].set_index(["week", "contest", "fmt"])["today"]
    rows = []
    for setting, r in ranks.items():
        cur = r.set_index(["week", "contest", "fmt"])["today"]
        for fmt in ("gpp", "cash"):
            idx = [i for i in cur.index if i[2] == fmt and i in base.index]
            if not idx:
                continue
            diff = cur.loc[idx] - base.loc[idx]
            by_week = diff.groupby(level="week").mean()
            rows.append(
                {
                    "setting": setting,
                    "format": fmt,
                    "contests": len(idx),
                    "rho": float(cur.loc[idx].mean()),
                    "diff": float(diff.mean()),
                    "se": float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else np.nan,
                    "contests_better": int((diff > 1e-12).sum()),
                    "weeks_better": f"{int((by_week > 1e-12).sum())}/{len(by_week)}",
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------
# F's curse rows, rescored three ways
# ---------------------------------------------------------------------------------------------------------


def _week_inputs(weeks: list[int]) -> dict:
    from dfs import results_update
    from dfs.research import curse
    from dfs.sim import retro

    lines = curse.lines_through(curse.SEASON, max(weeks))
    scored = results_update.load_all_scored(curse.SEASON)
    contests = retro.load_contests()
    return {int(w): curse.week_inputs(int(w), lines=lines, scored=scored, contests=contests) for w in weeks}


def score_setting(wi, lineups: list[list[str]], setting: str, *, n_sims: int = 5000) -> dict[tuple, tuple]:
    """`{sorted names: (Cash %, Top 1%)}` for `lineups` on week `wi` under `setting` (curse's scoring, with
    the field swapped for the model's when the setting asks)."""
    from dfs import lineup_suggest as ls
    from dfs import sim_inputs
    from dfs.kickoff import parse_kickoff
    from dfs.research import curse
    from dfs.sim.field import with_ownership_draws

    specs, lines = curse.sim_lines(wi.edge, wi.typed_cash)
    field = lines.field
    if setting != "today" and field is not None:
        model = _model_for(wi.week)
        if model is not None:
            field = with_ownership_draws(
                field, sim_inputs.ownership_by_name(wi.edge), model, seed=0, draws=setting == "draws"
            )
    by_name = curse._players(wi.edge, wi.now)  # noqa: SLF001 - the curse harness's own player table
    kickoff = {str(n): parse_kickoff(s) for n, s in zip(wi.edge["Name"], wi.edge["GameStart"], strict=True)}
    lus = [[by_name[n] for n in lu] for lu in lineups if all(n in by_name for n in lu)]
    scored = ls.score(
        lus,
        specs,
        kickoff,
        cash_line=lines.cash_line,
        gpp_target=lines.gpp_target,
        n_sims=n_sims,
        field=field,
    )
    return {tuple(sorted(p.name for p in s.players)): (float(s.p_cash), float(s.p_gpp)) for s in scored}


def curse_rescore(rows: pd.DataFrame | None = None, *, log=print) -> pd.DataFrame:
    """F's rows (today's builds, random and real field lineups) with the shown Cash % / Top 1% under every
    setting."""
    rows = pd.read_csv(CURSE_ROWS) if rows is None else rows
    rows = rows[rows["variant"].isin(CURSE_SETS)].copy()
    wis = _week_inputs(sorted(int(w) for w in rows["week"].unique()))
    parts = []
    for week, g in rows.groupby("week"):
        names = sorted({n for n in g["names"]})
        lus = [n.split("|") for n in names]
        for setting in SETTINGS:
            got = score_setting(wis[int(week)], lus, setting)
            key = g["names"].map(lambda s: tuple(sorted(s.split("|"))))
            parts.append(
                g.assign(
                    setting=setting,
                    shown_cash=key.map(lambda k, got=got: got.get(k, (np.nan, np.nan))[0]),
                    shown_top1=key.map(lambda k, got=got: got.get(k, (np.nan, np.nan))[1]),
                )
            )
        log(f"curse rows: week {week} rescored")
    return pd.concat(parts, ignore_index=True)


def curse_calibration(res: pd.DataFrame) -> pd.DataFrame:
    """Per setting and set (cash builds, GPP builds, random, field): shown Cash % against the cash rate at the
    real line (gap and its SE over weeks, Brier), Top 1% shown summed against hits, and the within-week
    Spearman of the shown number with realized points (the ranking: must not get worse)."""
    from scipy.stats import spearmanr

    out = []
    sets = {
        "cash builds": (res["variant"] == "today") & (res["kind"] == "cash"),
        "GPP builds": (res["variant"] == "today") & (res["kind"] == "gpp"),
        "random": (res["variant"] == "random") & (res["kind"] == "cash"),
        "field": res["variant"].isin(["field_cash", "field_gpp"]),
    }
    for setting in SETTINGS:
        for label, m in sets.items():
            d = res[m & (res["setting"] == setting)].dropna(subset=["shown_cash", "cashed"])
            if d.empty:
                continue
            gap = (d["shown_cash"] - d["cashed"]).groupby(d["week"]).mean()
            top = d.dropna(subset=["top1"])
            score = "shown_top1" if label == "GPP builds" else "shown_cash"
            rho = [
                spearmanr(w[score], w["realized"]).statistic
                for _, w in d.groupby("week")
                if len(w) >= 10 and w[score].nunique() > 1
            ]
            out.append(
                {
                    "setting": setting,
                    "set": label,
                    "n": len(d),
                    "shown_cash": float(d["shown_cash"].mean()),
                    "cashed": float(d["cashed"].mean()),
                    "gap": float(gap.mean()),
                    "gap_se": float(gap.std(ddof=1) / np.sqrt(len(gap))) if len(gap) > 1 else np.nan,
                    "brier_cash": brier(d["shown_cash"], d["cashed"]),
                    "top1_shown": float(top["shown_top1"].sum()),
                    "top1_hits": int(top["top1"].sum()),
                    "brier_top1": brier(top["shown_top1"], top["top1"]) if len(top) else np.nan,
                    "rho": float(np.mean(rho)) if rho else np.nan,
                }
            )
    return pd.DataFrame(out)


def main() -> None:  # pragma: no cover - the study, run by hand
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = own_error_retro.load_weeks()
    for week in sorted(data):
        own_error_retro.model_without(week, data=data)
    runs = contest_runs()
    with open(OUT_DIR / "contest_runs.pkl", "wb") as fh:
        pickle.dump(runs, fh)
    print(coverage(runs).round(3).to_string(index=False))
    print(contest_calibration(runs).round(4).to_string(index=False))
    print(ranking(runs).round(4).to_string(index=False))
    res = curse_rescore()
    res.to_csv(OUT_DIR / "curse_rescored.csv", index=False)
    print(curse_calibration(res).round(4).to_string(index=False))


if __name__ == "__main__":  # pragma: no cover
    main()
