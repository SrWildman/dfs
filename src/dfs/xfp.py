"""xFP: expected fantasy points from ffopportunity's weekly file, converted to DraftKings scoring, and
the per-player windows and `Edge` context tokens built on it (`BUY↑`, `FADE↓`, `USAGE↑`, `USAGE↓`).

Pure and offline-testable; the thin fetch wrapper is `sources/nflverse_xfp.py`, the cross-source
glue (joining to DraftKings players, injuries, the as-of rules) is `signals.py`.

**What the file is.** ffverse's `ffopportunity` publishes, per player-week, an xgboost model's expected
receptions, yards, touchdowns and so on, next to what actually happened (`ep_weekly_<season>.parquet`,
updated weekly). Summed through DraftKings' scoring this is `xFP`: what the player's OPPORTUNITY was
worth that week, whether or not he cashed it.

**DK conversion** (one place; the cloud model branch uses the same formula, and this block is to be
replaced by an import from `dfs.model` once that branch is merged)::

    receptions_exp + 0.1*rec_yards_gained_exp + 6*rec_touchdown_exp
    + 0.1*rush_yards_gained_exp + 6*rush_touchdown_exp
    + 0.04*pass_yards_gained_exp + 4*pass_touchdown_exp - pass_interception_exp
    + 2*(pass + rec + rush two-point conversions expected)

It is split three ways (`rec_xfp`, `rush_xfp`, `pass_xfp`) so injury redistribution can move receiving
value with targets and rushing value with carries.

**The tokens are context, not proven edges.** The planning session found that public "regression" and
"usage jump" signals do NOT beat TFFB's projection (corr(xFP - actual over L3, actual - TFFB) = -.015;
usage jump -.095): TFFB already prices them in. They are shown, labelled "context, not proven to beat
projections; tracked in Model Check", and `results_analysis`/Model Check keep score on them.

Windows are the player's last `XFP_WINDOW_GAMES` games PLAYED strictly BEFORE the week in question
(`before_week`): a missed game is never a zero, and nothing from the week being predicted can reach
its own signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model.history import XFP_TWO_POINT_POINTS, XFP_WEIGHTS
from dfs.player_join import normalize_position, normalize_team

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
XFP_WINDOW_GAMES = 3

# DK scoring applied to ffopportunity's expected stats: imported from the model package so there is ONE
# formula (`dfs.model.history.XFP_WEIGHTS`, and 2 points per expected two-point conversion). The sum is split
# by the prefix of each expected column (`rec_`/`receptions` = receiving, `rush_`, `pass_`) so redistribution
# can move receiving value with targets and rushing value with carries.
XFP_TWO_POINT = XFP_TWO_POINT_POINTS

# BUY / FADE: his last-3 DK points per game against his last-3 xFP per game. A gap of at least
# `XFP_GAP_POINTS` points OR `XFP_GAP_PCT` of xFP/G counts (Sam, 2026-10-07: "either one"). The player's
# xFP/G must also be in the top half of his position (`XFP_TOP_HALF_PCT`, over the rosterable pool).
XFP_GAP_POINTS = 3.0
XFP_GAP_PCT = 0.25
XFP_TOP_HALF_PCT = 0.5
# FADE needs the mirror gap AND at least this many TDs above ffopportunity's expected TDs over the same
# games (Sam, 2026-10-07: "extra requirement").
FADE_TD_EXCESS = 1.5

# USAGE: the last `USAGE_RECENT_GAMES` games against every earlier game this season. At least
# `USAGE_MIN_EARLIER_GAMES` earlier games are required (Sam, 2026-10-07: "2 recent + 2 earlier"), and `n`
# (the earlier-game count) is shown. A jump is the recent average minus the earlier average.
USAGE_RECENT_GAMES = 2
USAGE_MIN_EARLIER_GAMES = 2
USAGE_JUMP_TGT_SHARE = 0.06
USAGE_JUMP_RUSH_SHARE = 0.12
USAGE_JUMP_RZ_PER_GAME = 1.0

TOKEN_BUY = "BUY↑"
TOKEN_FADE = "FADE↓"
TOKEN_USAGE_UP = "USAGE↑"
TOKEN_USAGE_DOWN = "USAGE↓"
CONTEXT_NOTE = "context, not proven to beat projections; tracked in Model Check"

PLAYER_WEEK_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "season",
    "week",
    "targets",
    "carries",
    "team_targets",
    "team_carries",
    "rec_xfp",
    "rush_xfp",
    "pass_xfp",
    "xfp",
    "td_exp",
    "td",
]
FFO_COLUMNS = [
    "season",
    "posteam",
    "week",
    "player_id",
    "full_name",
    "position",
    "rec_attempt",
    "rush_attempt",
    "rec_attempt_team",
    "rush_attempt_team",
    "receptions_exp",
    "rec_yards_gained_exp",
    "rec_touchdown_exp",
    "rush_yards_gained_exp",
    "rush_touchdown_exp",
    "pass_yards_gained_exp",
    "pass_touchdown_exp",
    "pass_interception_exp",
    "pass_two_point_conv_exp",
    "rec_two_point_conv_exp",
    "rush_two_point_conv_exp",
    "pass_touchdown",
    "rec_touchdown",
    "rush_touchdown",
]


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def _component(ffo: pd.DataFrame, side: str, prefixes: tuple[str, ...]) -> pd.Series:
    """Weighted sum of the expected columns whose name starts with one of `prefixes`, plus the two-point
    conversion term of `side`. A column ffopportunity drops counts as 0, never an error."""
    total = pd.Series(0.0, index=ffo.index)
    for column, weight in XFP_WEIGHTS.items():
        if column.startswith(prefixes):
            total = total + weight * _num(ffo, column)
    return total + XFP_TWO_POINT * _num(ffo, f"{side}_two_point_conv_exp")


def expected_dk_points(ffo: pd.DataFrame) -> pd.DataFrame:
    """`rec_xfp`, `rush_xfp`, `pass_xfp` and their sum `xfp` per ffopportunity row, DK scoring (the
    model package's weights). `tests/test_xfp.py` asserts the sum equals `dfs.model.history`'s own."""
    rec = _component(ffo, "rec", ("receptions", "rec_"))
    rush = _component(ffo, "rush", ("rush_",))
    passing = _component(ffo, "pass", ("pass_",))
    return pd.DataFrame(
        {"rec_xfp": rec, "rush_xfp": rush, "pass_xfp": passing, "xfp": rec + rush + passing}, index=ffo.index
    )


def player_weeks(ffo: pd.DataFrame, *, season: int | None = None) -> pd.DataFrame:
    """One row per skill-position player-week (`PLAYER_WEEK_COLUMNS`) from the raw ffopportunity frame:
    opportunity (targets, carries, team totals), expected points and TDs, actual TDs. `season` filters
    when the frame holds more than one."""
    if ffo is None or ffo.empty:
        return pd.DataFrame(columns=PLAYER_WEEK_COLUMNS)
    df = ffo.copy()
    df["position"] = df["position"].map(normalize_position)
    df = df[df["position"].isin(SKILL_POSITIONS)]
    if season is not None and "season" in df.columns:
        df = df[pd.to_numeric(df["season"], errors="coerce") == season]
    if df.empty:
        return pd.DataFrame(columns=PLAYER_WEEK_COLUMNS)
    parts = expected_dk_points(df)
    out = pd.DataFrame(
        {
            "GsisId": df["player_id"],
            "Name": df["full_name"],
            "Team": df["posteam"].map(normalize_team),
            "Position": df["position"],
            "season": pd.to_numeric(df.get("season", np.nan), errors="coerce"),
            "week": pd.to_numeric(df["week"], errors="coerce"),
            "targets": _num(df, "rec_attempt"),
            "carries": _num(df, "rush_attempt"),
            "team_targets": _num(df, "rec_attempt_team"),
            "team_carries": _num(df, "rush_attempt_team"),
            "rec_xfp": parts["rec_xfp"],
            "rush_xfp": parts["rush_xfp"],
            "pass_xfp": parts["pass_xfp"],
            "xfp": parts["xfp"],
            "td_exp": _num(df, "pass_touchdown_exp")
            + _num(df, "rec_touchdown_exp")
            + _num(df, "rush_touchdown_exp"),
            "td": _num(df, "pass_touchdown") + _num(df, "rec_touchdown") + _num(df, "rush_touchdown"),
        }
    )
    out = out.dropna(subset=["GsisId", "week"]).drop_duplicates(
        subset=["GsisId", "season", "week"], keep="last"
    )
    return out[PLAYER_WEEK_COLUMNS].reset_index(drop=True)


def attach_actual_points(weeks: pd.DataFrame, offense_actual: pd.DataFrame) -> pd.DataFrame:
    """Add `dk_actual` (the real DK points that game) from `results_actual.score_offense_actual`'s output,
    joined on gsis id and week. Rows with no stat line stay NaN."""
    actual = offense_actual[["player_id", "week", "dk_actual"]].rename(columns={"player_id": "GsisId"})
    actual = actual.drop_duplicates(subset=["GsisId", "week"], keep="last")
    return weeks.merge(actual, on=["GsisId", "week"], how="left")


def recent_games(weeks: pd.DataFrame, *, before_week: int, window: int = XFP_WINDOW_GAMES) -> pd.DataFrame:
    """Each player's last `window` games played strictly before `before_week` (one row per game)."""
    past = weeks[pd.to_numeric(weeks["week"], errors="coerce") < before_week]
    past = past.sort_values(["GsisId", "week"], ascending=[True, False])
    return past.groupby("GsisId", sort=False).head(window)


def xfp_windows(weeks: pd.DataFrame, *, before_week: int, window: int = XFP_WINDOW_GAMES) -> pd.DataFrame:
    """Per player (indexed by `GsisId`): `Games`, `xFP/G`, `DkG` (actual DK points per game), `TdExcess`
    (actual minus expected TDs over those games), plus the volume per game that redistribution needs
    (`tgt_g`, `car_g`, `rec_xfp_g`, `rush_xfp_g`). Only games before `before_week`."""
    recent = recent_games(weeks, before_week=before_week, window=window)
    if recent.empty:
        return pd.DataFrame(
            columns=["Games", "xFP/G", "DkG", "TdExcess", "tgt_g", "car_g", "rec_xfp_g", "rush_xfp_g"]
        )
    if "dk_actual" not in recent.columns:
        recent = recent.assign(dk_actual=np.nan)
    grouped = recent.groupby("GsisId")
    out = pd.DataFrame(
        {
            "Games": grouped["week"].count(),
            "xFP/G": grouped["xfp"].mean(),
            "DkG": grouped["dk_actual"].mean(),
            "TdExcess": grouped["td"].sum() - grouped["td_exp"].sum(),
            "tgt_g": grouped["targets"].mean(),
            "car_g": grouped["carries"].mean(),
            "rec_xfp_g": grouped["rec_xfp"].mean(),
            "rush_xfp_g": grouped["rush_xfp"].mean(),
        }
    )
    return out


def position_percentile(
    values: pd.Series, position: pd.Series, pool_mask: pd.Series | None = None
) -> pd.Series:
    """Each value's percentile (0-1) within its position, over `pool_mask` rows when given (blanks never
    ranked). A row outside the pool is ranked against the pool's values."""
    out = pd.Series(np.nan, index=values.index)
    for pos in position.dropna().unique():
        in_pos = position == pos
        reference = values[in_pos & (pool_mask if pool_mask is not None else True)].dropna()
        if reference.empty:
            continue
        sorted_ref = np.sort(reference.to_numpy())
        ranks = np.searchsorted(sorted_ref, values[in_pos].dropna().to_numpy(), side="right")
        out.loc[values[in_pos].dropna().index] = ranks / len(sorted_ref)
    return out


def buy_fade_tokens(windows: pd.DataFrame, top_half: pd.Series) -> pd.Series:
    """`BUY↑` / `FADE↓` / "" per player, indexed like `windows`. `top_half` is True where xFP/G is in the
    top half of the position.

    BUY: actual DK/G at least `XFP_GAP_POINTS` points OR `XFP_GAP_PCT` BELOW xFP/G.
    FADE: the mirror (ABOVE), AND TDs at least `FADE_TD_EXCESS` above expected over the same games.
    Both need a real window (`Games >= 1`) and xFP/G in the top half of the position."""
    xfp = windows["xFP/G"]
    actual = windows["DkG"]
    gap = xfp - actual
    positive = xfp.where(xfp > 0)
    below = ((gap >= XFP_GAP_POINTS) | (gap >= XFP_GAP_PCT * positive)) & actual.notna()
    above = ((-gap >= XFP_GAP_POINTS) | (-gap >= XFP_GAP_PCT * positive)) & actual.notna()
    eligible = top_half.reindex(windows.index).fillna(False).astype(bool) & (windows["Games"] >= 1)
    fade = above & (windows["TdExcess"] >= FADE_TD_EXCESS)
    out = pd.Series("", index=windows.index, dtype=object)
    out[below & eligible] = TOKEN_BUY
    out[fade & eligible] = TOKEN_FADE
    return out


def usage_jumps(
    weeks: pd.DataFrame,
    rz_by_week: pd.DataFrame | None,
    *,
    before_week: int,
    recent: int = USAGE_RECENT_GAMES,
    min_earlier: int = USAGE_MIN_EARLIER_GAMES,
) -> pd.DataFrame:
    """Per player (indexed by `GsisId`): the last-`recent`-games average of target share, carry share and
    red-zone opportunities per game, against his earlier games THIS season (`before_week`), with `n` the
    earlier-game count. A metric a player has no volume for in either window is NaN.

    `weeks` is `player_weeks` for one season (shares come from the team totals riding on each row);
    `rz_by_week` is `[GsisId, week, rz]` (red-zone targets plus carries) or None. Returns the rows with at
    least `min_earlier` earlier games and `recent` recent games; `token` is `USAGE↑`, `USAGE↓` or ""."""
    past = weeks[pd.to_numeric(weeks["week"], errors="coerce") < before_week].copy()
    if past.empty:
        return pd.DataFrame(columns=["n", "token", "d_tgt", "d_rush", "d_rz"]).rename_axis("GsisId")
    past["tgt_share"] = past["targets"] / past["team_targets"].where(past["team_targets"] > 0)
    past["rush_share"] = past["carries"] / past["team_carries"].where(past["team_carries"] > 0)
    if rz_by_week is not None and not rz_by_week.empty:
        past = past.merge(rz_by_week[["GsisId", "week", "rz"]], on=["GsisId", "week"], how="left")
        past["rz"] = past["rz"].fillna(0.0)
    else:
        past["rz"] = np.nan
    rows = []
    for gsis, games in past.sort_values("week").groupby("GsisId"):
        if len(games) < recent + min_earlier:
            continue
        last, earlier = games.iloc[-recent:], games.iloc[:-recent]
        rows.append(
            {
                "GsisId": gsis,
                "n": len(earlier),
                "d_tgt": last["tgt_share"].mean() - earlier["tgt_share"].mean(),
                "d_rush": last["rush_share"].mean() - earlier["rush_share"].mean(),
                "d_rz": last["rz"].mean() - earlier["rz"].mean(),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["n", "token", "d_tgt", "d_rush", "d_rz"]).rename_axis("GsisId")
    out = pd.DataFrame(rows).set_index("GsisId")
    up = (
        (out["d_tgt"] >= USAGE_JUMP_TGT_SHARE)
        | (out["d_rush"] >= USAGE_JUMP_RUSH_SHARE)
        | (out["d_rz"] >= USAGE_JUMP_RZ_PER_GAME)
    )
    down = (
        (out["d_tgt"] <= -USAGE_JUMP_TGT_SHARE)
        | (out["d_rush"] <= -USAGE_JUMP_RUSH_SHARE)
        | (out["d_rz"] <= -USAGE_JUMP_RZ_PER_GAME)
    )
    token = pd.Series("", index=out.index, dtype=object)
    token[up & ~down] = TOKEN_USAGE_UP
    token[down & ~up] = TOKEN_USAGE_DOWN
    # A player whose metrics jumped in opposite directions (targets up, carries down) gets no token: a
    # mixed signal is not a signal.
    out["token"] = token
    return out
