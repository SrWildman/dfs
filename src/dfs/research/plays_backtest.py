"""The plays model's history and backtest (Plan M, M1): every past week's calls as they would have stood at
the lock, the walk-forward weights, and the tests against the ship bar.

**Each week as it stood at the lock** (`week_inputs`): the last EdgeRaw snapshot before Sunday 1 pm ET, CalPts
on every rated row, cut to the main slate (`research.curse.week_inputs`, the Flashback's own). What the
snapshot lacks is filled the way the tool would have filled it with only earlier weeks to go on: `GppOwn` /
`CashOwn` from the field-ownership model fitted on the weeks BEFORE (`field_own`), the `FADE↓` chip from the
week's last pre-lock signal archive, the GPP field's construction rates from the earlier weeks' real fields.
The week's earlier snapshots (up to the same moment) feed the news path. Nothing from the games is read:
`plays.inputs_only` cuts every frame to the columns that exist before the lock.

**Targets** (what the weights learn from, and what the tests score): realized DraftKings points per $1K
(cash); a player's share of the real top-1% lineups minus his real ownership, over the week's GPPs of
`BIG_GPP` entries or more (GPP).

**Walk-forward.** Week N's calls use weights fitted on weeks before N only (`plays.fit_weights`), and the
late-news ownership factor likewise. The fitted weights are saved under `data/research/calls_weights/` with
the week they were fitted through.

**The tests** (`evaluate`), on every week with standings (2 and 4 on):

- *Cash*: CORE + PLAY minus AVOID in realized points per $1K, per week; the same gap for the baselines (CalPts
  per $1K alone, TFFB's ProjPts per $1K) labelled with the same counts; the ship bar is the calls' gap
  beating each baseline's by more than 2 SE of the paired weekly difference, in most weeks.
- *GPP*: TARGET + LEVERAGE players' real top-1% excess (share of the real top 1% minus real ownership) minus
  that of random players at the same position and price (within `plays.PRICE_BAND`), per week; combined
  one-sided p (Stouffer) below 0.05 and above each baseline (CalPts value; TFFB's ceiling per ownership) in
  most weeks.
- *News*: where a week has a Saturday and a Sunday-morning snapshot, both versions scored.

Local and read-only (Sam's files under `data/`); the report goes to `docs/research/plays/` (gitignored).

    python -m dfs.research.plays_backtest run [--weeks 2,4,5,6,7,8] [--fresh]
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import plays
from dfs.paths import DATA_DIR, REPO_ROOT
from dfs.player_join import normalize_name

SEASON = 2026
EVAL_WEEKS = (2, 4, 5, 6, 7, 8)  # every week with standings (Sam, 2026-11-05: Week 9 joins at its close)
BIG_GPP = 700  # a GPP with this many entries or more has a top 1% worth counting
CACHE_DIR = DATA_DIR / "research" / "calls_features"
REPORT_DIR = REPO_ROOT / "docs" / "research" / "plays"
CACHE_VERSION = "2"  # M1.6: FADE↓ as its measured effect, the QB downgrade, the label rules
SATURDAY_HOUR_ET = 24  # "Saturday": the last snapshot before Sunday 00:00 ET


# ---------------------------------------------------------------------------------------------------------
# One week as it stood at the lock
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Context:
    """Everything loaded once: the scored tables, the standings, the cash lines."""

    season: int
    scored: pd.DataFrame
    contests: list  # retro.ContestField
    real: list  # contest_retro.RealContest (for the GPP field's construction rates)
    lines: dict[int, float]

    @classmethod
    def load(cls, season: int = SEASON, through: int = 8, log: Callable[[str], None] = print) -> Context:
        from dfs import results_update
        from dfs.research import curse
        from dfs.sim import contest_retro, retro

        t = time.monotonic()
        scored = results_update.load_all_scored(season)
        contests = retro.load_contests()
        real = contest_retro.load_real_contests()
        lines = curse.lines_through(season, through)
        log(f"context loaded in {time.monotonic() - t:.0f} s ({len(contests)} contests)")
        return cls(season, scored, contests, real, lines)


@dataclass
class HistWeek:
    week: int
    now: datetime
    features: plays.WeekFeatures
    actual: dict[str, float]  # edge Name -> realized DK points
    cash_target: pd.Series | None  # aligned with features.frame
    gpp_target: pd.Series | None
    gpp_detail: pd.DataFrame | None  # name -> top1_share, own_actual
    cash_own_actual: dict[str, float]
    snapshot: str  # the snapshot the features came from
    fade: plays.FadeEffect | None = None  # FADE↓'s record over the scored weeks before this one


def field_own_filled(edge: pd.DataFrame, ctx: Context, week: int) -> pd.DataFrame:
    """`edge` with `GppOwn` / `CashOwn` where the snapshot had none: the shadow model fitted on the weeks
    before `week` only (left blank when those weeks lack either format)."""
    from dfs import field_own
    from dfs.sim import retro

    have = all(
        c in edge.columns and pd.to_numeric(edge[c], errors="coerce").notna().any()
        for c in ("GppOwn", "CashOwn")
    )
    if have:
        return edge
    parts = []
    for w in sorted({c.week for c in ctx.contests if c.week < week}):
        try:
            path, _ = retro.edge_snapshot_before(w, ctx.season)
        except FileNotFoundError:
            continue
        part, _ = field_own.week_frame(pd.read_csv(path, low_memory=False), ctx.contests, w)
        parts.append(part)
    model = field_own.fit(pd.concat(parts, ignore_index=True)) if parts else None
    if model is None:
        return edge
    return field_own.attach(edge, model)


def fade_filled(edge: pd.DataFrame, season: int, week: int, lock: datetime) -> pd.DataFrame:
    """`edge` with the `Edge` chips from the week's last pre-lock signal archive where the snapshot has none
    (weeks before the chips were written to EdgeRaw)."""
    if "Edge" in edge.columns and edge["Edge"].notna().any():
        return edge
    from dfs import signals_data

    archives = [(ts, p) for ts, p in signals_data.archive_files(season, week) if ts <= lock]
    if not archives:
        return edge
    arch = pd.read_csv(archives[-1][1], low_memory=False)
    if "Edge" not in arch.columns:
        return edge
    by = dict(zip(arch["Name"].map(normalize_name), arch["Edge"], strict=False))
    out = edge.copy()
    out["Edge"] = out["Name"].map(normalize_name).map(by)
    return out


def field_rates_before(ctx: Context, week: int):  # noqa: ANN201 - a small holder with `.rates`, or None
    """The GPP field's construction rates from the real GPP fields of earlier weeks (None: there are none)."""
    from dfs.sim import contest_retro

    before = [c for c in ctx.real if c.week < week and c.fmt == "gpp"]
    if not before:
        return None

    @dataclass
    class _Rates:
        rates: object

    return _Rates(contest_retro.rates_for(before, "gpp"))


def top1_shares(contests: Sequence, week: int) -> pd.DataFrame | None:
    """Per player (normalised name): his share of the real top-1% lineups and his real ownership, averaged
    over the week's GPPs of `BIG_GPP`+ entries. None when the week has none."""
    big = [c for c in contests if c.week == week and c.fmt == "gpp" and c.entries >= BIG_GPP]
    if not big:
        return None
    rows = []
    for c in big:
        n_top = max(1, round(0.01 * len(c.points)))
        line = float(c.points[n_top - 1])
        hits: dict[str, float] = {}
        total = 0.0
        for key, count in c.copies.items():
            pts = sum(c.fpts.get(k, 0.0) for k in key)
            if pts >= line - 1e-9:
                total += count
                for k in key:
                    hits[k] = hits.get(k, 0.0) + count
        names = set(c.own) | set(hits)
        for k in names:
            rows.append(
                {
                    "key": k,
                    "contest": c.contest_id,
                    "top1_share": hits.get(k, 0.0) / max(total, 1.0),
                    "own": c.own.get(k, 0.0),
                }
            )
    frame = pd.DataFrame(rows)
    out = frame.groupby("key")[["top1_share", "own"]].sum() / len(big)
    return out


def cash_own_actual(contests: Sequence, week: int) -> dict[str, float]:
    from dfs import field_own

    own, n = field_own.actual_ownership(contests, week, "cash")
    return own.to_dict() if n else {}


def fade_before(ctx: Context, week: int) -> plays.FadeEffect:
    """FADE↓'s Model Check record over the scored weeks BEFORE `week` (walk-forward: the live calls read the
    season's table, which only ever holds earlier weeks)."""
    from dfs import results_signals

    earlier = ctx.scored[pd.to_numeric(ctx.scored["week"], errors="coerce") < week]
    if earlier.empty:
        return plays.FadeEffect()
    rep = results_signals.signal_report(results_signals.evaluation_frame(earlier, ctx.season))
    row = rep[rep["Signal"] == plays.FADE_TOKEN]
    weeks = sorted(int(w) for w in pd.to_numeric(earlier["week"], errors="coerce").dropna().unique())
    return plays.FadeEffect.from_record(
        row.iloc[0].to_dict() if not row.empty else None, (weeks[0], weeks[-1]) if weeks else None
    )


def games_before(season: int, week: int) -> list[dict]:
    """The per-game usage rows `news_context` reads, as the live sync would have saved them before `week`:
    nflverse's player stats and snap counts (the research caches), games before the week only."""
    from dfs import usage_weeks
    from dfs.research import data

    sp = data.read_stats_player([season])
    sp = sp[pd.to_numeric(sp["week"], errors="coerce") < week]
    snaps = None
    try:
        sn = data.read_snaps([season])
        names = data.read_players()[["pfr_id", "display_name"]].dropna().drop_duplicates("pfr_id")
        sn = sn.merge(names, left_on="pfr_player_id", right_on="pfr_id", how="left")
        snaps = pd.DataFrame(
            {
                "NameKey": sn["display_name"].map(normalize_name),
                "Wk": pd.to_numeric(sn["week"], errors="coerce"),
                "Snap%": pd.to_numeric(sn["offense_pct"], errors="coerce"),
            }
        ).dropna(subset=["NameKey", "Wk"])
        snaps = snaps[snaps["Wk"] < week].drop_duplicates(["NameKey", "Wk"], keep="last")
    except Exception:  # noqa: BLE001 - no snaps: news_context finds no starter, so no QB news
        snaps = None
    return usage_weeks.player_weeks(sp, None, snaps).to_dict("records")


def snapshot_at(week: int, season: int, until: datetime) -> tuple[pd.DataFrame, datetime] | None:
    """The last EdgeRaw snapshot at or before `until` (the main slate cut is the caller's)."""
    snaps = plays.edge_snapshots(season, week, until)
    return (snaps[-1][1], snaps[-1][0]) if snaps else None


def week_inputs(
    ctx: Context, week: int, *, at: datetime | None = None, own_lag: plays.OwnLag | None = None
) -> HistWeek:
    """The week's features as they stood at the lock (or at `at`, an earlier moment), and its targets."""
    from dfs.contests import week_frames
    from dfs.research import curse
    from dfs.sim import retro

    wi = curse.week_inputs(week, lines=ctx.lines, scored=ctx.scored, contests=ctx.contests)
    now = wi.now if at is None else min(at, wi.now)
    if at is None:
        edge = wi.edge
        snap_name = retro.edge_snapshot_before(week, ctx.season)[0].name
    else:
        found = snapshot_at(week, ctx.season, now)
        if found is None:
            raise FileNotFoundError(f"no Week {week} snapshot before {now:%Y-%m-%d %H:%M} UTC")
        raw, stamp = found
        full = pd.read_csv(retro.EDGE_DIR / f"{stamp:%Y%m%dT%H%M%S}Z.csv", low_memory=False)
        edge = curse.main_slate(retro.with_calpts(full, ctx.scored, week, ctx.season), week)
        snap_name = f"{stamp:%Y%m%dT%H%M%S}Z.csv"
        now = stamp
    lock = week_frames.lock_utc(ctx.season, week)
    edge = field_own_filled(edge, ctx, week)
    edge = fade_filled(edge, ctx.season, week, lock)
    snaps = plays.edge_snapshots(ctx.season, week, now)
    fade = fade_before(ctx, week)
    wf = plays.week_features(
        edge,
        typed_cash=wi.typed_cash,
        now=now,
        snapshots=snaps,
        field_model=field_rates_before(ctx, week),
        own_lag=own_lag,
        carried=plays.carried_out(ctx.season, week),
        games=games_before(ctx.season, week),
        fade=fade,
    )
    frame = wf.frame
    actual = wi.actual
    pts = frame["Name"].map(actual)
    cash_y = pts / (frame["Salary"] / 1000)
    shares = top1_shares(ctx.contests, week)
    gpp_y, detail = None, None
    if shares is not None:
        key = frame["Name"].map(normalize_name)
        detail = pd.DataFrame(
            {
                "top1_share": key.map(shares["top1_share"]).fillna(0.0).to_numpy(),
                "own_actual": key.map(shares["own"]).fillna(0.0).to_numpy(),
            },
            index=frame.index,
        )
        gpp_y = detail["top1_share"] - detail["own_actual"]
        gpp_y = gpp_y.where(pts.notna())
    return HistWeek(
        week=week,
        now=now,
        features=wf,
        actual=actual,
        cash_target=cash_y if cash_y.notna().any() else None,
        gpp_target=gpp_y,
        gpp_detail=detail,
        cash_own_actual=cash_own_actual(ctx.contests, week),
        snapshot=snap_name,
        fade=fade,
    )


def cache_path(week: int, tag: str, code: str) -> Path:
    return CACHE_DIR / f"{SEASON}-wk{week:02d}-{tag}-{CACHE_VERSION}-{code}.pkl"


def cached_week(
    ctx: Context,
    week: int,
    *,
    tag: str = "lock",
    at: datetime | None = None,
    fresh: bool = False,
    own_lag=None,
) -> HistWeek:  # noqa: ANN001
    """`week_inputs`, saved per code state so a rerun is quick (`--fresh` recomputes)."""
    from dfs import backtest

    code = backtest.code_stamp()
    lag_tag = "" if own_lag is None else f"-lag{own_lag.cash:.3f}-{own_lag.gpp:.3f}"
    path = cache_path(week, tag + lag_tag, code)
    if path.exists() and not fresh and code != "nogit":
        try:
            return pickle.loads(path.read_bytes())
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
            pass
    hw = week_inputs(ctx, week, at=at, own_lag=own_lag)
    if code != "nogit":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(hw))
    return hw


# ---------------------------------------------------------------------------------------------------------
# Late news: how far the real ownership of a late-news beneficiary beat the prediction
# ---------------------------------------------------------------------------------------------------------


def late_news_rows(hw: HistWeek) -> pd.DataFrame:
    """One row per late-news beneficiary of the week (`plays.Bump.late` with a positive bump) and format: the
    ownership the calls showed at the lock (`pred`) and the real one (`actual`, 0 for none drafted)."""
    rows = []
    frame = hw.features.frame.set_index("Name")
    shares = hw.gpp_detail
    for b in hw.features.news.bumps:
        if not b.late or b.bump <= 0 or b.name not in frame.index:
            continue
        key = normalize_name(b.name)
        if hw.cash_own_actual:
            rows.append(
                {
                    "week": hw.week,
                    "name": b.name,
                    "fmt": "cash",
                    "pred": frame.at[b.name, "cash_own"],
                    "actual": hw.cash_own_actual.get(key, 0.0),
                }
            )
        if shares is not None:
            i = hw.features.frame.index[hw.features.frame["Name"] == b.name]
            if len(i):
                rows.append(
                    {
                        "week": hw.week,
                        "name": b.name,
                        "fmt": "gpp",
                        "pred": frame.at[b.name, "gpp_own"],
                        "actual": float(shares.loc[i[0], "own_actual"]),
                    }
                )
    return pd.DataFrame(rows, columns=["week", "name", "fmt", "pred", "actual"])


# ---------------------------------------------------------------------------------------------------------
# Labels for the baselines, and the measures
# ---------------------------------------------------------------------------------------------------------


def baseline_cash_labels(frame: pd.DataFrame, value: pd.Series) -> pd.Series:
    """The calls' label counts and rules applied to `value` alone: no chalk rule (no ownership), and the
    "never AVOID when nobody at his price beats him" guard read on `value` itself."""
    return plays.cash_labels(frame.assign(fade=0.0, cash_own=np.nan, cash_value=value), value)[0]


def baseline_gpp_set(frame: pd.DataFrame, score: pd.Series) -> pd.Series:
    """True for the top `TARGET_N + LEVERAGE_N` per position by `score` (the size of the calls' set)."""
    out = pd.Series(False, index=frame.index)
    for pos, idx in frame.groupby("Position").groups.items():
        n = plays.TARGET_N.get(pos, 2) + plays.LEVERAGE_N.get(pos, 2)
        out.loc[score.loc[idx].sort_values(ascending=False, kind="stable").index[:n]] = True
    return out


def tffb_gpp_score(frame: pd.DataFrame, edge_frame: pd.DataFrame | None = None) -> pd.Series:
    """TFFB's own projection and ownership: ProjPts per $1K over (TFFB Own% + 1%), in logs."""
    proj = frame["ProjPts"].fillna(frame["CalPts"])
    own = frame["gpp_own"].fillna(0.0)
    return np.log(proj.clip(lower=0.5) / (frame["Salary"] / 1000)) - np.log(own + 0.01)


def cash_gap(frame: pd.DataFrame, labels: pd.Series, target: pd.Series) -> tuple[float, int, int]:
    """CORE + PLAY minus AVOID in realized points per $1K, and the two counts."""
    good = target[labels.isin(["CORE", "PLAY"]) & target.notna()]
    bad = target[labels.eq("AVOID") & target.notna()]
    if good.empty or bad.empty:
        return float("nan"), len(good), len(bad)
    return float(good.mean() - bad.mean()), len(good), len(bad)


def matched_excess(frame: pd.DataFrame, chosen: pd.Series, excess: pd.Series) -> tuple[float, float, int]:
    """Mean over the chosen players of (his real top-1% excess minus the mean excess of the unchosen players
    at his position within `PRICE_BAND` of his salary), its SE and n."""
    diffs = []
    for i in frame.index[chosen & excess.notna()]:
        pool = frame[
            (frame["Position"] == frame.at[i, "Position"])
            & ((frame["Salary"] - frame.at[i, "Salary"]).abs() <= plays.PRICE_BAND)
            & ~chosen
            & excess.notna()
        ]
        if pool.empty:
            continue
        diffs.append(float(excess.at[i] - excess.loc[pool.index].mean()))
    if not diffs:
        return float("nan"), float("nan"), 0
    d = np.asarray(diffs)
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("nan")
    return float(d.mean()), se, len(d)


def mean_se(x: Sequence[float]) -> tuple[float, float, int]:
    v = np.asarray([a for a in x if a == a], dtype=float)
    if len(v) == 0:
        return float("nan"), float("nan"), 0
    return float(v.mean()), float(v.std(ddof=1) / math.sqrt(len(v))) if len(v) > 1 else float("nan"), len(v)


def stouffer(zs: Sequence[float]) -> tuple[float, float]:
    """Combined z and its one-sided p."""
    from scipy.stats import norm

    v = [z for z in zs if z == z]
    if not v:
        return float("nan"), float("nan")
    z = float(sum(v) / math.sqrt(len(v)))
    return z, float(1 - norm.cdf(z))


def spearman(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 5:
        return float("nan")
    return float(a[m].rank().corr(b[m].rank()))


# ---------------------------------------------------------------------------------------------------------
# The walk-forward run
# ---------------------------------------------------------------------------------------------------------


@dataclass
class WeekResult:
    week: int
    calls: plays.Calls
    frame: pd.DataFrame  # features + labels + targets + baseline labels
    weights: plays.Weights
    cash: dict = field(default_factory=dict)
    gpp: dict = field(default_factory=dict)
    held_out: dict = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    calls_fade: plays.FadeEffect | None = None


def train_set(history: dict[int, HistWeek], before: int) -> dict:
    return {w: (hw.features.frame, hw.cash_target, hw.gpp_target) for w, hw in history.items() if w < before}


def score_week(hw: HistWeek, weights: plays.Weights, season: int = SEASON) -> WeekResult:
    calls = plays.make_calls(hw.features, weights, season=season, week=hw.week, at=hw.now, fade=hw.fade)
    frame = hw.features.frame.reset_index(drop=True).copy()
    lab = pd.DataFrame(
        [
            {
                "Name": c.name,
                "cash": c.cash,
                "gpp": c.gpp,
                "cash_score": c.cash_score,
                "gpp_score": c.gpp_score,
            }
            for c in calls.calls
        ]
    )
    frame = frame.merge(lab, on="Name", how="left")
    frame["pts"] = frame["Name"].map(hw.actual)
    frame["ppk"] = frame["pts"] / (frame["Salary"] / 1000)
    if hw.gpp_detail is not None:
        detail = hw.gpp_detail.reset_index(drop=True)
        frame["top1_share"] = detail["top1_share"].to_numpy()
        frame["own_actual"] = detail["own_actual"].to_numpy()
        frame["excess"] = (frame["top1_share"] - frame["own_actual"]).where(frame["pts"].notna())
    frame["base_cal"] = baseline_cash_labels(frame, frame["value"])
    frame["base_tffb"] = baseline_cash_labels(
        frame, frame["ProjPts"].fillna(frame["CalPts"]) / (frame["Salary"] / 1000)
    )
    res = WeekResult(hw.week, calls, frame, weights)
    res.calls_fade = hw.fade
    res.contradictions = [
        f"Week {hw.week} {c.name}: {b}" for c in calls.calls for b in plays.contradictions(plays.call_dict(c))
    ]
    # cash
    target = frame["ppk"]
    g, n_good, n_bad = cash_gap(frame, frame["cash"], target)
    res.cash = {"gap": g, "n_good": n_good, "n_bad": n_bad}
    for base in ("base_cal", "base_tffb"):
        res.cash[base] = cash_gap(frame, frame[base], target)[0]
    med = frame[frame["relevant"]].groupby("Position")["ppk"].median()
    above = frame["ppk"] >= frame["Position"].map(med)
    for base in ("base_cal", "base_tffb"):
        for label in ("CORE", "PLAY", "AVOID"):
            m = (frame[base] == label) & frame["ppk"].notna()
            hit = above[m] if label != "AVOID" else ~above[m]
            res.cash[f"{base} n {label}"] = int(m.sum())
            res.cash[f"{base} hit {label}"] = float(hit.mean()) if m.any() else float("nan")
    for label in plays.CASH_LABELS:
        m = (frame["cash"] == label) & frame["ppk"].notna()
        hit = above[m] if label in ("CORE", "PLAY") else ~above[m]
        res.cash[f"n {label}"] = int(m.sum())
        res.cash[f"hit {label}"] = float(hit.mean()) if m.any() else float("nan")
        res.cash[f"ppk {label}"] = float(frame.loc[m, "ppk"].mean()) if m.any() else float("nan")
    # gpp
    if "excess" in frame.columns:
        chosen = frame["gpp"].isin(["TARGET", "LEVERAGE"])
        d, se, n = matched_excess(frame, chosen, frame["excess"])
        res.gpp = {"diff": d, "se": se, "n": n, "z": d / se if se and se == se and se > 0 else float("nan")}
        for name, score in (("base_cal", frame["value"]), ("base_tffb", tffb_gpp_score(frame))):
            chosen_b = baseline_gpp_set(frame, score)
            res.gpp[name] = matched_excess(frame, chosen_b, frame["excess"])[0]
            m = chosen_b & frame["excess"].notna()
            res.gpp[f"{name} n set"] = int(m.sum())
            res.gpp[f"{name} hit set"] = (
                float((frame.loc[m, "excess"] > 0).mean()) if m.any() else float("nan")
            )
        for label in plays.GPP_LABELS:
            m = (frame["gpp"] == label) & frame["excess"].notna()
            hit = (
                frame.loc[m, "excess"] > 0 if label in ("TARGET", "LEVERAGE") else frame.loc[m, "excess"] <= 0
            )
            res.gpp[f"n {label}"] = int(m.sum())
            res.gpp[f"hit {label}"] = float(hit.mean()) if m.any() else float("nan")
            res.gpp[f"excess {label}"] = float(frame.loc[m, "excess"].mean()) if m.any() else float("nan")
    # held out: the model's score against the standardised target, walk-forward vs the prior
    prior_sc = plays.scores(hw.features.frame.reset_index(drop=True), plays.Weights.prior(season))
    rel = frame["relevant"].astype(bool)
    if hw.cash_target is not None:
        zt = plays.standardise_target(frame, frame["ppk"])
        res.held_out["cash rho"] = spearman(frame.loc[rel, "cash_score"], zt[rel])
        res.held_out["cash rho prior"] = spearman(prior_sc.loc[rel, "cash_score"], zt[rel])
        res.held_out["cash rho value"] = spearman(frame.loc[rel, "value"], zt[rel])
    if "excess" in frame.columns:
        zt = plays.standardise_target(frame, frame["excess"])
        res.held_out["gpp rho"] = spearman(frame.loc[rel, "gpp_score"], zt[rel])
        res.held_out["gpp rho prior"] = spearman(prior_sc.loc[rel, "gpp_score"], zt[rel])
    return res


def run(
    weeks: Sequence[int] = EVAL_WEEKS,
    *,
    season: int = SEASON,
    train_from: int = 1,
    fresh: bool = False,
    save_weights: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[dict[int, WeekResult], dict[int, HistWeek], list[plays.Weights]]:
    """The walk-forward backtest: every week from `train_from` through the last of `weeks` is loaded (the
    earlier ones only train), each week in `weeks` scored on weights fitted before it."""
    last = max(weeks)
    ctx = Context.load(season, last, log=log)
    history: dict[int, HistWeek] = {}
    results: dict[int, WeekResult] = {}
    fitted: list[plays.Weights] = []
    lag_rows: list[pd.DataFrame] = []
    for w in range(train_from, last + 1):
        t = time.monotonic()
        lag = plays.fit_own_lag(pd.concat(lag_rows, ignore_index=True) if lag_rows else pd.DataFrame())
        try:
            hw = cached_week(ctx, w, fresh=fresh, own_lag=lag if lag.n else None)
        except (FileNotFoundError, KeyError, ValueError) as e:
            log(f"  week {w}: skipped ({type(e).__name__}: {e})")
            continue
        weights = plays.fit_weights(train_set(history, w), season=season, through=w - 1, own_lag=lag)
        if save_weights and w - 1 >= 1 and history:
            plays.save_weights(weights)
        fitted.append(weights)
        history[w] = hw
        lag_rows.append(late_news_rows(hw))
        if w in weeks:
            results[w] = score_week(hw, weights, season)
        log(f"  week {w}: {len(hw.features.frame)} players, {time.monotonic() - t:.0f} s")
    final = plays.fit_weights(
        train_set(history, last + 1),
        season=season,
        through=last,
        own_lag=plays.fit_own_lag(pd.concat(lag_rows, ignore_index=True) if lag_rows else pd.DataFrame()),
    )
    if save_weights:
        plays.save_weights(final)
    fitted.append(final)
    return results, history, fitted


# ---------------------------------------------------------------------------------------------------------
# The news path: Saturday's calls against Sunday morning's
# ---------------------------------------------------------------------------------------------------------


def saturday_cutoff(season: int, week: int) -> datetime:
    """Sunday 00:00 ET of the week, in UTC: "Saturday" is every snapshot before it."""
    from dfs.contests import week_frames

    lock = week_frames.lock_utc(season, week)
    return lock - timedelta(hours=13)


def news_path(
    ctx: Context,
    weeks: Sequence[int],
    weights_by_week: dict[int, plays.Weights],
    log: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Per week with a Saturday and a Sunday-morning snapshot: the calls scored both ways."""
    rows = []
    for w in weeks:
        sat_cut = saturday_cutoff(ctx.season, w)
        snaps = plays.edge_snapshots(ctx.season, w, sat_cut)
        if not snaps:
            log(f"  week {w}: no Saturday snapshot")
            continue
        try:
            sat = cached_week(ctx, w, tag="saturday", at=sat_cut)
            sun = cached_week(ctx, w)
        except (FileNotFoundError, KeyError, ValueError) as e:
            log(f"  week {w}: news path skipped ({e})")
            continue
        if sat.snapshot == sun.snapshot:
            continue
        wts = weights_by_week.get(w) or plays.Weights.prior(ctx.season)
        a, b = score_week(sat, wts), score_week(sun, wts)
        both = a.frame[["Name", "cash", "gpp"]].merge(
            b.frame[["Name", "cash", "gpp"]], on="Name", suffixes=("_sat", "_sun")
        )
        rows.append(
            {
                "week": w,
                "saturday": sat.snapshot,
                "sunday": sun.snapshot,
                "cash gap sat": a.cash.get("gap"),
                "cash gap sun": b.cash.get("gap"),
                "gpp diff sat": a.gpp.get("diff"),
                "gpp diff sun": b.gpp.get("diff"),
                "cash calls changed": int((both["cash_sat"] != both["cash_sun"]).sum()),
                "gpp calls changed": int((both["gpp_sat"] != both["gpp_sun"]).sum()),
                "bumps sun": sum(1 for x in sun.features.news.bumps if x.applied > 0),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------
# The verdict and the report
# ---------------------------------------------------------------------------------------------------------

SHIP_BAR = (
    "cash: the calls' CORE+PLAY minus AVOID gap (points per $1K) beats each baseline's by more than 2 SE of "
    "the paired weekly difference and in most weeks; GPP: TARGET+LEVERAGE over-represented in the real "
    "top 1% "
    "against same-position, same-price players with a combined one-sided p below 0.05, and above each "
    "baseline in most weeks"
)


def summarize(results: dict[int, WeekResult]) -> dict:
    weeks = sorted(results)
    out: dict = {"weeks": weeks}
    gaps = [results[w].cash.get("gap", float("nan")) for w in weeks]
    out["cash gap"] = mean_se(gaps)
    for base in ("base_cal", "base_tffb"):
        diffs = [
            results[w].cash.get("gap", float("nan")) - results[w].cash.get(base, float("nan")) for w in weeks
        ]
        m, se, n = mean_se(diffs)
        won = sum(1 for d in diffs if d == d and d > 0)
        out[f"cash vs {base}"] = {
            "diff": m,
            "se": se,
            "weeks": n,
            "won": won,
            "baseline gap": mean_se([results[w].cash.get(base, float("nan")) for w in weeks])[0],
            "clears": bool(n and se == se and m > 2 * se and won > n / 2),
        }
    gw = [w for w in weeks if results[w].gpp]
    zs = [results[w].gpp.get("z", float("nan")) for w in gw]
    z, p = stouffer(zs)
    out["gpp"] = {
        "weeks": gw,
        "diff": mean_se([results[w].gpp.get("diff", float("nan")) for w in gw]),
        "z": z,
        "p": p,
    }
    for base in ("base_cal", "base_tffb"):
        won = sum(
            1 for w in gw if results[w].gpp.get("diff", float("nan")) > results[w].gpp.get(base, float("nan"))
        )
        out["gpp"][f"won vs {base}"] = won
        out["gpp"][f"{base} diff"] = mean_se([results[w].gpp.get(base, float("nan")) for w in gw])[0]
    out["gpp"]["clears"] = bool(
        gw
        and p == p
        and p < 0.05
        and all(out["gpp"][f"won vs {b}"] > len(gw) / 2 for b in ("base_cal", "base_tffb"))
    )
    out["cash clears"] = all(out[f"cash vs {b}"]["clears"] for b in ("base_cal", "base_tffb"))
    for kind, labels in (("cash", plays.CASH_LABELS), ("gpp", plays.GPP_LABELS)):
        for label in labels:
            n = sum(getattr(results[w], kind).get(f"n {label}", 0) for w in weeks)
            hits = sum(
                getattr(results[w], kind).get(f"hit {label}", 0.0)
                * getattr(results[w], kind).get(f"n {label}", 0)
                for w in weeks
                if getattr(results[w], kind).get(f"n {label}", 0)
            )
            out[f"{kind} hit {label}"] = (hits / n if n else float("nan"), n)
    for k in ("cash rho", "cash rho prior", "cash rho value", "gpp rho", "gpp rho prior"):
        out[k] = mean_se([results[w].held_out.get(k, float("nan")) for w in weeks])
    for base in ("base_cal", "base_tffb"):
        for label in ("CORE", "PLAY", "AVOID"):
            n = sum(results[w].cash.get(f"{base} n {label}", 0) for w in weeks)
            hits = sum(
                results[w].cash.get(f"{base} hit {label}", 0.0) * results[w].cash.get(f"{base} n {label}", 0)
                for w in weeks
                if results[w].cash.get(f"{base} n {label}", 0)
            )
            out[f"cash {base} hit {label}"] = (hits / n if n else float("nan"), n)
        n = sum(results[w].gpp.get(f"{base} n set", 0) for w in gw)
        hits = sum(
            results[w].gpp.get(f"{base} hit set", 0.0) * results[w].gpp.get(f"{base} n set", 0)
            for w in gw
            if results[w].gpp.get(f"{base} n set", 0)
        )
        out[f"gpp {base} hit set"] = (hits / n if n else float("nan"), n)
    n = sum(results[w].gpp.get(f"n {lab}", 0) for w in gw for lab in ("TARGET", "LEVERAGE"))
    hits = sum(
        results[w].gpp.get(f"hit {lab}", 0.0) * results[w].gpp.get(f"n {lab}", 0)
        for w in gw
        for lab in ("TARGET", "LEVERAGE")
        if results[w].gpp.get(f"n {lab}", 0)
    )
    out["gpp hit set"] = (hits / n if n else float("nan"), n)
    out["contradictions"] = [c for w in weeks for c in results[w].contradictions]
    out["news"] = news_check(results)
    out["fade"] = {
        str(w): {"points": r.calls_fade.points, "n": r.calls_fade.n, "applies": r.calls_fade.applies}
        for w, r in sorted(results.items())
        if r.calls_fade is not None
    }
    return out


NEWS_KINDS = (
    ("qb", "QB downgrade (R8)", lambda f: f["qb"] < 0),
    ("bump", "teammate out (R7 bump)", lambda f: f["bump"] > 0),
    ("fade", "FADE↓ (Model Check record)", lambda f: f["fade_pts"] < 0),
)


def news_check(results: dict[int, WeekResult]) -> dict:
    """Did the news points help against the realized points? Per kind over every scored week: the flagged
    players' mean (actual - CalPts) minus the same position's unflagged relevant players', with CalPts as it
    stood before the points (`pre`) and after (`post`), and the flagged players' mean absolute error both
    ways. This is the 2026 check "against what the projections already moved": CalPts at the lock already
    holds whatever the market moved."""
    parts = []
    for w, r in sorted(results.items()):
        f = r.frame
        if "pts" not in f.columns:
            continue
        pre = f["CalPts"] - f["bump"] - f.get("qb", 0.0) - f.get("fade_pts", 0.0)
        parts.append(f.assign(week=w, pre=pre, post=f["CalPts"]))
    if not parts:
        return {}
    f = pd.concat(parts, ignore_index=True)
    f = f[f["pts"].notna() & f["relevant"].astype(bool)]
    out = {}
    for key, title, flag in NEWS_KINDS:
        if key not in f.columns and key != "fade":
            continue
        if key == "fade" and "fade_pts" not in f.columns:
            continue
        m = flag(f).fillna(False).astype(bool)
        if not m.any():
            out[key] = {"title": title, "n": 0}
            continue
        ctrl = f[~m & ~(f["bump"] > 0) & ~(f.get("qb", 0.0) < 0) & ~(f.get("fade_pts", 0.0) < 0)]  # no news
        base = ctrl.groupby("Position").apply(lambda g: (g["pts"] - g["pre"]).mean(), include_groups=False)
        adj = f.loc[m, "Position"].map(base).fillna(0.0)
        r_pre = f.loc[m, "pts"] - f.loc[m, "pre"] - adj
        r_post = f.loc[m, "pts"] - f.loc[m, "post"] - adj
        se = float(r_pre.std(ddof=1) / math.sqrt(len(r_pre))) if len(r_pre) > 1 else float("nan")
        out[key] = {
            "title": title,
            "n": int(m.sum()),
            "weeks": sorted(int(x) for x in f.loc[m, "week"].unique()),
            "applied": float(f.loc[m, {"fade": "fade_pts"}.get(key, key)].mean()),  # this kind's own points
            "resid_pre": float(r_pre.mean()),
            "resid_post": float(r_post.mean()),
            "se": se,
            "mae_pre": float((f.loc[m, "pts"] - f.loc[m, "pre"]).abs().mean()),
            "mae_post": float((f.loc[m, "pts"] - f.loc[m, "post"]).abs().mean()),
        }
    return out


def _f(x: float, d: int = 2) -> str:
    return "-" if x is None or x != x else f"{x:+.{d}f}"


def report(
    results: dict[int, WeekResult],
    summary: dict,
    *,
    news: pd.DataFrame | None,
    lag_rows: pd.DataFrame,
    weights: plays.Weights,
    flashback: list[str] | None = None,
    before: dict | None = None,
) -> list[str]:
    lines = [
        "# The plays model (M1.6): backtest",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Weeks {', '.join(map(str, summary['weeks']))}; "
        "every week scored on weights fitted on the weeks before it only.",
        "",
        f"**Ship bar:** {SHIP_BAR}.",
        "",
    ]
    cash_ok, gpp_ok = summary["cash clears"], summary["gpp"]["clears"]
    lines += [
        "## Verdict",
        "",
        f"- Cash: **{'clears the bar' if cash_ok else 'not proven yet'}**.",
        f"- GPP: **{'clears the bar' if gpp_ok else 'not proven yet'}**.",
        "",
    ]
    m, se, n = summary["cash gap"]
    lines += [
        "## Cash",
        "",
        f"CORE + PLAY minus AVOID, realized points per $1K: {_f(m)} (SE {_f(se)}, {n} weeks).",
        "",
    ]
    lines += [
        "| Week | calls gap | CalPts value gap | TFFB gap | CORE+PLAY | AVOID |",
        "|---|---|---|---|---|---|",
    ]
    for w, r in sorted(results.items()):
        lines.append(
            f"| {w} | {_f(r.cash.get('gap'))} | {_f(r.cash.get('base_cal'))} | "
            f"{_f(r.cash.get('base_tffb'))} | {r.cash.get('n_good')} | {r.cash.get('n_bad')} |"
        )
    lines.append("")
    for base, label in (("base_cal", "CalPts value rank"), ("base_tffb", "TFFB ProjPts value rank")):
        c = summary[f"cash vs {base}"]
        lines.append(
            f"- vs {label}: calls minus baseline {_f(c['diff'])} per week (SE {_f(c['se'])}), better in "
            f"{c['won']} of {c['weeks']} weeks: {'clears' if c['clears'] else 'does not clear'}."
        )
    lines += [
        "",
        "Hit rate by label (CORE/PLAY: at or above the position's median points per $1K; AVOID: below):",
        "",
        "| Label | hit | n |",
        "|---|---|---|",
    ]
    for label in plays.CASH_LABELS:
        h, n_ = summary[f"cash hit {label}"]
        lines.append(f"| {label} | {'-' if h != h or label == 'NEUTRAL' else f'{h:.0%}'} | {n_} |")
    g = summary["gpp"]
    lines += [
        "",
        "## GPP",
        "",
        "TARGET + LEVERAGE real top-1% excess (share of the real top 1% minus real ownership) over "
        f"same-position, same-price players: {_f(g['diff'][0], 3)} (SE {_f(g['diff'][1], 3)}), combined z "
        f"{_f(g['z'])}, one-sided p {g['p']:.3f}."
        if g["weeks"]
        else "No week has a 700+ entry GPP.",
        "",
    ]
    lines += ["| Week | calls | z | CalPts value | TFFB ceiling/own |", "|---|---|---|---|---|"]
    for w, r in sorted(results.items()):
        if r.gpp:
            lines.append(
                f"| {w} | {_f(r.gpp.get('diff'), 3)} | {_f(r.gpp.get('z'))} | "
                f"{_f(r.gpp.get('base_cal'), 3)} | {_f(r.gpp.get('base_tffb'), 3)} |"
            )
    lines += [
        "",
        f"- Above the CalPts value baseline in {g.get('won vs base_cal', 0)} of {len(g['weeks'])} weeks; "
        f"above TFFB in {g.get('won vs base_tffb', 0)}.",
        "",
        "Hit rate by label (TARGET/LEVERAGE: real top-1% share above real ownership; FADE: not):",
        "",
        "| Label | hit | n |",
        "|---|---|---|",
    ]
    for label in plays.GPP_LABELS:
        h, n_ = summary[f"gpp hit {label}"]
        lines.append(f"| {label} | {'-' if h != h or label == 'NEUTRAL' else f'{h:.0%}'} | {n_} |")
    lines += [
        "",
        "## Held-out error",
        "",
        "Spearman correlation of the score with the week's standardised target, relevant players, "
        "walk-forward weights against the prior alone (mean over weeks, SE):",
        "",
    ]
    for k in ("cash rho", "cash rho prior", "cash rho value", "gpp rho", "gpp rho prior"):
        m_, se_, n_ = summary[k]
        lines.append(f"- {k}: {_f(m_, 3)} (SE {_f(se_, 3)}, {n_} weeks)")
    lines += [
        "",
        "## The weights after the last week",
        "",
        f"Fitted through Week {weights.through} (cash on weeks {weights.weeks.get('cash')}, GPP on weeks "
        f"{weights.weeks.get('gpp')}):",
        "",
        "| kind | pos | " + " | ".join(sorted({f for k in plays.FEATURES for f in plays.FEATURES[k]})) + " |",
    ]
    allf = sorted({f for k in plays.FEATURES for f in plays.FEATURES[k]})
    lines.append("|---|---|" + "---|" * len(allf))
    for kind in plays.FEATURES:
        for pos in plays.POSITIONS:
            w_ = weights.weights[kind][pos]
            lines.append(
                f"| {kind} | {pos} | " + " | ".join(_f(w_[f]) if f in w_ else "" for f in allf) + " |"
            )
    lines += ["", "## News", ""]
    if news is not None and not news.empty:
        lines += [
            "Saturday's calls against Sunday morning's (same weights):",
            "",
            "| " + " | ".join(news.columns) + " |",
            "|" + "---|" * len(news.columns),
        ]
        for _, r in news.iterrows():
            lines.append("| " + " | ".join(_f(v) if isinstance(v, float) else str(v) for v in r) + " |")
    else:
        lines.append("No week has both a Saturday and a later Sunday-morning snapshot.")
    lines += ["", "Late-news ownership (beneficiaries of news that came after ownership published):", ""]
    if lag_rows is not None and not lag_rows.empty:
        lag = plays.fit_own_lag(lag_rows)
        lines.append(
            f"n {len(lag_rows)} rows over weeks {sorted(lag_rows['week'].unique().tolist())}; factor "
            f"(shrunk) cash {lag.cash:.2f}, GPP {lag.gpp:.2f}."
        )
        shown = lag_rows[
            (lag_rows["pred"].astype(float) >= 0.02) | (lag_rows["actual"].astype(float) >= 0.02)
        ]
        lines.append(f"Owned 2%+ either way ({len(shown)} rows):")
        for _, r in shown.iterrows():
            lines.append(
                f"- Week {r['week']} {r['name']} ({r['fmt']}): shown {r['pred']:.1%}, real {r['actual']:.1%}"
            )
    else:
        lines.append("None found in these weeks: the correction stays 1.")
    lines += m16_lines(summary, before)
    if flashback:
        lines += ["", "## Flashback: cash builds from the calls (CORE locked, AVOID banned)", "", *flashback]
    return lines


def _rate(pair: object) -> str:
    if not pair or not isinstance(pair, list | tuple) or pair[0] is None or pair[0] != pair[0]:
        return "-"
    return f"{pair[0]:.0%} of {int(pair[1])}"


def m16_lines(summary: dict, before: dict | None) -> list[str]:
    """M1.6's sections: every label's hit rate before (M1's saved summary) and after, beside the baselines;
    the contradiction count; the news points against CalPts; FADE↓'s effect week by week."""
    before = before or {}
    lines = [
        "",
        "## M1.6: hit rate by label, before and after, against the baselines",
        "",
        "Before = M1's saved walk-forward run (`docs/research/plays/*-plays-summary-m1.json`); after = this "
        "run. Baselines: the same label counts and rules applied to CalPts per $1K alone and to TFFB's "
        "ProjPts per $1K (cash), the same-size top set by CalPts value and by TFFB ceiling over ownership "
        "(GPP: hit = a real top-1% share above real ownership).",
        "",
        "| Label | before (M1) | after (M1.6) | CalPts value | TFFB |",
        "|---|---|---|---|---|",
    ]
    for label in ("CORE", "PLAY", "AVOID"):
        cells = [
            before.get(f"cash hit {label}"),
            summary.get(f"cash hit {label}"),
            summary.get(f"cash base_cal hit {label}"),
            summary.get(f"cash base_tffb hit {label}"),
        ]
        lines.append(f"| cash {label} | " + " | ".join(_rate(c) for c in cells) + " |")
    for label in ("TARGET", "LEVERAGE", "FADE"):
        was, now = before.get(f"gpp hit {label}"), summary.get(f"gpp hit {label}")
        lines.append(f"| GPP {label} | {_rate(was)} | {_rate(now)} | | |")
    lines.append(
        f"| GPP TARGET+LEVERAGE set | - | {_rate(summary.get('gpp hit set'))} | "
        f"{_rate(summary.get('gpp base_cal hit set'))} | {_rate(summary.get('gpp base_tffb hit set'))} |"
    )
    bc, ac = before.get("cash gap") or [None], summary.get("cash gap") or [None]
    lines += [
        "",
        f"- Cash CORE+PLAY minus AVOID gap: before {_f(bc[0])}, after {_f(ac[0])} points per $1K.",
    ]
    for base in ("base_cal", "base_tffb"):
        b, a = before.get(f"cash vs {base}") or {}, summary.get(f"cash vs {base}") or {}
        lines.append(
            f"- vs {base}: before {_f(b.get('diff'))} (SE {_f(b.get('se'))}, won {b.get('won', '-')} of "
            f"{b.get('weeks', '-')}), after {_f(a.get('diff'))} (SE {_f(a.get('se'))}, won "
            f"{a.get('won', '-')} of {a.get('weeks', '-')}): "
            f"{'clears' if a.get('clears') else 'does not clear'} the bar."
        )
    bg, ag = before.get("gpp") or {}, summary.get("gpp") or {}
    lines.append(
        f"- GPP combined p: before {bg.get('p', float('nan')):.3f}, after {ag.get('p', float('nan')):.3f}; "
        f"{'clears' if ag.get('clears') else 'does not clear'} the bar."
    )
    bad = summary.get("contradictions") or []
    lines += [
        "",
        "## M1.6: labels against their own reasons",
        "",
        f"`plays.contradictions` over every call of every scored week: **{len(bad)}** contradiction(s)."
        + ("" if not bad else " First ten:"),
        *[f"- {b}" for b in bad[:10]],
        "",
        "## M1.6: the news against CalPts (2026, every scored week)",
        "",
        "Flagged players' mean (actual - CalPts) minus their position's unflagged relevant players', with "
        "CalPts before the news points and after (negative: they did worse than the projection); MAE both "
        "ways.",
        "",
        "| News | n | weeks | points applied | resid before | resid after | SE | MAE before | MAE after |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _key, v in (summary.get("news") or {}).items():
        if not v.get("n"):
            lines.append(f"| {v.get('title')} | 0 | | | | | | | |")
            continue
        lines.append(
            f"| {v['title']} | {v['n']} | {', '.join(map(str, v['weeks']))} | {_f(v['applied'])} | "
            f"{_f(v['resid_pre'])} | {_f(v['resid_post'])} | {_f(v['se'])} | {v['mae_pre']:.2f} | "
            f"{v['mae_post']:.2f} |"
        )
    lines += ["", "FADE↓'s measured effect as each week read it (Model Check, scored weeks before it):", ""]
    for w, v in (summary.get("fade") or {}).items():
        lines.append(
            f"- Week {w}: {v['points']:+.2f} pts over n={v['n']}"
            + (" (applied)" if v["applies"] else " (thin: not applied)")
        )
    return lines


# ---------------------------------------------------------------------------------------------------------
# Weekly tracking (`dfs week close --research`'s "calls" stage)
# ---------------------------------------------------------------------------------------------------------

HISTORY_PATH = DATA_DIR / "research" / "calls_history.csv"
HISTORY_COLUMNS = [
    "season",
    "week",
    "source",
    "at",
    "name",
    "position",
    "team",
    "salary",
    "cash",
    "cash_score",
    "gpp",
    "gpp_score",
    "cash_own",
    "gpp_own",
    "pts",
    "ppk",
    "top1_share",
    "own_actual",
    "excess",
    "cash_hit",
    "gpp_hit",
]


def saved_calls_at_lock(
    season: int, week: int, data_dir: Path | None = None
) -> tuple[str, list[dict]] | None:
    """The last version of the week's live `calls` record made before the lock: (its time, its calls)."""
    from dfs import week_inputs
    from dfs.contests import week_frames

    rec = week_inputs.load_input(plays.INPUT_NAME, season, week, data_dir)
    if not rec:
        return None
    lock = week_frames.lock_utc(season, week)
    before = [v for v in rec.get("versions") or [] if datetime.fromisoformat(v["at"]) <= lock]
    return (before[-1]["at"], before[-1]["calls"]) if before else None


def outcome_rows(
    season: int, week: int, source: str, at: str, calls: Sequence[Mapping], hw: HistWeek
) -> pd.DataFrame:
    """One row per call with what happened: points, points per $1K, the real top-1% share and ownership, and
    whether the call hit (CORE/PLAY: at or above the position's median points per $1K among the calls;
    AVOID: below; TARGET/LEVERAGE: a real top-1% share above his real ownership; FADE: not)."""
    shares = top1_shares_by_name(hw)
    rows = []
    for c in calls:
        pts = hw.actual.get(c["name"])
        sal = float(c["salary"])
        share, own = shares.get(normalize_name(c["name"]), (float("nan"), float("nan")))
        rows.append(
            {
                "season": season,
                "week": week,
                "source": source,
                "at": at,
                "name": c["name"],
                "position": c["position"],
                "team": c["team"],
                "salary": sal,
                "cash": c["cash"],
                "cash_score": c["cash_score"],
                "gpp": c["gpp"],
                "gpp_score": c["gpp_score"],
                "cash_own": c.get("cash_own"),
                "gpp_own": c.get("gpp_own"),
                "pts": pts,
                "ppk": None if pts is None else pts / (sal / 1000),
                "top1_share": share,
                "own_actual": own,
                "excess": share - own if pts is not None else float("nan"),
            }
        )
    frame = pd.DataFrame(rows, columns=HISTORY_COLUMNS[:-2])
    med = frame.groupby("position")["ppk"].transform("median")
    above = frame["ppk"] >= med
    frame["cash_hit"] = np.where(
        frame["ppk"].isna() | frame["cash"].eq("NEUTRAL"),
        np.nan,
        np.where(frame["cash"].isin(["CORE", "PLAY"]), above, ~above).astype(float),
    )
    has = frame["excess"].notna()
    frame["gpp_hit"] = np.where(
        ~has | frame["gpp"].eq("NEUTRAL"),
        np.nan,
        np.where(frame["gpp"].isin(["TARGET", "LEVERAGE"]), frame["excess"] > 0, frame["excess"] <= 0).astype(
            float
        ),
    )
    return frame


def top1_shares_by_name(hw: HistWeek) -> dict[str, tuple[float, float]]:
    if hw.gpp_detail is None:
        return {}
    names = hw.features.frame["Name"].map(normalize_name).to_numpy()
    d = hw.gpp_detail.reset_index(drop=True)
    return {n: (float(a), float(b)) for n, a, b in zip(names, d["top1_share"], d["own_actual"], strict=True)}


def append_history(frame: pd.DataFrame, path: Path | None = None) -> Path:
    """Add the week's rows to `calls_history.csv`, replacing any rows of the same season, week and source."""
    path = path or HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path)
        keys = set(zip(frame["season"], frame["week"], frame["source"], strict=True))
        keep = [k not in keys for k in zip(old["season"], old["week"], old["source"], strict=True)]
        frame = pd.concat([old[keep], frame], ignore_index=True)
    frame.sort_values(["season", "week", "source", "position", "name"]).to_csv(path, index=False)
    return path


def label_lines(frame: pd.DataFrame) -> list[str]:
    """ "CORE 4 of 7, PLAY ...": each label's hits."""
    out = []
    for kind, labels in (("cash", ("CORE", "PLAY", "AVOID")), ("gpp", ("TARGET", "LEVERAGE", "FADE"))):
        parts = []
        for label in labels:
            hits = frame.loc[frame[kind].eq(label), f"{kind}_hit"].dropna()
            if len(hits):
                parts.append(f"{label} {int(hits.sum())} of {len(hits)}")
        if parts:
            out.append(f"{kind}: " + ", ".join(parts))
    return out


def close_week(season: int, week: int, log: Callable[[str], None] = print) -> dict:
    """Score the week's calls (the last live version before the lock, else the calls rebuilt from the
    pre-lock snapshot), append them to `calls_history.csv`, refit the weights through the week and save them.
    Returns what the close report needs."""
    results, history, fitted = run((week,), season=season, log=log)
    if week not in history:
        raise FileNotFoundError(f"Week {week}: no pre-lock snapshot or no scored results")
    hw = history[week]
    saved = saved_calls_at_lock(season, week)
    if saved:
        at, calls = saved
        source = "live"
    else:
        r = results[week]
        at = hw.now.isoformat(timespec="seconds")
        calls = [plays.call_dict(c) for c in r.calls.calls]
        source = "rebuilt"
    frame = outcome_rows(season, week, source, at, calls, hw)
    path = append_history(frame)
    before = fitted[-2] if len(fitted) > 1 else plays.Weights.prior(season)
    shifts = plays.biggest_shifts(fitted[-1], before)
    held = results[week].held_out if week in results else {}
    return {
        "source": source,
        "at": at,
        "rows": len(frame),
        "labels": label_lines(frame),
        "shifts": shifts,
        "held_out": held,
        "history": str(path),
        "weights": str(plays.weights_path(season, week)),
        "cash_gap": results[week].cash.get("gap") if week in results else None,
        "gpp_diff": results[week].gpp.get("diff") if week in results else None,
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the research entry point
    parser = argparse.ArgumentParser(prog="dfs.research.plays_backtest")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--weeks", default=",".join(map(str, EVAL_WEEKS)))
    r.add_argument("--fresh", action="store_true")
    r.add_argument("--no-news", action="store_true")
    r.add_argument("--before", default="", help="M1's saved summary JSON to report against")
    args = parser.parse_args(argv)
    weeks = [int(x) for x in args.weeks.split(",") if x.strip()]
    results, history, fitted = run(weeks, fresh=args.fresh)
    summary = summarize(results)
    ctx = None
    news = None
    if not args.no_news:
        ctx = Context.load(SEASON, max(weeks))
        news = news_path(ctx, weeks, {r.week: r.weights for r in results.values()})
    lag_rows = pd.concat([late_news_rows(hw) for hw in history.values()], ignore_index=True)
    before_path = (
        Path(args.before)
        if args.before
        else REPORT_DIR / f"{SEASON}-wk{max(weeks):02d}-plays-summary-m1.json"
    )
    before = json.loads(before_path.read_text()) if before_path.exists() else None
    lines = report(results, summary, news=news, lag_rows=lag_rows, weights=fitted[-1], before=before)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{SEASON}-wk{max(weeks):02d}-plays-backtest.md"
    path.write_text("\n".join(lines) + "\n")
    (REPORT_DIR / f"{SEASON}-wk{max(weeks):02d}-plays-summary.json").write_text(
        json.dumps(summary, default=float, indent=1)
    )
    print("\n".join(lines))
    print(f"\nreport: {path}")
    if summary["contradictions"]:
        print(f"{len(summary['contradictions'])} label(s) contradict their reasons: see the report")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - through the imported module, so saved weeks unpickle anywhere
    from dfs.research import plays_backtest as _module

    raise SystemExit(_module.main())
