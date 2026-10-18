"""Game script: does a role pair's correlation depend on the spread or the implied total?

`docs/SIM.md` tested only the game TOTAL (terciles) and found chance. This module tests the two things a
football player would name first -- the **closing spread** (team-relative: favourite or underdog, and by how
much) and the team's **implied total** -- for every role pair, on the same normal scores
(`correlation.normal_scores`: "how surprising was this game, given the projection"), fitted on 2014-2022 and
judged on 2023-2025.

Pure functions over frames (no network, no sheet):

1. `oriented_rows` -- the (game, z_a, z_b) rows of a role pair, each carrying the spread and implied total of
   ROLE A's team. A same-team pair has one team; an opposing pair is oriented by role A's team ("RB1 against
   the other side's WR1, by the spread of the RB1's team") and is listed once per unordered role pair.
2. `scan` -- for every pair, the slope of the cross-product z_a * z_b on the covariate (game-clustered
   t-statistic), plus the bucket correlations. With 110 pairs and two covariates a few |t| > 2 appear by
   chance, so the whole scan is repeated with the games' contexts SHUFFLED among games of the same season
   (breaking any link to the surprises): `Scan.null_max_t` / `null_counts` hold what chance produces, and
   `p_adj_*` reads each real t against the largest |t| any pair gets in a shuffle. This is the tercile
   analysis's chance flag, made to cover a family of tests.
3. `ContextLookup` -- the conditional table: a bucket's correlation, shrunk toward the POOLED value (not
   toward 0) with the same prior the pooled table uses, as a layer over a `CorrelationLookup`.
4. `loglik_gain` / `summarize_gain` -- the out-of-sample score: the bivariate-normal log-likelihood of the
   test seasons' (z_a, z_b) pairs under the conditional rho against the pooled rho, with a game-clustered
   standard error; `shuffled_frame` gives the control (the same table fitted on shuffled contexts).
5. `marginal_effects`, `margin_mechanism`, `tail_dependence` -- the side questions: does the projection
   already price the spread, where does a pair's dependence come from, and do booms cluster beyond a
   Gaussian copula.

Nothing here changes `models/sim/correlations.csv`; whether a conditional table ships is decided by the
out-of-sample numbers (`docs/SIM.md`, "Game script").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.model import data
from dfs.model.history import team_game_context
from dfs.sim.correlation import POOLED, SHRINK_PRIOR_N, _game_sums, _pearson, role_pairs, shrink
from dfs.sim.roles import ROLES

TRAIN_THROUGH = 2022
TEST_SEASONS = (2023, 2024, 2025)

# --- buckets --------------------------------------------------------------------------------------------

SPREAD_BUCKETS = ("BD", "SD", "SF", "BF")  # big dog, small dog, small favourite, big favourite
BIG_SPREAD = 7.0  # a favourite (or dog) of a touchdown or more
IMPLIED_BUCKETS = ("low", "mid", "high")
VARIABLES = ("spread", "implied")
SPREAD_LABELS = {
    "BD": "big dog (+7 or more)",
    "SD": "small dog",
    "SF": "small favourite",
    "BF": "big favourite (-7 or more)",
}


def spread_bucket(spread) -> np.ndarray:
    """Team-relative closing spread (positive = this team favoured) -> 'BF' (>= 7), 'SF' (0 to 7), 'SD'
    (under 0 to -7) or 'BD' (<= -7). A pick'em is a small favourite on both sides."""
    s = np.asarray(spread, dtype=float)
    return np.select([s >= BIG_SPREAD, s >= 0.0, s > -BIG_SPREAD], ["BF", "SF", "SD"], "BD")


def implied_cutoffs(implied) -> tuple[float, float]:
    """Terciles of team implied totals (compute on the TRAINING seasons only and apply to the rest)."""
    lo, hi = np.quantile(np.asarray(implied, dtype=float), [1 / 3, 2 / 3])
    return float(lo), float(hi)


def implied_bucket(implied, cutoffs: tuple[float, float]) -> np.ndarray:
    x = np.asarray(implied, dtype=float)
    return np.select([x < cutoffs[0], x < cutoffs[1]], ["low", "mid"], "high")


def bucket_of(variable: str, values, cutoffs: tuple[float, float] | None = None) -> np.ndarray:
    if variable == "spread":
        return spread_bucket(values)
    if cutoffs is None:
        raise ValueError("implied-total buckets need the training cutoffs")
    return implied_bucket(values, cutoffs)


def buckets_of(variable: str) -> tuple[str, ...]:
    return SPREAD_BUCKETS if variable == "spread" else IMPLIED_BUCKETS


# --- the frame ------------------------------------------------------------------------------------------


def with_context(frame: pd.DataFrame, games: pd.DataFrame | None = None) -> pd.DataFrame:
    """`frame` (`roles.build_frame`) plus the team-relative `spread` and `implied` total of each row's team
    and whether it is the `home` side (nflverse's closing line; `history.team_game_context`)."""
    if {"spread", "implied", "home"} <= set(frame.columns):
        return frame
    ctx = team_game_context(data.read_games() if games is None else games)[
        ["game_id", "team", "implied", "spread", "home"]
    ]
    return frame.drop(
        columns=[c for c in ctx.columns if c in frame.columns and c not in ("game_id", "team")]
    ).merge(ctx, on=["game_id", "team"], how="left", validate="many_to_one")


def game_context(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per game: `game_id, season, home_spread, total` (the home side's closing spread and the game
    total), recovered from the rows' team-relative values."""
    home = frame[frame["home"] == 1].drop_duplicates("game_id")
    out = home.rename(columns={"spread": "home_spread"})[["game_id", "season", "home_spread", "total"]]
    return out.reset_index(drop=True)


# --- the pairs ------------------------------------------------------------------------------------------


def family_pairs() -> list[tuple[str, str, str]]:
    """The tested family, one entry per unordered role pair: every same-team pair, and every opposing pair of
    two DIFFERENT roles (a role against itself has no team-relative spread). An opposing pair is listed
    with its roles in `ROLES` order; `oriented_rows` can be asked for either orientation."""
    out = [("same_team", a, b) for a, b in role_pairs() if a != b]
    out += [("opp", a, b) for a, b in role_pairs() if a != b]
    return out


# The pairs the task names, with role A's team supplying the spread. (relation, a, b, label)
NAMED_PAIRS = (
    ("same_team", "RB1", "DST", "RB1 with his own DST"),
    ("opp", "RB1", "DST", "RB1 with the opposing DST"),
    ("same_team", "QB1", "WR1", "QB1 with WR1"),
    ("same_team", "QB1", "WR2", "QB1 with WR2"),
    ("same_team", "QB1", "TE1", "QB1 with TE1"),
    ("opp", "QB1", "WR1", "QB1 with the opposing WR1 (bring-back)"),
    ("same_team", "WR1", "WR2", "WR1 with WR2 (cannibalisation)"),
    ("opp", "RB1", "WR1", "favourite's RB1 with the dog's WR1"),
    ("opp", "RB1", "WR2", "favourite's RB1 with the dog's WR2"),
    ("opp", "RB1", "TE1", "favourite's RB1 with the dog's TE1"),
    ("opp", "QB1", "RB1", "QB1 with the opposing RB1"),
)


def oriented_rows(frame: pd.DataFrame, relation: str, a: str, b: str) -> pd.DataFrame:
    """One row per (game, a-player, b-player) for the pair, with `z_a, z_b` and ROLE A's team context:
    `game_id, season, team, home, spread, implied, total`. For an opposing pair `a` is the role whose team
    supplies the context; `frame` needs `z`, `spread`, `implied`, `total`."""
    cols = ["game_id", "season", "team", "opp", "z", "spread", "implied", "total", "home"]
    left = frame.loc[frame["role"] == a, cols]
    right = frame.loc[frame["role"] == b, ["game_id", "team", "z"]]
    if relation == "same_team":
        m = left.merge(right, on=["game_id", "team"], suffixes=("", "_b"))
    else:
        m = left.merge(right, left_on=["game_id", "opp"], right_on=["game_id", "team"], suffixes=("", "_b"))
    m = m.rename(columns={"z": "z_a"})
    return m[["game_id", "season", "team", "home", "z_a", "z_b", "spread", "implied", "total"]].reset_index(
        drop=True
    )


# --- the slope test -------------------------------------------------------------------------------------


def clustered_slope(y: np.ndarray, x: np.ndarray, cluster: np.ndarray) -> tuple[float, float]:
    """Slope of `y` on `x` (with an intercept) and its cluster-robust standard error (clusters = games, so
    the two teams of a game and every pair inside it stay together)."""
    n = len(y)
    if n < 30:
        return float("nan"), float("nan")
    xc = x - x.mean()
    sxx = float((xc**2).sum())
    if sxx <= 0:
        return float("nan"), float("nan")
    slope = float((xc * y).sum() / sxx)
    resid = y - y.mean() - slope * xc
    score = np.bincount(cluster, weights=xc * resid)
    se = float(np.sqrt((score**2).sum()) / sxx)
    return slope, se


def covariate(rows: pd.DataFrame, variable: str) -> np.ndarray:
    return rows[variable].to_numpy(dtype=float)


@dataclass
class PairData:
    """One tested pair's observations in the form the scan and the shuffles need."""

    relation: str
    a: str
    b: str
    game: np.ndarray  # integer game codes (index into the game table)
    sign: np.ndarray  # +1 when role A's team is the home side, else -1
    y: np.ndarray  # z_a * z_b
    za: np.ndarray
    zb: np.ndarray
    spread: np.ndarray
    implied: np.ndarray
    sd: dict[str, float]  # standard deviation of each covariate across these observations


def pair_data(rows: pd.DataFrame, relation: str, a: str, b: str, game_code: pd.Series) -> PairData:
    za, zb = rows["z_a"].to_numpy(), rows["z_b"].to_numpy()
    spread, implied = rows["spread"].to_numpy(float), rows["implied"].to_numpy(float)
    return PairData(
        relation,
        a,
        b,
        rows["game_id"].map(game_code).to_numpy(),
        np.where(rows["home"].to_numpy() == 1, 1.0, -1.0),
        za * zb,
        za,
        zb,
        spread,
        implied,
        {
            "spread": float(spread.std()) if len(spread) else 0.0,
            "implied": float(implied.std()) if len(implied) else 0.0,
        },
    )


def t_statistic(pd_: PairData, variable: str, spread_g: np.ndarray, total_g: np.ndarray) -> float:
    """The clustered t of the slope of z_a * z_b on `variable`, taking the games' contexts from
    `spread_g` (home spread) and `total_g` (game total) arrays indexed by game code -- the real ones, or a
    shuffled copy."""
    s = pd_.sign * spread_g[pd_.game]
    x = s if variable == "spread" else total_g[pd_.game] / 2.0 + s / 2.0
    slope, se = clustered_slope(pd_.y, x, pd_.game)
    return slope / se if se and np.isfinite(se) and se > 0 else float("nan")


def shuffled_contexts(games: pd.DataFrame, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """The games' (home spread, total) permuted among games of the SAME season: any link between a game's
    context and its surprises is broken, while the contexts' distribution and each season's level are kept."""
    spread = games["home_spread"].to_numpy(float).copy()
    total = games["total"].to_numpy(float).copy()
    for season in games["season"].unique():
        idx = np.flatnonzero((games["season"] == season).to_numpy())
        perm = rng.permutation(idx)
        spread[idx], total[idx] = spread[perm], total[perm]
    return spread, total


@dataclass
class Scan:
    """The pooled correlation, the slope t-statistics and the bucket correlations of every tested pair."""

    table: pd.DataFrame  # relation, role_a, role_b, n, r_pooled, t_spread, t_implied, sd_*, p_adj_*
    buckets: pd.DataFrame  # relation, role_a, role_b, variable, bucket, n, r, lo, hi
    null_max_t: dict[str, np.ndarray]  # variable -> the largest |t| of each shuffle
    null_counts: dict[str, np.ndarray]  # variable -> pairs with |t| > 1.96 in each shuffle
    cutoffs: tuple[float, float]


def scan(
    frame: pd.DataFrame,
    *,
    n_shuffle: int = 200,
    n_boot: int = 500,
    seed: int = 0,
    pairs: list[tuple[str, str, str]] | None = None,
    min_n: int = 100,
) -> Scan:
    """Scan `frame` (needs `z`, `spread`, `implied`, `total`, `role`, ...; the caller restricts it to the
    seasons to scan) for correlation-by-context. `pairs` defaults to the whole family."""
    pairs = pairs or family_pairs()
    games = game_context(frame)
    game_code = pd.Series(np.arange(len(games)), index=games["game_id"])
    cutoffs = implied_cutoffs(frame.drop_duplicates(["game_id", "team"])["implied"])
    spread_g = games["home_spread"].to_numpy(float)
    total_g = games["total"].to_numpy(float)

    data_by_pair: list[PairData] = []
    rows_by_pair: dict[tuple[str, str, str], pd.DataFrame] = {}
    for relation, a, b in pairs:
        rows = oriented_rows(frame, relation, a, b)
        if len(rows) < min_n:
            continue
        rows_by_pair[(relation, a, b)] = rows
        data_by_pair.append(pair_data(rows, relation, a, b, game_code))

    rng = np.random.default_rng(seed)
    out_rows, bucket_rows = [], []
    t_real = {v: np.array([t_statistic(p, v, spread_g, total_g) for p in data_by_pair]) for v in VARIABLES}
    null_max = {v: np.zeros(n_shuffle) for v in VARIABLES}
    null_count = {v: np.zeros(n_shuffle, dtype=int) for v in VARIABLES}
    for k in range(n_shuffle):
        sp, tt = shuffled_contexts(games, rng)
        for v in VARIABLES:
            t = np.array([t_statistic(p, v, sp, tt) for p in data_by_pair])
            null_max[v][k] = np.nanmax(np.abs(t))
            null_count[v][k] = int((np.abs(t) > 1.96).sum())

    boot_rng = np.random.default_rng(seed + 1)
    counts = boot_rng.multinomial(len(games), np.full(len(games), 1 / len(games)), size=n_boot).astype(float)
    for p, t_s, t_i in zip(data_by_pair, t_real["spread"], t_real["implied"], strict=True):
        rows = rows_by_pair[(p.relation, p.a, p.b)]
        pair_frame = pd.DataFrame({"game_id": rows["game_id"], "za": p.za, "zb": p.zb})
        sums = _game_sums(pair_frame, game_code, len(games))
        r_pooled = float(_pearson(sums.sum(axis=0, keepdims=True))[0])
        row = {
            "relation": p.relation,
            "role_a": p.a,
            "role_b": p.b,
            "n": len(p.y),
            "r_pooled": r_pooled,
            "t_spread": t_s,
            "t_implied": t_i,
            "sd_spread": p.sd["spread"],
            "sd_implied": p.sd["implied"],
        }
        for v, t in (("spread", t_s), ("implied", t_i)):
            has_null = n_shuffle > 0 and np.isfinite(t)
            row[f"p_adj_{v}"] = float((null_max[v] >= abs(t)).mean()) if has_null else float("nan")
            slope, _ = clustered_slope(
                p.y, p.sign * spread_g[p.game] if v == "spread" else rows["implied"].to_numpy(float), p.game
            )
            row[f"slope_{v}"] = slope * p.sd[v]  # change in correlation per +1 sd of the covariate
        out_rows.append(row)
        for v in VARIABLES:
            values = rows["spread"] if v == "spread" else rows["implied"]
            labels = bucket_of(v, values, cutoffs)
            for bucket in buckets_of(v):
                mask = labels == bucket
                s = sums_masked(pair_frame, game_code, len(games), mask)
                n = int(s[:, 0].sum())
                if n < 30:
                    bucket_rows.append((p.relation, p.a, p.b, v, bucket, n, np.nan, np.nan, np.nan))
                    continue
                r = float(_pearson(s.sum(axis=0, keepdims=True))[0])
                boot = _pearson(counts @ s)
                lo, hi = np.nanquantile(boot, [0.05, 0.95])
                bucket_rows.append((p.relation, p.a, p.b, v, bucket, n, r, float(lo), float(hi)))
    table = pd.DataFrame(out_rows)
    buckets = pd.DataFrame(
        bucket_rows, columns=["relation", "role_a", "role_b", "variable", "bucket", "n", "r", "lo", "hi"]
    )
    return Scan(table, buckets, null_max, null_count, cutoffs)


def sums_masked(pair_frame: pd.DataFrame, game_code: pd.Series, n_games: int, mask) -> np.ndarray:
    """`_game_sums` over the rows where `mask` holds."""
    sub = pair_frame[np.asarray(mask)]
    if sub.empty:
        return np.zeros((n_games, 6))
    return _game_sums(sub, game_code, n_games)


def chance_summary(table: pd.DataFrame, scan_: Scan) -> pd.DataFrame:
    """Per covariate: how many pairs have |t| > 1.96 on the real contexts, against the shuffled contexts."""
    rows = []
    for v in VARIABLES:
        real = int((table[f"t_{v}"].abs() > 1.96).sum())
        null = scan_.null_counts[v]
        rows.append(
            {
                "variable": v,
                "pairs": len(table),
                "flagged_real": real,
                "flagged_shuffled_mean": float(null.mean()),
                "flagged_shuffled_p95": float(np.quantile(null, 0.95)),
                "max_abs_t_real": float(table[f"t_{v}"].abs().max()),
                "max_abs_t_shuffled_p95": float(np.quantile(scan_.null_max_t[v], 0.95)),
            }
        )
    return pd.DataFrame(rows)


# --- the conditional table ------------------------------------------------------------------------------


def shrink_toward(r, n, centre, prior_n: float = SHRINK_PRIOR_N):
    """A bucket's correlation shrunk in Fisher-z space toward `centre` (the pooled value) with a prior worth
    `prior_n` observations: z' = (w z + prior_n c) / (w + prior_n), w = n - 3."""
    r = np.clip(np.asarray(r, dtype=float), -0.999999, 0.999999)
    c = np.clip(np.asarray(centre, dtype=float), -0.999999, 0.999999)
    w = np.maximum(np.asarray(n, dtype=float) - 3.0, 0.0)
    return np.tanh((w * np.arctanh(r) + prior_n * np.arctanh(c)) / (w + prior_n))


class ContextLookup:
    """The conditional table, as a layer over a pooled `CorrelationLookup` (`base`).

    A bucket's correlation (from a scan of the training seasons) is shrunk toward the pooled value, and the
    difference from the pooled value, in Fisher-z space, is the bucket's **deviation**. `rho(...)` is the
    pooled value plus the deviation of the role pair's spread bucket (and/or implied-total bucket: `use`
    names the variables conditioned on; the two deviations add, each being a separately fitted effect). Any
    pair, variable or bucket without a row falls back to the pooled value, so this is a drop-in
    `CorrelationLookup` for `simulate_lineups` (it carries `rho_pair`, which also reads the players' team
    context). `pairs` limits the conditioning to those role pairs (e.g. the ones that survived a test)."""

    def __init__(
        self,
        base,
        buckets: pd.DataFrame,
        cutoffs: tuple[float, float],
        *,
        prior_n: float = SHRINK_PRIOR_N,
        use: tuple[str, ...] = VARIABLES,
        pairs: set[tuple[str, str, str]] | None = None,
    ):
        self.base = base
        self.cutoffs = cutoffs
        self.use = use
        self.prior_n = prior_n
        self._dev: dict[tuple[str, str, str, str, str], float] = {}
        for rel, a, b, v, bucket, n, r in zip(
            buckets["relation"],
            buckets["role_a"],
            buckets["role_b"],
            buckets["variable"],
            buckets["bucket"],
            buckets["n"],
            buckets["r"],
            strict=True,
        ):
            if v not in use or not np.isfinite(r) or (pairs is not None and (rel, a, b) not in pairs):
                continue
            centre = base.rho(rel, a, b)
            shrunk = float(shrink_toward(r, n, centre, prior_n))
            self._dev[(rel, a, b, v, bucket)] = float(
                np.arctanh(np.clip(shrunk, -0.999999, 0.999999))
                - np.arctanh(np.clip(centre, -0.999999, 0.999999))
            )

    @property
    def deviations(self) -> dict:
        return dict(self._dev)

    def has(self, relation: str, a: str, b: str) -> bool:
        return self.base.has(relation, a, b)

    def pooled_rho(self, relation: str, a: str, b: str) -> float:
        return self.base.rho(relation, a, b)

    def rho(
        self,
        relation: str,
        a: str,
        b: str,
        ctx_a: tuple[float, float] | None = None,
        ctx_b: tuple[float, float] | None = None,
    ) -> float:
        """The correlation of role `a` with role `b`. `ctx_a` / `ctx_b` are the (spread, implied total) of
        each role's own team (the same for a same-team pair, so `ctx_b` may be left out there); without any
        context it is the pooled value. A pair is stored once, in `ROLES` order, with the context of its
        first role's team; asked in the other order, the second role's team supplies it."""
        pooled = self.base.rho(relation, a, b)
        if ctx_a is None:
            return pooled
        order = {r: i for i, r in enumerate(ROLES)}
        if order[a] > order[b]:
            a, b = b, a
            ctx_a, ctx_b = (ctx_b if ctx_b is not None else ctx_a), ctx_a
        spread, implied = ctx_a
        z = float(np.arctanh(np.clip(pooled, -0.999999, 0.999999)))
        for v in self.use:
            value = spread if v == "spread" else implied
            bucket = str(bucket_of(v, value, self.cutoffs))
            z += self._dev.get((relation, a, b, v, bucket), 0.0)
        return float(np.tanh(z))

    def rho_array(self, relation: str, a: str, b: str, spread, implied) -> np.ndarray:
        """`rho` for many observations at once: role `a`'s team has the given `spread` / `implied` arrays and
        the pair is stored in `ROLES` order (as in `family_pairs`)."""
        spread = np.asarray(spread, dtype=float)
        z = np.full(
            len(spread), float(np.arctanh(np.clip(self.base.rho(relation, a, b), -0.999999, 0.999999)))
        )
        for v in self.use:
            labels = bucket_of(v, spread if v == "spread" else np.asarray(implied, dtype=float), self.cutoffs)
            dev = {k[4]: d for k, d in self._dev.items() if k[:4] == (relation, a, b, v)}
            z = z + np.array([dev.get(str(x), 0.0) for x in labels])
        return np.tanh(z)

    def rho_pair(self, relation: str, role_a: str, role_b: str, spec_a, spec_b) -> float:
        """`simulate.correlation_matrix`'s hook: the correlation of two players' roles given their teams'
        context (a `PlayerSpec` without a spread or implied total gets the pooled value)."""
        if spec_a.spread is None or spec_a.implied is None or spec_b.spread is None or spec_b.implied is None:
            return self.pooled_rho(relation, role_a, role_b)
        return self.rho(
            relation, role_a, role_b, (spec_a.spread, spec_a.implied), (spec_b.spread, spec_b.implied)
        )


def pooled_rhos(frame: pd.DataFrame, pairs: list[tuple[str, str, str]], prior_n: float = SHRINK_PRIOR_N):
    """The pooled shrunk correlation of each pair on `frame` (what `correlations.csv` holds for its "
    "seasons)."""
    out = {}
    for relation, a, b in pairs:
        rows = oriented_rows(frame, relation, a, b)
        if len(rows) < 5:
            continue
        r = float(np.corrcoef(rows["z_a"], rows["z_b"])[0, 1])
        out[(relation, a, b)] = float(shrink(r, len(rows), prior_n))
    return out


def lookup_from_rhos(rhos: dict[tuple[str, str, str], float]):
    """A `CorrelationLookup` holding `rhos` (the output of `pooled_rhos`): pairs not in it are 0."""
    from dfs.sim.correlation import CSV_COLUMNS, CorrelationLookup

    rows = [(rel, a, b, POOLED, rho, 0, rho, rho) for (rel, a, b), rho in rhos.items()]
    return CorrelationLookup(pd.DataFrame(rows, columns=CSV_COLUMNS))


# --- the out-of-sample score ----------------------------------------------------------------------------


def bivariate_loglik(za: np.ndarray, zb: np.ndarray, rho: np.ndarray | float) -> np.ndarray:
    """Per-observation log-likelihood of standard-normal pairs with correlation `rho`."""
    rho = np.asarray(rho, dtype=float)
    q = (za**2 - 2 * rho * za * zb + zb**2) / (2 * (1 - rho**2))
    return -0.5 * np.log(1 - rho**2) - q


def loglik_gain(test: pd.DataFrame, lookup: ContextLookup, pairs: list[tuple[str, str, str]]) -> pd.DataFrame:
    """The test seasons' log-likelihood gain of the conditional rho over the pooled rho, one row per
    (pair, game): `relation, role_a, role_b, game_id, n, gain`."""
    parts = []
    for relation, a, b in pairs:
        obs = oriented_rows(test, relation, a, b)
        if len(obs) < 30:
            continue
        pooled = lookup.pooled_rho(relation, a, b)
        cond = lookup.rho_array(relation, a, b, obs["spread"].to_numpy(float), obs["implied"].to_numpy(float))
        za, zb = obs["z_a"].to_numpy(), obs["z_b"].to_numpy()
        gain = bivariate_loglik(za, zb, cond) - bivariate_loglik(za, zb, pooled)
        g = pd.DataFrame({"game_id": obs["game_id"].to_numpy(), "gain": gain, "n": 1})
        g = g.groupby("game_id", as_index=False).sum()
        parts.append(g.assign(relation=relation, role_a=a, role_b=b))
    cols = ["relation", "role_a", "role_b", "game_id", "n", "gain"]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


def summarize_gain(long: pd.DataFrame, pairs: list[tuple[str, str, str]] | None = None) -> dict:
    """Total gain, per 1,000 pair-observations, and its game-clustered standard error over `pairs` (all of
    them by default). Games are the clusters, summed over the pairs in a game, because the pairs of one game
    share its draws."""
    if pairs is not None:
        keys = set(pairs)
        long = long[
            [(r, a, b) in keys for r, a, b in zip(long.relation, long.role_a, long.role_b, strict=True)]
        ]
    if long.empty:
        return {"n": 0, "gain": 0.0, "per_1000": 0.0, "se_per_1000": 0.0}
    by_game = long.groupby("game_id")["gain"].sum()
    n = int(long["n"].sum())
    return {
        "n": n,
        "gain": float(long["gain"].sum()),
        "per_1000": float(1000 * long["gain"].sum() / n),
        "se_per_1000": float(1000 * np.sqrt((by_game**2).sum()) / n),
    }


def per_pair_gain(long: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (relation, a, b), g in long.groupby(["relation", "role_a", "role_b"]):
        s = summarize_gain(g)
        rows.append({"relation": relation, "role_a": a, "role_b": b, **s})
    return pd.DataFrame(rows)


def shuffled_frame(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """`frame` with each game's context (home spread, total) swapped among games of the same season: the null
    the real conditional table is compared against."""
    games = game_context(frame)
    sp, tt = shuffled_contexts(games, rng)
    new = pd.DataFrame({"game_id": games["game_id"], "h": sp, "t": tt})
    out = frame.drop(columns=["spread", "implied", "total"]).merge(new, on="game_id", how="left")
    sign = np.where(out["home"] == 1, 1.0, -1.0)
    out["spread"] = sign * out["h"]
    out["total"] = out["t"]
    out["implied"] = out["t"] / 2 + out["spread"] / 2
    return out.drop(columns=["h", "t"])


# --- the marginal side: does the projection already price the spread? -----------------------------------

MARGINAL_ROLES = ("QB1", "RB1", "WR1", "WR2", "TE1", "DST")


def naive_baseline(history) -> pd.DataFrame:
    """A projection that knows nothing about the game: each player's mean DK points over his three previous
    games (`gsis_id, season, week, naive`; NaN-free rows only). The comparison point for 'does the shipped
    projection already move with the spread?'."""
    pg = history.player_games[["gsis_id", "season", "week", "t", "dk"]]
    tg = history.team_games[["gsis_id", "season", "week", "t", "dk"]]
    both = pd.concat([pg, tg], ignore_index=True).sort_values(["gsis_id", "t"])
    both["naive"] = both.groupby("gsis_id")["dk"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )
    return both.dropna(subset=["naive"])[["gsis_id", "season", "week", "naive"]]


def marginal_effects(frame: pd.DataFrame, naive: pd.DataFrame | None = None) -> pd.DataFrame:
    """For each role, the change in (actual - projection) DK points per +10 points of spread, separately
    for the training seasons (<= `TRAIN_THROUGH`) and the test seasons, with game-clustered t-statistics;
    the same for the naive baseline when given (on the same rows). A projection that fully priced the
    spread would show ~0 for every role. Also `mean_pred_per10`: how much the shipped projection itself
    moves per +10 points."""
    rows = []
    f = frame if naive is None else frame.merge(naive, on=["gsis_id", "season", "week"], how="left")
    for role in MARGINAL_ROLES:
        sub = f[f["role"] == role]
        for era, mask in (("train", sub["season"] <= TRAIN_THROUGH), ("test", sub["season"] > TRAIN_THROUGH)):
            d = sub[mask]
            code = pd.factorize(d["game_id"])[0]
            row = {"role": role, "era": era, "n": len(d), "mean_pred": float(d["pred"].mean())}
            for label, proj in (("um", d["pred"]), ("naive", d["naive"] if "naive" in d else None)):
                if proj is None:
                    continue
                ok = proj.notna().to_numpy()
                slope_, se = clustered_slope(
                    (d["dk"] - proj).to_numpy()[ok], d["spread"].to_numpy()[ok], code[ok]
                )
                row[f"{label}_per10"], row[f"{label}_t"] = 10 * slope_, slope_ / se
            slope_, se = clustered_slope(d["pred"].to_numpy(), d["spread"].to_numpy(), code)
            row["mean_pred_per10"] = 10 * slope_
            if "z" in d:  # where the actual lands in the projection's own distribution (what the copula sees)
                slope_, se = clustered_slope(d["z"].to_numpy(), d["spread"].to_numpy(), code)
                row["z_per10"], row["z_t"] = 10 * slope_, slope_ / se
            rows.append(row)
    return pd.DataFrame(rows)


# --- the mechanism: the realized margin ---------------------------------------------------------------


def margin_mechanism(
    frame: pd.DataFrame, games: pd.DataFrame, pairs: list[tuple[str, str, str]]
) -> pd.DataFrame:
    """Per pair: the correlation of the surprises, the same after removing what the final margin of role A's
    team explains (a quadratic in the margin, from each of the two z-scores), and each role's correlation with
    that margin. The margin is only known after the game; this says where a pair's dependence comes from."""
    from dfs.sim.script_pbp import margins

    f = frame.merge(margins(games), on=["game_id", "team"], how="left")
    rows = []
    for relation, a, b in pairs:
        r = oriented_rows(f, relation, a, b).merge(
            f[f["role"] == a][["game_id", "team", "margin"]].drop_duplicates(["game_id", "team"]),
            on=["game_id", "team"],
        )
        x = np.column_stack([np.ones(len(r)), r["margin"], r["margin"] ** 2])

        def resid(y, x=x):
            return y - x @ np.linalg.lstsq(x, y, rcond=None)[0]

        za, zb = r["z_a"].to_numpy(), r["z_b"].to_numpy()
        rows.append(
            {
                "relation": relation,
                "role_a": a,
                "role_b": b,
                "n": len(r),
                "r": float(np.corrcoef(za, zb)[0, 1]),
                "r_given_margin": float(np.corrcoef(resid(za), resid(zb))[0, 1]),
                "r_a_margin": float(np.corrcoef(za, r["margin"])[0, 1]),
                "r_b_margin": float(np.corrcoef(zb, r["margin"])[0, 1]),
            }
        )
    return pd.DataFrame(rows)


# --- the tails: do the booms of a pair cluster more than a Gaussian copula says? ---------------------------


def tail_dependence(
    frame: pd.DataFrame,
    pairs: list[tuple[str, str, str]],
    cuts: tuple[float, ...] = (1.0, 1.5),
    *,
    n_boot: int = 300,
    seed: int = 0,
) -> pd.DataFrame:
    """For each pair and cut c: how often BOTH z-scores exceed c, against what a bivariate normal with the
    pair's own Pearson correlation gives, and the ratio. A Gaussian copula has no tail dependence beyond its
    correlation, so a ratio well above 1 means lineups built on the pair boom together more often than the
    simulator says. 90% interval by resampling games."""
    from scipy.stats import multivariate_normal

    rng = np.random.default_rng(seed)
    rows = []
    for relation, a, b in pairs:
        r = oriented_rows(frame, relation, a, b)
        rho = float(np.corrcoef(r["z_a"], r["z_b"])[0, 1])
        codes, _ = pd.factorize(r["game_id"])
        n_games = codes.max() + 1
        games_n = np.bincount(codes, minlength=n_games).astype(float)
        for c in cuts:
            hit = ((r["z_a"] > c) & (r["z_b"] > c)).to_numpy().astype(float)
            gauss = float(multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]]).cdf([-c, -c]))
            per_game = np.bincount(codes, weights=hit, minlength=n_games)
            draws = rng.integers(0, n_games, size=(n_boot, n_games))
            boot = per_game[draws].sum(axis=1) / games_n[draws].sum(axis=1)
            lo, hi = np.quantile(boot, [0.05, 0.95])
            obs = float(hit.mean())
            rows.append(
                {
                    "relation": relation,
                    "role_a": a,
                    "role_b": b,
                    "cut": c,
                    "n": len(r),
                    "rho": rho,
                    "observed": obs,
                    "gaussian": gauss,
                    "ratio": obs / gauss if gauss > 0 else float("nan"),
                    "ratio_lo": lo / gauss,
                    "ratio_hi": hi / gauss,
                }
            )
    return pd.DataFrame(rows)
