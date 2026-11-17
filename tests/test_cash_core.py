"""`cash_core`: the Default cash lineup, its core and its open spots (blueprint Phase A). No simulator."""

from datetime import UTC, datetime

from test_lineup_suggest import CAP, NOW, _brute_force, slate, tags_for

from dfs import cash_core as cc
from dfs import lineup_suggest as ls
from dfs.late_swap_search import SwapPlayer
from dfs.models import ROSTER_SLOTS


def P(name, pos, salary=5000, value=10.0, team="A", opp="B", game="g1"):  # noqa: N802
    return SwapPlayer(name, pos, team, opp, game, salary, value)


def lineup(*players):
    return list(players)


def base(extra=0.0):
    """A legal nine on two games (RBs in different games), worth 100 + `extra`."""
    return [
        P("Q", "QB", 6000, 20.0 + extra, "A", "B", "g1"),
        P("R1", "RB", 6000, 14.0, "A", "B", "g1"),
        P("R2", "RB", 5000, 12.0, "C", "D", "g2"),
        P("W1", "WR", 6000, 13.0, "C", "D", "g2"),
        P("W2", "WR", 5000, 11.0, "B", "A", "g1"),
        P("W3", "WR", 4000, 10.0, "D", "C", "g2"),
        P("T", "TE", 4000, 8.0, "D", "C", "g2"),
        P("F", "WR", 3500, 7.0, "B", "A", "g1"),
        P("DEF", "DST", 3000, 5.0, "C", "D", "g2"),
    ]


def swap(lu, out, new):
    return [new if p.name == out else p for p in lu]


KICK = {}


# -- rule 1: the Default ---------------------------------------------------------------------------------


def test_the_default_is_the_top_projection_ties_broken_by_salary_then_names():
    a = base()
    cheaper = swap(a, "F", P("F2", "WR", 3000, 7.0, "B", "A", "g1"))  # same points, $500 cheaper
    worse = swap(a, "F", P("F3", "WR", 3000, 6.0, "B", "A", "g1"))
    core = cc.cash_core([a, worse, cheaper], [*a, *cheaper, *worse], cap=CAP, kickoff=KICK)
    assert {p.name for p in core.default} == {p.name for p in cheaper}
    assert core.tied == 2
    assert core.projection == 100.0


def test_the_default_matches_an_exhaustive_search_on_every_seed():
    for seed in range(1, 6):
        frame = slate(teams=4, seed=seed, per_team={"QB": 1, "RB": 1, "WR": 2, "TE": 1, "DST": 1})
        pool = ls.pool_for(frame, tags_for(frame), ls.CASH_TAGS, now=NOW, metric="CalPts")
        lineups, _ = ls.cash_lineups(pool, cap=CAP, k=50)
        core = cc.cash_core(lineups, pool, cap=CAP, kickoff=KICK, k=50)
        assert round(core.projection, 6) == _brute_force(pool, k=1)[0], seed


# -- rule 2: the near-optimal set ------------------------------------------------------------------------


def test_the_near_set_is_everything_within_one_and_a_half_points_and_says_when_it_may_be_cut_short():
    a = base()
    near = swap(a, "F", P("G", "WR", 3500, 5.5, "B", "A", "g1"))  # 98.5: inside
    far = swap(a, "F", P("H", "WR", 3500, 5.4, "B", "A", "g1"))  # 98.4: outside
    inside, cut = cc.near_optimal([far, near, a])
    assert [sum(p.value for p in lu) for lu in inside] == [100.0, 98.5]
    assert not cut
    inside, cut = cc.near_optimal([near, a], k=2)  # asked for two, both inside: more may exist
    assert cut
    inside, cut = cc.near_optimal([near, a], k=300)  # the search found only two: that is all of them
    assert not cut


# -- rules 3 and 4: the core and the open spots ------------------------------------------------------------


def test_core_is_seventy_percent_of_the_near_set_and_the_rest_are_open_spots_in_roster_order():
    a = base()
    lus = [a]
    for i, name in enumerate(["X1", "X2", "X3"]):  # three near lineups without F, and one without R2
        lus.append(swap(a, "F", P(name, "WR", 3500, 7.0 - 0.1 * (i + 1), "B", "A", "g1")))
    lus.append(swap(a, "R2", P("Y", "RB", 5000, 11.9, "C", "D", "g2")))
    core = cc.cash_core(lus, [p for lu in lus for p in lu], cap=CAP, kickoff=KICK)
    assert core.near == 5
    assert "R2" in core.core_names  # 4 of 5 = 80%
    assert "F" not in core.core_names  # 1 of 5
    assert [s.slot for s in core.open] == ["FLEX"]
    assert core.open[0].held == 2  # the Default and the lineup without R2
    assert [lab for lab in cc.SLOT_LABELS] == ["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST"]


def test_one_lineup_in_the_set_makes_all_nine_core():
    a = base()
    core = cc.cash_core([a], a, cap=CAP, kickoff=KICK)
    assert len(core.core) == 9 and core.open == ()


# -- rule 5: alternatives are single swaps from the pool -------------------------------------------------


def test_alternatives_fit_the_slot_and_the_cap_keep_the_lineup_legal_and_sit_within_the_band():
    a = base()
    other = swap(a, "F", P("X", "WR", 3500, 6.9, "B", "A", "g1"))
    pool = [
        *a,
        P("X", "WR", 3500, 6.9, "B", "A", "g1"),
        P("TE2", "TE", 3000, 6.0, "C", "D", "g2"),  # fits FLEX, -1.0: in
        P("RICH", "WR", 4000, 9.0, "B", "A", "g1"),  # +2.0 but $500 over (left is $0 here? see below)
        P("RB3", "RB", 3000, 6.5, "A", "B", "g1"),  # a second RB in g1 next to R1: illegal
        P("LOW", "WR", 3000, 5.0, "B", "A", "g1"),  # -2.0: outside the band
        P("QB2", "QB", 3000, 7.0, "B", "A", "g1"),  # does not fit FLEX
    ]
    left = CAP - sum(p.salary for p in a)
    core = cc.cash_core([a, other], pool, cap=CAP, kickoff=KICK)
    spot = next(s for s in core.open if s.slot == "FLEX")
    names = [x.player.name for x in spot.alternatives]
    assert "RB3" not in names and "LOW" not in names and "QB2" not in names
    if 4000 <= 3500 + left:
        assert names[0] == "RICH"  # best by projection first
    assert len(names) <= cc.MAX_ALTERNATIVES
    for x in spot.alternatives:
        assert x.d_points == round(x.player.value - spot.player.value, 6)
        assert x.d_salary == spot.player.salary - x.player.salary


def test_an_extra_legality_test_is_honoured():
    a = base()
    other = swap(a, "F", P("X", "WR", 3500, 6.9, "B", "A", "g1"))
    pool = [*a, P("X", "WR", 3500, 6.9, "B", "A", "g1")]
    core = cc.cash_core(
        [a, other], pool, cap=CAP, kickoff=KICK, legal=lambda lu: "X" not in {p.name for p in lu}
    )
    assert next(s for s in core.open if s.slot == "FLEX").alternatives == ()


# -- rule 6 and 7 ------------------------------------------------------------------------------------------


def test_a_spot_with_no_alternative_is_still_a_spot_and_no_lineup_gives_none():
    a = base()
    other = swap(a, "F", P("X", "WR", 3500, 6.9, "B", "A", "g1"))
    core = cc.cash_core([a, other], a, cap=CAP, kickoff=KICK)  # X is not in the pool handed in
    spot = next(s for s in core.open if s.slot == "FLEX")
    assert spot.alternatives == ()
    lines = cc.summary_lines(core, cc.band(None, None, None))
    assert any(cc.NOTHING_AT_THE_PRICE in line for line in lines)
    assert cc.cash_core([], a, cap=CAP, kickoff=KICK) is None


def test_the_same_inputs_give_the_same_payload_whatever_the_input_order():
    frame = slate(teams=4, seed=2, per_team={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DST": 1})
    pool = ls.pool_for(frame, tags_for(frame), ls.CASH_TAGS, now=NOW, metric="CalPts")
    lineups, _ = ls.cash_lineups(pool, cap=CAP, k=80)
    one = cc.cash_core(lineups, pool, cap=CAP, kickoff=KICK, k=80)
    two = cc.cash_core(list(reversed(lineups)), list(reversed(pool)), cap=CAP, kickoff=KICK, k=80)
    band = cc.band(0.5, (14, 25), 9.5, [4, 5])
    assert cc.payload(one, {}, band, round=1, at="t") == cc.payload(two, {}, band, round=1, at="t")


# -- the band ----------------------------------------------------------------------------------------------


def test_the_band_is_a_word_and_a_record_never_a_percentage():
    assert cc.band(0.40, (14, 25), 9.5).text == "under: cash builds have cashed 14 of 25"
    assert cc.band(0.50, (14, 25), 9.5).text == "coin flip: cash builds have cashed 14 of 25"
    assert cc.band(0.58, (14, 25), 9.5).text == "lean: cash builds have cashed 14 of 25"
    assert cc.band(0.58, None, 9.5).text == "lean: no cash build has been scored yet"
    assert "%" not in cc.band(None, (1, 2), None).text
    assert cc.band(0.58, (14, 25), 9.5).shown == 0.58  # kept, never printed


def test_the_record_counts_every_scored_row():
    rows = [{"cashed": "True"}, {"cashed": "False"}, {"cashed": "true"}, {"cashed": ""}]
    assert cc.record_of(rows) == (2, 3)
    assert cc.record_of([]) is None


def test_details_mark_late_games_and_status_flags():
    rows = [
        {"Name": "A", "Id": "1", "GameStart": "2026-11-15T18:00:00Z", "CashOwn": "0.31", "Avail": ""},
        {"Name": "B", "Id": "2", "GameStart": "2026-11-15T21:25:00Z", "CashOwn": "", "Avail": "Q"},
    ]
    d = cc.details_from_rows(rows)
    assert d["A"]["late"] is False and d["B"]["late"] is True
    assert d["A"]["cash_own"] == 0.31 and d["B"]["cash_own"] is None
    assert d["B"]["flags"] == ["Q"]


def test_the_payload_has_no_tier_or_percentage_text_and_its_differences_are_signed_right():
    a = base()
    other = swap(a, "F", P("X", "WR", 3000, 6.9, "B", "A", "g1"))
    core = cc.cash_core([a, other], [*a, P("X", "WR", 3000, 6.9, "B", "A", "g1")], cap=CAP, kickoff=KICK)
    out = cc.payload(core, {}, cc.band(0.6, (3, 5), 9.5, [8, 9]), round=4, at=datetime.now(UTC).isoformat())
    spot = out["open"][0]
    alt = spot["alternatives"][0]
    assert alt["d_points"] == -0.1 and alt["d_salary"] == 500  # $500 freed
    assert out["default"]["band"]["text"] == "lean: cash builds have cashed 3 of 5"
    assert len(out["default"]["names"]) == len(ROSTER_SLOTS)
