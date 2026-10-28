"""A Sleeper stat line in one league's scoring.

Sleeper's stat keys and its `scoring_settings` keys are the same vocabulary, so a league's points are the dot
product of the two. Two gaps need a fallback, both on kickers: a projection gives `fgm_50p` where a league
scores `fgm_50_59` / `fgm_60p`, and `fgmiss_<range>` where it scores `fgmiss`. Everything a league does not
score is ignored; every stat a line lacks is 0.

`tests/season/test_scoring.py` checks this reproduces Sleeper's own `pts_ppr` / `pts_half_ppr` / `pts_std` on
real lines when handed those formats' settings.
"""

from __future__ import annotations

import pandas as pd


def _fallbacks(stats: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    zero = pd.Series(0.0, index=stats.index)
    col = lambda k: stats[k] if k in stats.columns else pd.Series(float("nan"), index=stats.index)  # noqa: E731
    fg50p = col("fgm_50p").fillna(0.0)
    fg60 = col("fgm_60p")
    out["fgm_50_59"] = col("fgm_50_59").fillna((fg50p - fg60.fillna(0.0)).clip(lower=0.0))
    out["fgm_60p"] = fg60.fillna(0.0)
    misses = [c for c in stats.columns if c.startswith("fgmiss_")]
    out["fgmiss"] = col("fgmiss").fillna(stats[misses].fillna(0.0).sum(axis=1) if misses else zero)
    return out


def score_stats(stats: pd.DataFrame, scoring: dict[str, float]) -> pd.Series:
    """Points per row of a frame whose columns are Sleeper stat keys (missing = 0)."""
    fallbacks = _fallbacks(stats)
    total = pd.Series(0.0, index=stats.index)
    for key, weight in scoring.items():
        if not weight:
            continue
        if key in fallbacks:
            values = fallbacks[key]
        elif key in stats.columns:
            values = stats[key]
        else:
            continue
        total = total + pd.to_numeric(values, errors="coerce").fillna(0.0) * float(weight)
    return total.round(2)


def stats_frame(records: list[dict]) -> pd.DataFrame:
    """Sleeper week records to one row per player: `player_id`, `team`, `opponent`, `played` and the stats."""
    rows = []
    for r in records:
        stats = dict(r.get("stats") or {})
        rows.append(
            {
                "player_id": str(r.get("player_id")),
                "team": r.get("team") or "",
                "opponent": r.get("opponent") or "",
                **stats,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["player_id", "team", "opponent", "gp"])
    if "gp" not in frame.columns:
        frame["gp"] = float("nan")
    return frame.drop_duplicates("player_id", keep="first").reset_index(drop=True)
