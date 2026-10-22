"""Opportunity per dollar: where a player's usage outruns his salary. Pure: frames in, frames out.

Sam's question (2026-10-21): "targets per route run, weighted opportunities, targets per dollar... help
identify good plays for their price." Our own research already says a usage model does not beat TFFB as a
PROJECTION (UM, 12 seasons) and that `USAGE↑` is weak, so nothing here feeds `CalPts` or a build: this is the
PRICE angle, a read on what the salary has not caught, scored in Model Check as a shadow
(`results_signals.opportunity_report`) until the 2026 weeks show it holds.

**Per player, this season only** (roles change between seasons, so there is no prior-season blend; a player
with no 2026 game yet is blank): over the games he PLAYED strictly before the slate week (a missed game is
skipped, never a zero), as of the slate, nothing from the week being predicted.

- `xFP Szn` / `xFP L3`: ffopportunity's expected fantasy points (DK scoring, `xfp.py`) per game, over the
  season so far and over his last three games; `xFP trend` = L3 minus Szn (needs four games).
- `TPRR~` / `TPRR~ L3`: targets per route run, a PROXY. Free in-season route counts do not exist in bulk,
  so a game's routes are estimated as `offensive snaps x his team's dropback rate in that game` (nflverse
  snap counts and play-by-play: quarterback dropbacks, sacks and scrambles included, over scrimmage pass
  and run plays). It overstates routes for a back who stays in to block and for a tight end who blocks; it
  is labelled a proxy everywhere it is shown. The ratio is a ratio of window SUMS, and blank under
  `MIN_ROUTES` estimated routes.
- `xFP/$`: `xFP Szn` per $1,000 of salary. `Tgt/$`: targets per game per $1,000 (WR, TE, RB). `HVT/$`:
  high-value touches (targets plus carries from inside the opponent's 10, `usage_metrics.HVT_YARDLINE`) per
  game per $1,000, RB only.
- `PriceGap`: how far his `xFP Szn` sits above what his salary usually buys at his position, in standard
  deviations. Within a position's rosterable pool, `xFP Szn` is fitted on salary (a straight line); the
  gap is his residual over the residual SD. +1.0 = one SD more expected scoring from his role than a
  typical player at his price. Positive means the opportunity is bigger than the price says; it is not a
  projection and it is not advice.

`read_research_frames` is the one cache read (the research cache's snap counts, play-by-play and player
crosswalk); everything else is pure. `player_games` builds the per-game table once (`GAME_COLUMNS`);
`attach_features` reads it for a slate, cut at the slate week, so Model Check's backfill and the live sync
call the same code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.derived import LAST5_COLUMNS, OPPORTUNITY_COLUMNS
from dfs.log import get_logger
from dfs.player_join import normalize_name, normalize_position
from dfs.usage_metrics import HVT_YARDLINE

log = get_logger("opportunity")

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
RECEIVER_POSITIONS = ("RB", "WR", "TE")
WINDOW_GAMES = 3
MIN_GAMES = 3  # games before xFP/$ and PriceGap mean anything
TREND_MIN_GAMES = 4  # L3 has to differ from the season for a trend
MIN_ROUTES = 40.0  # estimated routes in a window before TPRR~ is shown
MIN_FIT_PLAYERS = 8  # rosterable players at a position before PriceGap can be fitted

# The Edge columns this module fills are `derived.OPPORTUNITY_COLUMNS` / `LAST5_COLUMNS` (EdgeRaw order; the
# names are the headers the sheet prints).
FEATURE_COLUMNS = OPPORTUNITY_COLUMNS
# Last five games played, from the Player card's game log (`player_card.usage_log`): the averages the
# card used to read from its hidden raw tab.
LAST5_SOURCE = {"Snap% L5": "Snap%", "Tgt L5": "Tgt", "Car L5": "Car", "DK L5": "DK"}

GAME_COLUMNS = [
    "GsisId",
    "season",
    "week",
    "xfp",
    "targets",
    "hvt",
    "routes",
    "dk_actual",
]


def team_dropback_rates(pbp: pd.DataFrame) -> pd.DataFrame:
    """`team, season, week, dropback_rate`: quarterback dropbacks over scrimmage pass and run plays, per team
    game (regular season, two-point tries out)."""
    if pbp is None or pbp.empty:
        return pd.DataFrame(columns=["team", "season", "week", "dropback_rate"])
    plays = pbp
    if "season_type" in plays.columns:
        plays = plays[plays["season_type"] == "REG"]
    plays = plays[plays["play_type"].isin(["pass", "run"]) & plays["posteam"].notna()]
    plays = plays[pd.to_numeric(plays["two_point_attempt"], errors="coerce").fillna(0) != 1]
    drop = pd.to_numeric(plays["qb_dropback"], errors="coerce").fillna(0)
    grouped = drop.groupby([plays["posteam"], plays["season"], plays["week"]])
    out = (grouped.sum() / grouped.size()).rename("dropback_rate").reset_index()
    return out.rename(columns={"posteam": "team"})


def offense_snap_counts(snaps: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """`GsisId, season, week, team, snaps`: offensive snaps through the PFR -> gsis crosswalk (an id that maps
    to two players, or the reverse, is dropped rather than guessed)."""
    if snaps is None or snaps.empty:
        return pd.DataFrame(columns=["GsisId", "season", "week", "team", "snaps"])
    cw = players[["pfr_id", "gsis_id"]].drop_duplicates()
    cw = cw[~cw["pfr_id"].duplicated(keep=False) & ~cw["gsis_id"].duplicated(keep=False)]
    s = snaps.merge(cw, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    if "game_type" in s.columns:
        s = s[s["game_type"] == "REG"]
    s = s.groupby(["gsis_id", "season", "week", "team"], as_index=False)["offense_snaps"].max()
    return s.rename(columns={"gsis_id": "GsisId", "offense_snaps": "snaps"})


def high_value_touches(pbp: pd.DataFrame) -> pd.DataFrame:
    """`GsisId, season, week, hvt`: targets plus carries from inside the opponent's 10 per player-game, the
    definition of the sheet's `HVT/G` (`usage_metrics.red_zone_counts`)."""
    if pbp is None or pbp.empty:
        return pd.DataFrame(columns=["GsisId", "season", "week", "hvt"])
    plays = pbp
    if "season_type" in plays.columns:
        plays = plays[plays["season_type"] == "REG"]
    plays = plays[plays["play_type"].isin(["pass", "run"])]
    plays = plays[pd.to_numeric(plays["two_point_attempt"], errors="coerce").fillna(0) != 1]
    yard = pd.to_numeric(plays["yardline_100"], errors="coerce")
    is_pass = pd.to_numeric(plays["pass"], errors="coerce").fillna(0) == 1
    is_rush = pd.to_numeric(plays["rush"], errors="coerce").fillna(0) == 1
    touches = []
    for ids, mask in ((plays["receiver_player_id"], is_pass), (plays["rusher_player_id"], is_rush)):
        keep = mask & ids.notna() & yard.notna()
        touches.append(
            pd.DataFrame(
                {
                    "GsisId": ids[keep],
                    "season": plays["season"][keep],
                    "week": plays["week"][keep],
                    "hvt": (yard[keep] <= HVT_YARDLINE).astype(int),
                }
            )
        )
    all_touches = pd.concat(touches, ignore_index=True)
    return all_touches.groupby(["GsisId", "season", "week"], as_index=False)["hvt"].sum()


def read_research_frames(season: int, notes: list[str]) -> tuple[pd.DataFrame | None, ...]:
    """`(snaps, play-by-play, players crosswalk)` for the route proxy from the research cache, each None
    when it cannot be read (TPRR~ and HVT/$ blank, the xFP columns unaffected)."""
    from dfs.research import data as research_data

    out = []
    for label, read in (
        ("snap counts", lambda: research_data.read_snaps([season])),
        ("play-by-play", lambda: research_data.read_pbp([season])),
        ("players crosswalk", research_data.read_players),
    ):
        try:
            out.append(read())
        except (research_data.ResearchDataError, OSError, ValueError, KeyError) as e:
            notes.append(f"Opportunity: {label} not readable ({e}); TPRR~ and HVT/$ blank")
            log.warning("opportunity: %s not readable: %s", label, e)
            out.append(None)
    return tuple(out)


def player_games(
    weeks: pd.DataFrame,
    snaps: pd.DataFrame | None,
    pbp: pd.DataFrame | None,
    players: pd.DataFrame | None,
) -> pd.DataFrame:
    """One row per player-game (`GAME_COLUMNS`) from ffopportunity's player-weeks (`xfp.player_weeks` with
    `dk_actual`): expected points, targets, high-value touches (0 when he had none) and the ESTIMATED routes
    (`snaps x team dropback rate`; NaN when the snap row or the play-by-play is missing, which keeps that game
    out of a TPRR~ window rather than counting it as zero)."""
    if weeks is None or weeks.empty:
        return pd.DataFrame(columns=GAME_COLUMNS)
    games = weeks[weeks["Position"].isin(SKILL_POSITIONS)].copy()
    games["season"] = pd.to_numeric(games["season"], errors="coerce")
    games["week"] = pd.to_numeric(games["week"], errors="coerce")
    games = games.drop_duplicates(["GsisId", "season", "week"], keep="last")
    games = games.rename(columns={"Team": "team"})
    if pbp is not None and not pbp.empty:
        games = games.merge(high_value_touches(pbp), on=["GsisId", "season", "week"], how="left")
        games["hvt"] = games["hvt"].fillna(0.0)
    else:
        games["hvt"] = np.nan
    games["routes"] = np.nan
    if snaps is not None and not snaps.empty and players is not None and pbp is not None and not pbp.empty:
        counts = offense_snap_counts(snaps, players).drop(columns="team")
        rates = team_dropback_rates(pbp)
        games = games.merge(counts, on=["GsisId", "season", "week"], how="left")
        games = games.merge(rates, on=["team", "season", "week"], how="left")
        games["routes"] = games["snaps"] * games["dropback_rate"]
    if "dk_actual" not in games.columns:
        games["dk_actual"] = np.nan
    for column in ("xfp", "targets", "dk_actual"):
        games[column] = pd.to_numeric(games[column], errors="coerce")
    return games[GAME_COLUMNS].reset_index(drop=True)


def _window_ratio(window: pd.DataFrame) -> float:
    routes = window["routes"].sum(min_count=1)
    if pd.isna(routes) or routes < MIN_ROUTES:
        return np.nan
    rows = window.dropna(subset=["routes"])
    return float(rows["targets"].sum() / rows["routes"].sum())


def player_features(games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Per player (indexed by `GsisId`): `Games`, and the season-to-date and last-three measures of the games
    PLAYED before `(season, week)` in `season` only: `xfp_szn`, `xfp_l3`, `xfp_trend`, `tgt_pg`, `hvt_pg`,
    `tprr`, `tprr_l3`. Nothing at or after the slate week can reach a feature."""
    cols = ["Games", "xfp_szn", "xfp_l3", "xfp_trend", "tgt_pg", "hvt_pg", "tprr", "tprr_l3"]
    if games is None or games.empty:
        return pd.DataFrame(columns=cols)
    past = games[(games["season"] == season) & (games["week"] < week)]
    if past.empty:
        return pd.DataFrame(columns=cols)
    past = past.sort_values(["GsisId", "week"], ascending=[True, False])
    rows = {}
    for gsis, g in past.groupby("GsisId", sort=False):
        last3 = g.head(WINDOW_GAMES)
        n = len(g)
        szn = g["xfp"].mean()
        l3 = last3["xfp"].mean()
        rows[gsis] = {
            "Games": n,
            "xfp_szn": szn,
            "xfp_l3": l3,
            "xfp_trend": l3 - szn if n >= TREND_MIN_GAMES else np.nan,
            "tgt_pg": g["targets"].mean(),
            "hvt_pg": g["hvt"].mean(),
            "tprr": _window_ratio(g),
            "tprr_l3": _window_ratio(last3),
        }
    return pd.DataFrame.from_dict(rows, orient="index", columns=cols)


def price_gap(xfp: pd.Series, salary: pd.Series, position: pd.Series, pool: pd.Series) -> pd.Series:
    """Residual z of `xfp` against a straight-line fit on salary, within each of QB / RB / WR / TE over the
    rosterable pool (players with an xFP). Blank where a position has fewer than `MIN_FIT_PLAYERS` fitted
    players or no spread."""
    out = pd.Series(np.nan, index=xfp.index, dtype=float)
    for pos in SKILL_POSITIONS:
        at = (position == pos) & xfp.notna() & (salary > 0)
        fit = at & pool
        if fit.sum() < MIN_FIT_PLAYERS:
            continue
        slope, intercept = np.polyfit(salary[fit] / 1000.0, xfp[fit], 1)
        resid = xfp[at] - (slope * salary[at] / 1000.0 + intercept)
        sd = float((xfp[fit] - (slope * salary[fit] / 1000.0 + intercept)).std(ddof=2))
        if sd > 0:
            out[at] = resid / sd
    return out


def attach_features(frame: pd.DataFrame, games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """`FEATURE_COLUMNS` for each row of `frame` (index-aligned; needs `GsisId`, `Position`, `Salary` and
    optionally `RosterablePool`). Blank where a measure does not apply: TPRR~ and Tgt/$ for QB and DST, HVT/$
    for anyone but RB, everything for a player with no game yet this season."""
    out = pd.DataFrame(np.nan, index=frame.index, columns=FEATURE_COLUMNS, dtype=float)
    if "GsisId" not in frame.columns:
        return out
    feats = player_features(games, season, week)
    if feats.empty:
        return out
    ids = frame["GsisId"].where(frame["GsisId"].isin(feats.index))
    have = ids.notna()
    f = feats.reindex(ids[have]).set_index(frame.index[have])
    position = frame["Position"]
    per_k = pd.to_numeric(frame["Salary"], errors="coerce") / 1000.0
    per_k = per_k.where(per_k > 0)
    played = f["Games"] >= MIN_GAMES

    def put(column: str, values: pd.Series, mask: pd.Series | None = None) -> None:
        values = pd.to_numeric(values, errors="coerce")
        if mask is not None:
            values = values.where(mask.reindex(values.index, fill_value=False))
        out.loc[values.index, column] = values

    receiver = position.reindex(f.index).isin(RECEIVER_POSITIONS)
    skill = position.reindex(f.index).isin(SKILL_POSITIONS)
    rb = position.reindex(f.index) == "RB"
    put("TPRR~", f["tprr"], receiver)
    put("TPRR~ L3", f["tprr_l3"], receiver)
    put("xFP Szn", f["xfp_szn"], skill)
    put("xFP L3", f["xfp_l3"], skill)
    put("xFP trend", f["xfp_trend"], skill)
    put("xFP/$", f["xfp_szn"] / per_k.reindex(f.index), skill & played)
    put("Tgt/$", f["tgt_pg"] / per_k.reindex(f.index), receiver & played)
    put("HVT/$", f["hvt_pg"] / per_k.reindex(f.index), rb & played)
    pool = (
        frame["RosterablePool"].astype(bool)
        if "RosterablePool" in frame.columns
        else pd.Series(True, index=frame.index)
    )
    xfp_for_gap = out["xFP Szn"].where(out.index.isin(f.index[played]))
    out["PriceGap"] = price_gap(xfp_for_gap, pd.to_numeric(frame["Salary"], errors="coerce"), position, pool)
    return out


def last5_averages(log: pd.DataFrame | None) -> pd.DataFrame:
    """`NameKey, Position` plus `LAST5_COLUMNS`: the mean over each player's last five games PLAYED of the
    Player card's game log (`player_card.usage_log`). A column he has no value in (a quarterback's targets,
    snap share when the snap file failed) stays blank, never 0."""
    columns = ["NameKey", "Position", *LAST5_COLUMNS]
    if log is None or log.empty:
        return pd.DataFrame(columns=columns)
    grouped = log.groupby(["NameKey", "Position"], as_index=False)
    out = grouped[list(LAST5_SOURCE.values())].mean()
    return out.rename(columns={v: k for k, v in LAST5_SOURCE.items()})[columns]


def attach_last5(frame: pd.DataFrame, log: pd.DataFrame | None) -> pd.DataFrame:
    """`LAST5_COLUMNS` for each row of `frame` (needs `Name`, `Position`), joined the way the card joined the
    log: normalized name and position."""
    out = pd.DataFrame(np.nan, index=frame.index, columns=LAST5_COLUMNS, dtype=float)
    avg = last5_averages(log)
    if avg.empty:
        return out
    keyed = avg.set_index(["NameKey", "Position"])
    keys = pd.MultiIndex.from_arrays(
        [frame["Name"].map(normalize_name), frame["Position"].map(normalize_position)]
    )
    found = keyed.reindex(keys)
    found.index = frame.index
    return found[LAST5_COLUMNS].astype(float)
