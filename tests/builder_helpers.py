"""The builder tests' shared synthetic slate and helpers (no tests here): the slate, the pool tags, one call
of `suggest_builds`, the legality check, a plausible ownership and the exhaustive search the searches are
checked against. `test_lineup_suggest*.py`, the CLI tests and the other builder files import it."""

import copy
from datetime import UTC, datetime
from itertools import combinations

import numpy as np
import pandas as pd

from dfs import lineup_suggest as ls
from dfs.late_swap_search import conflicts
from dfs.models import ROSTER_SLOTS
from dfs.sim_inputs import build_specs

CAP = 50000
SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
EARLY, LATE, NIGHT = "2026-10-18T13:00:00Z", "2026-10-18T16:25:00Z", "2026-10-18T20:20:00Z"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)  # 8 am ET Sunday: nothing has kicked off
N_SIMS = 1500  # plenty for the rules; the sheet itself uses 20,000


def slate(teams=6, seed=1, per_team=None):
    """A synthetic slate: `teams` teams in `teams // 2` games; each has a QB, 2 RB, 4 WR, 2 TE and a DST with
    salaries that make the cap bind. Kickoffs rotate through three slots."""
    rng = np.random.default_rng(seed)
    per_team = per_team or {"QB": 1, "RB": 2, "WR": 4, "TE": 2, "DST": 1}
    base = {
        "QB": (14, 24, 5000, 7500),
        "RB": (6, 18, 4000, 8500),
        "WR": (5, 17, 3500, 8500),
        "TE": (4, 12, 2800, 5500),
        "DST": (3, 9, 2000, 3500),
    }
    rows, pid = [], 1
    for t in range(teams):
        game, side = divmod(t, 2)
        team, opp = f"T{t:02d}", f"T{(t ^ 1):02d}"
        start = (EARLY, LATE, NIGHT)[game % 3]
        for pos, n in per_team.items():
            lo, hi, slo, shi = base[pos]
            for k in range(n):
                skill = rng.uniform(0.0, 1.0)
                rows.append(
                    {
                        "Id": pid,
                        "Name": f"{team} {pos}{k + 1}",
                        "Position": pos,
                        "Team": team,
                        "Opp": opp,
                        "GameID": f"G{game}",
                        "GameStart": start,
                        "Salary": int(round((slo + (shi - slo) * skill + rng.normal(0, 150)) / 100) * 100),
                        "CalPts": round(lo + (hi - lo) * skill + rng.normal(0, 1.0), 1),
                        "ProjPts": round(lo + (hi - lo) * skill, 1),
                        "Avail": "",
                        "Own%": 0.0,
                    }
                )
                pid += 1
    return pd.DataFrame(rows)


def tags_for(edge, tag="Both"):
    return dict.fromkeys(edge["Name"], tag)


def run(edge, tags=None, **kw):
    kw.setdefault("n_sims", N_SIMS)
    return ls.suggest_builds(
        edge,
        tags if tags is not None else tags_for(edge),
        build_specs(edge),
        now=NOW,
        cap=CAP,
        cash_line=145.0,
        gpp_target=190.0,
        metric="CalPts",
        **kw,
    )


def assert_legal(build):
    players = list(build.players)
    assert len(players) == 9 and len({p.name for p in players}) == 9
    assert sum(p.salary for p in players) <= CAP and build.salary == sum(p.salary for p in players)
    for slot, p in zip(ROSTER_SLOTS, players, strict=True):  # slot positions, FLEX takes an RB, WR or TE
        assert p.position in (("RB", "WR", "TE") if slot == "FLEX" else (slot,))
    for p in players:
        assert not conflicts(p, [o for o in players if o is not p])  # no DST vs own QB, one RB a game


def owned(edge, seed=1):
    """`edge` with a plausible ownership: popular where value is high, position totals as on a real slate."""
    rng = np.random.default_rng(seed)
    out = edge.copy()
    value = (out["CalPts"] / out["Salary"] * 1000) ** 3 * rng.uniform(0.6, 1.4, len(out))
    for pos, total in {"QB": 1.0, "RB": 2.2, "WR": 3.4, "TE": 1.1, "DST": 1.0}.items():
        mask = out["Position"] == pos
        out.loc[mask, "Own%"] = (value[mask] / value[mask].sum() * total).clip(upper=0.6)
    return out


def brute_force(pool, k):
    """The k best legal lineup totals by exhaustive search: every 9-set with the right position counts."""
    by = {pos: [p for p in pool if p.position == pos] for pos in SLOTS}
    totals = {}
    for qb in by["QB"]:
        for dst in by["DST"]:
            for rbs in combinations(by["RB"], 2):
                for wrs in combinations(by["WR"], 3):
                    for te in by["TE"]:
                        used = {qb, dst, te, *rbs, *wrs}
                        for flex in (p for p in pool if p.position in ("RB", "WR", "TE") and p not in used):
                            lineup = [qb, *rbs, *wrs, te, flex, dst]
                            if sum(p.salary for p in lineup) > CAP:
                                continue
                            if any(conflicts(p, [o for o in lineup if o is not p]) for p in lineup):
                                continue
                            totals[frozenset(p.name for p in lineup)] = round(sum(p.value for p in lineup), 6)
    # each SET of nine players counts once, however its surplus position is seated
    return sorted(totals.values(), reverse=True)[:k]


_REPORTS: dict = {}


def cli_report():
    """A copy of ONE real report on the CLI tests' slate (`slate(teams=4, seed=2)`, every player Both, one
    cash and one GPP build), built once per session (per worker): the command tests that only check what
    the command does around the builder hand it this instead of running the search again."""
    if "report" not in _REPORTS:
        edge = slate(teams=4, seed=2)
        _REPORTS["report"] = run(edge, cash_builds=1, gpp_builds=1)
    return copy.deepcopy(_REPORTS["report"])


def stub_builder(monkeypatch, module) -> list[dict]:
    """`module.suggest_builds` returns `cli_report()`; the keyword arguments of every call are returned."""
    calls: list[dict] = []

    def stub(*args, **kw):
        calls.append(kw)
        return cli_report()

    monkeypatch.setattr(module, "suggest_builds", stub)
    return calls
