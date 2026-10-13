"""Suggested lineups (Week 6, slice 4): legality, diversity, the stack rule, the exposure cap, determinism and
the time budget, on a synthetic slate (no sheet, no network)."""

import time
from datetime import UTC, datetime
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

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


def test_the_defaults_are_three_cash_builds_and_five_gpp_builds(report):
    assert [b.kind for b in report.builds] == ["cash"] * 3 + ["gpp"] * 5
    assert [b.number for b in report.builds] == list(range(1, 9))
    assert report.complete and report.notes == []


def test_every_build_is_a_legal_lineup(report):
    for build in report.builds:
        assert_legal(build)


def test_every_build_differs_from_the_earlier_ones_of_its_kind_by_at_least_three_players(report):
    for kind in ("cash", "gpp"):
        group = [b for b in report.builds if b.kind == kind]
        for a, b in combinations(group, 2):
            assert len({p.name for p in a.players} - {p.name for p in b.players}) >= ls.MIN_DIFFERENCE


def test_every_gpp_build_holds_a_stack_and_its_title_names_it(report):
    for build in (b for b in report.builds if b.kind == "gpp"):
        qb = next(p for p in build.players if p.position == "QB")
        mates = [p for p in build.players if p.team == qb.team and p.position in ("WR", "TE")]
        backs = [p for p in build.players if p.team == qb.opp and p.position != "DST"]
        assert len(mates) >= 2 and len(backs) >= 1
        assert build.stack == f"{qb.team} stack + {qb.opp} bring-back" and build.stack in build.title


def test_no_player_is_in_more_than_sixty_percent_of_the_gpp_builds(report):
    gpp = [b for b in report.builds if b.kind == "gpp"]
    cap = ls.exposure_cap(len(gpp))
    assert cap == 3  # 60% of five builds
    uses: dict[str, int] = {}
    for build in gpp:
        for p in build.players:
            uses[p.name] = uses.get(p.name, 0) + 1
    assert max(uses.values()) <= cap


def test_cash_builds_are_ranked_by_cash_percent_and_gpp_builds_by_the_gpp_chance(report):
    cash = [b.p_cash for b in report.builds if b.kind == "cash"]
    assert cash == sorted(cash, reverse=True)
    gpp = [b.p_gpp for b in report.builds if b.kind == "gpp"]
    assert gpp == sorted(gpp, reverse=True)


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
    assert ls.pool_for(frame, {}, ls.CASH_TAGS, now=NOW, metric="CalPts") == []  # an empty pool


def test_a_player_with_no_calpts_is_projected_at_his_projpts():
    frame = slate()
    frame.loc[0, "CalPts"] = np.nan
    pool = {p.name: p for p in ls.pool_for(frame, tags_for(frame), ls.CASH_TAGS, now=NOW, metric="CalPts")}
    assert pool[frame.loc[0, "Name"]].value == float(frame.loc[0, "ProjPts"])


# --- the pieces --------------------------------------------------------------


def P(name, pos, team="A", opp="B", salary=5000, value=10.0, game="g"):
    return SwapPlayer(name, pos, team, opp, game, salary, value)


def test_is_stacked_needs_two_pass_catchers_and_one_opponent():
    qb = P("QB", "QB", "DET", "ARI")
    wr1, wr2, te = P("W1", "WR", "DET", "ARI"), P("W2", "WR", "DET", "ARI"), P("T", "TE", "DET", "ARI")
    back = P("Back", "WR", "ARI", "DET")
    assert (
        ls.is_stacked([qb, wr1, te, back])
        and ls.stack_name([qb, wr1, te, back]) == "DET stack + ARI bring-back"
    )
    assert not ls.is_stacked([qb, wr1, back])  # one pass catcher
    assert not ls.is_stacked([qb, wr1, wr2])  # no bring-back
    assert not ls.is_stacked([qb, wr1, wr2, P("D", "DST", "ARI", "DET")])  # a DST is not a bring-back
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
    totals = [sum(p.value for p in lu) for lu in lineups]
    assert totals == sorted(totals, reverse=True)


# --- when it cannot be done --------------------------------------------------


def test_a_pool_with_no_possible_stack_says_so_and_still_builds_the_cash_lineups(edge):
    # only one QB's team and nobody from his opponent in the GPP pool
    qb = edge[edge["Position"] == "QB"].iloc[0]
    tags = {name: "Cash" for name in edge["Name"]} | {
        n: "GPP" for n in edge.loc[(edge["Team"] == qb["Team"]) & (edge["Position"] != "DST"), "Name"]
    }
    report = run(edge, tags)
    assert [b.kind for b in report.builds].count("cash") == 3
    assert not [b for b in report.builds if b.kind == "gpp"]
    assert any("No stacked GPP lineup fits" in n for n in report.notes)


def test_an_empty_pool_builds_nothing_and_says_nothing_dishonest(edge):
    report = run(edge, {})
    assert report.builds == [] and report.candidates == {"cash": 0, "gpp": 0}


def test_asking_for_no_cash_or_no_gpp_builds_skips_that_search(edge):
    only_gpp = run(edge, cash_builds=0, gpp_builds=2)
    assert [b.kind for b in only_gpp.builds] == ["gpp", "gpp"] and [b.number for b in only_gpp.builds] == [
        1,
        2,
    ]
    only_cash = run(edge, cash_builds=2, gpp_builds=0)
    assert [b.kind for b in only_cash.builds] == ["cash", "cash"]
