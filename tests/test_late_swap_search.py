import random
import time

import pytest

from dfs.late_swap_report import format_suggestions
from dfs.late_swap_search import (
    LineupState,
    OpenSlot,
    SwapPlayer,
    _fits,
    best_refill,
    conflicts,
    correlation_notes,
    one_for_one,
    suggest,
    two_for_two,
)
from dfs.models import ROSTER_SLOTS

CAP = 50000
IDX = {"QB": 0, "RB1": 1, "RB2": 2, "WR1": 3, "WR2": 4, "WR3": 5, "TE": 6, "FLEX": 7, "DST": 8}
_SLOT_NAME = {k: ROSTER_SLOTS[v] for k, v in IDX.items()}


def P(name, pos, team, opp, game, salary, value, avail=""):
    return SwapPlayer(name, pos, team, opp, game, salary, value, avail)


def state_of(fixed: dict[str, SwapPlayer], open_: dict[str, SwapPlayer | None], cap=CAP) -> LineupState:
    return LineupState(
        cap=cap,
        fixed={IDX[k]: v for k, v in fixed.items()},
        open=[OpenSlot(IDX[k], _SLOT_NAME[k], v) for k, v in open_.items()],
    )


def locked_core(**override):
    """QB, RB2, three WR, FLEX and DST locked, each from its own game, cheap enough to leave room."""
    core = {
        "QB": P("Locked QB", "QB", "QQ", "Q2", "g_q", 6000, 20),
        "RB2": P("Locked RB2", "RB", "RR", "R2", "g_r", 5000, 12),
        "WR1": P("Locked WR1", "WR", "W1", "X1", "g_w1", 6000, 14),
        "WR2": P("Locked WR2", "WR", "W2", "X2", "g_w2", 5500, 13),
        "WR3": P("Locked WR3", "WR", "W3", "X3", "g_w3", 5000, 12),
        "FLEX": P("Locked Flex", "WR", "W4", "X4", "g_w4", 4500, 11),
        "DST": P("Locked DST", "DST", "DD", "D2", "g_d", 3000, 8),
    }
    core.update(override)
    return core  # salary so far: 35,000


# ---------------------------------------------------------------------------------------------
# Sam's note: an expensive RB + a cheap TE out, a mid RB + a better TE in
# ---------------------------------------------------------------------------------------------


def sams_note_state():
    big_rb = P("Big RB", "RB", "BB", "B2", "g_b", 9000, 15.0)
    cheap_te = P("Cheap TE", "TE", "CT", "C2", "g_c", 2500, 4.0)
    # A pricey locked QB leaves only $1,000 spare: 37,500 locked + 9,000 + 2,500 = 49,000.
    locked = locked_core(QB=P("Locked QB", "QB", "QQ", "Q2", "g_q", 8500, 20))
    return state_of(locked, {"RB1": big_rb, "TE": cheap_te}), big_rb, cheap_te


def sams_note_pool():
    return [
        P("Mid RB", "RB", "MR", "M2", "g_m", 6500, 14.0),  # 1 pt worse than Big RB, 2,500 cheaper
        P("Better TE", "TE", "BT", "T2", "g_t", 5000, 12.0),  # 8 pts better than Cheap TE, 2,500 dearer
        P("Slightly Better TE", "TE", "ST", "S2", "g_s", 2600, 5.0),  # the only 1-for-1 that helps (+1)
    ]


def test_the_two_for_two_beats_every_one_for_one_and_is_found():
    state, _big_rb, _cheap_te = sams_note_state()
    pool = sams_note_pool()
    # $1,000 spare: the better TE (+2,500 dearer) does not fit on its own, the cheaper mid RB scores less.
    singles = one_for_one(state, pool)
    best_single = max(s.delta for swaps in singles.values() for s in swaps)
    assert best_single == pytest.approx(1.0)  # only the +100 salary TE fits, and it is worth just 1 point

    pairs = two_for_two(state, pool)
    assert pairs, "the 2-for-2 should be found"
    top = pairs[0]
    assert {i.name for _, i in top.changes.values()} == {"Mid RB", "Better TE"}
    assert {o.name for o, _ in top.changes.values()} == {"Big RB", "Cheap TE"}
    assert top.delta == pytest.approx(-1.0 + 8.0)
    assert top.delta > best_single
    assert top.salary_left == 1000  # 9,000 + 2,500 out, 6,500 + 5,000 in: the same 11,500


def test_the_full_refill_also_finds_it_and_the_report_lists_it_first():
    state, *_ = sams_note_state()
    suggestions = suggest(state, sams_note_pool())
    assert suggestions.refill.delta == pytest.approx(7.0)
    lines = format_suggestions(suggestions, metric="ProjPts")
    text = "\n".join(lines)
    assert text.index("a. Best full re-fill") < text.index("b. Best 2-for-2") < text.index("c. Best 1-for-1")
    assert "Big RB" in text and "Mid RB" in text and "+7.0 pts" in text


# ---------------------------------------------------------------------------------------------
# The constraints
# ---------------------------------------------------------------------------------------------


def test_the_salary_cap_counts_the_locked_players():
    state = state_of(locked_core(), {"RB1": P("Old RB", "RB", "OO", "O2", "g_o", 6000, 10), "TE": None})
    # 35,000 locked leaves 15,000 for the RB and the empty TE slot.
    pool = [
        P("Star RB", "RB", "SR", "S2", "g_s", 12000, 30),
        P("Pricey TE", "TE", "PT", "T2", "g_t", 9000, 15),
        P("Fair TE", "TE", "FT", "T3", "g_f", 4000, 9),
    ]
    refill = best_refill(state, [*pool, state.open[0].current])
    # Star RB + Fair TE = 16,000 does not fit. Old RB + Pricey TE = 15,000 does, and is the best that fits.
    assert {i.name for _, i in refill.changes.values()} == {"Pricey TE"}
    assert refill.delta == pytest.approx(15.0) and refill.salary_left == 0
    for swaps in one_for_one(state, pool).values():
        for swap in swaps:
            assert swap.salary_left >= 0


def test_flex_takes_an_rb_wr_or_te_and_never_a_qb_or_dst():
    fixed = locked_core()
    del fixed["FLEX"]
    state = state_of(fixed, {"FLEX": P("Old Flex", "WR", "OF", "O2", "g_of", 4500, 10)})
    pool = [
        P("Flex TE", "TE", "FT", "F2", "g_ft", 4500, 13),
        P("Flex RB", "RB", "FR", "F3", "g_fr", 4500, 12),
        P("Big QB", "QB", "BQ", "B2", "g_bq", 4500, 40),
        P("Big DST", "DST", "BD", "B3", "g_bd", 4500, 40),
    ]
    singles = one_for_one(state, pool)[IDX["FLEX"]]
    assert [s.changes[IDX["FLEX"]][1].name for s in singles] == ["Flex TE", "Flex RB"]
    assert _fits("FLEX", pool[0]) and _fits("FLEX", pool[1])
    assert not _fits("FLEX", pool[2]) and not _fits("FLEX", pool[3])


def test_locked_players_never_move():
    state, *_ = sams_note_state()
    pool = sams_note_pool() + [P("Better QB", "QB", "BQ", "B2", "g_bq", 6000, 40)]
    refill = best_refill(state, [*pool, *(s.current for s in state.open)])
    assert set(refill.changes) <= {s.index for s in state.open}
    for swaps in one_for_one(state, pool).values():
        assert all(set(s.changes) <= {IDX["RB1"], IDX["TE"]} for s in swaps)
    for swap in two_for_two(state, pool):
        assert set(swap.changes) <= {IDX["RB1"], IDX["TE"]}


def test_no_dst_against_your_own_qb():
    # The locked QB plays for QQ against Q2. A DST for Q2 faces the QB: better score, but refused.
    state = state_of(
        {k: v for k, v in locked_core().items() if k != "DST"},
        {"DST": P("Old DST", "DST", "OD", "O2", "g_od", 3000, 6), "RB1": None, "TE": None},
    )
    facing = P("Facing DST", "DST", "Q2", "QQ", "g_q", 3000, 15)
    safe = P("Safe DST", "DST", "SD", "S2", "g_sd", 3000, 8)
    rb = P("RB In", "RB", "RI", "R3", "g_ri", 5000, 10)
    te = P("TE In", "TE", "TI", "T3", "g_ti", 3000, 7)
    pool = [facing, safe, rb, te]
    assert conflicts(facing, [state.fixed[IDX["QB"]]])
    singles = one_for_one(state, pool)[IDX["DST"]]
    assert [s.changes[IDX["DST"]][1].name for s in singles] == ["Safe DST"]
    refill = best_refill(state, [*pool, state.open[0].current])
    assert refill.changes[IDX["DST"]][1].name == "Safe DST"


def test_at_most_one_rb_per_game():
    # The locked RB2 plays in g_r. A second RB from g_r is refused in every search.
    state = state_of(
        {k: v for k, v in locked_core().items() if k != "FLEX"},
        {"RB1": P("Old RB", "RB", "OO", "O2", "g_o", 5000, 8), "FLEX": None, "TE": None},
    )
    same_game = P("Same Game RB", "RB", "R2", "RR", "g_r", 5000, 25)  # the locked RB's opponent, same game
    other = P("Other RB", "RB", "OR", "O3", "g_or", 5000, 12)
    pool = [same_game, other, P("TE In", "TE", "TI", "T3", "g_ti", 3000, 7)]
    assert conflicts(same_game, [state.fixed[IDX["RB2"]]])
    singles = one_for_one(state, pool)[IDX["RB1"]]
    assert [s.changes[IDX["RB1"]][1].name for s in singles] == ["Other RB"]
    refill = best_refill(state, [*pool, state.open[0].current])
    assert "Same Game RB" not in {i.name for _, i in refill.changes.values()}


def test_a_flex_rb_counts_toward_the_one_rb_per_game_rule():
    in_game = P("RB One", "RB", "AA", "BB", "g_ab", 5000, 10)
    flex_rb = P("RB Two", "RB", "BB", "AA", "g_ab", 5000, 14)  # would go in FLEX, same game as RB One
    assert conflicts(flex_rb, [in_game])
    assert not conflicts(P("WR In Game", "WR", "BB", "AA", "g_ab", 5000, 14), [in_game])


def test_two_swaps_that_differ_only_by_interchangeable_slots_count_once():
    state = state_of(
        {k: v for k, v in locked_core().items() if k != "RB2"},
        {
            "RB1": P("Old RB One", "RB", "O1", "X1", "g_o1", 4000, 5),
            "RB2": P("Old RB Two", "RB", "O2", "X2", "g_o2", 4000, 6),
        },
    )
    pool = [
        P("New RB A", "RB", "NA", "XA", "g_na", 4500, 14),
        P("New RB B", "RB", "NB", "XB", "g_nb", 4500, 13),
    ]
    pairs = two_for_two(state, pool)
    assert len(pairs) == 1  # (A in slot 1, B in slot 2) and (B in slot 1, A in slot 2) are one lineup


def test_nothing_better_is_said_when_nothing_beats_the_lineup():
    state, *_ = sams_note_state()
    suggestions = suggest(state, [P("Worse RB", "RB", "WR_", "W2_", "g_wr", 6000, 3)])
    assert suggestions.nothing_better
    assert "Nothing beats this lineup on ProjPts" in "\n".join(
        format_suggestions(suggestions, metric="ProjPts")
    )


def test_an_empty_slot_is_filled_by_the_best_affordable_player():
    fixed = locked_core()
    del fixed["WR1"]
    state = state_of(fixed, {"WR1": None, "RB1": None, "TE": None})
    # 29,000 locked leaves 21,000 for RB + WR + TE.
    pool = [
        P("RB", "RB", "R_", "R2_", "g_rb", 7000, 15),
        P("WR", "WR", "W_", "W2_", "g_wr", 7000, 16),
        P("TE", "TE", "T_", "T2_", "g_te", 7000, 12),
        P("Cheap TE", "TE", "C_", "C2_", "g_ct", 3500, 7),
    ]
    refill = best_refill(state, pool)
    assert {i.name for _, i in refill.changes.values()} == {"RB", "WR", "TE"}
    assert refill.delta == pytest.approx(43.0)


# ---------------------------------------------------------------------------------------------
# The search is exact: compare with brute force on random small slates
# ---------------------------------------------------------------------------------------------


def _brute_best(state, candidates):
    slots = [s.slot for s in state.open]
    fixed = list(state.fixed.values())
    budget = state.cap - state.fixed_salary
    best = [None]

    def rec(i, chosen, used, spent):
        if i == len(slots):
            value = sum(p.value for p in chosen)
            if best[0] is None or value > best[0]:
                best[0] = value
            return
        for p in candidates:
            if p.name in used or not _fits(slots[i], p) or spent + p.salary > budget:
                continue
            if conflicts(p, fixed + chosen):
                continue
            rec(i + 1, [*chosen, p], used | {p.name}, spent + p.salary)

    rec(0, [], {p.name for p in fixed}, 0)
    return best[0]


def _random_slate(rng):
    """A small slate on 4 games (8 teams): collisions are common, so the DST/QB and RB/game rules bite."""
    teams = [f"T{i}" for i in range(8)]
    opp = {teams[2 * g]: teams[2 * g + 1] for g in range(4)} | {
        teams[2 * g + 1]: teams[2 * g] for g in range(4)
    }
    game = {t: f"G{teams.index(t) // 2}" for t in teams}
    counts = {"QB": 3, "RB": 5, "WR": 6, "TE": 3, "DST": 3}
    players = []
    for pos, n in counts.items():
        for i in range(n):
            team = rng.choice(teams)
            players.append(
                P(
                    f"{pos}{i}",
                    pos,
                    team,
                    opp[team],
                    game[team],
                    rng.randrange(30, 85) * 100,
                    round(rng.uniform(3, 25), 1),
                )
            )
    return players


@pytest.mark.parametrize("seed", range(12))
def test_best_refill_matches_brute_force(seed):
    rng = random.Random(seed)
    players = _random_slate(rng)
    by_pos = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)
    # Lock a random subset of the nine slots to players from the slate; the rest are open.
    slot_keys = list(IDX)
    rng.shuffle(slot_keys)
    open_keys = set(slot_keys[: rng.randrange(3, 7)])
    used = set()
    fixed, open_ = {}, {}
    for key in IDX:
        pos = "FLEX" if key == "FLEX" else key.rstrip("123")
        if key in open_keys:
            open_[key] = None
            continue
        options = [p for p in players if p.name not in used and _fits(pos, p)]
        pick = rng.choice(options)
        used.add(pick.name)
        fixed[key] = pick
    state = state_of(fixed, open_)
    if state.fixed_salary > CAP:
        pytest.skip("locked part alone is over the cap")
    candidates = [p for p in players if p.name not in used]
    expected = _brute_best(state, candidates)
    got = best_refill(state, candidates)
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert got.delta == pytest.approx(expected)  # nothing is open-and-filled, so delta == total value
        assert got.salary_left >= 0


# ---------------------------------------------------------------------------------------------
# Time budget: 9 open slots against the full rosterable pool
# ---------------------------------------------------------------------------------------------


def _full_pool(rng):
    """A slate the size of the rosterable pool (32 QB, 64 RB, 96 WR, 32 TE, 32 DST), salaries in $100s, points
    roughly proportional to salary plus noise: about as hard for the search as a real slate (a few tenths of a
    second here, ~0.6 s on the real Week 5 pool)."""
    teams = [f"T{i}" for i in range(32)]
    opp = {teams[2 * g]: teams[2 * g + 1] for g in range(16)} | {
        teams[2 * g + 1]: teams[2 * g] for g in range(16)
    }
    game = {t: f"G{teams.index(t) // 2}" for t in teams}
    players = []
    for pos, n, lo, hi in (("QB", 32, 5000, 8000), ("RB", 64, 3000, 9000), ("WR", 96, 3000, 9500),
                           ("TE", 32, 2800, 7000), ("DST", 32, 2200, 4200)):  # fmt: skip
        for i in range(n):
            team = teams[(i * 7 + len(players)) % 32]
            salary = rng.randrange(lo // 100, hi // 100) * 100
            players.append(
                P(
                    f"{pos}{i}",
                    pos,
                    team,
                    opp[team],
                    game[team],
                    salary,
                    round(max(0.5, salary / 1000 * 1.6 + rng.gauss(0, 2.5)), 1),
                )
            )
    return players


def test_a_full_refill_with_nine_open_slots_finishes_inside_two_seconds():
    pool = _full_pool(random.Random(0))
    state = state_of({}, {k: None for k in IDX})
    started = time.perf_counter()
    refill = best_refill(state, pool)
    elapsed = time.perf_counter() - started
    assert refill is not None and len(refill.changes) == 9
    assert refill.salary_left >= 0
    assert elapsed < 2.0, f"9-slot re-fill took {elapsed:.2f}s"


def test_the_whole_suggest_pass_for_nine_open_slots_stays_fast_too():
    pool = _full_pool(random.Random(6))
    by_pos = {pos: [p for p in pool if p.position == pos] for pos in ("QB", "RB", "WR", "TE", "DST")}
    current = {
        "QB": by_pos["QB"][0],
        "RB1": by_pos["RB"][0],
        "RB2": by_pos["RB"][5],
        "WR1": by_pos["WR"][0],
        "WR2": by_pos["WR"][7],
        "WR3": by_pos["WR"][9],
        "TE": by_pos["TE"][0],
        "FLEX": by_pos["WR"][11],
        "DST": by_pos["DST"][0],
    }
    state = state_of({}, current)
    started = time.perf_counter()
    result = suggest(state, pool)
    assert time.perf_counter() - started < 6.0  # three searches, nine slots, full pool
    assert result.refill is not None


# ---------------------------------------------------------------------------------------------
# Correlation notes
# ---------------------------------------------------------------------------------------------


def test_correlation_notes_name_a_new_stack_bring_back_and_dst_pair():
    qb = P("QB", "QB", "AAA", "BBB", "g_ab", 6000, 20)
    dst = P("DST", "DST", "CCC", "DDD", "g_cd", 3000, 8)
    base = [qb, dst, P("Other WR", "WR", "EEE", "FFF", "g_ef", 5000, 10)]
    teammate = P("Teammate WR", "WR", "AAA", "BBB", "g_ab", 5000, 12)
    opposing = P("Opposing WR", "WR", "BBB", "AAA", "g_ab", 5000, 12)
    own_rb = P("DST's RB", "RB", "CCC", "DDD", "g_cd", 5000, 12)
    assert correlation_notes(base, [*base, teammate]) == ["adds a QB stack: Teammate WR"]
    assert correlation_notes(base, [*base, opposing]) == ["adds a bring-back: Opposing WR"]
    assert correlation_notes(base, [*base, own_rb]) == ["adds a DST pair: DST's RB"]
    assert correlation_notes([*base, teammate], base) == ["breaks the QB stack: loses Teammate WR"]
    assert correlation_notes(base, [*base, P("Nobody", "WR", "GGG", "HHH", "g_gh", 4000, 9)]) == []


def test_a_dst_on_the_qbs_team_joins_the_stack_and_is_no_pair():
    qb = P("QB", "QB", "AAA", "BBB", "g_ab", 6000, 20)
    mate = P("Teammate WR", "WR", "AAA", "BBB", "g_ab", 5000, 12)
    own_dst = P("Own DST", "DST", "AAA", "BBB", "g_ab", 3000, 8)
    notes = correlation_notes([qb, mate], [qb, mate, own_dst])
    assert notes == ["grows the QB stack: adds Own DST"]


def test_the_report_always_lists_all_three_kinds_and_says_none_where_there_is_none():
    state, *_ = sams_note_state()
    lines = format_suggestions(
        suggest(state, [P("Worse RB", "RB", "WR_", "W2_", "g_wr", 6000, 3)]), metric="AggPts"
    )
    text = "\n".join(lines)
    assert "a. Best full re-fill: none" in text
    assert "b. Best 2-for-2 swaps: none" in text
    assert "c. Best 1-for-1 swaps: none" in text
    assert "Ranked by AggPts" in text and "Nothing beats this lineup on AggPts" in text


def test_a_fully_locked_lineup_has_nothing_to_swap():
    state = state_of(locked_core(), {})
    lines = format_suggestions(suggest(state, []), metric="ProjPts")
    assert lines[-1] == "Nothing left to swap: every slot is locked."


def test_a_refill_that_moves_the_flex_back_into_an_rb_slot_and_brings_in_a_fourth_receiver_is_assigned():
    """Found live (2026-10-08): the best lineup kept the FLEX back, dropped an RB and added a fourth WR.
    Pinning the FLEX back in FLEX left the new WR with no slot (StopIteration). The surplus receiver takes
    FLEX; the back moves into the RB slot."""
    flex_rb = P("Flex RB", "RB", "FR", "F2", "g_fr", 6000, 15)
    state = state_of(
        {},
        {
            "QB": P("Old QB", "QB", "OQ", "Q2", "g_oq", 5000, 10),
            "RB1": P("Old RB1", "RB", "O1", "A2", "g_o1", 5000, 10),
            "RB2": P("Old RB2", "RB", "O2", "B2", "g_o2", 5000, 10),
            "WR1": P("Old WR1", "WR", "W1", "C2", "g_w1", 5000, 10),
            "WR2": P("Old WR2", "WR", "W2", "D2", "g_w2", 5000, 10),
            "WR3": P("Old WR3", "WR", "W3", "E2", "g_w3", 5000, 10),
            "TE": P("Old TE", "TE", "OT", "T2", "g_ot", 4000, 8),
            "FLEX": flex_rb,
            "DST": P("Old DST", "DST", "OD", "D3", "g_od", 3000, 7),
        },
        cap=100000,
    )
    pool = [
        P("New QB", "QB", "NQ", "N2", "g_nq", 5000, 20),
        P("Star RB", "RB", "SR", "S2", "g_sr", 6000, 25),
        *[P(f"Star WR{i}", "WR", f"SW{i}", f"X{i}", f"g_sw{i}", 6000, 22 + i) for i in range(4)],
        P("New TE", "TE", "NT", "N3", "g_nt", 4000, 12),
        P("New DST", "DST", "ND", "N4", "g_nd", 3000, 11),
    ]
    refill = best_refill(state, [*pool, *(s.current for s in state.open)])
    assert refill is not None
    after = {i: new.name for i, (_old, new) in refill.changes.items()}
    assert after[IDX["FLEX"]].startswith("Star WR")  # the fourth receiver sits in FLEX
    # the FLEX back moved into an RB slot rather than being left in FLEX
    assert any(new.name == "Flex RB" and ROSTER_SLOTS[i] == "RB" for i, (_old, new) in refill.changes.items())
    for index, (_old, new) in refill.changes.items():  # and every changed slot holds a legal player
        assert _fits(ROSTER_SLOTS[index], new)
