"""Suggested lineups (Week 6, slice 4): legality, diversity, the stack rule, the exposure cap, determinism and
the time budget, on a synthetic slate (no sheet, no network)."""

import time
from datetime import UTC, datetime
from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs import lineup_shapes as shapes_mod
from dfs import lineup_suggest as ls
from dfs.kickoff import parse_kickoff
from dfs.late_swap_search import LineupState, OpenSlot, SwapPlayer, conflicts, top_lineups
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


# --- the rules ---------------------------------------------------------------


def assert_legal(build):
    players = list(build.players)
    assert len(players) == 9 and len({p.name for p in players}) == 9
    assert sum(p.salary for p in players) <= CAP and build.salary == sum(p.salary for p in players)
    for slot, p in zip(ROSTER_SLOTS, players, strict=True):  # slot positions, FLEX takes an RB, WR or TE
        assert p.position in (("RB", "WR", "TE") if slot == "FLEX" else (slot,))
    for p in players:
        assert not conflicts(p, [o for o in players if o is not p])  # no DST vs own QB, one RB a game


@pytest.fixture(scope="module")
def edge():
    return slate()


@pytest.fixture(scope="module")
def report(edge):
    return run(edge)


def _qb(build):
    return next(p.name for p in build.players if p.position == "QB")


def test_the_defaults_are_three_cash_builds_and_a_gpp_build_for_every_qb_stack_two_when_they_fit(
    report, edge
):
    kinds = [b.kind for b in report.builds]
    assert kinds[:3] == ["cash"] * 3 and set(kinds[3:]) == {"gpp"}
    assert [b.number for b in report.builds] == list(range(1, len(report.builds) + 1))
    quarterbacks = {
        p.name
        for p in ls.pool_for(edge, tags_for(edge), ls.GPP_TAGS, now=NOW, metric="CalPts")
        if p.position == "QB"
    }
    per_qb = {}
    for build in (b for b in report.builds if b.kind == "gpp"):
        per_qb[_qb(build)] = per_qb.get(_qb(build), 0) + 1
    assert set(per_qb) == quarterbacks  # every QB stack has a build...
    assert all(1 <= n <= ls.BUILDS_PER_QB for n in per_qb.values())  # ...and at most two each
    assert any(n == ls.BUILDS_PER_QB for n in per_qb.values())
    assert report.complete


def test_every_build_is_a_legal_lineup(report):
    for build in report.builds:
        assert_legal(build)


def test_every_build_differs_from_the_earlier_ones_of_its_kind_by_at_least_three_players(report):
    for kind in ("cash", "gpp"):
        group = [b for b in report.builds if b.kind == kind]
        floor = (
            ls.MIN_DIFFERENCE if kind == "cash" else ls.SECOND_BUILD_MIN_DIFFERENCE
        )  # a QB's 2nd GPP build
        for a, b in combinations(group, 2):
            assert len({p.name for p in a.players} - {p.name for p in b.players}) >= floor


def test_every_gpp_build_has_a_shape_it_really_matches_and_its_title_names_it(report):
    for build in (b for b in report.builds if b.kind == "gpp"):
        qb = next(p for p in build.players if p.position == "QB")
        mates = [p for p in build.players if p.team == qb.team and p.position in ("RB", "WR", "TE")]
        assert 1 <= len(mates) <= 3  # every shape ties the QB to one to three teammates
        assert build.shapes and set(build.shapes) <= set(shapes_mod.SHAPES)
        assert tuple(shapes_mod.shapes_of(build.players)) == tuple(
            s for s in build.shapes if s != shapes_mod.CONTRARIAN
        )
        assert build.stack == shapes_mod.shape_name(build.players, build.shapes)
        assert build.stack.startswith(qb.team) and build.stack in build.title


def test_no_player_is_in_more_than_sixty_percent_of_the_gpp_builds(edge):
    # `--gpp N` (the N best): the exposure cap binds every build. The default covers every QB first, and a
    # QB's first build is never given up to the cap (only its second round respects it).
    gpp = run(edge, cash_builds=0, gpp_builds=5).builds
    cap = ls.exposure_cap(len(gpp))
    assert cap == 3  # 60% of five builds
    uses: dict[str, int] = {}
    for build in gpp:
        for p in build.players:
            uses[p.name] = uses.get(p.name, 0) + 1
    assert max(uses.values()) <= cap


def test_builds_are_listed_cash_then_gpp_by_qb_then_calpts_total_and_numbered_down_the_list(report):
    """Sam, 2026-10-17: ordered by QB, then CalPts total, within Cash and GPP; Build N follows that order."""
    assert [b.number for b in report.builds] == list(range(1, len(report.builds) + 1))
    kinds = [b.kind for b in report.builds]
    assert kinds == sorted(kinds, key=lambda k: k != "cash")  # every cash build before every GPP build
    for kind in ("cash", "gpp"):
        part = [b for b in report.builds if b.kind == kind]
        keys = [(_qb(b), -round(b.projection, 6)) for b in part]
        assert keys == sorted(keys), kind  # QB name A-Z, the higher CalPts total first within a QB
    for b in report.builds:  # the title carries the number it is listed under
        assert b.title.startswith(f"{'Cash' if b.kind == 'cash' else 'GPP'} build {b.number}")


def test_a_run_times_its_search_phases_and_phase_lines_lays_them_out_with_a_total(edge):
    report = run(edge, cash_builds=1, gpp_builds=1)
    assert list(report.phases) == ["candidates", "search", "simulate", "choose"]
    assert all(seconds >= 0 for seconds in report.phases.values())
    assert sum(report.phases.values()) <= report.seconds + 1e-6
    lines = ls.phase_lines({"read sheet": (1.0, 2), "search": (2.0, 0), "format tab": (1.0, 1)})
    assert lines[0] == "Phase times:"
    assert "read sheet" in lines[1] and "2 requests" in lines[1] and " 25%" in lines[1]
    assert "request" not in lines[2] and " 50%" in lines[2]  # no sheet calls, nothing to show
    assert "1 request" in lines[3] and "requests" not in lines[3]
    assert lines[-1].split() == ["total", "4.0", "s"]
    assert ls.phase_lines({})[-1].strip().startswith("total")  # an empty run does not divide by zero


def test_display_order_is_the_same_whatever_order_the_picks_were_made_in():
    def pick(qb, total, extra="x"):
        players = tuple(P(n, pos) for n, pos in ((qb, "QB"), (f"{extra}1", "RB"), (f"{extra}2", "WR")))
        return ls.Scored(players, total, 50000, 0.5, 0.1, 100.0, 130.0)

    picked = [
        ("gpp", pick("Zed", 130.0, "a"), "T stack"),
        ("cash", pick("Bob", 120.0, "b"), ""),
        ("gpp", pick("Abe", 125.0, "c"), "U stack"),
        ("cash", pick("Bob", 135.0, "d"), ""),
        ("gpp", pick("Abe", 140.0, "e"), "U stack"),
    ]
    forward = ls.number_builds(picked)
    backward = ls.number_builds(picked[::-1])
    assert [(b.kind, b.players[0].name, b.projection) for b in forward] == [
        ("cash", "Bob", 135.0),
        ("cash", "Bob", 120.0),
        ("gpp", "Abe", 140.0),
        ("gpp", "Abe", 125.0),
        ("gpp", "Zed", 130.0),
    ]
    assert [b.title for b in forward] == [b.title for b in backward]
    assert [b.title for b in forward][0] == "Cash build 1" and forward[2].title == "GPP build 3  ·  U stack"


def test_the_flex_of_every_build_is_the_latest_kickoff_of_its_surplus_position(report, edge):
    kickoff = dict(zip(edge["Name"], edge["GameStart"].map(parse_kickoff), strict=True))
    for build in report.builds:
        flex = build.players[ROSTER_SLOTS.index("FLEX")]
        same = [p for p in build.players if p.position == flex.position]
        assert kickoff[flex.name] == max(kickoff[p.name] for p in same)


def test_the_same_pool_and_data_give_the_same_builds(edge, report):
    again = run(edge)
    assert [(b.title, [p.name for p in b.players], b.p_cash, b.p_gpp) for b in again.builds] == [
        (b.title, [p.name for p in b.players], b.p_cash, b.p_gpp) for b in report.builds
    ]


def test_a_realistic_pool_finishes_inside_the_time_budget():
    edge = slate(teams=6, seed=7)  # 60 players, every one tagged Both: as big as a real pool gets
    started = time.monotonic()
    report = run(edge)
    assert report.complete and report.seconds < ls.TIME_LIMIT_SECONDS
    assert time.monotonic() - started < ls.TIME_LIMIT_SECONDS


# --- who is a candidate ------------------------------------------------------


def test_only_pool_players_with_the_right_tag_who_are_playing_and_have_not_kicked_off_are_candidates(edge):
    frame = edge.copy()
    frame.loc[0, "Avail"] = "OUT"
    frame.loc[1, "Avail"] = "Q"  # questionable stays: a coin flip, the Avail is shown beside the name
    started = frame.index[frame["GameStart"] == EARLY][5]
    tags = {name: "Cash" for name in frame["Name"]}
    tags[frame.loc[2, "Name"]] = "GPP"
    tags[frame.loc[3, "Name"]] = "Both"
    after_early = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 2 pm ET: the 1 pm games are under way
    cash = {p.name for p in ls.pool_for(frame, tags, ls.CASH_TAGS, now=NOW, metric="CalPts")}
    gpp = {p.name for p in ls.pool_for(frame, tags, ls.GPP_TAGS, now=NOW, metric="CalPts")}
    later = {p.name for p in ls.pool_for(frame, tags, ls.CASH_TAGS, now=after_early, metric="CalPts")}
    assert frame.loc[0, "Name"] not in gpp  # OUT
    assert frame.loc[started, "Name"] in cash and frame.loc[started, "Name"] not in later  # his game started
    assert frame.loc[2, "Name"] not in cash and frame.loc[2, "Name"] in gpp  # GPP-only: not a cash candidate
    assert frame.loc[3, "Name"] in cash and frame.loc[3, "Name"] in gpp  # Both is in each
    assert frame.loc[started, "Name"] in gpp  # a Cash-marked player is a GPP candidate too: any mark will do
    assert ls.pool_for(frame, {}, ls.CASH_TAGS, now=NOW, metric="CalPts") == []  # an empty pool


def test_any_dst_on_the_slate_is_a_candidate_pooled_or_not_but_never_one_ruled_out_or_kicked_off(edge):
    """Sam, 2026-10-17: builds may use ANY defense. Here nobody pooled a DST at all."""
    frame = edge.copy()
    dst = frame["Position"] == "DST"
    tags = {name: "Both" for name in frame.loc[~dst, "Name"]}
    out = frame.index[dst][0]
    started = frame.index[dst & (frame["GameStart"] == EARLY)][-1]
    frame.loc[out, "Avail"] = "OUT"
    after_early = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)
    now = {p.name for p in ls.pool_for(frame, tags, ls.CASH_TAGS, now=NOW, metric="CalPts", any_dst=True)}
    late = {
        p.name for p in ls.pool_for(frame, tags, ls.GPP_TAGS, now=after_early, metric="CalPts", any_dst=True)
    }
    dsts = set(frame.loc[dst, "Name"])
    assert dsts - {frame.loc[out, "Name"]} <= now  # every other defense, none of them pooled
    assert frame.loc[out, "Name"] not in now
    assert frame.loc[started, "Name"] in now and frame.loc[started, "Name"] not in late  # his game started
    plain = {p.name for p in ls.pool_for(frame, tags, ls.CASH_TAGS, now=NOW, metric="CalPts")}
    assert not plain & dsts  # the default stays "pool only"
    assert ls.pool_for(frame, {}, ls.CASH_TAGS, now=NOW, metric="CalPts", any_dst=True) == []  # empty pool


def test_builds_use_defenses_nobody_pooled_and_never_one_against_the_builds_own_qb(edge):
    dst = edge["Position"] == "DST"
    tags = {name: "Both" for name in edge.loc[~dst, "Name"]}
    report = run(edge, tags)
    assert report.builds
    for build in report.builds:
        assert_legal(build)  # no DST against his own QB
    used = {p.name for b in report.builds for p in b.players if p.position == "DST"}
    assert used and not used & set(tags)  # every defense used is one nobody pooled


def test_a_player_with_no_calpts_is_projected_at_his_projpts():
    frame = slate()
    frame.loc[0, "CalPts"] = np.nan
    pool = {p.name: p for p in ls.pool_for(frame, tags_for(frame), ls.CASH_TAGS, now=NOW, metric="CalPts")}
    assert pool[frame.loc[0, "Name"]].value == float(frame.loc[0, "ProjPts"])


# --- the pieces --------------------------------------------------------------


def P(name, pos, team="A", opp="B", salary=5000, value=10.0, game="g"):
    return SwapPlayer(name, pos, team, opp, game, salary, value)


def test_can_stack_needs_one_teammate_of_any_skill_position_in_the_pool():
    qb = P("QB1", "QB", "A", "B")
    assert ls.can_stack(qb, [qb, P("A WR1", "WR", "A", "B")])
    assert ls.can_stack(qb, [qb, P("A RB1", "RB", "A", "B")]) and ls.can_stack(
        qb, [qb, P("A TE1", "TE", "A", "B")]
    )
    assert not ls.can_stack(qb, [qb])  # alone
    assert not ls.can_stack(qb, [qb, P("A DST", "DST", "A", "B")])  # a DST is not a teammate for a stack
    assert not ls.can_stack(qb, [qb, P("B WR1", "WR", "B", "A")])  # only the other team's players


def test_a_qb_who_cannot_stack_is_not_searched_so_it_cannot_eat_the_time_limit(edge, monkeypatch):
    """Real pool, 2026-10-17: 7 of 12 QBs had no possible stack; each still spent its share of the 60 s."""
    pool = ls.pool_for(edge, tags_for(edge), ls.GPP_TAGS, now=NOW, metric="CalPts")
    stuck = next(p for p in pool if p.position == "QB")
    pool = [
        p for p in pool if not (p.team == stuck.team and p.position in ("RB", "WR", "TE"))
    ]  # no teammates
    assert not ls.can_stack(stuck, pool)
    searched = []
    real = shapes_mod.top_lineups
    monkeypatch.setattr(
        shapes_mod,
        "top_lineups",
        lambda state, *a, **k: searched.append(state.fixed[0].name) or real(state, *a, **k),
    )
    lineups, complete = ls.stacked_lineups(pool, cap=CAP, deadline=time.monotonic() + 60)
    assert complete and lineups
    quarterbacks = {p.name for p in pool if p.position == "QB"}
    assert stuck.name in quarterbacks and stuck.name not in searched  # he was never searched...
    # ...and everyone else was, except the QB facing him, whose stack-WITH-a-bring-back has no bring-back left
    opponent = {p.name for p in pool if p.position == "QB" and p.team == stuck.opp}
    assert set(searched) == quarterbacks - {stuck.name} - opponent
    assert all(stuck.name not in {p.name for p in lu} for lu in lineups)  # and no lineup has him


def test_is_stacked_needs_the_qb_and_one_to_three_teammates_and_names_the_stack():
    qb = P("QB", "QB", "DET", "ARI")
    wr1, wr2, te = P("W1", "WR", "DET", "ARI"), P("W2", "WR", "DET", "ARI"), P("T", "TE", "DET", "ARI")
    rb = P("R", "RB", "DET", "ARI")
    back = P("Back", "WR", "ARI", "DET")
    assert ls.is_stacked([qb, wr1, te, back])
    assert ls.stack_name([qb, wr1, te, back]) == "DET stack QB+WR+TE + ARI bring-back"
    assert ls.is_stacked([qb, wr1, back]) and ls.is_stacked([qb, wr1, wr2])  # one teammate, or no bring-back
    assert ls.stack_name([qb, rb, wr1]) == "DET naked stack QB+RB+WR"
    assert ls.is_stacked(
        [qb, wr1, wr2, P("D", "DST", "ARI", "DET")]
    )  # a DST is neither a teammate nor a back
    assert not ls.is_stacked([qb, back])  # no teammate
    assert not ls.is_stacked([qb, wr1, wr2, te, rb])  # four teammates
    assert not ls.is_stacked([wr1, wr2, back])  # no QB
    assert ls.stack_name([wr1]) == ""


def test_choose_skips_lookalikes_and_enforces_the_exposure_cap():
    def lineup(names):
        return tuple(P(n, "WR") for n in names)

    def scored(names, p):
        return ls.Scored(lineup(names), 100.0, 50000, p, p, 100.0, 120.0)

    a = scored("abcdefghi", 0.9)
    near = scored("abcdefgxy", 0.8)  # differs from a by 2: skipped
    other = scored("abcdefxyz", 0.7)  # differs by 3: kept
    third = scored("ABCDEFGHI", 0.6)
    chosen = ls.choose([third, near, other, a], 3, key=lambda s: -s.p_cash)
    assert [s.players[0].name for s in chosen] == ["a", "a", "A"] and near not in chosen
    capped = ls.choose([a, other, third], 3, key=lambda s: -s.p_cash, max_per_player=1)
    assert capped == [a, third]  # `other` shares players with `a`: over the cap of one
    assert ls.exposure_cap(5) == 3 and ls.exposure_cap(3) == 2 and ls.exposure_cap(1) == 1


def _scored(names, p_gpp=0.0, projection=100.0, salary=50000):
    return ls.Scored(tuple(P(n, "WR") for n in names), projection, salary, p_gpp, p_gpp, 100.0, 120.0)


def test_choose_is_the_same_whatever_order_the_candidates_arrive_in():
    """Probabilities are counts of draws, so ties are everywhere: the order may not come from the input."""
    import random

    candidates = [
        _scored([f"p{i}{j}" for j in range(9)], p_gpp=(i % 3) / 100, projection=100.0 + (i % 2))
        for i in range(30)
    ]
    expected = [s.players for s in ls.choose(candidates, 4, key=ls.gpp_key, max_per_player=2)]
    for seed in range(8):
        shuffled = candidates[:]
        random.Random(seed).shuffle(shuffled)
        assert [s.players for s in ls.choose(shuffled, 4, key=ls.gpp_key, max_per_player=2)] == expected


def test_probabilities_that_differ_by_float_noise_are_a_tie_that_falls_to_the_next_rule():
    noisy = _scored("abcdefghi", p_gpp=0.123456789 + 3e-16, projection=100.0)
    clean_better = _scored("ABCDEFGHI", p_gpp=0.123456789, projection=101.0)
    assert ls.gpp_key(noisy)[0] == ls.gpp_key(clean_better)[0]
    assert ls.choose([noisy, clean_better], 1, key=ls.gpp_key) == [clean_better]  # decided by projection
    # a real difference (one draw in 1,500) still decides
    real = _scored("abcdefghi", p_gpp=0.123456789 + 1 / 1500)
    assert ls.choose([clean_better, real], 1, key=ls.gpp_key) == [real]
    # and a full tie is settled by the names, not by who came first
    one, two = _scored("abcdefghi"), _scored("ABCDEFGHI")
    assert ls.choose([one, two], 1, key=ls.gpp_key) == ls.choose([two, one], 1, key=ls.gpp_key)


def test_choose_searches_every_candidate_not_just_the_best_few_when_the_cap_leaves_it_short():
    """Every one of the 100 best candidates holds `X`, so with a cap of one only one can be picked; the second
    build exists but is far down the ranking. (The search used to look at the top 80 only.)"""
    top = [_scored(["X", *[f"t{i}{j}" for j in range(8)]], p_gpp=0.5 - i / 1000) for i in range(100)]
    far = _scored([f"far{j}" for j in range(9)], p_gpp=0.01)
    chosen = ls.choose([*top, far], 2, key=ls.gpp_key, max_per_player=1)
    assert chosen == [top[0], far]


def test_choose_returns_the_first_valid_set_in_rank_order_and_agrees_with_brute_force():
    import random
    from itertools import combinations

    rng = random.Random(5)
    for trial in range(12):
        names = [f"n{i}" for i in range(16)]
        candidates = [
            _scored(
                rng.sample(names, 9), p_gpp=rng.choice([0.0, 0.002, 0.004]), projection=rng.choice([100, 101])
            )
            for _ in range(25)
        ]
        ordered = sorted(candidates, key=lambda s: (*ls.gpp_key(s), ls.lineup_key(s)))

        def valid(group):
            uses: dict[str, int] = {}
            for s in group:
                for p in s.players:
                    uses[p.name] = uses.get(p.name, 0) + 1
            return max(uses.values()) <= 2 and all(
                ls.different_enough(a.players, b.players) for a, b in combinations(group, 2)
            )

        expected = next((list(g) for g in combinations(ordered, 3) if valid(g)), None)
        got = ls.choose(candidates, 3, key=ls.gpp_key, max_per_player=2)
        if expected is None:
            assert len(got) < 3, trial
        else:
            assert got == expected, trial


def test_seat_puts_the_latest_kickoff_of_the_surplus_position_in_flex_and_ties_use_the_lowest_projection():
    early, late = parse_kickoff(EARLY), parse_kickoff(NIGHT)
    players = [
        P("QB", "QB", value=20),
        P("R1", "RB", value=12),
        P("R2", "RB", value=11),
        P("W1", "WR", value=15),
        P("W2", "WR", value=14),
        P("W3", "WR", value=13),
        P("W4", "WR", value=9),
        P("TE", "TE", value=8),
        P("DST", "DST", value=6),
    ]
    kick = {p.name: early for p in players} | {"W2": late}
    seated = ls.seat(players, kick)
    assert [p.name for p in seated][ROSTER_SLOTS.index("FLEX")] == "W2"  # the one that plays at night
    tie = ls.seat(players, {p.name: early for p in players})
    assert tie[ROSTER_SLOTS.index("FLEX")].name == "W4"  # all early: the lowest projected is the FLEX
    assert [p.position for p in seated] == [s if s != "FLEX" else "WR" for s in ROSTER_SLOTS]


def _brute_force(pool, k):
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


def test_top_lineups_agrees_with_exhaustive_search_on_a_small_pool():
    frame = slate(teams=4, seed=3, per_team={"QB": 1, "RB": 1, "WR": 2, "TE": 1, "DST": 1})
    pool = ls.pool_for(frame, tags_for(frame), ls.CASH_TAGS, now=NOW, metric="CalPts")
    state = LineupState(cap=CAP, fixed={}, open=[OpenSlot(i, s, None) for i, s in enumerate(ROSTER_SLOTS)])
    found, complete = top_lineups(state, pool, k=12)
    assert complete and len(found) == 12
    totals = [round(sum(p.value for p in a.values()), 6) for a in found]
    assert totals == sorted(totals, reverse=True)
    assert totals == _brute_force(pool, 12)[: len(totals)]
    sets = [frozenset(p.name for p in a.values()) for a in found]
    assert len(set(sets)) == len(sets)  # no lineup twice


def test_stacked_lineups_are_all_stacked_legal_and_best_first(edge):
    pool = ls.pool_for(edge, tags_for(edge), ls.GPP_TAGS, now=NOW, metric="CalPts")
    lineups, complete = ls.stacked_lineups(pool, cap=CAP, k=60)
    assert complete and len(lineups) == 60  # the best 60 of every QB's best 80
    assert all(ls.is_stacked(lu) for lu in lineups)
    for lu in lineups:
        assert sum(p.salary for p in lu) <= CAP and len({p.name for p in lu}) == 9
        assert not any(conflicts(p, [o for o in lu if o is not p]) for p in lu)
    # best first at the precision the ordering uses: totals that differ by float noise are ties
    totals = [round(sum(p.value for p in lu), ls.VALUE_DECIMALS) for lu in lineups]
    assert totals == sorted(totals, reverse=True)


# --- when it cannot be done --------------------------------------------------


def test_a_pool_with_no_possible_stack_says_so_and_builds_no_gpp_lineup(edge):
    # only one QB's team and nobody from his opponent in the pool (a GPP build may use any mark)
    qb = edge[edge["Position"] == "QB"].iloc[0]
    tags = {n: "GPP" for n in edge.loc[(edge["Team"] == qb["Team"]) & (edge["Position"] != "DST"), "Name"]}
    report = run(edge, tags)
    assert not [b for b in report.builds if b.kind == "gpp"]
    assert any("No stacked GPP lineup fits" in n for n in report.notes)


def test_an_empty_pool_builds_nothing_and_says_nothing_dishonest(edge):
    report = run(edge, {})
    assert report.builds == [] and report.candidates == {"cash": 0, "gpp": 0}


@pytest.mark.parametrize("seed", [0, 1000, 1019, 1021, 1033])
def test_the_gpp_builds_are_found_whatever_the_simulation_draws_turn_out_to_be(edge, seed):
    """The ranking of this slate's stacked lineups is almost all ties (0-5 of 1,500 draws reach the target),
    so a different realisation of the draws reorders it, and that used to decide whether the last build was
    found. Five builds (no player in more than 3) and two (none shared) exist whichever seed is drawn."""
    five = run(edge, cash_builds=0, gpp_builds=5, seed=seed)
    two = run(edge, cash_builds=0, gpp_builds=2, seed=seed)
    problems = [n for n in five.notes + two.notes if "Ownership has not published" not in n]
    assert len(five.builds) == 5 and len(two.builds) == 2 and problems == [], problems
    for build in (*five.builds, *two.builds):
        assert_legal(build)


def test_asking_for_no_cash_or_no_gpp_builds_skips_that_search(edge):
    only_gpp = run(edge, cash_builds=0, gpp_builds=2)
    assert [b.kind for b in only_gpp.builds] == ["gpp", "gpp"] and [b.number for b in only_gpp.builds] == [
        1,
        2,
    ]
    only_cash = run(edge, cash_builds=2, gpp_builds=0)
    assert [b.kind for b in only_cash.builds] == ["cash", "cash"]


def test_a_gpp_build_is_never_a_lineup_of_nothing_but_cash_marked_players():
    ok = ls.not_all_cash({"a": "Cash", "b": "Cash", "c": "GPP", "d": "Both"})

    def players(*names):
        return [SimpleNamespace(name=n) for n in names]

    assert not ok(players("a", "b"))  # all Cash-marked
    assert ok(players("a", "c")) and ok(players("a", "d"))  # one GPP or Both player is enough
    assert not ok(players("a", "b", "an-unpooled-dst"))  # a defense nobody pooled is not marked GPP


# --- shapes, duplicates and the Top 1% share ----------------------------------------------------------------


def owned(edge, seed=1):
    """`edge` with a plausible ownership: popular where value is high, position totals as on a real slate."""
    rng = np.random.default_rng(seed)
    out = edge.copy()
    value = (out["CalPts"] / out["Salary"] * 1000) ** 3 * rng.uniform(0.6, 1.4, len(out))
    for pos, total in {"QB": 1.0, "RB": 2.2, "WR": 3.4, "TE": 1.1, "DST": 1.0}.items():
        mask = out["Position"] == pos
        out.loc[mask, "Own%"] = (value[mask] / value[mask].sum() * total).clip(upper=0.6)
    return out


@pytest.fixture(scope="module")
def owned_report(edge):
    return run(owned(edge))


def _cand(names_values, p_gpp, e_dups=None):
    players = tuple(SwapPlayer(n, "WR", "T", "U", "G", 5000, 10.0) for n in names_values)
    objective = None if e_dups is None else p_gpp / (1 + e_dups)
    return ls.Scored(players, 90.0, 45000, 0.5, p_gpp, 100.0, 130.0, (), None, None, e_dups, objective)


def test_the_gpp_objective_ranks_equal_top_1_percent_by_fewer_duplicates():
    unique, chalk, no_info = _cand("a", 0.02, 0.1), _cand("b", 0.02, 5.0), _cand("c", 0.02)
    ranked = sorted([chalk, unique], key=ls.gpp_key)
    assert [s.players[0].name for s in ranked] == ["a", "b"]
    # a higher Top 1% loses to enough extra copies; without ownership the plain chance decides
    assert ls.gpp_key(_cand("d", 0.021, 5.0)) > ls.gpp_key(_cand("e", 0.020, 0.1))
    assert no_info.gpp_value == pytest.approx(0.02)
    assert ls.gpp_key(_cand("f", 0.03)) < ls.gpp_key(_cand("g", 0.02))


def test_top1_ranks_by_the_chance_alone_and_share_discounts_the_duplicates():
    unique, chalk = _cand("a", 0.020, 0.1), _cand("b", 0.021, 5.0)
    assert [s.players[0].name for s in sorted([unique, chalk], key=ls.top1_key)] == ["b", "a"]
    assert [s.players[0].name for s in sorted([unique, chalk], key=ls.gpp_key)] == ["a", "b"]
    # with the chance tied, the projection decides under top1 (the duplicates are only shown)
    tied = [_cand("c", 0.02, 5.0), _cand("d", 0.02, 0.1)]
    assert ls.top1_key(tied[0])[:3] == ls.top1_key(tied[1])[:3]  # the chance, projection and salary tie


def test_the_default_rank_is_top1_and_an_unknown_rank_is_refused():
    assert ls.DEFAULT_RANK == "top1" and ls.RANKS == ("top1", "share")
    assert ls.rank_key("top1") is ls.top1_key and ls.rank_key("share") is ls.gpp_key
    with pytest.raises(ValueError, match="rank must be one of top1, share"):
        ls.rank_key("ev")


def test_a_gpp_run_defaults_to_top1_and_either_rank_still_reports_the_duplicates(edge, owned_report):
    explicit = run(owned(edge), rank="top1")
    assert [_build_key(b) for b in explicit.builds] == [_build_key(b) for b in owned_report.builds]
    shared = run(owned(edge), rank="share")
    for report in (explicit, shared):
        gpp = [b for b in report.builds if b.kind == "gpp"]
        assert gpp and all(b.e_dups is not None and b.objective is not None for b in gpp)
        for b in report.builds:
            assert_legal(b)
    # the same lineups are scored either way; only the order they are picked in can differ
    assert {b.p_cash for b in shared.builds if b.kind == "cash"} == {
        b.p_cash for b in explicit.builds if b.kind == "cash"
    }
    with pytest.raises(ValueError):
        run(owned(edge), rank="bogus")


def test_every_gpp_build_reports_sum_product_dupes_and_the_top_1_share(owned_report):
    from dfs.sim import dups

    edge = owned(slate())
    specs = build_specs(edge)
    from dfs.sim_inputs import ownership_by_name

    shares = ls.ownership_shares(specs, edge, None, "top")
    assert shares and sum(shares.values()) == pytest.approx(9.0)
    assert ownership_by_name(edge)  # the shares come from the edge's Own%
    gpp = [b for b in owned_report.builds if b.kind == "gpp"]
    assert gpp
    for b in gpp:
        own = dups.shares_for([p.name for p in b.players], shares)
        assert b.own_sum == pytest.approx(dups.ownership_sum(own))
        assert b.own_product == pytest.approx(dups.ownership_product(own))
        assert b.e_dups == pytest.approx(dups.DUP_MODEL_GPP.expected(own, dups.DEFAULT_GPP_ENTRIES))
        assert b.entries == dups.DEFAULT_GPP_ENTRIES
        assert b.objective == pytest.approx(b.p_gpp / (1 + b.e_dups))
        assert dups.dups_label(b.e_dups) in b.title and b.stack in b.title
        assert b.shapes


def test_the_contest_size_is_an_input(edge, owned_report):
    small = run(owned(edge), cash_builds=0, gpp_entries=800)
    base = [b for b in owned_report.builds if b.kind == "gpp"]
    assert {x.entries for x in small.builds} == {800} and {x.entries for x in base} == {1000}
    # the same lineup has proportionally more copies in a bigger field
    by_players = {tuple(p.name for p in b.players): b for b in base}
    shared = [b for b in small.builds if tuple(p.name for p in b.players) in by_players]
    assert shared
    for b in shared:
        assert by_players[tuple(p.name for p in b.players)].e_dups == pytest.approx(b.e_dups * 1000 / 800)


def test_without_ownership_the_gpp_objective_is_the_plain_top_1_percent(report):
    assert any("Ownership has not published" in n for n in report.notes)
    for b in (b for b in report.builds if b.kind == "gpp"):
        assert b.objective is None and b.e_dups is None and "dupes" not in b.title


def test_cash_builds_are_not_changed_by_ownership_only_read_out(edge, owned_report, report):
    plain = [b for b in report.builds if b.kind == "cash"]
    with_own = [b for b in owned_report.builds if b.kind == "cash"]
    assert [[p.name for p in b.players] for b in plain] == [[p.name for p in b.players] for b in with_own]
    assert [b.p_cash for b in plain] == [b.p_cash for b in with_own]
    assert all(b.e_dups is None for b in plain) and all(b.e_dups is not None for b in with_own)
    assert all(b.objective is None for b in with_own)  # nothing ranks on it


def test_a_cash_build_many_entries_share_gets_a_warning_and_is_still_built(edge):
    loud = run(owned(edge), gpp_builds=0, double_up_entries=5_000_000)
    assert len([b for b in loud.builds if b.kind == "cash"]) == 3  # never a filter
    assert any("identical entries" in n for n in loud.notes)
    quiet = run(owned(edge), gpp_builds=0, double_up_entries=10)
    assert not any("identical entries" in n for n in quiet.notes)


def test_the_candidates_cover_the_shapes_and_the_builds_are_tagged(edge, owned_report):
    seen = {s for b in owned_report.builds if b.kind == "gpp" for s in b.shapes}
    assert shapes_mod.STACK in seen and len(seen) > 1
    stack_only = run(owned(edge), cash_builds=0, shapes=(shapes_mod.STACK,))
    assert all(b.shapes and shapes_mod.STACK in b.shapes for b in stack_only.builds)


def test_the_shaped_builds_are_deterministic_under_a_seed(edge, owned_report):
    again = run(owned(edge))
    assert [_build_key(b) for b in again.builds] == [_build_key(b) for b in owned_report.builds]


def _build_key(b):
    return (b.title, [p.name for p in b.players], b.p_cash, b.p_gpp, b.shapes, b.e_dups, b.objective)


def test_the_shaped_search_stays_inside_the_time_budget(owned_report):
    assert owned_report.complete and owned_report.seconds < ls.TIME_LIMIT_SECONDS
