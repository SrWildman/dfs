"""Is the Gaussian copula the right dependence for a game? Against a t-copula and a shared game factor.

docs/SIM.md ("Tails: the one thing the copula misses") found that stack booms co-occur 30-45% more often
than a Gaussian copula with the same correlation says. This asks whether a heavier-tailed dependence fixes
it, judged the way the shape table was: fit on the training seasons only, score on seasons held out.

The unit is a game. Every game's normal scores `z` (`correlation.normal_scores`, the PIT of the actual through
the player's own projected distribution) for the roles on the field form one vector, one entry per
(team, role), and the correlation matrix `R` of the game is assembled from the pooled pair table exactly as
`simulate.correlation_matrix` does (and repaired the same way). Three dependence models share that `R`:

- **Gaussian** (shipped): `z ~ N(0, R)`.
- **t-copula**: `x ~ t_nu(0, R)`, `u = T_nu(x)`. One chi-square mixing draw per game scales everyone in the
game
  together, so big games are big for all of them: tail dependence the Gaussian does not have. `R` is the
  shipped table, or the Kendall-tau version (`sin(pi tau / 2)`, the usual estimator for an elliptical copula).
- **Game factor**: `z | s ~ N(0, R(m))`, `R(m) = (1 - m) I + m R`, `m = exp(tau s - tau^2 / 2)` with a shared
  game-level `s ~ N(0, 1)` (a shootout game ties everyone together, a dull one nobody), `m` capped where
  `R(m)` stops being positive semi-definite. The correlation scale is random per game, margins stay N(0, 1).

Scores: the log-likelihood of the copula per game (a proper score of the whole joint), and the rate of joint
top-decile events (QB1 + WR1 of one team; plus the other team's WR1) in held-out games against what each
fitted model says for the same games. `nu` and `tau` are chosen on the TRAINING seasons.

    python -m dfs.sim.copula_test --frame frame.parquet
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import gammaln, ndtr, ndtri

from dfs.sim import shape_retest
from dfs.sim.correlation import SHRINK_PRIOR_N, pair_rows, role_pairs, shrink
from dfs.sim.roles import ROLES
from dfs.sim.simulate import repair_correlation

TRAIN_THROUGH = 2023
NU_GRID = (3, 4, 5, 6, 8, 10, 15, 25, 50, 100)
# (tau, base): the game factor's spread and its base level. The base is below 1 because a game's `R` is
# near-singular once repaired, so `R(m)` is only positive semi-definite for m <= ~1: the factor can dampen the
# shipped correlations in some games, and can only reach them (not exceed them) in the others.
FACTOR_GRID = tuple((tau, base) for tau in (0.0, 0.1, 0.2, 0.3, 0.5) for base in (0.7, 0.8, 0.9, 1.0))
TOP = 0.90
Z_TOP = float(ndtri(TOP))
GH_NODES = 24


# --- the pair tables ----------------------------------------------------------------------------------------


def pair_tables(train: pd.DataFrame, prior_n: float = SHRINK_PRIOR_N) -> dict:
    """`{"pearson": {key: rho}, "kendall": {key: rho}}`, key `(relation, role_a, role_b)` in `ROLES` order,
    both shrunk toward 0 with the shipped prior. Pairs with fewer than 30 observations are left out (0)."""
    out = {"pearson": {}, "kendall": {}}
    for relation in ("same_team", "opp"):
        for a, b in role_pairs():
            rows = pair_rows(train, relation, a, b)
            if len(rows) < 30:
                continue
            r = float(np.corrcoef(rows["za"], rows["zb"])[0, 1])
            tau = float(stats.kendalltau(rows["za"], rows["zb"])[0])
            out["pearson"][(relation, a, b)] = float(shrink(r, len(rows), prior_n))
            out["kendall"][(relation, a, b)] = float(shrink(np.sin(np.pi * tau / 2), len(rows), prior_n))
    return out


# --- games as vectors ---------------------------------------------------------------------------------------


@dataclass
class Game:
    game_id: str
    season: int
    z: np.ndarray
    teams: list[str]
    roles: list[str]
    r_pearson: np.ndarray  # repaired
    r_kendall: np.ndarray  # repaired


def _matrix(teams: list[str], roles: list[str], table: dict) -> np.ndarray:
    n = len(roles)
    order = {r: i for i, r in enumerate(ROLES)}
    r = np.eye(n)
    for i in range(n):
        for j in range(i):
            a, b = roles[i], roles[j]
            if order[a] > order[b]:
                a, b = b, a
            relation = "same_team" if teams[i] == teams[j] else "opp"
            r[i, j] = r[j, i] = table.get((relation, a, b), 0.0)
    return repair_correlation(r)[0]


PASSING_CORE = ("QB1", "WR1", "WR2", "WR3", "TE1")


def build_games(f: pd.DataFrame, tables: dict, roles: tuple[str, ...] | None = None) -> list[Game]:
    """One `Game` per game of `f` (rows with `z`), one entry per (team, role) (a second player in a role is
    dropped, as the pair table has no value for two of one role on a team). `roles` limits the vector."""
    g = f.drop_duplicates(["game_id", "team", "role"])
    if roles is not None:
        g = g[g["role"].isin(roles)]
    games = []
    for gid, sub in g.groupby("game_id", sort=False):
        teams, roles = sub["team"].tolist(), sub["role"].tolist()
        if len(roles) < 4:
            continue
        games.append(
            Game(
                gid,
                int(sub["season"].iloc[0]),
                sub["z"].to_numpy(float),
                teams,
                roles,
                _matrix(teams, roles, tables["pearson"]),
                _matrix(teams, roles, tables["kendall"]),
            )
        )
    return games


# --- log-likelihoods ----------------------------------------------------------------------------------------


def ll_gaussian(game: Game, r: np.ndarray) -> float:
    """Copula log density of the game's normal scores under N(0, R)."""
    w, v = np.linalg.eigh(r)
    w = np.maximum(w, 1e-8)
    c = v.T @ game.z
    return float(-0.5 * np.log(w).sum() - 0.5 * ((c**2) * (1.0 / w - 1.0)).sum())


def ll_t(game: Game, r: np.ndarray, nu: float) -> float:
    """Copula log density under the t-copula: the joint t density over the product of the t margins, at
    `x = T_nu^-1(Phi(z))`."""
    d = len(game.z)
    x = stats.t.ppf(ndtr(game.z), nu)
    w, v = np.linalg.eigh(r)
    w = np.maximum(w, 1e-8)
    q = ((v.T @ x) ** 2 / w).sum()
    joint = (
        gammaln((nu + d) / 2)
        - gammaln(nu / 2)
        - d / 2 * np.log(nu * np.pi)
        - 0.5 * np.log(w).sum()
        - (nu + d) / 2 * np.log1p(q / nu)
    )
    margins = (
        d * (gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(nu * np.pi))
        - (nu + 1) / 2 * np.log1p(x**2 / nu).sum()
    )
    return float(joint - margins)


_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(GH_NODES)
_GH_W = _GH_W / _GH_W.sum()


def scale_for(tau: float, s: np.ndarray, base: float = 1.0) -> np.ndarray:
    return base * np.exp(tau * s - tau**2 / 2)


def fmt(param) -> str:
    return f"tau={param[0]:g}, m0={param[1]:g}" if isinstance(param, tuple) else f"{param:g}"


def ll_factor(game: Game, r: np.ndarray, tau: float, base: float = 1.0) -> float:
    """Copula log density when the game's correlation scale is random: the mixture over `s` of N(0, R(m))."""
    w, v = np.linalg.eigh(r)
    c2 = (v.T @ game.z) ** 2
    m = np.minimum(scale_for(tau, _GH_X, base), 1.0 / max(1.0 - w[0], 1e-9) - 1e-6)  # keeps R(m) PSD
    lam = (1.0 - m)[:, None] + m[:, None] * w[None, :]
    lam = np.maximum(lam, 1e-8)
    comp = -0.5 * np.log(lam).sum(axis=1) - 0.5 * (c2[None, :] * (1.0 / lam - 1.0)).sum(axis=1)
    top = comp.max()
    return float(top + np.log((_GH_W * np.exp(comp - top)).sum()))


def total(games: list[Game], fn) -> np.ndarray:
    return np.array([fn(g) for g in games])


# --- simulate a fitted model on a game ----------------------------------------------------------------------


def simulate_z(game: Game, kind: str, param: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """`n` draws of the game's normal scores under `kind` ('gaussian', 'gaussian_k', 't', 't_p', 'factor')."""
    r = game.r_kendall if kind in ("gaussian_k", "t") else game.r_pearson
    w, v = np.linalg.eigh(r)
    root = (v * np.sqrt(np.maximum(w, 0.0))) @ v.T
    if kind in ("gaussian", "gaussian_k"):
        return rng.standard_normal((n, len(w))) @ root
    if kind in ("t", "t_p"):
        mix = np.sqrt(param / rng.chisquare(param, size=(n, 1)))
        x = (rng.standard_normal((n, len(w))) @ root) * mix
        return ndtri(np.clip(stats.t.cdf(x, param), 1e-12, 1 - 1e-12))
    if kind == "factor":
        m = np.minimum(
            scale_for(param[0], rng.standard_normal(n), param[1]), 1.0 / max(1.0 - w[0], 1e-9) - 1e-6
        )
        lam = np.maximum((1 - m)[:, None] + m[:, None] * w[None, :], 0.0)
        e = rng.standard_normal((n, len(w)))
        return ((e @ v) * np.sqrt(lam)) @ v.T
    raise ValueError(kind)


EVENTS = ("QB1+WR1", "QB1+WR1+opp WR1")


def event_columns(game: Game, event: str) -> list[list[int]]:
    """For each team with a QB1 and a WR1 on the field: the indices (into the game's vector) of the event's
    players -- his QB1, his WR1 and, for the "opp" event, the other team's WR1."""

    def find(role: str, team: str) -> int | None:
        for i, (t, r) in enumerate(zip(game.teams, game.roles, strict=True)):
            if t == team and r == role:
                return i
        return None

    teams = list(dict.fromkeys(game.teams))
    out = []
    for team in teams:
        qb, wr1 = find("QB1", team), find("WR1", team)
        if qb is None or wr1 is None:
            continue
        cols = [qb, wr1]
        if event.endswith("opp WR1"):
            opp = [find("WR1", t) for t in teams if t != team]
            if not opp or opp[0] is None:
                continue
            cols.append(opp[0])
        out.append(cols)
    return out


def event_rates(
    games: list[Game], kind: str | None, param: float, n_sim: int, seed: int, cut: float = Z_TOP
) -> pd.DataFrame:
    """Per game and event instance: the observed indicator (kind None) or the model's probability that every
    player of the event is in his top decile (z above the 90th normal percentile)."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in games:
        sims = None if kind is None else simulate_z(g, kind, param, n_sim, rng)
        for ev in EVENTS:
            for cols in event_columns(g, ev):
                if kind is None:
                    value = float(np.all(g.z[cols] > cut))
                else:
                    value = float(np.all(sims[:, cols] > cut, axis=1).mean())
                rows.append((g.game_id, ev, value))
    return pd.DataFrame(rows, columns=["game_id", "event", "p"])


# --- the study ----------------------------------------------------------------------------------------------


def study(
    f: pd.DataFrame,
    *,
    train_through: int = TRAIN_THROUGH,
    n_sim: int = 400,
    seed: int = 0,
    roles: tuple[str, ...] | None = None,
    cuts: tuple[float, ...] = (1.0, Z_TOP, 1.5),
) -> dict:
    train, test = f[f["season"] <= train_through], f[f["season"] > train_through]
    tables = pair_tables(train)
    g_train, g_test = build_games(train, tables, roles), build_games(test, tables, roles)
    g_all = g_train + g_test

    # --- log-likelihood: choose nu / tau on the training games, score on the held-out games
    base_train = total(g_train, lambda g: ll_gaussian(g, g.r_pearson))
    base_test = total(g_test, lambda g: ll_gaussian(g, g.r_pearson))
    models = {
        "gaussian (shipped R)": (lambda g: ll_gaussian(g, g.r_pearson), None),
        "gaussian (Kendall R)": (lambda g: ll_gaussian(g, g.r_kendall), None),
    }
    grid_rows = []
    best = {}
    for label, make, grid, key in (
        ("t-copula (shipped R)", lambda p: lambda g: ll_t(g, g.r_pearson, p), NU_GRID, "nu"),
        ("t-copula (Kendall R)", lambda p: lambda g: ll_t(g, g.r_kendall, p), NU_GRID, "nu"),
        ("game factor (shipped R)", lambda p: lambda g: ll_factor(g, g.r_pearson, *p), FACTOR_GRID, "factor"),
    ):
        scored = []
        for p in grid:
            fn = make(p)
            tr = total(g_train, fn)
            te = total(g_test, fn)
            scored.append((p, (tr - base_train).sum(), te - base_test))
            grid_rows.append(
                {
                    "model": label,
                    "param": fmt(p),
                    "train_gain_total": float((tr - base_train).sum()),
                    "test_gain_per_game": float((te - base_test).mean()),
                    "test_se": float((te - base_test).std(ddof=1) / np.sqrt(len(te))),
                }
            )
        p_best = max(scored, key=lambda s: s[1])[0]
        best[label] = (key, p_best, make(p_best))
        models[f"{label}, {key}={fmt(p_best)}"] = (make(p_best), p_best)
    rows = []
    for name, (fn, _) in models.items():
        te = total(g_test, fn)
        d = te - base_test
        rows.append(
            {
                "model": name,
                "test_ll_per_game": float(te.mean()),
                "gain_vs_gaussian_per_game": float(d.mean()),
                "se": float(d.std(ddof=1) / np.sqrt(len(d))),
                "z": float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if d.std() > 0 else float("nan"),
                "games": len(d),
            }
        )
    ll_table = pd.DataFrame(rows)

    # --- joint tail events: observed vs what each fitted model says, held-out games and all games
    sim_models = {
        "gaussian (shipped R)": ("gaussian", 0.0),
        "t-copula (Kendall R)": ("t", best["t-copula (Kendall R)"][1]),
        "t-copula (shipped R)": ("t_p", best["t-copula (shipped R)"][1]),
        "game factor (shipped R)": ("factor", best["game factor (shipped R)"][1]),
    }
    rate_rows = []
    for scope, games in (("held out", g_test), ("all seasons", g_all)):
        for cut in cuts:
            observed = event_rates(games, None, 0.0, 0, seed, cut)
            for name, (kind, param) in sim_models.items():
                pred = event_rates(games, kind, param, n_sim, seed + 1, cut)
                for ev in EVENTS:
                    o, p = observed[observed.event == ev], pred[pred.event == ev]
                    by_game = pd.DataFrame(
                        {"o": o.groupby("game_id")["p"].sum(), "p": p.groupby("game_id")["p"].sum()}
                    )
                    obs, exp = float(by_game["o"].sum()), float(by_game["p"].sum())
                    resid = (by_game["o"] - by_game["p"]).to_numpy()
                    rate_rows.append(
                        {
                            "scope": scope,
                            "cut_z": round(cut, 2),
                            "model": name
                            + (f", {'nu=' if kind.startswith('t') else ''}{fmt(param)}" if param else ""),
                            "event": ev,
                            "instances": int(len(o)),
                            "observed": obs,
                            "model_expects": exp,
                            "ratio": obs / exp if exp else float("nan"),
                            "ratio_se": float(np.sqrt((resid**2).sum())) / exp if exp else float("nan"),
                        }
                    )
    rates = pd.DataFrame(rate_rows)
    return {
        "ll": ll_table,
        "grid": pd.DataFrame(grid_rows),
        "rates": rates,
        "best": {k: (v[0], v[1]) for k, v in best.items()},
        "games": (len(g_train), len(g_test)),
        "tables": tables,
        "train_through": train_through,
    }


def render(result: dict) -> str:
    pd.set_option("display.width", 200)
    out = [
        f"fit 2014-{result['train_through']} ({result['games'][0]} games), "
        f"held out {result['games'][1]} games",
        "",
        "log-likelihood of the held-out games (copula density), per game:",
        result["ll"].round(4).to_string(index=False),
        "",
        "joint tail events (all players above the cut z; observed / what the fitted model says):",
        result["rates"].round(3).to_string(index=False),
        "",
        "chosen on training: " + ", ".join(f"{k}: {a}={fmt(b)}" for k, (a, b) in result["best"].items()),
    ]
    return "\n".join(out)


def main() -> None:
    from dfs.model.history import load_history
    from dfs.model.predict import available_seasons

    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--train-through", type=int, default=TRAIN_THROUGH)
    ap.add_argument("--n-sim", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--core", action="store_true", help="limit each game to the passing core (QB1, WR1-3, TE1)"
    )
    ap.add_argument(
        "--clip", type=float, default=None, help="normal-score clip on u (default: the shipped 0.005)"
    )
    args = ap.parse_args()
    if args.clip is not None:
        from dfs.sim import correlation

        correlation.U_CLIP = (args.clip, 1 - args.clip)
    frame = pd.read_parquet(args.frame)
    history, _ = load_history(available_seasons(int(frame["season"].max())))
    f = shape_retest.prepare(frame, history, args.seed)
    result = study(
        f,
        train_through=args.train_through,
        n_sim=args.n_sim,
        seed=args.seed,
        roles=PASSING_CORE if args.core else None,
    )
    print(render(result))
    print()
    print(result["grid"].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
