"""Matchups by position: which offenses have the softest week at QB, RB, WR and TE, and which defenses
have the best at DST.

Pure and offline-testable. The inputs are the season's actual DK points by player-week
(`results_actual.score_offense_actual`) and by defense-week (`score_dst_actual`), the schedule, the
team metrics (`team_metrics`: Pace, PROE, DefEPA) and this week's implied totals.

**Adjusted points allowed** ("allowed above expectation"). For a defense and a position: the DK points its
opponents scored at that position, MINUS what each of those same offenses scored at that position in its
OTHER games (against everyone else), averaged over the defense's games. An offense with only that one
game falls back to the league's average at the position. DST is the mirror: for each offense, the DST
points the defenses scored against it minus what those same defenses scored in their other games.

Current-season results are blended with last season's through `team_metrics.blend_with_prior` and
`PBP_PRIOR_WEIGHT_GAMES`, the same early-season blend the pbp metrics use. Only games BEFORE the slate's
week count (no lookahead).

**Matchup score** per (this week's offense, position): the weighted mean (`MATCHUP_WEIGHTS`, equal to
start) of z-scores, across the teams on the slate, of: the opponent's adjusted points allowed to the
position; the opponent's EPA allowed (pass for QB/WR/TE, rush for RB); the team's implied total; pace
(faster is better); PROE (+ for QB/WR/TE, - for RB). A missing input drops out and the weights
renormalise. **DST** (the defense, facing offense X): X's adjusted DST points allowed, plus X's implied
total inverted.

The output keeps the top `MATCHUP_TOP_N` and bottom `MATCHUP_BOTTOM_N` per position, each with its two
biggest reasons in words and the team's top two players at the position.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.player_join import normalize_team
from dfs.team_metrics import PBP_PRIOR_WEIGHT_GAMES, blend_with_prior

OFFENSE_POSITIONS = ["QB", "RB", "WR", "TE"]
MATCHUP_POSITIONS = [*OFFENSE_POSITIONS, "DST"]
MATCHUP_WEIGHTS = {"allowed": 1.0, "epa": 1.0, "implied": 1.0, "pace": 1.0, "proe": 1.0}
MATCHUP_TOP_N = 8
MATCHUP_BOTTOM_N = 4
MATCHUP_PLAYERS_PER_TEAM = 2
MIN_SLATE_TEAMS = 3  # a z-score over fewer teams means nothing
PROE_SIGN = {"QB": 1.0, "WR": 1.0, "TE": 1.0, "RB": -1.0}
EPA_COLUMN = {"QB": "DefEPA/Pass", "WR": "DefEPA/Pass", "TE": "DefEPA/Pass", "RB": "DefEPA/Rush"}
FEATURES_DST = ("allowed", "implied")

GROUP_TOP, GROUP_BOTTOM = "top", "bottom"
MATCHUP_COLUMNS = [
    "Position",
    "Team",
    "Opp",
    "Score",
    "Rank",
    "Group",
    "Reasons",
    "Players",
    "z_allowed",
    "z_epa",
    "z_implied",
    "z_pace",
    "z_proe",
]


def _schedule_long(schedule: pd.DataFrame) -> pd.DataFrame:
    """`week`, `team`, `opp` rows, two per game (team codes in DK's spelling)."""
    games = schedule.copy()
    games["week"] = pd.to_numeric(games["week"], errors="coerce")
    home = pd.DataFrame({"week": games["week"], "team": games["home_team"], "opp": games["away_team"]})
    away = pd.DataFrame({"week": games["week"], "team": games["away_team"], "opp": games["home_team"]})
    out = pd.concat([home, away], ignore_index=True)
    out["team"] = out["team"].map(normalize_team)
    out["opp"] = out["opp"].map(normalize_team)
    return out.dropna(subset=["week"])


def team_position_points(offense_actual: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """DK points each offense scored at each position each week it PLAYED (a position with no stat line
    that week is 0): `week`, `team`, `opp`, `Position`, `pts`."""
    played = _schedule_long(schedule)
    actual = offense_actual.assign(team=offense_actual["Team"].map(normalize_team))
    totals = actual.groupby(["team", "week", "Position"], as_index=False)["dk_actual"].sum()
    grid = played.merge(pd.DataFrame({"Position": OFFENSE_POSITIONS}), how="cross")
    out = grid.merge(totals, on=["team", "week", "Position"], how="left")
    out["pts"] = out["dk_actual"].fillna(0.0)
    return out.drop(columns="dk_actual")


def _adjusted(rows: pd.DataFrame, by: str, other: str, value: str, *, before_week: float) -> pd.DataFrame:
    """For rows of (`other` produced `value`, against `by`): value minus `other`'s mean in its OTHER games
    (league mean at that position when it has none), averaged per (`by`, Position). Needs `week`,
    `Position`. Returns `by`, `Position`, `adj`, `games`."""
    df = rows[rows["week"] < before_week].copy()
    if df.empty:
        return pd.DataFrame(columns=[by, "Position", "adj", "games"])
    grouped = df.groupby([other, "Position"])[value]
    total, count = grouped.transform("sum"), grouped.transform("count")
    others_mean = (total - df[value]) / (count - 1).where(count > 1)
    league = df.groupby("Position")[value].transform("mean")
    df["adj_row"] = df[value] - others_mean.fillna(league)
    out = df.groupby([by, "Position"], as_index=False).agg(adj=("adj_row", "mean"), games=("week", "nunique"))
    return out


def adjusted_points_allowed(team_points: pd.DataFrame, *, before_week: float) -> pd.DataFrame:
    """`defense`, `Position`, `adj`, `games`: DK points allowed above expectation, per defense and
    position (QB/RB/WR/TE), over weeks before `before_week`."""
    rows = team_points.rename(columns={"team": "offense", "opp": "defense"})
    out = _adjusted(rows, "defense", "offense", "pts", before_week=before_week)
    return out.rename(columns={"defense": "Team"})


def adjusted_dst_allowed(
    dst_actual: pd.DataFrame, schedule: pd.DataFrame, *, before_week: float
) -> pd.DataFrame:
    """`Team` (the OFFENSE), `adj`, `games`: DST points the defenses scored against each offense, above
    what those defenses scored in their other games, per game."""
    long = _schedule_long(schedule).rename(columns={"team": "defense", "opp": "offense"})
    dst = dst_actual.assign(defense=dst_actual["Team"].map(normalize_team))[["defense", "week", "dk_actual"]]
    rows = long.merge(dst, on=["defense", "week"], how="inner").assign(Position="DST")
    out = _adjusted(
        rows.rename(columns={"dk_actual": "pts"}), "offense", "defense", "pts", before_week=before_week
    )
    return out.drop(columns="Position").rename(columns={"offense": "Team"})


def blend_allowed(
    current: pd.DataFrame,
    prior: pd.DataFrame | None,
    key: list[str],
    *,
    prior_weight: float = PBP_PRIOR_WEIGHT_GAMES,
) -> pd.DataFrame:
    """Blend this season's adjusted allowed (`adj`, `games`) with last season's through `blend_with_prior`."""
    if prior is None or prior.empty:
        return current
    cur = current.set_index(key)
    pri = prior.set_index(key)
    blended = blend_with_prior(cur["adj"], pri["adj"], cur["games"], prior_weight, decimals=3)
    games = cur["games"].reindex(blended.index).fillna(0)
    return pd.DataFrame({"adj": blended, "games": games}).reset_index()


def _z(values: pd.Series) -> pd.Series:
    values = pd.to_numeric(values, errors="coerce")
    if values.notna().sum() < MIN_SLATE_TEAMS or values.std(ddof=0) == 0:
        return pd.Series(np.where(values.notna(), 0.0, np.nan), index=values.index)
    return (values - values.mean()) / values.std(ddof=0)


def _weighted_mean(z: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    names = [w for w in weights if f"z_{w}" in z.columns]
    cols = z[[f"z_{n}" for n in names]]
    present = cols.notna()
    w = pd.Series({f"z_{n}": weights[n] for n in names})
    numerator = cols.fillna(0).mul(w, axis=1).sum(axis=1)
    denominator = present.mul(w, axis=1).sum(axis=1)
    return numerator / denominator.replace(0, np.nan)


def week_pairs(schedule: pd.DataFrame, week: int, teams: set[str]) -> pd.DataFrame:
    """`Team`, `Opp` for every team in `teams` that plays in `week` (a bye week is not listed)."""
    long = _schedule_long(schedule)
    long = long[(long["week"] == week) & long["team"].isin(teams)]
    return long.rename(columns={"team": "Team", "opp": "Opp"})[["Team", "Opp"]].reset_index(drop=True)


def matchup_scores(
    pairs: pd.DataFrame,
    allowed: pd.DataFrame,
    dst_allowed: pd.DataFrame,
    team_metrics: pd.DataFrame | None,
    implied: pd.Series,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Score every (team, position) on the slate. `pairs` is `week_pairs`; `allowed` is
    `adjusted_points_allowed` (blended); `dst_allowed` is `adjusted_dst_allowed` (blended); `team_metrics`
    has `Team`, `Pace`, `PROE`, `DefEPA/Pass`, `DefEPA/Rush` (or None); `implied` is implied points
    indexed by team. Returns `MATCHUP_COLUMNS` less `Players` (see `attach_players`), sorted by position
    then score, best first."""
    weights = weights or MATCHUP_WEIGHTS
    metrics = (
        team_metrics.assign(Team=team_metrics["Team"].map(normalize_team)).set_index("Team")
        if (team_metrics is not None and not team_metrics.empty)
        else pd.DataFrame()
    )
    allowed_lookup = (
        allowed.set_index(["Team", "Position"])["adj"] if not allowed.empty else pd.Series(dtype=float)
    )
    dst_lookup = dst_allowed.set_index("Team")["adj"] if not dst_allowed.empty else pd.Series(dtype=float)
    pieces = []
    for position in MATCHUP_POSITIONS:
        part = pairs.copy()
        part["Position"] = position
        if position == "DST":
            part["allowed"] = part["Opp"].map(dst_lookup)
            part["implied"] = -part["Opp"].map(implied)
            z = pd.DataFrame({f"z_{f}": _z(part[f]) for f in FEATURES_DST}, index=part.index)
            raw = part[list(FEATURES_DST)].rename(columns=lambda c: f"raw_{c}")
        else:
            part["allowed"] = [allowed_lookup.get((opp, position), np.nan) for opp in part["Opp"]]
            epa_col = EPA_COLUMN[position]
            part["epa"] = part["Opp"].map(metrics[epa_col]) if epa_col in metrics.columns else np.nan
            part["implied"] = part["Team"].map(implied)
            part["pace"] = -part["Team"].map(metrics["Pace"]) if "Pace" in metrics.columns else np.nan
            part["proe"] = PROE_SIGN[position] * (
                part["Team"].map(metrics["PROE"]) if "PROE" in metrics.columns else np.nan
            )
            features = ["allowed", "epa", "implied", "pace", "proe"]
            z = pd.DataFrame({f"z_{f}": _z(part[f]) for f in features}, index=part.index)
            raw = part[features].rename(columns=lambda c: f"raw_{c}")
        part = pd.concat([part[["Position", "Team", "Opp"]], z, raw], axis=1)
        part["Score"] = _weighted_mean(z, weights)
        pieces.append(part)
    out = pd.concat(pieces, ignore_index=True)
    out["Rank"] = out.groupby("Position")["Score"].rank(ascending=False, method="first")
    return out.sort_values(["Position", "Rank"]).reset_index(drop=True)


def _reason_text(position: str, feature: str, raw: float, opp: str) -> str:
    """One feature's contribution in words, with the raw number."""
    if position == "DST":
        if feature == "allowed":
            return f"{opp} offense gives up {raw:+.1f} DST pts/g vs average"
        return f"{opp} implied total {-raw:.1f}"
    if feature == "allowed":
        return f"{opp} allow {raw:+.1f} pts/g to {position} above average"
    if feature == "epa":
        kind = "rush" if position == "RB" else "pass"
        return f"{opp} defense {raw:+.2f} EPA/{kind} allowed"
    if feature == "implied":
        return f"implied total {raw:.1f}"
    if feature == "pace":
        return f"pace {-raw:.1f}s/play"
    return f"PROE {raw * PROE_SIGN[position]:+.1f}"


def top_reasons(row: pd.Series, *, bottom: bool, k: int = 2) -> str:
    """The `k` features pulling hardest in the row's direction (highest z for a top matchup, lowest for a
    bottom one), in words."""
    position, opp = row["Position"], row["Opp"]
    features = FEATURES_DST if position == "DST" else ("allowed", "epa", "implied", "pace", "proe")
    z = {f: row.get(f"z_{f}") for f in features if pd.notna(row.get(f"z_{f}"))}
    ordered = sorted(z, key=lambda f: z[f], reverse=not bottom)[:k]
    return "; ".join(
        _reason_text(position, f, row[f"raw_{f}"], opp) for f in ordered if pd.notna(row.get(f"raw_{f}"))
    )


def select_groups(
    scores: pd.DataFrame, *, top: int = MATCHUP_TOP_N, bottom: int = MATCHUP_BOTTOM_N
) -> pd.DataFrame:
    """Per position, the best `top` and worst `bottom` rows (`Group`, `Reasons` filled), in order; rows with
    no score are left out. A position with fewer teams than `top + bottom` never lists a team twice."""
    pieces = []
    for _position, part in scores.dropna(subset=["Score"]).groupby("Position", sort=False):
        part = part.sort_values("Score", ascending=False)
        n = len(part)
        top_rows = part.head(top).assign(Group=GROUP_TOP)
        bottom_rows = part.tail(min(bottom, max(n - top, 0))).assign(Group=GROUP_BOTTOM)
        for group in (top_rows, bottom_rows):
            group = group.copy()
            group["Reasons"] = [
                top_reasons(r, bottom=(r["Group"] == GROUP_BOTTOM)) for _, r in group.iterrows()
            ]
            pieces.append(group)
    if not pieces:
        return pd.DataFrame(columns=[*MATCHUP_COLUMNS])
    out = pd.concat(pieces, ignore_index=True)
    out["Players"] = ""
    return out[[c for c in MATCHUP_COLUMNS if c in out.columns]]


def attach_players(
    groups: pd.DataFrame,
    players: pd.DataFrame,
    *,
    score_column: str = "CalPts",
    per_team: int = MATCHUP_PLAYERS_PER_TEAM,
) -> pd.DataFrame:
    """Fill `Players`: the team's top `per_team` players at the position by `score_column` (falling back to
    `ProjPts`), as "Name $salary". DST rows name the defense itself. `players` needs `Name`, `Team`,
    `Position`, `Salary` and the score column."""
    if groups.empty:
        return groups
    column = (
        score_column if score_column in players.columns and players[score_column].notna().any() else "ProjPts"
    )
    roster = players.assign(_team=players["Team"].map(normalize_team))
    out = groups.copy()
    labels = []
    for _, row in out.iterrows():
        part = roster[(roster["_team"] == row["Team"]) & (roster["Position"] == row["Position"])]
        part = part.sort_values(column, ascending=False).head(per_team)
        labels.append(", ".join(f"{r['Name']} ${int(r['Salary']):,}" for _, r in part.iterrows()))
    out["Players"] = labels
    return out
