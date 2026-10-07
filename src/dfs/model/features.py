"""Part 2: features, built by ONE code path for training and inference.

`build_features(history, rows)` returns the model's features for each row of `rows` (a player or a defense
in a given season and week, with that game's Vegas context) using ONLY games strictly before that week.
Training calls it for every historical row; inference calls it for this week's slate. There is no second
implementation to drift.

How "strictly before" is enforced -- by construction, not by a filter someone could forget: each history
table is first rolled up per player (or team) into running means that INCLUDE each game; a row's features
are then the running means of the latest game with `t < row.t` (`merge_asof`, `allow_exact_matches=False`),
where `t = season * 100 + week`. A row can never see its own game or anything after it, and adding later
rows to the history cannot change an earlier row's features (tests/model/test_features.py pins both).

Rolling windows are over GAMES PLAYED and continue across the season boundary: last season's games count
early in the year. A player with no prior games keeps NaN (HistGradientBoosting handles them) and a games
count of 0.
"""

from __future__ import annotations

import pandas as pd

from dfs.model.history import CONTEXT_COLUMNS, History

WINDOWS = (1, 3, 8)
QB_WINDOWS = (3, 8)
DEF_VS_POS_WINDOW = 8

# Rolled for every offensive position.
OFFENSE_ROLL = ("dk", "xfp", "targets", "carries", "target_share", "air_yards_share", "receiving_air_yards")
# Rolled additionally for QBs only.
QB_ROLL = ("attempts", "passing_epa")

# DST: (history column on the team's own row, feature name) for the defense, and for the opposing offense.
DST_OWN_ROLL = {"sacks": "def_sacks", "takeaways": "def_takeaways", "dk": "def_dk"}
DST_OPP_ROLL = {
    "sacks_allowed": "opp_sacks_allowed",
    "giveaways": "opp_giveaways",
    "dst_conceded": "opp_dst_conceded",
}
DST_WINDOW = 8

ROW_COLUMNS = ["gsis_id", "position", "team", "opp", "season", "week", *CONTEXT_COLUMNS]


def feature_columns(position: str) -> list[str]:
    """The model's input columns for a position, in a fixed order (the order the fitted model expects)."""
    if position == "DST":
        return [
            "opp_implied",
            "home",
            "weeks_into_season",
            "games_l8",
            *DST_OWN_ROLL.values(),
            *DST_OPP_ROLL.values(),
        ]
    cols = [f"{col}_l{n}" for col in OFFENSE_ROLL for n in WINDOWS]
    if position == "QB":
        cols += [f"{col}_l{n}" for col in QB_ROLL for n in QB_WINDOWS]
    return [
        *cols,
        "games_l8",
        "weeks_into_season",
        "implied",
        "spread",
        "total",
        "home",
        "def_vs_pos_l8",
    ]


def _rolled(table: pd.DataFrame, key: list[str], specs: dict[str, tuple[int, ...]]) -> pd.DataFrame:
    """Running means per `key` that include each row's own game: `{col}_l{n}` = mean of the last n games
    up to and including this one (fewer if the history is shorter), plus `games_l8`, the games actually in
    the 8-game window."""
    t = table.sort_values([*key, "t"]).reset_index(drop=True)
    grouped = t.groupby(key, sort=False)
    out = t[[*key, "t"]].copy()
    for col, windows in specs.items():
        for n in windows:
            out[f"{col}_l{n}"] = (
                grouped[col]
                .rolling(n, min_periods=1)
                .mean()
                .reset_index(level=list(range(len(key))), drop=True)
            )
    first = next(iter(specs))
    out["games_l8"] = (
        grouped[first].rolling(8, min_periods=1).count().reset_index(level=list(range(len(key))), drop=True)
    )
    return out


def _latest_before(
    rows: pd.DataFrame, rolled: pd.DataFrame, *, by: list[str], rolled_by: list[str] | None = None
) -> pd.DataFrame:
    """For each row, the rolled values of the latest game with `t` strictly before the row's own, matched
    on `by` (`rolled_by` names the same keys in `rolled` when they are spelled differently, e.g. a row's
    `opp` against a defense table's `team`). Row order is preserved; rows with no earlier game get NaN."""
    rolled_by = rolled_by or by
    left = rows[[*by, "t"]].copy()
    left["_row"] = range(len(left))
    right = rolled.rename(columns=dict(zip(rolled_by, by, strict=True)))
    merged = pd.merge_asof(
        left.sort_values("t"),
        right.sort_values("t"),
        on="t",
        by=by,
        direction="backward",
        allow_exact_matches=False,
    )
    return merged.sort_values("_row").drop(columns=["_row", *by, "t"]).reset_index(drop=True)


def _with_time(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["t"] = out["season"].astype(int) * 100 + out["week"].astype(int)
    return out.reset_index(drop=True)


def _offense_features(history: History, rows: pd.DataFrame, position: str) -> pd.DataFrame:
    specs: dict[str, tuple[int, ...]] = {col: WINDOWS for col in OFFENSE_ROLL}
    if position == "QB":
        specs.update({col: QB_WINDOWS for col in QB_ROLL})
    rolled = _rolled(history.player_games, ["gsis_id"], specs)
    own = _latest_before(rows, rolled, by=["gsis_id"])

    def_rolled = _rolled(history.def_vs_pos, ["team", "position"], {"rel_allowed": (DEF_VS_POS_WINDOW,)})
    opp_def = _latest_before(rows, def_rolled, by=["opp", "position"], rolled_by=["team", "position"])
    out = rows.reset_index(drop=True).copy()
    for col in own.columns:
        out[col] = own[col]
    out["games_l8"] = out["games_l8"].fillna(0.0)
    out["def_vs_pos_l8"] = opp_def[f"rel_allowed_l{DEF_VS_POS_WINDOW}"]
    out["weeks_into_season"] = out["week"].astype(int) - 1
    return out


def _dst_features(history: History, rows: pd.DataFrame) -> pd.DataFrame:
    team = history.team_games
    own_rolled = _rolled(team, ["team"], {col: (DST_WINDOW,) for col in DST_OWN_ROLL})
    own = _latest_before(rows, own_rolled, by=["team"])
    # The opposing OFFENSE's own recent games: same table, looked up by the row's `opp`.
    opp_rolled = _rolled(team, ["team"], {col: (DST_WINDOW,) for col in DST_OPP_ROLL})
    opp = _latest_before(rows, opp_rolled, by=["opp"], rolled_by=["team"])
    out = rows.reset_index(drop=True).copy()
    for col, name in DST_OWN_ROLL.items():
        out[name] = own[f"{col}_l{DST_WINDOW}"]
    for col, name in DST_OPP_ROLL.items():
        out[name] = opp[f"{col}_l{DST_WINDOW}"]
    out["games_l8"] = own["games_l8"].fillna(0.0)
    # The two teams' implied totals sum to the game total, so the opponent's is what is left.
    out["opp_implied"] = out["total"] - out["implied"]
    out["weeks_into_season"] = out["week"].astype(int) - 1
    return out


def build_features(history: History, rows: pd.DataFrame) -> pd.DataFrame:
    """Features for every row of `rows` (`ROW_COLUMNS`; DST rows use the team code as `gsis_id`), from
    `history` games strictly before each row's week. Returns `rows` (original order, original index
    dropped) with the `t` key and every feature column for its position added. Positions are built
    separately and stitched back in input order."""
    missing = [c for c in ROW_COLUMNS if c not in rows.columns]
    if missing:
        raise ValueError(f"rows is missing columns: {missing}")
    work = _with_time(rows[ROW_COLUMNS])
    parts = []
    for position, chunk in work.groupby("position", sort=False):
        if position == "DST":
            built = _dst_features(history, chunk.reset_index(drop=True))
        elif position in ("QB", "RB", "WR", "TE"):
            built = _offense_features(history, chunk.reset_index(drop=True), position)
        else:
            raise ValueError(f"unknown position {position!r}")
        built.index = chunk.index
        parts.append(built)
    return pd.concat(parts).sort_index().reset_index(drop=True)
