"""How high a slate scored, from public nflverse data and the repo's real DK scoring.

A per-week index, relative to the season's mean, of how high the main slate scored.

**Context only.** It does not predict Sam's cash lines (see `docs/CONTESTS.md`: five weeks, r about 0.1, and
the actual Oct 4 standings confirm that week 4's ~106 line was real: GIANT $25 Double Up 106.0, GIANT $10
Double Up 106.1, SE $5 Double Up 108.2 -- cash lines track whether the chalk hit, which no public slate
measure sees). `toughness.py` therefore estimates week effects from the contests themselves and does not
divide by this; the index is kept as a reported column beside each week's fitted effect, and as the `index`
comparator in the leave-one-week-out comparison of ways to handle the week.

**Main slate** is the repo's existing definition (`sources/dk_salaries._is_sunday_afternoon`): Sunday games
kicking off 12:00-17:59 ET (the 1 pm and 4 pm windows; no Thursday, Sunday night, Monday or early
international game). The players are those whose team plays in one of them.

**Actual points** are `results_actual.score_offense_actual` / `score_dst_actual` -- the real DK points
(yardage bonuses, points-allowed tiers), the same numbers the Model Check tab scores against.

**Candidates** (`CANDIDATES`; each is a number per season-week, and is then divided by its mean over that
season's complete weeks):

- `top_n_lineup`: the *slot-weighted sum of the mean actual points of the top-N players at each position*
  (QB 12, RB 24, WR 36, TE 12, DST 12; a lineup is QB 1, RB 2, WR 3, TE 1, DST 1 and a FLEX that is the
  N-weighted mean of RB/WR/TE). What a lineup of solid starters scored on this slate.
- `p95_optimal`: the *95th percentile of 10,000 random lineups drawn from the top of each position* (QB 16, RB
  32, WR 48, TE 16, DST 16, uniformly, no salary cap -- nflverse has no salaries -- FLEX from the extra RB,
  WR and TE draws). The ceiling-side view, "optimal-ish" lineups. Seeded by (season, week): deterministic.
- `points_per_game` (my addition): the mean combined final score of the main-slate games. It needs no player
  data at all, so it is the robust baseline the two player-level candidates have to beat.

`fit_candidates` regresses Sam's real cash lines on each candidate (OLS on the relative value, leave-one-out
RMSE) and `select` takes the best. The result, and why the choice is made on five points with care, is in
`docs/CONTESTS.md`. `python -m dfs.contests.slate_index` rebuilds `slate_index_backfill.csv` (2021-2025).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import results_actual
from dfs.paths import REPO_ROOT

BACKFILL_FILE = REPO_ROOT / "src" / "dfs" / "contests" / "slate_index_backfill.csv"
BACKFILL_SEASONS = (2021, 2022, 2023, 2024, 2025)

MAIN_SLATE_HOURS = range(12, 18)
# A week with fewer Sunday-afternoon games than this has no real main slate (Christmas weekend 2022 had 2):
# it gets no index, so its contests are left out of the fit rather than scaled by a meaningless number.
MIN_MAIN_SLATE_GAMES = 6
TOP_N = {"QB": 12, "RB": 24, "WR": 36, "TE": 12, "DST": 12}
POOL_N = {"QB": 16, "RB": 32, "WR": 48, "TE": 16, "DST": 16}
LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
SIMULATED_LINEUPS = 10_000
P95 = 0.95
CANDIDATES = ("top_n_lineup", "p95_optimal", "points_per_game")

# Sam's typed Results `Cash Line` (the GIANT $25 Double Up), 2026 weeks 1-5: the targets the index is fit to.
KNOWN_CASH_LINES = {
    (2026, 1): 147.56,
    (2026, 2): 140.16,
    (2026, 3): 158.7,
    (2026, 4): 105.98,
    (2026, 5): 153.06,
}

# The candidate shown as context (and compared as `index`). Lowest leave-one-out error on KNOWN_CASH_LINES,
# which is to say none of them predicts: see docs/CONTESTS.md.
SELECTED = "top_n_lineup"

INDEX_COLUMNS = ["season", "week", "n_games", *CANDIDATES, *(f"rel_{c}" for c in CANDIDATES)]


def main_slate_games(games: pd.DataFrame, season: int) -> pd.DataFrame:
    """Regular-season Sunday 12:00-17:59 ET games of `season` from nflverse's `games.csv`
    (`week, away_team, home_team, away_score, home_score`, filtered)."""
    g = games[(games["season"] == season) & (games["game_type"] == "REG")].copy()
    hour = pd.to_numeric(g["gametime"].astype(str).str.slice(0, 2), errors="coerce")
    sunday = g["weekday"].astype(str).str.casefold() == "sunday"
    return g[sunday & hour.isin(list(MAIN_SLATE_HOURS))].reset_index(drop=True)


def _slot_weighted_top_n(by_pos: dict[str, np.ndarray]) -> float:
    means = {
        pos: float(np.mean(np.sort(by_pos[pos])[::-1][:n])) for pos, n in TOP_N.items() if len(by_pos[pos])
    }
    if set(means) != set(TOP_N):
        return math.nan
    flex_weights = {pos: TOP_N[pos] for pos in ("RB", "WR", "TE")}
    flex = sum(means[pos] * w for pos, w in flex_weights.items()) / sum(flex_weights.values())
    return sum(LINEUP_SLOTS[pos] * means[pos] for pos in LINEUP_SLOTS) + flex


def _p95_optimal(by_pos: dict[str, np.ndarray], rng: np.random.Generator) -> float:
    pools = {pos: np.sort(by_pos[pos])[::-1][:n] for pos, n in POOL_N.items()}
    if any(len(pools[pos]) < LINEUP_SLOTS[pos] + (1 if pos in ("RB", "WR", "TE") else 0) for pos in pools):
        return math.nan
    d = SIMULATED_LINEUPS

    def draw(pos: str, k: int) -> np.ndarray:
        pool = pools[pos]
        order = np.argsort(rng.random((d, len(pool))), axis=1)[:, :k]
        return pool[order]

    qb, dst = draw("QB", 1), draw("DST", 1)
    rb, wr, te = draw("RB", 3), draw("WR", 4), draw("TE", 2)
    extras = np.stack([rb[:, 2], wr[:, 3], te[:, 1]], axis=1)  # the extra RB, WR and TE draw: FLEX picks one
    flex = extras[np.arange(d), rng.integers(0, 3, size=d)]
    total = qb[:, 0] + dst[:, 0] + rb[:, :2].sum(axis=1) + wr[:, :3].sum(axis=1) + te[:, 0] + flex
    return float(np.quantile(total, P95))


def weekly_candidates(
    stats_player: pd.DataFrame, stats_team: pd.DataFrame, games: pd.DataFrame, season: int
) -> pd.DataFrame:
    """One row per *complete* regular-season week of `season` (every main-slate game has a final score, there
    are at least `MIN_MAIN_SLATE_GAMES` of them, and the week has player stats): `season, week, n_games` and
    the raw value of each candidate."""
    slate = main_slate_games(games, season)
    season_games = games[(games["season"] == season) & (games["game_type"] == "REG")]
    offense = results_actual.score_offense_actual(stats_player)
    defense = results_actual.score_dst_actual(stats_team, stats_player, season_games)
    scored = pd.concat(
        [
            offense[["Team", "Position", "week", "dk_actual"]],
            defense[["Team", "Position", "week", "dk_actual"]],
        ],
        ignore_index=True,
    )
    scored["week"] = pd.to_numeric(scored["week"], errors="coerce")
    rows = []
    for week, wk in slate.groupby("week"):
        if len(wk) < MIN_MAIN_SLATE_GAMES or wk["home_score"].isna().any() or wk["away_score"].isna().any():
            continue
        teams = set(wk["home_team"]) | set(wk["away_team"])
        pool = scored[(scored["week"] == week) & scored["Team"].isin(teams) & scored["dk_actual"].notna()]
        by_pos = {pos: pool.loc[pool["Position"] == pos, "dk_actual"].to_numpy(dtype=float) for pos in TOP_N}
        if not len(pool):
            continue
        rng = np.random.default_rng(season * 100 + int(week))
        rows.append(
            {
                "season": season,
                "week": int(week),
                "n_games": len(wk),
                "top_n_lineup": _slot_weighted_top_n(by_pos),
                "p95_optimal": _p95_optimal(by_pos, rng),
                "points_per_game": float((wk["home_score"] + wk["away_score"]).mean()),
            }
        )
    return pd.DataFrame(rows, columns=["season", "week", "n_games", *CANDIDATES])


def with_relative(candidates: pd.DataFrame) -> pd.DataFrame:
    """Adds `rel_<candidate>`: each value divided by its mean over the same season's weeks in the frame.
    (For the current season that mean is over the weeks played so far, so it moves as the season goes.)"""
    out = candidates.copy()
    for c in CANDIDATES:
        out[f"rel_{c}"] = out[c] / out.groupby("season")[c].transform("mean")
    return out[INDEX_COLUMNS]


def slate_index(table: pd.DataFrame, candidate: str = SELECTED) -> pd.DataFrame:
    """`season, week, index`: the selected candidate's relative value, ~1.0 on an average slate."""
    return table[["season", "week", f"rel_{candidate}"]].rename(columns={f"rel_{candidate}": "index"})


@dataclass(frozen=True)
class CandidateFit:
    candidate: str
    n: int
    intercept: float
    slope: float
    r: float
    r2: float
    spearman: float
    loo_rmse: float
    # leave-one-out RMSE of predicting every week with the mean of the others: what "no index" costs
    baseline_loo_rmse: float
    # the proportional view: cash line / mean cash line against the relative index (1.0 = faithful scale)
    rel_slope: float


def fit_candidates(
    table: pd.DataFrame, cash_lines: dict[tuple[int, int], float] | None = None
) -> pd.DataFrame:
    """One row per candidate: how well its relative value predicts the cash lines. With five points the
    summary is deliberately plain: an OLS line, its r / R^2 / Spearman, the leave-one-out RMSE (the
    honest number: a line through 5 points always looks good in-sample) and `rel_slope`, the slope of
    cash-line-over-its-mean on the relative index (1.0 means the index is a faithful scale for scores)."""
    cash = cash_lines or KNOWN_CASH_LINES
    joined = table.assign(
        cash=[cash.get((int(s), int(w))) for s, w in zip(table["season"], table["week"], strict=True)]
    ).dropna(subset=["cash"])
    rows = []
    y = joined["cash"].to_numpy(dtype=float)
    for c in CANDIDATES:
        x = joined[f"rel_{c}"].to_numpy(dtype=float)
        ok = ~np.isnan(x)
        if ok.sum() < 3:
            continue
        xs, ys = x[ok], y[ok]
        slope, intercept = np.polyfit(xs, ys, 1)
        pred = intercept + slope * xs
        ss_res, ss_tot = float(((ys - pred) ** 2).sum()), float(((ys - ys.mean()) ** 2).sum())
        r = float(np.corrcoef(xs, ys)[0, 1])
        loo = []
        for i in range(len(xs)):
            keep = np.arange(len(xs)) != i
            b, a = np.polyfit(xs[keep], ys[keep], 1)
            loo.append(ys[i] - (a + b * xs[i]))
        rel_slope = float(np.polyfit(xs, ys / ys.mean(), 1)[0])
        baseline = [ys[i] - np.delete(ys, i).mean() for i in range(len(ys))]
        rows.append(
            CandidateFit(
                c,
                int(ok.sum()),
                float(intercept),
                float(slope),
                r,
                1 - ss_res / ss_tot if ss_tot else math.nan,
                float(pd.Series(xs).corr(pd.Series(ys), method="spearman")),
                float(np.sqrt(np.mean(np.square(loo)))),
                float(np.sqrt(np.mean(np.square(baseline)))),
                rel_slope,
            ).__dict__
        )
    return pd.DataFrame(rows)


def select(fits: pd.DataFrame) -> str:
    """The candidate with the lowest leave-one-out RMSE (ties: higher R^2)."""
    ranked = fits.sort_values(["loo_rmse", "r2"], ascending=[True, False])
    return str(ranked.iloc[0]["candidate"])


# ---- the thin disk / network layer ---------------------------------------------------------------


def read_index_file(path: Path) -> pd.DataFrame:
    """A caller-supplied `season, week, index` CSV, validated: a missing column or a non-numeric value is a
    `ValueError` that names the file, not a traceback later."""
    table = pd.read_csv(path)
    missing = [c for c in ("season", "week", "index") if c not in table.columns]
    if missing:
        raise ValueError(
            f"{path}: the slate index file needs column(s) {missing} (has {list(table.columns)})"
        )
    for column in ("season", "week", "index"):
        table[column] = pd.to_numeric(table[column], errors="coerce")
    if table[["season", "week", "index"]].isna().any().any():
        raise ValueError(f"{path}: season, week and index must all be numbers")
    return table[["season", "week", "index"]].astype({"season": int, "week": int})


def load_backfill() -> pd.DataFrame:
    """The committed 2021-2025 table (empty frame if it has not been built)."""
    if not BACKFILL_FILE.exists():
        return pd.DataFrame(columns=INDEX_COLUMNS)
    return pd.read_csv(BACKFILL_FILE)


def fetch_schedule() -> pd.DataFrame:
    """nflverse `games.csv` with kickoff day and time (the existing loaders keep only scores)."""
    from dfs.sources.nflverse_games import GAMES_CSV_URL
    from dfs.sources.nflverse_results import _download

    return pd.read_csv(io.BytesIO(_download(GAMES_CSV_URL, "games.csv")))


def compute_season(season: int, games: pd.DataFrame | None = None) -> pd.DataFrame:
    """Candidates for one season from the existing nflverse loaders (network). Raises
    `nflverse_results.ResultsFetchError` when a file is not published."""
    from dfs.sources import nflverse_results

    games = fetch_schedule() if games is None else games
    return weekly_candidates(
        nflverse_results.fetch_stats_player(season),
        nflverse_results.fetch_stats_team(season),
        games,
        season,
    )


def build_table(seasons, games: pd.DataFrame | None = None) -> pd.DataFrame:
    games = fetch_schedule() if games is None else games
    parts = [compute_season(s, games) for s in seasons]
    return with_relative(pd.concat(parts, ignore_index=True))


def read_local(directory: Path, seasons) -> pd.DataFrame:
    """Same as `build_table` from files already on disk (`sp_<season>.parquet`, `st_<season>.parquet`,
    `games.csv`): for building the backfill where only some hosts are reachable."""
    games = pd.read_csv(directory / "games.csv")
    parts = [
        weekly_candidates(
            pd.read_parquet(directory / f"sp_{s}.parquet"),
            pd.read_parquet(directory / f"st_{s}.parquet"),
            games,
            s,
        )
        for s in seasons
    ]
    return with_relative(pd.concat(parts, ignore_index=True))


if __name__ == "__main__":  # pragma: no cover - rebuilds the committed backfill
    built = build_table(BACKFILL_SEASONS)
    built.round(6).to_csv(BACKFILL_FILE, index=False)
    print(f"wrote {BACKFILL_FILE} ({len(built)} weeks)")
