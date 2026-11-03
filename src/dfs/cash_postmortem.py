"""Plan D item 8: the cash post-mortem `dfs week close` prints (and the close report quotes), and the cash
chalk check `dfs lineups suggest` prints.

Sam, Nov 3: "most lineups that cashed had combos and players that were not suggested in any of our builds
... the info was out there, we just missed them." Planning's hand check on Weeks 4-8 said otherwise for
most players: the players who separated cashers from non-cashers were mostly in our cash builds; the
misses were double-up chalk no cash build used. This makes that check weekly and keeps a count over weeks:

- the 10 players with the biggest gap between how often the week's double-up cashers and non-cashers
  rostered them (only players in at least `MIN_FIELD` of the field), each tagged: in N of our cash builds
  / in the pool only (Cash or GPP) / missed entirely, with our pre-lock CalPts and Value position ranks
  and predicted CashOwn against actual double-up ownership;
- every distinct cash build of the week scored against the cash line ("5 of 10 would have cashed");
- the entered cash lineup against the nearest build: who was swapped in and out and what that cost;
- one row per tagged player appended to `data/contests/cash_postmortem.csv` (a re-run replaces the week).

Display only: it changes no build and no sheet. The core (`postmortem`) is pure; `load_week` reads the
files."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dfs.paths import CONTESTS_DIR
from dfs.player_join import normalize_name

MIN_FIELD = 0.08  # a player in fewer of the double-up lineups than this is not listed
TOP_N = 10
CHALK_CASH_OWN = 0.30  # the chalk check lists every player predicted at this or more in double-ups
POSTMORTEM_CSV = CONTESTS_DIR / "cash_postmortem.csv"
ME = "srwildman"
DOUBLE_UP = "Double Up"

IN_BUILDS = "in cash builds"
POOL_ONLY = "pool only"
MISSED = "missed"


def key(name: object) -> str:
    return normalize_name(str(name))


# --- the inputs, as plain data -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DuContest:
    """One double-up's standings: every lineup (a set of player keys, its points, the entrant) and the cash
    line."""

    contest_id: str
    cash_line: float
    lineups: list[tuple[frozenset[str], float, str]]  # (players, points, entry name)
    fpts: dict[str, float]  # player key -> actual DK points


@dataclass(frozen=True)
class CashBuild:
    names: tuple[str, ...]
    label: str  # "build 3 (round 2)"
    shown_cash: float | None = None  # the Cash % the tab showed

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(key(n) for n in self.names)


@dataclass(frozen=True)
class WeekInputs:
    season: int
    week: int
    contests: list[DuContest]
    builds: list[CashBuild] | None  # None: no suggest history for the week
    pool: dict[str, str] | None  # player key -> "Cash" / "GPP"; None: no saved pool
    edge: pd.DataFrame  # pre-lock EdgeRaw: Name, Position, Salary, CalPts, CashOwn
    entered: list[tuple[tuple[str, ...], str]]  # (names, where it came from)
    notes: list[str] = field(default_factory=list)


# --- the result --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GapRow:
    name: str
    position: str
    field: float  # share of all double-up lineups
    in_cashers: float
    in_non_cashers: float
    tag: str  # IN_BUILDS / POOL_ONLY / MISSED / "" (no build record)
    builds: int  # cash builds holding him
    pool: str  # "Cash" / "GPP" / "-" / "" (no pool saved)
    calpts_rank: float | None  # position rank, 1 = best
    value_rank: float | None
    cash_own: float | None  # predicted (CashOwn)

    @property
    def gap(self) -> float:
        return self.in_cashers - self.in_non_cashers

    @property
    def tag_text(self) -> str:
        if self.tag == IN_BUILDS:
            return f"in {self.builds} cash build{'s' if self.builds != 1 else ''}"
        if self.tag == POOL_ONLY:
            return f"pool only ({self.pool})"
        if self.tag == MISSED:
            return "missed entirely"
        return f"no build record (pool {self.pool or '?'})"


@dataclass(frozen=True)
class BuildScore:
    build: CashBuild
    points: float
    cashed: bool


@dataclass(frozen=True)
class EnteredDiff:
    names: tuple[str, ...]
    origin: str
    points: float
    cashed: bool
    nearest: BuildScore | None
    out: list[tuple[str, float]]  # in the build, not in his lineup (points)
    into: list[tuple[str, float]]  # in his lineup, not in the build

    @property
    def cost(self) -> float:
        return sum(p for _, p in self.out) - sum(p for _, p in self.into)


@dataclass(frozen=True)
class PostMortem:
    season: int
    week: int
    lineups: int
    cashers: int
    cash_line: float
    gaps: list[GapRow]
    builds: list[BuildScore] | None
    entered: list[EnteredDiff]
    outside_builds: tuple[float, float] | None  # mean players outside our cash builds: cashers, non-cashers
    notes: list[str]


def _ranks(edge: pd.DataFrame) -> pd.DataFrame:
    e = edge.copy()
    e["k"] = e["Name"].map(key)
    cal = pd.to_numeric(e.get("CalPts", e.get("ProjPts")), errors="coerce")
    salary = pd.to_numeric(e["Salary"], errors="coerce")
    e["_cal"] = cal
    e["_value"] = cal / salary * 1000
    e["calpts_rank"] = e.groupby("Position")["_cal"].rank(ascending=False, method="min")
    e["value_rank"] = e.groupby("Position")["_value"].rank(ascending=False, method="min")
    e["cash_own"] = pd.to_numeric(e["CashOwn"], errors="coerce") if "CashOwn" in e else float("nan")
    return e.drop_duplicates("k").set_index("k")


def _num(v: object) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def postmortem(inputs: WeekInputs) -> PostMortem:
    rows = [(players, pts >= c.cash_line, name) for c in inputs.contests for players, pts, name in c.lineups]
    cashers = sum(1 for _, cashed, _ in rows if cashed)
    losers = len(rows) - cashers
    count_c: dict[str, int] = {}
    count_n: dict[str, int] = {}
    for players, cashed, _ in rows:
        counts = count_c if cashed else count_n
        for p in players:
            counts[p] = counts.get(p, 0) + 1
    edge = _ranks(inputs.edge)
    names = {}
    for c in inputs.contests:  # the standings' own spelling, for a player the edge does not hold
        for players, _, _ in c.lineups:
            for p in players:
                names.setdefault(p, p)
    build_keys = [b.keys for b in inputs.builds] if inputs.builds is not None else None
    gaps = []
    for p in set(count_c) | set(count_n):
        share = (count_c.get(p, 0) + count_n.get(p, 0)) / max(1, len(rows))
        if share < MIN_FIELD:
            continue
        in_builds = sum(p in k for k in build_keys) if build_keys is not None else 0
        pool = "" if inputs.pool is None else inputs.pool.get(p, "-")
        if build_keys is None:
            tag = ""
        elif in_builds:
            tag = IN_BUILDS
        elif pool not in ("", "-"):
            tag = POOL_ONLY
        else:
            tag = MISSED
        e = edge.loc[p] if p in edge.index else None
        gaps.append(
            GapRow(
                name=str(e["Name"]).strip() if e is not None else names[p],
                position=str(e["Position"]) if e is not None else "",
                field=share,
                in_cashers=count_c.get(p, 0) / max(1, cashers),
                in_non_cashers=count_n.get(p, 0) / max(1, losers),
                tag=tag,
                builds=in_builds,
                pool=pool,
                calpts_rank=_num(e["calpts_rank"]) if e is not None else None,
                value_rank=_num(e["value_rank"]) if e is not None else None,
                cash_own=_num(e["cash_own"]) if e is not None else None,
            )
        )
    gaps.sort(key=lambda g: (-g.gap, g.name))
    line = min(c.cash_line for c in inputs.contests)
    fpts: dict[str, float] = {}
    for c in inputs.contests:
        fpts.update(c.fpts)
    scores = None
    if inputs.builds is not None:
        scores = []
        for b in inputs.builds:
            pts = sum(fpts.get(k, 0.0) for k in b.keys)
            scores.append(BuildScore(b, pts, pts >= line))
    outside = None
    if build_keys:
        union = frozenset().union(*build_keys)
        out_c = [len(p - union) for p, cashed, _ in rows if cashed]
        out_n = [len(p - union) for p, cashed, _ in rows if not cashed]
        if out_c and out_n:
            outside = (sum(out_c) / len(out_c), sum(out_n) / len(out_n))
    entered = []
    for names_, origin in inputs.entered:
        keys = frozenset(key(n) for n in names_)
        pts = sum(fpts.get(k, 0.0) for k in keys)
        nearest = max(scores, key=lambda s: (len(s.build.keys & keys), s.points)) if scores else None
        out, into = [], []
        if nearest is not None:
            by_key = {key(n): n for n in nearest.build.names}
            out = [(by_key[k], fpts.get(k, 0.0)) for k in sorted(nearest.build.keys - keys)]
            mine = {key(n): n for n in names_}
            into = [(mine[k], fpts.get(k, 0.0)) for k in sorted(keys - nearest.build.keys)]
        entered.append(EnteredDiff(tuple(names_), origin, pts, pts >= line, nearest, out, into))
    return PostMortem(
        season=inputs.season,
        week=inputs.week,
        lineups=len(rows),
        cashers=cashers,
        cash_line=line,
        gaps=gaps[:TOP_N],
        builds=scores,
        entered=entered,
        outside_builds=outside,
        notes=list(inputs.notes),
    )


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def _rank(v: float | None) -> str:
    return "-" if v is None else f"#{v:.0f}"


def report_lines(pm: PostMortem) -> list[str]:
    out = [
        f"CASH POST-MORTEM, Week {pm.week}: {pm.lineups:,} double-up lineups, {pm.cashers:,} cashed "
        f"(cash line {pm.cash_line:.1f})"
    ]
    out += [f"  {n}" for n in pm.notes]
    out.append(
        f"  Biggest casher / non-casher gaps (players in {MIN_FIELD:.0%}+ of the field): in cashers, in "
        "non-cashers, our tag, pre-lock CalPts / Value position rank, CashOwn predicted vs actual"
    )
    for g in pm.gaps:
        out.append(
            f"  {g.name:<22} {g.position:<3} {_pct(g.in_cashers):>4} vs {_pct(g.in_non_cashers):>4}  "
            f"{g.tag_text:<26} CalPts {_rank(g.calpts_rank):>4} Value {_rank(g.value_rank):>4}  "
            f"CashOwn {_pct(g.cash_own):>4} vs {_pct(g.field):>4}"
        )
    if pm.gaps and pm.gaps[0].tag:
        n_in = sum(g.tag == IN_BUILDS for g in pm.gaps)
        out.append(f"  {n_in} of the top {len(pm.gaps)} were in our cash builds.")
    if pm.outside_builds is not None:
        c, n = pm.outside_builds
        out.append(f"  Players outside our cash builds: cashers {c:.1f}, non-cashers {n:.1f} (of 9).")
    if pm.builds is None:
        out.append("  Cash builds: no build record for the week.")
    elif pm.builds:
        cashed = sum(s.cashed for s in pm.builds)
        best = max(s.points for s in pm.builds)
        out.append(
            f"  Cash builds: {cashed} of {len(pm.builds)} distinct would have cashed "
            f"(line {pm.cash_line:.1f}, "
            f"best {best:.1f})."
        )
        for s in sorted(pm.builds, key=lambda s: -s.points):
            shown = f", shown Cash % {s.build.shown_cash:.0%}" if s.build.shown_cash is not None else ""
            out.append(f"    {s.build.label:<20} {s.points:6.1f} {'CASHED' if s.cashed else '      '}{shown}")
    for d in pm.entered:
        out.append(
            f"  Entered ({d.origin}): {d.points:.1f} {'cashed' if d.cashed else 'did not cash'}"
            + (f"; nearest {d.nearest.build.label} ({d.nearest.points:.1f})" if d.nearest is not None else "")
        )
        if d.nearest is not None and (d.out or d.into):
            swaps = ", ".join(f"{a} ({pa:.1f})" for a, pa in d.out)
            ins = ", ".join(f"{b} ({pb:.1f})" for b, pb in d.into)
            out.append(f"    left out {swaps}; put in {ins}: {d.cost:+.1f} pts against the build")
        elif d.nearest is not None:
            out.append("    the same nine as the build")
    if not pm.entered:
        out.append("  Entered cash lineup: none found (no record and no srwildman row in the double-ups).")
    return out


CSV_COLUMNS = [
    "season",
    "week",
    "rank",
    "name",
    "position",
    "field",
    "in_cashers",
    "in_non_cashers",
    "gap",
    "tag",
    "cash_builds",
    "pool",
    "calpts_rank",
    "value_rank",
    "cash_own_pred",
    "du_own_actual",
]


def csv_rows(pm: PostMortem) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "season": pm.season,
                "week": pm.week,
                "rank": i,
                "name": g.name,
                "position": g.position,
                "field": round(g.field, 4),
                "in_cashers": round(g.in_cashers, 4),
                "in_non_cashers": round(g.in_non_cashers, 4),
                "gap": round(g.gap, 4),
                "tag": g.tag or "no build record",
                "cash_builds": g.builds,
                "pool": g.pool,
                "calpts_rank": g.calpts_rank,
                "value_rank": g.value_rank,
                "cash_own_pred": g.cash_own,
                "du_own_actual": round(g.field, 4),
            }
            for i, g in enumerate(pm.gaps, start=1)
        ],
        columns=CSV_COLUMNS,
    )


def append_csv(pm: PostMortem, path: Path | None = None) -> Path:
    """Add the week's rows to the running file, replacing any rows the week already had."""
    path = path or POSTMORTEM_CSV
    new = csv_rows(pm)
    if path.exists() and path.stat().st_size:
        old = pd.read_csv(path)
        old = old[~((old["season"] == pm.season) & (old["week"] == pm.week))]
        new = pd.concat([old, new], ignore_index=True).sort_values(["season", "week", "rank"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    new.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return path


# --- the chalk check (dfs lineups suggest) -----------------------------------------------------------------


def chalk_check(edge: pd.DataFrame, cash_builds: Sequence[Sequence[str]]) -> list[str]:
    """Every player predicted at `CHALK_CASH_OWN` or more in double-ups (CashOwn) and how many of the cash
    builds use him, most owned first. [] when CashOwn is not out (or nobody is that high)."""
    if "CashOwn" not in edge or not len(cash_builds):
        return []
    own = pd.to_numeric(edge["CashOwn"], errors="coerce")
    chalk = edge.assign(_own=own)[own >= CHALK_CASH_OWN].sort_values("_own", ascending=False)
    chalk = chalk.drop_duplicates("Name")
    if chalk.empty:
        return []
    keys = [frozenset(key(n) for n in b) for b in cash_builds]
    out = [f"Cash chalk check (CashOwn {CHALK_CASH_OWN:.0%}+; display only, the builder is unchanged):"]
    for _, r in chalk.iterrows():
        k = key(r["Name"])
        n = sum(k in b for b in keys)
        fade = "  <- in none of them: a fade" if n == 0 else ""
        out.append(
            f"  {str(r['Name']).strip():<22} {r['Position']:<3} CashOwn {r['_own']:.0%}  "
            f"in {n} of {len(keys)} cash builds{fade}"
        )
    return out


# --- reading the week ---------------------------------------------------------------------------------------

CASH_ENTRY_FORMATS = frozenset({"Double Up", "50/50", "H2H"})


def cash_builds_of(state) -> list[CashBuild]:  # noqa: ANN001 - suggest_state.StateFile
    """The week's distinct cash builds over every round (first time seen wins the label)."""
    seen: dict[frozenset[str], CashBuild] = {}
    for rnd in state.rounds:
        for b in rnd.builds:
            if b.kind != "cash":
                continue
            build = CashBuild(
                tuple(b.names), f"build {b.number} (round {rnd.round})", b.metrics.get("p_cash")
            )
            seen.setdefault(build.keys, build)
    return list(seen.values())


def du_contest(standings) -> DuContest:  # noqa: ANN001 - dfs.contests.standings.Standings
    """A parsed double-up standings file as the post-mortem reads it (points per player: its own table)."""
    rows = standings.lineups
    own = standings.ownership
    return DuContest(
        contest_id=standings.contest_id,
        cash_line=float(standings.profile.cash_line),
        lineups=[
            (frozenset(key(n) for _, n in slots), float(pts), str(name))
            for slots, pts, name in zip(rows["slots"], rows["points"], rows["entry_name"], strict=True)
        ],
        fpts={key(p): float(v) for p, v in zip(own["player"], own["fpts"], strict=True) if v == v},
    )


def load_week(season: int, week: int) -> WeekInputs:
    """The week's double-up standings (the downloads `week close` keeps), its saved cash builds and pool, the
    last pre-lock EdgeRaw and what was entered. Raises FileNotFoundError when there are no double-up
    standings for the week."""
    from dfs import entered as entered_mod
    from dfs import pool_store, suggest_state
    from dfs.contests import week_frames
    from dfs.research import curse
    from dfs.sim import contest_retro

    notes: list[str] = []
    real = [c for c in contest_retro.load_real_contests(weeks={week}) if c.format == DOUBLE_UP]
    if not real:
        raise FileNotFoundError(f"no double-up standings for Week {week} (download them before the close)")
    contests = [du_contest(c.standings) for c in real]
    state = suggest_state.load_state(week)
    builds = cash_builds_of(state) if state.rounds else None
    if builds is None:
        notes.append(suggest_state.missing_history_note(week) + ": players are tagged by the pool only.")
    lock = week_frames.lock_utc(season, week)
    snap = pool_store.pool_at(season, week, lock)
    pool = {key(n): v for n, v in snap.pool.items()} if snap is not None else None
    if pool is None:
        notes.append(f"No pool saved for Week {week}.")
    edge = curse.main_slate(contest_retro.pre_lock_edge(week, season), week, season)
    entered: list[tuple[tuple[str, ...], str]] = []
    record = entered_mod.load(season, week)
    if record is not None:
        for lu in record.lineups:
            if any(c.format in CASH_ENTRY_FORMATS for c in lu.contests) or (not lu.contests and lu.label):
                entered.append((tuple(lu.names), f"entries record, {lu.source_text}"))
    if not entered:
        mine: dict[frozenset[str], tuple[str, ...]] = {}
        for c in real:
            rows = c.standings.lineups
            for slots, name in zip(rows["slots"], rows["entry_name"], strict=True):
                if str(name).strip().lower() == ME:
                    names = tuple(n for _, n in slots)
                    mine.setdefault(frozenset(key(n) for n in names), names)
        entered = [(names, "double-up standings") for names in mine.values()]
    return WeekInputs(season, week, contests, builds, pool, edge, entered, notes)


def run(season: int, week: int, *, append: bool = True) -> list[str]:
    """The post-mortem's lines for the week (and its rows appended to the running file)."""
    pm = postmortem(load_week(season, week))
    lines = report_lines(pm)
    if append and pm.gaps:
        lines.append(f"  Kept in {append_csv(pm)}")
    return lines
