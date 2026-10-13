"""The FLEX rule: the latest kickoff among the flex position's players goes in FLEX (Week 6, slice 1)."""

from datetime import UTC, datetime

from dfs import flex_order as fo
from dfs.kickoff import parse_kickoff

SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
EARLY = "2026-10-18T13:00:00Z"  # Eastern wall-clock, labelled Z (see kickoff.py)
LATE = "2026-10-18T16:25:00Z"
SNF = "2026-10-18T20:20:00Z"
NOW_BEFORE = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)  # 11:00 am ET: nothing has started
NOW_AFTER_EARLY = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 2:00 pm ET: the 1 pm games are on


def seats(positions, kickoffs, names=None):
    """Nine seats in ROSTER_SLOTS order: `positions` and `kickoffs` per slot (FLEX's included)."""
    names = names or [f"P{i}" for i in range(9)]
    return [
        fo.Seat(slot, name, pos, parse_kickoff(k) if k else None)
        for slot, name, pos, k in zip(SLOTS, names, positions, kickoffs, strict=True)
    ]


# QB RB RB WR WR WR TE FLEX DST
WR_FLEX = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]
RB_FLEX = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "RB", "DST"]
TE_FLEX = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "TE", "DST"]


def test_the_wr_with_the_latest_kickoff_is_swapped_into_flex():
    # WR slots at 1:00, 1:00, SNF; the FLEX WR plays at 1:00 -> the SNF WR (slot index 5) goes to FLEX
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, SNF, EARLY, EARLY, EARLY]
    plan = fo.plan_flex(seats(WR_FLEX, kicks), NOW_BEFORE)
    assert plan.status == "move" and plan.to_flex.slot == "WR" and plan.from_flex.slot == "FLEX"
    cells = [f"c{i}" for i in range(9)]
    swapped = fo.apply_swap(cells, plan, seats(WR_FLEX, kicks))
    assert swapped[5] == "c7" and swapped[7] == "c5"  # exactly those two slots exchange
    assert [swapped[i] for i in (0, 1, 2, 3, 4, 6, 8)] == [cells[i] for i in (0, 1, 2, 3, 4, 6, 8)]


def test_rb_and_te_surpluses_follow_the_same_rule():
    rb_kicks = [EARLY, EARLY, LATE, EARLY, EARLY, EARLY, EARLY, EARLY, EARLY]  # the second RB plays late
    plan = fo.plan_flex(seats(RB_FLEX, rb_kicks), NOW_BEFORE)
    assert plan.moves and plan.to_flex.position == "RB" and plan.to_flex.slot == "RB"
    te_kicks = [
        EARLY,
        EARLY,
        EARLY,
        EARLY,
        EARLY,
        EARLY,
        SNF,
        EARLY,
        EARLY,
    ]  # TE1 plays SNF, the FLEX TE early
    plan = fo.plan_flex(seats(TE_FLEX, te_kicks), NOW_BEFORE)
    assert plan.moves and plan.to_flex.position == "TE" and plan.to_flex.slot == "TE"


def test_the_flex_already_holding_the_latest_kickoff_is_left_alone():
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, EARLY, EARLY, SNF, EARLY]
    assert fo.plan_flex(seats(WR_FLEX, kicks), NOW_BEFORE).status == "ok"


def test_a_tie_for_the_latest_kickoff_leaves_the_lineup_as_it_is():
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, SNF, EARLY, SNF, EARLY]  # two WRs at SNF
    plan = fo.plan_flex(seats(WR_FLEX, kicks), NOW_BEFORE)
    assert plan.status == "tie" and not plan.moves


def test_a_player_whose_game_has_started_is_never_moved():
    # the FLEX WR's 1 pm game is under way: swapping him out is impossible, so nothing moves
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, SNF, EARLY, EARLY, EARLY]
    plan = fo.plan_flex(seats(WR_FLEX, kicks), NOW_AFTER_EARLY)
    assert plan.status == "locked" and not plan.moves
    # both are unstarted at 11 am, so the same lineup moves then
    assert fo.plan_flex(seats(WR_FLEX, kicks), NOW_BEFORE).moves


def test_a_missing_kickoff_or_an_incomplete_lineup_is_not_guessed_at():
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, None, EARLY, EARLY, EARLY]
    assert fo.plan_flex(seats(WR_FLEX, kicks), NOW_BEFORE).status == "unknown"
    names = [f"P{i}" for i in range(9)]
    names[3] = ""
    assert fo.plan_flex(seats(WR_FLEX, [EARLY] * 9, names), NOW_BEFORE).status == "incomplete"
    # a lineup with no surplus position (e.g. 2 RB, 3 WR, TE, FLEX holding a QB) is not a legal shape
    bad = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "QB", "DST"]
    assert fo.plan_flex(seats(bad, [EARLY] * 9), NOW_BEFORE).status == "incomplete"


def test_the_labels_say_tnf_snf_mnf_or_the_day_and_time():
    assert fo.plays_label(parse_kickoff("2026-10-18T20:20:00Z")) == "SNF"
    assert fo.plays_label(parse_kickoff("2026-10-19T20:15:00Z")) == "MNF"
    assert fo.plays_label(parse_kickoff("2026-10-15T20:15:00Z")) == "TNF"
    assert fo.plays_label(parse_kickoff("2026-10-18T16:25:00Z")) == "Sun 4:25 pm"


def test_describe_names_the_two_players_and_when_the_mover_plays():
    kicks = [EARLY, EARLY, EARLY, EARLY, EARLY, SNF, EARLY, EARLY, EARLY]
    names = ["Q", "R1", "R2", "W1", "W2", "Puka Nacua", "T", "Cooper Kupp", "D"]
    plan = fo.plan_flex(seats(WR_FLEX, kicks, names), NOW_BEFORE)
    assert fo.describe(plan) == "Moved Puka Nacua to FLEX (plays SNF), Cooper Kupp to WR"
    assert fo.describe(fo.FlexPlan("ok")) == ""
