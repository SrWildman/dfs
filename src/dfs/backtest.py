"""`dfs backtest` (session J's Flashback): would these build settings have made money?

Rebuild past slates with today's code and score the builds in the contests Sam actually entered, against those
contests' real fields and payouts. The proof harness for any change to how lineups are built: a settings file
(`backtest_settings`) names the change, `--compare` pits two settings file against each other on the same
contests.

**Each week as it stood at the lock** (`research.curse.week_inputs`): the last EdgeRaw snapshot before Sunday
1 pm ET, CalPts on every rated row, cut to the main slate; nothing from the games is used to build. The
builder is today's `lineup_suggest.suggest_builds` with the specs and lines `dfs lineups suggest` would use,
run on **Sam's pool** where the week has one saved (`pool_store`: written at every sync and every `suggest`
from Week 7 on), else the constructed pool of `sim/retro.pool_tags` (his Week 6 counts, top CalPts per
position). The report says which, per week. Weeks 2, 4, 5 and 6 predate the saved pool, so those are
constructed.

**Entering the builds.** For each contest Sam entered that week with a standings file (the history log's
series, fee and size; the standings' own `srwildman` rows say how many entries he made), the best `k` builds
of that contest's kind go in, ranked as the tool ranks them (Cash %, or Top 1%): a double-up gets the best
cash build, a 5-entry-max GPP the best five GPP builds. A settings flag can choose differently
(`Flag.assign` per contest, `Flag.choose` for the week's contests as one set). The report also scores
**every** build of the kind in every contest ("any build"), which is the same question with far less luck
in it: a single-entry double-up is one coin flip a week.

**Scored against the real field.** A lineup's points are DraftKings' own (the standings' `FPTS`); its place is
among the contest's real final scores (the builds are an extra entry, as in the contest retro) and it shares
the payout of its real copies in the field (`contest_retro.realized_payout`'s rule), paid from the contest's
fitted payout table (`sim/payouts.py`, the min-cash tier fitted on Sam's own results). Cashed = paid anything;
Top 1% = at or above the contest's real top-1% score.

**Beside Sam.** His real lineups in the same contests, twice: with the winnings DraftKings actually paid him
(the history log) and re-paid on the same fitted table as the builds, so the comparison is like for like
(the gap between the two is the table's error, printed as a check).

**How much to believe it.** Contests of one week share most of their players and one slate's luck, so the
contests are not independent: the standard errors here are across WEEKS (and that is 3 to 4 numbers). Anything
short of a clear, same-direction win in most weeks is "not proven".

Local and read-only: Sam's files under `data/`, never a sheet; the report goes to `docs/research/backtest/`
(gitignored) and the saved builds to `data/research/backtest/` so a rerun of the same settings on the same
code is free (`--fresh` ignores them).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import backtest_settings as bs
from dfs import lineup_suggest as ls
from dfs import pool_store, suggest_state
from dfs.contests import history, week_frames
from dfs.paths import REPO_ROOT
from dfs.player_join import normalize_name
from dfs.research import curse
from dfs.sim import contest_retro, payouts, retro

SEASON = retro.SEASON
OUT_DIR = Path("data/research/backtest")
REPORT_DIR = REPO_ROOT / "docs" / "research" / "backtest"
CACHE_VERSION = "1"  # bump when the stored builds' meaning changes
DEFAULT_WEEKS = (2, 4, 5, 6)
ME = contest_retro.ME
MIN_POOL_PLAYERS = 25  # a saved pool smaller than this is not a pool anyone builds twenty lineups from
N_SIMS = curse.N_SIMS
WORKERS = 6

# ---------------------------------------------------------------------------------------------------------
# The builds of one week under one set of settings
# ---------------------------------------------------------------------------------------------------------


def code_stamp() -> str:
    """The repository state the builds came from: HEAD, plus a hash of any uncommitted change under src/, so a
    cached build is reused only for the code that made it ("nogit" when git cannot say: then nothing is
    reused)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=30
        ).stdout

    try:
        head = git("rev-parse", "--short", "HEAD").strip()
        dirty = git("diff", "HEAD", "--", "src").strip() + git(
            "ls-files", "--others", "--exclude-standard", "src"
        )
    except (OSError, subprocess.SubprocessError):
        return "nogit"
    return head + (f"+{hashlib.sha256(dirty.encode()).hexdigest()[:8]}" if dirty else "")


def pool_for_week(wi: curse.WeekInputs, settings: bs.Settings) -> tuple[dict[str, str], str]:
    """`(tags, source)`: Sam's saved pool at the lock when the settings allow it and it holds enough
    main-slate players, else the constructed pool (`wi.tags`)."""
    if settings.pool == "auto":
        snap = pool_store.pool_at(SEASON, wi.week, week_frames.lock_utc(SEASON, wi.week))
        if snap is not None:
            names = set(wi.edge["Name"].astype(str))
            tags = {n: t for n, t in snap.pool.items() if n in names}
            if len(tags) >= MIN_POOL_PLAYERS:
                return tags, f"saved {snap.at:%m-%d %H:%M} UTC ({snap.source}, {len(tags)} players)"
    return dict(wi.tags), f"constructed ({len(wi.tags)} players)"


def apply_rules(
    wi: curse.WeekInputs, settings: bs.Settings, tags: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    """The rules lines the backtest applies: Adjust / Ownership (your frame: the projections and ownership the
    builder reads) and Never use (out of the pool). Returns the edge, the tags and the notes."""
    notes: list[str] = []
    if not any(v.strip() for v in settings.rules.values()):
        return wi.edge, tags, notes
    text = suggest_state.RulesText.from_labels(dict(settings.rules))
    frame, frame_notes = suggest_state.frame_from_rules(text, wi.edge, bs_metric())
    notes += frame_notes
    if frame:
        notes.append(suggest_state.frame_note(frame))
    names = list(dict.fromkeys(str(n) for n in wi.edge["Name"]))
    rules = suggest_state.parse_rules(text, names, set(wi.edge["Team"].dropna().astype(str)))
    tags = {n: t for n, t in tags.items() if n not in rules.never}
    notes += [p for p in rules.problems if p.split(":", 1)[0] == "Never use"]
    return frame.edge, tags, notes


def bs_metric() -> str:
    return curse.METRIC


def make_context(wi: curse.WeekInputs, settings: bs.Settings) -> bs.BuildContext:
    tags, source = pool_for_week(wi, settings)
    edge, tags, notes = apply_rules(wi, settings, tags)
    return bs.BuildContext(
        week=wi.week,
        edge=edge,
        tags=tags,
        now=wi.now,
        cap=curse.retro_cap(),
        metric=bs_metric(),
        settings=settings,
        typed_cash=wi.typed_cash,
        n_sims=N_SIMS,
        notes=[f"pool: {source}", *notes],
    )


def ensure_sim(ctx: bs.BuildContext) -> bs.BuildContext:
    """The specs and lines from `ctx.edge` where a hook left them out."""
    if ctx.specs is None or ctx.lines is None:
        specs, lines = curse.sim_lines(ctx.edge, ctx.typed_cash)
        ctx.specs = ctx.specs if ctx.specs is not None else specs
        ctx.lines = ctx.lines if ctx.lines is not None else lines
    return ctx


def default_builder(ctx: bs.BuildContext) -> list[ls.Build]:
    """Today's `dfs lineups suggest` on `ctx` (a fresh run: cash, then every GPP shape)."""
    ctx = ensure_sim(ctx)
    s = ctx.settings
    report = ls.suggest_builds(
        ctx.edge,
        ctx.tags,
        ctx.specs,
        now=ctx.now,
        cap=ctx.cap,
        cash_line=ctx.lines.cash_line,
        gpp_target=ctx.lines.gpp_target,
        metric=ctx.metric,
        cash_builds=s.cash,
        gpp_builds=s.gpp,
        n_sims=ctx.n_sims,
        time_limit=s.seconds,
        field=ctx.lines.field,
        rank=s.rank,
    )
    ctx.notes += report.notes
    return report.builds


def run_builder(ctx: bs.BuildContext) -> list[ls.Build]:
    """The settings' flags around the default builder: `prepare` hooks in order, then the `build` hooks nested
    (the first registered is the outermost)."""
    return build_prepared(prepare_context(ctx))


def prepare_context(ctx: bs.BuildContext) -> bs.BuildContext:
    """The `prepare` hooks in order, then the specs and lines any of them left out."""
    for f in ctx.settings.active_flags():
        if f.prepare is not None:
            ctx = f.prepare(ctx, ctx.settings.flags[f.name])
    return ensure_sim(ctx)


def build_prepared(ctx: bs.BuildContext) -> list[ls.Build]:
    """The `build` hooks nested around the default builder on a prepared context."""
    flags = ctx.settings.active_flags()
    fn: Callable[[bs.BuildContext], list[ls.Build]] = default_builder
    for f in reversed(flags):
        if f.build is not None:
            fn = _wrap(f, ctx.settings.flags[f.name], fn)
    return fn(ctx)


def _wrap(flag: bs.Flag, value, inner):  # noqa: ANN001, ANN202
    return lambda c: flag.build(c, value, inner)


def week_contests(week: int) -> list[bs.WeekContest]:
    """The contests Sam entered in `week` as a `choose` hook may see them (no standings)."""
    return [
        bs.WeekContest(e.c.contest_id, e.c.series, e.fmt, e.kind, e.k)
        for e in load_entered([week])
        if e.c.week == week
    ]


def run_choose(
    ctx: bs.BuildContext,
    builds: list[ls.Build],
    contests: Callable[[], list[bs.WeekContest]],
) -> dict[str, list[list[str]]]:
    """The `choose` hooks in order (a later one overrides an earlier one's contests); `contests` is called
    only when a hook is on."""
    hooks = [f for f in ctx.settings.active_flags() if f.choose is not None]
    if not hooks:
        return {}
    week = contests()
    out: dict[str, list[list[str]]] = {}
    for f in hooks:
        picked = f.choose(ctx, builds, week, ctx.settings.flags[f.name])
        out.update({str(cid): [list(map(str, names)) for names in v] for cid, v in picked.items()})
    return out


def build_record(b: ls.Build) -> dict:
    return {
        "kind": b.kind,
        "number": b.number,
        "names": [p.name for p in b.players],
        "stack": b.stack,
        "proj": round(float(b.projection), 3),
        "p_cash": float(b.p_cash),
        "p_gpp": float(b.p_gpp),
        "own_sum": None if b.own_sum is None else float(b.own_sum),
        "e_dups": None if b.e_dups is None else float(b.e_dups),
    }


def job_path(settings: bs.Settings, code: str, week: int, root: Path = OUT_DIR) -> Path:
    return root / "jobs" / settings.fingerprint(CACHE_VERSION + code) / f"w{week:02d}.json"


def run_week(
    wi: curse.WeekInputs, settings: bs.Settings, code: str, *, fresh: bool = False, root: Path = OUT_DIR
) -> dict:
    """The week's builds under `settings` as a JSON-able dict, from the saved run when there is one."""
    path = job_path(settings, code, wi.week, root)
    if path.exists() and not fresh and code != "nogit":
        return json.loads(path.read_text())
    bs.check_flags(settings)
    started = time.monotonic()
    ctx = prepare_context(make_context(wi, settings))
    builds = build_prepared(ctx)
    chosen = run_choose(ctx, builds, lambda: week_contests(wi.week))
    out = {
        "week": wi.week,
        "settings": settings.name,
        "code": code,
        "seconds": round(time.monotonic() - started, 1),
        "notes": ctx.notes,
        "pool": next((n[6:] for n in ctx.notes if n.startswith("pool: ")), ""),
        "builds": [build_record(b) for b in builds],
    }
    if chosen:
        out["chosen"] = chosen  # contest id -> the lineups a `choose` hook entered there
    if code != "nogit":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


def _worker(args: tuple) -> dict:
    wi, settings, code, fresh = args
    return run_week(wi, settings, code, fresh=fresh)


def run_weeks(
    inputs: dict[int, curse.WeekInputs],
    settings: Sequence[bs.Settings],
    *,
    fresh: bool = False,
    workers: int = WORKERS,
    log: Callable[[str], None] = print,
) -> dict[str, dict[int, dict]]:
    """`{settings name: {week: run}}` for every settings x week, the ones not saved yet in parallel."""
    code = code_stamp()
    jobs = [(wi, s, code, fresh) for s in settings for wi in inputs.values()]
    todo = [j for j in jobs if fresh or code == "nogit" or not job_path(j[1], code, j[0].week).exists()]
    log(f"{len(jobs)} week-builds, {len(jobs) - len(todo)} saved, {len(todo)} to run (code {code})")
    if len(todo) > 1 and workers > 1:
        import multiprocessing as mp

        with mp.get_context("spawn").Pool(min(workers, len(todo))) as pool:
            for r in pool.imap_unordered(_worker, todo):
                log(f"  week {r['week']} done in {r['seconds']:.0f} s")
    else:
        for j in todo:
            r = _worker(j)
            log(f"  week {r['week']} done in {r['seconds']:.0f} s")
    out: dict[str, dict[int, dict]] = {}
    for _, s, _, _ in jobs:
        out.setdefault(s.name, {})
    for wi, s, _, _ in jobs:
        path = job_path(s, code, wi.week)
        out[s.name][wi.week] = (
            json.loads(path.read_text())
            if path.exists() and code != "nogit"
            else _worker((wi, s, code, False))
        )
    return out


# ---------------------------------------------------------------------------------------------------------
# The real contests Sam entered
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Entered:
    """One real contest Sam entered, with what scoring a lineup in it needs."""

    c: contest_retro.RealContest
    kind: payouts.ContestKind
    mine: list[tuple[tuple[str, ...], float]]  # his lineups: (sorted normalised names, points)
    actual: list[tuple[float, float]]  # his entries as the history log has them: (points, winnings)
    fee: float
    top1_line: float
    cum: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(1))
    copies: Counter = field(repr=False, default_factory=Counter)
    sorted_pts: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))

    @property
    def k(self) -> int:
        return len(self.mine)

    @property
    def fmt(self) -> str:
        return self.c.fmt

    def pay(self, names: Sequence[str], points: float, *, own: bool = False) -> float:
        """What a lineup of `names` (normalised) scoring `points` is paid here: its place among the real final
        scores and the payout shared with its real copies; `own` for one of Sam's real lineups (one of those
        copies is itself)."""
        from dfs.sim import contest_sim as csim

        copies = self.copies.get(tuple(sorted(names)), 0)
        if own:
            copies = max(copies - 1, 0)
        place = 1 + int(len(self.sorted_pts) - np.searchsorted(self.sorted_pts, points + 1e-9, side="right"))
        return float(csim.block_payout(self.cum, np.array([place], float), np.array([copies], float))[0])


def kind_for(c: contest_retro.RealContest, min_cash: payouts.MinCash) -> payouts.ContestKind:
    k = c.kind
    cash = None if k.format not in payouts.GPP_FORMATS else min_cash.lookup(c.series, c.fee, c.format)
    return payouts.ContestKind(
        k.series, k.format, k.entries, k.places_paid, k.prize_pool, k.fee, cash, k.max_entries
    )


def load_entered(weeks: Sequence[int]) -> list[Entered]:
    """Every standings contest of `weeks` that Sam entered, with his lineups and his real winnings."""
    from dfs.sim import contest_sim as csim

    log = history.typed(history.read_log("data/contests/contest_log.csv"))
    log = log[(log["season"] == SEASON) & (log["game_type"] == "Classic")]
    min_cash = payouts.fit_min_cash(
        payouts.paid_entries(history.typed(history.read_log("data/contests/contest_log.csv")))
    )
    out = []
    for c in contest_retro.load_real_contests(set(weeks)):
        rows = c.standings.lineups
        mine_rows = rows[rows["entry_name"].astype(str).str.casefold() == ME]
        if mine_rows.empty:
            continue
        mine = [
            (tuple(sorted(normalize_name(n) for _, n in slots)), float(p))
            for slots, p in zip(mine_rows["slots"], mine_rows["points"], strict=True)
        ]
        mine_log = log[log["contest_key"].astype(str) == c.contest_id]
        kind = kind_for(c, min_cash)
        pts = np.sort(c.points)  # ascending
        top1 = float(c.points[max(1, round(retro.TOP_FRACTION * len(c.points))) - 1])
        out.append(
            Entered(
                c=c,
                kind=kind,
                mine=mine,
                actual=[
                    (float(p), float(w))
                    for p, w in zip(mine_log["points"], mine_log["winnings"], strict=True)
                ],
                fee=float(c.fee),
                top1_line=top1,
                cum=csim._cumulative(payouts.payout_table(kind)),  # noqa: SLF001
                copies=c.copies(),
                sorted_pts=pts,
            )
        )
    return sorted(out, key=lambda e: (e.c.week, e.c.fmt, e.c.contest_id))


# ---------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------

ROW_COLUMNS = [
    "side",
    "week",
    "contest",
    "series",
    "fmt",
    "fee",
    "entries",
    "slot",
    "scope",
    "points",
    "paid",
    "cashed",
    "top1",
]


def entry_order(builds: list[dict], kind: str, rank: str) -> list[dict]:
    """The builds of `kind` best first by the tool's own ranking (Cash % for cash; for GPP the Top 1%
    chance, or with `rank = "share"` that chance over 1 + the duplicates expected): the order Sam would fill
    contests in. The builder's own numbering is alphabetical by quarterback, which is no ranking."""

    def key(b: dict):
        p = b["p_cash"] if kind == "cash" else b["p_gpp"]
        if kind == "gpp" and rank == "share" and b.get("e_dups") is not None:
            p = p / (1.0 + b["e_dups"])
        return (-round(p, ls.PROB_DECIMALS), -b["proj"], b["number"])

    return sorted((b for b in builds if b["kind"] == kind), key=key)


def default_assign(builds: list[dict], e: Entered, rank: str = ls.DEFAULT_RANK) -> list[dict]:
    """The best `k` builds of the contest's kind (`entry_order`)."""
    return entry_order(builds, e.fmt, rank)[: e.k]


def week_points(entered: list[Entered], week: int) -> dict[str, float]:
    return contest_retro.week_points([e.c for e in entered if e.c.week == week], week)


def lineup_points(names: Sequence[str], pts: dict[str, float], fallback: dict[str, float]) -> float:
    """The lineup's DK points: the standings' own player scores, else the nflverse fallback; NaN if any is
    unknown."""
    total = 0.0
    for n in names:
        key = normalize_name(n)
        v = pts.get(key, fallback.get(n, float("nan")))
        total += v
    return total


def score_week(
    week: int,
    run: dict,
    entered: list[Entered],
    wi: curse.WeekInputs,
    settings: bs.Settings,
) -> tuple[pd.DataFrame, list[str]]:
    """Every backtest row of one week: `entry` rows (the builds that would have gone in) and `any` rows (every
    build of the kind, every contest). Returns the frame and the lineups dropped for an unknown player."""
    pts = week_points(entered, week)
    builds = run["builds"]
    assign = [f.assign for f in settings.active_flags() if f.assign is not None]
    chosen = run.get("chosen", {})
    rows, dropped = [], []
    cache: dict[tuple, float] = {}
    for e in (x for x in entered if x.c.week == week):
        for scope in ("entry", "any"):
            picked = (
                default_assign(builds, e, settings.rank)
                if scope == "entry"
                else entry_order(builds, e.fmt, settings.rank)
            )
            if scope == "entry" and e.c.contest_id in chosen:
                picked = [{"names": names} for names in chosen[e.c.contest_id]]
            elif scope == "entry" and assign:
                objs = [_AsBuild(b) for b in builds]
                for hook in assign:
                    objs = hook(objs, e, e.k, settings.flags)
                picked = [o.record for o in objs]
            for slot, b in enumerate(picked, start=1):
                names = tuple(sorted(normalize_name(n) for n in b["names"]))
                p = cache.get(names)
                if p is None:
                    p = cache[names] = lineup_points(b["names"], pts, wi.actual)
                if np.isnan(p):
                    dropped.append(f"w{week} {' | '.join(b['names'])}")
                    continue
                rows.append(_row("backtest", e, slot, scope, p, e.pay(names, p)))
    return pd.DataFrame(rows, columns=ROW_COLUMNS), sorted(set(dropped))


@dataclass
class _AsBuild:
    """A saved build handed to an `assign` hook: `.record` is the dict, the other attributes read through."""

    record: dict

    def __getattr__(self, item: str):
        try:
            return self.record[item]
        except KeyError as e:
            raise AttributeError(item) from e


def _row(side: str, e: Entered, slot: int, scope: str, points: float, paid: float) -> dict:
    return {
        "side": side,
        "week": e.c.week,
        "contest": e.c.contest_id,
        "series": e.c.series,
        "fmt": e.fmt,
        "fee": e.fee,
        "entries": e.c.entries,
        "slot": slot,
        "scope": scope,
        "points": points,
        "paid": paid,
        "cashed": float(paid > 1e-9),
        "top1": float(points >= e.top1_line - 1e-9),
    }


def sam_rows(entered: list[Entered]) -> pd.DataFrame:
    """Sam's real lineups: `sam` (re-paid on the fitted table, like the builds) and `sam_actual` (what
    DraftKings actually paid each entry: the history log's own rows)."""
    rows = []
    for e in entered:
        for slot, (names, p) in enumerate(e.mine, start=1):
            rows.append(_row("sam", e, slot, "entry", p, e.pay(names, p, own=True)))
        for slot, (p, won) in enumerate(e.actual, start=1):
            rows.append(_row("sam_actual", e, slot, "entry", p, won))
    return pd.DataFrame(rows, columns=ROW_COLUMNS)


# ---------------------------------------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------------------------------------


def contest_table(rows: pd.DataFrame) -> pd.DataFrame:
    """One line per side, scope, contest: dollars in and out, entries, cashes and top-1% hits."""
    g = rows.groupby(["side", "scope", "week", "contest", "series", "fmt"], sort=True)
    t = g.agg(
        entries=("paid", "size"),
        fee=("fee", "first"),
        paid=("paid", "sum"),
        cashed=("cashed", "sum"),
        top1=("top1", "sum"),
        points=("points", "mean"),
    ).reset_index()
    t["cost"] = t["entries"] * t["fee"]
    t["roi"] = t["paid"] / t["cost"] - 1.0
    return t


def pooled(t: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Dollars-weighted ROI, cash rate and top-1% rate over the contests of each `by` group."""
    g = t.groupby(by, sort=True)
    out = g.agg(
        contests=("contest", "nunique"),
        entries=("entries", "sum"),
        cost=("cost", "sum"),
        paid=("paid", "sum"),
        cashed=("cashed", "sum"),
        top1=("top1", "sum"),
    ).reset_index()
    out["roi"] = out["paid"] / out["cost"] - 1.0
    out["cash_rate"] = out["cashed"] / out["entries"]
    out["top1_rate"] = out["top1"] / out["entries"]
    return out


def mean_se(x: Sequence[float]) -> tuple[float, float]:
    v = np.asarray([a for a in x if a == a], dtype=float)
    if len(v) == 0:
        return float("nan"), float("nan")
    return float(v.mean()), float(v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else float("nan")


def week_roi_table(t: pd.DataFrame, side: str, scope: str, fmt: str | None = None) -> pd.Series:
    """ROI by week for one side and scope (all formats, or one)."""
    d = t[(t["side"] == side) & (t["scope"] == scope)]
    if fmt is not None:
        d = d[d["fmt"] == fmt]
    g = d.groupby("week")
    return (g["paid"].sum() / g["cost"].sum() - 1.0).rename(side)


def paired_vs(t: pd.DataFrame, a: str, b: str, scope: str = "entry", fmt: str | None = None) -> dict:
    """Side `a` minus side `b` by contest (same contests, ROI points) and by week (dollars-weighted ROI):
    mean difference, its standard error over contests and over weeks, and the weeks `a` won."""
    d = t[t["scope"] == scope]
    if fmt is not None:
        d = d[d["fmt"] == fmt]
    pa = d[d["side"] == a].set_index("contest")["roi"]
    pb = d[d["side"] == b].set_index("contest")["roi"]
    diff = (pa - pb).dropna()
    wa, wb = week_roi_table(t, a, scope, fmt), week_roi_table(t, b, scope, fmt)
    wd = (wa - wb).dropna()
    m_c, se_c = mean_se(diff)
    m_w, se_w = mean_se(wd)
    return {
        "contests": len(diff),
        "diff_contest": m_c,
        "se_contest": se_c,
        "weeks": len(wd),
        "diff_week": m_w,
        "se_week": se_w,
        "weeks_won": int((wd > 0).sum()),
    }


@dataclass
class Result:
    """What one backtest produced, kept whole so a report (or a test) can read any slice."""

    weeks: list[int]
    settings: bs.Settings
    runs: dict[int, dict]
    rows: pd.DataFrame
    contests: pd.DataFrame
    dropped: list[str]
    coverage: dict[str, float]

    @property
    def name(self) -> str:
        return self.settings.name


def rename_side(rows: pd.DataFrame, name: str) -> pd.DataFrame:
    return rows.assign(side=rows["side"].where(rows["side"] != "backtest", name))


def sam_coverage(weeks: Sequence[int], entered: list[Entered]) -> dict[str, float]:
    """How much of Sam's real Classic spend in `weeks` the scored contests cover (the rest has no standings
    file)."""
    log = history.typed(history.read_log("data/contests/contest_log.csv"))
    log = log[
        (log["season"] == SEASON)
        & log["week"].isin(list(weeks))
        & (log["game_type"] == "Classic")
        & (log["fee"] > 0)
    ]
    log = log[(log["slate"] == "main") & ~log["format"].isin(["H2H"])]
    total = float(log["fee"].sum())
    seen = {e.c.contest_id for e in entered}
    covered = float(log[log["contest_key"].astype(str).isin(seen)]["fee"].sum())
    return {"fees": total, "covered": covered, "share": covered / total if total else float("nan")}


def backtest(
    weeks: Sequence[int] = DEFAULT_WEEKS,
    settings: Sequence[bs.Settings] | None = None,
    *,
    fresh: bool = False,
    workers: int = WORKERS,
    log: Callable[[str], None] = print,
    study: curse.Study | None = None,
) -> dict[str, Result]:
    """Backtest each settings (default: today's) on `weeks`. Returns `{settings name: Result}`."""
    settings = list(settings or [bs.Settings()])
    names = [s.name for s in settings]
    if len(set(names)) != len(names):
        raise ValueError(f"settings names must differ, got {names}")
    t0 = time.monotonic()
    entered = load_entered(weeks)
    weeks = [w for w in weeks if any(e.c.week == w for e in entered)]
    if not weeks:
        raise ValueError("none of those weeks has a standings file of a contest Sam entered")
    study = study or curse.Study(weeks)
    inputs = study.weeks
    log(f"inputs for weeks {', '.join(map(str, weeks))} ready ({time.monotonic() - t0:.0f} s)")
    runs = run_weeks({w: inputs[w] for w in weeks}, settings, fresh=fresh, workers=workers, log=log)
    sam = sam_rows(entered)
    coverage = sam_coverage(weeks, entered)
    out = {}
    for s in settings:
        frames, dropped = [], []
        for w in weeks:
            f, d = score_week(w, runs[s.name][w], entered, inputs[w], s)
            frames.append(f)
            dropped += d
        rows = pd.concat([*frames, sam], ignore_index=True)
        out[s.name] = Result(
            weeks=list(weeks),
            settings=s,
            runs=runs[s.name],
            rows=rows,
            contests=contest_table(rows),
            dropped=dropped,
            coverage=coverage,
        )
    log(f"scored in {time.monotonic() - t0:.0f} s")
    return out


# ---------------------------------------------------------------------------------------------------------
# Weekly hook (`dfs research refresh --only backtest`)
# ---------------------------------------------------------------------------------------------------------


def refresh_summary(season: int, through: int) -> dict:
    """The contract of `research.refresh`: today's defaults on every week with standings up to `through`."""
    from dfs import backtest_report

    if season != SEASON:
        return {
            "headline": [f"The backtest is wired for {SEASON} only."],
            "metrics": {},
            "ship_bar": backtest_report.SHIP_BAR,
        }
    lines = curse.lines_through(season, through)
    weeks = [w for w in DEFAULT_WEEKS if w in lines and w <= through]
    weeks += [w for w in sorted(lines) if w > max(DEFAULT_WEEKS) and w <= through]
    res = backtest(weeks, [bs.Settings()], log=lambda _m: None)
    return backtest_report.refresh_from(res["today"])
