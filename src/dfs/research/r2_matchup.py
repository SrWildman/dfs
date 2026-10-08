"""R2: what makes a matchup good, and how much is it worth?

Every component is computed from games BEFORE the one being predicted (`dfs.research.rolling`), for the
defense or offense involved, and joined onto each UM player-game (or, for DST, team-game):

  def_dk           DK points the opposing defense allowed to the position. `raw`, or `adj` (schedule
                   adjusted: each game's points minus what that offense normally produces at the position,
                   its own prior 8-game mean -- so a defense that faced three great offenses is not
                   mistaken for a bad one). Lookbacks l4 / l8 / blend (season to date + last season).
  def_epa_pass/rush  EPA per pass / rush play the defense allowed (pbp).
  implied          this team's implied total.
  team_pace/opp_pace  plays per game of each side. team_proe  pass rate over expected.
  DST: the opposing offense's sacks allowed and giveaways per game, the DST points it concedes (raw / adj:
       minus the opposing defenses' own prior 8-game mean), its EPA per pass / rush, pace, implied.

Target: the residual `actual - UM` (what UM, which already carries implied total and a basic
defense-vs-position term, leaves over), and separately `actual - trailing-8` (the whole context effect).
Per position a ridge on standardized components, alpha and variant chosen by leave-one-season-out CV on
2014-2021, then scored once on 2022-2025.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.baseline import load_baseline, load_game_tables
from dfs.research.common import FIT_SEASONS, SEASONS, TEST_SEASONS, metadata, write_json
from dfs.research.pbp_features import offense_game_table
from dfs.research.rolling import LOOKBACKS, prior_mean, prior_stat
from dfs.research.stats import Z90

POSITIONS = ("QB", "RB", "WR", "TE", "DST")
ALPHAS = (1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0)
NORM_WINDOW = 8
CONTEXT_NOT_EDGE_MAE = 0.2  # a holdout MAE gain below this (points) means context, not edge

OFFENSE_COMPONENTS = (
    "def_dk",
    "def_epa_pass",
    "def_epa_rush",
    "implied",
    "team_pace",
    "opp_pace",
    "team_proe",
)
DST_COMPONENTS = (
    "opp_dst_conceded",
    "opp_sacks_allowed",
    "opp_giveaways",
    "opp_epa_pass",
    "opp_epa_rush",
    "opp_pace",
    "opp_implied",
)
SCHEDULE_ADJUSTED = ("def_dk", "opp_dst_conceded")  # the components that have an `adj` form


def components_for(position: str) -> tuple[str, ...]:
    return DST_COMPONENTS if position == "DST" else OFFENSE_COMPONENTS


def column_name(component: str, lookback: str, adjusted: bool) -> str:
    """The wide frame's column for a component under one variant."""
    if component in ("implied", "opp_implied"):
        return component
    kind = ("adj" if adjusted else "raw") if component in SCHEDULE_ADJUSTED else "x"
    return f"{component}__{kind}__{lookback}"


# --------------------------------------------------------------------------------------------------------
# Component construction
# --------------------------------------------------------------------------------------------------------


def _rolled(frame: pd.DataFrame, key: list[str], col: str, out: str) -> pd.DataFrame:
    """`frame` with `{out}__l4/l8/blend` = the prior-only statistic of `col` for each key."""
    res = frame.copy()
    for lb in LOOKBACKS:
        res[f"{out}__{lb}"] = prior_stat(frame, key, col, lb)
    return res


def defense_dk_table(pos_game: pd.DataFrame) -> pd.DataFrame:
    """Per (defense, position, game): the prior-only DK-points-allowed statistics, raw and schedule
    adjusted, under each lookback. Keyed (team = the defense, position, season, week)."""
    pg = pos_game.sort_values(["team", "position", "t"]).copy()
    pg["norm"] = prior_mean(pg, ["team", "position"], "dk", NORM_WINDOW, min_periods=2)
    d = pg.rename(columns={"team": "off", "opp": "team"})
    d["allowed_raw"] = d["dk"]
    d["allowed_adj"] = d["dk"] - d["norm"]
    d = d.sort_values(["team", "position", "t"])
    for col, kind in (("allowed_raw", "raw"), ("allowed_adj", "adj")):
        for lb in LOOKBACKS:
            d[f"def_dk__{kind}__{lb}"] = prior_stat(d, ["team", "position"], col, lb)
    keep = ["team", "position", "season", "week"] + [c for c in d.columns if c.startswith("def_dk__")]
    return d[keep]


def roll_columns(frame: pd.DataFrame, key: list[str], col: str, prefix: str) -> pd.DataFrame:
    """`{prefix}__l4/l8/blend`: the prior-only statistic of `col` under each lookback (frame's index)."""
    return pd.DataFrame({f"{prefix}__{lb}": prior_stat(frame, key, col, lb) for lb in LOOKBACKS})


def offense_stat_tables(off_game: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(offense, defense)`, both prior-only and keyed (team, season, week).

    offense: the team's own `plays` (pace), `proe`, `epa_pass`, `epa_rush` per game, each under every
             lookback (`plays__l4` ...).
    defense: the EPA per pass / rush play the team's DEFENSE allowed (`def_epa_pass__x__l4` ...), rolled
             over the defending team's own games."""
    og = off_game.sort_values(["team", "t"]).reset_index(drop=True)
    parts = [roll_columns(og, ["team"], col, col) for col in ("plays", "proe", "epa_pass", "epa_rush")]
    offense = pd.concat([og[["team", "season", "week"]], *parts], axis=1)
    d = og.rename(columns={"team": "off", "opp": "team"}).sort_values(["team", "t"]).reset_index(drop=True)
    parts = [
        roll_columns(d, ["team"], "epa_pass", "def_epa_pass__x"),
        roll_columns(d, ["team"], "epa_rush", "def_epa_rush__x"),
    ]
    defense = pd.concat([d[["team", "season", "week"]], *parts], axis=1)
    return offense, defense


def offense_conceded_table(team_game: pd.DataFrame) -> pd.DataFrame:
    """DST study: per offense, per game, the prior-only sacks allowed, giveaways and DST points conceded
    (raw, and adjusted by the facing defense's own prior 8-game mean DST score), keyed (team, season,
    week)."""
    tg = team_game.sort_values(["team", "t"]).copy()
    norm = tg[["team", "season", "week", "t", "dk"]].copy()
    norm["dst_norm"] = prior_mean(norm, ["team"], "dk", NORM_WINDOW, min_periods=2)
    faced = norm.rename(columns={"team": "opp"})[["opp", "season", "week", "dst_norm"]]
    tg = tg.merge(faced, on=["opp", "season", "week"], how="left")
    tg = tg.sort_values(["team", "t"])
    tg["conceded_raw"] = tg["dst_conceded"]
    tg["conceded_adj"] = tg["dst_conceded"] - tg["dst_norm"]
    for col, name in (("conceded_raw", "opp_dst_conceded__raw"), ("conceded_adj", "opp_dst_conceded__adj")):
        for lb in LOOKBACKS:
            tg[f"{name}__{lb}"] = prior_stat(tg, ["team"], col, lb)
    for col, name in (("sacks_allowed", "opp_sacks_allowed__x"), ("giveaways", "opp_giveaways__x")):
        for lb in LOOKBACKS:
            tg[f"{name}__{lb}"] = prior_stat(tg, ["team"], col, lb)
    keep = ["team", "season", "week"] + [c for c in tg.columns if c.startswith("opp_")]
    return tg[keep]


def _as(frame: pd.DataFrame, mapping: dict[str, str], key_from: str, key_to: str) -> pd.DataFrame:
    """The columns of `frame` starting with each mapping key, renamed to the mapping value, with its
    `team` key renamed to `key_to`; just the join keys and those columns."""
    cols = {}
    for src, dst in mapping.items():
        cols |= {c: dst + c[len(src) :] for c in frame.columns if c.startswith(src + "__")}
    out = frame[[key_from, "season", "week", *cols]].rename(columns={key_from: key_to, **cols})
    return out


def matchup_features(
    base: pd.DataFrame, pos_game: pd.DataFrame, team_game: pd.DataFrame, off_game: pd.DataFrame
) -> pd.DataFrame:
    """The baseline rows with every component under every variant (`column_name`), prior-only. Offense
    rows get the opposing defense's and their own offense's components; DST rows the opposing offense's."""
    def_dk = defense_dk_table(pos_game)
    offense, defense = offense_stat_tables(off_game)
    conceded = offense_conceded_table(team_game)
    key = ["season", "week"]

    off = base[base["position"] != "DST"].copy()
    off = off.merge(def_dk.rename(columns={"team": "opp"}), on=["opp", "position", *key], how="left")
    off = off.merge(defense.rename(columns={"team": "opp"}), on=["opp", *key], how="left")
    own = _as(offense, {"plays": "team_pace__x", "proe": "team_proe__x"}, "team", "team")
    off = off.merge(own, on=["team", *key], how="left")
    opp = _as(offense, {"plays": "opp_pace__x"}, "team", "opp")
    off = off.merge(opp, on=["opp", *key], how="left")

    dst = base[base["position"] == "DST"].copy()
    dst = dst.merge(conceded.rename(columns={"team": "opp"}), on=["opp", *key], how="left")
    opp_off = _as(
        offense,
        {"plays": "opp_pace__x", "epa_pass": "opp_epa_pass__x", "epa_rush": "opp_epa_rush__x"},
        "team",
        "opp",
    )
    dst = dst.merge(opp_off, on=["opp", *key], how="left")
    return pd.concat([off, dst], ignore_index=True)


# --------------------------------------------------------------------------------------------------------
# Ridge, validated by season
# --------------------------------------------------------------------------------------------------------


@dataclass
class RidgeFit:
    means: np.ndarray
    sds: np.ndarray
    weights: np.ndarray  # per 1 SD of each component, in target points
    intercept: float
    alpha: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.means) / self.sds
        z = np.where(np.isnan(z), 0.0, z)  # a missing component contributes nothing
        return self.intercept + z @ self.weights


def fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> RidgeFit:
    """Closed-form ridge on standardized columns (mean / SD from `x` itself; a missing value is the mean,
    i.e. 0 after standardizing). The intercept is the mean of y and unpenalised."""
    means = np.nanmean(x, axis=0)
    sds = np.nanstd(x, axis=0)
    sds = np.where(sds > 0, sds, 1.0)
    z = (x - means) / sds
    z = np.where(np.isnan(z), 0.0, z)
    ybar = float(y.mean())
    gram = z.T @ z + alpha * np.eye(z.shape[1])
    w = np.linalg.solve(gram, z.T @ (y - ybar))
    return RidgeFit(means, sds, w, ybar, alpha)


def season_folds(seasons: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Leave-one-season-out: one fold per distinct season, the whole season held out. Never a random row
    split -- a season's games share a schedule, a rule set and a scoring era."""
    return [
        (np.flatnonzero(seasons != s), np.flatnonzero(seasons == s)) for s in sorted(set(seasons.tolist()))
    ]


def cv_mae(x: np.ndarray, y: np.ndarray, seasons: np.ndarray, alpha: float) -> float:
    """Mean absolute error of the ridge's out-of-fold predictions (leave one season out)."""
    errs = np.empty(len(y))
    for train, held in season_folds(seasons):
        fit = fit_ridge(x[train], y[train], alpha)
        errs[held] = np.abs(y[held] - fit.predict(x[held]))
    return float(errs.mean())


def cv_intercept_mae(y: np.ndarray, seasons: np.ndarray) -> float:
    """The same CV score for a model with no components at all (just the fit-season mean residual)."""
    errs = np.empty(len(y))
    for train, held in season_folds(seasons):
        errs[held] = np.abs(y[held] - y[train].mean())
    return float(errs.mean())


def holdout_scores(y: np.ndarray, pred: np.ndarray, intercept: float, game: np.ndarray) -> dict:
    """MAE of UM (or trailing-8), of UM + fit-season mean bias, and of the model, with the paired MAE gain
    over the bias-corrected baseline, its 90% interval (clustered by game) and the R^2 gain over that
    baseline. The decision rule uses the bias-corrected gain: it isolates what the components add."""
    e_model = y - pred
    e_bc = y - intercept
    mae_base = float(np.abs(y).mean())
    mae_bc = float(np.abs(e_bc).mean())
    mae_model = float(np.abs(e_model).mean())
    diff = np.abs(e_bc) - np.abs(e_model)
    gain = float(diff.mean())
    codes = pd.factorize(game)[0]
    per = np.bincount(codes, weights=diff - gain)
    se = float(np.sqrt(per.size / max(per.size - 1, 1) * np.sum(per**2)) / len(diff))
    return {
        "mae_baseline": mae_base,
        "mae_baseline_bias_corrected": mae_bc,
        "mae_model": mae_model,
        "mae_gain_vs_baseline": mae_base - mae_model,
        "mae_gain_bias_corrected": gain,
        "mae_gain_ci90": [gain - Z90 * se, gain + Z90 * se],
        "r2_gain": float(1 - np.sum(e_model**2) / np.sum(e_bc**2)),
    }


def univariate_slopes(
    fit: pd.DataFrame, test: pd.DataFrame, columns: list[str], components, target: str
) -> dict:
    """Each component on its own: the OLS slope of the target on the component, in points per 1 SD (SD from
    the fit seasons), in the fit and in the test seasons. Ridge weights on correlated inputs are hard to
    read one at a time; these are not."""
    out = {}
    for comp, col in zip(components, columns, strict=True):
        mu, sd = fit[col].mean(), fit[col].std()
        row = {}
        for name, d in (("fit", fit), ("test", test)):
            z = ((d[col] - mu) / sd).to_numpy(float)
            y = d[target].to_numpy(float)
            ok = ~(np.isnan(z) | np.isnan(y))
            z, y = z[ok], y[ok]
            slope = float(np.cov(z, y)[0, 1] / np.var(z, ddof=1)) if len(z) > 2 and np.var(z) > 0 else np.nan
            resid = y - y.mean() - slope * (z - z.mean()) if not np.isnan(slope) else y
            se = float(np.sqrt(np.sum(resid**2) / max(len(y) - 2, 1)) / np.sqrt(np.sum((z - z.mean()) ** 2)))
            row[name] = {"slope_per_sd": slope, "se": se, "n": int(len(y))}
        out[comp] = row
    return out


def study_position(frame: pd.DataFrame, position: str, target: str) -> dict:
    """One position, one target (`resid` = actual - UM, `resid_l8` = actual - trailing-8): every
    (lookback x schedule-adjusted) variant is scored by season-CV on the fit seasons, the best one is
    refit on all fit seasons and scored on the test seasons."""
    comps = components_for(position)
    d = frame[(frame["position"] == position) & frame["um"].notna() & frame[target].notna()]
    fit = d[d["season"].isin(FIT_SEASONS)]
    test = d[d["season"].isin(TEST_SEASONS)]
    y_fit, y_test = fit[target].to_numpy(float), test[target].to_numpy(float)
    seasons_fit = fit["season"].to_numpy()
    variants = []
    adj_options = (False, True) if any(c in SCHEDULE_ADJUSTED for c in comps) else (False,)
    for lb in LOOKBACKS:
        for adjusted in adj_options:
            cols = [column_name(c, lb, adjusted) for c in comps]
            x_fit, x_test = fit[cols].to_numpy(float), test[cols].to_numpy(float)
            scores = {a: cv_mae(x_fit, y_fit, seasons_fit, a) for a in ALPHAS}
            alpha = min(scores, key=scores.get)
            model = fit_ridge(x_fit, y_fit, alpha)
            held = holdout_scores(y_test, model.predict(x_test), model.intercept, test["game_id"].to_numpy())
            variants.append(
                {
                    "lookback": lb,
                    "schedule_adjusted": adjusted,
                    "alpha": alpha,
                    "cv_mae": scores[alpha],
                    "cv_mae_by_alpha": {str(a): v for a, v in scores.items()},
                    "components": list(comps),
                    "columns": cols,
                    "holdout": held,
                    "model": model,
                }
            )
    best = min(variants, key=lambda v: v["cv_mae"])
    cv_none = cv_intercept_mae(y_fit, seasons_fit)
    m = best["model"]
    return {
        "n_fit": int(len(fit)),
        "n_test": int(len(test)),
        "cv_mae_intercept_only": cv_none,
        "cv_gain_best": cv_none - best["cv_mae"],
        "chosen": {
            "lookback": best["lookback"],
            "schedule_adjusted": best["schedule_adjusted"],
            "alpha": best["alpha"],
            "components": best["components"],
            "columns": best["columns"],
            "weights_per_sd": dict(zip(best["components"], map(float, m.weights), strict=True)),
            "means": dict(zip(best["components"], map(float, m.means), strict=True)),
            "sds": dict(zip(best["components"], map(float, m.sds), strict=True)),
            "intercept": m.intercept,
        },
        "holdout": best["holdout"],
        "univariate_slopes": univariate_slopes(fit, test, best["columns"], best["components"], target),
        "context_not_edge": bool(best["holdout"]["mae_gain_bias_corrected"] < CONTEXT_NOT_EDGE_MAE),
        "variants": [
            {k: v for k, v in var.items() if k not in ("model", "components", "columns")} for var in variants
        ],
        "schedule_adjustment": _schedule_adjustment(variants),
    }


def _schedule_adjustment(variants: list[dict]) -> dict:
    """Does the schedule-adjusted form beat the raw one? Best CV score and its holdout gain for each."""
    out = {}
    for adjusted, name in ((False, "raw"), (True, "adjusted")):
        sub = [v for v in variants if v["schedule_adjusted"] == adjusted]
        if not sub:
            continue
        best = min(sub, key=lambda v: v["cv_mae"])
        out[name] = {
            "best_lookback": best["lookback"],
            "cv_mae": best["cv_mae"],
            "holdout_mae_gain_bias_corrected": best["holdout"]["mae_gain_bias_corrected"],
        }
    if len(out) == 2:
        out["adjusted_helps_cv"] = bool(out["adjusted"]["cv_mae"] < out["raw"]["cv_mae"])
        out["adjusted_helps_holdout"] = bool(
            out["adjusted"]["holdout_mae_gain_bias_corrected"] > out["raw"]["holdout_mae_gain_bias_corrected"]
        )
    return out


def run_study(features: pd.DataFrame) -> dict:
    """R2 over every position and both targets."""
    result: dict = {"positions": {}}
    for position in POSITIONS:
        result["positions"][position] = {
            "vs_um": study_position(features, position, "resid"),
            "vs_trailing8": study_position(features, position, "resid_l8"),
        }
    return result


def write_outputs(result: dict, n: int, directory=None) -> None:
    meta = metadata("r2", SEASONS, n, alphas=list(ALPHAS), context_not_edge_mae=CONTEXT_NOT_EDGE_MAE)
    write_json("matchup_weights.json", result, meta, directory)


def run(log=lambda msg: None) -> dict:
    log("R2: building matchup components (prior-only)")
    base = load_baseline(log=log)
    pos_game, team_game = load_game_tables(log=log)
    off_game = offense_game_table(data.read_pbp())
    feats = matchup_features(base, pos_game, team_game, off_game)
    result = run_study(feats)
    result["_frame"] = feats
    out = {k: v for k, v in result.items() if k != "_frame"}
    write_outputs(out, int(feats["um"].notna().sum()))
    return result
