"""Hindsight checks for the playoff and survival simulations, and the history they back-fill.

**The state at an earlier week** (`league_at`). A league as it stood after week c, rebuilt from its own
matchup history: wins (the median game's included, as Sleeper counts them), points for, the teams still alive
(chopped: the lowest score of each week out), and each roster as it was set for week c + 1 (the `players`
Sleeper keeps on that week's matchup; this week's real roster for the latest cutoff). Injury statuses are
blanked: the table holds today's, which nobody knew at the cutoff. Everything else is production code
(`league_view.assemble` at `through=c`, the same blend, spread and `Evaluator`).

**What is scored** (held out: every prediction is made from weeks 1..c and judged on weeks c+1..L, L = the
last week scored so far; the spread scale is refitted on weeks 1..c too):

- a head-to-head game's winner (P that the first team scores more);
- redraft: is a team in the playoff places, and in a bye place, on the standings after week L (wins, then
  points for, the league's own seeding rules); chopped: is a team still alive after each of weeks c + 1..L;
- the Brier score of each model, against naive ones:
  - `record`: a rating from the record alone (wins + 1 over games + 2, as a probit rating), no scores or
    rosters;
  - `scoring`: each team's average real score so far, shrunk toward the league average by how much of the
    spread between teams is real (empirical Bayes, fitted on the same weeks 1..c), one pooled weekly spread,
    no rosters or projections;
  - `uncalibrated`: ours with the spread left at the model's own (scale 1);
  - a constant (a coin for a game, the base rate for the playoff places, an even chop for survival).

One caveat is stated wherever the numbers are: Sleeper's projections for weeks after c are its last ones
before those games, a little fresher than a forecast made at c would have had.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import median

import numpy as np
import pandas as pd
from scipy.stats import norm

from dfs.season_long import calibration, history, objective, outlook, ros
from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long.league_view import LeagueView, assemble

MIN_CUTOFF = 2
EVENT_COLUMNS = ["league", "cutoff", "kind", "model", "p", "y"]
POOLED_SD_FLOOR = 5.0
RATING_CLIP = (0.04, 0.96)


@dataclass
class State:
    """A league after week `through`."""

    through: int
    wins: dict[int, float]
    points_for: dict[int, float]
    games: dict[int, int]
    alive: set[int]
    scores: dict[int, list[float]]  # roster_id -> real score of each week so far


def week_points(league: sl.League, week: int) -> dict[int, float]:
    return {
        int(m["roster_id"]): float(m["points"])
        for m in sl.fetch_matchups(league.league_id, week, final=True)
        if m.get("points") is not None
    }


def week_pairs(league: sl.League, week: int) -> list[tuple[int, int]]:
    groups: dict[int, list[int]] = {}
    for m in sl.fetch_matchups(league.league_id, week, final=True):
        if m.get("matchup_id") is not None:
            groups.setdefault(int(m["matchup_id"]), []).append(int(m["roster_id"]))
    return [(g[0], g[1]) for g in groups.values() if len(g) == 2]


def state_after(league: sl.League, through: int) -> State:
    """Records, points and (chopped) survivors after week `through`, from the matchups themselves."""
    median_game = bool(league.raw_settings.get("league_average_match"))
    ids = [t.roster_id for t in league.teams]
    st = State(through, dict.fromkeys(ids, 0.0), dict.fromkeys(ids, 0.0), dict.fromkeys(ids, 0), set(ids), {})
    for w in range(1, through + 1):
        pts = week_points(league, w)
        if not pts:
            continue
        for rid, x in pts.items():
            st.points_for[rid] = st.points_for.get(rid, 0.0) + x
            st.scores.setdefault(rid, []).append(x)
            st.games[rid] = st.games.get(rid, 0) + 1
        if league.chopped:
            playing = {r: x for r, x in pts.items() if r in st.alive}
            if len(playing) > 1:
                st.alive.discard(min(playing, key=playing.get))
            continue
        for a, b in week_pairs(league, w):
            if pts.get(a, 0.0) > pts.get(b, 0.0):
                st.wins[a] += 1
            elif pts.get(b, 0.0) > pts.get(a, 0.0):
                st.wins[b] += 1
        if median_game:
            med = median(pts.values())
            for rid, x in pts.items():
                st.wins[rid] += x > med
                st.games[rid] += 1  # the median game is a second game
    return st


def league_at(league: sl.League, through: int, last_scored: int) -> tuple[sl.League, State]:
    """`league` as it stood after week `through` (the league itself at `last_scored`)."""
    st = state_after(league, through)
    if through >= last_scored:
        return league, st
    nxt = {int(m["roster_id"]): list(m.get("players") or []) for m in
           sl.fetch_matchups(league.league_id, through + 1, final=True)}  # fmt: skip
    this = {int(m["roster_id"]): list(m.get("players") or []) for m in
            sl.fetch_matchups(league.league_id, through, final=True)}  # fmt: skip
    teams = []
    for t in league.teams:
        teams.append(
            replace(
                t,
                players=[str(p) for p in (nxt.get(t.roster_id) or this.get(t.roster_id) or [])],
                wins=int(st.wins[t.roster_id]),
                points_for=round(st.points_for[t.roster_id], 2),
                alive=t.roster_id in st.alive if league.chopped else True,
            )
        )
    return replace(league, current_week=through + 1, last_scored_week=through, teams=teams), st


def blank_injuries(table: pd.DataFrame) -> pd.DataFrame:
    out = table.copy()
    out["injury_status"] = ""
    return out


# -- naive models ------------------------------------------------------------------------------------------


def record_ratings(ids: list[int], st: State) -> np.ndarray:
    """A probit rating from the record alone: Phi^-1 of (wins + 1) / (games + 2), clipped."""
    r = np.array([(st.wins[i] + 1.0) / (st.games.get(i, 0) + 2.0) for i in ids])
    return norm.ppf(np.clip(r, *RATING_CLIP))


def scoring_arrays(ids: list[int], st: State, weeks: int) -> tuple[np.ndarray, np.ndarray]:
    """Each team's average real score so far, shrunk toward the league's average by how much of the spread
    between teams is real (empirical Bayes: between-team variance over itself plus the weekly noise over the
    team's games), with one pooled weekly spread for all. No rosters or projections. Weeks x teams."""
    n = np.array([max(len(st.scores.get(i, [])), 1) for i in ids], dtype=float)
    avg = np.array([np.mean(st.scores.get(i, [0.0])) for i in ids])
    resid = [x - np.mean(v) for v in st.scores.values() if len(v) > 1 for x in v]
    dof = max(len(resid) - len(st.scores), 1)
    noise = float(np.sum(np.square(resid)) / dof) if resid else POOLED_SD_FLOOR**2
    grand = float(np.mean(avg))
    between = max(float(np.var(avg, ddof=1)) - noise / float(np.mean(n)), 0.0) if len(avg) > 1 else 0.0
    keep = between / (between + noise / n) if between > 0 else np.zeros_like(n)
    mean = grand + keep * (avg - grand)
    sd = max(math.sqrt(noise), POOLED_SD_FLOOR)
    return np.tile(mean, (weeks, 1)), np.full((weeks, len(ids)), sd)


# -- one cutoff --------------------------------------------------------------------------------------------


def _events(league: str, cutoff: int, kind: str, model: str, p: float, y: float) -> tuple:
    return (league, cutoff, kind, model, float(p), float(y))


def check_cutoff(
    league: sl.League,
    cutoff: int,
    last_scored: int,
    table: pd.DataFrame,
    cfg,
    misses: pd.DataFrame,
) -> tuple[list[tuple], dict[int, dict] | None, LeagueView, objective.Evaluator]:
    """Events scored for one league at one cutoff, the history snapshot of that week, and the view / evaluator
    that made it (the snapshot is the production model's `Evaluator.base()`)."""
    lg, st = league_at(league, cutoff, last_scored)
    scale = calibration.fit_scale(misses, cutoff)
    view = assemble(
        lg, blank_injuries(table) if cutoff < last_scored else table, cfg, scale,
        through=cutoff, final_through=last_scored,
    )  # fmt: skip
    ev = objective.Evaluator(view)
    base = ev.base()
    snap = history.snapshot_teams(base, lg.chopped, view)
    rows: list[tuple] = []
    if cutoff >= last_scored:
        return rows, snap, view, ev
    horizon = last_scored - cutoff
    ids = ev.ids
    n = len(ids)
    name = league.name
    if league.chopped:
        rows += _survival_events(name, cutoff, league, ev, st, ids, horizon)
        return rows, snap, view, ev
    mean, sd = ev.team_arrays()
    mean, sd = mean[:horizon], sd[:horizon]
    raw_sd = sd / ev.spread_scale if ev.spread_scale else sd
    pairs = [week_pairs(league, w) for w in range(cutoff + 1, last_scored + 1)]
    real = state_after(league, last_scored)
    rules = ev.rules
    col = {rid: j for j, rid in enumerate(ids)}
    rating = record_ratings(ids, st)
    s_mean, s_sd = scoring_arrays(ids, st, horizon)
    models = {
        "ours": (mean, sd),
        "uncalibrated": (mean, raw_sd),
        "scoring": (s_mean, s_sd),
        "record": (np.tile(rating, (horizon, 1)), np.ones((horizon, n))),
    }
    # games
    for i, w in enumerate(range(cutoff + 1, last_scored + 1)):
        pts = week_points(league, w)
        for a, b in pairs[i]:
            if a not in col or b not in col or pts.get(a) == pts.get(b):
                continue
            y = float(pts[a] > pts[b])
            rows.append(_events(name, cutoff, "game", "coin", 0.5, y))
            for m, (mu, s) in models.items():
                z = (mu[i, col[a]] - mu[i, col[b]]) / math.sqrt(s[i, col[a]] ** 2 + s[i, col[b]] ** 2)
                rows.append(_events(name, cutoff, "game", m, norm.cdf(z), y))
    # standings after the last scored week
    rank = outlook.seed_rank(
        np.array([[real.wins[i] for i in ids]]), np.array([[real.points_for[i] for i in ids]]), ids, rules
    )[0]
    wins0 = np.array([st.wins[i] for i in ids])
    pf0 = np.array([st.points_for[i] for i in ids])
    normals = ev.normals[:, :horizon, :]
    for m, (mu, s) in models.items():
        sim = outlook.simulate(ids, mu, s, normals, pairs, wins0, pf0, rules)
        for j in range(n):
            rows.append(_events(name, cutoff, "playoffs", m, sim.p_playoffs[j], float(rank[j] < rules.spots)))
            if rules.byes:
                rows.append(_events(name, cutoff, "bye", m, sim.p_bye[j], float(rank[j] < rules.byes)))
    for j in range(n):
        rows.append(
            _events(name, cutoff, "playoffs", "base rate", rules.spots / n, float(rank[j] < rules.spots))
        )
        if rules.byes:
            rows.append(
                _events(name, cutoff, "bye", "base rate", rules.byes / n, float(rank[j] < rules.byes))
            )
    return rows, snap, view, ev


def _survival_events(name, cutoff, league, ev, st, ids, horizon) -> list[tuple]:
    """P(alive after each of the next weeks) per team, against who was."""
    alive_after = {
        k: state_after_alive(league, cutoff + k) for k in range(1, horizon + 1)
    }  # fmt: skip
    out: list[tuple] = []
    n = len(ids)
    mean, sd = ev.team_arrays()
    scale = ev.spread_scale
    s_mean, s_sd = scoring_arrays(ids, st, mean.shape[0])
    teams = {
        rid: [tvm.WeekLineup(w, 0, s_mean[i, j], {}, s_sd[i, j]) for i, w in enumerate(ev.weeks)]
        for j, rid in enumerate(ids)
    }
    scoring = tvm.survival(teams, ev.view.cfg.survival_decay, 0, seed=ev.seed, normals=ev.normals)
    ev.spread_scale = 1.0
    uncal_base = ev.evaluate()
    ev.spread_scale = scale
    base = ev.base()
    for k in range(1, horizon + 1):
        for rid in ids:
            y = float(rid in alive_after[k])
            out.append(_events(name, cutoff, "survive", "ours", base[rid].alive[k - 1], y))
            out.append(_events(name, cutoff, "survive", "uncalibrated", uncal_base[rid].alive[k - 1], y))
            out.append(_events(name, cutoff, "survive", "scoring", scoring.alive_after[rid][k - 1], y))
            out.append(_events(name, cutoff, "survive", "even chop", (n - k) / n, y))
    return out


def state_after_alive(league: sl.League, through: int) -> set[int]:
    return state_after(league, through).alive


# -- the whole check ---------------------------------------------------------------------------------------


def run_check(cfg, table: pd.DataFrame, log=print, backfill: bool = True) -> pd.DataFrame:
    """Every sleeper league, every cutoff 2..L-1 scored (and, with `backfill`, the history written for every
    cutoff 2..L). Returns the events."""
    misses = calibration.all_misses(cfg, table)
    events: list[tuple] = []
    for lc in cfg.leagues:
        if lc.platform != "sleeper":
            continue
        league = sl.load_league(lc.name, lc.league_id, lc.username)
        if lc.playoff_week_start and not league.playoff_week_start:
            league.playoff_week_start = lc.playoff_week_start
        last = league.last_scored_week
        for c in range(MIN_CUTOFF, last + 1):
            log(f"{lc.name}: cutoff Week {c}")
            rows, snap, view, ev = check_cutoff(league, c, last, table, cfg, misses)
            events += rows
            if backfill and snap is not None:
                history.save_snapshot(lc.name, c, snap, "backfill")
    return pd.DataFrame(events, columns=EVENT_COLUMNS)


def sos_check(league: sl.League, table: pd.DataFrame, split: int = 4) -> list[str]:
    """Does the schedule index (Sleeper's own projected matchup effects) match what defenses really allowed?
    Per position, over the NFL defenses: the correlation of the projection-based index with the real one in
    the same weeks (concurrent), of the index from weeks 1..split projections with the real index in the
    weeks after (forward), and, for comparison, of the real index in weeks 1..split with the real one after
    (persistence)."""
    last = league.last_scored_week
    weeks = list(range(1, last + 1))
    proj = ros.projections(league.scoring, league.season, weeks, last)
    act = ros.actuals(league.scoring, league.season, weeks)
    pos = table["position"]
    df = proj.merge(act, on=["player_id", "week"], how="inner")
    df = df[(df["proj"] > 0) & (df["opponent"].astype(str).str.len() > 0)].copy()
    df["position"] = df["player_id"].map(pos)
    g = df.groupby("player_id")
    df["avg_p"], df["avg_a"], df["n"] = (
        g["proj"].transform("mean"),
        g["pts"].transform("mean"),
        g["proj"].transform("size"),
    )
    df = df[(df["avg_p"] >= outlook.SOS_MIN_AVG) & (df["n"] >= outlook.SOS_MIN_WEEKS)]
    df["real"] = df["pts"] / df["avg_a"].clip(lower=1.0)

    def real_index(sub: pd.DataFrame) -> pd.Series:
        return sub.groupby("opponent").apply(
            lambda x: np.average(x["real"], weights=x["avg_p"]), include_groups=False
        )

    full, early = outlook.defense_index(proj, pos), outlook.defense_index(proj[proj["week"] <= split], pos)
    parts = []
    for p in ("QB", "RB", "WR", "TE"):
        sub = df[df["position"] == p]
        late, soon = real_index(sub[sub["week"] > split]), real_index(sub[sub["week"] <= split])
        both = real_index(sub)

        def corr(a: pd.Series, b: pd.Series) -> float:
            j = a.dropna().index.intersection(b.dropna().index)
            return float(np.corrcoef(a[j], b[j])[0, 1]) if len(j) > 5 else math.nan

        parts.append(f"{p} {corr(full[p], both):.2f}/{corr(early[p], late):.2f}/{corr(soon, late):.2f}")
    return [
        f"{league.name}: schedule index vs what defenses really allowed (concurrent / projections of weeks "
        f"1-{split} vs real after / real 1-{split} vs real after): " + "; ".join(parts)
    ]


# -- the report --------------------------------------------------------------------------------------------


def brier(df: pd.DataFrame) -> pd.DataFrame:
    """Brier score and count per (kind, model)."""
    d = df.assign(sq=(df["p"] - df["y"]) ** 2)
    out = d.groupby(["kind", "model"]).agg(brier=("sq", "mean"), n=("sq", "size")).reset_index()
    return out


def paired(df: pd.DataFrame, kind: str, a: str, b: str) -> tuple[float, float, int]:
    """Mean Brier difference a - b (negative = a better) over (league, cutoff) units, its standard error and
    the number of units. The units overlap in time, so the error is optimistic."""
    d = df[df["kind"] == kind].assign(sq=lambda x: (x["p"] - x["y"]) ** 2)
    unit = d.groupby(["league", "cutoff", "model"])["sq"].mean().unstack("model")
    if a not in unit or b not in unit:
        return math.nan, math.nan, 0
    diff = (unit[a] - unit[b]).dropna()
    se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else math.nan
    return float(diff.mean()), se, len(diff)


KIND_TEXT = {
    "game": "a game's winner (head to head)",
    "playoffs": "in the playoff places after the last scored week",
    "bye": "in a bye place after the last scored week",
    "survive": "still alive after each of the next weeks (chopped)",
}
MODEL_ORDER = ["ours", "uncalibrated", "scoring", "record", "coin", "base rate", "even chop"]


def report_lines(df: pd.DataFrame, scale_line: str = "") -> list[str]:
    if df.empty:
        return ["No cutoff could be scored yet."]
    b = brier(df)
    lines = [
        "Held out: each prediction from weeks 1..c, scored on the weeks after (Brier, lower is better).",
        f"Leagues: {', '.join(sorted(df['league'].unique()))}; cutoffs {int(df['cutoff'].min())}-"
        f"{int(df['cutoff'].max())}.",
    ]
    if scale_line:
        lines.append(scale_line)
    for kind, text in KIND_TEXT.items():
        sub = b[b["kind"] == kind]
        if sub.empty:
            continue
        lines.append(f"{text}:")
        for m in MODEL_ORDER:
            r = sub[sub["model"] == m]
            if len(r):
                lines.append(f"  {m:13s} {float(r['brier'].iloc[0]):.4f}   n = {int(r['n'].iloc[0])}")
        for naive in ("record", "scoring", "coin", "base rate", "even chop"):
            if naive in set(sub["model"]):
                d, se, units = paired(df, kind, "ours", naive)
                if units:
                    lines.append(f"  ours - {naive}: {d:+.4f} (se {se:.4f}, {units} league-cutoffs)")
    return lines
