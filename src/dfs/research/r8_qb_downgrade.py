"""R8 (Plan M1.6): when the starting quarterback misses a game, what do his pass catchers score against
their projection?

`news_context` states the fact ("QB downgrade: Lock (backup) for Darnold (OUT)"); this measures what it is
worth, the way R7 measured a teammate out (`r7_teammate_bump`), so the plays model reads a number and not a
guess.

**The events.** A team-game's *starter* is the quarterback with the most pass attempts (at least
`MIN_ATTEMPTS`). The *regular starter* of game g is the starter of the team's game g-1 who also started at
least `ESTABLISHED` of games g-3..g-1. Game g is a **QB-out event** when the regular starter has no stats row
in it (he missed the game) and a **control** when he started it. A game he played without starting (benched,
hurt early) is neither, and so is any team-game with a regular pass catcher or back out (`r1.find_absences`),
so the QB effect is never mixed with a teammate's. The beneficiaries are the players of the prior three-game
rotation who played the game, by R1's role label (WR1 ... WR4+, TE1, TE2+, RB1 ... RB3+).

**The outcome** is UM's out-of-fold residual, DK points minus UM's projection (`r7.residual_frame`). UM reads
the game's Vegas context, so what is left is the effect *beyond* what the betting market's total already
moved:
the closest history has to "against what the projections already moved". Demeaned within (position, season),
the event mean minus the control mean per role, game-clustered 90% interval, the fit (2018-2021) and test
(2022-2025) halves. Receiving volume is reported beside it: targets in the game minus the player's prior
three-game average, event minus control (the RB-receiving outcome the plan asks for).

**The table** (`models/research/qb_downgrade.csv`, R7's columns, `absent = QB1`) shrinks each cell toward 0
by the empirical-Bayes weight `tau2 / (tau2 + se^2)` and keeps a cell only with `r7.MIN_EVENTS`+ events and
the fit and test halves agreeing in sign. `dfs.plays` reads it (`load_table`).

    python -m dfs.research.r8_qb_downgrade [--write]
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.paths import REPO_ROOT
from dfs.research import flags
from dfs.research import r1_redistribution as r1
from dfs.research import r7_teammate_bump as r7

SEASONS = list(range(2018, 2026))  # UM's out-of-fold history ends in 2025; 2026 is checked against CalPts
FIT = list(range(2018, 2022))
MIN_ATTEMPTS = 10
ESTABLISHED = 2
ABSENT = "QB1"
BENEFICIARIES = ("WR1", "WR2", "WR3", "WR4+", "TE1", "TE2+", "RB1", "RB2", "RB3+")
TABLE_FILE = "qb_downgrade.csv"
TABLE_PATH = REPO_ROOT / "models" / "research" / TABLE_FILE


def starters(rows: pd.DataFrame) -> pd.DataFrame:
    """`team, season, gidx, starter`: the quarterback with the most pass attempts in each team-game (at least
    `MIN_ATTEMPTS`; a game without one has no row)."""
    qb = rows[(rows["position"] == "QB") & (rows["attempts"] >= MIN_ATTEMPTS)]
    qb = qb.sort_values(["team", "season", "gidx", "attempts"], ascending=[True, True, True, False])
    top = qb.drop_duplicates(["team", "season", "gidx"])
    return top[["team", "season", "gidx", "gsis_id"]].rename(columns={"gsis_id": "starter"})


def qb_games(rows: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """One row per team-game with an established regular starter: `team, season, tidx, week, regular,
    event` (True: he missed the game; False: he started it). Games he played without starting are dropped."""
    st = starters(rows)
    idx = st.set_index(["team", "season", "gidx"])["starter"]
    tg = team_games.rename(columns={"gidx": "tidx"})[["team", "season", "tidx", "week"]].copy()
    get = idx.to_dict()

    def prev(row: pd.Series, lag: int) -> object:
        return get.get((row["team"], row["season"], row["tidx"] - lag))

    tg = tg[tg["tidx"] > r1.WINDOW]
    lags = {
        lag: [get.get((t, s, i - lag)) for t, s, i in zip(tg["team"], tg["season"], tg["tidx"], strict=True)]
        for lag in range(1, r1.WINDOW + 1)
    }
    tg["regular"] = lags[1]
    tg["starts"] = sum(
        (pd.Series(lags[lag], index=tg.index) == tg["regular"]).astype(int) for lag in range(1, r1.WINDOW + 1)
    )
    tg = tg[tg["regular"].notna() & (tg["starts"] >= ESTABLISHED)]
    played = set(zip(rows["gsis_id"], rows["team"], rows["season"], rows["gidx"], strict=True))
    tg["played"] = [
        (q, t, s, i) in played
        for q, t, s, i in zip(tg["regular"], tg["team"], tg["season"], tg["tidx"], strict=True)
    ]
    tg["started"] = [
        get.get((t, s, i)) == q
        for q, t, s, i in zip(tg["regular"], tg["team"], tg["season"], tg["tidx"], strict=True)
    ]
    tg = tg[~tg["played"] | tg["started"]]
    tg["event"] = ~tg["played"]
    return tg[["team", "season", "tidx", "week", "regular", "event"]].reset_index(drop=True)


def event_rows(sp: pd.DataFrame, games: pd.DataFrame, resid: pd.DataFrame) -> pd.DataFrame:
    """One row per (QB event or control team-game, rotation pass catcher / back who played): his role
    `label`, `abs_label` (`QB1` in an event, None in a control), `event`, UM's residual and the targets
    delta (game targets minus his prior three-game average)."""
    tg = r1.team_game_index(games)
    rows = r1.offense_rows(sp, tg)
    prior = r1.prior_windows(rows, team_games=tg)
    rot = r1.rank_rotation(prior)
    out_any = r1.find_absences(prior, rows, tg)
    messy = set(zip(out_any["team"], out_any["season"], out_any["tidx"], strict=True))
    qg = qb_games(rows, tg)
    qg = qg[[(t, s, i) not in messy for t, s, i in zip(qg["team"], qg["season"], qg["tidx"], strict=True)]]
    ben = rot[rot["position"].isin(["WR", "TE", "RB"])].merge(qg, on=["team", "season", "tidx"])
    game = rows[["gsis_id", "team", "season", "gidx", "targets"]].rename(
        columns={"gidx": "tidx", "targets": "g_t"}
    )
    ben = ben.merge(game, on=["gsis_id", "team", "season", "tidx"])  # played the game
    ben["tgt_delta"] = ben["g_t"] - ben["p_targets"] / r1.WINDOW
    ben["abs_label"] = np.where(ben["event"], ABSENT, None)
    ben = ben.merge(resid[["gsis_id", "season", "week", "resid"]], on=["gsis_id", "season", "week"])
    ben["game_key"] = ben["team"] + "|" + ben["season"].astype(str) + "|" + ben["week"].astype(str)
    keep = [
        "gsis_id",
        "name",
        "position",
        "team",
        "season",
        "week",
        "label",
        "abs_label",
        "event",
        "resid",
        "tgt_delta",
        "game_key",
    ]
    out = flags.add_split_and_demeaned(ben[keep])
    out["split"] = np.where(out["season"].isin(FIT), "fit", "test")
    return out.reset_index(drop=True)


def target_deltas(rows: pd.DataFrame) -> pd.DataFrame:
    """Per role: event minus control mean targets delta (the receiving volume he gains or loses)."""
    out = []
    for label in BENEFICIARIES:
        sel = rows[rows["label"] == label]
        ev, ct = sel.loc[sel["event"], "tgt_delta"], sel.loc[~sel["event"], "tgt_delta"]
        if ev.empty:
            continue
        out.append({"beneficiary": label, "n_tgt": len(ev), "targets": float(ev.mean() - ct.mean())})
    return pd.DataFrame(out)


GROUPS = (("pass catchers", ("WR", "TE")), ("WR", ("WR",)), ("TE", ("TE",)), ("RB", ("RB",)))
SHIP = {"WR": "pass catchers", "TE": "pass catchers", "RB": "RB"}  # position -> the group it reads
GROUP_COLUMNS = [
    "group",
    "n_events",
    "n_control",
    "effect",
    "se",
    "lo90",
    "hi90",
    "t",
    "fit_n",
    "fit_effect",
    "test_n",
    "test_effect",
    "agree",
]
TABLE_COLUMNS = ["position", "group", "points", *GROUP_COLUMNS[1:]]


def cells(rows: pd.DataFrame) -> pd.DataFrame:
    """Per role (WR1 ... RB3+): R7's cell table, reported beside the groups (too thin to ship one by one)."""
    labels = [b for b in BENEFICIARIES if (rows["label"] == b).any()]
    saved = r7.BENEFICIARIES
    try:
        r7.BENEFICIARIES = tuple(labels)
        return r7.cell_effects(rows, absent_roles=(ABSENT,))
    finally:
        r7.BENEFICIARIES = saved


def groups(rows: pd.DataFrame) -> pd.DataFrame:
    """Per position group: event minus control (demeaned, game-clustered), all seasons and the two halves.
    The role cells share one sign and one size (every pass-catcher cell is -0.5 to -1.2), so the shipped
    number is the pooled group's, not each thin cell shrunk toward 0."""
    out = []
    for name, positions in GROUPS:
        sel = rows[rows["position"].isin(positions)].reset_index(drop=True)
        if not sel["event"].any():
            continue
        allp = flags.effect(sel, sel["event"], -1, cluster="game_key")
        res = flags.evaluate(sel, sel["event"], -1, cluster="game_key")
        out.append(
            {
                "group": name,
                "n_events": allp["n_flagged"],
                "n_control": allp["n_other"],
                "effect": allp["diff"],
                "se": float((allp["hi90"] - allp["lo90"]) / (2 * r7.Z90)),
                "lo90": allp["lo90"],
                "hi90": allp["hi90"],
                "t": allp["t"],
                "fit_n": res["fit"]["n_flagged"],
                "fit_effect": res["fit"]["diff"],
                "test_n": res["test"]["n_flagged"],
                "test_effect": res["test"]["diff"],
            }
        )
    table = pd.DataFrame(out)
    table["agree"] = np.sign(table["fit_effect"]) == np.sign(table["test_effect"])
    return table


def shipped(table: pd.DataFrame) -> pd.DataFrame:
    """Per position the points the plays model applies: its group's effect where the group has
    `r7.MIN_EVENTS`+ events and the two halves agree in sign, else 0."""
    by = table.set_index("group")
    out = []
    for pos, name in SHIP.items():
        r = by.loc[name]
        ok = bool(r["agree"]) and r["n_events"] >= r7.MIN_EVENTS
        out.append(
            {"position": pos, "group": name, "points": float(r["effect"]) if ok else 0.0, **r.to_dict()}
        )
    return pd.DataFrame(out)[TABLE_COLUMNS]


def run(resid: pd.DataFrame | None = None, log=lambda msg: None) -> dict:  # noqa: ANN001
    from dfs.research import data

    if resid is None:
        from dfs.sim.pipeline import load_frame

        log("UM residuals")
        resid = r7.residual_frame(load_frame(SEASONS[-1], log=log))
    resid = resid[resid["season"].isin(SEASONS)]
    sp, games = data.read_stats_player(SEASONS), data.read_games(SEASONS)
    rows = event_rows(sp, games, resid)
    n_games = rows.loc[rows["event"], "game_key"].nunique()
    log(
        f"{n_games} QB-out team-games, {int(rows['event'].sum())} event rows, "
        f"{int((~rows['event']).sum())} controls"
    )
    grp = groups(rows)
    return {
        "rows": rows,
        "cells": cells(rows),
        "groups": grp,
        "table": shipped(grp),
        "targets": target_deltas(rows),
        "games": n_games,
    }


def write_outputs(result: dict, directory: Path | None = None) -> None:
    from dfs.research.common import metadata, write_csv

    table = result["table"]
    meta = metadata("r8", SEASONS, int(result["groups"]["n_events"].max()), qb_games=result["games"])
    write_csv(TABLE_FILE, table, meta, directory)


def render(result: dict) -> str:
    lines = [f"QB-out team-games {result['games']} (seasons {SEASONS[0]}-{SEASONS[-1]}), DK points vs UM", ""]
    lines.append("group           n_ev   effect    se     t    fit(n)         test(n)       agree")
    for r in result["groups"].itertuples():
        lines.append(
            f"{r.group:14} {r.n_events:5d}  {r.effect:+6.2f} {r.se:5.2f} {r.t:+5.1f}  "
            f"{r.fit_effect:+6.2f} ({r.fit_n:4d})  {r.test_effect:+6.2f} ({r.test_n:4d})  "
            f"{'yes' if r.agree else 'no'}"
        )
    lines += [
        "",
        "shipped: " + ", ".join(f"{r.position} {r.points:+.2f}" for r in result["table"].itertuples()),
        "",
    ]
    c = result["cells"].merge(result["targets"], on="beneficiary", how="left")
    lines.append(
        "by role (reported, not shipped): role  n_ev  effect  se  t  fit  test  targets vs his prior average"
    )
    for r in c.itertuples():
        lines.append(
            f"  {r.beneficiary:5} {r.n_events:4d}  {r.effect:+6.2f} {r.se:5.2f} {r.t:+5.1f}  "
            f"{r.fit_effect:+6.2f}  {r.test_effect:+6.2f}   {r.targets:+5.2f}"
        )
    return "\n".join(lines)


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, float]:
    table = pd.read_csv(path)
    return {str(r.position): float(r.points) for r in table.itertuples() if r.points == r.points and r.points}


def load_table(path: Path | None = None) -> dict[str, float]:
    """`{position: points}` a pass catcher's projection moves when his starting QB is out (the shipped cells;
    empty when the file is missing)."""
    path = path or TABLE_PATH
    return _load(path) if path.exists() else {}


if __name__ == "__main__":  # pragma: no cover - the research entry point
    import sys

    cache = REPO_ROOT / "data" / "research" / "um_resid_2014_2025.pkl"
    res = run(pd.read_pickle(cache) if cache.exists() else None, log=print)
    print(render(res))
    if "--write" in sys.argv:
        write_outputs(res)
        print(f"wrote models/research/{TABLE_FILE}")
