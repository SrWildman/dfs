"""The shipped team-shape table: QB1 with TE1, by the tight end's share of his team's targets.

`script_shape.py` tested 22 modifiers on the pair correlations (docs/SIM.md, "Game script, team shape and the
bring-back"). One is shipped (Sam, 2026-10-18): a tight end who gets a big share of his team's targets moves
with his QB more than a blocking tight end does (raw +0.19 in the bottom third of target share, +0.33 in the
top). The five other QB1-with-receiver pairs tested the same way (RB1, RB2, WR1, WR2, WR3) stay at the pooled
value and are to be re-tested later, not dropped: `SHIPPED_TESTS` is the one list that adds them.

The table is a layer on `correlations.csv` (`script_shape.ShapeLookup`): the same normal-score correlation of
the pair within each third of the feature, shrunk toward the POOLED value with the pooled table's prior, so a
third the data barely supports stays near the pooled number. `correlations_shape.csv` holds the three raw
third-correlations with their counts and the cut points; `dfs sim fit` writes it next to `correlations.csv`.

The feature is the live one, not the research one: `usage_metrics`' `Tgt%` -- the player's targets over his
team's in his last three games played this season (a ratio of window sums), which is what EdgeRaw carries --
so the table is fitted on the quantity `sim_inputs.build_specs` hands it. A tight end with no `Tgt%` (week 1,
a rookie) gets the pooled value.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.sim import correlation
from dfs.sim.correlation import SIM_DIR, CorrelationLookup
from dfs.sim.script_shape import TERCILES, ShapeLookup, cutoffs_for, tercile, test_rows

SHAPE_FILE = "correlations_shape.csv"
SHIPPED_TESTS = (("same_team", "QB1", "TE1", "b", "tshare"),)
SHAPE_COLUMNS = [
    "relation",
    "role_a",
    "role_b",
    "side",
    "feature",
    "bucket",
    "n",
    "r",
    "cut_lo",
    "cut_hi",
]
WINDOW_GAMES = 3  # `usage_metrics.USAGE_WINDOW_GAMES`


class ShapeTableError(Exception):
    """The shape table is malformed."""


def live_tshare(history) -> pd.DataFrame:
    """`gsis_id, season, week, tshare`: each player's targets over his team's in his previous (up to) three
    games this season, a ratio of window sums -- `usage_metrics`' `Tgt%` as of the game's week. Nothing from
    the game itself; no look at the previous season (the live column has none either), so week 1 is blank."""
    pg = history.player_games.sort_values(["gsis_id", "t"]).copy()
    pg["team_targets"] = pg.groupby(["game_id", "team"])["targets"].transform("sum")
    grouped = pg.groupby(["gsis_id", "season"])
    mine = grouped["targets"].transform(lambda s: s.shift(1).rolling(WINDOW_GAMES, min_periods=1).sum())
    theirs = grouped["team_targets"].transform(
        lambda s: s.shift(1).rolling(WINDOW_GAMES, min_periods=1).sum()
    )
    pg["tshare"] = mine / theirs.where(theirs > 0)
    return pg[["gsis_id", "season", "week", "tshare"]]


def with_live_tshare(frame: pd.DataFrame, history) -> pd.DataFrame:
    """`frame` (`roles.build_frame`'s output, with `z`) plus the live `tshare` of every row."""
    base = frame.drop(columns=["tshare"], errors="ignore")
    return base.merge(live_tshare(history), on=["gsis_id", "season", "week"], how="left")


def fit_shape(frame: pd.DataFrame) -> pd.DataFrame:
    """The shipped table's rows, fitted on `frame` (`z` and the live `tshare` present; the caller restricts
    the seasons): one row per tercile of each of `SHIPPED_TESTS`."""
    rows = []
    for test in SHIPPED_TESTS:
        data = test_rows(frame, test)
        lo, hi = cutoffs_for(frame, test)
        labels = tercile(data["x"], (lo, hi))
        for name in TERCILES:
            sub = data[labels == name]
            r = float(np.corrcoef(sub["z_a"], sub["z_b"])[0, 1]) if len(sub) > 30 else float("nan")
            rows.append((*test, name, len(sub), r, lo, hi))
    return pd.DataFrame(rows, columns=SHAPE_COLUMNS)


def save_shape(table: pd.DataFrame, directory: Path = SIM_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SHAPE_FILE
    table[SHAPE_COLUMNS].to_csv(path, index=False, float_format="%.5f")
    default_lookup.cache_clear()
    return path


def shape_lookup(base: CorrelationLookup, table: pd.DataFrame) -> ShapeLookup:
    """`table` (the file's rows) as a layer over the pooled lookup `base`."""
    missing = [c for c in SHAPE_COLUMNS if c not in table.columns]
    if missing:
        raise ShapeTableError(f"shape table is missing columns {missing}")
    tests = {
        tuple(t) for t in table[["relation", "role_a", "role_b", "side", "feature"]].itertuples(index=False)
    }
    unknown = tests - set(SHIPPED_TESTS)
    if unknown:
        raise ShapeTableError(f"shape table has tests that are not shipped: {sorted(unknown)}")
    cuts = {
        tuple(g.iloc[0][["relation", "role_a", "role_b", "side", "feature"]]): (
            float(g.iloc[0]["cut_lo"]),
            float(g.iloc[0]["cut_hi"]),
        )
        for _, g in table.groupby(["relation", "role_a", "role_b", "side", "feature"])
    }
    buckets = table[["relation", "role_a", "role_b", "side", "feature", "bucket", "n", "r"]]
    return ShapeLookup(base, buckets, cuts)


@lru_cache(maxsize=4)
def default_lookup(directory: Path = SIM_DIR):
    """What the simulator reads when it is not handed a table: the pooled `correlations.csv`, with the shipped
    shape layer on top when `correlations_shape.csv` exists. Without the file (or for a player whose `tshare`
    is unknown) every pair is its pooled value."""
    base = correlation.load_correlations(directory)
    path = directory / SHAPE_FILE
    if not path.exists():
        return base
    return shape_lookup(base, pd.read_csv(path))


__all__ = [
    "SHAPE_FILE",
    "SHIPPED_TESTS",
    "default_lookup",
    "fit_shape",
    "live_tshare",
    "save_shape",
    "shape_lookup",
    "with_live_tshare",
]
