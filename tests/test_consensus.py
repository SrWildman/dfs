"""The consensus lineup (highest total ownership under DraftKings' rules) and copies of Sam's lineups."""

from __future__ import annotations

from itertools import combinations, product

import pandas as pd

from dfs import consensus as cons
from dfs.sim import dups
from dfs.sim.field_gen import SALARY_CAP

# Two games (A@B, C@D): the QBs play in the first, the DSTs in the second, so every lineup spans two games.
PLAYERS = [
    ("Q1", "QB", "A", "B", 7000, 0.50),
    ("Q2", "QB", "B", "A", 6000, 0.10),
    ("R1", "RB", "A", "B", 9000, 0.60),
    ("R2", "RB", "C", "D", 7000, 0.50),
    ("R3", "RB", "D", "C", 5000, 0.10),
    ("R4", "RB", "B", "A", 4000, 0.05),
    ("W1", "WR", "A", "B", 9500, 0.70),
    ("W2", "WR", "C", "D", 6000, 0.40),
    ("W3", "WR", "D", "C", 5000, 0.30),
    ("W4", "WR", "B", "A", 4000, 0.20),
    ("W5", "WR", "C", "D", 3000, 0.05),
    ("T1", "TE", "C", "D", 5000, 0.30),
    ("T2", "TE", "A", "B", 3000, 0.10),
    ("D1", "DST", "C", "D", 3000, 0.20),
    ("D2", "DST", "D", "C", 2500, 0.10),
]


def frame() -> pd.DataFrame:
    return pd.DataFrame(PLAYERS, columns=["name", "position", "team", "opp", "salary", "CashOwn"])


def brute_force_best() -> tuple[float, frozenset]:
    by = {p[0]: p for p in PLAYERS}
    pos = {k: [p[0] for p in PLAYERS if p[1] == k] for k in ("QB", "RB", "WR", "TE", "DST")}
    best = (-1.0, frozenset())
    for qb, rbs, wrs, te, dst in product(
        pos["QB"], combinations(pos["RB"], 2), combinations(pos["WR"], 3), pos["TE"], pos["DST"]
    ):
        core = {qb, *rbs, *wrs, te, dst}
        for flex in [n for n in pos["RB"] + pos["WR"] + pos["TE"] if n not in core]:
            names = core | {flex}
            if sum(by[n][4] for n in names) <= SALARY_CAP:
                own = sum(by[n][5] for n in names)
                if own > best[0] + 1e-12:
                    best = (own, frozenset(names))
    return best


def test_the_consensus_is_the_highest_total_ownership_legal_lineup():
    names = cons.consensus_from_frame(frame(), "CashOwn")
    by = {p[0]: p for p in PLAYERS}
    assert len(names) == 9
    assert sum(by[n][4] for n in names) <= SALARY_CAP
    assert sorted(by[n][1] for n in names).count("QB") == 1
    own, best = brute_force_best()
    assert frozenset(names) == best
    assert abs(sum(by[n][5] for n in names) - own) < 1e-9


def test_no_ownership_means_no_consensus():
    assert cons.consensus_from_frame(frame().assign(CashOwn=None), "CashOwn") is None


def edge(**avail) -> list[dict]:
    return [
        {
            "Name": n,
            "Position": p,
            "Team": t,
            "Opp": o,
            "Salary": s,
            "CashOwn": own,
            "GppOwn": own / 2,
            "Avail": avail.get(n, ""),
        }
        for n, p, t, o, s, own in PLAYERS
    ]


def test_out_and_doubtful_players_are_left_out_of_the_field_lineup():
    v = cons.view(edge(W1="OUT", R1="D"), [], models={})
    assert "W1" not in v["consensus"]["cash"]["names"]
    assert "R1" not in v["consensus"]["cash"]["names"]


def test_a_gpp_lineup_at_five_copies_is_flagged_and_a_cash_one_never_is():
    best = sorted(brute_force_best()[1])
    lineups = [
        cons.Lineup("Cash 1", "cash", tuple(best)),
        cons.Lineup("GPP 2", "gpp", tuple(best)),
        cons.Lineup("GPP 3", "gpp", ("Q2", "R3", "R4", "W4", "W5", "W3", "T2", "R2", "D2")),
    ]
    heavy = dups.DupModel(scale=1e6, exponent=0.5)
    v = cons.view(edge(), lineups, models={"cash": heavy, "gpp": dups.DupModel(scale=200.0, exponent=0.5)})
    rows = {r["label"]: r for r in v["lineups"]}
    assert rows["Cash 1"]["shared"]["cash"] == 9
    assert rows["Cash 1"]["contests"][0]["flag"] is False  # close to the field is fine in cash
    assert rows["GPP 2"]["contests"][0]["flag"] is True
    assert rows["GPP 3"]["contests"][0]["flag"] is False
    assert rows["GPP 3"]["contests"][0]["copies"] < rows["GPP 2"]["contests"][0]["copies"]


def test_an_entered_contest_uses_its_series_size_and_kind():
    lineup = cons.Lineup("#1", "", ("Q1",), (("NFL $5 Double Up", "Double Up"), ("NFL $3 Mini", "SE GPP")))
    v = cons.view(edge(), [lineup], models={"cash": None, "gpp": None}, typical={"cash": 500.0, "gpp": 900.0})
    first, second = v["lineups"][0]["contests"]
    assert (first["kind"], first["entrants"], first["copies"]) == ("cash", 500, None)
    assert (second["kind"], second["entrants"]) == ("gpp", 900)


def test_pending_until_ownership_is_out():
    rows = [{**r, "CashOwn": "", "GppOwn": ""} for r in edge()]
    assert cons.view(rows, [], models={})["pending"] is True
    assert cons.view([], [], models={})["pending"] is True


def test_the_solver_takes_only_the_two_objectives():
    import pytest

    from dfs.sim import field_gen

    slate = field_gen.Slate.from_frame(frame(), "CashOwn")
    assert len(field_gen.chalk_lineups(slate, k=1, objective="product")) == 1
    with pytest.raises(ValueError):
        field_gen.chalk_lineups(slate, k=1, objective="max")
