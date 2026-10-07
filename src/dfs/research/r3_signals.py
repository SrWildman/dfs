"""R3: do the "context" signals carry information beyond the projection?

Each signal is a flag on a player-game built from games BEFORE it; the test is whether flagged players beat or
miss UM (`resid = actual - UM`) relative to unflagged players of the same position and season.

  BUY↑    last-3 DK/G is at least X points (or p%) BELOW last-3 xFP/G, with xFP/G in the top half of the
          position that week.
  FADE↓   the mirror (last-3 DK/G at least X points / p% ABOVE xFP/G, same top-half condition), and, as the
          signal specifies, a TD excess over the last 3 games of at least Y touchdowns (actual rushing +
          receiving + passing TDs minus ffopportunity's expected). The mirror alone is reported too.
  USAGE↑/↓  the last 2 games against the 6 games before them: target share, carry share, red-zone share.
          Up = any metric jumped by at least its threshold, down = any dropped by it.
  Salary lag  cannot be tested: there is no historical salary in any public dataset.

Thresholds are chosen on 2014-2021 only (largest t of the effect in the expected direction, with at least
`MIN_FLAGGED` flagged rows), then reported untouched on 2022-2025. A signal is `keep` only if the effect has
the expected sign in BOTH periods, is at least `MIN_EFFECT` points in the test period, and the 90% interval of
the test-period effect excludes zero (`borderline`: all but the size -- see `verdict`). Intervals are
clustered by player (the same player is flagged in consecutive weeks).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.baseline import load_baseline
from dfs.research.common import FIT_SEASONS, OFFENSE_POSITIONS, SEASONS, TEST_SEASONS, metadata, write_json
from dfs.research.flags import MIN_EFFECT, add_split_and_demeaned, evaluate, verdict
from dfs.research.pbp_features import red_zone_usage

MIN_FLAGGED = 150  # flagged rows in the fit seasons before a threshold is eligible
BUY_ABS = (2.0, 3.0, 4.0, 5.0)
BUY_PCT = (0.15, 0.20, 0.25, 0.30, 0.35)
TD_EXCESS = (1.0, 1.5, 2.0, 2.5)
USAGE_GRID = {
    "target_share": (0.03, 0.05, 0.07, 0.10),
    "carry_share": (0.05, 0.10, 0.15, 0.20),
    "rz_share": (0.05, 0.10, 0.15, 0.20),
}
# Which positions each usage metric is meaningful for.
USAGE_POSITIONS = {
    "target_share": ("WR", "TE", "RB"),
    "carry_share": ("RB",),
    "rz_share": ("WR", "TE", "RB"),
}
RECENT, EARLIER = 2, 6


# --------------------------------------------------------------------------------------------------------
# The signal frame
# --------------------------------------------------------------------------------------------------------


def player_game_usage(sp: pd.DataFrame, ep: pd.DataFrame, rz: pd.DataFrame) -> pd.DataFrame:
    """Per offensive player-game: target share and carry share of the team's totals that game, red-zone
    opportunity share, and touchdowns scored minus expected. Teams' totals are over every QB/RB/WR/TE row."""
    s = sp[sp["position"].isin(OFFENSE_POSITIONS)].copy()
    s = s.rename(columns={"player_id": "gsis_id"})
    for c in ("targets", "carries", "receiving_tds", "rushing_tds", "passing_tds"):
        s[c] = s[c].fillna(0.0)
    key = ["team", "season", "week"]
    s["target_share"] = s["targets"] / s.groupby(key)["targets"].transform("sum").replace(0, np.nan)
    s["carry_share"] = s["carries"] / s.groupby(key)["carries"].transform("sum").replace(0, np.nan)
    exp_cols = ["rec_touchdown_exp", "rush_touchdown_exp", "pass_touchdown_exp"]
    e = ep[ep["player_id"].notna()].copy()
    e["season"] = e["season"].astype(int)  # ffopportunity ships season / week as text in some files
    e["week"] = e["week"].astype(int)
    e["td_exp"] = e[exp_cols].fillna(0.0).sum(axis=1)
    e = e.groupby(["player_id", "season", "week"], as_index=False)["td_exp"].sum()
    e = e.rename(columns={"player_id": "gsis_id"})
    out = s.merge(e, on=["gsis_id", "season", "week"], how="left")
    out["td_exp"] = out["td_exp"].fillna(0.0)
    out["td_excess"] = out["receiving_tds"] + out["rushing_tds"] + out["passing_tds"] - out["td_exp"]
    out = out.merge(
        rz[["gsis_id", "season", "week", "rz_share"]], on=["gsis_id", "season", "week"], how="left"
    )
    out["rz_share"] = out["rz_share"].fillna(0.0)
    out["target_share"] = out["target_share"].fillna(0.0)
    out["carry_share"] = out["carry_share"].fillna(0.0)
    out["t"] = out["season"] * 100 + out["week"]
    cols = ["gsis_id", "season", "week", "t", "target_share", "carry_share", "rz_share", "td_excess"]
    return out[cols].sort_values(["gsis_id", "t"]).reset_index(drop=True)


def prior_usage_features(games: pd.DataFrame) -> pd.DataFrame:
    """For each player-game: for every usage metric the mean over the previous `RECENT` games, the mean over
    the `EARLIER` games before those, and `jump = recent - earlier`; plus the summed TD excess of the
    previous 3 games. Previous = earlier rows of the same player, so the game itself never counts. A jump
    needs both windows full (8 prior games)."""
    g = games.sort_values(["gsis_id", "t"]).reset_index(drop=True)
    by = g.groupby("gsis_id", sort=False)
    out = g[["gsis_id", "season", "week"]].copy()
    for m in USAGE_GRID:
        prev = by[m].shift(1)
        grp = prev.groupby(g["gsis_id"], sort=False)
        recent = grp.transform(lambda x: x.rolling(RECENT, min_periods=RECENT).mean())
        total = grp.transform(lambda x: x.rolling(RECENT + EARLIER, min_periods=RECENT + EARLIER).sum())
        recent_sum = recent * RECENT
        earlier = (total - recent_sum) / EARLIER
        out[f"{m}_recent"] = recent
        out[f"{m}_earlier"] = earlier
        out[f"{m}_jump"] = recent - earlier
    td = by["td_excess"].shift(1).groupby(g["gsis_id"], sort=False)
    out["td_excess_l3"] = td.transform(lambda x: x.rolling(3, min_periods=3).sum())
    return out


def signal_frame(base: pd.DataFrame, usage: pd.DataFrame) -> pd.DataFrame:
    """The UM-population offense rows with the BUY / FADE inputs and the usage jumps attached, and the
    residual demeaned within (position, season)."""
    b = base[base["position"].isin(OFFENSE_POSITIONS) & base["um"].notna()].copy()
    b = b.merge(usage, on=["gsis_id", "season", "week"], how="left")
    b = add_split_and_demeaned(b)
    b["gap_abs"] = b["xfp_l3"] - b["dk_l3"]
    b["gap_pct"] = b["gap_abs"] / b["xfp_l3"].where(b["xfp_l3"] > 0)
    median = b.groupby(["season", "week", "position"])["xfp_l3"].transform("median")
    b["xfp_top_half"] = b["xfp_l3"] >= median
    return b.reset_index(drop=True)


# --------------------------------------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------------------------------------


def buy_flag(f: pd.DataFrame, x: float | None = None, pct: float | None = None) -> pd.Series:
    """Underperforming xFP by at least `x` points or `pct` of xFP, in the top half of the position."""
    gap = (f["gap_abs"] >= x) if x is not None else (f["gap_pct"] >= pct)
    return (gap & f["xfp_top_half"]).fillna(False)


def fade_flag(
    f: pd.DataFrame, x: float | None = None, pct: float | None = None, td: float | None = None
) -> pd.Series:
    """Outperforming xFP by at least `x` points or `pct` of xFP (top half of the position), and, when `td` is
    given, a last-3 TD excess of at least `td` touchdowns."""
    gap = (-f["gap_abs"] >= x) if x is not None else (-f["gap_pct"] >= pct)
    flag = gap & f["xfp_top_half"]
    if td is not None:
        flag = flag & (f["td_excess_l3"] >= td)
    return flag.fillna(False)


def usage_flag(f: pd.DataFrame, metric: str, threshold: float, direction: str) -> pd.Series:
    """The metric's last-2-vs-earlier jump is at least `threshold` up (or down), for the positions the
    metric is meaningful for."""
    jump = f[f"{metric}_jump"]
    hit = (jump >= threshold) if direction == "up" else (jump <= -threshold)
    return (hit & f["position"].isin(USAGE_POSITIONS[metric])).fillna(False)


# --------------------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------------------


def signed_t(res: dict, direction: int) -> float:
    """The fit-season t statistic in the signal's expected direction (positive = the expected sign)."""
    t = res["fit"]["t"]
    return direction * t if t == t else -np.inf


def sweep(f: pd.DataFrame, candidates: list[tuple[dict, pd.Series]], direction: int) -> dict:
    """Evaluate every candidate flag, choose on fit only, and judge the chosen one on test."""
    rows = []
    for params, flag in candidates:
        res = evaluate(f, flag, direction)
        rows.append({"params": params, "eligible": res["fit"]["n_flagged"] >= MIN_FLAGGED, **res})
    eligible = [r for r in rows if r["eligible"]]
    chosen = max(eligible, key=lambda r: signed_t(r, direction)) if eligible else None
    same_sign = [r for r in rows if r["eligible"] and direction * r["test"]["diff"] > 0]
    out = {
        "direction": "up" if direction > 0 else "down",
        "sweep": [
            {
                "params": r["params"],
                "eligible": r["eligible"],
                "fit": {k: r["fit"][k] for k in ("n_flagged", "diff", "lo90", "hi90", "t")},
                "test": {k: r["test"][k] for k in ("n_flagged", "diff", "lo90", "hi90", "t")},
            }
            for r in rows
        ],
        "eligible_thresholds": len(eligible),
        "thresholds_with_expected_sign_in_test": len(same_sign),
    }
    if chosen is None:
        return {
            **out,
            "chosen": None,
            "recommendation": "drop",
            "reasons": ["no threshold had enough flagged rows"],
        }
    return {
        **out,
        "chosen": chosen["params"],
        "fit": chosen["fit"],
        "test": chosen["test"],
        "n_per_season": chosen["n_per_season"],
        "by_position": chosen["by_position"],
        "verdict_by_position": {
            pos: verdict(parts, direction)["recommendation"] for pos, parts in chosen["by_position"].items()
        },
        **verdict(chosen, direction),
    }


def run_study(f: pd.DataFrame) -> dict:
    """All four signals."""
    buy = [({"x": x}, buy_flag(f, x=x)) for x in BUY_ABS] + [
        ({"pct": p}, buy_flag(f, pct=p)) for p in BUY_PCT
    ]
    fade_gap = [({"x": x}, fade_flag(f, x=x)) for x in BUY_ABS] + [
        ({"pct": p}, fade_flag(f, pct=p)) for p in BUY_PCT
    ]
    fade = [
        ({**g, "td_excess": y}, fade_flag(f, x=g.get("x"), pct=g.get("pct"), td=y))
        for g, _ in fade_gap
        for y in TD_EXCESS
    ]
    signals = {
        "BUY_up": sweep(f, buy, +1),
        "FADE_down": sweep(f, fade, -1),
        "FADE_down_gap_only": sweep(f, fade_gap, -1),
    }
    for direction, name in ((+1, "up"), (-1, "down")):
        per_metric = {}
        for metric, grid in USAGE_GRID.items():
            # Each metric is judged among the positions it applies to, against the unflagged players of
            # those same positions (a carry-share jump is an RB signal: not RBs against everyone).
            fm = f[f["position"].isin(USAGE_POSITIONS[metric])].reset_index(drop=True)
            cands = [({"metric": metric, "threshold": t}, usage_flag(fm, metric, t, name)) for t in grid]
            per_metric[metric] = sweep(fm, cands, direction)
        # The combined signal: any metric past ITS chosen threshold, among every position a metric covers.
        fc = f[f["position"].isin(("WR", "TE", "RB"))].reset_index(drop=True)
        flags = [
            usage_flag(fc, metric, res["chosen"]["threshold"], name)
            for metric, res in per_metric.items()
            if res["chosen"] is not None
        ]
        combined = flags[0].copy()
        for fl in flags[1:]:
            combined = combined | fl
        signals[f"USAGE_{name}"] = {
            "per_metric": per_metric,
            "combined": {
                "thresholds": {m: r["chosen"]["threshold"] for m, r in per_metric.items() if r["chosen"]},
                **sweep(fc, [({"combined": True}, combined)], direction),
            },
        }
    signals["salary_lag"] = {
        "tested": False,
        "reason": "no historical DraftKings salary exists in any public dataset, so a lagging salary cannot "
        "be measured. Test it on Sam's own weekly sheets once enough weeks accumulate.",
    }
    return signals


def write_outputs(signals: dict, n: int, directory=None) -> None:
    meta = metadata(
        "r3",
        SEASONS,
        n,
        min_flagged=MIN_FLAGGED,
        min_effect_points=MIN_EFFECT,
        keep_rule=(
            "expected sign in fit and test, test effect >= min_effect, test 90% interval excludes 0; "
            "borderline = all but the size"
        ),
    )
    write_json("signal_thresholds.json", {"signals": signals}, meta, directory)


def run(log=lambda msg: None) -> dict:
    log("R3: building prior-only usage jumps and TD excess")
    base = load_baseline(log=log)
    sp = data.read_stats_player(
        SEASONS,
        ["player_id", "position", "team", "season", "week", "season_type", "targets", "carries"]
        + ["receiving_tds", "rushing_tds", "passing_tds"],
    )
    ep = data.read_ep(
        SEASONS,
        ["player_id", "season", "week", "rec_touchdown_exp", "rush_touchdown_exp", "pass_touchdown_exp"],
    )
    rz = red_zone_usage(data.read_pbp())
    usage = prior_usage_features(player_game_usage(sp, ep, rz))
    frame = signal_frame(base, usage)
    signals = run_study(frame)
    write_outputs(signals, len(frame))
    return {"signals": signals, "frame": frame}


__all__ = ["run", "run_study", "FIT_SEASONS", "TEST_SEASONS"]
