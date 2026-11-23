"""Edge at lock (blueprint Phase D; `SPECS_A_TO_D.md` section 5): how far Sam's lineups out-projected the
field at lock, on a benchmark the tool does not control.

For each contest with saved standings (`sim.contest_retro.load_real_contests`: Classic, with a contest-log
row) and each distinct lineup of his in it, one row of `data/contests/edge_at_lock.csv`: his nine players
summed on CalPts and on the **four-source consensus** (the plain mean of `ProjPts` (TFFB), `SleeperPts`,
`FantasyProsPts` and `EspnPts`, skipping blanks; section 0 item 9: props are not in it), the same sums over
every lineup of the field, his place among them, how concentrated the field was, and what happened.

**The projections at lock** (`lock_frame`): the week's DraftKings main slate (`contests.week_frames`), joined
by normalised name to the last EdgeRaw snapshot before the lock (`sim.retro.edge_snapshot_before`, CalPts
from `with_calpts` where the snapshot had none). A source the snapshot lacks (Sleeper, FantasyPros and ESPN
were not in EdgeRaw before Week 6) is filled from the scored rows stamped at or before the lock, as the
blueprint's study did. `n_src` is the fewest sources any of his nine had. A snapshot taken before the
previous week's lock is not this week's: the week is skipped (`NoSnapshot`).

A player the frame lacks counts 0 toward a sum, and his lineup's `n_src` is 0. The field is computed in full
(a boolean matrix of lineups by players), whatever its size. Pure except `lock_frame` and `run`.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.paths import CONTESTS_DIR
from dfs.player_join import normalize_name

PATH = CONTESTS_DIR / "edge_at_lock.csv"
CONSENSUS = ("ProjPts", "SleeperPts", "FantasyProsPts", "EspnPts")
COLUMNS = [
    "season",
    "week",
    "contest_key",
    "format",
    "fee",
    "entrants",
    "field",
    "paid_share",
    "lineup_id",
    "entries",
    "snapshot",
    "proj_calpts",
    "proj_consensus",
    "n_src",
    "field_mean_calpts",
    "field_mean_consensus",
    "field_sd",
    "cut_proj",
    "edge",
    "pct_in_field",
    "modal_share",
    "copies_exact",
    "within_2_swaps",
    "realized",
    "field_mean_pts",
    "line",
    "margin",
]
SHARED_FOR_NEAR = 7  # "within two swaps": sharing seven or more of his nine


class NoSnapshot(Exception):
    """No EdgeRaw snapshot of this week before its lock."""


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def frame_from(base: pd.DataFrame, edge: pd.DataFrame, scored: pd.DataFrame | None = None) -> pd.DataFrame:
    """Players (index: normalised name) x `calpts`, `consensus`, `n_src`. `base` is the DK main slate
    (`Name`), `edge` the lock-time snapshot, `scored` the week's scored rows already cut to the lock. Pure."""
    keys = base["Name"].map(normalize_name)
    e = edge.copy()
    e["_k"] = e["Name"].map(normalize_name)
    e = e.drop_duplicates("_k").set_index("_k")
    out = pd.DataFrame(index=pd.Index(keys.drop_duplicates(), name="key"))
    out["calpts"] = _num(e["CalPts"]).reindex(out.index) if "CalPts" in e else np.nan
    srcs = pd.DataFrame(index=out.index)
    for c in CONSENSUS:
        srcs[c] = _num(e[c]).reindex(out.index) if c in e else np.nan
        if scored is not None and c in scored.columns:
            s = scored.assign(_k=scored["Name"].map(normalize_name)).drop_duplicates("_k").set_index("_k")
            srcs[c] = srcs[c].fillna(_num(s[c]).reindex(out.index))
    out["consensus"] = srcs.mean(axis=1, skipna=True)
    out["n_src"] = srcs.notna().sum(axis=1).astype(int)
    return out


def lock_frame(season: int, week: int) -> tuple[pd.DataFrame, str]:
    """`frame_from` for `week`'s lock, and the snapshot's stem. Raises `NoSnapshot`."""
    from dfs import results_update
    from dfs.contests import week_frames
    from dfs.sim import retro

    try:
        path, stamp = retro.edge_snapshot_before(week, season)
    except FileNotFoundError as exc:
        raise NoSnapshot(str(exc)) from exc
    if week > 1 and stamp <= week_frames.lock_utc(season, week - 1):
        raise NoSnapshot(f"no EdgeRaw snapshot of Week {week} before its lock (the newest is {path.stem})")
    scored = results_update.load_all_scored(season)
    edge = retro.with_calpts(pd.read_csv(path), scored, week, season)
    base, note = week_frames.week_frame(retro.DK_DIR, season, week)
    if base is None:
        raise NoSnapshot(note)
    lock = week_frames.lock_utc(season, week)
    s = scored[_num(scored["week"]) == week].copy()
    ts = pd.to_datetime(s["Snapshot"].astype(str), format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")
    return frame_from(base, edge, s[ts <= lock]), path.stem


def _keys(slots) -> tuple[str, ...]:  # noqa: ANN001 - ((slot, name), ...)
    return tuple(sorted(normalize_name(n) for _, n in slots))


def contest_rows(  # noqa: ANN001 - contest: a RealContest
    contest, frame: pd.DataFrame, me: str, *, season: int, snapshot: str = ""
) -> list[dict]:
    """One row per distinct lineup of `me` in `contest` (none when he is not in it). Pure."""
    lu = contest.standings.lineups
    if lu.empty:
        return []
    keys = [_keys(s) for s in lu["slots"]]
    cal = frame["calpts"].to_dict()
    con = frame["consensus"].to_dict()
    nsrc = frame["n_src"].to_dict()

    def total(k: tuple[str, ...], values: dict) -> float:
        return float(sum(0.0 if (v := values.get(n)) is None or v != v else v for n in k))

    cal_sum = np.array([total(k, cal) for k in keys])
    con_sum = np.array([total(k, con) for k in keys])
    points = lu["points"].to_numpy(float)
    n = len(keys)
    counts = Counter(keys)
    players = {p: i for i, p in enumerate(sorted({p for k in keys for p in k}))}
    matrix = np.zeros((n, len(players)), dtype=np.int8)
    for i, k in enumerate(keys):
        matrix[i, [players[p] for p in k]] = 1
    entrants = int(contest.entries or n)
    paid_share = (contest.places_paid / entrants) if entrants else 0.0
    line = float(contest.standings.profile.cash_line)
    mine: dict[tuple[str, ...], list[int]] = {}
    names = lu["entry_name"].astype(str).str.strip().str.casefold()
    for i, nm in enumerate(names):
        if nm == me.casefold():
            mine.setdefault(keys[i], []).append(i)
    out = []
    for k, idx in mine.items():
        i = idx[0]
        overlap = matrix @ matrix[i].astype(np.int32)
        realized = float(points[i])
        out.append(
            {
                "season": season,
                "week": contest.week,
                "contest_key": contest.contest_id,
                "format": contest.format,
                "fee": contest.fee,
                "entrants": entrants,
                "field": n,
                "paid_share": round(paid_share, 4),
                "lineup_id": str(lu["entry_id"].iloc[i]),
                "entries": len(idx),
                "snapshot": snapshot,
                "proj_calpts": round(cal_sum[i], 2),
                "proj_consensus": round(con_sum[i], 2),
                "n_src": int(min(int(nsrc.get(p, 0) or 0) for p in k)) if k else 0,
                "field_mean_calpts": round(float(cal_sum.mean()), 2),
                "field_mean_consensus": round(float(con_sum.mean()), 2),
                "field_sd": round(float(con_sum.std(ddof=1)) if n > 1 else 0.0, 2),
                "cut_proj": round(float(np.quantile(con_sum, 1 - paid_share)), 2),
                "edge": round(float(con_sum[i] - con_sum.mean()), 2),
                "pct_in_field": round(float((con_sum < con_sum[i]).mean()), 4),
                "modal_share": round(counts.most_common(1)[0][1] / n, 4),
                "copies_exact": counts[k] - len(idx),
                "within_2_swaps": round(float((overlap >= SHARED_FOR_NEAR).mean()), 4),
                "realized": round(realized, 2),
                "field_mean_pts": round(float(points.mean()), 2),
                "line": round(line, 2) if line == line else "",
                "margin": round(realized - line, 2) if line == line else "",
            }
        )
    return out


def _path(path: Path | None) -> Path:
    """`data/contests/edge_at_lock.csv`, or a secondary slate's own (`slate.data_path`)."""
    from dfs import slate

    return path or slate.data_path(PATH)


def read(path: Path | None = None) -> list[dict]:
    p = _path(path)
    if not p.exists():
        return []
    with p.open(newline="") as fh:
        return list(csv.DictReader(fh))


def replace_week(rows: list[dict], season: int, week: int, path: Path | None = None) -> None:
    """Write `rows` as the week's rows, keeping every other week's (a recompute replaces only its week). A
    secondary slate's rows carry its key."""
    from dfs import slate

    p = _path(path)
    rows = [slate.stamp(r) for r in rows]
    keep = [
        r for r in read(p) if not (str(r.get("season")) == str(season) and str(r.get("week")) == str(week))
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=slate.columns(COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in sorted(
            [*keep, *rows], key=lambda r: (int(r["season"]), int(r["week"]), str(r["contest_key"]))
        ):
            w.writerow(r)
    tmp.replace(p)


def run(season: int, week: int, path: Path | None = None, contests=None) -> int:  # noqa: ANN001
    """Compute the week's rows from its saved standings and write them (`replace_week`); the row count.
    Raises `NoSnapshot` when the week has no lock-time snapshot (nothing written)."""
    from dfs.contests import series as series_mod
    from dfs.sim import contest_retro

    frame, stem = lock_frame(season, week)
    if contests is None:
        contests = contest_retro.load_real_contests(weeks={week})
    rows = []
    for c in contests:
        rows += contest_rows(c, frame, series_mod.MY_USERNAME, season=season, snapshot=stem)
    replace_week(rows, season, week, path)
    return len(rows)


def _f(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _realized(r: dict) -> float | None:
    pts, field = _f(r.get("realized")), _f(r.get("field_mean_pts"))
    return None if pts is None or field is None else pts - field


def summary(rows: list[dict], season: int, week: int) -> dict:
    """The Close screen's tile and card: this week's mean edge and percentile (over his rows in double-ups and
    other cash contests, each distinct lineup once per contest), the season's weeks so far and their mean.
    `realized` is the same rows' actual margin over the field: his points minus the field's mean points
    (`realized - field_mean_pts`); the scorecard's status comes from it, the projected edge is its text."""
    from dfs import field_review

    def cash(r: dict) -> bool:
        return str(r.get("format")) in field_review.CASH_FORMATS

    mine = [r for r in rows if str(r.get("season")) == str(season) and cash(r)]
    by_week: dict[int, list[dict]] = {}
    for r in mine:
        by_week.setdefault(int(r["week"]), []).append(r)

    def mean(xs: list[float | None]) -> float | None:
        ys = [x for x in xs if x is not None]
        return round(sum(ys) / len(ys), 2) if ys else None

    weeks = {
        w: {
            "edge": mean([_f(r["edge"]) for r in rs]),
            "pct": mean([_f(r["pct_in_field"]) for r in rs]),
            "realized": mean([_realized(r) for r in rs]),
            "rows": len(rs),
            "n_src": min(int(_f(r["n_src"]) or 0) for r in rs),
        }
        for w, rs in sorted(by_week.items())
        if w <= week
    }
    this = weeks.get(week)
    return {
        "week": week,
        "this_week": this,
        "weeks": {str(w): v for w, v in weeks.items()},
        "season_edge": mean([v["edge"] for v in weeks.values()]),
        "season_pct": mean([v["pct"] for v in weeks.values()]),
        "season_realized": mean([v["realized"] for v in weeks.values()]),
        "n_weeks": len(weeks),
    }
