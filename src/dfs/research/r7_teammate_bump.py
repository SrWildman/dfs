"""R7: when a starter is out, what do his teammates score against their projection?

R1 measured where the vacated VOLUME goes (targets, carries) and found it is spread thin: no single teammate
inherits most of it. This asks the question a lineup cares about: in DK points, does a teammate beat his
projection when a starter is out? The projection here is UM's out-of-fold mean (`dfs.sim.pipeline`'s `pred`,
built from the player's own prior games and the game's Vegas context, blind to who is missing), so the
residual
`actual - UM` is exactly what the absence adds that the projection did not know.

The events are R1's: a clean event is a team-game where exactly one regular (>= 15% of the team's targets or
>= 30% of its carries over the prior 3 games) has no stats row, and the beneficiaries are the teammates who
played, labelled by their rank in the same prior window (WR1, WR2, ..., RB1, RB2, RB3+, TE1, TE2+). Controls
are team-games with a full window and no regular out. For each (absent role, beneficiary role) the effect is
the mean residual in the event games minus the mean residual of the same role in the control games, both
demeaned within (position, season) as `flags.add_split_and_demeaned` does, with a game-clustered 90%
interval and the fit (2014-2021) / test (2022-2025) split every research study reports.

**The shadow table** shrinks each cell toward 0 with an empirical-Bayes weight (`tau2 / (tau2 + se^2)`, `tau2`
the between-cell variance left after sampling noise), and keeps a cell only if the fit and the test periods
agree on its sign. The out-of-sample check fits the table on the fit seasons and applies it to the test
seasons' event rows: does `actual - (UM + bump)` beat `actual - UM`?

The result is a table of numbers, not a model change: `dfs.teammate_bump` reads it as a SHADOW (a Model Check
row; CalPts and late swap do not use it).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research import data, flags
from dfs.research import r1_redistribution as r1
from dfs.research.common import FIT_SEASONS, TEST_SEASONS, metadata, write_csv
from dfs.research.stats import Z90

ABSENT_ROLES = ("WR1", "WR2", "TE1", "RB1")  # the starters the round asked for
EXTRA_ABSENT_ROLES = ("WR3", "RB2")  # shown, thin, never shipped
BENEFICIARIES = ("QB1", "RB1", "RB2", "RB3+", "WR1", "WR2", "WR3", "WR4+", "TE1", "TE2+")
MIN_EVENTS = 15  # a cell with fewer flagged rows is reported but gets no bump
TABLE_FILE = "teammate_bump.csv"
TAU2_FLOOR = 0.05  # points^2; the between-cell variance is never taken below this
COLUMNS = [
    "absent",
    "beneficiary",
    "n_events",
    "n_control",
    "effect",
    "se",
    "lo90",
    "hi90",
    "t",
    "fit_n",
    "fit_effect",
    "fit_se",
    "test_n",
    "test_effect",
    "test_se",
    "agree",
    "bump",
]


def residual_frame(baseline: pd.DataFrame) -> pd.DataFrame:
    """`gsis_id, season, week, position, dk, um, resid`: UM's out-of-fold residual for every rated
    player-game.
    `baseline` is `sim.pipeline.load_frame`'s output (`pred` is UM)."""
    cols = ["gsis_id", "season", "week", "position", "dk", "pred"]
    out = baseline[cols].rename(columns={"pred": "um"})
    out = out[out["position"].isin(["QB", "RB", "WR", "TE"])]
    return out.assign(resid=out["dk"] - out["um"]).reset_index(drop=True)


def event_rows(sp: pd.DataFrame, games: pd.DataFrame, resid: pd.DataFrame) -> pd.DataFrame:
    """One row per (team-game, teammate who played in the prior rotation) with his role `label`, the event's
    absent role `abs_label` (None in a control game), `event` (bool) and UM's residual. Team-games with two or
    more regulars out are in neither group."""
    tg = r1.team_game_index(games)
    rows = r1.offense_rows(sp, tg)
    prior = r1.prior_windows(rows, team_games=tg)
    rot = r1.rank_rotation(prior)
    absences = r1.find_absences(prior, rows, tg).merge(
        rot[["team", "season", "tidx", "gsis_id", "label", "rank"]],
        on=["team", "season", "tidx", "gsis_id"],
        how="left",
    )
    volume = r1.team_volume(rows)
    events = r1.clean_events(absences, volume)
    controls = r1.control_events(prior, volume, absences)
    parts = []
    for group, is_event in ((events, True), (controls, False)):
        ben = r1.beneficiaries(group, rot, rows)
        ben = ben[ben["kind"] == "rot_played"]
        ben = ben.merge(group[["event_id", "team", "season", "tidx"]], on="event_id")
        ben = ben.merge(
            tg.rename(columns={"gidx": "tidx"})[["team", "season", "tidx", "week", "game_id"]],
            on=["team", "season", "tidx"],
        )
        ben["event"] = is_event
        parts.append(
            ben[["event_id", "gsis_id", "season", "week", "game_id", "team", "label", "abs_label", "event"]]
        )
    out = pd.concat(parts, ignore_index=True)
    out = out.merge(resid, on=["gsis_id", "season", "week"], how="inner")
    out["game_key"] = out["team"] + "|" + out["season"].astype(str) + "|" + out["week"].astype(str)
    return flags.add_split_and_demeaned(out)


def _se(d: dict) -> float:
    return float((d["hi90"] - d["lo90"]) / (2 * Z90)) if np.isfinite(d["lo90"]) else float("nan")


def cell_effects(rows: pd.DataFrame, absent_roles=ABSENT_ROLES + EXTRA_ABSENT_ROLES) -> pd.DataFrame:
    """The (absent role, beneficiary role) table, before shrinkage: all seasons, and the fit / test
    periods."""
    out = []
    for absent in absent_roles:
        for label in BENEFICIARIES:
            sel = rows[(rows["label"] == label) & (~rows["event"] | (rows["abs_label"] == absent))]
            flag = sel["event"].reset_index(drop=True)
            sel = sel.reset_index(drop=True)
            if flag.sum() == 0:
                continue
            res = flags.evaluate(sel, flag, +1, cluster="game_key")
            allp = flags.effect(sel, flag, +1, cluster="game_key")
            row = {
                "absent": absent,
                "beneficiary": label,
                "n_events": allp["n_flagged"],
                "n_control": allp["n_other"],
                "effect": allp["diff"],
                "se": _se(allp),
                "lo90": allp["lo90"],
                "hi90": allp["hi90"],
                "t": allp["t"],
            }
            for name in ("fit", "test"):
                row[f"{name}_n"] = res[name]["n_flagged"]
                row[f"{name}_effect"] = res[name]["diff"]
                row[f"{name}_se"] = _se(res[name])
            out.append(row)
    table = pd.DataFrame(out)
    table["agree"] = (
        (np.sign(table["fit_effect"]) == np.sign(table["test_effect"]))
        & table["fit_effect"].notna()
        & table["test_effect"].notna()
    )
    return table


def between_cell_variance(table: pd.DataFrame, min_events: int = MIN_EVENTS) -> float:
    """Method of moments: the spread of the cell effects beyond what their standard errors explain."""
    t = table[(table["n_events"] >= min_events) & table["effect"].notna() & table["se"].notna()]
    if len(t) < 3:
        return TAU2_FLOOR
    return float(max(TAU2_FLOOR, t["effect"].var(ddof=1) - (t["se"] ** 2).mean()))


def shrink_table(
    table: pd.DataFrame,
    *,
    tau2: float | None = None,
    min_events: int = MIN_EVENTS,
    require_agree: bool = True,
    min_abs_t: float = 0.0,
) -> pd.DataFrame:
    """`bump` per cell: the effect shrunk toward 0 by `tau2 / (tau2 + se^2)`; zero where there are too few
    events, where (`require_agree`) the fit and test periods disagree in sign, where |t| is under `min_abs_t`,
    or for a role the round did not ask for."""
    tau2 = between_cell_variance(table, min_events) if tau2 is None else tau2
    out = table.copy()
    w = tau2 / (tau2 + out["se"] ** 2)
    ok = (out["n_events"] >= min_events) & out["absent"].isin(ABSENT_ROLES) & (out["t"].abs() >= min_abs_t)
    if require_agree:
        ok &= out["agree"]
    out["bump"] = np.where(ok, out["effect"] * w, 0.0)
    out.attrs["tau2"] = tau2
    return out


def out_of_sample(rows: pd.DataFrame, min_events: int = MIN_EVENTS, min_abs_t: float = 0.0) -> dict:
    """Fit the table on the fit seasons, apply it to the test seasons' event rows. `better` is the
    game-clustered paired difference of squared and absolute error, old minus new (positive = the bump
    helps)."""
    fit = rows[rows["season"].isin(FIT_SEASONS)]
    table = shrink_table(cell_effects(fit), min_events=min_events, require_agree=False, min_abs_t=min_abs_t)
    lookup = {(r.absent, r.beneficiary): r.bump for r in table.itertuples()}
    test = rows[rows["season"].isin(TEST_SEASONS) & rows["event"]].copy()
    test["bump"] = [lookup.get((a, b), 0.0) for a, b in zip(test["abs_label"], test["label"], strict=True)]
    affected = test[test["bump"] != 0]
    out = {"table": table, "events": int(len(test)), "affected": int(len(affected))}
    if affected.empty:
        return out
    old, new = affected["resid"].to_numpy(), (affected["resid"] - affected["bump"]).to_numpy()
    for name, a, b in (("sq", old**2, new**2), ("abs", np.abs(old), np.abs(new))):
        diff = a - b
        codes = pd.factorize(affected["game_key"])[0]
        per_game = np.bincount(codes, weights=diff)
        n = len(diff)
        out[name] = {
            "old": float(a.mean()),
            "new": float(b.mean()),
            "better": float(diff.mean()),
            "se": float(np.sqrt((per_game**2).sum()) / n),
        }
    out["bias_old"] = float(old.mean())
    out["bias_new"] = float(new.mean())
    return out


def run(log=lambda msg: None) -> dict:
    """The whole study from the cache: the cell table, its shrunk version and the out-of-sample check."""
    from dfs.sim.pipeline import load_frame

    log("UM residuals, 2014-2025")
    resid = residual_frame(load_frame(TEST_SEASONS[-1], log=log))
    sp, games = data.read_stats_player(), data.read_games()
    rows = event_rows(sp, games, resid)
    log(f"{int(rows['event'].sum())} event rows, {int((~rows['event']).sum())} control rows")
    table = shrink_table(cell_effects(rows))
    return {
        "rows": rows,
        "table": table,
        "oos": out_of_sample(rows),
        "oos_t2": out_of_sample(rows, min_abs_t=2.0),
        "tau2": table.attrs["tau2"],
    }


def write_outputs(result: dict, directory=None) -> None:
    """`models/research/teammate_bump.csv` (+ metadata). Only the shipped cells' columns; a refit of this file
    is Sam's call (it feeds a shadow, not CalPts)."""
    table = result["table"]
    meta = metadata(
        "r7", list(FIT_SEASONS) + list(TEST_SEASONS), int(table["n_events"].sum()), tau2=result["tau2"]
    )
    write_csv(TABLE_FILE, table[COLUMNS], meta, directory)


def render(result: dict) -> str:
    t = result["table"]
    shown = t[t["absent"].isin(ABSENT_ROLES) & (t["n_events"] >= 8)]
    lines = [
        f"tau2 (between-cell variance, pts^2) = {result['tau2']:.3f}",
        "absent    teammate     n_ev    effect    se    t    fit(n)        test(n)       agree   bump",
    ]
    for r in shown.itertuples():
        lines.append(
            f"{r.absent:7}  {r.beneficiary:8} {r.n_events:6d}  {r.effect:+7.2f} {r.se:5.2f} {r.t:+5.1f}  "
            f"{r.fit_effect:+6.2f} ({r.fit_n:3d})  {r.test_effect:+6.2f} ({r.test_n:3d})  "
            f"{'yes' if r.agree else 'no ':4}  {r.bump:+6.2f}"
        )
    for key, label in (
        ("oos", "every cell with 15+ events"),
        ("oos_t2", "cells with |t| >= 2 in the fit seasons"),
    ):
        o = result[key]
        lines.append(
            f"\nout of sample ({label}): fit {FIT_SEASONS[0]}-{FIT_SEASONS[-1]}, "
            f"applied to {TEST_SEASONS[0]}-{TEST_SEASONS[-1]}"
        )
        lines.append(f"  event rows {o['events']}, of which the bump moved {o['affected']}")
        for k in ("sq", "abs"):
            if k in o:
                d = o[k]
                lines.append(
                    f"  {k:3} error: old {d['old']:.3f}  new {d['new']:.3f}  "
                    f"old-new {d['better']:+.3f} (SE {d['se']:.3f})"
                )
        if "bias_old" in o:
            lines.append(f"  mean residual: old {o['bias_old']:+.2f}  new {o['bias_new']:+.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    res = run(log=print)
    print(render(res))
    if "--write" in sys.argv:
        write_outputs(res)
        print(f"wrote models/research/{TABLE_FILE}")
