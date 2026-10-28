"""The `OurDST` study: is our own DST projection (`dfs.model.dst`) better than what we use today?

    python -m dfs.model.dst_study [--out <file.md>]

Offline, read only (the nflverse cache from `dfs model fetch` and the scored weeks in `data/results/`).

1. **Held out, 2023-2025.** Fit on 2014-2022 (2014 only builds the rates), judge every team-game of 2023-2025:
   MAE, within-week Spearman rho, bias, and P(DST >= 10 / 12 / 14) (Brier). Beside it: a Vegas-only line
   (DK points on the opponent's implied total, the spread and home, fitted on the same seasons) and trailing-8
   DST points. UM's DST row was judged on the same seasons in `docs/MODEL.md` (its numbers are quoted, not
   rerun).
2. **2026 so far, against TFFB and CalPts.** Fit on 2014-2025; project every DST in each scored week from
   games before that week only; judge on Model Check's population (rosterable pool, a real stat line) against
   TFFB's `ProjPts`, the walk-forward `CalPts` (Week 2 on, `calibration.backtest_frame`), UM and the Vegas
   line. Boom% (P(points >= 4x salary)) against the distribution engine on CalPts, which is what EdgeRaw
   shows.

**Ship bar** (the project's): better by more than 2 standard errors of the per-week paired difference, and in
most weeks. Nothing here changes CalPts or the builds.

The Vegas line is the nflverse schedule's (the same one UM reads; for past games it is the closing line, about
the lock-time line a DFS projection sees).
"""

from __future__ import annotations

import argparse
import glob
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.calibration import SHADOW_RACE_COLUMNS, backtest_frame, score_predictions
from dfs.model import data, distribution, dst
from dfs.model.history import team_game_context
from dfs.paths import DATA_DIR
from dfs.probabilities import BOOM_MULTIPLE
from dfs.results_analysis import _spearman, usable

FIT_SEASONS = (2014, 2022)
HOLDOUT_SEASONS = (2023, 2025)
SEASON = 2026
BOOM_THRESHOLDS = (10, 12, 14)
MIN_WEEK_ROWS = 8  # a week needs this many defenses for a rank correlation
TRAILING_GAMES = 8
SHIP_SE = 2.0
# UM's DST row on the same held-out seasons (docs/MODEL.md, "DST 4,670 / 1,632"): quoted, not rerun.
UM_HOLDOUT = {"MAE": 4.683, "Rho": 0.187}
CALPTS_SET = "Weeks 2+ (CalPts)"


def load_inputs(season: int = SEASON) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """`dst_games`, `qb_games` and the schedule from the model cache, through `season`."""
    seasons = list(range(data.FIRST_SEASON, season + 1))
    stats_team = data.read_season_files("stats_team", seasons)
    stats_player = data.read_season_files("stats_player", seasons)
    games = data.read_games()
    return dst.build_dst_games(stats_team, stats_player, games), dst.build_qb_games(stats_player), games


def vegas_line(train: pd.DataFrame) -> np.ndarray:
    """Least squares of DK points on [1, opp_implied, spread, home]: the "just use the line" baseline."""
    x = np.c_[np.ones(len(train)), train[["opp_implied", "spread", "home"]].to_numpy(dtype=float)]
    beta, *_ = np.linalg.lstsq(x, train["dk"].to_numpy(dtype=float), rcond=None)
    return beta


def apply_vegas(beta: np.ndarray, features: pd.DataFrame) -> np.ndarray:
    x = np.c_[np.ones(len(features)), features[["opp_implied", "spread", "home"]].to_numpy(dtype=float)]
    return x @ beta


def trailing_points(dst_games: pd.DataFrame) -> pd.Series:
    """Each team-game's mean DK points over the defense's previous `TRAILING_GAMES` games (any season)."""
    ordered = dst_games.sort_values(["team", "t"])
    trail = ordered.groupby("team")["dk"].transform(
        lambda s: s.shift().rolling(TRAILING_GAMES, min_periods=1).mean()
    )
    return trail.reindex(dst_games.index)


# -----------------------------------------------------------------------------------------------------
# Scoring
# -----------------------------------------------------------------------------------------------------


def week_metrics(frame: pd.DataFrame, columns: list[str], actual: str = "dk") -> pd.DataFrame:
    """One row per (week, column): n, MAE, rho (NaN under `MIN_WEEK_ROWS`), bias (actual - projected). `frame`
    needs `season`, `week`, `actual` and each column; only rows where every column exists are scored."""
    df = frame.dropna(subset=[actual, *columns])
    rows = []
    for (season, week), g in df.groupby(["season", "week"]):
        for col in columns:
            err = g[actual] - g[col]
            rho = _spearman(g[col], g[actual]) if len(g) >= MIN_WEEK_ROWS else float("nan")
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "Source": col,
                    "n": len(g),
                    "MAE": float(err.abs().mean()),
                    "Rho": rho,
                    "Bias": float(err.mean()),
                }
            )
    return pd.DataFrame(rows)


def summary(weekly: pd.DataFrame) -> pd.DataFrame:
    """Per source: rows, MAE and bias weighted by rows, rho as the mean over weeks."""
    out = []
    for source, g in weekly.groupby("Source", sort=False):
        w = g["n"].to_numpy(dtype=float)
        out.append(
            {
                "Source": source,
                "n": int(w.sum()),
                "Weeks": len(g),
                "MAE": float(np.average(g["MAE"], weights=w)),
                "Rho": float(g["Rho"].mean()),
                "Bias": float(np.average(g["Bias"], weights=w)),
            }
        )
    return pd.DataFrame(out)


@dataclass(frozen=True)
class Paired:
    """`ours` minus `other`, per week: mean, standard error across weeks, z, and the weeks ours was better."""

    metric: str
    ours: str
    other: str
    mean: float
    se: float
    weeks: int
    better: int

    @property
    def z(self) -> float:
        return self.mean / self.se if self.se > 0 else float("nan")

    @property
    def passes(self) -> bool:
        """Better by more than `SHIP_SE` SE (lower MAE / higher rho / lower Brier) and in most weeks."""
        sign = 1.0 if self.metric == "Rho" else -1.0
        return sign * self.z > SHIP_SE and self.better > self.weeks / 2

    def line(self) -> str:
        verdict = "PASSES" if self.passes else "does not pass"
        return (
            f"{self.metric} {self.ours} - {self.other}: {self.mean:+.3f} "
            f"(SE {self.se:.3f}, z {self.z:+.1f}), better in {self.better} of {self.weeks} weeks: {verdict}"
        )


def paired(weekly: pd.DataFrame, metric: str, ours: str, other: str) -> Paired:
    """The per-week paired difference of `metric` between two sources scored on the same rows."""
    a = weekly[weekly["Source"] == ours].set_index(["season", "week"])[metric]
    b = weekly[weekly["Source"] == other].set_index(["season", "week"])[metric]
    diff = (a - b).dropna()
    n = len(diff)
    se = float(diff.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    better = int((diff > 0).sum()) if metric == "Rho" else int((diff < 0).sum())
    return Paired(metric, ours, other, float(diff.mean()) if n else float("nan"), se, n, better)


def brier_by_week(frame: pd.DataFrame, probs: dict[str, str], hit: str) -> pd.DataFrame:
    """Per (week, probability column): n and Brier score against the 0/1 column `hit`. Shaped like
    `week_metrics` (metric column `Brier`) so `paired` reads it."""
    df = frame.dropna(subset=[hit, *probs.values()])
    rows = []
    for (season, week), g in df.groupby(["season", "week"]):
        for label, col in probs.items():
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "Source": label,
                    "n": len(g),
                    "Brier": float(((g[col] - g[hit]) ** 2).mean()),
                    "Mean": float(g[col].mean()),
                    "Rate": float(g[hit].mean()),
                }
            )
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------------------------------
# 1. Held out
# -----------------------------------------------------------------------------------------------------


@dataclass
class HoldoutResult:
    model: dst.DstModel
    frame: pd.DataFrame
    weekly: pd.DataFrame
    booms: pd.DataFrame


def holdout(dst_games: pd.DataFrame, qb_games: pd.DataFrame) -> HoldoutResult:
    model = dst.fit(dst_games, qb_games, FIT_SEASONS)
    train = dst.training_frame(dst_games, qb_games, model.priors, model.params)
    train = train[train["season"].between(*model.fit_seasons)]
    beta = vegas_line(train)

    test = dst_games[dst_games["season"].between(*HOLDOUT_SEASONS)]
    pred, draws = dst.predict(model, test, dst_games, qb_games)
    feats = dst.dst_features(test, dst_games, qb_games, model.priors, model.params)
    frame = test.copy()
    frame["OurDST"] = pred["dst_mean"]
    frame["Vegas"] = apply_vegas(beta, feats)
    frame["Trailing8"] = trailing_points(dst_games).reindex(test.index)
    weekly = week_metrics(frame, ["OurDST", "Vegas", "Trailing8"])

    probs = {}
    for t in BOOM_THRESHOLDS:
        frame[f"hit{t}"] = (frame["dk"] >= t).astype(float)
        frame[f"ours{t}"] = dst.prob_at_least(draws, float(t))
        frame[f"eng{t}"] = _engine(frame["OurDST"], t)
        frame[f"vegas_eng{t}"] = _engine(frame["Vegas"], t)
        probs[t] = brier_by_week(
            frame,
            {"OurDST": f"ours{t}", "Engine on OurDST": f"eng{t}", "Engine on Vegas": f"vegas_eng{t}"},
            f"hit{t}",
        ).assign(Threshold=t)
    return HoldoutResult(model, frame, weekly, pd.concat(probs.values(), ignore_index=True))


def _engine(mean: pd.Series, threshold: float | pd.Series) -> np.ndarray:
    """The shipped distribution engine's P(DST >= threshold) at a projected mean (NaN where the mean is)."""
    m = pd.to_numeric(mean, errors="coerce").to_numpy(dtype=float)
    t = np.broadcast_to(np.asarray(threshold, dtype=float), m.shape).copy()
    out = np.full(m.shape, np.nan)
    ok = ~np.isnan(m) & ~np.isnan(t)
    if ok.any():
        out[ok] = distribution.prob_at_least_many("DST", m[ok], t[ok])
    return out


# -----------------------------------------------------------------------------------------------------
# 2. 2026 against TFFB and CalPts
# -----------------------------------------------------------------------------------------------------


def load_scored(season: int = SEASON) -> pd.DataFrame:
    paths = sorted(glob.glob(str(DATA_DIR / "results" / f"scored_{season}_w*.csv")))
    if not paths:
        raise FileNotFoundError(f"no scored weeks for {season} in data/results")
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


def slate_rows(
    frame: pd.DataFrame, games: pd.DataFrame, season: int, qb_games: pd.DataFrame | None = None
) -> pd.DataFrame:
    """`dst.ROW_COLUMNS` for every DST row of `frame` (`Team`, `week`) from the schedule: opponent, Vegas,
    roof and the opponent's starting QB. Where the schedule names no starter yet (a game not played, or
    nflverse not updated), the opponent's starter in its previous game (`dst.previous_starters`, given
    `qb_games`): an unknown QB would otherwise read as a rookie. Index follows `frame`; a team with no game
    that week is dropped."""
    sched = games[(games["season"] == season) & (games["game_type"] == "REG")]
    ctx = team_game_context(sched)
    side = pd.concat(
        [
            pd.DataFrame(
                {
                    "game_id": sched["game_id"],
                    "week": sched["week"],
                    "team": sched["home_team"].map(data.normalize_team),
                    "opp": sched["away_team"].map(data.normalize_team),
                    "opp_qb_id": sched["away_qb_id"],
                }
            ),
            pd.DataFrame(
                {
                    "game_id": sched["game_id"],
                    "week": sched["week"],
                    "team": sched["away_team"].map(data.normalize_team),
                    "opp": sched["home_team"].map(data.normalize_team),
                    "opp_qb_id": sched["home_qb_id"],
                }
            ),
        ],
        ignore_index=True,
    )
    side = side.merge(ctx, on=["game_id", "team"], how="left")
    side["dome"] = side["game_id"].map(sched.set_index("game_id")["roof"].isin(dst.DOME_ROOFS).astype(int))
    rows = pd.DataFrame(
        {"team": frame["Team"].map(data.normalize_team), "week": pd.to_numeric(frame["week"])},
        index=frame.index,
    )
    rows["_i"] = rows.index
    merged = rows.merge(side, on=["team", "week"], how="inner").set_index("_i")
    merged.index.name = None
    merged["season"] = season
    if qb_games is not None:
        merged["opp_qb_id"] = merged["opp_qb_id"].astype(object)  # all-NaN arrives as float
        missing = merged["opp_qb_id"].isna()
        if missing.any():
            merged.loc[missing, "opp_qb_id"] = dst.previous_starters(merged[missing], qb_games)
    return merged[dst.ROW_COLUMNS]


@dataclass
class SeasonResult:
    frame: pd.DataFrame
    weekly: pd.DataFrame
    booms: pd.DataFrame


def season_race(
    dst_games: pd.DataFrame,
    qb_games: pd.DataFrame,
    games: pd.DataFrame,
    scored: pd.DataFrame,
    season: int = SEASON,
) -> SeasonResult:
    model = dst.fit(dst_games, qb_games, (data.FIRST_SEASON, season - 1))
    train = dst.training_frame(dst_games, qb_games, model.priors, model.params)
    beta = vegas_line(train[train["season"].between(*model.fit_seasons)])

    frame = backtest_frame(scored, season=season)
    week1 = scored[pd.to_numeric(scored["week"]) == scored["week"].min()]
    frame = pd.concat([usable(week1, "pool").assign(CalPts=np.nan), frame], ignore_index=True)
    frame = frame[frame["Position"] == "DST"].reset_index(drop=True)
    rows = slate_rows(frame, games, season, qb_games)
    pred, draws = dst.predict(model, rows, dst_games, qb_games)
    feats = dst.dst_features(rows, dst_games, qb_games, model.priors, model.params)
    frame["OurDST"] = pred["dst_mean"].reindex(frame.index)
    frame["Vegas"] = pd.Series(apply_vegas(beta, feats), index=rows.index).reindex(frame.index)
    frame["dk"] = pd.to_numeric(frame["DkActual"], errors="coerce")
    frame["season"] = season
    for col in ("ProjPts", "CalPts", "UmPts", "Salary"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.rename(columns={"ProjPts": "TFFB", "UmPts": "UM"})
    weekly = pd.concat(
        [
            week_metrics(frame, ["OurDST", "TFFB", "Vegas", "UM"]).assign(Set="Weeks 1+"),
            week_metrics(frame, ["OurDST", "TFFB", "CalPts", "Vegas"]).assign(Set=CALPTS_SET),
        ],
        ignore_index=True,
    )

    boom_line = BOOM_MULTIPLE * frame["Salary"] / 1000.0
    frame["boom_hit"] = (frame["dk"] >= boom_line).astype(float).where(frame["Salary"].notna())
    ours = np.full(len(frame), np.nan)
    pos = frame.index.get_indexer(rows.index)
    ours[pos] = dst.prob_at_least(draws, boom_line.reindex(rows.index).to_numpy(dtype=float))
    frame["Boom OurDST"] = ours
    frame["Boom CalPts"] = _engine(frame["CalPts"], boom_line)
    frame["Boom TFFB"] = _engine(frame["TFFB"], boom_line)
    booms = brier_by_week(
        frame, {"OurDST": "Boom OurDST", "CalPts": "Boom CalPts", "TFFB": "Boom TFFB"}, "boom_hit"
    )
    return SeasonResult(frame, weekly, booms)


# -----------------------------------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------------------------------


def _table(df: pd.DataFrame, digits: int = 3) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = [f"{v:.{digits}f}" if isinstance(v, float) else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _boom_summary(booms: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    out = []
    for key, g in booms.groupby(by, sort=False):
        w = g["n"].to_numpy(dtype=float)
        key = key if isinstance(key, tuple) else (key,)
        out.append(
            {
                **dict(zip(by, key, strict=True)),
                "n": int(w.sum()),
                "Brier": float(np.average(g["Brier"], weights=w)),
                "Shown": float(np.average(g["Mean"], weights=w)),
                "Real": float(np.average(g["Rate"], weights=w)),
            }
        )
    return pd.DataFrame(out)


def report(hold: HoldoutResult, race: SeasonResult) -> str:
    m = hold.model
    lines = [
        "# OurDST: our own DST projection (shadow study)",
        "",
        f"Fit {m.fit_seasons[0]}-{m.fit_seasons[1]} ({m.train_rows} team-games); "
        f"held out {HOLDOUT_SEASONS[0]}-{HOLDOUT_SEASONS[1]}; {SEASON} refit through {SEASON - 1}.",
        "",
        "## 1. Held out, 2023-2025 (every team-game)",
        "",
        _table(summary(hold.weekly)),
        "",
        f"UM's DST row on the same seasons (docs/MODEL.md): MAE {UM_HOLDOUT['MAE']}, "
        f"rho {UM_HOLDOUT['Rho']}.",
        "",
    ]
    for other in ("Vegas", "Trailing8"):
        for metric in ("MAE", "Rho"):
            lines.append("- " + paired(hold.weekly, metric, "OurDST", other).line())
    lines += ["", "P(DST >= T), Brier (lower is better), shown vs real rate:", ""]
    lines.append(_table(_boom_summary(hold.booms, ["Threshold", "Source"]), 4))
    lines.append("")
    for t in BOOM_THRESHOLDS:
        b = hold.booms[hold.booms["Threshold"] == t]
        lines.append(f"- P(>={t}) " + paired(b, "Brier", "OurDST", "Engine on OurDST").line())
    lines += ["", f"## 2. {SEASON} so far against TFFB and CalPts (Model Check's DST rows)", ""]
    for label, g in race.weekly.groupby("Set", sort=False):
        lines += [f"**{label}**", "", _table(summary(g)), ""]
        others = ["TFFB", "Vegas", "UM"] if label.startswith("Weeks 1") else ["TFFB", "CalPts"]
        for other in others:
            for metric in ("MAE", "Rho"):
                lines.append("- " + paired(g, metric, "OurDST", other).line())
        lines.append("")
    lines += ["Boom% (P(points >= 4x salary)), Brier, shown vs real rate:", ""]
    lines.append(_table(_boom_summary(race.booms, ["Source"]), 4))
    lines.append("")
    for other in ("CalPts", "TFFB"):
        lines.append("- Boom% " + paired(race.booms, "Brier", "OurDST", other).line())
    lines += ["", "Per week (OurDST vs TFFB vs CalPts), rho / MAE:", ""]
    wk = race.weekly[race.weekly["Set"].str.startswith("Weeks 1")]
    pivot = wk.pivot_table(index="week", columns="Source", values=["Rho", "MAE"]).round(3)
    pivot.columns = [f"{a} {b}" for a, b in pivot.columns]
    lines.append(_table(pivot.reset_index()))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------
# Model Check and the weekly refresh
# ---------------------------------------------------------------------------------------------------------

LABEL = "OurDST (shadow)"
SHIP_BAR = (
    "OurDST feeds nothing. It would be worth putting in front of CalPts only if it beat CalPts at DST by "
    "more than 2 SE of the per-week paired difference (MAE or rho) and in most weeks."
)


def model_check_race(
    scored: pd.DataFrame,
    season: int = SEASON,
    inputs: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Model Check's SHADOW SOURCES rows for OurDST: `calibration.SHADOW_RACE_COLUMNS` with "Rated by" =
    "OurDST", the DST row of TFFB, CalPts and OurDST on the DSTs of the weeks CalPts did not train on (Week 2
    on). OurDST is fitted on every season before `season` and each week's rows see only earlier games.
    Empty before a DST has been scored."""
    empty = pd.DataFrame(columns=SHADOW_RACE_COLUMNS)
    frame = backtest_frame(scored, season=season)
    if frame.empty:
        return empty
    frame = frame[frame["Position"] == "DST"]
    if frame.empty:
        return empty
    dst_games, qb_games, games = inputs or load_inputs(season)
    model = dst.fit(dst_games, qb_games, (data.FIRST_SEASON, season - 1))
    rows = slate_rows(frame, games, season, qb_games)
    pred, _ = dst.predict(model, rows, dst_games, qb_games)
    frame = frame.assign(OurDST=pred["dst_mean"].round(2).reindex(frame.index))
    rated = frame[frame["OurDST"].notna()]
    if rated.empty:
        return empty
    table = score_predictions(rated, (("TFFB", "ProjPts"), ("CalPts", "CalPts"), (LABEL, "OurDST")))
    table = table[table["Position"] == "DST"].reset_index(drop=True)
    table.insert(0, "Rated by", "OurDST")
    return table


def refresh_summary(season: int, through: int) -> dict:
    """The weekly hook for `dfs research refresh`: OurDST against TFFB and CalPts over the season's scored
    weeks through `through`, with the paired ship bar."""
    scored = load_scored(season)
    scored = scored[pd.to_numeric(scored["week"], errors="coerce") <= through]
    dst_games, qb_games, games = load_inputs(season)
    race = season_race(dst_games, qb_games, games, scored, season)
    cal = race.weekly[race.weekly["Set"] == CALPTS_SET]
    tab = summary(cal).set_index("Source")
    vs_cal = {m: paired(cal, m, "OurDST", "CalPts") for m in ("MAE", "Rho")}
    vs_tffb = {m: paired(cal, m, "OurDST", "TFFB") for m in ("MAE", "Rho")}
    ships = any(p.passes for p in vs_cal.values())
    verdict = (
        "OurDST beats CalPts at DST" if ships else "OurDST does not beat CalPts at DST"
    ) + f" (Weeks 2-{through}, {int(tab.loc['OurDST', 'n'])} DSTs)."
    headline = [
        verdict,
        "",
        *_table(summary(cal)).splitlines(),
        "",
        *(f"- {p.line()}" for p in (*vs_cal.values(), *vs_tffb.values())),
    ]
    metrics: dict[str, float | bool] = {"weeks": int(tab.loc["OurDST", "Weeks"])}
    for source in ("OurDST", "CalPts", "TFFB"):
        metrics[f"{source} MAE"] = round(float(tab.loc[source, "MAE"]), 3)
        metrics[f"{source} rho"] = round(float(tab.loc[source, "Rho"]), 3)
    for metric, p in vs_cal.items():
        metrics[f"vs CalPts {metric} z"] = round(p.z, 2)
    metrics["beats CalPts"] = ships
    return {"headline": headline, "metrics": metrics, "ship_bar": SHIP_BAR}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="also write the report here")
    args = parser.parse_args(argv)
    dst_games, qb_games, games = load_inputs()
    text = report(holdout(dst_games, qb_games), season_race(dst_games, qb_games, games, load_scored()))
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)


if __name__ == "__main__":
    main()
