"""R5: four quick checks, one table each: does the residual `actual - UM` differ for

  short_week          a team playing on 5 or fewer days of rest (a Thursday game after a Sunday is 4)
  divisional          a game against a division opponent
  home                the home team (neutral-site games are left out)
  back_to_back_road   the second road game in a row (neutral-site games break the chain)

with the signal's expected sign fixed in advance (short week -, divisional -, home +, back-to-back road -).
Same machinery and verdicts as R3: the effect is flagged minus unflagged mean residual, demeaned within
position and season, with a game-clustered 90% interval, in the fit seasons and the test seasons, pooled
over positions and then position by position."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.baseline import load_baseline
from dfs.research.common import SEASONS, metadata, write_json
from dfs.research.flags import add_split_and_demeaned, evaluate, verdict

SHORT_WEEK_REST_DAYS = 5
CHECKS = {
    # name: (expected direction, one-line definition)
    "short_week": (-1, f"own rest of {SHORT_WEEK_REST_DAYS} days or fewer"),
    "divisional": (-1, "division opponent"),
    "home": (+1, "home team (neutral-site games excluded)"),
    "back_to_back_road": (-1, "second consecutive road game (neutral-site games break the chain)"),
}


def team_game_context(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team): `home` (1 / 0, NaN at a neutral site), `rest` days, `divisional`, and
    `back_to_back_road` (this game and the team's previous game of the season were both true road games)."""
    g = games.copy()
    neutral = g["location"].eq("Neutral")
    home = pd.DataFrame(
        {
            "game_id": g["game_id"],
            "season": g["season"],
            "week": g["week"],
            "team": g["home_team"],
            "home": np.where(neutral, np.nan, 1.0),
            "rest": g["home_rest"],
            "divisional": g["div_game"],
            "neutral": neutral,
        }
    )
    away = home.assign(team=g["away_team"], home=np.where(neutral, np.nan, 0.0), rest=g["away_rest"])
    ctx = pd.concat([home, away], ignore_index=True).sort_values(["team", "season", "week"])
    road = ctx["home"] == 0.0  # a true road game: away and not neutral
    prev_road = road.groupby([ctx["team"], ctx["season"]]).shift(1, fill_value=False)
    ctx["back_to_back_road"] = (road & prev_road.astype(bool)).astype(float)
    ctx["short_week"] = (ctx["rest"] <= SHORT_WEEK_REST_DAYS).astype(float)
    return ctx.drop(columns=["season", "week"]).reset_index(drop=True)


def flags_frame(base: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """UM-population rows (every position) with the four flags attached and the residual demeaned."""
    f = add_split_and_demeaned(base[base["um"].notna()])
    ctx = team_game_context(games)
    return f.merge(ctx, on=["game_id", "team"], how="left", suffixes=("", "_ctx")).reset_index(drop=True)


def check_flag(f: pd.DataFrame, name: str) -> tuple[pd.DataFrame, pd.Series]:
    """The rows a check applies to and its boolean flag. `home` and `back_to_back_road` leave out neutral
    sites (their home value is NaN); the others apply to every row with the needed field."""
    if name == "short_week":
        rows = f[f["rest"].notna()]
        return rows, rows["short_week"] == 1.0
    if name == "divisional":
        rows = f[f["divisional"].notna()]
        return rows, rows["divisional"] == 1
    if name == "home":
        rows = f[f["home_ctx"].notna()]
        return rows, rows["home_ctx"] == 1.0
    rows = f[f["home_ctx"].notna() | f["back_to_back_road"].notna()]
    rows = rows[rows["home_ctx"].notna()]
    return rows, rows["back_to_back_road"] == 1.0


def run_study(base: pd.DataFrame, games: pd.DataFrame) -> dict:
    f = flags_frame(base, games)
    out: dict = {"skipped": [], "checks": {}}
    for name, (direction, definition) in CHECKS.items():
        rows, flag = check_flag(f, name)
        res = evaluate(rows.reset_index(drop=True), flag.reset_index(drop=True), direction, cluster="game_id")
        out["checks"][name] = {
            "definition": definition,
            "expected_direction": "up" if direction > 0 else "down",
            "pooled": {k: res[k] for k in ("fit", "test", "n_per_season")},
            "by_position": res["by_position"],
            "verdict_by_position": {
                pos: verdict(parts, direction)["recommendation"] for pos, parts in res["by_position"].items()
            },
            **verdict(res, direction),
        }
    return out


def write_outputs(result: dict, n: int, directory=None) -> None:
    meta = metadata("r5", SEASONS, n, short_week_rest_days=SHORT_WEEK_REST_DAYS)
    write_json("quick_checks.json", result, meta, directory)


def run(log=lambda msg: None) -> dict:
    log("R5: short week, divisional, home / away, back-to-back road")
    base = load_baseline(log=log)
    result = run_study(base, data.read_games())
    write_outputs(result, int(base["um"].notna().sum()))
    return result
