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


def pre_lock_edge(week: int, season: int = SEASON) -> pd.DataFrame:
    """The last pre-lock EdgeRaw snapshot with a `CalPts` for every scored row (the calibration fitted on the
    weeks before, where the snapshot had none)."""
    from dfs import results_update

    path, _ = retro.edge_snapshot_before(week, season)
    return retro.with_calpts(pd.read_csv(path), results_update.load_all_scored(season), week, season)


def slate_specs(slate: field_gen.Slate, frame: pd.DataFrame, edge: pd.DataFrame) -> list:
    """Each slate player's simulator spec (`sim_inputs.build_specs` on the pre-lock EdgeRaw, CalPts first),
    joined by normalised name and team; None for a player the simulator cannot rate."""
    from dfs import sim_inputs

    specs = sim_inputs.build_specs(edge)
    by_key = {(normalize_name(n), s.team.upper()): s for n, s in specs.items()}
    team = dict(zip(frame["name"], frame["team"], strict=False))
    return [by_key.get((name, str(team.get(name, "")).upper())) for name in slate.names]


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


# ---------------------------------------------------------------------------------------------------------
# Part 4: the retro of the contest simulator
# ---------------------------------------------------------------------------------------------------------

ME = "srwildman"
FIELD_SAMPLE = {"gpp": 60, "cash": 40}  # random real field lineups per week, besides all of Sam's


OWNERSHIP_SCENARIOS = 200
OWNERSHIP_CONTEST = {"cash": (10.0, 3000), "gpp": (5.0, 2000)}  # the stake and size the field stands for


def _ownership_targets(
    slate: field_gen.Slate,
    model,
    fmt: str,
    n_sims: int,
    draws: bool,
    seed: int,
    *,
    contest: tuple[float, int] | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """The generated field's targets from the ownership model: its fitted mean for `fmt` (TFFB's Own% in, at
    `contest`'s stake and size, default the format's representative one), and (with `draws`) scenarios drawn
    around it."""
    from dfs.sim import own_error

    mean_model = model.means.get(fmt)
    fee, entries = contest or OWNERSHIP_CONTEST[fmt]
    pos = list(slate.position)
    if mean_model is None:
        mean = slate.targets(dups.double_up_ownership if fmt == "cash" else None)
        totals = {p: float(mean[slate.position == p].sum()) for p in own_error.POSITIONS}
    else:
        mean = mean_model.shares(slate.own, pos, fee=fee, entries=entries)
        totals = mean_model.totals
    error = model.formats.get(fmt)
    if not draws or error is None:
        return mean, None
    rng = np.random.default_rng(seed + 7)
    k = min(OWNERSHIP_SCENARIOS, n_sims)
    return mean, own_error.draw_shares(mean, pos, list(slate.team), totals, error, k, rng)


def lead_ranks(contests: list[RealContest], keys: dict[int, list[tuple[str, ...]]]) -> dict[int, int]:
    """Per week, the product rank (0 = highest) of the chalk lineup the week's real contests copied most
    (copies pooled over every contest of the week; weeks where no chalk lineup was copied are left out)."""
    out = {}
    for week, ks in keys.items():
        total = np.zeros(len(ks))
        for c in contests:
            if c.week == week:
                cp = c.copies()
                total += [cp.get(k, 0) for k in ks]
        if total.max() > 1:
            out[week] = int(total.argmax())
    return out


def fit_lead_decay(ranks: dict[int, int], *, exclude_week: int | None) -> float:
    """The geometric decay of the lead lineup's rank, by maximum likelihood on the other weeks' ranks:
    `q = mean / (1 + mean)` (0 when every lead was the highest-product lineup)."""
    rs = [r for w, r in ranks.items() if w != exclude_week]
    if not rs:
        return 0.0
    mean = float(np.mean(rs))
    return mean / (1.0 + mean)


def real_lineup_indices(slots, index: dict[str, int]) -> np.ndarray | None:
    k = [index.get(normalize_name(n)) for _, n in slots]
    return None if any(i is None for i in k) else np.sort(np.array(k))


def realized_payout(
    c: RealContest, kind: payouts.ContestKind, lineup_names: tuple[str, ...], points: float, own_entry: bool
) -> float:
    """What the lineup was actually paid (or would have been, entered once more) in contest `c`: its place
    among the real scores and its real copies, paid from the contest's table."""
    from dfs.sim import contest_sim as csim

    copies = c.copies().get(lineup_names, 0)
    if own_entry:
        copies = max(copies - 1, 0)
    place = 1 + int((c.points > points + 1e-9).sum())
    cum = csim._cumulative(payouts.payout_table(kind))  # noqa: SLF001
    return float(csim.block_payout(cum, np.array([place], float), np.array([copies], float))[0])


def retro_week(
    week: int,
    contests: list[RealContest],
    *,
    keys: dict[int, list[tuple[str, ...]]],
    chalks: dict[int, np.ndarray],
    min_cash: payouts.MinCash,
    n_sims: int = 4000,
    seed: int = 0,
    ownership=None,  # dfs.sim.own_error.OwnershipErrorModel fitted without this week, or None (today)
    ownership_draws: bool = True,
    ownership_per_contest: bool = False,
) -> dict:
    """One week of the retro: Sam's real lineups and a sample of real field lineups, simulated in every real
    contest of the week from pre-lock inputs (fields, herd, rates fitted on the other weeks), beside today's
    Cash % / Top 1% and what each lineup was actually paid. Returns frames for `summarize_retro`.

    `ownership` (session H, shadow): the fields are raked to the model's fitted mean instead of TFFB / the
    double-up curve and (with `ownership_draws`) reweighted per draw to ownership drawn around it; today's
    Cash % / Top 1% get the same treatment (`field.with_ownership_draws`). `ownership_per_contest` gives each
    contest a field of its own: the format's field reweighted to the mean at THAT contest's stake and size
    (and drawn around it), which its duplicate estimate reads too."""
    from dfs import sim_inputs
    from dfs.sim import contest_sim as csim
    from dfs.sim import field as field_model
    from dfs.sim import simulate

    week_cs = [c for c in contests if c.week == week]
    frame = week_slate_frame(week)
    edge = pre_lock_edge(week)
    slate = slate_for(frame, "tffb")
    specs = slate_specs(slate, frame, edge)
    index = {n: i for i, n in enumerate(slate.names)}
    chalk = chalks[week]
    ranks = lead_ranks(contests, keys)
    lead = fit_lead_decay(ranks, exclude_week=week)
    herd_keys = {w: set(v) for w, v in keys.items()}
    fields = {}
    for fmt in sorted({c.fmt for c in week_cs}):
        rates = rates_for(contests, fmt, exclude_week=week, herd_keys=herd_keys)
        h = fit_herd(contests, keys, fmt, exclude_week=week)
        herd = field_gen.HerdModel(share=h.share, decay=h.decay, k=h.k, lead_decay=lead)
        targets = slate.targets(dups.double_up_ownership if fmt == "cash" else None)
        if ownership is not None:
            targets, scenarios = _ownership_targets(slate, ownership, fmt, n_sims, ownership_draws, seed)
        fields[fmt] = csim.build_field(slate, rates, herd, fmt=fmt, chalk=chalk, targets=targets, seed=seed)
        if ownership is not None and scenarios is not None:
            fields[fmt] = csim.with_scenarios(fields[fmt], scenarios)

    # candidates: every lineup of Sam's that week, then a random sample of real field lineups
    rng = np.random.default_rng(seed + week)
    cand: dict[tuple, dict] = {}
    for c in week_cs:
        rows = c.standings.lineups
        mine = rows["entry_name"].astype(str).str.casefold() == ME
        for slots in rows.loc[mine, "slots"]:
            idx = real_lineup_indices(slots, index)
            if idx is not None and all(specs[i] is not None for i in idx):
                cand.setdefault(tuple(idx), {"source": "sam", "entered": set()})["entered"].add(c.contest_id)
    for fmt, n in FIELD_SAMPLE.items():
        pool = [(c, s) for c in week_cs if c.fmt == fmt for s in c.standings.lineups["slots"]]
        if not pool:
            continue
        for j in rng.permutation(len(pool))[: n * 3]:
            c, slots = pool[j]
            idx = real_lineup_indices(slots, index)
            if idx is None or any(specs[i] is None for i in idx) or tuple(idx) in cand:
                continue
            cand[tuple(idx)] = {"source": f"field_{fmt}", "entered": {c.contest_id}}
            if sum(v["source"] == f"field_{fmt}" for v in cand.values()) >= n:
                break
    lineups = np.array(list(cand), dtype=int)
    labels = [f"w{week}-{i}" for i in range(len(lineups))]
    kinds = []
    for c in week_cs:
        k = c.kind
        if k.format in payouts.GPP_FORMATS:
            k = payouts.ContestKind(
                k.series + f" #{c.contest_id}",
                k.format,
                k.entries,
                k.places_paid,
                k.prize_pool,
                k.fee,
                min_cash.lookup(c.series, c.fee, c.format),
                k.max_entries,
            )
        else:
            k = payouts.ContestKind(
                k.series + f" #{c.contest_id}",
                k.format,
                k.entries,
                k.places_paid,
                k.prize_pool,
                k.fee,
                None,
                k.max_entries,
            )
        kinds.append(k)
    if ownership is not None and ownership_per_contest:
        for c, k in zip(week_cs, kinds, strict=True):
            mean, scenarios = _ownership_targets(
                slate, ownership, c.fmt, n_sims, ownership_draws, seed, contest=(c.fee, c.entries)
            )
            rows_ = scenarios if scenarios is not None else mean[None, :]
            fields[k.series] = csim.with_scenarios(fields[c.fmt], rows_, own=mean)
    res = csim.simulate_contests(fields, specs, lineups, kinds, labels=labels, n_sims=n_sims, seed=seed)
    fixed_herd = csim.simulate_contests(
        fields, specs, lineups[:1], kinds, n_sims=n_sims, seed=seed, per_draw_herd=False
    )

    # today's Cash % / Top 1% (relative lines on the projected field), same lineups
    edge_specs = sim_inputs.build_specs(edge)
    by_key = {(normalize_name(n), s.team.upper()): s for n, s in edge_specs.items()}
    team = dict(zip(frame["name"], frame["team"], strict=False))
    as_specs = [[by_key[(slate.names[i], str(team[slate.names[i]]).upper())] for i in row] for row in lineups]
    fm = field_model.build_field(
        edge_specs.values(), sim_inputs.ownership_by_name(edge), cash_offset=0.6, top_offset=63.2
    )
    if ownership is not None:
        fm = field_model.with_ownership_draws(
            fm, sim_inputs.ownership_by_name(edge), ownership, seed=seed, draws=ownership_draws
        )
    base = simulate.simulate_lineups(as_specs, n_sims=n_sims, seed=seed, field=fm)
    baseline = pd.DataFrame(
        {
            "lineup": labels,
            "cash_pct": [s.p_cash for s in base.lineups],
            "top1_pct": [s.p_gpp for s in base.lineups],
            "mean": [s.mean for s in base.lineups],
        }
    )
    # the same two numbers against the GENERATOR's per-draw lines (herd lead drawn per draw): Cash % against
    # the double-up field's cash line (the week's biggest double-up), Top 1% against the GPP field's top-1%
    # line
    sc = res.scores.astype(float)
    cash_kinds = [k for k in kinds if k.cash]
    gpp_kinds = [k for k in kinds if not k.cash]
    if cash_kinds:
        line = res.lines[max(cash_kinds, key=lambda k: k.entries).series][:, 0]
        baseline["gen_cash_pct"] = (sc >= line[:, None]).mean(axis=0)
    else:
        baseline["gen_cash_pct"] = np.nan
    if gpp_kinds:
        line = res.lines[max(gpp_kinds, key=lambda k: k.entries).series][:, 1]
        baseline["gen_top1_pct"] = (sc >= line[:, None]).mean(axis=0)
    else:
        baseline["gen_top1_pct"] = np.nan

    # realized
    pts = week_points(contests, week)
    real_rows = []
    for (key, meta), label in zip(cand.items(), labels, strict=True):
        names = tuple(sorted(slate.names[i] for i in key))
        for c, k in zip(week_cs, kinds, strict=True):
            p = sum(c.fpts.get(n, pts.get(n, 0.0)) for n in names)
            real_rows.append(
                {
                    "lineup": label,
                    "contest": k.series,
                    "source": meta["source"],
                    "entered": c.contest_id in meta["entered"],
                    "points": p,
                    "realized": realized_payout(c, k, names, p, c.contest_id in meta["entered"]),
                }
            )
    table = res.table.merge(pd.DataFrame(real_rows), on=["lineup", "contest"]).merge(baseline, on="lineup")
    table["week"] = week
    table["fmt"] = np.where(table["format"].isin(list(CASH_FORMATS)), "cash", "gpp")
    table["realized_roi"] = table["realized"] / table["fee"] - 1.0

    # line coverage: the real lines against the sim's 10-90% band, with and without the per-draw herd
    cover = []
    for c, k in zip(week_cs, kinds, strict=True):
        real = {
            "cash": float(c.standings.profile.cash_line),
            "top1": float(c.points[max(1, round(0.01 * len(c.points))) - 1]),
        }
        for mode, r in (("per_draw", res), ("fixed", fixed_herd)):
            band = r.lines[k.series]
            for j, line in enumerate(("cash", "top1")):
                lo, mid, hi = np.percentile(band[:, j], [10, 50, 90])
                cover.append(
                    {
                        "week": week,
                        "contest": k.series,
                        "fmt": c.fmt,
                        "entries": c.entries,
                        "mode": mode,
                        "line": line,
                        "real": real[line],
                        "p10": lo,
                        "p50": mid,
                        "p90": hi,
                        "inside": lo <= real[line] <= hi,
                    }
                )

    # Opt% / SimLev against the real top 1% of the week's big GPPs
    big = [c for c in week_cs if c.fmt == "gpp" and c.entries >= 700]
    opt = res.opt.copy()
    if big:
        top_counts, n_top = collections.Counter(), 0
        for c in big:
            rows = c.standings.lineups.sort_values("points", ascending=False)
            cut = max(1, round(0.01 * len(rows)))
            for slots in rows["slots"].head(cut):
                top_counts.update(normalize_name(n) for _, n in slots)
            n_top += cut
        real_own = pd.DataFrame({c.contest_id: pd.Series(c.own) for c in big}).fillna(0.0).mean(axis=1)
        opt["real_opt"] = opt["name"].map(lambda n: top_counts.get(n, 0) / n_top)
        opt["real_own"] = opt["name"].map(real_own).fillna(0.0)
        opt["week"] = week
    return {
        "table": table,
        "cover": pd.DataFrame(cover),
        "opt": opt,
        "lead_decay": lead,
        "seconds": res.seconds,
    }


# ---------------------------------------------------------------------------------------------------------
# The whole retro, its summary, and the weekly hook (`dfs research refresh`)
# ---------------------------------------------------------------------------------------------------------

SHIP_BAR = (
    "A ranking ships only if, held out by week, its within-contest Spearman with realized dollars beats "
    "today's (Top 1% for GPPs, Cash % for cash) by more than two standard errors over contests AND in a "
    "majority of the weeks, with at least 4 weeks scored"  # no full stop: the refresh report adds one
)
MEASURES = {
    "e_dollars": "E[$]",
    "e_dollars_herd_copies": "E[$], herd copies only",
    "e_dollars_ex_top": "E[$] without the top 0.1%",
    "gen_line": "today's number on the generator's lines",
}


def run_retro(
    through: int,
    *,
    weeks: list[int] | None = None,
    n_sims: int = 4000,
    log=print,
    ownership=None,
    ownership_draws: bool = True,
    ownership_per_contest: bool = False,
) -> dict[int, dict]:
    """`retro_week` for every week with standings up to `through` (or `weeks`). `ownership(week)` (optional)
    returns the ownership model fitted without that week (session H's shadow; None: today)."""
    contests = load_real_contests()
    chalks, keys = {}, {}
    for wk in sorted({c.week for c in contests}):
        slate = slate_for(week_slate_frame(wk), "tffb")
        chalks[wk] = field_gen.chalk_lineups(slate)
        keys[wk] = chalk_keys(slate, chalks[wk])
    mc = payouts.fit_min_cash(
        payouts.paid_entries(history.typed(history.read_log("data/contests/contest_log.csv")))
    )
    chosen = weeks or sorted(w for w in {c.week for c in contests} if w <= through)
    out = {}
    for wk in chosen:
        out[wk] = retro_week(
            wk,
            contests,
            keys=keys,
            chalks=chalks,
            min_cash=mc,
            n_sims=n_sims,
            ownership=ownership(wk) if ownership is not None else None,
            ownership_draws=ownership_draws,
            ownership_per_contest=ownership_per_contest,
        )
        log(f"week {wk}: {out[wk]['table']['lineup'].nunique()} lineups, sim {out[wk]['seconds']:.1f}s")
    return out


def _rho(x: pd.Series, y: pd.Series) -> float:
    from scipy.stats import spearmanr

    return float(spearmanr(x, y).statistic) if x.nunique() > 1 and y.nunique() > 1 else float("nan")


def ranking_table(table: pd.DataFrame) -> pd.DataFrame:
    """One row per contest (15+ lineups): the Spearman of each measure with realized dollars. `today` is Top
    1% for a GPP and Cash % for a cash contest; `gen_line` is the same number on the generator's lines."""
    rows = []
    for (week, contest, fmt), d in table.groupby(["week", "contest", "fmt"]):
        if len(d) < 15 or d["realized"].nunique() < 2:
            continue
        today, gen = ("top1_pct", "gen_top1_pct") if fmt == "gpp" else ("cash_pct", "gen_cash_pct")
        row = {
            "week": week,
            "contest": contest,
            "fmt": fmt,
            "n": len(d),
            "today": _rho(d[today], d["realized"]),
        }
        row["gen_line"] = _rho(d[gen], d["realized"])
        for m in ("e_dollars", "e_dollars_herd_copies", "e_dollars_ex_top"):
            row[m] = _rho(d[m], d["realized"])
        rows.append(row)
    return pd.DataFrame(rows)


def compare(rank: pd.DataFrame, measure: str, fmt: str) -> dict[str, float]:
    """`measure` against today's ranking in one format: mean difference over contests, its standard error,
    the contests and weeks where it was better."""
    d = rank[rank["fmt"] == fmt].dropna(subset=[measure, "today"])
    if d.empty:
        return {
            "diff": float("nan"),
            "se": float("nan"),
            "contests_better": 0,
            "contests": 0,
            "weeks_better": 0,
            "weeks": 0,
        }
    diff = d[measure] - d["today"]
    by_week = diff.groupby(d["week"]).mean()
    return {
        "rho": float(d[measure].mean()),
        "rho_today": float(d["today"].mean()),
        "diff": float(diff.mean()),
        "se": float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else float("nan"),
        "contests_better": int((diff > 0).sum()),
        "contests": int(len(diff)),
        "weeks_better": int((by_week > 0).sum()),
        "weeks": int(len(by_week)),
    }


def passes_bar(c: dict[str, float]) -> bool:
    return bool(c["weeks"] >= 4 and c["diff"] > 2 * c["se"] and c["weeks_better"] > c["weeks"] / 2)


def cash_recalibration(table: pd.DataFrame) -> dict[str, float]:
    """Fix (c2): P(cash) in cash contests recalibrated by a logistic map `logit(p') = a + b logit(p)` fitted
    on the OTHER weeks' (lineup, contest) pairs, judged on the held-out week: Brier and log loss before and
    after, over all pairs and over Sam's own entries."""
    from scipy.optimize import minimize

    d = table[table["fmt"] == "cash"].copy()
    if d["week"].nunique() < 2:
        return {}
    d["y"] = (d["realized"] > 0).astype(float)
    eps = 1e-4
    d["z"] = np.log(np.clip(d["p_cash"], eps, 1 - eps) / (1 - np.clip(d["p_cash"], eps, 1 - eps)))
    d["p_new"] = np.nan
    for wk in d["week"].unique():
        train = d[d["week"] != wk]

        def nll(theta, z=train["z"].to_numpy(), y=train["y"].to_numpy()):
            q = 1 / (1 + np.exp(-(theta[0] + theta[1] * z)))
            q = np.clip(q, eps, 1 - eps)
            return -float(np.sum(y * np.log(q) + (1 - y) * np.log(1 - q)))

        a, b = minimize(nll, [0.0, 1.0], method="Nelder-Mead").x
        m = d["week"] == wk
        d.loc[m, "p_new"] = 1 / (1 + np.exp(-(a + b * d.loc[m, "z"])))

    def scores(x: pd.DataFrame, col: str) -> tuple[float, float]:
        p = np.clip(x[col], eps, 1 - eps)
        return float(((p - x["y"]) ** 2).mean()), float(
            -(x["y"] * np.log(p) + (1 - x["y"]) * np.log(1 - p)).mean()
        )

    out = {}
    for label, x in (("all", d), ("sam", d[(d["source"] == "sam") & d["entered"]])):
        if x.empty:
            continue
        b0, l0 = scores(x, "p_cash")
        b1, l1 = scores(x, "p_new")
        out.update(
            {
                f"{label} brier": b0,
                f"{label} brier recal": b1,
                f"{label} logloss": l0,
                f"{label} logloss recal": l1,
                f"{label} p_cash": float(x["p_cash"].mean()),
                f"{label} p_recal": float(x["p_new"].mean()),
                f"{label} cash rate": float(x["y"].mean()),
                f"{label} n": int(len(x)),
            }
        )
    return out


def simlev_check(opt: pd.DataFrame) -> dict[str, float]:
    """Did high-SimLev players land in the real top 1% of the week's big GPPs more than ownership implied?
    Players projected or owned 1%+: correlation of SimLev with (real top-1% share - real ownership), and that
    excess in the top and bottom fifths of SimLev."""
    o = opt[(opt["own"] >= 0.01) | (opt["real_own"] >= 0.01)].copy()
    if o.empty:
        return {}
    o["excess"] = o["real_opt"] - o["real_own"]
    q = pd.qcut(o["sim_lev"].rank(method="first"), 5, labels=False)
    by_week = {
        int(w): float(np.corrcoef(x["sim_lev"], x["excess"])[0, 1])
        for w, x in o.groupby("week")
        if len(x) > 10
    }
    return {
        "n": int(len(o)),
        "corr": float(np.corrcoef(o["sim_lev"], o["excess"])[0, 1]),
        "rank_corr": _rho(o["sim_lev"], o["excess"]),
        "top_fifth_excess": float(o.loc[q == 4, "excess"].mean()),
        "bottom_fifth_excess": float(o.loc[q == 0, "excess"].mean()),
        "top_fifth_real_opt": float(o.loc[q == 4, "real_opt"].mean()),
        "top_fifth_real_own": float(o.loc[q == 4, "real_own"].mean()),
        "weeks_positive": int(sum(v > 0 for v in by_week.values())),
        "weeks": len(by_week),
        **{f"corr week {w}": v for w, v in by_week.items()},
    }


def best_in_check(table: pd.DataFrame, column: str = "roi") -> dict[str, float]:
    """Best in (the week's contest with the highest predicted `column` for each of Sam's lineups) against
    where he entered it: mean realized ROI of each, paired difference and its standard error."""
    sam = table[table["source"] == "sam"].copy()
    if sam.empty:
        return {}
    if column != "roi":
        sam[column] = sam[column.removeprefix("roi_")] / sam["fee"] - 1
    best = sam.loc[sam.groupby(["week", "lineup"])[column].idxmax()].set_index(["week", "lineup"])[
        "realized_roi"
    ]
    entered = sam[sam["entered"]].groupby(["week", "lineup"])["realized_roi"].mean()
    diff = (best - entered).dropna()
    return {
        "lineups": int(len(diff)),
        "best_in_roi": float(best.mean()),
        "entered_roi": float(entered.mean()),
        "diff": float(diff.mean()),
        "se": float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else float("nan"),
    }


def summarize_retro(out: dict[int, dict]) -> dict:
    """Every number the STOP reports and the weekly refresh read, from `run_retro`'s output."""
    table = pd.concat([o["table"] for o in out.values()], ignore_index=True)
    cover = pd.concat([o["cover"] for o in out.values()], ignore_index=True)
    opts = [o["opt"] for o in out.values() if "real_opt" in o["opt"]]
    rank = ranking_table(table)
    big = cover[cover["entries"] >= 700]
    gpp = table[table["fmt"] == "gpp"]
    return {
        "weeks": sorted(out),
        "table": table,
        "rank": rank,
        "compare": {(m, f): compare(rank, m, f) for m in MEASURES for f in ("gpp", "cash")},
        "coverage": big.groupby(["fmt", "line", "mode"])["inside"].mean().to_dict(),
        "top_share": float(gpp["e_top"].sum() / gpp["e_dollars"].sum()) if len(gpp) else float("nan"),
        "calibration": {
            f: {
                "pred_roi": float(x["roi"].mean()),
                "real_roi": float(x["realized_roi"].mean()),
                "p_cash": float(x["p_cash"].mean()),
                "cash_rate": float((x["realized"] > 0).mean()),
            }
            for f, x in table.groupby("fmt")
        },
        "cash_recal": cash_recalibration(table),
        "simlev": simlev_check(pd.concat(opts, ignore_index=True)) if opts else {},
        "best_in": {
            c: best_in_check(table, c) for c in ("roi", "roi_e_dollars_ex_top", "roi_e_dollars_herd_copies")
        },
    }


def refresh_summary(season: int, through: int) -> dict:
    """The weekly hook for `dfs research refresh` (session B's contract): reruns the retro on every week with
    standings up to `through` and returns `{"headline": [...], "metrics": {...}, "ship_bar": str}`."""
    if season != SEASON:
        return {
            "headline": [f"The contest retro is wired for {SEASON} only."],
            "metrics": {},
            "ship_bar": SHIP_BAR,
        }
    return refresh_from(run_retro(through, log=lambda _msg: None))


def refresh_from(out: dict[int, dict]) -> dict:
    """`refresh_summary`'s dictionary from a `run_retro` output already in hand."""
    s = summarize_retro(out)
    metrics: dict[str, float | bool] = {"weeks": len(s["weeks"])}
    lines = []
    for (m, f), c in s["compare"].items():
        if not c.get("contests"):
            continue
        ok = passes_bar(c)
        metrics[f"{f} {m} rho"] = round(c["rho"], 4)
        metrics[f"{f} {m} minus today"] = round(c["diff"], 4)
        metrics[f"{f} {m} ships"] = ok
        lines.append(
            f"- {f.upper()} {MEASURES[m]}: rho {c['rho']:.3f} vs today {c['rho_today']:.3f} "
            f"({c['diff']:+.3f} +/- {c['se']:.3f}; better in {c['contests_better']}/{c['contests']} "
            f"contests, {c['weeks_better']}/{c['weeks']} weeks){' SHIPS' if ok else ''}"
        )
    shipping = [k for k, v in metrics.items() if k.endswith("ships") and v]
    head = f"Contest retro, weeks {', '.join(map(str, s['weeks']))}: " + (
        f"{len(shipping)} ranking(s) clear the bar." if shipping else "no ranking clears the bar yet."
    )
    sl = s["simlev"]
    if sl:
        metrics.update(
            {"simlev corr": round(sl["corr"], 4), "simlev top fifth excess": round(sl["top_fifth_excess"], 4)}
        )
        lines.append(
            f"- SimLev vs the real top 1%: corr {sl['corr']:.3f} (n {sl['n']}); top fifth "
            f"{sl['top_fifth_excess']:+.3f}, bottom fifth {sl['bottom_fifth_excess']:+.3f} over ownership"
        )
    bi = s["best_in"]["roi"]
    if bi:
        metrics.update({"best in minus entered roi": round(bi["diff"], 4)})
        lines.append(
            f"- Best in vs where he entered: {bi['best_in_roi']:+.3f} vs {bi['entered_roi']:+.3f} ROI "
            f"({bi['diff']:+.3f} +/- {bi['se']:.3f}, {bi['lineups']} lineups)"
        )
    for key, v in s["coverage"].items():
        metrics[f"coverage {' '.join(key)}"] = round(float(v), 4)
    metrics["gpp top 0.1% share of E[$]"] = round(s["top_share"], 4)
    rc = s["cash_recal"]
    if rc:
        metrics.update(
            {"cash brier": round(rc["all brier"], 4), "cash brier recal": round(rc["all brier recal"], 4)}
        )
        lines.append(
            f"- Cash P(cash) recalibrated held out: Brier {rc['all brier']:.4f} -> "
            f"{rc['all brier recal']:.4f}"
        )
    return {"headline": [head, *lines], "metrics": metrics, "ship_bar": SHIP_BAR}
