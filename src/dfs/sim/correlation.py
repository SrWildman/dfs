"""Part 1: empirical correlations between players, estimated from history.

The method, in four steps:

1. **Normal scores.** Every out-of-fold player-game's actual DK points are pushed through that player's own
   predicted distribution (`marginal.cdf_u`: the ratio quantiles at his projection), giving u in (0, 1); u is
   clipped to [0.005, 0.995] and mapped to z = Phi^-1(u). An actual of exactly 0 is placed at random inside
   the zero atom (seeded). A z-score is "how surprising was this player's game, given his projection" -- so a
   correlation of z is the correlation of *surprises*, which is what a copula on top of the projections needs.
2. **Roles.** Within a team-game each player has a role from prior games only (`roles.py`).
3. **Pairs.** For every pair of roles on the same team, and every pair across the two teams of a game, the
   Pearson correlation of z, with a game-level bootstrap 90% interval (games are resampled whole, so the two
   teams of a game and every pair inside a game stay together), shrunk toward 0 in Fisher-z space with a prior
   worth `SHRINK_PRIOR_N` observations. Pairs in different games are 0 by assumption (and checked).
4. **Game total (report only).** The same, within terciles of the game's Vegas total, flagging pairs whose
   terciles' (unshrunk) bootstrap intervals do not overlap. Nothing from this is shipped: the flagged pairs
   are what chance produces (docs/SIM.md), so only the pooled value ships and the simulator takes no game
   total.

Everything random is seeded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations_with_replacement
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtri

from dfs.paths import REPO_ROOT
from dfs.sim import marginal
from dfs.sim.roles import ROLES

SIM_DIR = REPO_ROOT / "models" / "sim"
CORRELATIONS_FILE = "correlations.csv"
DETAIL_FILE = "correlations_detail.csv"
META_FILE = "correlations_meta.json"

CSV_COLUMNS = ["relation", "role_a", "role_b", "total_bucket", "rho", "n", "ci_lo", "ci_hi"]
RELATIONS = ("same_team", "opp")
POOLED = "all"
TOTAL_BUCKETS = ("low", "mid", "high")
U_CLIP = (0.005, 0.995)
SHRINK_PRIOR_N = 200
N_BOOT = 1000
CI_LEVEL = 0.90
CROSS_GAME_LIMIT = 0.03


class CorrelationError(Exception):
    """The correlation table is missing or malformed."""


# --- normal scores ---------------------------------------------------------------------------------------


def normal_scores(frame: pd.DataFrame, *, tables: pd.DataFrame | None = None, seed: int = 0) -> pd.Series:
    """z for every row of `frame` (`position, pred, dk`), aligned to its index."""
    rng = np.random.default_rng(seed)
    z = pd.Series(np.nan, index=frame.index)
    for position in sorted(frame["position"].unique()):
        sel = frame["position"] == position
        m = marginal.marginals(position, frame.loc[sel, "pred"].to_numpy(), tables=tables)
        u = marginal.cdf_u(m, frame.loc[sel, "dk"].to_numpy(), rng)
        z[sel] = ndtri(np.clip(u, *U_CLIP))
    return z


# --- shrinkage --------------------------------------------------------------------------------------------


def shrink(r, n, prior_n: float = SHRINK_PRIOR_N):
    """Shrink a correlation toward 0 in Fisher-z space. A correlation from `n` pairs has Fisher-z precision
    n - 3; a prior worth `prior_n` observations at 0 gives the posterior mean
    z * (n - 3) / (n - 3 + prior_n)."""
    r = np.clip(np.asarray(r, dtype=float), -0.999999, 0.999999)
    weight = np.maximum(np.asarray(n, dtype=float) - 3.0, 0.0)
    weight = weight / (weight + prior_n)
    return np.tanh(np.arctanh(r) * weight)


# --- pair data --------------------------------------------------------------------------------------------


def role_pairs() -> list[tuple[str, str]]:
    """Every unordered pair of roles, in `ROLES` order (a role paired with itself is only possible across
    teams)."""
    return list(combinations_with_replacement(ROLES, 2))


def pair_rows(frame: pd.DataFrame, relation: str, a: str, b: str) -> pd.DataFrame:
    """The (game, z_a, z_b) rows for roles `a` and `b` in `relation` ('same_team' or 'opp'). Needs `z`."""
    cols = ["game_id", "team", "opp", "z"]
    left = frame.loc[frame["role"] == a, cols]
    right = frame.loc[frame["role"] == b, cols]
    if relation == "same_team":
        if a == b:
            return pd.DataFrame(columns=["game_id", "za", "zb"])
        m = left.merge(right, on=["game_id", "team"], suffixes=("_a", "_b"))
    else:
        m = left.merge(right, left_on=["game_id", "opp"], right_on=["game_id", "team"], suffixes=("_a", "_b"))
        if a == b:
            m = m[m["team_a"] < m["team_b"]]  # each game's pair once, not once per orientation
    return pd.DataFrame({"game_id": m["game_id"], "za": m["z_a"], "zb": m["z_b"]})


def _game_sums(pairs: pd.DataFrame, code: pd.Series, n_games: int) -> np.ndarray:
    """Per-game sufficient statistics (n, sa, sb, saa, sbb, sab), G x 6, zeros for games without the pair."""
    g = pairs["game_id"].map(code).to_numpy()
    za, zb = pairs["za"].to_numpy(), pairs["zb"].to_numpy()
    cols = [np.ones(len(g)), za, zb, za * za, zb * zb, za * zb]
    return np.column_stack([np.bincount(g, weights=c, minlength=n_games) for c in cols])


def _pearson(t: np.ndarray) -> np.ndarray:
    """Pearson r from summed statistics (rows of n, sa, sb, saa, sbb, sab)."""
    n, sa, sb, saa, sbb, sab = t.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return (n * sab - sa * sb) / np.sqrt((n * saa - sa**2) * (n * sbb - sb**2))


# --- the fit ----------------------------------------------------------------------------------------------


@dataclass
class Fit:
    """A fitted table. `detail` has one row per (relation, role pair, total bucket), raw and shrunk, with the
    tercile comparison (report only); `shipped` is `correlations.csv`: the pooled (`all`) rows only."""

    detail: pd.DataFrame
    shipped: pd.DataFrame
    cutoffs: tuple[float, float]
    meta: dict = field(default_factory=dict)
    scored: pd.DataFrame | None = None  # the fitted frame, with its z column

    def lookup(self) -> CorrelationLookup:
        return CorrelationLookup(self.shipped)


def tercile_cutoffs(frame: pd.DataFrame) -> tuple[float, float]:
    """The 1/3 and 2/3 quantiles of the game total over the distinct games in `frame`."""
    totals = frame.drop_duplicates("game_id")["total"]
    lo, hi = np.quantile(totals, [1 / 3, 2 / 3])
    return float(lo), float(hi)


def total_bucket(total: float, cutoffs: tuple[float, float]) -> str:
    """'low' (below the first cutoff), 'mid' or 'high' (at or above the second)."""
    return "low" if total < cutoffs[0] else ("mid" if total < cutoffs[1] else "high")


def fit_correlations(
    frame: pd.DataFrame,
    *,
    tables: pd.DataFrame | None = None,
    seed: int = 0,
    n_boot: int = N_BOOT,
    prior_n: float = SHRINK_PRIOR_N,
) -> Fit:
    """Fit every role-pair correlation on `frame` (`roles.build_frame`'s output, restricted by the caller to
    the seasons to fit on). `tables` are the distribution tables the normal scores are taken through (default
    the shipped ones)."""
    frame = frame.copy()
    frame["z"] = normal_scores(frame, tables=tables, seed=seed)
    cutoffs = tercile_cutoffs(frame)
    games = frame.drop_duplicates("game_id")[["game_id", "total"]].reset_index(drop=True)
    code = pd.Series(np.arange(len(games)), index=games["game_id"])
    bucket_of_game = np.array([total_bucket(t, cutoffs) for t in games["total"]])
    rng = np.random.default_rng(seed + 1)
    counts = rng.multinomial(len(games), np.full(len(games), 1 / len(games)), size=n_boot).astype(float)
    lo_q, hi_q = (1 - CI_LEVEL) / 2, 1 - (1 - CI_LEVEL) / 2

    rows = []
    for relation in RELATIONS:
        for a, b in role_pairs():
            pairs = pair_rows(frame, relation, a, b)
            sums = _game_sums(pairs, code, len(games)) if len(pairs) else np.zeros((len(games), 6))
            for bucket in (POOLED, *TOTAL_BUCKETS):
                s = sums if bucket == POOLED else sums * (bucket_of_game == bucket)[:, None]
                n = int(s[:, 0].sum())
                if n < 5:
                    rows.append((relation, a, b, bucket, n, np.nan, np.nan, np.nan))
                    continue
                r = float(_pearson(s.sum(axis=0, keepdims=True))[0])
                boot = _pearson(counts @ s)
                lo, hi = np.nanquantile(boot, [lo_q, hi_q])
                rows.append((relation, a, b, bucket, n, r, float(lo), float(hi)))
    detail = pd.DataFrame(
        rows, columns=["relation", "role_a", "role_b", "total_bucket", "n", "r_raw", "raw_lo", "raw_hi"]
    )
    detail["rho"] = shrink(detail["r_raw"], detail["n"], prior_n)
    detail["ci_lo"] = shrink(detail["raw_lo"], detail["n"], prior_n)
    detail["ci_hi"] = shrink(detail["raw_hi"], detail["n"], prior_n)
    detail["conditional"] = _flag_conditional(detail)
    detail = detail.dropna(subset=["rho"]).reset_index(drop=True)
    shipped = detail.loc[detail["total_bucket"] == POOLED, CSV_COLUMNS]
    shipped = shipped.reset_index(drop=True)
    meta = {
        "rows": int(len(frame)),
        "games": int(len(games)),
        "seasons": [int(frame["season"].min()), int(frame["season"].max())],
        "total_cutoffs": list(cutoffs),
        "shrink_prior_n": prior_n,
        "n_boot": n_boot,
        "ci_level": CI_LEVEL,
        "u_clip": list(U_CLIP),
        "seed": seed,
    }
    return Fit(detail, shipped, cutoffs, meta, frame)


def _flag_conditional(detail: pd.DataFrame) -> pd.Series:
    """True on every row of a (relation, pair) whose total terciles differ significantly: some two terciles'
    unshrunk 90% intervals do not overlap."""
    flags = {}
    for key, g in detail[detail["total_bucket"].isin(TOTAL_BUCKETS)].groupby(
        ["relation", "role_a", "role_b"]
    ):
        g = g.dropna(subset=["raw_lo", "raw_hi"])
        lo, hi = g["raw_lo"].to_numpy(), g["raw_hi"].to_numpy()
        flags[key] = bool(any(hi[i] < lo[j] or hi[j] < lo[i] for i in range(len(g)) for j in range(i)))
    keys = list(zip(detail["relation"], detail["role_a"], detail["role_b"], strict=True))
    is_bucket = detail["total_bucket"].isin(TOTAL_BUCKETS)
    return pd.Series(
        [flags.get(k, False) and b for k, b in zip(keys, is_bucket, strict=True)], index=detail.index
    )


# --- the shipped table ------------------------------------------------------------------------------------


class CorrelationLookup:
    """Pooled role-pair correlations. Symmetric in the two roles; a pair never observed is 0."""

    def __init__(self, table: pd.DataFrame):
        missing = [c for c in CSV_COLUMNS if c not in table.columns]
        if missing:
            raise CorrelationError(f"correlation table is missing columns {missing}")
        if (table["total_bucket"] != POOLED).any():
            raise CorrelationError(
                f"correlation table has rows other than total_bucket={POOLED!r} -- run `dfs sim fit`"
            )
        self._order = {role: i for i, role in enumerate(ROLES)}
        self._rho = {
            self._key(rel, a, b): float(rho)
            for rel, a, b, rho in zip(
                table["relation"], table["role_a"], table["role_b"], table["rho"], strict=True
            )
        }

    def _key(self, relation: str, a: str, b: str) -> tuple[str, str, str]:
        if self._order[a] > self._order[b]:
            a, b = b, a
        return (relation, a, b)

    def has(self, relation: str, a: str, b: str) -> bool:
        return self._key(relation, a, b) in self._rho

    def rho(self, relation: str, a: str, b: str) -> float:
        """The correlation for the pair of roles ('same_team' or 'opp'); 0 when never observed."""
        return self._rho.get(self._key(relation, a, b), 0.0)


def save_fit(fit: Fit, directory: Path = SIM_DIR, extra_meta: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CORRELATIONS_FILE
    fit.shipped.to_csv(path, index=False, float_format="%.5f")
    fit.detail.to_csv(directory / DETAIL_FILE, index=False, float_format="%.5f")
    meta = fit.meta | (extra_meta or {})
    (directory / META_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    load_correlations.cache_clear()
    return path


@lru_cache(maxsize=4)
def load_correlations(directory: Path = SIM_DIR) -> CorrelationLookup:
    path = directory / CORRELATIONS_FILE
    if not path.exists():
        raise CorrelationError(f"{path} not found -- run `dfs sim fit`")
    return CorrelationLookup(pd.read_csv(path))


# --- checks -----------------------------------------------------------------------------------------------


def cross_game_check(frame: pd.DataFrame, *, seed: int = 0, partners: int = 1) -> pd.DataFrame:
    """Do players in DIFFERENT games of the same week move together? For every role pair, each row of role
    `a` is matched with `partners` randomly chosen role-`b` rows from the same (season, week) but another
    game, and the Pearson r of z is taken. Needs `z`. Returns one row per role pair: n, r, and the
    independence noise floor E|r| = sqrt(2 / (pi n))."""
    rng = np.random.default_rng(seed)
    week = frame["season"] * 100 + frame["week"]
    out = []
    by_role = {r: frame[frame["role"] == r] for r in ROLES}
    weeks = {r: week[f.index].to_numpy() for r, f in by_role.items()}
    for a, b in combinations_with_replacement(ROLES, 2):
        fa, fb = by_role[a], by_role[b]
        za, zb = [], []
        wb = pd.Series(np.arange(len(fb))).groupby(weeks[b]).apply(np.asarray)
        gb = fb["game_id"].to_numpy()
        zbv = fb["z"].to_numpy()
        for w, game, z in zip(weeks[a], fa["game_id"], fa["z"], strict=True):
            pool = wb.get(w)
            if pool is None:
                continue
            pool = pool[gb[pool] != game]
            if len(pool) == 0:
                continue
            pick = rng.choice(pool, size=min(partners, len(pool)), replace=False)
            za.extend([z] * len(pick))
            zb.extend(zbv[pick])
        n = len(za)
        if n < 3:
            continue
        r = float(np.corrcoef(za, zb)[0, 1])
        out.append({"role_a": a, "role_b": b, "n": n, "r": r, "noise_floor": float(np.sqrt(2 / (np.pi * n)))})
    return pd.DataFrame(out)


# The figures widely cited for DraftKings, as the task states them (approximate), and the statement the repo's
# strategy review makes about QB vs the opposing defense.
LITERATURE = [
    ("same_team", "QB1", "WR1", "+0.4 to +0.5", 0.45),
    ("same_team", "QB1", "TE1", "+0.3", 0.30),
    ("same_team", "QB1", "RB1", "near +0.1", 0.10),
    ("opp", "QB1", "QB1", "+0.2", 0.20),
    ("opp", "QB1", "DST", "-0.46 (strategy review)", -0.46),
]


def literature_comparison(fit: Fit) -> pd.DataFrame:
    """Our pooled z-score correlation next to the cited figure, plus the same pair's correlation of raw DK
    points (what the literature measures: it includes whatever the projections already explain)."""
    frame = fit.scored
    raw = frame.assign(z=frame["dk"])
    rows = []
    for relation, a, b, cited, point in LITERATURE:
        d = fit.detail[
            (fit.detail["relation"] == relation)
            & (fit.detail["role_a"] == a)
            & (fit.detail["role_b"] == b)
            & (fit.detail["total_bucket"] == POOLED)
        ].iloc[0]
        pr = pair_rows(raw, relation, a, b)
        rows.append(
            {
                "relation": relation,
                "pair": f"{a}-{b}",
                "cited": cited,
                "cited_point": point,
                "n": int(d["n"]),
                "rho": d["rho"],
                "r_raw": d["r_raw"],
                "ci_lo": d["ci_lo"],
                "ci_hi": d["ci_hi"],
                "r_points": float(np.corrcoef(pr["za"], pr["zb"])[0, 1]),
            }
        )
    return pd.DataFrame(rows)
