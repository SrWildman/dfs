"""The contest simulator checked against Sam's real contests: the field generator against each real field, and
(Part 4) the simulator's predicted money against what lineups actually won.

Local and read-only: the standings, salary snapshots, EdgeRaw snapshots and contest log are Sam's files under
`data/` (never in git). Everything known before a week's lock comes from the last snapshot before Sunday 1 pm
ET (`retro.edge_snapshot_before`) and the week's DraftKings salary file; nothing from the contest itself is
used to generate its field. The construction rates (stack, bring-back, salary left) are measured on the OTHER
weeks' real fields of the same format class.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.contests import field as field_mod
from dfs.contests import history, week_frames
from dfs.contests import standings as st
from dfs.derived import OUT_STATUSES
from dfs.player_join import normalize_name
from dfs.sim import dups, field_gen, payouts, retro

SEASON = retro.SEASON
CASH_FORMATS = {payouts.DOUBLE_UP, payouts.FIFTY_FIFTY}
NOT_PLAYING = frozenset({*OUT_STATUSES, "D"})
OWN_SOURCES = ("tffb", "gpp_own", "cash_own", "actual")


@dataclass
class RealContest:
    contest_id: str
    week: int
    series: str
    format: str  # the log's Format (Double Up, SE GPP, ...)
    fmt: str  # "cash" / "gpp"
    entries: int
    places_paid: int
    prize_pool: float
    fee: float
    standings: st.Standings
    features: pd.DataFrame  # contests.field features, one row per parsed lineup (+ `nkey`, normalised names)
    fpts: dict[str, float]  # normalised name -> actual DK points
    own: dict[str, float]  # normalised name -> actual %Drafted (fraction)

    @property
    def kind(self) -> payouts.ContestKind:
        return payouts.ContestKind(
            series=self.series,
            format=self.format,
            entries=self.entries,
            places_paid=self.places_paid,
            prize_pool=self.prize_pool,
            fee=self.fee,
            max_entries=payouts.max_entries_of(self.series),
        )

    @property
    def points(self) -> np.ndarray:
        return np.sort(self.standings.lineups["points"].to_numpy(float))[::-1]

    def copies(self) -> collections.Counter:
        return collections.Counter(
            tuple(sorted(normalize_name(n) for _, n in slots)) for slots in self.standings.lineups["slots"]
        )


def players_frame(week: int, season: int = SEASON) -> pd.DataFrame | None:
    """The week's DraftKings main-slate players (`Name, Position, Team, Opponent, Salary`)."""
    frame, _ = week_frames.week_frame(retro.DK_DIR, season, week)
    return frame


def load_real_contests(
    weeks: set[int] | None = None, folder: Path = retro.STANDINGS_DIR
) -> list[RealContest]:
    """Every Classic paid contest in the standings folder that the history log has a row for (optionally only
    `weeks`), parsed once, with its field features."""
    log = history.typed(history.read_log("data/contests/contest_log.csv"))
    log = log[log["season"] == SEASON].drop_duplicates("contest_key").set_index("contest_key")
    frames: dict[int, pd.DataFrame | None] = {}
    out = []
    for source in sorted(st.scan_standings(folder).sources, key=lambda s: s.contest_id):
        if source.contest_id not in log.index:
            continue
        m = log.loc[source.contest_id]
        week = int(m["week"])
        if (weeks is not None and week not in weeks) or m["format"] not in (
            payouts.FLAT_FORMATS | payouts.GPP_FORMATS
        ):
            continue
        s = st.parse_standings(source.raw, contest_id=source.contest_id, places_paid=int(m["places_paid"]))
        if s.kind != "Classic" or s.lineups.empty:
            continue
        if week not in frames:
            frames[week] = players_frame(week)
        feats = field_mod.field_stats(s, frames[week]).features
        feats["nkey"] = [tuple(sorted(normalize_name(n) for _, n in slots)) for slots in s.lineups["slots"]]
        own = s.ownership
        pct = own.groupby("player")["pct_drafted"].sum()
        scale = 100.0 if float(pct.max()) > 1.5 else 1.0
        out.append(
            RealContest(
                contest_id=source.contest_id,
                week=week,
                series=str(m["series"]),
                format=str(m["format"]),
                fmt="cash" if m["format"] in CASH_FORMATS else "gpp",
                entries=int(m["entries"]),
                places_paid=int(m["places_paid"]),
                prize_pool=float(m["prize_pool"]),
                fee=float(m["fee"]),
                standings=s,
                features=feats,
                fpts={
                    normalize_name(p): float(v)
                    for p, v in zip(own["player"], own["fpts"], strict=True)
                    if v == v
                },
                own={normalize_name(p): float(v) / scale for p, v in pct.items()},
            )
        )
    return out


def rates_for(
    contests: list[RealContest],
    fmt: str,
    *,
    exclude_week: int | None = None,
    herd_keys: dict[int, set[tuple[str, ...]]] | None = None,
) -> field_gen.FieldRates:
    """Construction rates pooled over the real fields of format class `fmt`, leaving out `exclude_week` and
    (with `herd_keys`, week -> the chalk lineups' name keys) the herd's lineups, which the generator places
    itself."""
    chosen = [c for c in contests if c.fmt == fmt and c.week != exclude_week] or [
        c for c in contests if c.fmt == fmt
    ]
    feats = []
    for c in chosen:
        f = c.features
        if herd_keys and c.week in herd_keys:
            f = f[~f["nkey"].isin(herd_keys[c.week])]
        feats.append(f)
    return field_gen.rates_from_features(pd.concat(feats, ignore_index=True))


def chalk_keys(slate: field_gen.Slate, chalk: np.ndarray) -> list[tuple[str, ...]]:
    return [tuple(sorted(slate.names[i] for i in row)) for row in chalk]


def herd_stats(c: RealContest, keys: list[tuple[str, ...]]) -> tuple[float, float]:
    """The share of a real field on the chalk lineups, and the share of that held by its most-copied one."""
    copies = c.copies()
    counts = [copies.get(k, 0) for k in keys]
    total = sum(counts)
    return total / max(len(c.standings.lineups), 1), (max(counts) / total if total else 0.0)


def decay_for_top_share(top: float, k: int = field_gen.HERD_K) -> float:
    """The geometric decay whose first weight of `k` is `top`."""
    from scipy.optimize import brentq

    if top <= 1.0 / k:
        return 1.0
    if top >= 0.999:
        return 0.0
    return float(brentq(lambda r: (1 - r) / (1 - r**k) - top, 1e-6, 1 - 1e-9))


def fit_herd(
    contests: list[RealContest], keys: dict[int, list[tuple[str, ...]]], fmt: str, *, exclude_week: int | None
) -> field_gen.HerdModel:
    """The herd's share and decay for format class `fmt`: the mean over the other weeks' real fields of the
    share on the chalk lineups, and the decay that gives their most-copied lineup its mean share of that."""
    stats = [
        herd_stats(c, keys[c.week])
        for c in contests
        if c.fmt == fmt and c.week != exclude_week and c.week in keys
    ]
    if not stats:
        return field_gen.HerdModel()
    share = float(np.mean([s for s, _ in stats]))
    top = float(np.mean([t for s, t in stats if s > 0])) if any(s > 0 for s, _ in stats) else 1.0
    return field_gen.HerdModel(share=share, decay=decay_for_top_share(top))


def week_slate_frame(week: int, season: int = SEASON) -> pd.DataFrame:
    """The week's main-slate players as they stood at the lock: DraftKings' salary file joined to the last
    pre-lock EdgeRaw snapshot by name and team (`name` normalised as in the standings). Players listed OUT, IR
    or Doubtful are dropped. Columns: name, display, position, team, opp, salary, tffb, gpp_own, cash_own,
    calpts, projpts."""
    dk = players_frame(week, season)
    if dk is None:
        raise FileNotFoundError(f"no DraftKings salary snapshot for Week {week}")
    path, _ = retro.edge_snapshot_before(week, season)
    edge = pd.read_csv(path)
    edge["_k"] = [
        (normalize_name(n), str(t).upper()) for n, t in zip(edge["Name"], edge["Team"], strict=True)
    ]
    edge = edge.drop_duplicates("_k").set_index("_k")

    def col(name: str) -> pd.Series:
        return (
            pd.to_numeric(edge[name], errors="coerce")
            if name in edge.columns
            else pd.Series(np.nan, index=edge.index)
        )

    keys = [(normalize_name(n), str(t).upper()) for n, t in zip(dk["Name"], dk["Team"], strict=True)]
    frame = pd.DataFrame(
        {
            "name": [k[0] for k in keys],
            "display": dk["Name"].astype(str).to_numpy(),
            "position": dk["Position"].astype(str).to_numpy(),
            "team": dk["Team"].astype(str).str.upper().to_numpy(),
            "opp": dk["Opponent"].astype(str).str.upper().to_numpy(),
            "salary": pd.to_numeric(dk["Salary"], errors="coerce").to_numpy(),
        }
    )
    for out, src in (
        ("tffb", "Own%"),
        ("gpp_own", "GppOwn"),
        ("cash_own", "CashOwn"),
        ("calpts", "CalPts"),
        ("projpts", "ProjPts"),
    ):
        frame[out] = [col(src).get(k, np.nan) for k in keys]
    avail = (
        edge["Avail"].astype(str).str.upper() if "Avail" in edge.columns else pd.Series("", index=edge.index)
    )
    frame["avail"] = [avail.get(k, "") for k in keys]
    return frame[~frame["avail"].isin(NOT_PLAYING)].reset_index(drop=True)


def with_field_own(
    frame: pd.DataFrame, contests: list[RealContest], week: int, season: int = SEASON
) -> pd.DataFrame:
    """`frame` with `gpp_own` / `cash_own` from the shadow ownership model (`field_own`) fitted on the weeks
    BEFORE `week` only; left blank when those weeks lack either format (the model needs both)."""
    from dfs import field_own

    parts = []
    for w in sorted({c.week for c in contests if c.week < week}):
        try:
            path, _ = retro.edge_snapshot_before(w, season)
        except FileNotFoundError:
            continue
        part, _ = field_own.week_frame(pd.read_csv(path), contests, w)
        parts.append(part)
    model = field_own.fit(pd.concat(parts, ignore_index=True)) if parts else None
    out = frame.copy()
    if model is None:
        return out
    path, _ = retro.edge_snapshot_before(week, season)
    edge = field_own.attach(pd.read_csv(path), model)
    key = [(normalize_name(n), str(t).upper()) for n, t in zip(edge["Name"], edge["Team"], strict=True)]
    for col, src in (("gpp_own", field_own.GPP_COLUMN), ("cash_own", field_own.CASH_COLUMN)):
        by_key = dict(zip(key, pd.to_numeric(edge[src], errors="coerce"), strict=True))
        out[col] = [by_key.get((n, t), np.nan) for n, t in zip(out["name"], out["team"], strict=True)]
    return out


def week_points(contests: list[RealContest], week: int) -> dict[str, float]:
    """Every player's actual DK points that week, from the union of the week's contests' player tables."""
    out: dict[str, float] = {}
    for c in contests:
        if c.week == week:
            out.update(c.fpts)
    return out


def slate_for(frame: pd.DataFrame, own: str = "tffb", contest: RealContest | None = None) -> field_gen.Slate:
    """The generator's `Slate`, owned by `own`: TFFB's projected Own% (`tffb`), the shadow `gpp_own` /
    `cash_own`, or the contest's real %Drafted (`actual`, the oracle that isolates the generator's own
    error)."""
    f = frame.copy()
    if own == "actual":
        if contest is None:
            raise ValueError("own='actual' needs the contest")
        f["own"] = f["name"].map(contest.own).fillna(0.0)
    else:
        f["own"] = f[own]
    return field_gen.Slate.from_frame(f, own_column="own")


# ---------------------------------------------------------------------------------------------------------
# Part 2 validation: the generated field against the real one
# ---------------------------------------------------------------------------------------------------------

RELEVANT_OWN = 0.01


def _real_feature_rates(c: RealContest) -> dict[str, float]:
    f = c.features[c.features["resolved"].astype(bool)]
    un = c.features["unspent"].dropna()
    flex = c.features["flex_pos"]
    return {
        "stack1": float(f["stack1"].mean()),
        "stack2": float(f["stack2"].mean()),
        "bringback": float(f["bringback"].mean()),
        "unspent": float(un.mean()),
        "unspent_1k": float((un >= 1000).mean()),
        "flex_te": float((flex == "TE").sum() / max((flex != "").sum(), 1)),
    }


def validate_contest(
    c: RealContest,
    frame: pd.DataFrame,
    rates: field_gen.FieldRates,
    points: dict[str, float],
    *,
    herd: field_gen.HerdModel | None = None,
    chalk: np.ndarray | None = None,
    own: str = "tffb",
    cash_curve: bool = True,
    seed: int = 0,
    cap: int = 20_000,
) -> dict:
    """One real contest against a field generated from pre-lock inputs: ownership error, construction rates,
    duplicates, and the field mean / cash line / top-1% line scored on the week's ACTUAL points, beside the
    fixed-offset lines (projected-ownership field mean + 0.6 / + 63.2)."""
    slate = slate_for(frame, own, c)
    n = min(c.entries, cap)
    curve = dups.double_up_ownership if (c.fmt == "cash" and cash_curve and own != "actual") else None
    targets = slate.targets(curve)
    gen = field_gen.generate_field(slate, rates, n, herd=herd, chalk=chalk, seed=seed, targets=targets)
    pts = np.array([points.get(name, 0.0) for name in slate.names])
    scores = gen.scores(pts)
    gl = field_gen.lines(scores, c.entries, c.places_paid)
    real = c.points
    rl = {
        "mean": float(real.mean()),
        "cash_line": float(c.standings.profile.cash_line),
        "top1": float(real[max(1, round(0.01 * len(real))) - 1]),
    }
    # The fixed-offset method's field mean: projected Own% (normalised to nine players) x the actual points.
    tffb = pd.to_numeric(frame["tffb"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy()
    fixed_mean = float(9.0 * tffb @ frame["name"].map(points).fillna(0.0).to_numpy() / tffb.sum())
    proj_slate = slate_for(frame, "tffb", c)  # same players, same order as `slate`
    real_own = np.array([c.own.get(name, 0.0) for name in slate.names])
    gen_own = gen.ownership()
    tffb_own = proj_slate.targets()
    input_own = targets
    rel = (real_own >= RELEVANT_OWN) | (tffb_own >= RELEVANT_OWN)
    gf = gen.features()
    rr = _real_feature_rates(c)
    model = dups.DUP_MODEL_CASH if c.fmt == "cash" else dups.DUP_MODEL_GPP
    share_by_name = dict(zip(proj_slate.names, tffb_own, strict=True))
    if c.fmt == "cash":
        share_by_name = dups.double_up_shares(share_by_name)
    model_c = field_gen.model_copies(gen, share_by_name, model, c.entries)
    gen_copies = gen.copies() * (c.entries / max(len(gen), 1))
    real_copies = c.copies()
    real_dup_share = float(sum(v for v in real_copies.values() if v >= 2) / max(len(c.standings.lineups), 1))
    return {
        "week": c.week,
        "contest": c.contest_id,
        "series": c.series,
        "fmt": c.fmt,
        "entries": c.entries,
        "own": own,
        "own_mae_gen": float(np.abs(gen_own - real_own)[rel].mean()),
        "own_mae_tffb": float(np.abs(tffb_own - real_own)[rel].mean()),
        "own_mae_input": float(np.abs(input_own - real_own)[rel].mean()),
        "own_gen_vs_target": float(np.abs(gen_own - targets)[rel].mean()),
        "stack1_gen": float(gf["stack1"].mean()),
        "stack1_real": rr["stack1"],
        "stack2_gen": float(gf["stack2"].mean()),
        "stack2_real": rr["stack2"],
        "bb_gen": float(gf["bringback"].mean()),
        "bb_real": rr["bringback"],
        "unspent_gen": float(gf["unspent"].mean()),
        "unspent_real": rr["unspent"],
        "flex_te_gen": float((gf["flex_pos"] == "TE").mean()),
        "flex_te_real": rr["flex_te"],
        "dup_share_gen": gen.dup_share(),
        "dup_share_real": real_dup_share,
        "top_dup_gen": float(max(collections.Counter(gen.keys()).values()) / max(len(gen), 1)),
        "top_dup_real": float(max(real_copies.values()) / max(len(c.standings.lineups), 1)),
        "copies_gen": float(gen_copies.mean()),
        "copies_model": float(model_c.mean()),
        "mean_gen": gl["mean"],
        "mean_real": rl["mean"],
        "mean_fixed": fixed_mean,
        "cash_gen": gl["cash_line"],
        "cash_real": rl["cash_line"],
        "cash_fixed": fixed_mean + 0.6 if c.fmt == "cash" else fixed_mean + 17.9,
        "top1_gen": gl["top1"],
        "top1_real": rl["top1"],
        "top1_fixed": fixed_mean + 63.2,
        "n_gen": len(gen),
    }
