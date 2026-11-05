"""The Sunday inactives check and its pivots (Plan K item 2): slot, salary and kickoff rules."""

from __future__ import annotations

from datetime import UTC, datetime

from dfs import inactives as ina

NOW = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)  # 1:30 pm ET: the 1:00 games have started
EARLY, LATE = "2026-10-25T13:00:00Z", "2026-10-25T16:25:00Z"  # TFFB's Eastern wall time with a "Z"

# slot order: QB RB RB WR WR WR TE FLEX DST; salaries add up to 48,000 (2,000 left)
LINEUP = [
    ("Qb One", "QB", 7000, EARLY),
    ("Rb One", "RB", 6000, LATE),
    ("Rb Two", "RB", 5000, EARLY),
    ("Wr One", "WR", 6000, LATE),
    ("Wr Two", "WR", 5000, LATE),
    ("Wr Three", "WR", 4000, EARLY),
    ("Te One", "TE", 4000, LATE),
    ("Flex Rb", "RB", 8000, LATE),
    ("Home DST", "DST", 3000, LATE),
]


def row(name, pos, salary, start, avail="", calpts=10.0, leverage=None) -> dict:
    return {
        "Name": name,
        "Position": pos,
        "Team": "GB",
        "Salary": salary,
        "GameStart": start,
        "Avail": avail,
        "CalPts": calpts,
        "Leverage": "" if leverage is None else leverage,
    }


def slate(**status) -> list[dict]:
    rows = [row(n, p, s, k, status.get(n, "")) for n, p, s, k in LINEUP]
    rows += [
        row("Wr Cheap", "WR", 5500, LATE, calpts=9.0, leverage=20),
        row("Wr Exact", "WR", 8000, LATE, calpts=15.0, leverage=-5),  # exactly freed (6,000) + left (2,000)
        row("Wr Over", "WR", 8100, LATE, calpts=30.0, leverage=50),  # $100 over
        row("Wr Started", "WR", 5000, EARLY, calpts=25.0, leverage=40),  # his game is on
        row("Wr Hurt", "WR", 5000, LATE, avail="Q", calpts=24.0, leverage=30),
        row("Te Late", "TE", 5000, LATE, calpts=40.0, leverage=60),  # never a WR pivot
        row("Rb Late", "RB", 7000, LATE, calpts=14.0, leverage=10),
    ]
    return rows


def source(kind="cash") -> ina.Source:
    return ina.Source("Cash build 1", kind, tuple(n for n, *_ in LINEUP))


def test_pivots_fit_the_slot_cost_at_most_freed_plus_left_and_have_not_started():
    [lc] = ina.check(slate(**{"Wr One": "OUT"}), [source()], NOW)
    assert lc.left == 2000
    [flag] = lc.flags
    assert (flag.name, flag.slot, flag.status, flag.budget) == ("Wr One", "WR", "OUT", 8000)
    names = [p.name for p in flag.pivots]
    assert names == ["Wr Exact", "Wr Cheap"]  # by CalPts; not Over ($100 over), Started, Hurt or the TE
    assert flag.ranked_by == "CalPts"


def test_a_gpp_lineup_ranks_its_pivots_by_leverage():
    [lc] = ina.check(slate(**{"Wr One": "D"}), [source("gpp")], NOW)
    [flag] = lc.flags
    assert flag.ranked_by == "Leverage"
    assert [p.name for p in flag.pivots] == ["Wr Cheap", "Wr Exact"]


def test_before_ownership_a_gpp_lineup_falls_back_to_calpts_and_says_so():
    rows = slate(**{"Wr One": "Q"})
    for r in rows:
        r["Leverage"] = ""
    [flag] = ina.check(rows, [source("gpp")], NOW)[0].flags
    assert flag.ranked_by == "CalPts (Leverage is not out yet)"
    assert flag.pivots[0].name == "Wr Exact"


def test_the_flex_slot_takes_any_rb_wr_te():
    [flag] = ina.check(slate(**{"Flex Rb": "OUT"}), [source()], NOW)[0].flags
    assert flag.slot == "FLEX" and flag.budget == 10000
    assert [p.name for p in flag.pivots] == ["Te Late", "Wr Over", "Wr Exact"]  # $8,100 fits $10,000


def test_a_flagged_player_whose_game_started_is_not_checked():
    assert ina.check(slate(**{"Rb Two": "OUT"}), [source()], NOW) == []


def test_worst_status_first_and_a_clean_lineup_is_left_out():
    found = ina.check(slate(**{"Wr Two": "Q", "Te One": "OUT"}), [source(), ina.Source("x", "cash", ())], NOW)
    assert len(found) == 1
    assert [f.status for f in found[0].flags] == ["OUT", "Q"]


def test_the_kind_comes_from_the_build_then_the_contests_then_the_marker():
    record = {
        "lineups": [
            {"names": ["a"], "source": {"kind": "gpp", "number": 3}, "contests": [{"format": "Double Up"}]},
            {
                "names": ["b"],
                "contests": [{"name": "c", "format": "Double Up"}, {"name": "d", "format": "SE GPP"}],
            },
            {"names": ["c"], "contests": [{"name": "c", "format": "H2H"}]},
        ]
    }
    kinds = [s.kind for s in ina.sources_from_entered(record)]
    assert kinds == ["gpp", "gpp", "cash"]
    assert [s.label for s in ina.sources_from_entered(record)][0] == "Gpp build 3"
    sheet = ina.sources_from_sheet(
        [
            {"label": "L1", "marker": "GPP", "names": ["a"]},
            {"label": "L2", "marker": "", "names": ["b"]},
            {"label": "L3", "marker": "Cash", "names": ["", ""]},
        ]  # fmt: skip
    )
    assert [(s.label, s.kind) for s in sheet] == [("L1", "gpp"), ("L2", "cash")]


def test_an_unknown_salary_still_lists_pivots_up_to_his_own_salary():
    rows = [r for r in slate(**{"Wr One": "OUT"}) if r["Name"] != "Te One"]  # one player not on the slate
    [flag] = ina.check(rows, [source()], NOW)[0].flags
    assert flag.budget == 6000
    assert [p.name for p in flag.pivots] == ["Wr Cheap"]
