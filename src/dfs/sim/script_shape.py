"""Team shape and player role as modifiers on a pair's correlation.

`script.py` asked whether a correlation moves with the SPREAD (it does not, usefully). This asks the
question a football player asks next: does it move with WHO THE PLAYERS ARE -- a receiving back moves with
his QB and a two-down back does not, a target hog moves with his QB more than a decoy, a running QB's
receivers see less of him, a pass-heavy offense ties the whole passing game together.

Features, all from the player's or the team's three previous games only (continuing across the season
boundary, the convention of `roles.py`; nothing from the game itself):

- `tshare`  -- the player's share of his team's targets (RB, WR and TE rows; nflverse `target_share`);
- `qb_rush` -- the QB's share of his team's carries;
- `proe`    -- the team's pass rate over expected (nflfastR `pass_oe`, play-by-play);
- `conc`    -- the share of the team's targets that went to its top target.

Route participation is not in the weekly data this repo loads (nflverse's weekly stats carry targets and
target share, not routes), so target share stands in for it.

The test is `script.py`'s: a tercile table (cut on the training seasons) of each modified pair's correlation,
shrunk toward the POOLED value, a game-clustered slope t-statistic, a control that shuffles the feature among
rows of the same role and season, and the held-out pair likelihood of the table against the pooled value.
Everything is a pure function over frames; nothing changes `correlations.csv`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.sim.correlation import SHRINK_PRIOR_N
from dfs.sim.roles import ROLES
from dfs.sim.script import (
    bivariate_loglik,
    clustered_slope,
    shrink_toward,
    summarize_gain,
)

FEATURES = ("tshare", "qb_rush", "proe", "conc")
TERCILES = ("low", "mid", "high")
FEATURE_LABELS = {
    "tshare": "target share of the receiver / back",
    "qb_rush": "the QB's share of his team's carries",
    "proe": "the team's pass rate over expected",
    "conc": "target concentration (the top target's share)",
}
# (relation, role a, role b, the side whose feature it is, feature). Role pairs are in ROLES order (as
# `script.family_pairs`); `side` "a" or "b" says whose row carries the feature; `proe` and `conc` are the
# team's.
TESTS = (
    ("same_team", "QB1", "RB1", "b", "tshare"),
    ("same_team", "QB1", "RB2", "b", "tshare"),
    ("same_team", "QB1", "WR1", "b", "tshare"),
    ("same_team", "QB1", "WR2", "b", "tshare"),
    ("same_team", "QB1", "WR3", "b", "tshare"),
    ("same_team", "QB1", "TE1", "b", "tshare"),
    ("opp", "QB1", "WR1", "b", "tshare"),
    ("opp", "QB1", "TE1", "b", "tshare"),
    ("opp", "QB1", "RB1", "b", "tshare"),
    ("same_team", "RB1", "WR1", "a", "tshare"),
    ("same_team", "QB1", "RB1", "a", "qb_rush"),
    ("same_team", "QB1", "WR1", "a", "qb_rush"),
    ("same_team", "QB1", "WR2", "a", "qb_rush"),
    ("same_team", "QB1", "TE1", "a", "qb_rush"),
    ("same_team", "QB1", "RB1", "a", "proe"),
    ("same_team", "QB1", "WR1", "a", "proe"),
    ("same_team", "QB1", "WR2", "a", "proe"),
    ("same_team", "QB1", "TE1", "a", "proe"),
    ("same_team", "WR1", "WR2", "a", "conc"),
    ("same_team", "QB1", "WR1", "a", "conc"),
    ("same_team", "QB1", "WR2", "a", "conc"),
    ("same_team", "QB1", "TE1", "a", "conc"),
)
# The family a "role" modifier ships as, if anything does: a QB's correlation with each receiver or back, by
# the receiver's share of the team's targets.
RECEIVER_TESTS = frozenset(
    t for t in TESTS if t[0] == "same_team" and t[1] == "QB1" and t[3] == "b" and t[4] == "tshare"
)
NAMED = (
    ("same_team", "QB1", "RB1", "b", "tshare", "QB1 with RB1, by the back's target share"),
    ("same_team", "QB1", "TE1", "b", "tshare", "QB1 with TE1, by the tight end's target share"),
    ("same_team", "QB1", "WR1", "b", "tshare", "QB1 with WR1, by his target share"),
    ("same_team", "QB1", "WR1", "a", "conc", "QB1 with WR1, by target concentration"),
    ("same_team", "QB1", "RB1", "a", "qb_rush", "QB1 with RB1, by the QB's rush share"),
    ("same_team", "QB1", "WR1", "a", "proe", "QB1 with WR1, by the team's PROE"),
    ("same_team", "WR1", "WR2", "a", "conc", "WR1 with WR2, by target concentration"),
)


# --- the features -----------------------------------------------------------------------------------------


def _prior3(df: pd.DataFrame, key: str, col: str) -> pd.Series:
    """Mean of `col` over the three games before the row's, by `key` (frame sorted by key, t)."""
    return df.groupby(key)[col].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())


def shape_features(frame: pd.DataFrame, history, proe: pd.DataFrame) -> pd.DataFrame:
    """`frame` (`roles.build_frame`'s output) plus the four features. `history` is `model.history.History`,
    `proe` is `script_pbp.team_game_proe`."""
    pg = history.player_games.sort_values(["gsis_id", "t"]).copy()
    team_carries = pg.groupby(["game_id", "team"])["carries"].transform("sum")
    team_targets = pg.groupby(["game_id", "team"])["targets"].transform("sum")
    pg["rush_share"] = (pg["carries"] / team_carries.replace(0, np.nan)).fillna(0.0)
    pg["tgt_share"] = pg["target_share"].where(pg["target_share"].notna(), pg["targets"] / team_targets)
    pg["tshare"] = _prior3(pg, "gsis_id", "tgt_share")
    pg["qb_rush"] = _prior3(pg, "gsis_id", "rush_share")

    share = pg["targets"] / team_targets.replace(0, np.nan)
    top = share.groupby([pg["game_id"], pg["team"]]).transform("max")
    teams = (
        pg.assign(top=top)
        .drop_duplicates(["game_id", "team"])[["game_id", "team", "t", "top"]]
        .merge(proe, on=["game_id", "team"], how="left")
        .sort_values(["team", "t"])
    )
    teams["conc"] = _prior3(teams, "team", "top")
    teams["proe"] = _prior3(teams, "team", "proe")
    out = frame.drop(columns=[c for c in FEATURES if c in frame.columns])
    out = out.merge(
        pg[["gsis_id", "season", "week", "tshare", "qb_rush"]], on=["gsis_id", "season", "week"], how="left"
    )
    return out.merge(teams[["game_id", "team", "conc", "proe"]], on=["game_id", "team"], how="left")


# --- one test's rows ---------------------------------------------------------------------------------------


def test_rows(frame: pd.DataFrame, test: tuple) -> pd.DataFrame:
    """`game_id, season, z_a, z_b, x`: the pair's rows with the feature of the named side (NaN dropped)."""
    relation, a, b, side, feature = test[:5]
    left = frame.loc[frame["role"] == a, ["game_id", "season", "team", "opp", "z", feature]]
    right = frame.loc[frame["role"] == b, ["game_id", "team", "z", feature]]
    if relation == "same_team":
        m = left.merge(right, on=["game_id", "team"], suffixes=("_a", "_b"))
    else:
        m = left.merge(right, left_on=["game_id", "opp"], right_on=["game_id", "team"], suffixes=("_a", "_b"))
    out = pd.DataFrame(
        {
            "game_id": m["game_id"],
            "season": m["season"],
            "z_a": m["z_a"],
            "z_b": m["z_b"],
            "x": m[f"{feature}_{side}"],
        }
    )
    return out.dropna(subset=["x"]).reset_index(drop=True)


def cutoffs_for(train: pd.DataFrame, test: tuple) -> tuple[float, float]:
    x = test_rows(train, test)["x"]
    lo, hi = np.quantile(x, [1 / 3, 2 / 3])
    return float(lo), float(hi)


def tercile(x, cuts: tuple[float, float]) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return np.select([x < cuts[0], x < cuts[1]], ["low", "mid"], "high")


def shuffled_features(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """`frame` with each feature permuted among rows of the same role and season (player features) or among
    team-games of the same season (team features): the link between who a player is and how his game went is
    broken, the distribution of the features is kept."""
    out = frame.copy()
    for feature in ("tshare", "qb_rush"):
        for _, idx in out.groupby(["role", "season"]).groups.items():
            idx = np.asarray(idx)
            out.loc[idx, feature] = out.loc[rng.permutation(idx), feature].to_numpy()
    teams = out.drop_duplicates(["game_id", "team"])[["game_id", "team", "season", "conc", "proe"]]
    for _, idx in teams.groupby("season").groups.items():
        idx = np.asarray(idx)
        teams.loc[idx, ["conc", "proe"]] = teams.loc[rng.permutation(idx), ["conc", "proe"]].to_numpy()
    out = out.drop(columns=["conc", "proe"]).merge(
        teams[["game_id", "team", "conc", "proe"]], on=["game_id", "team"]
    )
    return out


# --- the scan ----------------------------------------------------------------------------------------------


@dataclass
class ShapeScan:
    table: pd.DataFrame  # one row per test: n, pooled r, slope t, tercile r with n, adjusted p
    buckets: pd.DataFrame  # test key + bucket: n, r
    cuts: dict = field(default_factory=dict)  # test -> (lo, hi)
    null_max_t: np.ndarray | None = None


def _t(rows: pd.DataFrame) -> float:
    if len(rows) < 100:
        return float("nan")
    code = pd.factorize(rows["game_id"])[0]
    x = rows["x"].to_numpy(float)
    slope, se = clustered_slope(
        rows["z_a"].to_numpy() * rows["z_b"].to_numpy(), (x - x.mean()) / x.std(), code
    )
    return slope / se if se and np.isfinite(se) and se > 0 else float("nan")


def scan(frame: pd.DataFrame, *, tests=TESTS, n_shuffle: int = 100, seed: int = 0) -> ShapeScan:
    """Slope t and tercile correlations of every test on `frame` (the training seasons), the adjusted p read
    against the largest |t| over all tests in shuffled copies."""
    rng = np.random.default_rng(seed)
    real = {test: test_rows(frame, test) for test in tests}
    t_real = {test: _t(rows) for test, rows in real.items()}
    null_max = np.zeros(n_shuffle)
    for k in range(n_shuffle):
        sh = shuffled_features(frame, rng)
        null_max[k] = np.nanmax([abs(_t(test_rows(sh, test))) for test in tests])
    rows_out, bucket_rows, cuts = [], [], {}
    for test in tests:
        rows = real[test]
        if len(rows) < 100:
            continue
        cuts[test] = cut = cutoffs_for(frame, test)
        labels = tercile(rows["x"], cut)
        r_all = float(np.corrcoef(rows["z_a"], rows["z_b"])[0, 1])
        row = {
            "relation": test[0],
            "role_a": test[1],
            "role_b": test[2],
            "side": test[3],
            "feature": test[4],
            "n": len(rows),
            "r_pooled": r_all,
            "t": t_real[test],
            "p_adj": float((null_max >= abs(t_real[test])).mean()) if n_shuffle else float("nan"),
            "cut_lo": cut[0],
            "cut_hi": cut[1],
        }
        for name in TERCILES:
            sub = rows[labels == name]
            r = float(np.corrcoef(sub["z_a"], sub["z_b"])[0, 1]) if len(sub) > 30 else float("nan")
            row[f"r_{name}"] = r
            bucket_rows.append((*test[:5], name, len(sub), r))
        rows_out.append(row)
    buckets = pd.DataFrame(
        bucket_rows, columns=["relation", "role_a", "role_b", "side", "feature", "bucket", "n", "r"]
    )
    return ShapeScan(pd.DataFrame(rows_out), buckets, cuts, null_max)


# --- the conditional table ----------------------------------------------------------------------------------


class ShapeLookup:
    """Tercile deviations from the pooled value, as a layer over a `CorrelationLookup` (`base`), like
    `script.ContextLookup`: a tercile's correlation is shrunk toward the pooled value (prior `prior_n`
    observations) and its Fisher-z difference from the pooled value is the deviation. `rho_array` and `rho`
    read the features of the two players (`xa`, `xb`); `use` limits the features applied."""

    def __init__(
        self,
        base,
        buckets: pd.DataFrame,
        cuts: dict,
        *,
        prior_n: float = SHRINK_PRIOR_N,
        use: tuple[str, ...] = FEATURES,
        tests: set | None = None,
    ):
        self.base, self.cuts, self.use, self.prior_n = base, cuts, use, prior_n
        self._dev: dict[tuple, float] = {}
        for rel, a, b, side, feat, bucket, n, r in buckets.itertuples(index=False):
            test = (rel, a, b, side, feat)
            if feat not in use or not np.isfinite(r) or (tests is not None and test not in tests):
                continue
            centre = base.rho(rel, a, b)
            shrunk = float(shrink_toward(r, n, centre, prior_n))
            self._dev[(*test, bucket)] = float(np.arctanh(np.clip(shrunk, -0.999999, 0.999999))) - float(
                np.arctanh(np.clip(centre, -0.999999, 0.999999))
            )

    def rho_test(self, test: tuple, x) -> np.ndarray:
        """The pair's correlation for observations whose modifying feature is `x`, this test's deviation "
        "only."""
        relation, a, b = test[:3]
        z = float(np.arctanh(np.clip(self.base.rho(relation, a, b), -0.999999, 0.999999)))
        labels = tercile(x, self.cuts[test])
        dev = np.array([self._dev.get((*test[:5], str(lab)), 0.0) for lab in labels])
        return np.tanh(z + dev)

    def tests_for(self, relation: str, a: str, b: str) -> list[tuple]:
        return [t for t in self.cuts if t[:3] == (relation, a, b) and t[4] in self.use]

    def rho_array(self, relation: str, a: str, b: str, xa: dict, xb: dict, n: int) -> np.ndarray:
        """Correlation for `n` observations of the pair `(a, b)` in `ROLES` order; `xa[feature]` /
        `xb[feature]` are arrays of the features of role a's and role b's players."""
        z = np.full(n, float(np.arctanh(np.clip(self.base.rho(relation, a, b), -0.999999, 0.999999))))
        for test in self.tests_for(relation, a, b):
            side, feat = test[3], test[4]
            x = (xa if side == "a" else xb).get(feat)
            if x is None:
                continue
            labels = tercile(x, self.cuts[test])
            z = z + np.array(
                [
                    self._dev.get((*test, str(lab)), 0.0) if np.isfinite(v) else 0.0
                    for lab, v in zip(labels, x, strict=True)
                ]
            )
        return np.tanh(z)

    def pooled_rho(self, relation: str, a: str, b: str) -> float:
        return self.base.rho(relation, a, b)

    def rho(self, relation: str, a: str, b: str, xa: dict | None = None, xb: dict | None = None) -> float:
        """One pair's correlation (`xa`, `xb`: the two players' feature dicts; `ROLES` order is enforced)."""
        order = {r: i for i, r in enumerate(ROLES)}
        xa, xb = xa or {}, xb or {}
        if order[a] > order[b]:
            a, b, xa, xb = b, a, xb, xa
        arrays = [{k: np.array([v], dtype=float) for k, v in x.items()} for x in (xa, xb)]
        return float(self.rho_array(relation, a, b, arrays[0], arrays[1], 1)[0])

    def rho_pair(self, relation: str, role_a: str, role_b: str, spec_a, spec_b) -> float:
        """`simulate.correlation_matrix`'s hook, reading the players' features off their `PlayerSpec`s."""
        xa = {f: getattr(spec_a, f) for f in FEATURES if getattr(spec_a, f, None) is not None}
        xb = {f: getattr(spec_b, f) for f in FEATURES if getattr(spec_b, f, None) is not None}
        return self.rho(relation, role_a, role_b, xa, xb)


# --- the held-out score
# ---------------------------------------------------------------------------------------


def shape_gain(test_frame: pd.DataFrame, lookup: ShapeLookup, tests=TESTS) -> pd.DataFrame:
    """Per test and game, the held-out log-likelihood gain of the tercile table over the pooled value
    (`relation, role_a, role_b, side, feature, game_id, n, gain`). Each test moves the correlation with
    its own feature only, so tests are scored one at a time."""
    parts = []
    for test in tests:
        rows = test_rows(test_frame, test)
        if len(rows) < 30 or test not in lookup.cuts:
            continue
        rel, a, b, side, feat = test[:5]
        cond = lookup.rho_test(test, rows["x"].to_numpy(float))
        za, zb = rows["z_a"].to_numpy(), rows["z_b"].to_numpy()
        gain = bivariate_loglik(za, zb, cond) - bivariate_loglik(za, zb, lookup.pooled_rho(rel, a, b))
        g = pd.DataFrame({"game_id": rows["game_id"].to_numpy(), "gain": gain, "n": 1})
        parts.append(
            g.groupby("game_id", as_index=False)
            .sum()
            .assign(relation=rel, role_a=a, role_b=b, side=side, feature=feat)
        )
    cols = ["relation", "role_a", "role_b", "side", "feature", "game_id", "n", "gain"]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


def summarize(long: pd.DataFrame, features: tuple[str, ...] | None = None, tests: set | None = None) -> dict:
    """Total held-out gain (per 1,000 pair-observations, game-clustered SE) over the chosen features / "
    "tests."""
    sel = long
    if features is not None:
        sel = sel[sel["feature"].isin(features)]
    if tests is not None:
        keys = list(
            zip(sel["relation"], sel["role_a"], sel["role_b"], sel["side"], sel["feature"], strict=True)
        )
        sel = sel[[k in tests for k in keys]]
    return summarize_gain(sel)
