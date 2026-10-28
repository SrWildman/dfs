"""Our own DST projection (`OurDST`, shadow): DK defense points from the game's Vegas context and both units'
recent rates, built on public nflverse history only.

TFFB is weakest at DST (within-week rank correlation about 0.2 this season) and UM's DST row is a gradient
booster on raw trailing counts. This model is narrower and more structured: the defense's DK points are the
sum of their parts, and each part is driven by what should drive it.

- **Sacks** come from the opposing passer's dropbacks: the defense's own sack rate per dropback faced, the
  opposing offense's sack rate allowed, and the opposing starting QB's own sack rate.
- **Takeaways** (interceptions + fumble recoveries) likewise: the defense's takeaway rate per play faced, the
  opposing offense's giveaway rate, the opposing QB's own interception rate.
- **Points allowed** start at the opponent's implied team total; the tier score is integrated over the
  measured spread of real points around it.
- **Return TDs, safeties and blocks** are rare and close to random: a small mean that rises with takeaways.

Every rate is a recency-weighted mean over GAMES (a half-life in games, carried across seasons with an extra
discount at the season boundary, since rosters turn over), shrunk toward the league rate measured on the fit
seasons. A row only ever sees games strictly before its own (`merge_asof`, `allow_exact_matches=False`): the
same construction as `dfs.model.features`, so a training row and a live row are built by one code path.

The Vegas context is the nflverse schedule's line (the same one UM reads); weather is the schedule's roof.

Pure functions over frames. The study that fits it 2014-2022, judges 2023-2025 held out and 2026 against TFFB
and CalPts is `dfs.model.dst_study`. Nothing here feeds CalPts or the builds.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.dk_scoring import DK_DST_SCORING, DST_POINTS_ALLOWED_TIERS
from dfs.model import data
from dfs.model.history import team_game_context, time_key
from dfs.results_actual import dst_actual_fields

# -----------------------------------------------------------------------------------------------------
# The team-game table
# -----------------------------------------------------------------------------------------------------

# One row per completed team-game, from the DEFENSE's side. The `opp_*` columns are the opposing OFFENSE's
# volume and mistakes in that same game (what the defense faced).
DST_GAME_COLUMNS = [
    "team",
    "opp",
    "season",
    "week",
    "t",
    "game_id",
    "dk",
    "sacks",
    "qb_hits",
    "ints",
    "fum_rec",
    "takeaways",
    "def_td",
    "misc",
    "points_allowed",
    "opp_dropbacks",
    "opp_attempts",
    "opp_plays",
    "opp_giveaways",
    "opp_qb_id",
    "implied",
    "spread",
    "total",
    "home",
    "dome",
]

DOME_ROOFS = ("dome", "closed")


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _starting_qbs(games: pd.DataFrame) -> pd.DataFrame:
    """`game_id`, `team`, `qb_id` (that team's starting QB per the schedule) and `dome` (1 under a fixed or
    closed roof)."""
    g = games[games["game_type"] == "REG"]
    dome = g["roof"].isin(DOME_ROOFS).astype(int)
    home = pd.DataFrame(
        {"game_id": g["game_id"], "team": g["home_team"], "qb_id": g["home_qb_id"], "dome": dome}
    )
    away = pd.DataFrame(
        {"game_id": g["game_id"], "team": g["away_team"], "qb_id": g["away_qb_id"], "dome": dome}
    )
    out = pd.concat([home, away], ignore_index=True)
    out["team"] = out["team"].map(data.normalize_team)
    return out


def build_dst_games(
    stats_team: pd.DataFrame, stats_player: pd.DataFrame, games: pd.DataFrame
) -> pd.DataFrame:
    """`DST_GAME_COLUMNS` for every completed regular-season team-game in `stats_team`. DK points and points
    allowed come from the results loop's scorer (`results_actual.dst_actual_fields`, the number Model Check
    scores against), per season since that scorer keys on week alone."""
    st = stats_team[stats_team["season_type"] == "REG"].copy()
    st["season"] = st["season"].astype(int)
    st["week"] = st["week"].astype(int)
    for col in ("team", "opponent_team"):
        st[col] = st[col].map(data.normalize_team)
    sp = stats_player[stats_player["player_id"].notna()].copy()
    sp["season"] = sp["season"].astype(int)
    sp["team"] = sp["team"].map(data.normalize_team)
    scores = games[games["game_type"] == "REG"].copy()
    for col in ("home_team", "away_team"):
        scores[col] = scores[col].map(data.normalize_team)

    fields = []
    for season, chunk in st.groupby("season"):
        f = dst_actual_fields(chunk, sp[sp["season"] == season], scores[scores["season"] == season])
        f["season"] = season
        fields.append(f.rename(columns={"Team": "team"}))
    scored = pd.concat(fields, ignore_index=True)
    scored["dk"] = _dk_points(scored)

    # The offense's own volume and mistakes, keyed so the defense row can read its opponent's.
    offense = pd.DataFrame(
        {
            "opp": st["team"],
            "season": st["season"],
            "week": st["week"],
            "opp_attempts": _num(st, "attempts"),
            "opp_dropbacks": _num(st, "attempts") + _num(st, "sacks_suffered"),
            "opp_plays": _num(st, "attempts") + _num(st, "sacks_suffered") + _num(st, "carries"),
            "opp_giveaways": _num(st, "passing_interceptions")
            + _num(st, "sack_fumbles_lost")
            + _num(st, "rushing_fumbles_lost")
            + _num(st, "receiving_fumbles_lost"),
        }
    )
    base = st[["team", "opponent_team", "season", "week", "game_id", "def_qb_hits"]].rename(
        columns={"opponent_team": "opp", "def_qb_hits": "qb_hits"}
    )
    out = base.merge(
        scored[["team", "season", "week", "dk", *DK_DST_SCORING, "two_pt_return", "points_allowed"]],
        on=["team", "season", "week"],
        how="left",
    )
    out = out.merge(offense, on=["opp", "season", "week"], how="left")
    out = out.merge(team_game_context(games), on=["game_id", "team"], how="left")
    qbs = _starting_qbs(games)
    out = out.merge(
        qbs[["game_id", "team", "qb_id"]].rename(columns={"team": "opp", "qb_id": "opp_qb_id"}),
        on=["game_id", "opp"],
        how="left",
    )
    out = out.merge(qbs[["game_id", "team", "dome"]], on=["game_id", "team"], how="left")
    out = out.rename(columns={"sack": "sacks", "def_int": "ints"})
    out["takeaways"] = out["ints"] + out["fum_rec"]
    out["misc"] = out["safety"] + out["blocked_kick"] + out["two_pt_return"]
    out["qb_hits"] = pd.to_numeric(out["qb_hits"], errors="coerce")
    out["t"] = time_key(out["season"], out["week"])
    out = out[out["dk"].notna()]
    return out[DST_GAME_COLUMNS].sort_values(["team", "t"]).reset_index(drop=True)


def points_allowed_score(points_allowed: pd.Series) -> pd.Series:
    """DK's points-allowed tier for each REAL (integer) points-allowed figure."""
    pa = pd.to_numeric(points_allowed, errors="coerce")
    out = pd.Series(np.nan, index=pa.index, dtype=float)
    for low, high, points in DST_POINTS_ALLOWED_TIERS:
        mask = (pa >= low) & (True if high is None else pa <= high)
        out[mask] = points
    return out


def _dk_points(fields: pd.DataFrame) -> pd.Series:
    """The DST scorer's sum, vectorised (`dk_scoring.score_dst_actual_row` per row is the reference; a test
    pins them equal)."""
    total = sum(pd.to_numeric(fields[c], errors="coerce") * w for c, w in DK_DST_SCORING.items())
    total = total + 2.0 * pd.to_numeric(fields["two_pt_return"], errors="coerce")
    return (total + points_allowed_score(fields["points_allowed"])).round(2)


def build_qb_games(stats_player: pd.DataFrame) -> pd.DataFrame:
    """One row per QB-game: `qb_id`, `team`, `t`, `dropbacks`, `attempts`, `sacks`, `ints`, `fumbles`
    (lost). Regular season only."""
    sp = stats_player[(stats_player["season_type"] == "REG") & (stats_player["position"] == "QB")].copy()
    sp["season"] = sp["season"].astype(int)
    sp["week"] = sp["week"].astype(int)
    attempts = _num(sp, "attempts")
    sacks = _num(sp, "sacks_suffered")
    out = pd.DataFrame(
        {
            "qb_id": sp["player_id"],
            "team": sp["team"].map(data.normalize_team),
            "season": sp["season"],
            "week": sp["week"],
            "t": time_key(sp["season"], sp["week"]),
            "attempts": attempts,
            "dropbacks": attempts + sacks,
            "sacks": sacks,
            "ints": _num(sp, "passing_interceptions"),
            "fumbles": _num(sp, "sack_fumbles_lost") + _num(sp, "rushing_fumbles_lost"),
        }
    )
    # A QB who only handed off or knelt is not a passing sample.
    out = out[out["dropbacks"] > 0]
    return out.sort_values(["qb_id", "t"]).reset_index(drop=True)


def previous_starters(rows: pd.DataFrame, qb_games: pd.DataFrame) -> pd.Series:
    """For each row (`opp`, `season`, `week`), the opposing team's starter in its latest game strictly before
    the row's week: the QB with the most dropbacks for that team that game. What is known before the game
    (a QB change that week is missed); NaN when the team has no earlier game. Index follows `rows`."""
    games = qb_games.sort_values(["team", "t", "dropbacks"]).groupby(["team", "t"]).tail(1)
    left = pd.DataFrame(
        {
            "team": rows["opp"].to_numpy(),
            "t": time_key(rows["season"].astype(int), rows["week"].astype(int)).to_numpy(),
            "_row": range(len(rows)),
        }
    ).dropna(subset=["team"])
    merged = pd.merge_asof(
        left.sort_values("t"),
        games[["team", "t", "qb_id"]].sort_values("t"),
        on="t",
        by="team",
        direction="backward",
        allow_exact_matches=False,
    ).sort_values("_row")
    out = pd.Series(np.nan, index=rows.index, dtype=object)
    out.iloc[merged["_row"].to_numpy()] = merged["qb_id"].to_numpy()
    return out


# -----------------------------------------------------------------------------------------------------
# Recency-weighted state, strictly before each row
# -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recency:
    """`half_life` in games; `carry` multiplies every earlier game's weight once at each season boundary."""

    half_life: float = 8.0
    carry: float = 0.5


def ewm_state(table: pd.DataFrame, key: str, columns: list[str], recency: Recency) -> pd.DataFrame:
    """Per `key`, the recency-weighted SUMS of `columns` after each game, INCLUDING that game, plus `w_games`
    (the weighted game count). Each earlier game's weight decays by `0.5 ** (1 / half_life)` per later game
    and by `carry` once per season boundary crossed. Returns `key`, `t`, `season` and the sums."""
    decay = 0.5 ** (1.0 / recency.half_life)
    t = table.sort_values([key, "t"]).reset_index(drop=True)
    values = t[columns].to_numpy(dtype=float)
    values = np.nan_to_num(values, nan=0.0)
    keys = t[key].to_numpy()
    seasons = t["season"].to_numpy()
    sums = np.zeros_like(values)
    games = np.zeros(len(t))
    state = np.zeros(values.shape[1])
    g = 0.0
    for i in range(len(t)):
        if i == 0 or keys[i] != keys[i - 1]:
            state = np.zeros(values.shape[1])
            g = 0.0
        else:
            factor = decay * (recency.carry if seasons[i] != seasons[i - 1] else 1.0)
            state = state * factor
            g *= factor
        state = state + values[i]
        g += 1.0
        sums[i] = state
        games[i] = g
    out = pd.DataFrame(sums, columns=columns)
    out.insert(0, key, keys)
    out.insert(1, "t", t["t"].to_numpy())
    out.insert(2, "season", seasons)
    out["w_games"] = games
    return out


def state_before(
    rows: pd.DataFrame, state: pd.DataFrame, *, row_key: str, state_key: str, recency: Recency
) -> pd.DataFrame:
    """For each row, `state`'s sums after the latest game strictly before the row's `t`, matched on
    `row_key` (in `rows`) = `state_key` (in `state`), with one more season-boundary `carry` applied when that
    game was in an earlier season. Row order is preserved; rows with no earlier game get zeros."""
    left = rows[[row_key, "t", "season"]].rename(columns={row_key: "_k", "season": "_row_season"}).copy()
    left["_row"] = range(len(left))
    right = state.rename(columns={state_key: "_k", "season": "_state_season"})
    left = left[left["_k"].notna()]
    # one key type on both sides (nflverse ids arrive as pandas strings, a slate's as objects)
    left["_k"] = left["_k"].astype(str)
    right["_k"] = right["_k"].astype(str)
    merged = pd.merge_asof(
        left.sort_values("t"),
        right.sort_values("t"),
        on="t",
        by="_k",
        direction="backward",
        allow_exact_matches=False,
    )
    value_cols = [c for c in state.columns if c not in (state_key, "t", "season")]
    crossed = merged["_state_season"].notna() & (merged["_state_season"] != merged["_row_season"])
    factor = np.where(crossed, recency.carry, 1.0)
    for col in value_cols:
        merged[col] = merged[col].fillna(0.0) * factor
    out = pd.DataFrame(0.0, index=range(len(rows)), columns=value_cols)
    out.loc[merged["_row"].to_numpy(), value_cols] = merged[value_cols].to_numpy()
    return out


# -----------------------------------------------------------------------------------------------------
# Features
# -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DstParams:
    """The model's fixed settings, chosen on the validation split (fit 2015-2019, judge 2020-2022) and never
    on the held-out seasons. Shrinkage strengths are pseudo-observations at the league rate: `k_dropbacks`
    dropbacks for a pass rate, `k_plays` plays for a per-play rate, `k_games` games for a per-game mean,
    `k_qb` dropbacks for a quarterback's own rate. `league_half_life` (team-games) sets how fast the league
    levels of the rare parts (takeaways, return TDs, safeties and blocks) follow the trend: about two
    seasons."""

    recency: Recency = Recency(half_life=16.0, carry=0.6)
    k_dropbacks: float = 150.0
    k_plays: float = 300.0
    k_games: float = 4.0
    k_qb: float = 200.0
    league_half_life: float = 544.0


DEFAULT_PARAMS = DstParams()

# The parts of a DST score the model projects separately, and the features each reads. Sacks carry the only
# rate signal that holds out (validation: within-week rho 0.17 on Vegas alone, 0.24 with the rates);
# takeaways and points allowed are Vegas (the rates added nothing out of sample).
SACK_FEATURES = [
    "d_sack",
    "d_hit",
    "o_sack",
    "q_sack",
    "q_exp",
    "dome",
    "opp_implied",
    "spread",
    "total",
    "home",
]
TAKEAWAY_FEATURES = ["opp_implied", "spread", "total", "home"]
POINTS_ALLOWED_FEATURES = ["opp_implied", "o_pts", "d_pa", "dome"]
FEATURE_COLUMNS = list(dict.fromkeys([*SACK_FEATURES, *TAKEAWAY_FEATURES, *POINTS_ALLOWED_FEATURES]))
LEAGUE_PARTS = ("takeaways", "def_td", "misc")

# What a feature row needs (a slate row has no outcome columns): the defense, its opponent, the time key, the
# opponent's starting QB (NaN is fine: he then reads as the league prior) and the game context.
ROW_COLUMNS = ["team", "opp", "season", "week", "opp_qb_id", "implied", "spread", "total", "home", "dome"]


@dataclass(frozen=True)
class Priors:
    """League rates on the fit seasons: the shrinkage targets."""

    sack: float
    hit: float
    give: float
    points_allowed: float

    @classmethod
    def measure(cls, games: pd.DataFrame) -> Priors:
        return cls(
            sack=float(games["sacks"].sum() / games["opp_dropbacks"].sum()),
            hit=float(games["qb_hits"].sum() / games["opp_dropbacks"].sum()),
            give=float(games["opp_giveaways"].sum() / games["opp_plays"].sum()),
            points_allowed=float(games["points_allowed"].mean()),
        )


def league_levels(dst_games: pd.DataFrame, params: DstParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Per time key `t`, the league's recency-weighted mean per team-game of each of `LEAGUE_PARTS`, from
    games STRICTLY BEFORE `t` (each team-game weighs `0.5 ** (1 / league_half_life)` less per later
    team-game). A row for every `t` in `dst_games` plus one past the last, so a slate row after the last
    completed week finds its level. Columns `t`, `lg_<part>`."""
    per_t = dst_games.groupby("t").agg(n=("dk", "size"), **{p: (p, "sum") for p in LEAGUE_PARTS})
    decay = 0.5 ** (1.0 / params.league_half_life)
    rows = []
    sums = dict.fromkeys(LEAGUE_PARTS, 0.0)
    weight = 0.0
    for t, r in per_t.iterrows():
        rows.append(
            {"t": int(t), **{f"lg_{p}": (sums[p] / weight if weight else np.nan) for p in LEAGUE_PARTS}}
        )
        factor = decay ** r["n"]
        weight = weight * factor + r["n"]
        for p in LEAGUE_PARTS:
            sums[p] = sums[p] * factor + r[p]
    rows.append({"t": 10**9, **{f"lg_{p}": sums[p] / weight for p in LEAGUE_PARTS}})
    return pd.DataFrame(rows)


def dst_features(
    rows: pd.DataFrame,
    dst_games: pd.DataFrame,
    qb_games: pd.DataFrame,
    priors: Priors,
    params: DstParams = DEFAULT_PARAMS,
) -> pd.DataFrame:
    """`FEATURE_COLUMNS` plus the league levels (`lg_*`) for every row of `rows` (`ROW_COLUMNS`), each from
    games strictly before the row's week. Index and order follow `rows`.

    - `d_*`: the defense's own sack rate and QB-hit rate per dropback faced, points allowed per game.
    - `o_*`: the opposing offense's sack rate allowed, points scored per game (net of return TDs, as DK
      charges them).
    - `q_*`: the opposing starting QB's own sack rate and `q_exp`, log(1 + his weighted dropbacks), so a
      backup or rookie reads as one.
    - Vegas: `opp_implied` (the opponent's implied total), `spread` (positive = this defense's team favoured),
      `total`, `home`, `dome`."""
    missing = [c for c in ROW_COLUMNS if c not in rows.columns]
    if missing:
        raise ValueError(f"rows is missing columns: {missing}")
    rec = params.recency
    work = rows[ROW_COLUMNS].reset_index(drop=True).copy()
    work["t"] = time_key(work["season"].astype(int), work["week"].astype(int))

    d_state = ewm_state(dst_games, "team", ["sacks", "qb_hits", "points_allowed", "opp_dropbacks"], rec)
    d = state_before(work, d_state, row_key="team", state_key="team", recency=rec)
    offense = dst_games.drop(columns=["team"]).rename(columns={"opp": "offense"})
    o_state = ewm_state(offense, "offense", ["sacks", "opp_dropbacks", "points_allowed"], rec)
    o = state_before(work, o_state, row_key="opp", state_key="offense", recency=rec)
    q_state = ewm_state(qb_games, "qb_id", ["dropbacks", "sacks"], rec)
    q = state_before(work, q_state, row_key="opp_qb_id", state_key="qb_id", recency=rec)

    k_db, k_g, k_q = params.k_dropbacks, params.k_games, params.k_qb
    out = pd.DataFrame(index=work.index)
    out["d_sack"] = (d["sacks"] + k_db * priors.sack) / (d["opp_dropbacks"] + k_db)
    out["d_hit"] = (d["qb_hits"] + k_db * priors.hit) / (d["opp_dropbacks"] + k_db)
    out["d_pa"] = (d["points_allowed"] + k_g * priors.points_allowed) / (d["w_games"] + k_g)
    out["o_sack"] = (o["sacks"] + k_db * priors.sack) / (o["opp_dropbacks"] + k_db)
    out["o_pts"] = (o["points_allowed"] + k_g * priors.points_allowed) / (o["w_games"] + k_g)
    out["q_sack"] = (q["sacks"] + k_q * priors.sack) / (q["dropbacks"] + k_q)
    out["q_exp"] = np.log1p(q["dropbacks"])
    out["opp_implied"] = pd.to_numeric(work["total"], errors="coerce") - pd.to_numeric(
        work["implied"], errors="coerce"
    )
    for col in ("spread", "total", "home"):
        out[col] = pd.to_numeric(work[col], errors="coerce")
    out["dome"] = pd.to_numeric(work["dome"], errors="coerce").fillna(0.0)
    levels = league_levels(dst_games, params)
    lv = pd.merge_asof(
        work[["t"]].assign(_row=range(len(work))).sort_values("t"),
        levels.sort_values("t"),
        on="t",
        direction="forward",
    ).sort_values("_row")
    for p in LEAGUE_PARTS:
        out[f"lg_{p}"] = lv[f"lg_{p}"].to_numpy()
    out.index = rows.index
    return out


# -----------------------------------------------------------------------------------------------------
# Fit and predict
# -----------------------------------------------------------------------------------------------------

POISSON_ALPHA = 1e-4  # a whisper of L2, only so the solver converges on collinear Vegas columns
# Residual draws kept for the outcome distribution (a fixed-seed sample of training rows): enough for a
# Boom% to the nearest point, cheap enough for a slate.
RESIDUAL_DRAWS = 2000
RESIDUAL_SEED = 0
# The first season in the history only builds the rates: its rows have almost no earlier games.
BURN_IN_SEASONS = 1


@dataclass
class _Glm:
    """A Poisson regression on standardised columns, stored as plain numbers."""

    columns: list[str]
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float

    offset: str | None = None

    def rate(self, features: pd.DataFrame) -> np.ndarray:
        """The expected count: the fitted rate times the row's `offset` column when there is one."""
        x = (features[self.columns].to_numpy(dtype=float) - self.mean) / self.scale
        rate = np.exp(x @ self.coef + self.intercept)
        return rate if self.offset is None else rate * features[self.offset].to_numpy(dtype=float)


def _fit_glm(features: pd.DataFrame, target: pd.Series, columns: list[str], offset: str | None) -> _Glm:
    """Poisson GLM of `target` on `columns`, with the `offset` column (a known expected count the columns
    scale) as exposure: fitted on target / offset weighted by offset, which is the offset model exactly."""
    from sklearn.linear_model import PoissonRegressor

    x = features[columns].to_numpy(dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    exposure = np.ones(len(x)) if offset is None else features[offset].to_numpy(dtype=float)
    model = PoissonRegressor(alpha=POISSON_ALPHA, max_iter=2000)
    model.fit((x - mean) / scale, target.to_numpy(dtype=float) / exposure, sample_weight=exposure)
    return _Glm(columns, mean, scale, model.coef_.copy(), float(model.intercept_), offset)


@dataclass
class DstModel:
    """A fitted model: priors, settings, the sacks and takeaways GLMs, the points-allowed line and the joint
    residuals (one row per sampled training game: sacks, takeaways, return TDs + safeties/blocks as DK points,
    points allowed) the outcome distribution resamples: ratios to the fitted part for the counts, points
    for points allowed."""

    priors: Priors
    params: DstParams
    sacks: _Glm
    takeaways: _Glm
    pa_columns: list[str]
    pa_coef: np.ndarray
    pa_intercept: float
    residuals: np.ndarray
    fit_seasons: tuple[int, int]
    train_rows: int


def training_frame(
    dst_games: pd.DataFrame, qb_games: pd.DataFrame, priors: Priors, params: DstParams = DEFAULT_PARAMS
) -> pd.DataFrame:
    """Every team-game with its outcomes and its strictly-earlier features side by side (rows with no Vegas
    line dropped)."""
    feats = dst_features(dst_games, dst_games, qb_games, priors, params)
    frame = pd.concat([dst_games.reset_index(drop=True), feats.reset_index(drop=True)], axis=1)
    frame = frame.loc[:, ~frame.columns.duplicated(keep="last")]
    return frame[frame["opp_implied"].notna()].reset_index(drop=True)


def _parts(model_sacks: np.ndarray, takeaways: np.ndarray, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Return TDs scale with takeaways around the league's recent level; safeties, blocks and 2-point returns
    are the league's recent level."""
    lg_take = frame["lg_takeaways"].to_numpy(dtype=float)
    td = frame["lg_def_td"].to_numpy(dtype=float) * takeaways / lg_take
    misc = frame["lg_misc"].to_numpy(dtype=float)
    return {"sacks": model_sacks, "takeaways": takeaways, "def_td": td, "misc": misc}


def fit(
    dst_games: pd.DataFrame,
    qb_games: pd.DataFrame,
    seasons: tuple[int, int],
    params: DstParams = DEFAULT_PARAMS,
) -> DstModel:
    """Fit on team-games in `seasons` (inclusive; the first `BURN_IN_SEASONS` of the HISTORY only feed the
    rates). Priors are measured on the same seasons, so nothing later reaches the fit."""
    first, last = seasons
    in_fit = dst_games["season"].between(first, last)
    priors = Priors.measure(dst_games[in_fit])
    frame = training_frame(dst_games, qb_games, priors, params)
    start = max(first, int(dst_games["season"].min()) + BURN_IN_SEASONS)
    train = frame[frame["season"].between(start, last)].reset_index(drop=True)

    sacks = _fit_glm(train, train["sacks"], SACK_FEATURES, None)
    takeaways = _fit_glm(train, train["takeaways"], TAKEAWAY_FEATURES, "lg_takeaways")
    x = train[POINTS_ALLOWED_FEATURES].to_numpy(dtype=float)
    design = np.c_[np.ones(len(x)), x]
    beta, *_ = np.linalg.lstsq(design, train["points_allowed"].to_numpy(dtype=float), rcond=None)

    parts = _parts(sacks.rate(train), takeaways.rate(train), train)
    pa_hat = design @ beta
    other = 6.0 * train["def_td"].to_numpy(dtype=float) + 2.0 * train["misc"].to_numpy(dtype=float)
    joint = np.c_[
        train["sacks"].to_numpy(dtype=float) / parts["sacks"],
        train["takeaways"].to_numpy(dtype=float) / parts["takeaways"],
        other / (6.0 * parts["def_td"] + 2.0 * parts["misc"]),
        train["points_allowed"].to_numpy(dtype=float) - pa_hat,
    ]
    rng = np.random.default_rng(RESIDUAL_SEED)
    pick = rng.choice(len(joint), size=min(RESIDUAL_DRAWS, len(joint)), replace=False)
    return DstModel(
        priors=priors,
        params=params,
        sacks=sacks,
        takeaways=takeaways,
        pa_columns=list(POINTS_ALLOWED_FEATURES),
        pa_coef=beta[1:],
        pa_intercept=float(beta[0]),
        residuals=joint[pick],
        fit_seasons=(start, last),
        train_rows=len(train),
    )


PREDICTION_COLUMNS = ["dst_mean", "dst_sacks", "dst_takeaways", "dst_td_misc", "dst_pa", "dst_pa_score"]


def _draws(model: DstModel, parts: dict[str, np.ndarray], pa_mean: np.ndarray) -> np.ndarray:
    """DK points per (row, residual draw): each part's mean times (for the counts) or plus (for points
    allowed, then rounded and floored at 0) that training game's residual for the part, all from the SAME
    game, so the parts keep the correlation real games have (a turnover-heavy game is also a low-scoring
    one). Ratio residuals keep the counts non-negative without moving their mean."""
    r = model.residuals
    sacks = parts["sacks"][:, None] * r[None, :, 0]
    takeaways = parts["takeaways"][:, None] * r[None, :, 1]
    other = (6.0 * parts["def_td"] + 2.0 * parts["misc"])[:, None] * r[None, :, 2]
    pa = np.clip(np.round(pa_mean[:, None] + r[None, :, 3]), 0, None)
    tiers = points_allowed_score(pd.Series(pa.ravel())).to_numpy().reshape(pa.shape)
    return sacks + 2.0 * takeaways + other + tiers


def predict(
    model: DstModel, rows: pd.DataFrame, dst_games: pd.DataFrame, qb_games: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray]:
    """`PREDICTION_COLUMNS` for every row of `rows` (`ROW_COLUMNS`), index-aligned, plus the outcome draws
    (rows x `RESIDUAL_DRAWS`, DK points rounded to whole points as DST scores are) that `prob_at_least`
    reads. Rows with no Vegas line get NaN. `dst_mean` is the sum of the parts' means (sacks + 2 x takeaways
    + 6 x return TDs + 2 x safeties/blocks + the expected points-allowed tier)."""
    feats = dst_features(rows, dst_games, qb_games, model.priors, model.params)
    ok = feats["opp_implied"].notna().to_numpy() & feats[FEATURE_COLUMNS].notna().all(axis=1).to_numpy()
    out = pd.DataFrame(np.nan, index=rows.index, columns=PREDICTION_COLUMNS)
    draws = np.full((len(rows), len(model.residuals)), np.nan)
    if not ok.any():
        return out, draws
    f = feats[ok]
    parts = _parts(model.sacks.rate(f), model.takeaways.rate(f), f)
    pa_mean = f[model.pa_columns].to_numpy(dtype=float) @ model.pa_coef + model.pa_intercept
    sims = np.round(_draws(model, parts, pa_mean))
    pa_draws = np.clip(np.round(pa_mean[:, None] + model.residuals[None, :, 3]), 0, None)
    pa_score = (
        points_allowed_score(pd.Series(pa_draws.ravel())).to_numpy().reshape(pa_draws.shape).mean(axis=1)
    )
    idx = rows.index[ok]
    out.loc[idx, "dst_sacks"] = parts["sacks"]
    out.loc[idx, "dst_takeaways"] = parts["takeaways"]
    out.loc[idx, "dst_td_misc"] = 6.0 * parts["def_td"] + 2.0 * parts["misc"]
    out.loc[idx, "dst_pa"] = pa_mean
    out.loc[idx, "dst_pa_score"] = pa_score
    out.loc[idx, "dst_mean"] = (
        parts["sacks"] + 2.0 * parts["takeaways"] + 6.0 * parts["def_td"] + 2.0 * parts["misc"] + pa_score
    )
    draws[ok] = sims
    return out, draws


def prob_at_least(draws: np.ndarray, thresholds: np.ndarray | float) -> np.ndarray:
    """P(DK points >= threshold) per row from `predict`'s draws (NaN rows stay NaN)."""
    thresholds = np.broadcast_to(np.asarray(thresholds, dtype=float), (draws.shape[0],))
    out = (draws >= thresholds[:, None]).mean(axis=1)
    out[np.isnan(draws).all(axis=1)] = np.nan
    return out
