"""xFP: expected fantasy points from ffopportunity's weekly file, converted to DraftKings scoring, and
the per-player windows and `Edge` context tokens built on it (`FADE↓` for TEs, `USAGE↑` / `USAGE↓` for RB
carry share).

Pure and offline-testable; the thin fetch wrapper is `sources/nflverse_xfp.py`, the cross-source
glue (joining to DraftKings players, injuries, the as-of rules) is `signals.py`.

**What the file is.** ffverse's `ffopportunity` publishes, per player-week, an xgboost model's expected
receptions, yards, touchdowns and so on, next to what actually happened (`ep_weekly_<season>.parquet`,
updated weekly). Summed through DraftKings' scoring this is `xFP`: what the player's OPPORTUNITY was
worth that week, whether or not he cashed it.

**DK conversion** (`dfs.model.history.XFP_WEIGHTS`, imported, not copied)::

    receptions_exp + 0.1*rec_yards_gained_exp + 6*rec_touchdown_exp
    + 0.1*rush_yards_gained_exp + 6*rush_touchdown_exp
    + 0.04*pass_yards_gained_exp + 4*pass_touchdown_exp - pass_interception_exp
    + 2*(pass + rec + rush two-point conversions expected)

It is split three ways (`rec_xfp`, `rush_xfp`, `pass_xfp`) so injury redistribution can move rushing value
with
carries.

**The tokens are context, not proven edges, and their definitions are the research pack's** (R3, 12 seasons,
thresholds fit on 2014-2021 and tested on 2022-2025; read from `models/research/signal_thresholds.json` at run
time, see `research_constants.py`): `BUY↑` was dropped (wrong-signed out of sample); `FADE↓` survives for TEs
only; `USAGE↑` / `USAGE↓` survive for RB carry share only. They are shown labelled "context, not proven to
beat
projections; tracked in Model Check", and Model Check keeps score on them.

Windows are the player's last `XFP_WINDOW_GAMES` games PLAYED strictly BEFORE the slate (`before`, a
(season, week) pair), continuing across the season boundary as the research's windows do: a missed game is
never
a zero, and nothing from the week being predicted can reach its own signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model.history import XFP_TWO_POINT_POINTS, XFP_WEIGHTS
from dfs.player_join import normalize_position, normalize_team
from dfs.research_constants import signal_thresholds

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
XFP_WINDOW_GAMES = 3

# DK scoring applied to ffopportunity's expected stats: imported from the model package so there is ONE
# formula (`dfs.model.history.XFP_WEIGHTS`, and 2 points per expected two-point conversion). The sum is split
# by the prefix of each expected column (`rec_`/`receptions` = receiving, `rush_`, `pass_`) so redistribution
# can move receiving value with targets and rushing value with carries.
XFP_TWO_POINT = XFP_TWO_POINT_POINTS

# FADE / USAGE thresholds are NOT constants in this file: `research_constants.signal_thresholds()` reads them
# from `models/research/signal_thresholds.json` (FADE: last-3 DK/G at least `fade_gap_points` above last-3
# xFP/G, TE only; USAGE: RB carry-share jump of the last 2 games against the 6 before, at least +`usage_up`
# or at most -`usage_down`, needing all 8 prior games). The player's xFP/G must also be in the top half of
# his position (the measured FADE version; Sam, 2026-10-08), over the rosterable pool.
XFP_TOP_HALF_PCT = 0.5
_EPS = 1e-9  # a gap of exactly the threshold fires despite float error (0.3 - 0.2 is 0.0999...)

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


def attach_actual_points(weeks: pd.DataFrame, offense_actual: pd.DataFrame, season: int) -> pd.DataFrame:
    """Add `dk_actual` (the real DK points that game) for the rows of `weeks` in `season` from
    `results_actual.score_offense_actual`'s output (that season's), joined on gsis id and week. Rows of other
    seasons keep whatever `dk_actual` they had (NaN if none); rows with no stat line stay NaN."""
    actual = offense_actual[["player_id", "week", "dk_actual"]].rename(columns={"player_id": "GsisId"})
    actual = actual.drop_duplicates(subset=["GsisId", "week"], keep="last")
    mine = weeks[pd.to_numeric(weeks["season"], errors="coerce") == season].drop(
        columns="dk_actual", errors="ignore"
    )
    rest = weeks[pd.to_numeric(weeks["season"], errors="coerce") != season]
    joined = mine.merge(actual, on=["GsisId", "week"], how="left")
    return pd.concat([rest, joined], ignore_index=True) if not rest.empty else joined


def game_key(season, week):  # noqa: ANN001, ANN201 - Series or scalar
    """`season * 100 + week`: games sort chronologically across the season boundary (weeks never pass 22)."""
    return pd.to_numeric(season) * 100 + pd.to_numeric(week)


def recent_games(
    weeks: pd.DataFrame, *, before: tuple[int, int], window: int = XFP_WINDOW_GAMES
) -> pd.DataFrame:
    """Each player's last `window` games played strictly before `before` = (season, week), across seasons
    (one row per game, newest first)."""
    weeks = weeks.reset_index(drop=True)  # callers may concatenate seasons without renumbering
    key = game_key(weeks["season"], weeks["week"])
    past = weeks[key < game_key(*before)].assign(_key=key)
    past = past.sort_values(["GsisId", "_key"], ascending=[True, False])
    return past.groupby("GsisId", sort=False).head(window).drop(columns="_key")


def xfp_windows(
    weeks: pd.DataFrame, *, before: tuple[int, int], window: int = XFP_WINDOW_GAMES
) -> pd.DataFrame:
    """Per player (indexed by `GsisId`): `Games`, `xFP/G`, `DkG` (actual DK points per game), plus the volume
    per game that redistribution needs (`tgt_g`, `car_g`, `rec_xfp_g`, `rush_xfp_g`), his `tgt_share` /
    `car_share` of the team's volume over the window, and `LastKey` (the `game_key` of his latest game). Only
    games before `before`, across seasons."""
    recent = recent_games(weeks, before=before, window=window)
    columns = [
        "Games",
        "xFP/G",
        "DkG",
        "tgt_g",
        "car_g",
        "rec_xfp_g",
        "rush_xfp_g",
        "tgt_share",
        "car_share",
        "LastKey",
    ]
    if recent.empty:
        return pd.DataFrame(columns=columns)
    if "dk_actual" not in recent.columns:
        recent = recent.assign(dk_actual=np.nan)
    recent = recent.reset_index(drop=True)
    recent = recent.assign(_key=game_key(recent["season"], recent["week"]))
    grouped = recent.groupby("GsisId")
    return pd.DataFrame(
        {
            "Games": grouped["week"].count(),
            "xFP/G": grouped["xfp"].mean(),
            "DkG": grouped["dk_actual"].mean(),
            "tgt_g": grouped["targets"].mean(),
            "car_g": grouped["carries"].mean(),
            "rec_xfp_g": grouped["rec_xfp"].mean(),
            "rush_xfp_g": grouped["rush_xfp"].mean(),
            # share of his team's targets / carries over the window (sum over sum), the research's "regular"
            "tgt_share": grouped["targets"].sum() / grouped["team_targets"].sum().replace(0, np.nan),
            "car_share": grouped["carries"].sum() / grouped["team_carries"].sum().replace(0, np.nan),
            "LastKey": grouped["_key"].max(),
        }
    )


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


def fade_tokens(windows: pd.DataFrame, top_half: pd.Series, position: pd.Series) -> pd.Series:
    """`FADE↓` / "" per player, indexed like `windows`. TE only (the positions whose verdict in
    `signal_thresholds.json` is "keep"): last-3 DK/G at least `fade_gap_points` above last-3 xFP/G, with
    xFP/G in the top half of the position, over a full `XFP_WINDOW_GAMES` window. No touchdown-excess
    condition (it added nothing). `top_half` and `position` are indexed like `windows`."""
    thresholds = signal_thresholds()
    above = (windows["DkG"] - windows["xFP/G"]) >= thresholds.fade_gap_points - _EPS
    allowed = position.reindex(windows.index).isin(thresholds.fade_positions)
    eligible = top_half.reindex(windows.index).fillna(False).astype(bool) & (
        windows["Games"] >= XFP_WINDOW_GAMES
    )
    out = pd.Series("", index=windows.index, dtype=object)
    out[above & allowed & eligible] = TOKEN_FADE
    return out


def usage_jumps(weeks: pd.DataFrame, *, before: tuple[int, int]) -> pd.DataFrame:
    """RB carry-share jumps, per player (indexed by `GsisId`): the mean carry share of his last
    `recent_games` games minus the mean of the `earlier_games` games before them (the research's 2 and 6),
    across seasons, over games strictly before `before`. A carry share is his carries over his team's carries
    that game. A jump needs BOTH windows full (8 prior games). `token` is `USAGE↑` (jump at least
    +`usage_up`), `USAGE↓` (at most -`usage_down`) or ""; only positions whose verdict is "keep" (RB) are
    returned.
    `n` is the number of prior games used (always `prior_games_needed`)."""
    th = signal_thresholds()
    empty = pd.DataFrame(columns=["n", "token", "jump", "recent", "earlier"]).rename_axis("GsisId")
    weeks = weeks.reset_index(drop=True)
    key = game_key(weeks["season"], weeks["week"])
    past = weeks[(key < game_key(*before)) & weeks["Position"].isin(th.usage_positions)].assign(_key=key)
    if past.empty:
        return empty
    past = past.assign(share=past["carries"] / past["team_carries"].where(past["team_carries"] > 0))
    rows = []
    for gsis, games in past.sort_values("_key").groupby("GsisId"):
        if len(games) < th.prior_games_needed:
            continue
        last8 = games.iloc[-th.prior_games_needed :]
        earlier, recent = last8.iloc[: th.earlier_games], last8.iloc[th.earlier_games :]
        rows.append(
            {
                "GsisId": gsis,
                "n": len(last8),
                "recent": recent["share"].mean(),
                "earlier": earlier["share"].mean(),
            }
        )
    if not rows:
        return empty
    out = pd.DataFrame(rows).set_index("GsisId")
    out["jump"] = out["recent"] - out["earlier"]
    token = pd.Series("", index=out.index, dtype=object)
    token[out["jump"] >= th.usage_up - _EPS] = TOKEN_USAGE_UP
    token[out["jump"] <= -th.usage_down + _EPS] = TOKEN_USAGE_DOWN
    out["token"] = token
    return out
