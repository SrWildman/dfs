"""Rest-of-season points per player per remaining week, in ONE league's scoring.

**Inputs.** Sleeper's weekly stat lines (what happened) and its weekly stat projections (every remaining week,
matchup by matchup), both scored with the league's own `scoring_settings` (`scoring.py`). For the coming week
only, FantasyPros' and ESPN's stat projections (archived by the weekly sync) adjust Sleeper's by a consensus
factor: the sources' mean on the stats all three project (yards, TDs, receptions, turnovers), scored in this
league, over Sleeper's on the same stats (`CONSENSUS_CLIP`). CalPts is not used: its corrections are by
DraftKings salary tier on DraftKings points, and neither exists for a season-long stat line.

**The blend.** A player's points in week t = `a * season_rate + (1 - a) * projection_t`, with
`a = n / (n + k)` for n games played so far. k is fitted on this season's finished weeks
(`fit_blend_k`): at every cutoff week c, predict each player's per-game points over weeks c+1.. from what was
known at c (season rate through c, Sleeper's projection for c+1), and pick the k with the lowest error.
`accuracy_check` reports that error held out (each cutoff scored with k fitted on the other cutoffs) beside
two naive baselines, the season average and the last-3 average.

**Availability.** A bye week is 0 (a team plays week t when Sleeper projects it an opponent). `Out` is 0 this
week, `Doubtful` this week times `DOUBTFUL_FACTOR`; IR / PUP / NFI / suspended are 0 for
`ir_weeks_out` weeks (season.toml). A player with no NFL team is 0, and so is one Sleeper projects in no
remaining week (a backup's one big game is not a rate).

**Spread.** For the chopped-league simulation, each week's points carry a standard deviation
`sd = a_pos + b_pos * points`, fitted per position on this season's weekly misses of Sleeper's projections
(`fit_spread`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

import numpy as np
import pandas as pd

from dfs import store
from dfs.nfl_calendar import week_start_date
from dfs.player_join import _last_name_key, join_key
from dfs.season_long import sleeper_league as sl
from dfs.season_long.scoring import score_stats, stats_frame

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
OUT_ONE_WEEK = {"Out"}
OUT_LONG = {"IR", "PUP", "NFI", "Sus", "Suspended", "COV", "DNR"}
DOUBTFUL_FACTOR = 0.25
_RANKING_ONLY = {"adp_dd_ppr", "pos_adp_dd_ppr"}
# The coming week's consensus factor is clipped here, and needs this many points of Sleeper's common stats.
CONSENSUS_CLIP = (0.6, 1.6)
CONSENSUS_MIN_PTS = 3.0
CONSENSUS_SOURCES = ("fantasypros", "espn")
# Sleeper key -> the weekly sync's canonical column, for the stats every source projects.
COMMON_STATS = {
    "pass_yd": "pass_yd",
    "pass_td": "pass_td",
    "pass_int": "pass_int",
    "rush_yd": "rush_yd",
    "rush_td": "rush_td",
    "rec": "rec",
    "rec_yd": "rec_yd",
    "rec_td": "rec_td",
    "fum_lost": "fum_lost",
}
# The accuracy check scores players who matter: season rate or projection at least this many points.
RELEVANT_MIN_PTS = 6.0
BLEND_K_GRID = (0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 15, 25, 50, 1e9)
DEFAULT_BLEND_K = 4.0
DEFAULT_SPREAD = {
    "QB": (4.0, 0.25),
    "RB": (2.5, 0.45),
    "WR": (2.5, 0.5),
    "TE": (2.0, 0.5),
    "K": (3.0, 0.25),
    "DEF": (3.5, 0.3),
}


def player_table(players: dict) -> pd.DataFrame:
    rows = []
    for pid, p in players.items():
        pos = p.get("position") or ""
        if pos not in POSITIONS:
            continue
        if pos == "DEF":
            name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or pid
        else:
            name = p.get("full_name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
        rows.append(
            {
                "player_id": str(pid),
                "name": name,
                "position": pos,
                "fantasy_positions": tuple(
                    fp for fp in (p.get("fantasy_positions") or [pos]) if fp in POSITIONS
                )
                or (pos,),
                "team": p.get("team") or "",
                "injury_status": p.get("injury_status") or "",
                "search_rank": p.get("search_rank") if p.get("search_rank") is not None else 9_999_999,
            }
        )
    return pd.DataFrame(rows).set_index("player_id")


def actuals(scoring: dict, season: int, weeks: list[int]) -> pd.DataFrame:
    """[player_id, week, pts, played]: every stat line of the given finished weeks."""
    parts = []
    for w in weeks:
        f = stats_frame(sl.fetch_week_stats(season, w, final=True))
        if f.empty:
            continue
        pts = score_stats(f, scoring)
        gp = pd.to_numeric(f["gp"], errors="coerce")
        played = (gp > 0) | (gp.isna() & (pts != 0))
        parts.append(pd.DataFrame({"player_id": f["player_id"], "week": w, "pts": pts, "played": played}))
    if not parts:
        return pd.DataFrame(columns=["player_id", "week", "pts", "played"])
    out = pd.concat(parts, ignore_index=True)
    return out[out["played"]].reset_index(drop=True)


def projection_stats(season: int, week: int, *, final: bool) -> pd.DataFrame:
    recs = [
        r
        for r in sl.fetch_week_projections(season, week, final=final)
        if set(r.get("stats") or {}) - _RANKING_ONLY
    ]
    return stats_frame(recs)


def projections(scoring: dict, season: int, weeks: list[int], finished_through: int) -> pd.DataFrame:
    """[player_id, week, proj, team, opponent] per week (a finished week's are Sleeper's last before it)."""
    parts = []
    for w in weeks:
        f = projection_stats(season, w, final=w <= finished_through)
        if f.empty:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "player_id": f["player_id"],
                    "week": w,
                    "proj": score_stats(f, scoring),
                    "team": f["team"],
                    "opponent": f["opponent"],
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["player_id", "week", "proj", "team", "opponent"])
    return pd.concat(parts, ignore_index=True)


def teams_playing(proj: pd.DataFrame) -> dict[int, set[str]]:
    has_opp = proj[proj["opponent"].astype(str).str.len() > 0]
    return {int(w): set(g["team"]) for w, g in has_opp.groupby("week")}


# --- consensus (FantasyPros / ESPN) for one week ------------------------------------------------------------


def _last_snapshot_before(source: str, season: int, week: int) -> pd.DataFrame | None:
    start = datetime.combine(week_start_date(week, season), time(0), tzinfo=UTC)
    sunday = start + timedelta(days=5, hours=17)
    best = None
    for stamp, path in store.snapshot_paths(source):
        if start <= stamp < sunday:
            best = path
    return pd.read_csv(best) if best is not None else None


def _common_points(frame: pd.DataFrame, scoring: dict, columns: dict[str, str]) -> pd.Series:
    sub = pd.DataFrame(
        {
            k: pd.to_numeric(frame[c], errors="coerce") if c in frame.columns else 0.0
            for k, c in columns.items()
        },
        index=frame.index,
    )
    pts = score_stats(sub, {k: scoring.get(k, 0.0) for k in COMMON_STATS})
    if "two_pt" in frame.columns:
        pts = pts + pd.to_numeric(frame["two_pt"], errors="coerce").fillna(0.0) * scoring.get("rec_2pt", 2.0)
    return pts


def consensus_factor(
    scoring: dict, season: int, week: int, sleeper_stats: pd.DataFrame, table: pd.DataFrame
) -> tuple[pd.Series, list[str]]:
    """Per player_id: (mean of FantasyPros/ESPN common-stat points) / Sleeper's, clipped; sources used."""
    offense = sleeper_stats[sleeper_stats["player_id"].map(table["position"]).isin(["QB", "RB", "WR", "TE"])]
    if offense.empty:
        return pd.Series(dtype=float), []
    base = _common_points(offense, scoring, {k: k for k in COMMON_STATS})
    base = base + sum(
        offense[c].fillna(0.0) for c in ("pass_2pt", "rush_2pt", "rec_2pt") if c in offense.columns
    ) * scoring.get("rec_2pt", 2.0)
    base.index = offense["player_id"].values
    meta = table.loc[table.index.intersection(base.index)]
    keys = pd.Series(
        [join_key(n, t, p) for n, t, p in zip(meta["name"], meta["team"], meta["position"], strict=False)],
        index=meta.index,
    )
    last_keys = pd.Series(
        [
            _last_name_key(n, t, p)
            for n, t, p in zip(meta["name"], meta["team"], meta["position"], strict=False)
        ],
        index=meta.index,
    )
    per_source, used = [], []
    for source in CONSENSUS_SOURCES:
        snap = _last_snapshot_before(source, season, week)
        if snap is None or snap.empty:
            continue
        pts = _common_points(snap, scoring, COMMON_STATS)
        by_key = dict(
            zip(
                (
                    join_key(n, t, p)
                    for n, t, p in zip(snap["Name"], snap["Team"], snap["Position"], strict=False)
                ),
                pts,
                strict=False,
            )
        )
        last = pd.Series(
            pts.values,
            index=[
                _last_name_key(n, t, p)
                for n, t, p in zip(snap["Name"], snap["Team"], snap["Position"], strict=False)
            ],
        )
        last = last[~last.index.duplicated(keep=False)]
        mapped = keys.map(by_key)
        mapped = mapped.fillna(last_keys.map(last))
        if mapped.notna().sum():
            per_source.append(mapped)
            used.append(source)
    if not per_source:
        return pd.Series(dtype=float), []
    mean_src = pd.concat(per_source, axis=1).mean(axis=1)
    b = base.reindex(mean_src.index)
    factor = (mean_src / b).where(b >= CONSENSUS_MIN_PTS).clip(*CONSENSUS_CLIP).dropna()
    return factor, used


# --- the blend and its accuracy -----------------------------------------------------------------------------


def _rates_at(act: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    """Per player at the end of week `cutoff`: games, season rate, last-3 rate."""
    known = act[act["week"] <= cutoff].sort_values("week")
    g = known.groupby("player_id")["pts"]
    out = pd.DataFrame({"n": g.size(), "season": g.mean(), "last3": g.apply(lambda s: s.tail(3).mean())})
    return out


def _cutoff_rows(
    act: pd.DataFrame, proj: pd.DataFrame, through: int, next_proj: dict[int, pd.Series] | None = None
) -> pd.DataFrame:
    """One row per (cutoff, player): what was known at the cutoff and the per-game points that followed."""
    rows = []
    for c in range(1, through):
        rates = _rates_at(act, c)
        after = (
            act[(act["week"] > c) & (act["week"] <= through)]
            .groupby("player_id")["pts"]
            .agg(["mean", "size"])
        )
        # The forward rate known at c: Sleeper's projection for the first later week the player has one.
        later = proj[(proj["week"] > c) & (proj["week"] <= through)].sort_values("week")
        fwd = later.drop_duplicates("player_id").set_index("player_id")["proj"]
        if next_proj is not None and c + 1 in next_proj:
            fwd = next_proj[c + 1].reindex(fwd.index).fillna(fwd)
        df = rates.join(after, how="inner").join(fwd.rename("proj"), how="inner")
        df = df[(df["season"] >= RELEVANT_MIN_PTS) | (df["proj"] >= RELEVANT_MIN_PTS)]
        df["cutoff"] = c
        rows.append(df.reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _blend(rows: pd.DataFrame, k: float) -> pd.Series:
    a = rows["n"] / (rows["n"] + k)
    return a * rows["season"] + (1 - a) * rows["proj"]


def _mae(pred: pd.Series, rows: pd.DataFrame) -> float:
    w = rows["size"]
    return float(((pred - rows["mean"]).abs() * w).sum() / w.sum()) if len(rows) else float("nan")


def _best_k(rows: pd.DataFrame) -> float:
    return min(BLEND_K_GRID, key=lambda k: _mae(_blend(rows, k), rows))


def fit_blend_k(scoring: dict, season: int, through: int) -> float:
    if through < 2:
        return DEFAULT_BLEND_K
    weeks = list(range(1, through + 1))
    rows = _cutoff_rows(
        actuals(scoring, season, weeks), projections(scoring, season, weeks, through), through
    )
    return _best_k(rows) if len(rows) else DEFAULT_BLEND_K


@dataclass
class AccuracyReport:
    through: int
    players: int
    k: float
    mae: dict[str, float]
    by_position: pd.DataFrame
    cutoffs: dict[int, dict[str, float]] = field(default_factory=dict)
    consensus_note: str = ""


def accuracy_check(scoring: dict, season: int, through: int, table: pd.DataFrame) -> AccuracyReport:
    """Held out: each cutoff week's blend uses the k fitted on the other cutoffs."""
    weeks = list(range(1, through + 1))
    act = actuals(scoring, season, weeks)
    proj = projections(scoring, season, weeks, through)
    rows = _cutoff_rows(act, proj, through)
    rows["position"] = rows["player_id"].map(table["position"])
    preds = pd.Series(index=rows.index, dtype=float)
    cut_report: dict[int, dict[str, float]] = {}
    for c in sorted(rows["cutoff"].unique()):
        test = rows["cutoff"] == c
        k = _best_k(rows[~test]) if (~test).any() else DEFAULT_BLEND_K
        preds[test] = _blend(rows[test], k)
        cut_report[int(c)] = {"k": k, "players": int(test.sum())}
    methods = {
        "blend (held out)": preds,
        "season average": rows["season"],
        "last-3 average": rows["last3"],
        "Sleeper projection": rows["proj"],
    }
    mae = {m: _mae(p, rows) for m, p in methods.items()}
    by_pos = pd.DataFrame(
        {m: {pos: _mae(p[g.index], g) for pos, g in rows.groupby("position")} for m, p in methods.items()}
    )
    by_pos["players"] = rows.groupby("position").size()
    report = AccuracyReport(
        through=through,
        players=int(rows["player_id"].nunique()),
        k=_best_k(rows),
        mae=mae,
        by_position=by_pos,
        cutoffs=cut_report,
    )
    # Consensus: the same held-out blend with the forward rate adjusted by FantasyPros/ESPN where archived.
    next_proj: dict[int, pd.Series] = {}
    used_any: set[str] = set()
    for w in range(2, through + 1):
        st = projection_stats(season, w, final=True)
        factor, used = consensus_factor(scoring, season, w, st, table)
        if used:
            used_any.update(used)
            sp = proj[proj["week"] == w].set_index("player_id")["proj"]
            next_proj[w] = sp * factor.reindex(sp.index).fillna(1.0)
    if next_proj:
        rows_c = _cutoff_rows(act, proj, through, next_proj)
        mask = rows_c["cutoff"].map(lambda c: c + 1 in next_proj).astype(bool)
        rows_c = rows_c[mask].reset_index(drop=True)
        base = rows[rows["cutoff"].map(lambda c: c + 1 in next_proj).astype(bool)].reset_index(drop=True)
        if len(rows_c) and len(base):
            k_c = _best_k(rows_c)
            k_b = _best_k(base)
            report.consensus_note = (
                f"cutoffs {sorted(c - 1 for c in next_proj)} "
                f"(where {', '.join(sorted(used_any))} were archived): "
                f"blend MAE {_mae(_blend(base, k_b), base):.2f} with Sleeper alone vs "
                f"{_mae(_blend(rows_c, k_c), rows_c):.2f} with the consensus factor (in-sample k)"
            )
    return report


def fit_spread(
    scoring: dict, season: int, through: int, table: pd.DataFrame
) -> dict[str, tuple[float, float]]:
    """Per position (a, b) with sd = a + b * projection, from the weekly misses of Sleeper's projections."""
    weeks = list(range(1, through + 1))
    act = actuals(scoring, season, weeks)
    proj = projections(scoring, season, weeks, through)
    df = proj.merge(act, on=["player_id", "week"], how="inner")
    df = df[df["proj"] >= 1.0]
    df["position"] = df["player_id"].map(table["position"])
    out = dict(DEFAULT_SPREAD)
    for pos, g in df.groupby("position"):
        if len(g) < 40:
            continue
        y = (g["pts"] - g["proj"]).abs() * np.sqrt(np.pi / 2)
        b, a = np.polyfit(g["proj"], y, 1)
        out[pos] = (float(max(a, 0.5)), float(max(b, 0.0)))
    return out


# --- production ---------------------------------------------------------------------------------------------


@dataclass
class RosResult:
    weekly: pd.DataFrame
    summary: pd.DataFrame
    blend_k: float
    spread: dict[str, tuple[float, float]]
    consensus_sources: list[str]


def availability(status: str, week: int, first_week: int, ir_weeks_out: int) -> float:
    if status in OUT_LONG:
        return 0.0 if week < first_week + ir_weeks_out else 1.0
    if week == first_week:
        if status in OUT_ONE_WEEK:
            return 0.0
        if status == "Doubtful":
            return DOUBTFUL_FACTOR
    return 1.0


def build_ros(
    league: sl.League,
    table: pd.DataFrame,
    *,
    ir_weeks_out: int,
    playoff_weight: float,
    through: int | None = None,
) -> RosResult:
    scoring, season = league.scoring, league.season
    through = league.last_scored_week if through is None else through
    remaining = league.remaining_weeks
    finished = list(range(1, through + 1))
    act = actuals(scoring, season, finished)
    k = fit_blend_k(scoring, season, through)
    spread = fit_spread(scoring, season, through, table) if through >= 2 else dict(DEFAULT_SPREAD)
    proj = projections(scoring, season, remaining, through)
    playing = teams_playing(proj)
    first = remaining[0] if remaining else through + 1
    sources: list[str] = []
    if remaining:
        factor, sources = consensus_factor(
            scoring, season, first, projection_stats(season, first, final=False), table
        )
        is_first = proj["week"] == first
        proj.loc[is_first, "proj"] = proj.loc[is_first, "proj"] * proj.loc[is_first, "player_id"].map(
            factor
        ).fillna(1.0)
    rates = _rates_at(act, through)
    ids = sorted(set(proj["player_id"]) | set(rates.index) | {p for t in league.teams for p in t.players})
    ids = [i for i in ids if i in table.index]
    proj_w = proj.pivot_table(index="player_id", columns="week", values="proj", aggfunc="first")
    rows = []
    for pid in ids:
        meta = table.loc[pid]
        n = int(rates["n"].get(pid, 0))
        season_rate = float(rates["season"].get(pid, np.nan)) if n else np.nan
        a = n / (n + k) if n else 0.0
        nfl_team = meta["team"]
        for w in remaining:
            if not nfl_team or nfl_team not in playing.get(w, set()):
                pts = 0.0
            else:
                pw = proj_w[w].get(pid, np.nan) if w in proj_w.columns else np.nan
                if np.isnan(pw):
                    # Sleeper projects everyone with a role: a week it skips counts only for a player it
                    # projects in other weeks (his blended rate); a backup it never projects is 0.
                    row = proj_w.loc[pid] if pid in proj_w.index else None
                    fwd = float(row.mean()) if row is not None and row.notna().any() else np.nan
                    pts = 0.0 if np.isnan(fwd) else (a * season_rate + (1 - a) * fwd if n else fwd)
                else:
                    pts = a * season_rate + (1 - a) * pw if n else pw
                pts *= availability(meta["injury_status"], w, first, ir_weeks_out)
            pos = meta["position"]
            sa, sb = spread.get(pos, DEFAULT_SPREAD.get(pos, (3.0, 0.4)))
            rows.append(
                {
                    "player_id": pid,
                    "week": w,
                    "pts": round(float(max(pts, 0.0)), 2),
                    "sd": round(sa + sb * max(pts, 0.0), 2) if pts > 0 else 0.0,
                }
            )
    weekly = pd.DataFrame(rows, columns=["player_id", "week", "pts", "sd"])
    playoff = set(league.playoff_weeks)
    weekly["weight"] = weekly["week"].map(lambda w: playoff_weight if w in playoff else 1.0)
    g = weekly.assign(wpts=weekly["pts"] * weekly["weight"]).groupby("player_id")
    summary = table.loc[ids, ["name", "position", "team", "injury_status", "search_rank"]].copy()
    summary["games"] = rates["n"].reindex(summary.index).fillna(0).astype(int)
    summary["season_rate"] = rates["season"].reindex(summary.index).round(2)
    summary["ros_pts"] = g["pts"].sum().reindex(summary.index).fillna(0.0).round(1)
    summary["ros_weighted"] = g["wpts"].sum().reindex(summary.index).fillna(0.0).round(1)
    summary["playoff_pts"] = (
        weekly[weekly["week"].isin(playoff)]
        .groupby("player_id")["pts"]
        .sum()
        .reindex(summary.index)
        .fillna(0.0)
        .round(1)
    )
    summary["next_week"] = (
        weekly[weekly["week"] == first].set_index("player_id")["pts"].reindex(summary.index)
    )
    summary["games_left"] = (
        weekly[weekly["pts"] > 0].groupby("player_id").size().reindex(summary.index).fillna(0)
    )
    return RosResult(weekly=weekly, summary=summary, blend_k=k, spread=spread, consensus_sources=sources)
