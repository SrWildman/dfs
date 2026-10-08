"""The bridge between the sheet and the simulator: PlayerSpecs, the cash line, swaps."""

import numpy as np
import pandas as pd
import pytest

from dfs import sim_inputs as si
from dfs.sim.simulate import DEFAULT_CASH_LINE


def _edge(rows):
    base = {
        "Opp": "KC",
        "Salary": 5000,
        "ProjPts": 10.0,
        "CalPts": np.nan,
        "GameID": "2026_05_DEN_KC",
        "Avail": "",
        "Tgt%": np.nan,
        "Rush%": np.nan,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def _depth(rows):
    """rows: (team, gsis, position, pos_rank)"""
    return pd.DataFrame(
        [
            {"dt": "2026-10-05T00:00:00Z", "Team": t, "GsisId": g, "Name": g, "Position": p, "pos_rank": r}
            for t, g, p, r in rows
        ]
    )


def test_calpts_is_the_projection_where_present_else_tffb():
    edge = _edge(
        [
            {"Id": 1, "Name": "Has Cal", "Position": "WR", "Team": "DEN", "ProjPts": 11.0, "CalPts": 12.5},
            {"Id": 2, "Name": "No Cal", "Position": "WR", "Team": "DEN", "ProjPts": 9.0},
            {"Id": 3, "Name": "Nothing", "Position": "WR", "Team": "DEN", "ProjPts": np.nan},
        ]
    )
    specs = si.build_specs(edge)
    assert specs["Has Cal"].projection == 12.5 and specs["No Cal"].projection == 9.0
    assert "Nothing" not in specs  # no projection at all: it cannot be simulated


def test_game_team_and_opponent_come_from_edgeraw():
    edge = _edge([{"Id": 1, "Name": "A", "Position": "QB", "Team": "DEN", "Opp": "KC"}])
    spec = si.build_specs(edge)["A"]
    assert (spec.team, spec.opp, spec.game_id, spec.salary) == ("DEN", "KC", "2026_05_DEN_KC", 5000.0)
    no_game = edge.assign(GameID=np.nan)
    assert si.build_specs(no_game)["A"].game_id == "DEN-KC"  # a stable fallback shared by both teams' players


def test_qb_is_qb1_and_a_defense_is_dst():
    edge = _edge(
        [
            {"Id": 1, "Name": "Starter", "Position": "QB", "Team": "DEN"},
            {"Id": 2, "Name": "Backup", "Position": "QB", "Team": "DEN"},
            {"Id": 3, "Name": "Broncos", "Position": "DST", "Team": "DEN"},
        ]
    )
    specs = si.build_specs(edge)
    assert (specs["Starter"].role, specs["Backup"].role, specs["Broncos"].role) == ("QB1", "QB1", "DST")


def test_roles_follow_the_depth_chart_with_usage_breaking_ties_and_folding_past_the_last_role():
    rows = [
        # RBs: the chart ranks c(1), a(2), b(2: a tie with a, broken by usage), and d is not on the chart
        ("a", "RB", 0.10, "", 2),
        ("b", "RB", 0.30, "", 2),
        ("c", "RB", 0.05, "", 1),
        ("d", "RB", 0.50, "", None),
        ("e", "RB", 0.01, "", None),
        # TEs: two, so TE3 would fold into TE2
        ("t1", "TE", 0.2, "", 1),
        ("t2", "TE", 0.1, "", 2),
        ("t3", "TE", 0.0, "", 3),
    ]
    edge = _edge(
        [
            {"Id": i, "Name": g, "Position": pos, "Team": "DEN", "Tgt%": use, "Rush%": 0.0, "Avail": avail}
            for i, (g, pos, use, avail, _r) in enumerate(rows)
        ]
    )
    gsis = {i: g for i, (g, *_rest) in enumerate(rows)}
    chart = _depth([("DEN", g, pos, r) for g, pos, _u, _a, r in rows if r is not None])
    specs = si.build_specs(edge, chart, gsis)
    assert [specs[n].role for n in ("c", "b", "a", "d", "e")] == ["RB1", "RB2", "RB3", "RB3", "RB3"]
    assert [specs[n].role for n in ("t1", "t2", "t3")] == ["TE1", "TE2", "TE2"]


def test_without_a_depth_chart_usage_alone_decides_and_a_player_who_is_out_ranks_last():
    edge = _edge(
        [
            {"Id": 1, "Name": "Big", "Position": "WR", "Team": "DEN", "Tgt%": 0.30, "Avail": "OUT"},
            {"Id": 2, "Name": "Mid", "Position": "WR", "Team": "DEN", "Tgt%": 0.20},
            {"Id": 3, "Name": "Small", "Position": "WR", "Team": "DEN", "Tgt%": 0.05},
            {"Id": 4, "Name": "Tiny", "Position": "WR", "Team": "DEN", "Tgt%": 0.01},
            {"Id": 5, "Name": "Other Team", "Position": "WR", "Team": "KC", "Tgt%": 0.01},
        ]
    )
    roles = {n: s.role for n, s in si.build_specs(edge).items()}
    assert roles == {"Mid": "WR1", "Small": "WR2", "Tiny": "WR3", "Big": "WR4", "Other Team": "WR1"}


def test_a_lineup_needs_every_slot_filled_with_a_known_distinct_player():
    edge = _edge([{"Id": i, "Name": f"P{i}", "Position": "WR", "Team": "DEN"} for i in range(3)])
    specs = si.build_specs(edge)
    assert [p.id for p in si.lineup_from_names(["P0", "P1", " P2 "], specs)] == ["P0", "P1", "P2"]
    assert si.lineup_from_names(["P0", "", "P2"], specs) is None  # a blank slot
    assert si.lineup_from_names(["P0", "Nobody", "P2"], specs) is None  # a name EdgeRaw lacks
    assert si.lineup_from_names(["P0", "P0", "P2"], specs) is None  # the same player twice


# ---- the cash line ----------------------------------------------------------------------------

HEADER = ["Week", "Cash Pts", "Cash Line", "Cash Results", "H2H Entered"]


def test_the_cash_line_is_the_median_of_the_last_three_typed_weeks():
    rows = [
        ["1", "131", "147.56"],
        ["2", "170", "140.16"],
        ["3", "166", "158.7"],
        ["4", "344", "105.98"],
        ["5"],
    ]
    line = si.cash_line_from_results(HEADER, rows, before_week=5)
    assert (
        line.weeks == (2, 3, 4) and line.value == 140.16 and not line.is_default
    )  # median of 140.16/158.7/105.98
    # the first week is not used once three later ones exist; and only weeks before the slate count
    assert si.cash_line_from_results(HEADER, rows, before_week=4).weeks == (1, 2, 3)


def test_blank_and_non_numeric_cash_lines_are_skipped_not_zeroed():
    rows = [["1", "", ""], ["2", "", "140"], ["3", "", ""], ["4", "", "n/a"], ["5", "", "$150.50"]]
    line = si.cash_line_from_results(HEADER, rows, before_week=6)
    assert line.weeks == (2, 5) and line.value == pytest.approx(145.25)  # fewer than three: the median of two
    assert si.cash_line_from_results(HEADER, [["1", "", "100"], ["2", "", "120"]], before_week=3).value == 110


def test_with_no_typed_line_the_placeholder_is_used_and_flagged():
    line = si.cash_line_from_results(HEADER, [["1", "", ""], ["2"]], before_week=3)
    assert line.value == DEFAULT_CASH_LINE and line.is_default and "placeholder" in line.note
    broken = si.cash_line_from_results(["Week", "Cash Pts"], [["1", "100"]], before_week=3)
    assert broken.is_default  # no Cash Line column at all
    renamed = ["Wk", "Cash Line", "Week"]  # found by header name, wherever the columns sit
    assert (
        si.cash_line_from_results(renamed, [["x", "130", "1"], ["y", "150", "2"]], before_week=3).value == 140
    )


# ---- swaps ------------------------------------------------------------------------------------


def _roster():
    rows = [
        ("QB", "QB1", "DEN", "KC", 20.0),
        ("RB", "RB1", "DEN", "KC", 15.0),
        ("WR", "WR1", "DEN", "KC", 16.0),
        ("WR", "WR2", "DEN", "KC", 12.0),
        ("TE", "TE1", "KC", "DEN", 9.0),
        ("DST", "DST", "KC", "DEN", 7.0),
    ]
    from dfs.sim.simulate import PlayerSpec

    return [PlayerSpec(f"{r}-{t}", pos, t, o, "G1", r, p, 5000) for pos, r, t, o, p in rows]


def test_a_swap_to_a_better_player_raises_p_cash_and_the_lineup_alone_is_unchanged():
    from dfs.sim.simulate import PlayerSpec

    lineup = _roster()
    better = PlayerSpec("WR-star", "WR", "DEN", "KC", "G1", "WR2", 20.0, 9000)
    deltas = si.swap_deltas(lineup, [(["WR2-DEN"], [better])], cash_line=60.0, gpp_target=80.0)
    assert len(deltas) == 1 and deltas[0].delta_p_cash > 0 and deltas[0].delta_p_gpp >= 0
    assert si.swap_deltas(lineup, [], cash_line=60.0, gpp_target=80.0) == []


def test_swap_deltas_are_deterministic_under_the_seed_and_handle_two_for_two():
    from dfs.sim.simulate import PlayerSpec

    lineup = _roster()
    a = PlayerSpec("a", "WR", "DEN", "KC", "G1", "WR3", 18.0, 7000)
    b = PlayerSpec("b", "TE", "DEN", "KC", "G1", "TE2", 12.0, 5000)
    swaps = [(["WR2-DEN"], [a]), (["WR2-DEN", "TE1-KC"], [a, b])]
    first = si.swap_deltas(lineup, swaps, cash_line=60.0, gpp_target=80.0)
    again = si.swap_deltas(lineup, swaps, cash_line=60.0, gpp_target=80.0)
    assert first == again
    assert len(first) == 2 and first[1].p_cash != first[0].p_cash
    other_seed = si.swap_deltas(lineup, swaps, cash_line=60.0, gpp_target=80.0, seed=7)
    assert other_seed != first  # the seed is what fixes the numbers


def test_the_sheets_draw_count_and_seed_are_the_agreed_constants():
    from dfs import sheet_lineup_sim

    assert si.N_SIMS == sheet_lineup_sim.N_SIMS == 20000
    assert si.SEED == sheet_lineup_sim.SEED == 0
