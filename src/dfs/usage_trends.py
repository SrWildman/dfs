"""Usage trends: which players' last-3-games usage moved most against their earlier games, and by more than
normal week-to-week noise. Context only: nothing here feeds CalPts, a chip or a flag.

Pure and offline-testable (dataframes in, a dataframe out); `edge_finder_tab` reads the saved weekly inputs
and shows the result. Nothing here touches a sheet or the network.

**Metrics** (every share is a ratio of window SUMS, like `usage_metrics`; per-game values are window means):

- `Tgt%` (WR, TE, RB): targets / team targets.
- `WOPR` (WR, TE): 1.5 * target share + 0.7 * air-yards share (nflverse's own formula).
- `Air share` (WR, TE): receiving air yards / the team's passing air yards.
- `Rush%` (RB): carries / team carries.
- `Rec/G` (RB): receptions per game.
- `RZ/G` (RB, WR, TE): targets + carries from inside the opponent's 20, per game.
- `HVT/G` (RB): targets + carries from inside the opponent's 10, per game.
- `Snap%` (RB, WR, TE): offensive snap share, the mean over the games that have snap data.

Routes run are not available from any free source, and snaps are NOT used as a stand-in for them.

**Windows.** A player's *recent* window is his last `TREND_RECENT_GAMES` (3) games PLAYED this season (a game
counts when he has a row in the weekly stats, as in `usage_metrics`); *prior* is every earlier game this
season. A player needs a full recent window and at least `TREND_MIN_PRIOR_GAMES` earlier game(s) to be
compared at all. This season only: roles change between seasons, so last year's games would mislead.

**Noise band.** For each metric and position, the `Band` is one standard deviation of the recent-minus-prior
change across the qualifying players of that position this season. A player is marked only when his own change
is beyond `TREND_NOISE_SD` of those bands: `▲` above, `▼` below. The band needs `MIN_BAND_PLAYERS` players to
exist; with fewer, nothing is marked. The planned R6 signals research may replace this empirical band.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.player_join import normalize_name, normalize_position, normalize_team
from dfs.usage_metrics import WOPR_AIR_YARDS_WEIGHT, WOPR_TARGET_WEIGHT, team_week_totals

TREND_RECENT_GAMES = 3
TREND_MIN_PRIOR_GAMES = 1
TREND_NOISE_SD = 1.0
MIN_BAND_PLAYERS = 5
UP, DOWN = "▲", "▼"

TREND_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "Metric",
    "Recent",
    "Prior",
    "Change",
    "Band",
    "Z",
    "Direction",
    "RecentGames",
    "PriorGames",
]
WEEKLY_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "week",
    "targets",
    "receptions",
    "carries",
    "air",
    "team_targets",
    "team_carries",
    "team_air",
    "rz",
    "hvt",
    "snap",
]


@dataclass(frozen=True)
class Metric:
    name: str
    positions: frozenset[str]
    unit: str  # "pct" (a 0-1 share shown as a percent), "rate" (per game) or "wopr"


METRICS = (
    Metric("Tgt%", frozenset({"WR", "TE", "RB"}), "pct"),
    Metric("WOPR", frozenset({"WR", "TE"}), "wopr"),
    Metric("Air share", frozenset({"WR", "TE"}), "pct"),
    Metric("Rush%", frozenset({"RB"}), "pct"),
    Metric("Rec/G", frozenset({"RB"}), "rate"),
    Metric("RZ/G", frozenset({"RB", "WR", "TE"}), "rate"),
    Metric("HVT/G", frozenset({"RB"}), "rate"),
    Metric("Snap%", frozenset({"RB", "WR", "TE"}), "pct"),
)
METRIC_BY_NAME = {m.name: m for m in METRICS}
TREND_POSITIONS = ("WR", "TE", "RB")


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(0.0, index=frame.index)
    return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)


def weekly_frame(
    stats: pd.DataFrame, redzone: pd.DataFrame | None = None, snaps: pd.DataFrame | None = None
) -> pd.DataFrame:
    """One row per player-week from the weekly `stats_player` file (`WEEKLY_COLUMNS`).

    `redzone` is `usage_metrics.red_zone_counts` output (`GsisId`, `week`, `rz`, `hvt`); without it `rz`/`hvt`
    are blank, never 0. `snaps` is the weekly snap-count file (`player`, `team`, `position`, `week`,
    `offense_pct`), joined by normalised name and team (it has no nflverse id); a player it lacks has a blank
    `snap`, never 0."""
    if stats is None or stats.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    stats = stats[stats["season_type"] == "REG"].copy() if "season_type" in stats.columns else stats.copy()
    if stats.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    stats["position"] = stats["position"].map(normalize_position)
    totals = team_week_totals(stats)
    players = stats[stats["position"].isin(TREND_POSITIONS)].copy()
    frame = pd.DataFrame(
        {
            "GsisId": players["player_id"],
            "Name": players["player_display_name"],
            "Team": players["team"],
            "Position": players["position"],
            "week": players["week"],
            "targets": _col(players, "targets"),
            "receptions": _col(players, "receptions"),
            "carries": _col(players, "carries"),
            "air": _col(players, "receiving_air_yards"),
        }
    )
    frame = frame.drop_duplicates(subset=["GsisId", "week"], keep="last")
    frame = frame.merge(totals.rename(columns={"team": "Team"}), on=["Team", "week"], how="left")
    if redzone is not None and not redzone.empty:
        rz = redzone.drop_duplicates(subset=["GsisId", "week"], keep="last")
        frame = frame.merge(rz[["GsisId", "week", "rz", "hvt"]], on=["GsisId", "week"], how="left")
        frame[["rz", "hvt"]] = frame[["rz", "hvt"]].fillna(0.0)  # played, no red-zone touch: a real 0
    else:
        frame["rz"] = np.nan
        frame["hvt"] = np.nan
    frame["snap"] = np.nan
    if snaps is not None and not snaps.empty:
        keyed = snaps.assign(
            _k=[
                f"{normalize_name(n)}|{normalize_team(t)}"
                for n, t in zip(snaps["player"], snaps["team"], strict=True)
            ],
            week=pd.to_numeric(snaps["week"], errors="coerce"),
        ).drop_duplicates(subset=["_k", "week"], keep="last")
        frame["_k"] = [
            f"{normalize_name(n)}|{normalize_team(t)}"
            for n, t in zip(frame["Name"], frame["Team"], strict=True)
        ]
        merged = frame.merge(keyed[["_k", "week", "offense_pct"]], on=["_k", "week"], how="left")
        frame["snap"] = merged["offense_pct"].to_numpy()
        frame = frame.drop(columns="_k")
    return frame[WEEKLY_COLUMNS].reset_index(drop=True)


def _name_team_keys(names: pd.Series, teams: pd.Series) -> list[str]:
    return [f"{normalize_name(n)}|{normalize_team(t)}" for n, t in zip(names, teams, strict=True)]


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator and denominator > 0 else float("nan")


def metric_values(window: pd.DataFrame) -> dict[str, float]:
    """Every metric over one window of a player's games (NaN where the window cannot support it)."""
    games = len(window)
    if games == 0:
        return {m.name: float("nan") for m in METRICS}
    tgt = _ratio(window["targets"].sum(), window["team_targets"].sum())
    air = _ratio(window["air"].sum(), window["team_air"].sum())
    has_rz = window["rz"].notna().all()
    snaps = window["snap"].dropna()
    return {
        "Tgt%": tgt,
        "WOPR": WOPR_TARGET_WEIGHT * tgt + WOPR_AIR_YARDS_WEIGHT * air,
        "Air share": air,
        "Rush%": _ratio(window["carries"].sum(), window["team_carries"].sum()),
        "Rec/G": float(window["receptions"].sum() / games),
        "RZ/G": float(window["rz"].sum() / games) if has_rz else float("nan"),
        "HVT/G": float(window["hvt"].sum() / games) if has_rz else float("nan"),
        "Snap%": float(snaps.mean()) if len(snaps) == games else float("nan"),
    }


def compute_trends(
    weekly: pd.DataFrame,
    *,
    population: set[str] | None = None,
    recent_games: int = TREND_RECENT_GAMES,
    min_prior: int = TREND_MIN_PRIOR_GAMES,
    noise_sd: float = TREND_NOISE_SD,
) -> pd.DataFrame:
    """One row per player and metric (`TREND_COLUMNS`) for everyone with a full recent window and enough
    earlier games. `Direction` is `▲`/`▼` only when the change is beyond `noise_sd` standard deviations of
    that position's changes in that metric; otherwise blank. `population` limits whom the band is measured
    on (the players that matter for the slate); `None` uses everyone."""
    if weekly is None or weekly.empty:
        return pd.DataFrame(columns=TREND_COLUMNS)
    rows = []
    for gsis, games in weekly.sort_values("week").groupby("GsisId", sort=False):
        if len(games) < recent_games + min_prior:
            continue
        recent, prior = games.iloc[-recent_games:], games.iloc[:-recent_games]
        now, before = metric_values(recent), metric_values(prior)
        last = games.iloc[-1]
        for metric in METRICS:
            if last["Position"] not in metric.positions:
                continue
            a, b = now[metric.name], before[metric.name]
            if np.isnan(a) or np.isnan(b):
                continue
            rows.append(
                {
                    "GsisId": gsis,
                    "Name": last["Name"],
                    "Team": last["Team"],
                    "Position": last["Position"],
                    "Metric": metric.name,
                    "Recent": a,
                    "Prior": b,
                    "Change": a - b,
                    "RecentGames": len(recent),
                    "PriorGames": len(prior),
                }
            )
    if not rows:
        return pd.DataFrame(columns=TREND_COLUMNS)
    out = pd.DataFrame(rows)
    counted = out if population is None else out[out["GsisId"].isin(population)]
    spread = counted.groupby(["Position", "Metric"])["Change"].agg(["std", "count"])
    spread = spread[spread["count"] >= MIN_BAND_PLAYERS]["std"]
    out["Band"] = [spread.get((p, m), np.nan) for p, m in zip(out["Position"], out["Metric"], strict=True)]
    out["Z"] = out["Change"] / out["Band"].where(out["Band"] > 0)
    beyond = out["Change"].abs() > noise_sd * out["Band"]
    out["Direction"] = np.where(
        beyond & (out["Change"] > 0), UP, np.where(beyond & (out["Change"] < 0), DOWN, "")
    )
    return out[TREND_COLUMNS].reset_index(drop=True)


def format_value(metric: str, value: float) -> str:
    """A metric value as the tab shows it: `18%`, `0.62`, `5.3`."""
    unit = METRIC_BY_NAME[metric].unit
    if unit == "pct":
        return f"{value * 100:.0f}%"
    if unit == "wopr":
        return f"{value:.2f}"
    return f"{value:.1f}"


def why(row: pd.Series) -> str:
    """The plain-English line for one trend row, e.g. `Tgt% 18% → 26% over the last 3 (▲, beyond normal
    week-to-week noise); the earlier figure rests on 1 game`."""
    metric = row["Metric"]
    arrow = row["Direction"]
    earlier = int(row["PriorGames"])
    return (
        f"{metric} {format_value(metric, row['Prior'])} → {format_value(metric, row['Recent'])} over the "
        f"last {int(row['RecentGames'])} ({arrow}, beyond normal week-to-week noise); the earlier figure "
        f"rests on {earlier} game{'s' if earlier != 1 else ''}"
    )
