"""R6 trend bands: how far does a player's last-3 usage normally move from week to week?

The sheet shows a ▲/▼ next to a usage metric when the player's last-3-games value has moved beyond normal
noise from the 6 games before those. That is context, not a claim about points (`usage_signals.json` says
which moves actually beat the projection). This module measures the noise: for each metric the sheet shows
and each position, the distribution of `L3 - prior` over every player-game of 2018-2025, so the arrow can be
set to fire on about `TARGET_FLAG_RATE` of player-weeks.

Two populations are reported for every band:

  all   every skill-position player-game with full windows (a change needs `MIN_PRIOR_GAMES` earlier games);
  pool  the subset UM projects (>= 1 prior game and last-3 xFP >= 4) -- the players the sheet actually lists.

They differ because a fifth receiver's target share never moves; the `pool` threshold is the one that puts
arrows on 15% of the rows a person looks at."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research.r6_features import GAME_KEY, MIN_PRIOR_GAMES, PRIOR, RECENT

BAND_SEASONS = tuple(range(2018, 2026))
TARGET_FLAG_RATE = 0.15

# key -> (what the sheet calls it, feature metric, positions the sheet shows it for, unit)
TREND_METRICS: dict[str, tuple[str, str, tuple[str, ...], str]] = {
    "tgt_share": ("Tgt%", "tgt_share", ("RB", "WR", "TE"), "share"),
    "wopr": ("WOPR", "wopr", ("RB", "WR", "TE"), "index"),
    "ay_share": ("Air-yards share", "ay_share", ("RB", "WR", "TE"), "share"),
    "carry_share": ("Rush%", "carry_share", ("QB", "RB"), "share"),
    "rec_pg": ("Rec/G", "rec_pg", ("RB", "WR", "TE"), "per game"),
    "rb_tgt_share": ("RB Tgt%", "tgt_share", ("RB",), "share"),
    "rz_pg": ("RZ/G", "rz_pg", ("RB", "WR", "TE"), "per game"),
    "hvt_pg": ("HVT/G", "hvt_pg", ("RB", "WR", "TE"), "per game"),
    "snap_pct": ("Snap%", "snap_pct", ("RB", "WR", "TE"), "share"),
    "ez_tgt_pg": ("End-zone targets/G", "ez_tgt_pg", ("RB", "WR", "TE"), "per game"),
    "gl_share": ("Goal-line carry share", "gl_share", ("RB",), "share"),
}


def _round_sig(x: float, digits: int = 3) -> float:
    return float(f"{x:.{digits}g}") if x == x else float("nan")


def _floor_sig(x: float, digits: int = 3) -> float:
    """`x` rounded DOWN (toward zero) to `digits` significant digits. A count metric moves in sixths, so a
    threshold of exactly 2/3 must be written 0.666, not 0.667 (which would exclude the 2/3 it was read
    from)."""
    if x <= 0:
        return 0.0
    scale = 10 ** (digits - 1 - int(np.floor(np.log10(x))))
    return float(np.floor(x * scale + 1e-9) / scale)


def arrow_threshold(change: np.ndarray, target: float = TARGET_FLAG_RATE) -> dict:
    """The `|change| >= threshold` that flags closest to `target` of the rows (ties go to the higher
    threshold, fewer arrows), and the share it actually flags. Discrete metrics cannot hit 15% exactly:
    the threshold is an observed value, floored to 3 significant digits so the written rule flags
    exactly what was measured."""
    a = np.abs(change[~np.isnan(change)])
    if len(a) == 0:
        return {"threshold": None, "flag_rate": None}
    # Sixths computed two ways differ in the 16th digit (1/6 vs 1/6 + 1 ulp): merge them, or a sliver of
    # float noise looks like its own step and wins the search.
    values, counts = np.unique(np.round(a, 9), return_counts=True)
    rate_at_least = counts[::-1].cumsum()[::-1] / len(a)
    nonzero = values > 0
    values, rate_at_least = values[nonzero], rate_at_least[nonzero]
    if len(values) == 0:
        return {"threshold": None, "flag_rate": None}
    best = np.flatnonzero(np.isclose(np.abs(rate_at_least - target), np.abs(rate_at_least - target).min()))[
        -1
    ]
    thr = _floor_sig(float(values[best]))
    return {"threshold": thr, "flag_rate": _round_sig(float((a >= thr).mean()), 3)}


def describe(change: np.ndarray) -> dict:
    """n, SD of the change, the 80th / 90th percentiles of its absolute value, and the arrow threshold."""
    change = change[~np.isnan(change)]
    if len(change) == 0:
        return {
            "n": 0,
            "sd": None,
            "abs_p80": None,
            "abs_p90": None,
            "arrow_threshold": None,
            "flag_rate": None,
        }
    a = np.abs(change)
    arrow = arrow_threshold(change)
    return {
        "n": int(len(change)),
        "sd": _round_sig(float(change.std(ddof=1))),
        "abs_p80": _round_sig(float(np.quantile(a, 0.80))),
        "abs_p90": _round_sig(float(np.quantile(a, 0.90))),
        "arrow_threshold": arrow["threshold"],
        "flag_rate": arrow["flag_rate"],
    }


def flag_rate(change: np.ndarray, threshold: float) -> float:
    change = change[~np.isnan(change)]
    return _round_sig(float((np.abs(change) >= threshold).mean()), 3) if len(change) else float("nan")


def _population(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[frame["season"].isin(BAND_SEASONS)]
    return out


def trend_bands(games: pd.DataFrame, feats: pd.DataFrame, pool: pd.DataFrame) -> dict:
    """The band table. `games` carries each player-game's position, `feats` the window features, `pool` the
    UM-population rows (any frame with the game key)."""
    g = games[[*GAME_KEY, "position"]].merge(feats.drop(columns=["team"]), on=GAME_KEY, how="left")
    g = _population(g)
    in_pool = g.set_index(GAME_KEY).index.isin(pool.set_index(GAME_KEY).index)
    g = g.assign(in_pool=in_pool)
    metrics = {}
    for key, (label, feature, positions, unit) in TREND_METRICS.items():
        col = f"{feature}_chg"
        rows = g[g["position"].isin(positions) & g[col].notna()]
        by_pos = {
            pos: {
                "all": describe(rows.loc[rows["position"] == pos, col].to_numpy()),
                "pool": describe(rows.loc[(rows["position"] == pos) & rows["in_pool"], col].to_numpy()),
            }
            for pos in positions
        }
        pooled = {
            "all": describe(rows[col].to_numpy()),
            "pool": describe(rows.loc[rows["in_pool"], col].to_numpy()),
        }
        pool_rows = rows[rows["in_pool"]]
        pooled_thr = pooled["pool"]["arrow_threshold"]
        metrics[key] = {
            "label": label,
            "feature": col,
            "unit": unit,
            "positions": by_pos,
            "pooled": pooled,
            "recommended": {
                # per position: the scale of a share or a count differs between a WR and an RB, so one number
                # for the metric flags very different shares of each position (see flag_rate_at_pooled)
                "per_position": {
                    pos: {
                        "threshold": by_pos[pos]["pool"]["arrow_threshold"],
                        "flag_rate": by_pos[pos]["pool"]["flag_rate"],
                    }
                    for pos in positions
                },
                "pooled": {"threshold": pooled_thr, "flag_rate": pooled["pool"]["flag_rate"]},
                "flag_rate_at_pooled": {
                    pos: flag_rate(pool_rows.loc[pool_rows["position"] == pos, col].to_numpy(), pooled_thr)
                    for pos in positions
                },
            },
        }
    return {
        "window": {"recent_games": RECENT, "prior_games": PRIOR, "min_prior_games": MIN_PRIOR_GAMES},
        "seasons": [BAND_SEASONS[0], BAND_SEASONS[-1]],
        "target_flag_rate": TARGET_FLAG_RATE,
        "populations": {
            "all": "every skill-position player-game with full windows",
            "pool": "the UM-projected subset (>= 1 prior game, last-3 xFP >= 4): who the sheet lists",
        },
        "arrow_rule": "show ▲ when L3 - prior >= +threshold and ▼ when L3 - prior <= -threshold; no arrow "
        "when the player has fewer than min_prior_games earlier games. Use recommended.per_position",
        "n_player_games": int(g[[f"{m[1]}_chg" for m in TREND_METRICS.values()]].notna().any(axis=1).sum()),
        "metrics": metrics,
    }
