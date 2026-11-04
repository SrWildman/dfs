"""Roster sense for adds and drops (`roster_rules`, `trade.settle`, `waivers.rank_pickups`, the Sunday check),
on Sam's real rosters of 2026-11-04. Sam: "I would never drop a position player for a backup QB except on a
bye week. K and DST, I never need more than 1."
"""

from __future__ import annotations

import pytest

from dfs.season_long import lineups as lu
from dfs.season_long import roster_moves as rm
from dfs.season_long import roster_rules as rr
from dfs.season_long import sleeper_league as sl
from dfs.season_long import trade as tr
from dfs.season_long import waivers as wv
from dfs.season_long.config import RosterConfig, SeasonConfigError, load_season_config

from . import roster_fixtures as fx
from . import synthetic as syn

SKILL = {"RB", "WR", "TE"}


@pytest.fixture(autouse=True)
def _no_claims(monkeypatch):
    monkeypatch.setattr(sl, "fetch_transactions", lambda lid, w, final: [])


def picks(view, monkeypatch, **kw):
    ev = syn.evaluator(view, monkeypatch)
    return ev, wv.rank_pickups(view, ev, top=10, **kw)


def pos(view, pid):
    return view.ros.summary["position"][pid]


# -- Bourbon -------------------------------------------------------------------------------------------------


def test_bourbon_gets_no_second_qb_or_defense_while_daniels_plays(monkeypatch):
    v = fx.bourbon()
    ev, got = picks(v, monkeypatch)
    # The old arithmetic offered Young / Stroud / the Jaguars / the Texans for Corum.
    assert not [p for p in got if pos(v, p.player_id) in {"QB", "DEF", "K"} and p.drop in ev.rosters[1]]
    assert [p.player_id for p in got] == ["wr9"]  # an ordinary pickup still comes through
    assert got[0].drop == "corum" and got[0].to_ir == []


def test_a_qb_add_that_beats_the_incumbent_is_a_swap_never_a_skill_drop(monkeypatch):
    v = fx.bourbon(daniels=fx.flat(15))  # Stroud is now the better QB, and Daniels plays
    ev, got = picks(v, monkeypatch)
    qb = next(p for p in got if p.player_id == "stroud")
    assert qb.drop == "daniels" and qb.stream_week is None
    # The gain is the replacement over the incumbent, 5 points a game (Week 11 is a playoff week, counted
    # 1.5x), not a QB2's cover.
    assert qb.value == pytest.approx(5 * 3 + 5 * 1.5, abs=0.5)
    assert all(p.drop != "corum" for p in got if pos(v, p.player_id) in {"QB", "DEF"})


def test_bourbon_in_washingtons_bye_week_streams_a_qb_and_drops_after_the_week(monkeypatch):
    v = fx.bourbon(daniels=fx.bye(22))  # Daniels is on bye in the coming week (Week 8)
    ev, got = picks(v, monkeypatch)
    qb = next(p for p in got if pos(v, p.player_id) == "QB")
    assert qb.player_id == "stroud" and qb.stream_week == 8
    assert qb.drop == "corum" and qb.drop not in v.league.me.reserve
    assert "bye-week stream: drop him after Week 8" in qb.note
    # Valued on that week alone: about the 20 points of the week, not 4 weeks of a QB2.
    assert qb.value == pytest.approx(20.0, abs=1.0)
    assert "bye-week stream: drop him after Week 8" in wv.pickup_line(v, qb)
    # One stream is enough: he does not also stream the second QB in the same list's top-two.
    assert [pos(v, p.player_id) for p in got].count("QB") <= wv.PER_POSITION


def test_a_defense_add_swaps_the_defense_and_never_drops_a_skill_player(monkeypatch):
    mine = fx.bourbon_mine()
    free = {**fx.bourbon_free(), "jaguars": ("Jacksonville Jaguars", "DEF", "", fx.flat(12))}
    v = fx.build(mine, free, slots=fx.BOURBON_SLOTS, bench=5, reserve_slots=1, reserve=["smith"])
    ev, got = picks(v, monkeypatch)
    d = next(p for p in got if p.player_id == "jaguars")
    assert d.drop == "broncos"
    assert d.drop not in v.league.me.reserve and pos(v, d.drop) not in SKILL
    assert d.value == pytest.approx(3 * 3 + 3 * 1.5, abs=0.5)  # 3 points a game, the playoff week 1.5x


# -- Agile ---------------------------------------------------------------------------------------------------


def test_agile_swaps_the_kicker_on_ir_and_keeps_bernard(monkeypatch):
    v = fx.agile()  # the one IR slot already holds Tyson
    ev, got = picks(v, monkeypatch)
    k = next(p for p in got if pos(v, p.player_id) == "K")
    assert v.name(k.player_id) == "Ka'imi Fairbairn"
    assert k.drop == "loop" and k.to_ir == []
    assert all(p.drop != "bernard" for p in got if pos(v, p.player_id) == "K")
    assert "drop Tyler Loop" in wv.pickup_line(v, k)
    assert rr.ir_lines(v) == []  # no slot is open, and Tyson is still hurt


def test_agile_with_an_open_ir_slot_moves_loop_to_ir_and_drops_nobody(monkeypatch):
    v = fx.agile(reserve_slots=2)
    ev, got = picks(v, monkeypatch)
    k = next(p for p in got if pos(v, p.player_id) == "K")
    assert k.player_id == "fairbairn" and k.to_ir == ["loop"] and k.drop is None
    assert "Tyler Loop to IR (nobody dropped)" in wv.pickup_line(v, k)
    # ...and the move is its own line at the top.
    lines = rr.ir_lines(v)
    assert [x.kind for x in lines] == ["move"] and lines[0].player_id == "loop"
    assert "move Tyler Loop (K, IR) to IR" in lines[0].text


# -- Mega ----------------------------------------------------------------------------------------------------


def test_mega_suggests_no_second_qb_and_never_drops_from_ir(monkeypatch):
    v = fx.mega()
    ev, got = picks(v, monkeypatch)
    assert not [p for p in got if pos(v, p.player_id) == "QB"]
    assert all(p.drop not in {"henderson", "tate"} for p in got)
    # Even a named free agent, forced into the list, never takes an IR slot's player.
    named = wv.rank_pickups(v, ev, only=["mccarthy", "murray", "texans"])
    assert named and all(p.drop not in {"henderson", "tate"} for p in named)
    assert all(p.drop == "williams" for p in named if pos(v, p.player_id) == "QB")


def test_mega_takes_the_texans_only_as_a_swap_for_the_commanders(monkeypatch):
    worse = fx.mega()
    ev, got = picks(worse, monkeypatch)
    assert "texans" not in [p.player_id for p in got]
    better = fx.mega(texans=fx.flat(11))
    ev, got = picks(better, monkeypatch)
    d = next(p for p in got if p.player_id == "texans")
    assert d.drop == "commanders"


def test_mega_skips_a_k_it_has_no_slot_for(monkeypatch):
    v = fx.mega()
    assert v.league.slots.count("K") == 0
    assert rr.position_caps(v)["K"] == 1


# -- Choppa --------------------------------------------------------------------------------------------------


def test_choppa_with_no_qb_fills_the_empty_slot_with_the_bench_player_missed_least(monkeypatch):
    v = fx.choppa()
    ev, got = picks(v, monkeypatch)
    assert got and pos(v, got[0].player_id) == "QB"
    assert got[0].drop == "bench2" and got[0].drop not in v.league.me.reserve
    assert got[0].expected_weeks > 0
    # Both fill the same empty slot; each is judged against the roster as it is, so both are offered.
    qbs = [p for p in got if pos(v, p.player_id) == "QB"]
    assert {p.player_id for p in qbs} == {"stafford", "mccarthy"} and all(p.drop == "bench2" for p in qbs)


# -- a superflex league, and the caps from season.toml -------------------------------------------------


def superflex(**kw):
    mine = fx.bourbon_mine()
    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "K", "DEF"]
    return fx.build(mine, fx.bourbon_free(), slots=slots, bench=5, reserve_slots=1, reserve=["smith"], **kw)


def test_a_superflex_league_still_values_a_second_qb(monkeypatch):
    v = superflex()
    assert rr.position_caps(v)["QB"] == 2
    ev, got = picks(v, monkeypatch)
    qb = next(p for p in got if pos(v, p.player_id) == "QB")
    assert qb.stream_week is None and qb.drop is not None and pos(v, qb.drop) in SKILL
    assert qb.value > 0


def test_the_kicker_cap_comes_from_season_toml(monkeypatch):
    mine = fx.bourbon_mine()
    free = {**fx.bourbon_free(), "bass": ("Tyler Bass", "K", "", fx.flat(7.5))}
    one = fx.build(mine, free, slots=fx.BOURBON_SLOTS, bench=5, reserve_slots=1, reserve=["smith"])
    two = fx.build(
        mine, free, slots=fx.BOURBON_SLOTS, bench=5, reserve_slots=1, reserve=["smith"],
        roster=RosterConfig(max_k=2),
    )  # fmt: skip
    assert rr.position_caps(one)["K"] == 1 and rr.position_caps(two)["K"] == 2
    for view, kickers in ((one, 1), (two, 2)):
        ev = syn.evaluator(view, monkeypatch)
        roster = ev.rosters[1]
        after, dropped, _ = tr.settle([*roster, "bass"], len(roster), view, [], ev, before=roster)
        assert sum(pos(view, p) == "K" for p in after) == kickers
        assert "smith" in after and (kickers == 2) == ("bass" in after)
        assert all(pos(view, d) not in {"RB", "TE"} or kickers == 2 for d in dropped)


def test_the_roster_table_loads_from_season_toml(tmp_path):
    f = tmp_path / "season.toml"
    f.write_text('[roster]\nmax_k = 2\nmax_def = 3\n\n[[leagues]]\nname="A"\nleague_id="1"\nusername="u"\n')
    cfg = load_season_config(f)
    assert cfg.roster.max_k == 2 and cfg.roster.max_def == 3
    f.write_text("[roster]\nmax_qb = 3\n")
    with pytest.raises(SeasonConfigError):
        load_season_config(f)
    f.write_text("")
    assert load_season_config(f).roster.max_k == 1


# -- a same-position swap has to be worth min_swap_gain ----------------------------------------------------


def _bourbon_with(free_extra, **kw):
    """Bourbon with only `free_extra` as free agents (so the list is just what the test adds)."""
    return fx.build(
        fx.bourbon_mine(), free_extra, slots=fx.BOURBON_SLOTS, bench=5, reserve_slots=1,
        reserve=["smith"], **kw,
    )  # fmt: skip


def _qb(pts):
    return {"stroud": ("C.J. Stroud", "QB", "", fx.flat(pts))}


def test_min_swap_gain_defaults_to_five_points_and_loads_from_season_toml(tmp_path):
    assert RosterConfig().min_swap_gain == 5.0
    f = tmp_path / "season.toml"
    f.write_text('[roster]\nmin_swap_gain = 8\n\n[[leagues]]\nname="A"\nleague_id="1"\nusername="u"\n')
    assert load_season_config(f).roster.min_swap_gain == 8.0
    f.write_text("[roster]\nmin_swap_gain = -1\n")
    with pytest.raises(SeasonConfigError):
        load_season_config(f)


def test_a_small_qb_swap_is_not_suggested_but_a_big_one_is(monkeypatch):
    # Stroud 1 point a game over Daniels: 3 weeks + the playoff week at 1.5x = 4.5 points, under 5.
    small = _bourbon_with(_qb(23))
    ev, got = picks(small, monkeypatch)
    assert not [p for p in got if p.player_id == "stroud"]
    # 2 points a game is 9: over the bar, so the swap comes through.
    big = _bourbon_with(_qb(24))
    ev, got = picks(big, monkeypatch)
    qb = next(p for p in got if p.player_id == "stroud")
    assert qb.drop == "daniels" and qb.value == pytest.approx(9.0, abs=0.5)


def test_the_bar_is_the_leagues_own_setting(monkeypatch):
    for bar, offered in ((0.0, True), (4.5, True), (4.6, False), (20.0, False)):
        v = _bourbon_with(_qb(23), roster=RosterConfig(min_swap_gain=bar))
        ev, got = picks(v, monkeypatch)
        assert bool([p for p in got if p.player_id == "stroud"]) is offered, bar


def test_the_bar_covers_a_kicker_and_a_defense_too(monkeypatch):
    free = {
        "bass": ("Tyler Bass", "K", "", fx.flat(8.5)),  # +0.5 a game over Little
        "jaguars": ("Jacksonville Jaguars", "DEF", "", fx.flat(9.5)),  # +0.5 over the Broncos
    }
    ev, got = picks(_bourbon_with(free), monkeypatch)
    assert not [p for p in got if pos(_bourbon_with(free), p.player_id) in {"K", "DEF"}]
    v = _bourbon_with(free, roster=RosterConfig(min_swap_gain=0.0))
    ev, got = picks(v, monkeypatch)
    assert {p.player_id for p in got} >= {"bass", "jaguars"}


def test_an_empty_slot_fill_is_exempt_from_the_bar(monkeypatch):
    v = fx.choppa(roster=RosterConfig(min_swap_gain=1000.0))  # no QB at all
    ev, got = picks(v, monkeypatch)
    qbs = [p for p in got if pos(v, p.player_id) == "QB"]
    assert {p.player_id for p in qbs} == {"stafford", "mccarthy"} and all(p.drop == "bench2" for p in qbs)


def test_a_bye_week_stream_is_exempt_from_the_bar(monkeypatch):
    v = fx.bourbon(daniels=fx.bye(22), roster=RosterConfig(min_swap_gain=1000.0))
    ev, got = picks(v, monkeypatch)
    qb = next(p for p in got if pos(v, p.player_id) == "QB")
    assert qb.stream_week == 8 and qb.drop == "corum"  # the stream stands; no rest-of-season swap beside it


def test_a_named_free_agent_is_still_reported_under_the_bar(monkeypatch):
    v = _bourbon_with(_qb(23))
    ev = syn.evaluator(v, monkeypatch)
    named = wv.rank_pickups(v, ev, only=["stroud"])
    assert [p.player_id for p in named] == ["stroud"] and named[0].drop == "daniels"


def test_the_free_move_follows_the_same_bar(monkeypatch):
    v = _bourbon_with(_qb(23))  # +1 a game over 4 weeks = 4 points: over the 3-point free-move floor
    ev = syn.evaluator(v, monkeypatch)
    roster = ev.rosters[1]
    assert tr.best_swap(v, ev, roster, ["stroud"]) == (None, "", "")
    easy = _bourbon_with(_qb(23), roster=RosterConfig(min_swap_gain=0.0))
    ev = syn.evaluator(easy, monkeypatch)
    after, added, dropped = tr.best_swap(easy, ev, ev.rosters[1], ["stroud"])
    assert (added, dropped) == ("stroud", "daniels") and after is not None


def test_an_ordinary_skill_pickup_never_meets_the_bar(monkeypatch):
    v = _bourbon_with({"wr9": ("WR Nine", "WR", "", fx.flat(13))}, roster=RosterConfig(min_swap_gain=1000.0))
    ev, got = picks(v, monkeypatch)
    assert [p.player_id for p in got] == ["wr9"]


# -- IR housekeeping --------------------------------------------------------------------------------------


def test_a_healthy_player_in_an_ir_slot_is_flagged():
    v = fx.bourbon()
    v.ros.summary.loc["smith", "injury_status"] = ""
    lines = rr.ir_lines(v)
    assert [x.kind for x in lines] == ["activate"]
    assert "IR slot holds a healthy player" in lines[0].text and "DeVonta Smith" in lines[0].text


def test_reserve_eligibility_follows_the_leagues_settings():
    v = fx.mega()
    assert v.league.reserve_statuses >= {"IR", "PUP", "NFI", "Out", "Doubtful"}
    assert "Sus" not in v.league.reserve_statuses
    plain = syn.view()
    assert plain.league.reserve_statuses == {"IR", "PUP", "NFI"}


def test_a_reserve_list_is_read_from_sleepers_roster():
    # As in Sam's four leagues: the IR count is in settings, roster_positions has no "IR".
    league = {
        "league_id": "1",
        "season": "2026",
        "roster_positions": ["QB", "BN"],
        "settings": {"reserve_slots": 1},
    }
    rosters = [
        {"roster_id": 1, "owner_id": "u", "players": ["a", "b"], "reserve": ["b", "gone"], "settings": {}},
        {"roster_id": 2, "owner_id": "v", "players": ["c"], "reserve": None, "settings": {}},
    ]
    lg = sl.parse_league("L", league, rosters, [], "u", {"season": 2026, "week": 8})
    assert lg.team(1).reserve == ["b"] and lg.team(2).reserve == []
    assert lg.reserve_ids == {"b"} and lg.reserve_slots == 1 and lg.bench_slots == 1


# -- settle: trades and the free-agent fill ------------------------------------------------------------------


def test_a_trade_that_brings_a_second_defense_costs_the_worse_one(monkeypatch):
    v = fx.bourbon()
    ev = syn.evaluator(v, monkeypatch)
    mine = ev.rosters[1]
    after, dropped, _ = tr.settle([*mine, "jaguars"], len(mine), v, [], ev, before=mine)
    assert dropped == ["jaguars"]  # Denver is better, so the newcomer is the one who goes
    assert "smith" in after


def test_a_short_roster_is_filled_without_a_second_kicker_or_defense(monkeypatch):
    v = fx.bourbon()
    ev = syn.evaluator(v, monkeypatch)
    mine = [p for p in ev.rosters[1] if p != "wr4"]  # a roster that gave a player away
    pool = ["jaguars", "texans", "young", "wr9"]
    after, _, added = tr.settle(mine, len(mine) + 1, v, pool, ev, before=ev.rosters[1])
    assert added == ["wr9"] and "jaguars" not in after


def test_settle_never_drops_from_ir_even_when_everything_else_is_needed(monkeypatch):
    v = fx.bourbon()
    ev = syn.evaluator(v, monkeypatch)
    mine = ev.rosters[1]
    for extra in ("wr9", "young", "jaguars"):
        after, dropped, _ = tr.settle([*mine, extra], len(mine), v, [], ev, before=mine)
        assert "smith" in after and "smith" not in dropped


# -- the Sunday check ----------------------------------------------------------------------------------------


def test_the_sunday_check_names_the_drop_for_a_free_agent_qb_in_the_bye_week(monkeypatch):
    v = fx.bourbon(daniels=fx.bye(22))
    ev = syn.evaluator(v, monkeypatch)
    v.league.me.starters = ["daniels", "rb1", "rb2", "wr1", "wr2", "te1", "wr3", "wr4", "little", "broncos"]
    alerts = lu.check(v, ev)
    qb = next(a for a in alerts if a.slot == "QB")
    assert "Stroud" in qb.swap and "drop Blake Corum" in qb.swap


def test_the_sunday_check_flags_a_healthy_player_in_an_ir_slot(monkeypatch):
    v = fx.bourbon()
    v.ros.summary.loc["smith", "injury_status"] = ""
    ev = syn.evaluator(v, monkeypatch)
    v.league.me.starters = ["daniels", "rb1", "rb2", "wr1", "wr2", "te1", "wr3", "wr4", "little", "broncos"]
    alerts = [a for a in lu.check(v, ev) if a.slot == "IR"]
    assert alerts and "activate DeVonta Smith" in alerts[0].reason


def test_a_stream_is_not_offered_when_the_starter_plays(monkeypatch):
    v = fx.bourbon()
    ev = syn.evaluator(v, monkeypatch)
    assert not rm.stream_applies(v, ev, ev.rosters[1], "stroud")
    v2 = fx.bourbon(daniels=fx.bye(22))
    ev2 = syn.evaluator(v2, monkeypatch)
    assert rm.stream_applies(v2, ev2, ev2.rosters[1], "stroud")
