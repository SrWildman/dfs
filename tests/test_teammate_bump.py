"""The teammate bump shadow: the table that ships, the per-teammate function, the scored-table column and
the Model Check row. Synthetic frames; no nflverse cache."""

import pandas as pd

from dfs import calibration as cal
from dfs import teammate_bump as tb

TABLE = {("WR1", "WR2"): 1.5, ("WR1", "QB1"): -1.0, ("RB1", "RB2"): 4.0}


def roles():
    rows = [
        # (team, week, gsis, name, position, label, regular)
        ("KC", 5, "k-wr1", "Kelce Wide", "WR", "WR1", True),
        ("KC", 5, "k-wr2", "Second Wide", "WR", "WR2", False),
        ("KC", 5, "k-qb", "Quarter Back", "QB", "QB1", False),
        ("KC", 5, "k-rb1", "Run One", "RB", "RB1", True),
        ("KC", 5, "k-rb2", "Run Two", "RB", "RB2", False),
        ("DEN", 5, "d-wr1", "Den Wide", "WR", "WR1", True),
        ("DEN", 5, "d-wr2", "Den Second", "WR", "WR2", False),
    ]
    return pd.DataFrame(
        rows, columns=["team", "week", "gsis_id", "name", "position", "label", "regular"]
    ).assign(season=2026)


def scored(out=("Kelce Wide",), extra_out=()):
    rows = []
    for name, team, pos, gsis, avail in [
        ("Kelce Wide", "KC", "WR", "", "OUT"),
        ("Second Wide", "KC", "WR", "k-wr2", None),
        ("Quarter Back", "KC", "QB", "k-qb", None),
        ("Run One", "KC", "RB", "k-rb1", None),
        ("Run Two", "KC", "RB", "k-rb2", None),
        ("Den Wide", "DEN", "WR", "d-wr1", None),
        ("Den Second", "DEN", "WR", "d-wr2", None),
    ]:
        listed = avail if name in out or name in extra_out else None
        rows.append({"week": 5, "Name": name, "Team": team, "Position": pos, "GsisId": gsis, "Avail": listed})
    return pd.DataFrame(rows)


def frame():
    f = scored(out=()).query("Avail != 'OUT'").copy()
    f = f[f["Name"] != "Kelce Wide"]
    f["CalPts"] = 10.0
    return f.reset_index(drop=True)


def test_the_committed_table_ships_only_the_asked_for_absent_roles_and_nonzero_cells():
    table = tb.load_table()
    assert table, "models/research/teammate_bump.csv is missing"
    assert {a for a, _ in table} <= set(tb.SHIPPED_ABSENT)
    assert all(v != 0 for v in table.values())
    assert table[("RB1", "RB2")] > 2  # the one big, stable cell
    assert tb.load_table(tb.TABLE_PATH.with_name("missing.csv")) == {}


def test_teammate_bump_gives_each_teammate_the_cell_for_his_role():
    team = pd.DataFrame({"label": ["WR2", "QB1", "TE1"], "Avail": [None, None, None]})
    got = tb.teammate_bump({"label": "WR1"}, team, TABLE)
    assert got.tolist() == [1.5, -1.0, 0.0]  # TE1 has no cell
    assert tb.teammate_bump({"label": "TE2+"}, team, TABLE).tolist() == [0.0, 0.0, 0.0]


def test_a_teammate_who_is_himself_out_gets_nothing():
    team = pd.DataFrame({"label": ["WR2", "WR2"], "Avail": ["OUT", None]}, index=[7, 9])
    got = tb.teammate_bump({"label": "WR1"}, team, TABLE)
    assert got.to_dict() == {7: 0.0, 9: 1.5}


def test_the_absent_player_is_found_by_name_and_only_when_he_is_the_one_regular_out():
    assert tb.absent_by_team_week(scored(), roles()).to_dict() == {(5, "KC"): "WR1"}
    two = scored()
    two.loc[two["Name"] == "Run One", "Avail"] = "IR"
    assert tb.absent_by_team_week(two, roles()).empty  # two regulars out: left out, as in the research
    bench = scored(out=())
    bench.loc[bench["Name"] == "Second Wide", "Avail"] = "OUT"
    assert tb.absent_by_team_week(bench, roles()).empty  # not a regular


def test_with_teammate_bump_adds_the_cell_to_calpts_for_the_teammates_of_the_one_out():
    out = tb.with_teammate_bump(frame(), scored(), roles(), TABLE)
    by = out.set_index("Name")[tb.SOURCE]
    assert by["Second Wide"] == 11.5
    assert by["Quarter Back"] == 9.0
    assert by[["Run One", "Run Two", "Den Wide", "Den Second"]].isna().all()  # no cell, or another team


def test_with_teammate_bump_is_blank_without_a_table_or_roles_or_an_absence():
    assert tb.with_teammate_bump(frame(), scored(), roles(), {})[tb.SOURCE].isna().all()
    assert tb.with_teammate_bump(frame(), scored(out=()), roles(), TABLE)[tb.SOURCE].isna().all()
    assert tb.with_teammate_bump(frame().iloc[0:0], scored(), roles(), TABLE).empty


def test_the_race_is_empty_without_roles_so_old_scored_files_still_work():
    assert cal.teammate_bump_race(pd.DataFrame({"week": [1], "season": [2026]}), None).empty
    assert cal.teammate_bump_race(pd.DataFrame({"week": [1], "season": [2026]}), roles()).empty
