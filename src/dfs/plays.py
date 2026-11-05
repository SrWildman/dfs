"""The plays model (Plan M, round M1, SHADOW): who to play and who to avoid this week, by contest type.

Not a projection model: CalPts stays the projection. A *play* is points relative to price and to what the
field will do. Every rosterable player (`week_plays.rosterable`: the sheet's rosterable pool, not OUT / IR /
Doubtful) gets two **calls**, each a score, a label and ONE reason sentence naming the biggest driver:

- **Cash** -- CORE / PLAY / NEUTRAL / AVOID. Its main input is the simulator's priced Cash %
  (`sim.plays_sim.cash_scores`: his best slot in each of the near-optimal cash lineups, Cash % minus the
  slate's price of $1K, averaged). Two rules sit on top of the score:
  heavy double-up chalk (`CashOwn` 30%+) is CORE unless the best alternative at his position and price
  (`PRICE_BAND`) adds `CHALK_GAP`+ points of Cash % more (fading chalk needs a real gap, and the reason
  says how big), and the `FADE↓` chip makes him AVOID with the signal's Model Check record as the reason.
- **GPP** -- TARGET / LEVERAGE / NEUTRAL / FADE. Its main input is his share of the simulated top-1% lineups
  against his `GppOwn` (`sim.plays_sim.gpp_scores`: leverage, the "optimal % against ownership"). TARGET is
  a top ceiling at fair ownership (the position's highest top-1% shares, not over-owned), LEVERAGE is
  under-owned for his ceiling, FADE is over-owned for it (or carries `FADE↓`). A quarterback's call carries
  his best stack.

**The score is a small linear model per position and contest type** over a handful of inputs (`FEATURES`:
the sim's number, CalPts value, floor or ceiling, ownership, FADE↓, the teammate bump, and for GPP the
tails role), each standardised within the position and week. **It learns week over week** (`fit_weights`):
after every close the weights are refitted on every earlier week only (walk-forward) by ridge regression
SHRUNK toward a simple prior (`PRIOR`, `RIDGE`): the cash target is his realized points per $1K, the GPP
target his share of the real top-1% lineups minus his real ownership (weeks with a 700+ entry GPP's
standings). The weights are saved per week under `data/research/calls_weights/` with the week they were
fitted through; the live calls read the latest one before the week.

**News** (`week_news`). When a regular (15%+ of the team's targets or 30%+ of its carries over his last
three games) is ruled OUT or Doubtful during the week, his teammates get the measured teammate bump
(`teammate_bump.load_table`: RB1 out, RB2 about +4.3 shrunk; WR1 out, WR2 about +1.5) minus whatever their
projection has already moved since the news (nothing is counted twice), before anything is scored; an
absence already on the week's first snapshot is old news the projections have priced. When the news came
after ownership was first published, the field's ownership lags it: his ownership is raised by the factor
measured on earlier weeks' standings (`own_lag`), and the reason says so.

Pure functions: the inputs are frames and the I/O is in the loaders at the bottom (`data/raw/edge`
snapshots, the saved weights) and in `dfs.research.plays_backtest` (the history, the backtest). Nothing
here writes a sheet. The Week page does not show the calls yet (round M2).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import paths
from dfs.derived import VAL_ADJ_ROSTERABLE_TOP_N

SHADOW = "Shadow"
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
CASH_LABELS = ("CORE", "PLAY", "NEUTRAL", "AVOID")
GPP_LABELS = ("TARGET", "LEVERAGE", "NEUTRAL", "FADE")
# "Moved up a level": the order a call climbs in (TARGET and LEVERAGE are the same level, different reasons).
CASH_LEVEL = {"AVOID": 0, "NEUTRAL": 1, "PLAY": 2, "CORE": 3}
GPP_LEVEL = {"FADE": 0, "NEUTRAL": 1, "LEVERAGE": 2, "TARGET": 2}
UNPLAYABLE = frozenset({"OUT", "IR", "D"})
NEWS_STATUSES = frozenset({"OUT", "D"})  # DraftKings' OUT and Doubtful: the news the redistribution acts on

# Labels, per position (counts of the rosterable pool, best score first)
CORE_N = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
PLAY_N = {"QB": 2, "RB": 4, "WR": 6, "TE": 2, "DST": 2}
AVOID_SHARE = 0.30  # the bottom share by score of the RELEVANT players (`relevant`)
RELEVANT_TOP = {"QB": 12, "RB": 24, "WR": 36, "TE": 12, "DST": 12}  # by CalPts: players anyone considers
RELEVANT_OWN = 0.05  # ...or owned this much in either format
TARGET_N = {"QB": 2, "RB": 3, "WR": 4, "TE": 2, "DST": 2}
LEVERAGE_N = {"QB": 2, "RB": 3, "WR": 4, "TE": 2, "DST": 2}
FADE_MIN_OWN = 0.08  # a GPP fade must be owned enough to matter
FADE_SHARE = 0.30  # the bottom share by score of those
CHALK = 0.30  # heavy double-up chalk (Plan D's chalk check)
CHALK_GAP = 2.0  # points of Cash %: the gap to the best alternative that fading chalk needs
PRICE_BAND = 500  # dollars either side: "an alternative at the price"
FADE_TOKEN = "FADE↓"

# The model
FEATURES = {
    "cash": ("sim", "value", "floor", "own", "fade", "bump"),
    "gpp": ("lev", "ceiling", "own", "fade", "bump", "tail"),
}
UNSCALED = frozenset({"fade", "bump", "tail"})  # used as they are (0/1, points / 5, the role's stretch)
BUMP_SCALE = 5.0
# The simple prior every week's fit is shrunk toward: the simulator's number carries the call, FADE↓ (proven:
# 67% hit) pushes down. Units: standardised target per standardised input.
PRIOR = {
    "cash": {"sim": 0.25, "value": 0.0, "floor": 0.0, "own": 0.0, "fade": -0.25, "bump": 0.0},
    "gpp": {"lev": 0.20, "ceiling": 0.0, "own": 0.0, "fade": -0.25, "bump": 0.0, "tail": 0.0},
}
RIDGE = 150.0  # the prior counts as this many player-weeks per position
OWN_LAG_SHRINK = 5.0  # pseudo-players at "no lag" when the late-news ownership factor is fitted
OWN_LAG_MAX = 3.0
OWN_CAP = 0.95

WEIGHTS_SUBDIR = Path("research") / "calls_weights"
HISTORY_SUBDIR = Path("research")
HISTORY_NAME = "calls_history.csv"
INPUT_NAME = "calls"  # `week_inputs.save_input("calls", ...)`
MAX_VERSIONS = 60

# Every edge column the calls may read: all of them exist before the lock. A column holding a result
# (`POST_LOCK_COLUMNS`) is never read (tests/test_plays.py fails if one is added here).
INPUT_COLUMNS = (
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "ProjPts",
    "CalPts",
    "Own%",
    "GppOwn",
    "CashOwn",
    "Avail",
    "Edge",
    "Tgt%",
    "Rush%",
    "Id",
    "GameID",
    "GameStart",
    "OverUnder",
    "Spread",
)
POST_LOCK_COLUMNS = ("DkActual", "FPTS", "Actual", "Points", "%Drafted", "Status")


def num(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    return (
        pd.to_numeric(frame[name], errors="coerce")
        if name in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )


def _avail(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["Avail"].fillna("").astype(str).str.upper()
        if "Avail" in frame.columns
        else pd.Series("", index=frame.index)
    )


def inputs_only(edge: pd.DataFrame) -> pd.DataFrame:
    """The edge frame cut to `INPUT_COLUMNS`: nothing after the lock can reach the calls."""
    return edge[[c for c in INPUT_COLUMNS if c in edge.columns]].copy()


def rosterable(edge: pd.DataFrame) -> pd.DataFrame:
    """The players who get calls: per position the top `VAL_ADJ_ROSTERABLE_TOP_N` by ProjPts (CalPts where
    ProjPts is blank), with a salary and a CalPts, minus OUT / IR / Doubtful (`week_plays.rosterable`)."""
    frame = edge.copy()
    frame["_proj"] = _col(frame, "ProjPts").fillna(_col(frame, "CalPts"))
    frame = frame[(_col(frame, "Salary") > 0) & _col(frame, "CalPts").notna() & frame["_proj"].notna()]
    parts = []
    for pos in POSITIONS:
        at = frame[frame["Position"] == pos].sort_values("_proj", ascending=False, kind="stable")
        at = at.head(VAL_ADJ_ROSTERABLE_TOP_N[pos])
        parts.append(at[~_avail(at).isin(UNPLAYABLE)])
    out = pd.concat(parts) if parts else frame.head(0)
    return out.drop(columns="_proj").drop_duplicates("Name").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------
# News: a regular ruled out during the week, his work to his teammates, ownership that lags it
# ---------------------------------------------------------------------------------------------------------

REGULAR_TARGETS = 0.15  # research_constants.regular_thresholds: a regular's target share ...
REGULAR_CARRIES = 0.30  # ... or carry share, over his last three games
BENEFICIARY_ROLE = {"RB3": "RB3+", "WR4": "WR4+", "TE2": "TE2+", "QB": "QB1"}


@dataclass
class Absence:
    name: str
    team: str
    position: str
    role: str
    status: str
    at: datetime | None  # the first snapshot that listed him OUT / Doubtful (None: before the week's first)
    fresh: bool  # news during the week (not on its first snapshot)
    late: bool  # ...and after ownership was first published


@dataclass
class Bump:
    name: str
    team: str
    absent: str
    absent_role: str
    role: str
    bump: float  # the measured points
    moved: float  # how far his projection has moved since the news
    applied: float  # what is added: bump minus what the projection already moved, never below 0
    late: bool


@dataclass
class News:
    absences: list[Absence] = field(default_factory=list)
    bumps: list[Bump] = field(default_factory=list)

    def by_name(self) -> dict[str, Bump]:
        return {b.name: b for b in self.bumps if b.applied > 0}


def _roles(frame: pd.DataFrame) -> dict[str, str]:
    """Role by name for backs, receivers and tight ends: rank by targets + carries share within team and
    position, everyone included (the absent player keeps the role he had), plus QB / DST."""
    from dfs.sim_inputs import role_for_rank

    out = {str(n): "QB" for n in frame.loc[frame["Position"] == "QB", "Name"]}
    part = frame[frame["Position"].isin(["RB", "WR", "TE"])].copy()
    part["_use"] = _col(part, "Tgt%").fillna(0.0) + _col(part, "Rush%").fillna(0.0)
    part = part.sort_values(["Team", "Position", "_use", "Name"], ascending=[True, True, False, True])
    part["_rank"] = part.groupby(["Team", "Position"]).cumcount()
    for name, pos, rank in zip(part["Name"], part["Position"], part["_rank"], strict=True):
        out[str(name)] = role_for_rank(str(pos), int(rank))
    return out


def _projection(frame: pd.DataFrame) -> pd.Series:
    return _col(frame, "CalPts").fillna(_col(frame, "ProjPts"))


def week_news(
    edge: pd.DataFrame,
    snapshots: Sequence[tuple[datetime, pd.DataFrame]],
    table: Mapping[tuple[str, str], float] | None = None,
    carried: Iterable[str] = (),
) -> News:
    """Who was ruled out during the week and what his teammates inherit (module docstring). `snapshots` are
    the week's earlier edge snapshots, oldest first, each taken before `edge`'s moment (the caller cuts
    them: at the lock for a past week). `carried` names the players already out the week before
    (`carried_out`): designations reset every week, so without it a long absence would read as news."""
    from dfs import teammate_bump

    table = teammate_bump.load_table() if table is None else table
    news = News()
    if not table:
        return news
    frame = edge.reset_index(drop=True)
    avail = _avail(frame)
    first = snapshots[0][1] if snapshots else None
    first_out = (
        set(first.loc[_avail(first).isin(NEWS_STATUSES | {"IR"}), "Name"].astype(str))
        if first is not None
        else set()
    ) | {str(n) for n in carried}
    # the rotation the roles are read from: nobody who was already gone at the week's first snapshot
    rotation = frame[~avail.eq("IR") & ~frame["Name"].astype(str).isin(first_out)]
    roles = _roles(rotation)
    out_rows = frame[avail.isin(NEWS_STATUSES)]
    tgt, rush = _col(frame, "Tgt%"), _col(frame, "Rush%")
    by_team: dict[str, list[Absence]] = {}
    before_news: dict[str, pd.DataFrame | None] = {}
    for i, row in out_rows.iterrows():
        name = str(row["Name"])
        regular = (tgt.get(i, np.nan) or 0) >= REGULAR_TARGETS or (
            rush.get(i, np.nan) or 0
        ) >= REGULAR_CARRIES
        if row["Position"] not in ("RB", "WR", "TE") or not regular:
            continue
        at, before = None, None
        for ts, snap in snapshots:
            hit = snap[snap["Name"].astype(str) == name]
            if not hit.empty and _avail(hit).isin(NEWS_STATUSES | {"IR"}).any():
                at = ts
                break
            before = snap
        fresh = at is not None and name not in first_out
        published = before is not None and (_col(before, "Own%").fillna(0) > 0).any()
        absence = Absence(
            name,
            str(row["Team"]),
            str(row["Position"]),
            roles.get(name, ""),
            str(avail[i]),
            at,
            fresh,
            fresh and published,
        )
        news.absences.append(absence)
        if fresh:
            by_team.setdefault(absence.team, []).append(absence)
            before_news[name] = before
    proj_now = dict(zip(frame["Name"].astype(str), _projection(frame), strict=True))
    for team, absent in by_team.items():
        if len(absent) != 1:  # the research measured one regular out at a time
            continue
        a = absent[0]
        before = before_news.get(a.name)
        proj_before = (
            dict(zip(before["Name"].astype(str), _projection(before), strict=True))
            if before is not None
            else {}
        )
        mates = frame[
            (frame["Team"] == team) & ~avail.isin(UNPLAYABLE) & (frame["Name"].astype(str) != a.name)
        ]
        for name in mates["Name"].astype(str):
            role = roles.get(name, "")
            cell = table.get((a.role, BENEFICIARY_ROLE.get(role, role)))
            if not cell:
                continue
            now_p, was_p = proj_now.get(name), proj_before.get(name)
            moved = (
                (now_p - was_p) if now_p is not None and was_p is not None and not np.isnan(was_p) else 0.0
            )
            moved = 0.0 if moved != moved else float(moved)
            applied = max(0.0, float(cell) - max(0.0, moved)) if cell > 0 else 0.0
            news.bumps.append(
                Bump(name, team, a.name, a.role, role, float(cell), moved, round(applied, 2), a.late)
            )
    return news


def apply_news(edge: pd.DataFrame, news: News) -> pd.DataFrame:
    """`edge` with each beneficiary's CalPts raised by his applied bump (`week_news`)."""
    out = edge.copy()
    bumps = news.by_name()
    if not bumps or "CalPts" not in out.columns:
        return out
    cal = _col(out, "CalPts")
    add = out["Name"].astype(str).map({n: b.applied for n, b in bumps.items()}).fillna(0.0)
    out["CalPts"] = (cal + add).where(cal.notna())
    return out


@dataclass(frozen=True)
class OwnLag:
    """How much a late-news beneficiary's real ownership beat the prediction, per format (a factor)."""

    cash: float = 1.0
    gpp: float = 1.0
    n: int = 0
    weeks: tuple[int, ...] = ()


def fit_own_lag(rows: pd.DataFrame) -> OwnLag:
    """`rows`: one per late-news beneficiary of an earlier week with `fmt`, `pred`, `actual` (fractions).
    The factor is the geometric mean of actual / predicted, shrunk toward 1 by `OWN_LAG_SHRINK`."""
    if rows is None or rows.empty:
        return OwnLag()
    out = {}
    n = 0
    for fmt in ("cash", "gpp"):
        part = rows[(rows["fmt"] == fmt) & rows["pred"].notna() & rows["actual"].notna()]
        if part.empty:
            out[fmt] = 1.0
            continue
        actual = pd.to_numeric(part["actual"], errors="coerce").astype(float)
        pred = pd.to_numeric(part["pred"], errors="coerce").astype(float)
        logs = np.log((actual + 0.01) / (pred + 0.01)).dropna()
        if logs.empty:
            out[fmt] = 1.0
            continue
        k = len(logs)
        n = max(n, k)
        out[fmt] = float(
            np.clip(np.exp(logs.mean() * k / (k + OWN_LAG_SHRINK)), 1 / OWN_LAG_MAX, OWN_LAG_MAX)
        )
    weeks = tuple(sorted({int(w) for w in rows.get("week", pd.Series(dtype=int)).dropna()}))
    return OwnLag(out["cash"], out["gpp"], n, weeks)


# ---------------------------------------------------------------------------------------------------------
# The inputs, one row per rosterable player
# ---------------------------------------------------------------------------------------------------------


def cash_own(frame: pd.DataFrame) -> tuple[pd.Series, str]:
    """The double-up ownership the calls read: `CashOwn`, else TFFB's `Own%` (raw: before Plan D's model the
    tool had no double-up number of its own), else blank."""
    for col, src in (("CashOwn", "CashOwn"), ("Own%", "TFFB Own%")):
        values = _col(frame, col)
        if (values.fillna(0) > 0).any():
            return values, src
    return pd.Series(np.nan, index=frame.index), "not published"


def gpp_own(frame: pd.DataFrame) -> tuple[pd.Series, str]:
    for col, src in (("GppOwn", "GppOwn"), ("Own%", "TFFB Own%")):
        values = _col(frame, col)
        if (values.fillna(0) > 0).any():
            return values, src
    return pd.Series(np.nan, index=frame.index), "not published"


@dataclass
class WeekFeatures:
    frame: pd.DataFrame  # one row per rosterable player (columns below)
    lam: float
    ref_p_cash: float
    lines_mode: str
    own_src: dict[str, str]
    news: News
    stacks: dict[str, str]


FEATURE_COLUMNS = [
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "CalPts",
    "ProjPts",
    "cash_own",
    "gpp_own",
    "late_news",
    "fade",
    "bump",
    "bump_from",
    "cash_value",
    "cash_raw",
    "floor",
    "ceiling",
    "opt_pct",
    "lift",
    "lev",
    "tail",
    "value",
    "relevant",
]


def week_features(
    edge: pd.DataFrame,
    *,
    typed_cash,  # noqa: ANN001 - sim_inputs.CashLine
    now: datetime,
    snapshots: Sequence[tuple[datetime, pd.DataFrame]] = (),
    field_model=None,  # noqa: ANN001 - sim.stacks_retro.FieldModel fitted on earlier weeks (None: defaults)
    own_lag: OwnLag | None = None,
    bump_table: Mapping[tuple[str, str], float] | None = None,
    carried: Iterable[str] = (),
    cap: int = 50_000,
    n_sims: int | None = None,
    field_lineups: int | None = None,
    seed: int = 0,
) -> WeekFeatures:
    """Everything the calls are scored on, from what was known at `now` (module docstring)."""
    from dfs.sim import plays_sim as ps

    edge = inputs_only(edge)
    snapshots = [(t, inputs_only(f)) for t, f in snapshots if t <= now]
    news = week_news(edge, snapshots, bump_table, carried)
    framed = apply_news(edge, news)
    lag = own_lag or OwnLag()
    bumps = news.by_name()
    late = {n for n, b in bumps.items() if b.late}
    c_own, c_src = cash_own(framed)
    g_own, g_src = gpp_own(framed)
    names = framed["Name"].astype(str)
    if late:
        hit = names.isin(late)
        c_own = c_own.where(~hit, (c_own * lag.cash).clip(upper=OWN_CAP))
        g_own = g_own.where(~hit, (g_own * lag.gpp).clip(upper=OWN_CAP))
    if "GppOwn" in framed.columns or "Own%" in framed.columns:
        framed = framed.assign(GppOwn=g_own)  # the field is raked to the corrected GPP ownership
    sim = ps.simulate_slate(framed, typed_cash, n_sims=n_sims or ps.N_SIMS, seed=seed)
    ros = rosterable(framed)
    ros = ros[ros["Name"].astype(str).isin(sim.index)].reset_index(drop=True)
    lineups, _ = ps.reference_lineups(sim, framed, cap=cap, now=now)
    cash = ps.cash_scores(sim, lineups, list(ros["Name"].astype(str)))
    gpp = ps.gpp_scores(
        sim, framed, field_model=field_model, n_lineups=field_lineups or ps.FIELD_LINEUPS, seed=seed
    )
    cols = np.array([sim.index[n] for n in ros["Name"].astype(str)], dtype=int)
    draws = sim.draws[:, cols]
    sal = _col(ros, "Salary")
    own_by = dict(zip(names, c_own, strict=True))
    gown_by = dict(zip(names, g_own, strict=True))
    out = pd.DataFrame(
        {
            "Name": ros["Name"].astype(str),
            "Position": ros["Position"].astype(str),
            "Team": ros["Team"].astype(str),
            "Opp": ros.get("Opp", pd.Series("", index=ros.index)).astype(str),
            "Salary": sal,
            "CalPts": _col(ros, "CalPts"),
            "ProjPts": _col(ros, "ProjPts"),
        }
    )
    out["cash_own"] = out["Name"].map(own_by)
    out["gpp_own"] = out["Name"].map(gown_by)
    out["late_news"] = out["Name"].isin(late)
    edge_tokens = (
        ros["Edge"].fillna("").astype(str) if "Edge" in ros.columns else pd.Series("", index=ros.index)
    )
    out["fade"] = edge_tokens.str.contains(FADE_TOKEN, regex=False).astype(float).to_numpy()
    out["bump"] = out["Name"].map({n: b.applied for n, b in bumps.items()}).fillna(0.0)
    out["bump_from"] = [bumps[n].absent if n in bumps else "" for n in out["Name"]]
    out = out.merge(
        cash.frame[["name", "cash_value", "cash_raw"]], left_on="Name", right_on="name", how="left"
    )
    out = out.drop(columns="name")
    out["floor"] = np.quantile(draws, 0.10, axis=0) / (sal.to_numpy() / 1000)
    out["ceiling"] = np.quantile(draws, 0.90, axis=0) / (sal.to_numpy() / 1000)
    g = gpp.frame.set_index("name")
    for c in ("opt_pct", "lift", "lev"):
        out[c] = out["Name"].map(g[c])
    out["tail"] = [
        ps.TAIL_DELTA.get(sim.role[sim.index[n]], 0.0) if p != "DST" else 0.0
        for n, p in zip(out["Name"], out["Position"], strict=True)
    ]
    out["value"] = out["CalPts"] / (out["Salary"] / 1000)
    out["relevant"] = relevant(out)
    return WeekFeatures(
        frame=out[FEATURE_COLUMNS],
        lam=cash.lam,
        ref_p_cash=cash.ref_p_cash,
        lines_mode=sim.mode,
        own_src={"cash": c_src, "gpp": g_src},
        news=news,
        stacks=gpp.stacks,
    )


def relevant(frame: pd.DataFrame) -> pd.Series:
    """A player anyone would consider: top `RELEVANT_TOP` at his position by CalPts, or owned
    `RELEVANT_OWN`+ in either format. AVOID and FADE are only ever said about these."""
    rank = frame.groupby("Position")["CalPts"].rank(ascending=False, method="first")
    top = rank <= frame["Position"].map(RELEVANT_TOP).fillna(0)
    owned = (frame["cash_own"].fillna(0) >= RELEVANT_OWN) | (frame["gpp_own"].fillna(0) >= RELEVANT_OWN)
    return top | owned


# ---------------------------------------------------------------------------------------------------------
# The model: standardised inputs, ridge toward the prior, walk-forward
# ---------------------------------------------------------------------------------------------------------


def _input(frame: pd.DataFrame, kind: str, name: str) -> pd.Series:
    if name == "sim":
        return frame["cash_value"]
    if name == "own":
        return frame["cash_own" if kind == "cash" else "gpp_own"]
    if name == "bump":
        return frame["bump"] / BUMP_SCALE
    return frame[name]


def design(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    """The inputs of `kind`, standardised within position (an input with no spread is 0); blanks are 0, the
    position's average."""
    out = pd.DataFrame(index=frame.index)
    for name in FEATURES[kind]:
        x = pd.to_numeric(_input(frame, kind, name), errors="coerce").astype(float)
        if name in UNSCALED:
            out[name] = x.fillna(0.0)
            continue
        z = pd.Series(0.0, index=frame.index)
        for _, idx in frame.groupby("Position").groups.items():
            v = x.loc[idx]
            sd = v.std(ddof=0)
            if v.notna().sum() > 1 and sd > 0:
                z.loc[idx] = ((v - v.mean()) / sd).fillna(0.0)
        out[name] = z
    return out


def standardise_target(frame: pd.DataFrame, target: pd.Series) -> pd.Series:
    """`target` standardised within position (the scale the weights are fitted on), clipped at 3 SD."""
    z = pd.Series(np.nan, index=frame.index)
    for _, idx in frame.groupby("Position").groups.items():
        v = target.loc[idx]
        sd = v.std(ddof=0)
        if v.notna().sum() > 2 and sd > 0:
            z.loc[idx] = ((v - v.mean()) / sd).clip(-3, 3)
    return z


@dataclass
class Weights:
    """Per kind and position, a weight per input; `through` is the last week fitted on (0: the prior)."""

    season: int
    through: int
    weights: dict[str, dict[str, dict[str, float]]]  # kind -> position -> input -> weight
    n: dict[str, dict[str, int]]  # kind -> position -> player-weeks fitted on
    weeks: dict[str, list[int]]  # kind -> the weeks fitted on
    own_lag: OwnLag = field(default_factory=OwnLag)
    held_out: dict[str, dict[str, float]] = field(default_factory=dict)  # kind -> measure -> value

    @classmethod
    def prior(cls, season: int) -> Weights:
        return cls(
            season=season,
            through=0,
            weights={k: {p: dict(PRIOR[k]) for p in POSITIONS} for k in FEATURES},
            n={k: dict.fromkeys(POSITIONS, 0) for k in FEATURES},
            weeks={k: [] for k in FEATURES},
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["own_lag"] = asdict(self.own_lag)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Weights:
        lag = d.get("own_lag") or {}
        return cls(
            season=int(d["season"]),
            through=int(d["through"]),
            weights=d["weights"],
            n=d.get("n", {}),
            weeks=d.get("weeks", {}),
            own_lag=OwnLag(
                float(lag.get("cash", 1.0)),
                float(lag.get("gpp", 1.0)),
                int(lag.get("n", 0)),
                tuple(lag.get("weeks", ())),
            ),
            held_out=d.get("held_out", {}),
        )


def ridge(x: np.ndarray, y: np.ndarray, prior: np.ndarray, lam: float = RIDGE) -> np.ndarray:
    """argmin |y - x w|^2 + lam |w - prior|^2."""
    a = x.T @ x + lam * np.eye(x.shape[1])
    return np.linalg.solve(a, x.T @ y + lam * prior)


def fit_weights(
    train: Mapping[int, tuple[pd.DataFrame, pd.Series | None, pd.Series | None]],
    *,
    season: int,
    through: int,
    own_lag: OwnLag | None = None,
) -> Weights:
    """Weights fitted on the weeks in `train` (each: its features frame, the cash target and the GPP target,
    raw; None where the week has none), all of which must be at or before `through`."""
    if any(w > through for w in train):
        raise ValueError(
            f"fit through Week {through} was given later weeks: {sorted(w for w in train if w > through)}"
        )
    out = Weights.prior(season)
    out.through = through
    out.own_lag = own_lag or OwnLag()
    for kind in FEATURES:
        xs, ys, poss, weeks = [], [], [], []
        for w, (frame, cash_y, gpp_y) in sorted(train.items()):
            y = cash_y if kind == "cash" else gpp_y
            if y is None:
                continue
            z = standardise_target(frame, y)
            keep = z.notna() & frame["relevant"].astype(bool)
            if not keep.any():
                continue
            xs.append(design(frame, kind)[keep])
            ys.append(z[keep])
            poss.append(frame.loc[keep, "Position"])
            weeks.append(w)
        out.weeks[kind] = weeks
        if not xs:
            continue
        x_all, y_all, pos_all = pd.concat(xs), pd.concat(ys), pd.concat(poss)
        names = list(FEATURES[kind])
        for pos in POSITIONS:
            m = (pos_all == pos).to_numpy()
            out.n[kind][pos] = int(m.sum())
            if m.sum() < 5:
                continue
            prior = np.array([PRIOR[kind][f] for f in names])
            w_ = ridge(x_all[m][names].to_numpy(float), y_all[m].to_numpy(float), prior)
            out.weights[kind][pos] = {f: round(float(v), 4) for f, v in zip(names, w_, strict=True)}
    return out


def scores(frame: pd.DataFrame, weights: Weights) -> pd.DataFrame:
    """`cash_score` / `gpp_score` and each input's contribution (`cash:sim`, ...), per row of `frame`."""
    out = pd.DataFrame(index=frame.index)
    for kind in FEATURES:
        x = design(frame, kind)
        total = pd.Series(0.0, index=frame.index)
        for f in FEATURES[kind]:
            w = frame["Position"].map(lambda p, f=f, k=kind: weights.weights[k].get(p, PRIOR[k]).get(f, 0.0))
            contrib = x[f] * w.astype(float)
            out[f"{kind}:{f}"] = contrib
            total = total + contrib
        out[f"{kind}_score"] = total
    return out


# ---------------------------------------------------------------------------------------------------------
# The calls
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Call:
    name: str
    position: str
    team: str
    opp: str
    salary: float
    calpts: float | None
    cash_own: float | None
    gpp_own: float | None
    cash: str
    cash_score: float
    cash_reason: str
    gpp: str
    gpp_score: float
    gpp_reason: str
    cash_value: float | None = None  # priced Cash % (points)
    opt_pct: float | None = None  # share of the simulated top-1% lineups
    lev: float | None = None
    stack: str = ""  # a quarterback's best stack
    bump: float = 0.0
    bump_from: str = ""
    late_news: bool = False


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "-" if x is None or x != x else f"{x * 100:.0f}%"


def cash_labels(frame: pd.DataFrame, score: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Label by score within position, then the chalk and FADE↓ rules. Returns (labels, chalk_gap): the gap
    (points of Cash %) to the best alternative at the price, for chalk only."""
    labels = pd.Series("NEUTRAL", index=frame.index)
    gap = pd.Series(np.nan, index=frame.index)
    for pos, idx in frame.groupby("Position").groups.items():
        order = score.loc[idx].sort_values(ascending=False, kind="stable").index
        core, play = CORE_N.get(pos, 1), PLAY_N.get(pos, 2)
        labels.loc[order[:core]] = "CORE"
        labels.loc[order[core : core + play]] = "PLAY"
        rel = [i for i in order if frame.at[i, "relevant"] and labels.at[i] == "NEUTRAL"]
        n_avoid = int(round(AVOID_SHARE * len([i for i in order if frame.at[i, "relevant"]])))
        if n_avoid:
            labels.loc[rel[-n_avoid:]] = "AVOID"
    value = frame["cash_value"]
    for i in frame.index:
        own = frame.at[i, "cash_own"]
        if own is None or own != own or own < CHALK:
            continue
        same = frame[
            (frame["Position"] == frame.at[i, "Position"])
            & ((frame["Salary"] - frame.at[i, "Salary"]).abs() <= PRICE_BAND)
            & (frame.index != i)
        ]
        best = value.loc[same.index].max() if not same.empty else np.nan
        g = (best - value.at[i]) if best == best and value.at[i] == value.at[i] else np.nan
        gap.at[i] = g
        if not (g == g and g >= CHALK_GAP):
            labels.at[i] = "CORE"
    labels[frame["fade"] > 0] = "AVOID"
    return labels, gap


def gpp_labels(frame: pd.DataFrame, score: pd.Series) -> pd.Series:
    labels = pd.Series("NEUTRAL", index=frame.index)
    for pos, idx in frame.groupby("Position").groups.items():
        part = frame.loc[idx]
        fair = part["lev"].fillna(0) >= -0.01
        by_top = (
            part[fair].sort_values("opt_pct", ascending=False, kind="stable").index[: TARGET_N.get(pos, 2)]
        )
        labels.loc[by_top] = "TARGET"
        rest = score.loc[[i for i in idx if labels.at[i] == "NEUTRAL" and (frame.at[i, "lev"] or 0) > 0]]
        labels.loc[rest.sort_values(ascending=False, kind="stable").index[: LEVERAGE_N.get(pos, 2)]] = (
            "LEVERAGE"
        )
        owned = [
            i for i in idx if labels.at[i] == "NEUTRAL" and (frame.at[i, "gpp_own"] or 0) >= FADE_MIN_OWN
        ]
        n_fade = int(round(FADE_SHARE * len(owned)))
        if n_fade:
            labels.loc[score.loc[owned].sort_values(kind="stable").index[:n_fade]] = "FADE"
    labels[frame["fade"] > 0] = "FADE"
    return labels


CASH_DRIVER_TEXT = {
    "sim": "best Cash % for his price",
    "value": "projection per dollar",
    "floor": "floor for his price",
    "own": "how much of the double-up plays him",
    "fade": "the FADE↓ chip",
    "bump": "the work he inherits",
}


def _driver(contribs: Mapping[str, float], up: bool) -> str:
    items = [(k, v) for k, v in contribs.items() if (v > 0) == up and v != 0]
    return max(items, key=lambda kv: abs(kv[1]))[0] if items else ""


def cash_reason(
    row: pd.Series, label: str, contribs: Mapping[str, float], gap: float, alt: str, record: str
) -> str:
    pos, own = row["Position"], row["cash_own"]
    if row["fade"] > 0:
        return (
            record or "FADE↓: a tight end scoring well above his usage, which Model Check has seen fall back"
        )
    if row["bump"] > 0 and label in ("CORE", "PLAY"):
        return (
            f"{row['bump_from']} OUT: {row['Name']} takes about +{row['bump']:.1f} points of his work at "
            f"{_money(row['Salary'])}, more than the projections have moved"
            + ("; the field's ownership lags the news" if row["late_news"] else "")
            + "."
        )
    if own is not None and own == own and own >= CHALK:
        if gap == gap and gap >= CHALK_GAP:
            return (
                f"Fading chalk: {_pct(own)} of the double-up plays him, but {alt} adds {gap:.1f} more "
                f"points of "
                f"Cash % at the price."
            )
        tail = (
            f" (the best alternative at the price, {alt}, is within {gap:.1f} points)"
            if gap == gap and alt
            else ""
        )
        return f"Double-up chalk: {_pct(own)} owned and no clearly better {pos} at his price{tail}."
    if label in ("CORE", "PLAY"):
        d = _driver(contribs, up=True)
        if d == "floor":
            p10 = row["floor"] * row["Salary"] / 1000
            return f"Safe floor for {_money(row['Salary'])}: a 10th percentile of {p10:.1f} points."
        if d == "value":
            return f"Projection per dollar: {row['value']:.2f} points per $1K at {_money(row['Salary'])}."
        return (
            f"Among the best Cash % at {pos} for his price ({_money(row['Salary'])}, "
            f"{row['cash_raw']:.0f}% in his best slot)."
        )
    if label == "AVOID":
        if alt and gap == gap:
            return f"Costs {gap:.1f} points of Cash % against {alt} at the same price."
        d = _driver(contribs, up=False)
        if d == "own":
            return f"Popular for what he adds: {_pct(own)} of the double-up, below-par Cash % for his price."
        return f"Low Cash % for his price at {pos} ({_money(row['Salary'])})."
    driver = CASH_DRIVER_TEXT.get(_driver(contribs, up=True), "middle of the position")
    return f"{driver[0].upper()}{driver[1:]}: neither a core play nor a fade."


def gpp_reason(row: pd.Series, label: str, contribs: Mapping[str, float], record: str) -> str:
    own, opt = row["gpp_own"], row["opt_pct"]
    late = (
        " The field's ownership lags the news: expect him to run above the shown number."
        if row["late_news"]
        else ""
    )
    if row["fade"] > 0:
        return (
            record or "FADE↓: a tight end scoring well above his usage, which Model Check has seen fall back"
        )
    if row["bump"] > 0 and label in ("TARGET", "LEVERAGE"):
        return (
            f"{row['bump_from']} OUT: takes about +{row['bump']:.1f} points of his work; the field's "
            f"projections "
            f"lag.{late}"
        )
    share = f"{_pct(own)} owned, in {_pct(opt)} of simulated top-1% lineups"
    if label == "TARGET":
        return f"Top ceiling at fair ownership: {share}.{late}"
    if label == "LEVERAGE":
        return f"Under-owned for his ceiling: {share}.{late}"
    if label == "FADE":
        return f"Over-owned for his ceiling: {share}."
    return f"In {_pct(opt)} of simulated top-1% lineups at {_pct(own)} owned: about his ownership."


@dataclass
class Calls:
    season: int
    week: int
    at: datetime
    calls: list[Call]
    weights_through: int
    notes: list[str] = field(default_factory=list)
    trust: str = SHADOW

    def by_name(self) -> dict[str, Call]:
        return {c.name: c for c in self.calls}


def make_calls(
    wf: WeekFeatures,
    weights: Weights,
    *,
    season: int,
    week: int,
    at: datetime,
    fade_record: str = "",
) -> Calls:
    """Score and label every row of `wf` (module docstring)."""
    frame = wf.frame.reset_index(drop=True)
    sc = scores(frame, weights)
    c_lab, gap = cash_labels(frame, sc["cash_score"])
    g_lab = gpp_labels(frame, sc["gpp_score"])
    calls = []
    for i, row in frame.iterrows():
        alt_name, alt_gap = "", gap.at[i]
        if row["cash_value"] == row["cash_value"]:
            same = frame[
                (frame["Position"] == row["Position"])
                & ((frame["Salary"] - row["Salary"]).abs() <= PRICE_BAND)
                & (frame.index != i)
            ]
            if not same.empty and same["cash_value"].notna().any():
                j = same["cash_value"].idxmax()
                alt_name = f"{frame.at[j, 'Name']} ({_money(frame.at[j, 'Salary'])})"
                if alt_gap != alt_gap:
                    alt_gap = frame.at[j, "cash_value"] - row["cash_value"]
        cc = {f: float(sc.at[i, f"cash:{f}"]) for f in FEATURES["cash"]}
        gc = {f: float(sc.at[i, f"gpp:{f}"]) for f in FEATURES["gpp"]}
        calls.append(
            Call(
                name=row["Name"],
                position=row["Position"],
                team=row["Team"],
                opp=row["Opp"],
                salary=float(row["Salary"]),
                calpts=num(row["CalPts"]),
                cash_own=num(row["cash_own"]),
                gpp_own=num(row["gpp_own"]),
                cash=c_lab.at[i],
                cash_score=round(float(sc.at[i, "cash_score"]), 3),
                cash_reason=cash_reason(row, c_lab.at[i], cc, alt_gap, alt_name, fade_record),
                gpp=g_lab.at[i],
                gpp_score=round(float(sc.at[i, "gpp_score"]), 3),
                gpp_reason=gpp_reason(row, g_lab.at[i], gc, fade_record),
                cash_value=num(row["cash_value"]),
                opt_pct=num(row["opt_pct"]),
                lev=num(row["lev"]),
                stack=wf.stacks.get(row["Name"], "") if row["Position"] == "QB" else "",
                bump=float(row["bump"]),
                bump_from=str(row["bump_from"]),
                late_news=bool(row["late_news"]),
            )
        )
    notes = [
        f"ownership: cash {wf.own_src['cash']}, GPP {wf.own_src['gpp']}; lines {wf.lines_mode}",
        f"weights fitted through Week {weights.through}"
        if weights.through
        else "weights: the prior (nothing fitted yet)",
    ]
    if wf.news.bumps:
        moved = [b for b in wf.news.bumps if b.applied > 0]
        notes.append(
            f"news: {len(moved)} teammate bump(s) applied"
            + (
                ": " + ", ".join(f"{b.name} +{b.applied:.1f} ({b.absent} OUT)" for b in moved[:6])
                if moved
                else ""
            )
        )
    return Calls(season, week, at, calls, weights.through, notes)


# ---------------------------------------------------------------------------------------------------------
# The record the Week page will read (`week_inputs.save_input("calls", ...)`) and what moved
# ---------------------------------------------------------------------------------------------------------


def call_dict(c: Call) -> dict:
    d = asdict(c)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def moved_up(previous: Iterable[dict], current: Iterable[dict]) -> list[dict]:
    """Every call that climbed a level since the previous version (`CASH_LEVEL`, `GPP_LEVEL`): a player new
    to the list counts from NEUTRAL."""
    before = {c["name"]: c for c in previous}
    out = []
    for c in current:
        old = before.get(c["name"])
        for kind, levels in (("cash", CASH_LEVEL), ("gpp", GPP_LEVEL)):
            was = old[kind] if old else "NEUTRAL"
            if levels.get(c[kind], 1) > levels.get(was, 1):
                out.append(
                    {
                        "name": c["name"],
                        "position": c["position"],
                        "kind": kind,
                        "from": was,
                        "to": c[kind],
                        "reason": c[f"{kind}_reason"],
                    }
                )
    order = {"CORE": 0, "TARGET": 1, "PLAY": 2, "LEVERAGE": 3, "NEUTRAL": 4}
    return sorted(out, key=lambda m: (order.get(m["to"], 9), m["kind"], m["name"]))


def record(calls: Calls, previous: dict | None) -> dict:
    """The `calls` input with this version appended: `versions` (each `at` and its calls, oldest first, at
    most `MAX_VERSIONS`), the latest `calls`, and `moved_up` against the version before."""
    current = [call_dict(c) for c in calls.calls]
    versions = list((previous or {}).get("versions") or [])
    last = versions[-1]["calls"] if versions else []
    versions.append({"at": calls.at.astimezone(UTC).isoformat(timespec="seconds"), "calls": current})
    return {
        "season": calls.season,
        "week": calls.week,
        "trust": calls.trust,
        "weights_through": calls.weights_through,
        "notes": calls.notes,
        "at": versions[-1]["at"],
        "calls": current,
        "moved_up": moved_up(last, current) if last else [],
        "versions": versions[-MAX_VERSIONS:],
    }


def save(calls: Calls, *, data_dir: Path | None = None) -> dict:
    """Append this version to the week's `calls` input (fail-soft like every input). Returns the record."""
    from dfs import week_inputs

    previous = week_inputs.load_input(INPUT_NAME, calls.season, calls.week, data_dir)
    rec = record(calls, previous)
    week_inputs.save_input(
        INPUT_NAME, rec, season=calls.season, week=calls.week, data_dir=data_dir, now=calls.at
    )
    return rec


def top_lines(rec: Mapping, *, per: int = 3, positions: Sequence[str] = POSITIONS) -> list[str]:
    """The terminal view: per position the cash calls (CORE, PLAY, AVOID) and the GPP calls (TARGET,
    LEVERAGE, FADE), `per` per label, best score first, one reason each."""
    calls = list(rec.get("calls") or [])
    out = []
    for pos in positions:
        at = [c for c in calls if c["position"] == pos]
        if not at:
            continue
        out.append(f"{pos}")
        for kind, labels in (("cash", ("CORE", "PLAY", "AVOID")), ("gpp", ("TARGET", "LEVERAGE", "FADE"))):
            for label in labels:
                pick = [c for c in at if c[kind] == label]
                rev = label in ("AVOID", "FADE")
                pick.sort(key=lambda c: c[f"{kind}_score"], reverse=not rev)
                for c in pick[:per]:
                    own = c["cash_own"] if kind == "cash" else c["gpp_own"]
                    extra = f"  [stack: {c['stack']}]" if kind == "gpp" and c.get("stack") else ""
                    out.append(
                        f"  {kind.upper():<4} {label:<8} {c['name']} ({c['team']}, {_money(c['salary'])}, "
                        f"{_pct(own)} own): {c[f'{kind}_reason']}{extra}"
                    )
    return out


def moved_lines(rec: Mapping) -> list[str]:
    moved = rec.get("moved_up") or []
    if not moved:
        return []
    return ["New must-plays since the last version:"] + [
        f"  {m['name']} ({m['position']}): {m['kind']} {m['from']} -> {m['to']}. {m['reason']}" for m in moved
    ]


# ---------------------------------------------------------------------------------------------------------
# Loaders: the week's edge snapshots and the saved weights
# ---------------------------------------------------------------------------------------------------------


def edge_snapshots(
    season: int, week: int, until: datetime, folder: Path | None = None
) -> list[tuple[datetime, pd.DataFrame]]:
    """The week's EdgeRaw snapshots (`data/raw/edge/<stamp>Z.csv`) from its first day through `until`, oldest
    first, cut to `INPUT_COLUMNS`."""
    import re

    from dfs import nfl_calendar

    folder = folder or paths.RAW_DIR / "edge"
    start = nfl_calendar.week_start_date(week, season)
    out = []
    for path in sorted(folder.glob("*.csv")):
        m = re.match(r"^(\d{8}T\d{6})Z\.csv$", path.name)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        if ts.date() < start or ts > until:
            continue
        try:
            out.append((ts, inputs_only(pd.read_csv(path, low_memory=False))))
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
    return out


def carried_out(season: int, week: int, folder: Path | None = None) -> set[str]:
    """Who the previous week's last snapshot before ITS lock listed OUT / Doubtful / IR: old news this week
    (a snapshot after the games carries no designations)."""
    from dfs.contests import week_frames

    if week <= 1:
        return set()
    folder = folder or paths.RAW_DIR / "edge"
    lock = week_frames.lock_utc(season, week - 1)
    before = [p for p in sorted(folder.glob("*Z.csv")) if p.name[:15] <= f"{lock:%Y%m%dT%H%M%S}"]
    for path in reversed(before[-12:]):  # the latest one that carries designations at all
        try:
            frame = pd.read_csv(path, usecols=lambda c: c in ("Name", "Avail"), low_memory=False)
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
        if "Avail" not in frame.columns or frame["Avail"].isna().all():
            continue
        return set(frame.loc[_avail(frame).isin(NEWS_STATUSES | {"IR"}), "Name"].astype(str))
    return set()


def weights_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or paths.DATA_DIR) / WEIGHTS_SUBDIR


def weights_path(season: int, through: int, data_dir: Path | None = None) -> Path:
    return weights_dir(data_dir) / f"{season}-wk{through:02d}.json"


def save_weights(w: Weights, data_dir: Path | None = None) -> Path:
    path = weights_path(w.season, w.through, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(w.to_dict(), indent=1, default=float))
    return path


def load_weights(season: int, before_week: int, data_dir: Path | None = None) -> Weights:
    """The latest saved weights fitted through a week BEFORE `before_week`; the prior when there are none."""
    best = None
    for path in sorted(weights_dir(data_dir).glob(f"{season}-wk*.json")):
        try:
            through = int(path.stem.split("-wk")[1])
        except (IndexError, ValueError):
            continue
        if through < before_week:
            best = path
    if best is None:
        return Weights.prior(season)
    try:
        return Weights.from_dict(json.loads(best.read_text()))
    except (OSError, ValueError, KeyError):
        return Weights.prior(season)


def biggest_shifts(new: Weights, old: Weights, top: int = 3) -> list[str]:
    """The largest weight moves between two fits, in words."""
    moves = []
    for kind in FEATURES:
        for pos in POSITIONS:
            for f in FEATURES[kind]:
                a = old.weights.get(kind, {}).get(pos, {}).get(f, PRIOR[kind][f])
                b = new.weights.get(kind, {}).get(pos, {}).get(f, PRIOR[kind][f])
                moves.append((abs(b - a), f"{kind} {pos} {f} {a:+.2f} -> {b:+.2f}"))
    moves.sort(key=lambda m: -m[0])
    return [m[1] for m in moves[:top] if m[0] > 0.005]


# ---------------------------------------------------------------------------------------------------------
# The live calls: `dfs plays`, and every `dfs sync`
# ---------------------------------------------------------------------------------------------------------


def typed_cash_line(season: int, week: int):  # noqa: ANN201 - sim_inputs.CashLine
    """The fixed cash line the simulator falls back on before ownership publishes: the season median of the
    real double-up lines before `week` (`research.curse.lines_through`), else the placeholder."""
    from dfs import sim_inputs

    try:
        from dfs.research import curse

        lines = curse.lines_through(season, max(week - 1, 0))
        return sim_inputs.cash_line_from_results(
            ["Week", "Cash Line"], [[w, v] for w, v in lines.items()], before_week=week
        )
    except Exception:  # noqa: BLE001 - only the fallback line
        return sim_inputs.cash_line_from_results([], [], before_week=week)


def fade_record_line(season: int, data_dir: Path | None = None) -> str:
    """Model Check's record for `FADE↓`, in one line (`week_plays.record_line`)."""
    from dfs import week_inputs, week_plays

    try:
        table, weeks = week_inputs.load_signal_table(data_dir or paths.DATA_DIR, season)
    except Exception:  # noqa: BLE001
        return ""
    line = week_plays.record_line(FADE_TOKEN, table, weeks)
    return f"{line}: Model Check says these fall back to their usage."


def compute_live(
    season: int,
    week: int,
    *,
    now: datetime | None = None,
    edge: pd.DataFrame | None = None,
    data_dir: Path | None = None,
    save_record: bool = True,
) -> tuple[Calls, dict]:
    """This week's calls from the synced slate (`data/current/edge.csv`), the week's snapshots so far, the
    latest weights fitted before the week and the saved GPP field model; saved as a new version of the
    `calls` input (with `save_record`). Returns the calls and the record."""
    from dfs import stacks_inputs, store

    now = now or datetime.now(UTC)
    if edge is None:
        edge = store.load_current("edge")
    weights = load_weights(season, before_week=week, data_dir=data_dir)
    snaps = edge_snapshots(season, week, now)
    gone = carried_out(season, week)
    wf = week_features(
        edge,
        typed_cash=typed_cash_line(season, week),
        now=now,
        snapshots=snaps,
        field_model=stacks_inputs.load_field_model(),
        own_lag=weights.own_lag if weights.own_lag.n else None,
        carried=gone,
    )
    calls = make_calls(
        wf, weights, season=season, week=week, at=now, fade_record=fade_record_line(season, data_dir)
    )
    if save_record:
        rec = save(calls, data_dir=data_dir)
    else:
        from dfs import week_inputs

        rec = record(calls, week_inputs.load_input(INPUT_NAME, season, week, data_dir))
    return calls, rec
