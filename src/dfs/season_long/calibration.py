"""The spread of a team's week, measured on the leagues' real scores.

The simulations draw each team's week as a normal around its lineup's expected points, with a spread built
from the starters' own spreads (`ros.fit_spread`, players independent). This module checks that against what
happened: for every team-week so far, the Sleeper projection of the starters actually set, the spread the
model gives that lineup, and the real score. `spread_scale` is the standard deviation of
`(real - projected) / modelled spread` over every team-week of every league (about 1 when the spread is
right, under 1 when the model is too wide); the simulations multiply their team spread by it. The mean ratio
(real over projected) is reported beside it and is not applied: it moves every team alike.

Cached under `data/season/outlook/` and refitted when a new week has been scored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.paths import DATA_DIR
from dfs.season_long import ros
from dfs.season_long import sleeper_league as sl

CACHE_DIR = DATA_DIR / "season" / "outlook"
SCALE_FILE = "spread_scale.json"
SCALE_CLIP = (0.7, 1.5)
MIN_TEAM_WEEKS = 60
MISS_COLUMNS = ["league", "week", "roster_id", "actual", "mean", "sd"]


@dataclass
class Scale:
    scale: float
    mean_ratio: float
    team_weeks: int
    through: int
    per_league: dict[str, float]

    def line(self) -> str:
        parts = ", ".join(f"{n} {s:.2f}x" for n, s in self.per_league.items())
        return (
            f"team spread {self.scale:.2f}x the modelled one over {self.team_weeks} team-weeks "
            f"(Weeks 1-{self.through}; {parts}); real scores ran {self.mean_ratio:.0%} of the starters' "
            f"projections"
        )


def misses(league: sl.League, table: pd.DataFrame, through: int | None = None) -> pd.DataFrame:
    """[league, week, roster_id, actual, mean, sd] for each team-week through `through`: the real score, the
    summed Sleeper projection of the starters set, and the modelled spread of that lineup."""
    through = league.last_scored_week if through is None else through
    if through < 1:
        return pd.DataFrame(columns=MISS_COLUMNS)
    weeks = list(range(1, through + 1))
    spread = ros.fit_spread(league.scoring, league.season, through, table) if through >= 2 else {}
    spread = {**ros.DEFAULT_SPREAD, **spread}
    proj = ros.projections(league.scoring, league.season, weeks, league.last_scored_week)
    by_week = {w: dict(zip(g["player_id"], g["proj"], strict=True)) for w, g in proj.groupby("week")}
    position = table["position"]
    rows = []
    for w in weeks:
        p = by_week.get(w, {})
        for m in sl.fetch_matchups(league.league_id, w, final=True):
            starters = [str(s) for s in (m.get("starters") or []) if s and str(s) != "0"]
            if not starters or m.get("points") is None:
                continue
            mean = var = 0.0
            for pid in starters:
                x = float(p.get(pid, 0.0))
                a, b = spread.get(str(position.get(pid, "")), (3.0, 0.4))
                mean += x
                var += (a + b * x) ** 2 if x > 0 else 0.0
            rows.append((league.name, w, int(m["roster_id"]), float(m["points"]), mean, float(np.sqrt(var))))
    return pd.DataFrame(rows, columns=MISS_COLUMNS)


def fit_scale(df: pd.DataFrame, through: int | None = None) -> Scale:
    """The spread scale from `misses` rows, only the weeks up to `through` when given."""
    d = df if through is None else df[df["week"] <= through]
    d = d[(d["sd"] > 0) & (d["mean"] > 0)]
    last = int(d["week"].max()) if len(d) else 0
    if len(d) < MIN_TEAM_WEEKS:
        return Scale(1.0, 1.0, len(d), last, {})
    z = (d["actual"] - d["mean"]) / d["sd"]
    per = {n: float(((g["actual"] - g["mean"]) / g["sd"]).std(ddof=1)) for n, g in d.groupby("league")}
    return Scale(
        scale=float(np.clip(z.std(ddof=1), *SCALE_CLIP)),
        mean_ratio=float(d["actual"].sum() / d["mean"].sum()),
        team_weeks=len(d),
        through=last,
        per_league={k: round(v, 2) for k, v in per.items()},
    )


def all_misses(cfg, table: pd.DataFrame) -> pd.DataFrame:
    """`misses` for every Sleeper league in season.toml, the Sleeper calls cached like every other."""
    frames = []
    for lc in cfg.leagues:
        if lc.platform != "sleeper":
            continue
        league = sl.load_league(lc.name, lc.league_id, lc.username)
        frames.append(misses(league, table))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=MISS_COLUMNS)


def cached_scale(cfg, table: pd.DataFrame, last_scored: int) -> Scale:
    """The pooled scale, refitted when the leagues have scored a new week; 1.0 if it cannot be measured."""
    path = CACHE_DIR / SCALE_FILE
    if path.exists():
        try:
            saved = json.loads(path.read_text())
            if saved.get("through") == last_scored:
                return Scale(**saved)
        except (ValueError, TypeError):
            pass
    try:
        df = all_misses(cfg, table)
    except sl.SleeperError:
        return Scale(1.0, 1.0, 0, last_scored, {})
    out = fit_scale(df)
    out.through = last_scored
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out.__dict__))
    return out
