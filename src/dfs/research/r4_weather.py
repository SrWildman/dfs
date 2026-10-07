"""R4: weather, and the WIND flag.

Outdoor games only (`roof` outdoors or open -- a retractable roof that is open); a game with no wind reading
in `games.parquet` falls back to the wind in play-by-play's weather text ("Wind: SW 12 mph"), and a game with
neither is dropped. Wind and temperature are related to

  * pass-game DK points against UM (QB, WR, TE): `resid_c`, the residual `actual - UM` demeaned within
    position and season, bin by bin and for every "wind >= t" threshold, fit seasons against test seasons;
  * total points against the CLOSING total (`total_line`): is wind already priced into the number?

The rule for a wind flag, applied bin by bin: the QB's mean residual is -1 point or more with a 90% interval
(clustered by game) that excludes zero. Precipitation comes from the weather text and is indicative only.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.baseline import load_baseline
from dfs.research.common import SEASONS, metadata, split_of, write_json
from dfs.research.flags import add_split_and_demeaned, effect, evaluate
from dfs.research.stats import Z90, cluster_diff, cluster_mean, ols_cluster

OUTDOOR_ROOFS = ("outdoors", "open")
PASS_POSITIONS = ("QB", "WR", "TE")
WIND_BINS = ((0, 10, "0-9"), (10, 15, "10-14"), (15, 20, "15-19"), (20, 25, "20-24"), (25, 1000, "25+"))
TEMP_BINS = ((-100, 32, "<32"), (32, 50, "32-49"), (50, 70, "50-69"), (70, 200, "70+"))
THRESHOLDS = (10, 12, 15, 17, 20, 22, 25)
QB_RULE_POINTS = -1.0  # "meaningfully negative"
QB_RULE_TOLERANCE = 0.9  # "about -1": an effect of at least this many points counts

_WIND = re.compile(r"Wind:\s*(?:[A-Za-z]+\s+)?(\d+)")
_TEMP = re.compile(r"Temp:\s*(-?\d+)")


def parse_weather_text(text: object) -> dict:
    """Wind (mph), temperature (F) and a precipitation class (`none`, `rain`, `snow`) from nflfastR's weather
    text, e.g. 'Cloudy, rain Temp: 69° F, Humidity: 87%, Wind: SW 16 mph'. Anything unreadable is NaN / none.
    'Chance of rain' is not rain."""
    if not isinstance(text, str):
        return {"wind": np.nan, "temp": np.nan, "precip": None}
    wind = _WIND.search(text)
    temp = _TEMP.search(text)
    low = text.lower()
    if "chance" in low:
        precip = "none"
    elif "snow" in low or "flurr" in low or "sleet" in low:
        precip = "snow"
    elif any(k in low for k in ("rain", "shower", "drizzle", "storm", "thunder")):
        precip = "rain"
    else:
        precip = "none"
    return {
        "wind": float(wind.group(1)) if wind else np.nan,
        "temp": float(temp.group(1)) if temp else np.nan,
        "precip": precip,
    }


def game_weather(games: pd.DataFrame, pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per game: roof, wind and temperature (the schedule's reading, else the weather text's, with
    `wind_source` saying which), precipitation, the closing total, the points scored, and the game's pass
    rate and PROE from play-by-play."""
    text = pbp.groupby("game_id", as_index=False)["weather"].first()
    parsed = pd.DataFrame([parse_weather_text(t) for t in text["weather"]]).add_prefix("text_")
    text = pd.concat([text[["game_id"]], parsed], axis=1)
    g = games[["game_id", "season", "week", "roof", "wind", "temp", "total_line", "home_score", "away_score"]]
    g = g.merge(text, on="game_id", how="left")
    g["wind_source"] = np.where(
        g["wind"].notna(), "schedule", np.where(g["text_wind"].notna(), "text", "none")
    )
    g["wind_mph"] = g["wind"].fillna(g["text_wind"])
    g["temp_f"] = g["temp"].fillna(g["text_temp"])
    g["precip"] = g["text_precip"]
    g["total_pts"] = g["home_score"] + g["away_score"]
    g["outdoor"] = g["roof"].isin(OUTDOOR_ROOFS)
    plays = pbp[(pbp["pass"] == 1) | (pbp["rush"] == 1)]
    env = plays.groupby("game_id", as_index=False).agg(pass_rate=("pass", "mean"), proe=("pass_oe", "mean"))
    g = g.merge(env, on="game_id", how="left")
    return g.drop(columns=["wind", "temp", "text_wind", "text_temp", "text_precip"])


def wind_source_agreement(games: pd.DataFrame, pbp: pd.DataFrame) -> dict:
    """How far the weather text's wind is from the schedule's where both exist (for the data-quality note)."""
    text = pbp.groupby("game_id", as_index=False)["weather"].first()
    text["text_wind"] = [parse_weather_text(t)["wind"] for t in text["weather"]]
    both = games[["game_id", "wind"]].merge(text[["game_id", "text_wind"]], on="game_id").dropna()
    diff = (both["wind"] - both["text_wind"]).abs()
    return {
        "games_compared": int(len(both)),
        "share_identical": float((diff == 0).mean()),
        "share_within_2_mph": float((diff <= 2).mean()),
        "mean_abs_diff_mph": float(diff.mean()),
    }


def bin_label(values: pd.Series, bins) -> pd.Series:
    out = pd.Series(pd.NA, index=values.index, dtype="object")
    for lo, hi, label in bins:
        out[(values >= lo) & (values < hi)] = label
    return out


def player_weather_frame(base: pd.DataFrame, gw: pd.DataFrame) -> pd.DataFrame:
    """QB / WR / TE in UM's population with their game's weather. The residual is demeaned over ALL such
    rows (indoor and outdoor) first; only then are the outdoor games with a wind reading kept."""
    f = base[base["position"].isin(PASS_POSITIONS) & base["um"].notna()].copy()
    f = add_split_and_demeaned(f)
    f = f.merge(gw.drop(columns=["season", "week"]), on="game_id", how="left")
    return f[f["outdoor"] & f["wind_mph"].notna()].reset_index(drop=True)


def _bin_rows(f: pd.DataFrame, col: str, bins, by: str = "position") -> list[dict]:
    """Mean residual per (position, bin) in all / fit / test seasons, with a game-clustered 90% interval,
    and the difference from the calmest bin."""
    f = f.assign(_bin=bin_label(f[col], bins))
    ref = bins[0][2]
    rows = []
    for pos, d in f.groupby(by):
        for _, _, label in bins:
            row = {"position": pos, "bin": label}
            for name, sel in (
                ("all", d["split"].notna()),
                ("fit", d["split"] == "fit"),
                ("test", d["split"] == "test"),
            ):
                part = d[sel]
                cell = part[part["_bin"] == label]
                m = cluster_mean(cell["resid_c"], cell["game_id"])
                pair = part[part["_bin"].isin([label, ref])]
                dd = (
                    cluster_diff(pair["resid_c"], pair["_bin"] == label, pair["game_id"])
                    if label != ref
                    else None
                )
                row[name] = {
                    "n": m["n"],
                    "games": int(cell["game_id"].nunique()),
                    "mean_resid": m["mean"],
                    "lo90": m["lo"],
                    "hi90": m["hi"],
                    "vs_calm": None if dd is None else {k: dd[k] for k in ("diff", "lo", "hi")},
                }
            rows.append(row)
    return rows


def total_points_bins(gw: pd.DataFrame, col: str, bins) -> list[dict]:
    """Per bin of outdoor games: the closing total, the points scored, and the miss (scored - closing)."""
    g = gw[gw["outdoor"] & gw[col].notna() & gw["total_line"].notna() & gw["total_pts"].notna()].copy()
    g["split"] = split_of(g["season"])
    g["miss"] = g["total_pts"] - g["total_line"]
    g["_bin"] = bin_label(g[col], bins)
    rows = []
    for _, _, label in bins:
        row = {"bin": label}
        for name, sel in (
            ("all", g["split"].notna()),
            ("fit", g["split"] == "fit"),
            ("test", g["split"] == "test"),
        ):
            cell = g[sel & (g["_bin"] == label)]
            miss = cluster_mean(cell["miss"], cell["game_id"])
            row[name] = {
                "games": int(len(cell)),
                "mean_closing_total": float(cell["total_line"].mean()) if len(cell) else np.nan,
                "mean_points": float(cell["total_pts"].mean()) if len(cell) else np.nan,
                "mean_miss": miss["mean"],
                "miss_lo90": miss["lo"],
                "miss_hi90": miss["hi"],
                "mean_pass_rate": float(cell["pass_rate"].mean()) if len(cell) else np.nan,
                "mean_proe": float(cell["proe"].mean()) if len(cell) else np.nan,
            }
        rows.append(row)
    return rows


def ols_slope(x: np.ndarray, y: np.ndarray) -> dict:
    """OLS slope of y on x with a heteroskedasticity-robust (HC1) standard error."""
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = len(x)
    xc = x - x.mean()
    slope = float((xc * (y - y.mean())).sum() / (xc**2).sum())
    resid = y - y.mean() - slope * xc
    se = float(np.sqrt((n / (n - 2)) * ((xc**2 * resid**2).sum()) / ((xc**2).sum() ** 2)))
    return {"slope": slope, "se": se, "lo90": slope - Z90 * se, "hi90": slope + Z90 * se, "n": int(n)}


def pricing_check(gw: pd.DataFrame) -> dict:
    """Is wind already in the closing total? Points scored, the closing total and the miss (scored minus
    closing) regressed on wind in outdoor games, per +5 mph, linearly and above 10 mph only."""
    g = gw[gw["outdoor"] & gw["wind_mph"].notna() & gw["total_line"].notna() & gw["total_pts"].notna()]
    g = g.assign(miss=g["total_pts"] - g["total_line"], split=split_of(g["season"]))
    out = {}
    for name, sel in (
        ("all", g["split"].notna()),
        ("fit", g["split"] == "fit"),
        ("test", g["split"] == "test"),
    ):
        d = g[sel]
        wind = d["wind_mph"].to_numpy(float)
        hinge = np.maximum(wind - 10.0, 0.0)
        out[name] = {
            f"{y}_{kind}": {
                k: (v * 5 if k != "n" else v) for k, v in ols_slope(x, d[y].to_numpy(float)).items()
            }
            for y in ("total_pts", "total_line", "miss")
            for kind, x in (("linear", wind), ("above_10", hinge))
        }
    return out


def threshold_sweep(f: pd.DataFrame, gw: pd.DataFrame) -> dict:
    """For every `wind >= t`: the effect on each pass-game position's residual (flagged minus unflagged
    outdoor games) in all seasons, the fit seasons and the test seasons, plus the same cut on total points
    against the closing total."""
    out: dict = {"positions": {}, "total_points": []}
    for t in THRESHOLDS:
        flag = f["wind_mph"] >= t
        res = evaluate(f, flag, -1, cluster="game_id")
        for pos, parts in res["by_position"].items():
            in_pos = f["position"] == pos
            cells = {**parts, "all": effect(f[in_pos], flag[in_pos], -1, "game_id")}
            out["positions"].setdefault(pos, []).append(
                {
                    "threshold_mph": t,
                    **{k: {x: cells[k][x] for x in ("n_flagged", "diff", "lo90", "hi90")} for k in cells},
                }
            )
    g = gw[gw["outdoor"] & gw["wind_mph"].notna() & gw["total_line"].notna() & gw["total_pts"].notna()].copy()
    g["miss"] = g["total_pts"] - g["total_line"]
    g["split"] = split_of(g["season"])
    for t in THRESHOLDS:
        row = {"threshold_mph": t}
        for name, sel in (
            ("all", g["split"].notna()),
            ("fit", g["split"] == "fit"),
            ("test", g["split"] == "test"),
        ):
            d = g[sel]
            diff = cluster_diff(
                d["miss"].to_numpy(), (d["wind_mph"] >= t).to_numpy(), d["game_id"].to_numpy()
            )
            row[name] = {k: diff[k] for k in ("n_flag", "diff", "lo", "hi")}
        out["total_points"].append(row)
    return out


def joint_model(f: pd.DataFrame) -> dict:
    """Wind, rain, snow and cold fitted together (they travel together: cold, wet, windy games), per
    position, by OLS with game-clustered errors on the residual. Coefficients are points: per +5 mph of
    wind, rain (vs none), snow (vs none), and per 10 F below 50 F. Fitted on all seasons, then on the fit and
    test seasons separately."""
    names = ["wind_per_5mph", "rain", "snow", "cold_per_10F_below_50"]
    out: dict = {"terms": names}
    d = f[f["temp_f"].notna() & f["precip"].notna()]
    for pos in PASS_POSITIONS:
        out[pos] = {}
        for name, sel in (
            ("all", d["split"].notna()),
            ("fit", d["split"] == "fit"),
            ("test", d["split"] == "test"),
        ):
            sub = d[(d["position"] == pos) & sel]
            x = np.column_stack(
                [
                    sub["wind_mph"] / 5.0,
                    (sub["precip"] == "rain").astype(float),
                    (sub["precip"] == "snow").astype(float),
                    np.maximum(50.0 - sub["temp_f"], 0.0) / 10.0,
                ]
            )
            fit = ols_cluster(x, sub["resid_c"].to_numpy(), sub["game_id"].to_numpy())
            out[pos][name] = {
                "n": fit["n"],
                **{
                    term: {
                        "coef": float(fit["coef"][i + 1]),
                        "lo90": float(fit["coef"][i + 1] - Z90 * fit["se"][i + 1]),
                        "hi90": float(fit["coef"][i + 1] + Z90 * fit["se"][i + 1]),
                    }
                    for i, term in enumerate(names)
                },
            }
    return out


def recommend_threshold(bins: list[dict], sweep: dict) -> dict:
    """Two readings of "where does the wind effect become meaningfully negative for QBs":

    by bin        the first bin whose mean residual is `QB_RULE_POINTS` or worse with a 90% interval below
                  zero (all, fit and test seasons separately -- the top bins are thin).
    cumulative    the lowest `wind >= t` flag whose QB effect is at least `QB_RULE_TOLERANCE` points negative
                  with an interval below zero in BOTH the fit and the test seasons. `recommended_mph` is that
                  t, or None if no flag qualifies; `qualifying_thresholds` lists every t that does."""
    qb = [r for r in bins if r["position"] == "QB"]
    out: dict = {}
    for name in ("all", "fit", "test"):
        hit = next(
            (
                r
                for r in qb
                if r[name]["n"] > 0 and r[name]["mean_resid"] <= QB_RULE_POINTS and r[name]["hi90"] < 0
            ),
            None,
        )
        out[f"first_bin_meeting_rule_{name}"] = hit["bin"] if hit else None
    qualifying = [
        row["threshold_mph"]
        for row in sweep["positions"]["QB"]
        if all(row[k]["diff"] <= -QB_RULE_TOLERANCE and row[k]["hi90"] < 0 for k in ("fit", "test"))
    ]
    out["qualifying_thresholds"] = qualifying
    out["recommended_mph"] = min(qualifying) if qualifying else None
    return out


def run_study(base: pd.DataFrame, games: pd.DataFrame, pbp: pd.DataFrame) -> dict:
    gw = game_weather(games, pbp)
    f = player_weather_frame(base, gw)
    wind_bins = _bin_rows(f, "wind_mph", WIND_BINS)
    sweep = threshold_sweep(f, gw)
    outdoor = gw[gw["outdoor"]]
    coverage = {
        int(s): {
            "outdoor_games": int(len(d)),
            "with_wind": int(d["wind_mph"].notna().sum()),
            "wind_from_text": int((d["wind_source"] == "text").sum()),
        }
        for s, d in outdoor.groupby("season")
    }
    temp_f = f[f["temp_f"].notna()]
    precip_f = f[f["precip"].notna()].assign(precip_bin=lambda d: d["precip"])
    precip_bins = tuple((0, 0, p) for p in ("none", "rain", "snow"))
    precip_rows = []
    for pos, d in precip_f.groupby("position"):
        for _, _, label in precip_bins:
            for name, sel in (
                ("all", d["split"].notna()),
                ("fit", d["split"] == "fit"),
                ("test", d["split"] == "test"),
            ):
                cell = d[sel & (d["precip_bin"] == label)]
                m = cluster_mean(cell["resid_c"], cell["game_id"])
                precip_rows.append(
                    {
                        "position": pos,
                        "precip": label,
                        "seasons": name,
                        "n": m["n"],
                        "mean_resid": m["mean"],
                        "lo90": m["lo"],
                        "hi90": m["hi"],
                    }
                )
    return {
        "data_quality": {
            "wind_source_agreement": wind_source_agreement(games, pbp),
            "outdoor_coverage_by_season": coverage,
            "player_rows_used": int(len(f)),
            "games_used": int(f["game_id"].nunique()),
        },
        "wind_bins": wind_bins,
        "temp_bins": _bin_rows(temp_f, "temp_f", TEMP_BINS),
        "precipitation": precip_rows,
        "total_points_by_wind": total_points_bins(gw, "wind_mph", WIND_BINS),
        "total_points_by_temp": total_points_bins(gw, "temp_f", TEMP_BINS),
        "pricing_check_per_5mph": pricing_check(gw),
        "threshold_sweep": sweep,
        "joint_model": joint_model(f),
        "recommendation": {
            "rule": (
                f"QB mean residual <= {QB_RULE_POINTS} (cumulative: <= -{QB_RULE_TOLERANCE}, both periods) "
                "with a 90% interval below 0"
            ),
            **recommend_threshold(wind_bins, sweep),
        },
        "_frame_rows": int(len(f)),
    }


def write_outputs(result: dict, directory=None) -> None:
    payload = {k: v for k, v in result.items() if not k.startswith("_")}
    meta = metadata(
        "r4",
        SEASONS,
        result["_frame_rows"],
        wind_bins=[b[2] for b in WIND_BINS],
        outdoor_roofs=list(OUTDOOR_ROOFS),
    )
    write_json("weather.json", payload, meta, directory)


def run(log=lambda msg: None) -> dict:
    log("R4: joining weather to pass-game residuals")
    result = run_study(load_baseline(log=log), data.read_games(), data.read_pbp())
    write_outputs(result)
    return result
