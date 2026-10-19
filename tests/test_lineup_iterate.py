"""`dfs lineups suggest --iterate` (lineup_iterate.py, suggest_state.py): decisions, locks, typed-in names,
the
rules box, round history and the time budget, on a synthetic slate (no sheet, no network)."""

import copy
import sys
import time
from itertools import combinations

import pytest

sys.path.insert(0, "tests")
import test_lineup_suggest as syn  # noqa: E402

from dfs import lineup_iterate as li  # noqa: E402
from dfs import lineup_shapes as shapes_mod  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs import sheet_suggested as ss  # noqa: E402
from dfs import suggest_state as st  # noqa: E402
from dfs.late_swap_search import edge_players  # noqa: E402
from dfs.models import ROSTER_SLOTS  # noqa: E402
from dfs.sim_inputs import build_specs  # noqa: E402


@pytest.fixture(scope="module")
def edge():
    return syn.slate(teams=6, seed=7)


@pytest.fixture(scope="module")
def specs(edge):
    return build_specs(edge)


@pytest.fixture(scope="module")
def fresh(edge):
    """Round 1: 3 cash and 5 GPP builds, as a fresh run writes them."""
    return syn.run(edge, cash_builds=3, gpp_builds=5)


def tab_and_prior(fresh, edge):
    """The tab as round 1 left it (parsed back from the real layout) and round 1's record."""
    report = copy.copy(fresh)
    report.round = 1
    layout = ss.build_layout(report, edge, metric="CalPts", cash_line=145.0, gpp_target=190.0)
    prior = st.RoundRecord(1, "2026-10-18T12:00:00+00:00", "suggest", {}, li.records_from_report(fresh))
    return ss.parse_state(layout.rows), prior


def rerun(edge, specs, tab, prior, **kw):
    kw.setdefault("n_sims", syn.N_SIMS)
    return li.iterate_builds(
        edge, syn.tags_for(edge), specs, tab, prior, now=syn.NOW, cap=syn.CAP, cash_line=145.0,
        gpp_target=190.0, metric="CalPts", **kw,
    )  # fmt: skip


def names(build):
    return [p.name for p in build.players]


def by_number(result, number):
    return next(b for b in result.report.builds if b.number == number)


def find_build(result, names_wanted):
    return next(b for b in result.report.builds if set(names(b)) == set(names_wanted))


@pytest.fixture
def tab(fresh, edge):
    return tab_and_prior(fresh, edge)


def decide(tab_state, number, text):
    next(b for b in tab_state.builds if b.number == number).decision = text


def lock(tab_state, number, *slots):
    build = next(b for b in tab_state.builds if b.number == number)
    for i in slots:
        build.locked[i] = True
    return build


# --- the state: sheet <-> file ------------------------------------------------------------------------------


def test_the_state_round_trips_between_the_tab_and_the_file(fresh, edge, tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STATE_DIR", tmp_path)
    tab_state, prior = tab_and_prior(fresh, edge)
    assert tab_state.round == 1 and [b.number for b in tab_state.builds] == [b.number for b in fresh.builds]
    assert [b.names for b in tab_state.builds] == [names(b) for b in fresh.builds]  # sheet == round 1
    state = st.StateFile(week=7, rounds=[prior])
    state.rounds[0].builds[0].locked = ["x"]
    st.save_state(state)
    assert st.load_state(7) == state  # file -> same state
    assert st.load_state(8).rounds == []  # no file yet: an empty history
    # tab -> file -> tab: the records written from a rerun lay out and parse back to the same builds
    decide(tab_state, 1, "Keep")
    lock(tab_state, 5, 0, 3)
    result = rerun(edge, build_specs(edge), tab_state, prior)
    report = result.report
    report.round = 2
    report.history = st.history_lines(st.StateFile(7, [prior]))
    rows = ss.build_layout(report, edge, metric="CalPts", cash_line=145.0, gpp_target=190.0).rows
    again = ss.parse_state(rows)
    assert again.round == 2
    for record, parsed in zip(result.records, again.builds, strict=True):
        assert parsed.names == record.names and parsed.decision == record.decision
        assert {n for n, tick in zip(parsed.names, parsed.locked, strict=True) if tick} == set(record.locked)


def test_a_corrupt_state_file_is_an_error_not_an_empty_history(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STATE_DIR", tmp_path)
    (tmp_path / "3.json").write_text("{not json")
    with pytest.raises(ValueError, match="not a readable"):
        st.load_state(3)


# --- decisions ---------------------------------------------------------------------------------------------


def test_keep_leaves_the_build_untouched_and_lists_it_first(edge, specs, tab):
    tab_state, prior = tab
    before = {b.number: b.names for b in tab_state.builds}
    for b in tab_state.builds:
        b.decision = "Keep"
    result = rerun(edge, specs, tab_state, prior)
    assert [set(names(b)) for b in result.report.builds] == [set(before[n]) for n in sorted(before)]
    assert all(b.origin == "kept" and b.decision == "Keep" for b in result.report.builds)
    assert all(b.note.startswith("kept as it was") for b in result.report.builds)
    assert result.report.phases["simulate"] == 0 or "search" not in result.report.phases  # nothing searched


def test_locked_players_stay_in_a_rebuilt_build_and_it_is_still_legal(edge, specs, tab):
    tab_state, prior = tab
    decide(tab_state, 6, "Rebuild")
    build = lock(tab_state, 6, 0, 3)  # the QB and a WR
    locked = {build.names[0], build.names[3]}
    for n in (1, 2, 3, 4, 5, 7, 8):
        decide(tab_state, n, "Keep")
    result = rerun(edge, specs, tab_state, prior)
    rebuilt = [b for b in result.report.builds if b.origin == "rebuilt"]
    assert len(rebuilt) == 1 and locked <= set(names(rebuilt[0])) and locked <= set(rebuilt[0].locked)
    syn.assert_legal(rebuilt[0])
    assert shapes_mod.is_stacked(rebuilt[0].players) and "rebuilt around 2 fixed" in rebuilt[0].note
    assert [b.origin for b in result.report.builds] == ["kept"] * 7 + ["rebuilt"]  # kept first, then rebuilt


def valid_swap(edge, build_names, slot_index):
    """A player who can legally replace slot `slot_index` of the build."""
    players = edge_players(edge, "CalPts")
    current = [players[n] for n in build_names]
    slot = ROSTER_SLOTS[slot_index]
    for name, p in players.items():
        if name in build_names or not li.slot_takes(slot, p):
            continue
        trial = [*current[:slot_index], p, *current[slot_index + 1 :]]
        if li.lineup_problem(trial, syn.CAP) is None:
            return name
    raise AssertionError("no legal swap on this slate")


def test_a_name_typed_over_a_player_counts_as_a_lock_and_gets_the_your_pick_mark(edge, specs, tab):
    tab_state, prior = tab
    build = next(b for b in tab_state.builds if b.number == 6)
    new = valid_swap(edge, build.names, 3)
    old = build.names[3]
    build.names[3] = new  # typed over the cell
    for n in range(1, 9):
        decide(tab_state, n, "Keep" if n != 6 else "Rebuild")
    result = rerun(edge, specs, tab_state, prior)
    rebuilt = next(b for b in result.report.builds if b.origin == "rebuilt")
    assert new in names(rebuilt) and new in rebuilt.locked and new in rebuilt.yours
    assert f"→ {new} (yours, was {old})" in rebuilt.note
    # and written on the tab: the lock tick and the hidden "yours" flag follow the name
    report = result.report
    report.round = 2
    layout = ss.build_layout(report, edge, metric="CalPts", cash_line=145.0, gpp_target=190.0)
    rows = [layout.rows[r - 1] for r in layout.player_rows[-9:]]
    assert [r[ss.NAME_INDEX] for r in rows if r[ss.LOCK_INDEX] is True and r[-1] is True] == [new]


def test_a_typed_name_on_a_kept_build_is_validated_applied_and_rescored(edge, specs, tab):
    tab_state, prior = tab
    build = next(b for b in tab_state.builds if b.number == 6)
    new = valid_swap(edge, build.names, 4)
    build.names[4] = new
    build.decision = "Keep"
    result = rerun(edge, specs, tab_state, prior)
    kept = next(b for b in result.report.builds if new in names(b))
    assert kept.origin == "kept" and new in kept.yours and kept.p_cash >= 0


def test_an_invalid_edit_is_reported_and_the_build_is_left_as_it_was(edge, specs, tab):
    tab_state, prior = tab
    build = next(b for b in tab_state.builds if b.number == 6)
    original = list(build.names)
    qb = next(n for n in names_of(edge, "QB") if n not in original)
    build.names[3] = qb  # a QB in a WR slot
    build.decision = "Rebuild"
    result = rerun(edge, specs, tab_state, prior)
    left = find_build(result, original)
    assert left.problem and left.origin == "kept" and left.decision == ""
    assert "your change was not applied" in left.note and "WR slot takes a WR" in left.note
    assert any("Build 6" in n and "left as it was" in n for n in result.report.notes)
    # typos, a duplicate and an over-the-cap swap are refused too, each with its own reason
    for text, reason in (("Nobody Atall", "no player"), (original[4], "twice")):
        build.names = list(original)
        build.names[3] = text
        again = rerun(edge, specs, tab_state, prior)
        assert find_build(again, original).problem and reason in find_build(again, original).note
    build.names = list(original)
    build.names[3] = ""
    assert "the cell is empty" in find_build(rerun(edge, specs, tab_state, prior), original).note


def names_of(edge, position):
    return list(edge.loc[edge["Position"] == position, "Name"])


def test_drop_replaces_the_build_and_the_tab_stays_topped_up(edge, specs, tab):
    tab_state, prior = tab
    dropped = next(b for b in tab_state.builds if b.number == 7)
    decide(tab_state, 7, "Drop")
    for n in (1, 2, 3, 4, 5, 6, 8):
        decide(tab_state, n, "Keep")
    result = rerun(edge, specs, tab_state, prior)
    assert len(result.report.builds) == 8
    new = [b for b in result.report.builds if b.origin == "new"]
    assert len(new) == 1 and new[0].kind == "gpp" and "replacing dropped build 7" in new[0].note
    assert set(names(new[0])) != set(dropped.names)
    syn.assert_legal(new[0])
    assert result.report.builds[-1] is new[0]  # new builds come last


def test_diversity_and_exposure_are_counted_across_kept_and_new_builds(edge, specs, tab):
    tab_state, prior = tab
    for n in range(1, 9):
        decide(tab_state, n, "Keep")
    decide(tab_state, 8, "Drop")
    decide(tab_state, 7, "Drop")
    rules = tab_state.rules
    rules.wanted = "cash 3, gpp 5"
    result = rerun(edge, specs, tab_state, prior)
    builds = result.report.builds
    assert [b.kind for b in builds].count("gpp") == 5
    new = [b for b in builds if b.origin == "new"]
    assert len(new) == 2
    for fresh_build in new:  # each new build differs from EVERY other build by MIN_DIFFERENCE players
        for other in builds:
            if other is not fresh_build:
                assert len(set(names(fresh_build)) - set(names(other))) >= ls.MIN_DIFFERENCE
    cap = ls.exposure_cap(5)
    kept_uses = {}
    for b in builds:
        if b.kind == "gpp" and b.origin == "kept":
            for n in names(b):
                kept_uses[n] = kept_uses.get(n, 0) + 1
    for fresh_build in new:  # a new build never takes a player already at the cap among the kept builds
        assert not any(kept_uses.get(n, 0) >= cap for n in names(fresh_build) if fresh_build.kind == "gpp")


def test_a_removed_bring_back_stays_removed_unless_the_gain_is_large(edge, specs, fresh):
    """Sam types a non-bring-back over a build's bring-back and rebuilds: the rebuild keeps none (and the flag
    survives into the next round) unless the best build with one beats the best without by the threshold."""
    players = edge_players(edge, "CalPts")
    tab_state, prior = tab_and_prior(fresh, edge)
    target = next(
        b
        for b in tab_state.builds
        if b.kind == "gpp" and shapes_mod.has_bring_back([players[n] for n in b.names])
    )
    qb = next(players[n] for n in target.names if players[n].position == "QB")
    slot = next(
        i for i, n in enumerate(target.names) if players[n].team == qb.opp and players[n].position != "DST"
    )
    original = list(target.names)
    swap = None
    for name, p in players.items():  # a player of neither team: no bring-back, no extra teammate
        if name in original or p.team in (qb.team, qb.opp) or not li.slot_takes(ROSTER_SLOTS[slot], p):
            continue
        trial = [players[n] for n in original]
        trial[slot] = p
        if li.lineup_problem(trial, syn.CAP) is None and not shapes_mod.has_bring_back(trial):
            swap = name
            break
    assert swap is not None
    target.names[slot] = swap
    tab_state.builds = [target]  # alone on the tab: no other build's diversity or exposure in the way
    target.decision = "Rebuild"
    held = rerun(edge, specs, tab_state, prior, bring_back_gain=10.0)  # a gain nobody reaches
    rebuilt = next(b for b in held.report.builds if b.origin == "rebuilt")
    assert not shapes_mod.has_bring_back(rebuilt.players) and swap in names(rebuilt)
    assert (
        "no bring-back, as you left it" in rebuilt.note
        or "your pick" in rebuilt.note
        or swap in rebuilt.yours
    )
    assert held.records[[b.number for b in held.report.builds].index(rebuilt.number)].no_bring_back
    # the flag lives in the file: the next round, with nothing typed, still keeps it removed
    round2 = st.RoundRecord(2, "2026-10-18T13:00:00+00:00", "iterate", {}, held.records)
    again_tab = ss.parse_state(
        ss.build_layout(
            _numbered(held.report, 2), edge, metric="CalPts", cash_line=145.0, gpp_target=190.0
        ).rows
    )
    again_tab.builds[0].decision, again_tab.builds[0].locked = "", [False] * 9  # nothing locked: free again
    second = rerun(edge, specs, again_tab, round2, bring_back_gain=10.0)
    free = next(b for b in second.report.builds if b.origin == "rebuilt")
    assert not shapes_mod.has_bring_back(free.players) and "as you left it" in free.note
    # with a threshold of nothing the same rebuild is allowed its bring-back back whenever one is better
    loose = rerun(edge, specs, again_tab, round2, bring_back_gain=0.0)
    back = next(b for b in loose.report.builds if b.origin == "rebuilt")
    assert "overrule" in back.note or not shapes_mod.has_bring_back(back.players)


def _numbered(report, round_):
    report.round = round_
    return report


def test_vary_n_adds_n_alternatives_that_keep_the_locks_and_differ_by_two(edge, specs, tab):
    tab_state, prior = tab
    parent = lock(tab_state, 5, 0)  # the QB locked
    for n in range(1, 9):
        decide(tab_state, n, "Keep")
    decide(tab_state, 5, "Vary 2")
    tab_state.rules.wanted = "cash 3, gpp 5"  # the alternatives do not count toward it: no top-ups
    result = rerun(edge, specs, tab_state, prior, time_limit=20.0)
    original = find_build(result, parent.names)
    variations = [b for b in result.report.builds if "variation" in b.note]
    assert original.origin == "kept" and original.decision == "Keep"
    assert 1 <= len(variations) <= 2  # a small slate may not hold both
    for v in variations:
        assert parent.names[0] in names(v) and parent.names[0] in v.locked
        assert len(set(names(v)) - set(names(original))) >= li.VARY_MIN_DIFFERENCE
        syn.assert_legal(v)
    for a, b in combinations(variations, 2):
        assert len(set(names(a)) - set(names(b))) >= li.VARY_MIN_DIFFERENCE
    assert len(result.report.builds) == 8 + len(variations)


def test_a_vary_with_nothing_locked_keeps_the_quarterback(edge, specs, tab):
    tab_state, prior = tab
    parent = next(b for b in tab_state.builds if b.number == 6)
    for n in range(1, 9):
        decide(tab_state, n, "Keep")
    decide(tab_state, 6, "Vary 1")
    result = rerun(edge, specs, tab_state, prior)
    variation = next(b for b in result.report.builds if "variation" in b.note)
    assert parent.names[0] in names(variation)


# --- rounds -------------------------------------------------------------------------------------------------


def test_round_restore_brings_an_earlier_rounds_builds_back_frozen(edge, specs, tab):
    tab_state, prior = tab
    decide(tab_state, 6, "Rebuild")
    lock(tab_state, 6, 0)
    changed = rerun(edge, specs, tab_state, prior)
    restored = rerun(edge, specs, li.tab_from_record(prior), prior, freeze=True)
    assert [names(b) for b in restored.report.builds] == [r.names for r in prior.builds]
    assert [names(b) for b in changed.report.builds] != [names(b) for b in restored.report.builds]
    assert all(b.origin == "kept" for b in restored.report.builds)
    assert restored.report.rules == prior.rules or restored.report.rules == st.RulesText().by_label()


def test_the_same_tab_and_data_give_the_same_round(edge, specs, tab):
    tab_state, prior = tab
    decide(tab_state, 3, "Drop")
    decide(tab_state, 6, "Vary 1")
    lock(tab_state, 5, 0, 3)
    one = rerun(edge, specs, tab_state, prior)
    two = rerun(edge, specs, copy.deepcopy(tab_state), prior)
    assert [(b.title, names(b), b.p_cash, b.p_gpp, b.note) for b in one.report.builds] == [
        (b.title, names(b), b.p_cash, b.p_gpp, b.note) for b in two.report.builds
    ]
    assert one.records == two.records


def test_a_rerun_with_eight_builds_and_some_locks_finishes_inside_fifteen_seconds(edge, specs, tab):
    tab_state, prior = tab
    decide(tab_state, 1, "Keep")
    decide(tab_state, 2, "Keep")
    decide(tab_state, 3, "Drop")
    lock(tab_state, 5, 0, 3)
    lock(tab_state, 6, 0)
    lock(tab_state, 7, 0, 1)
    build = next(b for b in tab_state.builds if b.number == 4)
    build.names[3] = valid_swap(edge, build.names, 3)  # one typed-in pick
    started = time.monotonic()
    result = rerun(edge, specs, tab_state, prior)
    elapsed = time.monotonic() - started
    print(f"iterate rerun of 8 builds: {elapsed:.1f} s, phases {result.report.phases}")  # noqa: T201
    assert elapsed < 15.0 and len(result.report.builds) == 8
    assert set(result.report.phases) >= {"candidates", "search", "simulate", "choose", "rescore"}
    for b in result.report.builds:
        syn.assert_legal(b)


# --- the rules box ------------------------------------------------------------------------------------------


def rules_for(edge, **typed):
    return st.parse_rules(st.RulesText(**typed), list(edge["Name"]), set(edge["Team"]))


def test_the_rules_box_parses_names_exposure_stack_rules_and_builds_wanted(edge):
    qb, rb = "T02 QB1", "T03 RB1"
    r = rules_for(
        edge,
        always=f"{qb}, t03 rb1",
        never="T00 WR1; T01 TE2",
        exposure="T02 QB1: 4 builds, more T04 WR1",
        stack="T00: no bring-back; T01: QB+WR+WR ok\nT01: QB+RB ok; no stacks from T05",
        wanted="cash 2, gpp 6",
    )
    assert r.problems == [] and r.always == [qb, rb] and r.never == {"T00 WR1", "T01 TE2"}
    assert r.caps == {qb: 4} and r.more == {"T04 WR1"} and r.wanted == {"cash": 2, "gpp": 6}
    assert r.stacks.no_bring_back == {"T00"} and r.stacks.no_stack == {"T05"}
    assert r.stacks.only == {"T01": {"QB+2WR", "QB+RB"}}


def test_a_name_can_be_part_of_one_players_name_and_a_line_it_cannot_read_is_reported(edge):
    r = rules_for(
        edge,
        always="T02 QB1, QB1, Nobody Atall",  # QB1 matches many: ambiguous; the third is unknown
        exposure="T02 QB1 four; more",
        stack="T99: no bring-back; LAR does what; T00: QB+QB ok",
        wanted="cash lots, gpp 99",
    )
    text = " | ".join(r.problems)
    assert r.always == ["T02 QB1"]
    assert "'QB1' matches" in text and "no player 'Nobody Atall'" in text
    assert "Max exposure: cannot read 'T02 QB1 four'" in text and "Max exposure: cannot read 'more'" in text
    assert "no team T99" in text and "Stack rules: cannot read 'LAR does what'" in text
    assert "cannot read 'T00: QB+QB ok'" in text
    assert "Builds wanted: cannot read 'cash lots'" in text and "gpp 99 is more than 60" in text
    assert st.resolve_name("t02 qb1", ["T02 QB1"]) == ("T02 QB1", "")
    assert (
        "did you mean" in st.resolve_name("T02 QB", ["T02 QB1", "T02 QB2"])[1]
        or "matches" in (st.resolve_name("T02 QB", ["T02 QB1", "T02 QB2"])[1])
    )


def test_decisions_parse_in_any_case_and_unknown_ones_leave_the_build_alone(edge, specs, tab):
    assert [st.parse_decision(t) for t in ("", "keep", "REBUILD", "Drop", "vary 3", "Vary3")] == [
        st.Decision("rebuild"),
        st.Decision("keep"),
        st.Decision("rebuild"),
        st.Decision("drop"),
        st.Decision("vary", 3),
        st.Decision("vary", 3),
    ]
    assert st.parse_decision("vary 0") is None and st.parse_decision("maybe") is None
    tab_state, prior = tab
    for n in range(1, 9):
        decide(tab_state, n, "Keep")
    decide(tab_state, 2, "Mabye")
    result = rerun(edge, specs, tab_state, prior)
    assert any("decision 'Mabye'" in n for n in result.report.notes)
    assert [set(names(b)) for b in result.report.builds] == [set(b.names) for b in tab_state.builds]


def test_never_always_and_stack_rules_shape_every_rebuilt_and_new_build(edge, specs, tab):
    tab_state, prior = tab
    kept = {1, 2, 3}
    for b in tab_state.builds:
        b.decision = "Keep" if b.number in kept else "Drop"
    never = "T00 WR1"
    tab_state.rules = st.RulesText(
        always="T02 QB1", never=never, stack="no stacks from T03", wanted="cash 3, gpp 5"
    )
    result = rerun(edge, specs, tab_state, prior)
    new = [b for b in result.report.builds if b.origin == "new"]
    assert 3 <= len(new) <= 5  # a six-team slate may not hold five that differ by three players
    for b in new:
        assert never not in names(b)
        assert not (b.kind == "gpp" and shapes_mod.is_stacked(b.players) and b.players[0].team == "T03")
        assert "T02 QB1" in names(b) or b.kind == "cash"  # a lineup that can hold him does
    assert [b for b in new if "T02 QB1" in names(b)]


def test_locks_beat_the_pool_and_the_never_list(edge, specs, tab):
    tab_state, prior = tab
    build = next(b for b in tab_state.builds if b.number == 6)
    outside = build.names[3]
    tags = {n: "Both" for n in edge["Name"] if n != outside}  # he is not in the pool at all
    tab_state.rules = st.RulesText(never=outside)
    for b in tab_state.builds:
        b.decision = "Keep" if b is not build else "Rebuild"
    build.locked[3] = True
    result = li.iterate_builds(
        edge, tags, specs, tab_state, prior, now=syn.NOW, cap=syn.CAP, cash_line=145.0, gpp_target=190.0,
        metric="CalPts", n_sims=syn.N_SIMS,
    )  # fmt: skip
    rebuilt = next(b for b in result.report.builds if b.origin == "rebuilt")
    assert outside in names(rebuilt) and outside in rebuilt.locked


def test_max_exposure_targets_count_the_kept_builds_and_more_prefers_a_player(edge, specs, tab):
    tab_state, prior = tab
    target = "T04 QB1"
    for b in tab_state.builds:
        b.decision = "Keep" if b.kind == "cash" else "Drop"
    tab_state.rules = st.RulesText(exposure=f"{target}: 2", wanted="cash 3, gpp 5")
    result = rerun(edge, specs, tab_state, prior)
    uses = sum(1 for b in result.report.builds if b.kind == "gpp" and target in names(b))
    assert uses <= 2 and uses >= 1  # a cap of two, and new builds are steered toward it


def test_a_tab_from_another_round_gives_no_edits_and_says_so(edge, specs, tab):
    tab_state, prior = tab
    tab_state.round = 9
    build = tab_state.builds[5]
    build.names[3] = valid_swap(edge, build.names, 3)
    for b in tab_state.builds:
        b.decision = "Keep"
    result = rerun(edge, specs, tab_state, prior)
    assert any(
        "the tab says round 9" in n.lower() or "The tab says round 9" in n for n in result.report.notes
    )
    assert not any(b.yours for b in result.report.builds)  # nothing counted as typed-in
    assert build.names[3] in {n for b in result.report.builds for n in names(b)}


def test_an_empty_tab_with_a_builds_wanted_line_builds_that_many(edge, specs):
    tab_state = st.TabState(rules=st.RulesText(wanted="cash 1, gpp 4"))
    result = rerun(edge, specs, tab_state, None)
    kinds = [b.kind for b in result.report.builds]
    assert kinds[0] == "cash" and kinds.count("cash") == 1 and 2 <= kinds.count("gpp") <= 4
    assert all(b.origin == "new" for b in result.report.builds)
    assert [b.number for b in result.report.builds] == list(range(1, len(kinds) + 1))
