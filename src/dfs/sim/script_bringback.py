"""What a bring-back adds to a stack, by the stacked team's spread -- from what actually happened.

A stack's value is its right tail: a tournament pays the top 1%, and the bring-back is only worth a roster
spot if putting a player of the OTHER team beside the stack makes the top of the distribution more likely than
putting an unrelated player there would. The question Sam asked: does that lift vanish, or turn negative,
when the stacked team is a big favourite (the blowout: the other side is trailing and the stack's QB is
sitting on the bench in the fourth quarter)?

The measure, on 2014-2025 actual DK points:

- a **stack** `S` is a team's QB1 plus teammates (QB1+WR1, QB1+WR1+WR2, QB1+WR1+TE1, QB1+RB1) in one game;
- the **bring-back** `B` is the other team's WR1 (or WR2, TE1, RB1) in that game;
- the **baseline** `B'` is the same role from ANOTHER game, drawn at random from the games whose stacked team
  has the same spread bucket in the same season (so it has the same distribution as `B` and no link to `S`);
- a **tail threshold** `T_q` is the q-quantile of `S + B'` over all games (q = 0.90, 0.95, 0.99);
- the **lift** of the bring-back is `P(S + B >= T_q) / P(S + B' >= T_q)` within a spread bucket: above 1, the
  bring-back raises the chance of a big stack beyond what any unrelated player would; at 1 it adds
  nothing; below 1 it hurts.

The baseline probability is averaged over many random draws of `B'`; the 90% interval resamples whole games.
Pure functions over the same frame as `script.py`; no sheet, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.sim.script import SPREAD_BUCKETS, spread_bucket

STACKS = {
    "QB+WR1": ("QB1", "WR1"),
    "QB+WR1+WR2": ("QB1", "WR1", "WR2"),
    "QB+WR1+TE1": ("QB1", "WR1", "TE1"),
    "QB+RB1": ("QB1", "RB1"),
}
BRING_BACKS = ("WR1", "WR2", "TE1", "RB1")
QUANTILES = (0.90, 0.95, 0.99)


def team_roles(frame: pd.DataFrame, roles: tuple[str, ...]) -> pd.DataFrame:
    """One row per team-game that has every role in `roles`: `game_id, team, opp, season, spread` and one
    `dk` column per role (named after it)."""
    base = frame[["game_id", "team", "opp", "season", "spread"]].drop_duplicates(["game_id", "team"])
    out = base
    for role in roles:
        sub = frame.loc[frame["role"] == role, ["game_id", "team", "dk"]].drop_duplicates(["game_id", "team"])
        out = out.merge(sub.rename(columns={"dk": role}), on=["game_id", "team"], how="inner")
    return out


def pair_table(frame: pd.DataFrame, stack: tuple[str, ...], back: str) -> pd.DataFrame:
    """Rows `game_id, season, spread, bucket, S, B`: the stack's points on team T and the bring-back role's
    points on the other team of the game; `spread` and `bucket` are T's."""
    mine = team_roles(frame, stack)
    mine["S"] = mine[list(stack)].sum(axis=1)
    theirs = team_roles(frame, (back,)).rename(columns={back: "B", "team": "opp_team"})
    m = mine.merge(
        theirs[["game_id", "opp_team", "B"]], left_on=["game_id", "opp"], right_on=["game_id", "opp_team"]
    )
    m["bucket"] = spread_bucket(m["spread"])
    return m[["game_id", "season", "spread", "bucket", "S", "B"]].reset_index(drop=True)


@dataclass
class Lift:
    table: pd.DataFrame  # one row per stack x bring-back x bucket x quantile
    thresholds: pd.DataFrame  # the tail thresholds used


def _baseline_hits(
    t: pd.DataFrame, thresholds: dict[float, float], n_draws: int, rng: np.random.Generator
) -> dict[float, np.ndarray]:
    """Per row, the share of `n_draws` independent re-pairings (B shuffled among rows of the same season and
    spread bucket) whose S + B' reaches each threshold."""
    groups = t.groupby(["season", "bucket"]).indices
    hits = {q: np.zeros(len(t)) for q in thresholds}
    s, b = t["S"].to_numpy(), t["B"].to_numpy()
    for _ in range(n_draws):
        shuffled = b.copy()
        for idx in groups.values():
            shuffled[idx] = b[rng.permutation(idx)]
        total = s + shuffled
        for q, cut in thresholds.items():
            hits[q] += total >= cut
    return {q: h / n_draws for q, h in hits.items()}


def baseline_thresholds(t: pd.DataFrame, n_draws: int, rng: np.random.Generator) -> dict[float, float]:
    """`T_q`: the q-quantile of S + B' over all games, B' drawn as in `_baseline_hits`."""
    groups = t.groupby(["season", "bucket"]).indices
    s, b = t["S"].to_numpy(), t["B"].to_numpy()
    sums = []
    for _ in range(n_draws):
        shuffled = b.copy()
        for idx in groups.values():
            shuffled[idx] = b[rng.permutation(idx)]
        sums.append(s + shuffled)
    pooled = np.concatenate(sums)
    return {q: float(np.quantile(pooled, q)) for q in QUANTILES}


def lift_table(
    frame: pd.DataFrame,
    *,
    stacks: dict[str, tuple[str, ...]] = STACKS,
    backs: tuple[str, ...] = BRING_BACKS,
    n_draws: int = 100,
    n_boot: int = 500,
    seed: int = 0,
) -> Lift:
    """The lift of every bring-back role on every stack, by the stacked team's spread bucket and tail."""
    rng = np.random.default_rng(seed)
    rows, cuts = [], []
    for stack_name, stack in stacks.items():
        for back in backs:
            t = pair_table(frame, stack, back)
            if len(t) < 500:
                continue
            thresholds = baseline_thresholds(t, n_draws, rng)
            cuts.append(
                {"stack": stack_name, "back": back, **{f"T{int(q * 100)}": v for q, v in thresholds.items()}}
            )
            base = _baseline_hits(t, thresholds, n_draws, rng)
            s, b = t["S"].to_numpy(), t["B"].to_numpy()
            game_code, _ = pd.factorize(t["game_id"])
            n_games = game_code.max() + 1
            for bucket in (*SPREAD_BUCKETS, "all"):
                mask = np.ones(len(t), bool) if bucket == "all" else (t["bucket"] == bucket).to_numpy()
                idx = np.flatnonzero(mask)
                if len(idx) < 200:
                    continue
                corr = float(np.corrcoef(s[idx], b[idx])[0, 1])
                for q, cut in thresholds.items():
                    obs = (s[idx] + b[idx] >= cut).astype(float)
                    expect = base[q][idx]
                    lift = obs.mean() / expect.mean() if expect.mean() > 0 else float("nan")
                    # resample games: numerator and baseline move together
                    per_game_obs = np.bincount(game_code[idx], weights=obs, minlength=n_games)
                    per_game_exp = np.bincount(game_code[idx], weights=expect, minlength=n_games)
                    per_game_n = np.bincount(game_code[idx], minlength=n_games).astype(float)
                    draws = rng.integers(0, n_games, size=(n_boot, n_games))
                    num = per_game_obs[draws].sum(axis=1)
                    den = per_game_exp[draws].sum(axis=1)
                    boot = num / np.where(den > 0, den, np.nan)
                    lo, hi = np.nanquantile(boot, [0.05, 0.95])
                    rows.append(
                        {
                            "stack": stack_name,
                            "back": back,
                            "bucket": bucket,
                            "tail": f"top {round((1 - q) * 100)}%",
                            "n": len(idx),
                            "corr": corr,
                            "obs": float(obs.mean()),
                            "baseline": float(expect.mean()),
                            "lift": float(lift),
                            "lo": float(lo),
                            "hi": float(hi),
                            "hits": int(obs.sum()),
                            "games": int((per_game_n > 0).sum()),
                        }
                    )
    return Lift(pd.DataFrame(rows), pd.DataFrame(cuts))
