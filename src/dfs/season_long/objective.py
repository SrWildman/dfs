"""What a roster is worth to the team that holds it: the number a trade moves.

**Redraft.** `value = regular-season points + playoff_weight * P(make the playoffs) * playoff-week points`
(weekly lineup values from `team_value`, the cover term included). P(make the playoffs) comes from simulating
the rest of the regular season on Sleeper's own schedule: each week's matchups (and, where the league plays
one, the extra game against the week's median score), the current records and points for, the top
`playoff_teams` by wins then points for (divisions are not modelled). A team that is safely in weighs its
playoff weeks fully; a team fighting for a spot gets most of a trade's value from what it does to its odds of
getting there (Sam, 2026-10-28: "if I'm still fighting for a spot, we need to get to the playoffs first").

**Chopped.** `value` = the survival value of `team_value.survival` (P(alive after each week), week i ahead
weighed `survival_decay ** i`), with the chopped rosters going to waivers: each chopped roster's best
`PICKUPS_PER_CHOP` players go to survivors in proportion to their FAAB, at the prices this league's own
winning bids set (`pickup_prices`). A team near the cut line lives or dies this week, so this week's points
carry most of its value: a desperate partner pays for now by construction.

Every evaluation of one league shares one set of random draws, so two rosters are compared on the same luck.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long.league_view import LeagueView

PICKUPS_PER_CHOP = 3
# Before a league has any winning bids to learn from, the k-th pick of a chopped roster costs this share of
# the league's budget.
DEFAULT_PRICE_SHARE = (0.25, 0.12, 0.05)
PLAYOFF_DRAWS = 4000


@dataclass
class Outcome:
    value: float
    regular_pts: float = 0.0
    playoff_pts: float = 0.0
    p_playoffs: float = float("nan")
    expected_weeks: float = float("nan")
    p_survive_now: float = float("nan")
    p_last_standing: float = float("nan")  # chopped: alive after the final week


def pickup_prices(league: sl.League) -> np.ndarray:
    """Median over weeks of the k-th highest winning FAAB bid in this league (completed waiver claims)."""
    per_week: list[list[int]] = []
    for w in range(1, league.last_scored_week + 2):
        bids = sorted(
            (
                int((t.get("settings") or {}).get("waiver_bid") or 0)
                for t in sl.fetch_transactions(league.league_id, w, final=w <= league.last_scored_week)
                if t.get("type") == "waiver" and t.get("status") == "complete"
            ),
            reverse=True,
        )
        if bids and bids[0] > 0:
            per_week.append(bids)
    out = []
    for k in range(PICKUPS_PER_CHOP):
        kth = [b[k] for b in per_week if len(b) > k]
        out.append(float(np.median(kth)) if kth else DEFAULT_PRICE_SHARE[k] * league.waiver_budget)
    return np.array(out)


def schedule(league: sl.League, weeks: list[int]) -> dict[int, list[tuple[int, int]]]:
    out: dict[int, list[tuple[int, int]]] = {}
    for w in weeks:
        pairs: dict[int, list[int]] = {}
        for m in sl.fetch_matchups(league.league_id, w, final=False):
            if m.get("matchup_id") is not None:
                pairs.setdefault(int(m["matchup_id"]), []).append(int(m["roster_id"]))
        out[w] = [(a[0], a[1]) for a in pairs.values() if len(a) == 2]
    return out


def playoff_odds(
    ids: list[int],
    mean: np.ndarray,
    sd: np.ndarray,
    normals: np.ndarray,
    pairs: list[list[tuple[int, int]]],
    wins: np.ndarray,
    points_for: np.ndarray,
    median_game: bool,
    spots: int,
) -> np.ndarray:
    """P(top `spots` by wins, then points for) per team. mean/sd: weeks x teams; normals: draws "
    "x weeks x teams."""
    draws = normals.shape[0]
    w = np.tile(wins.astype(float), (draws, 1))
    pf = np.tile(points_for.astype(float), (draws, 1))
    col = {rid: j for j, rid in enumerate(ids)}
    for i in range(mean.shape[0]):
        score = mean[i] + sd[i] * normals[:, i, :]
        pf += score
        for a, b in pairs[i]:
            if a in col and b in col:
                ja, jb = col[a], col[b]
                w[:, ja] += score[:, ja] > score[:, jb]
                w[:, jb] += score[:, jb] > score[:, ja]
        if median_game:
            w += score > np.median(score, axis=1, keepdims=True)
    key = w * 1e5 + pf
    rank = (-key).argsort(axis=1).argsort(axis=1)
    return (rank < spots).mean(axis=0)


class Evaluator:
    def __init__(self, view: LeagueView, seed: int = 11):
        self.view = view
        lg = view.league
        self.ids = [t.roster_id for t in lg.alive_teams()]
        self.rosters = {t.roster_id: list(t.players) for t in lg.alive_teams()}
        self.faab = {t.roster_id: t.faab_left for t in lg.alive_teams()}
        self.weeks = lg.remaining_weeks
        self.playoff = [w for w in self.weeks if w in lg.playoff_weeks]
        self.regular = [w for w in self.weeks if w not in lg.playoff_weeks]
        rng = np.random.default_rng(seed)
        draws = view.cfg.survival_draws if lg.chopped else PLAYOFF_DRAWS
        self.normals = rng.standard_normal((draws, len(self.weeks), len(self.ids)))
        self.seed = seed
        self._lineups: dict[tuple, list[tvm.WeekLineup]] = {}
        wk = view.ros.weekly
        self._pts = {w: dict(zip(g["player_id"], g["pts"], strict=True)) for w, g in wk.groupby("week")}
        if lg.chopped:
            self.prices = pickup_prices(lg)
        else:
            self.pairs = schedule(lg, self.regular)
            self.median_game = bool(lg.raw_settings.get("league_average_match"))
        self._base: dict[int, Outcome] | None = None
        self._base_gain: np.ndarray | None = None
        self._totals: dict[tuple[frozenset, int], float] = {}

    # -- lineups ---------------------------------------------------------------------------------------------
    def lineups(self, roster: list[str]) -> list[tvm.WeekLineup]:
        key = tuple(sorted(roster))
        if key not in self._lineups:
            v = self.view
            self._lineups[key] = tvm.week_lineups(
                list(key), v.ros.weekly, v.positions, v.league.slots, v.cfg.starter_miss_rate
            )
        return self._lineups[key]

    def lineup_total(self, roster: list[str], week: int) -> float:
        key = (frozenset(roster), week)
        if key not in self._totals:
            pts = self._pts.get(week, {})
            lineup = {p: pts.get(p, 0.0) for p in roster}
            self._totals[key] = tvm.best_lineup(lineup, self.view.positions, self.view.league.slots)[0]
        return self._totals[key]

    def _arrays(self, rosters: dict[int, list[str]]) -> tuple[np.ndarray, np.ndarray]:
        mean = np.zeros((len(self.weeks), len(self.ids)))
        sd = np.zeros_like(mean)
        for j, rid in enumerate(self.ids):
            by = {w.week: w for w in self.lineups(rosters[rid])}
            for i, wk in enumerate(self.weeks):
                if wk in by:
                    mean[i, j], sd[i, j] = by[wk].value, by[wk].sd
        return mean, sd

    # -- chopped -------------------------------------------------------------------------------------------
    def pickup_gains(self, rosters: dict[int, list[str]], changed: set[int] | None = None) -> np.ndarray:
        """`gain[t, c, k, i]` (see `team_value.Pickups`). With `changed`, only those teams' rows and columns
        are recomputed from the base rosters' array."""
        summary = self.view.ros.summary
        n = len(self.ids)
        if changed is not None and self._base_gain is not None:
            gain = self._base_gain.copy()
        else:
            gain = np.zeros((n, n, PICKUPS_PER_CHOP, len(self.weeks)))
            changed = None
        base = {rid: [self.lineup_total(rosters[rid], w) for w in self.weeks] for rid in self.ids}
        for c, rc in enumerate(self.ids):
            vals = summary["ros_pts"].reindex(rosters[rc]).dropna().sort_values(ascending=False)
            gain[:, c] = 0.0 if changed is None or rc in changed else gain[:, c]
            for k, pid in enumerate(vals.index[:PICKUPS_PER_CHOP]):
                for t, rt in enumerate(self.ids):
                    if rt == rc or (changed is not None and rc not in changed and rt not in changed):
                        continue
                    for i, w in enumerate(self.weeks):
                        gain[t, c, k, i] = self.lineup_total([*rosters[rt], pid], w) - base[rt][i]
        return gain

    # -- the objective -------------------------------------------------------------------------------------
    def evaluate(
        self, rosters: dict[int, list[str]] | None = None, faab: dict[int, int] | None = None
    ) -> dict[int, Outcome]:
        r = {**self.rosters, **(rosters or {})}
        f = {**self.faab, **(faab or {})}
        mean, sd = self._arrays(r)
        if self.view.league.chopped:
            teams = {}
            for j, rid in enumerate(self.ids):
                teams[rid] = [
                    tvm.WeekLineup(w, 0, mean[i, j], {}, sd[i, j]) for i, w in enumerate(self.weeks)
                ]
            changed = {rid for rid in (rosters or {}) if rosters[rid] != self.rosters.get(rid)}
            if self._base_gain is None:
                self._base_gain = self.pickup_gains(self.rosters)
            gain = self.pickup_gains(r, changed) if changed else self._base_gain
            pickups = tvm.Pickups(gain=gain, faab=np.array([f[rid] for rid in self.ids]), price=self.prices)
            s = tvm.survival(
                teams, self.view.cfg.survival_decay, 0, seed=self.seed, pickups=pickups, normals=self.normals
            )
            return {
                rid: Outcome(
                    value=s.value[rid],
                    expected_weeks=s.expected_weeks[rid],
                    p_survive_now=1 - s.chop_now[rid],
                    p_last_standing=float(s.alive_after[rid][-1]) if len(s.weeks) else 1.0,
                    regular_pts=float(mean[:, j].sum()),
                )
                for j, rid in enumerate(self.ids)
            }
        lg = self.view.league
        reg_idx = [i for i, w in enumerate(self.weeks) if w in self.regular]
        po_idx = [i for i, w in enumerate(self.weeks) if w in self.playoff]
        if po_idx and reg_idx:
            teams_by = {t.roster_id: t for t in lg.teams}
            p = playoff_odds(
                self.ids,
                mean[reg_idx],
                sd[reg_idx],
                self.normals[:, reg_idx, :],
                [self.pairs.get(self.weeks[i], []) for i in reg_idx],
                np.array([teams_by[rid].wins for rid in self.ids]),
                np.array([teams_by[rid].points_for for rid in self.ids]),
                self.median_game,
                lg.playoff_teams or 6,
            )
        else:
            p = np.ones(len(self.ids))
        w_po = self.view.cfg.playoff_weight
        out = {}
        for j, rid in enumerate(self.ids):
            reg = float(mean[reg_idx, j].sum()) if reg_idx else 0.0
            po = float(mean[po_idx, j].sum()) if po_idx else 0.0
            out[rid] = Outcome(
                value=reg + w_po * p[j] * po, regular_pts=reg, playoff_pts=po, p_playoffs=float(p[j])
            )
        return out

    def base(self) -> dict[int, Outcome]:
        if self._base is None:
            self._base = self.evaluate()
        return self._base

    def faab_dollar_value(self, roster_id: int, step: int = 50) -> float:
        """Chopped: what one more FAAB dollar adds to this team's survival value (finite difference)."""
        have = self.faab[roster_id]
        up = self.evaluate(faab={roster_id: have + step})[roster_id].value
        down = self.evaluate(faab={roster_id: max(0, have - step)})[roster_id].value
        return (up - down) / (have + step - max(0, have - step))
