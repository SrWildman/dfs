"""Your projections and builder rules (my_frame.py, the Adjust / Ownership / Build rules lines): parsing, the
frame transform, builds that change because of an adjustment, min unique and max from one game honoured, late
swap and the simulator reading MyPts, the Cockpit line, and Model Check left alone. Synthetic slates only."""

import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, "tests")
import test_late_swap_cli as lsc  # noqa: E402
import test_lineup_suggest as syn  # noqa: E402

from dfs import cli, cockpit, my_frame  # noqa: E402
from dfs import lineup_iterate as li  # noqa: E402
from dfs import suggest_state as st  # noqa: E402
from dfs.late_swap_sim import SimSettings  # noqa: E402
from dfs.my_frame import ADD, PERCENT, SET, Adjustment  # noqa: E402
from dfs.sheet_suggested import NAME_INDEX  # noqa: E402
from dfs.sim_inputs import build_specs, ownership_by_name  # noqa: E402


@pytest.fixture(scope="module")
def edge():
    frame = syn.slate(teams=6, seed=7)
    frame["Own%"] = [0.02 + 0.01 * (i % 10) for i in range(len(frame))]  # ownership has published
    return frame


def rules_for(edge, **typed):
    return st.parse_rules(st.RulesText(**typed), list(edge["Name"]), set(edge["Team"]))


def pts(frame, name, column="CalPts"):
    return float(frame.loc[frame["Name"] == name, column].iloc[0])


# --- parsing ---------------------------------------------------------------------------------------


def test_adjust_ownership_and_build_rules_parse(edge):
    r = rules_for(
        edge,
        adjust="t02 wr1 +2, T03 RB1 -15%; T04 QB1 24\nT01 TE2: +1.5",
        ownership="T03 RB1 40%; T02 WR1: 12.5 %",
        build="min unique 2; max from one game 5",
    )
    assert r.problems == []
    assert r.adjust == {
        "T02 WR1": Adjustment(ADD, 2),
        "T03 RB1": Adjustment(PERCENT, -15),
        "T04 QB1": Adjustment(SET, 24),
        "T01 TE2": Adjustment(ADD, 1.5),
    }
    assert r.ownership == {"T03 RB1": 0.40, "T02 WR1": 0.125}
    assert (r.min_unique, r.max_per_game) == (2, 5)


def test_lines_it_cannot_read_are_reported_never_guessed(edge):
    r = rules_for(
        edge,
        adjust="T03 RB1 15%, Nobody Atall +2, T02 QB1 plus two, T04 QB1 +",
        ownership="T03 RB1 40, T02 WR1 140%, T01 TE2 forty%",
        build="min unique 0; max from one game 1; max from one game; bring-back 2",
    )
    text = " | ".join(r.problems)
    assert r.adjust == {} and r.ownership == {} and r.min_unique is None and r.max_per_game is None
    assert "Adjust: cannot read 'T03 RB1 15%'" in text and "needs its + or -" in text
    assert "no player 'Nobody Atall'" in text and "cannot read 'T02 QB1 plus two'" in text
    assert "Ownership: cannot read 'T03 RB1 40'" in text and "140% is more than 100%" in text
    assert "min unique must be 1 to 8" in text and "max from one game must be 2 to 9" in text
    assert "cannot read 'max from one game'" in text and "cannot read 'bring-back 2'" in text


def test_the_box_has_the_three_new_lines_and_a_state_file_without_them_still_loads():
    assert {"Adjust", "Ownership", "Build rules"} <= set(st.RULE_LABELS)
    old = st.RulesText.from_labels({"Always use": "Bijan"})  # a round saved before these lines existed
    assert old.always == "Bijan" and old.adjust == old.ownership == old.build == ""


# --- the frame transform ---------------------------------------------------------------------------


def test_the_frame_adjusts_by_points_percent_or_a_set_value_and_never_below_zero(edge):
    adj = {
        "T00 WR1": Adjustment(ADD, 2),
        "T00 WR2": Adjustment(PERCENT, -15),
        "T00 WR3": Adjustment(SET, 24),
        "T00 TE1": Adjustment(ADD, -100),
    }
    before = edge.copy()
    frame = my_frame.apply(edge, adj, {})
    mine = frame.edge
    assert pts(mine, "T00 WR1") == pytest.approx(pts(edge, "T00 WR1") + 2)
    assert pts(mine, "T00 WR2") == pytest.approx(pts(edge, "T00 WR2") * 0.85)
    assert pts(mine, "T00 WR3") == 24 and pts(mine, "T00 TE1") == 0
    assert pts(mine, "T00 WR1", "MyPts") == pts(mine, "T00 WR1")
    assert pts(mine, "T01 WR1") == pts(edge, "T01 WR1") == pts(mine, "T01 WR1", "MyPts")  # untouched
    pd.testing.assert_frame_equal(edge, before)  # the raw slate is never changed
    assert frame.lines() == ["T00 TE1 -100", "T00 WR1 +2", "T00 WR2 -15%", "T00 WR3 24"]


def test_a_player_with_no_calpts_is_adjusted_from_his_projpts(edge):
    blank = edge.copy()
    blank.loc[blank["Name"] == "T00 WR1", "CalPts"] = float("nan")
    mine = my_frame.apply(blank, {"T00 WR1": Adjustment(ADD, 2)}, {}).edge
    assert pts(mine, "T00 WR1") == pytest.approx(pts(blank, "T00 WR1", "ProjPts") + 2)


def test_the_run_metric_is_adjusted_too(edge):
    mine = my_frame.apply(edge, {"T00 WR1": Adjustment(ADD, 3)}, {}, "ProjPts").edge
    assert pts(mine, "T00 WR1", "ProjPts") == pytest.approx(pts(edge, "T00 WR1", "ProjPts") + 3)


def test_what_cannot_be_adjusted_is_left_out_and_said(edge):
    blank = edge.copy()
    blank.loc[blank["Name"] == "T00 WR1", ["CalPts", "ProjPts"]] = float("nan")
    frame = my_frame.apply(
        blank, {"T00 WR1": Adjustment(ADD, 2), "T00 WR2": Adjustment(SET, 9), "Ghost": Adjustment(ADD, 1)}, {}
    )
    assert set(frame.adjust) == {"T00 WR2"}
    assert any("T00 WR1 has no projection to adjust" in n for n in frame.notes)
    assert any("Ghost is not on the slate" in n for n in frame.notes)


def test_ownership_overrides_own_pct_and_waits_for_the_slates_ownership_to_publish(edge):
    frame = my_frame.apply(edge, {}, {"T02 RB1": 0.40})
    assert ownership_by_name(frame.edge)["T02 RB1"] == 0.40
    assert ownership_by_name(frame.edge)["T02 RB2"] == ownership_by_name(edge)["T02 RB2"]
    unpublished = edge.assign(**{"Own%": 0.0}).copy()
    unpublished["Own%"] = float("nan")
    waiting = my_frame.apply(unpublished, {}, {"T02 RB1": 0.40})
    assert not waiting.ownership and any("has not published" in n for n in waiting.notes)
    assert waiting.edge["Own%"].isna().all()  # one player's share is not a field


def test_with_nothing_typed_the_frame_equals_the_slate(edge):
    frame = my_frame.apply(edge, {}, {})
    assert not frame and frame.lines() == []
    pd.testing.assert_frame_equal(frame.edge.drop(columns="MyPts"), edge)


def test_frame_from_rules_reports_only_the_lines_it_applies():
    frame_edge = syn.slate(teams=4, seed=2)
    text = st.RulesText(adjust="T00 WR1 +2, T00 WR2 15%", build="min unique x", never="Nobody")
    frame, notes = st.frame_from_rules(text, frame_edge)
    assert list(frame.adjust) == ["T00 WR1"]
    assert notes and all(n.startswith("Adjust") for n in notes)  # not the Never / Build rules problems


# --- builds ----------------------------------------------------------------------------------------


def cash_names(edge):
    return {p.name for p in syn.run(edge, cash_builds=1, gpp_builds=0).builds[0].players}


def test_a_bump_pulls_a_player_into_a_build_and_a_cut_drops_him(edge):
    base = syn.run(edge, cash_builds=1, gpp_builds=0).builds[0]
    held = next(p.name for p in base.players if p.position == "WR")
    out = next(
        p for p in edge.sort_values("CalPts")["Name"] if p not in {q.name for q in base.players} and "WR" in p
    )
    cut = my_frame.apply(edge, {held: Adjustment(SET, 0)}, {}).edge
    assert held not in cash_names(cut)
    bumped = my_frame.apply(edge, {out: Adjustment(ADD, 30)}, {}).edge
    assert out in cash_names(bumped) and out not in {p.name for p in base.players}
    assert base.projection != syn.run(bumped, cash_builds=1, gpp_builds=0).builds[0].projection


def run_iterate(edge, rules_text, **kw):
    frame, _ = st.frame_from_rules(rules_text, edge)
    tab = st.TabState(rules=rules_text)
    return li.iterate_builds(
        frame.edge, syn.tags_for(edge), build_specs(frame.edge), tab, None, now=syn.NOW, cap=syn.CAP,
        cash_line=145.0, gpp_target=190.0, metric="CalPts", n_sims=syn.N_SIMS, **kw,
    )  # fmt: skip


def test_a_rerun_says_which_builds_hold_your_numbers(edge):
    base = run_iterate(edge, st.RulesText(wanted="cash 2, gpp 2"))
    star = base.report.builds[0].players[0].name
    text = st.RulesText(wanted="cash 2, gpp 2", adjust=f"{star} +5", ownership=f"{star} 30%")
    result = run_iterate(edge, text)
    holding = [b for b in result.report.builds if star in [p.name for p in b.players]]
    assert holding
    for b in holding:
        assert f"{star} +5 (yours)" in b.note and f"{star} 30% owned (yours)" in b.note
    for b in result.report.builds:
        if b not in holding:
            assert "(yours)" not in b.note


def game_of(edge):
    return dict(zip(edge["Name"], edge["GameID"], strict=True))


def test_min_unique_is_honoured_and_never_relaxed(edge):
    result = run_iterate(edge, st.RulesText(wanted="cash 0, gpp 6", build="min unique 5"))
    builds = result.report.builds
    assert len(builds) >= 2
    for a, b in combinations(builds, 2):
        assert len({p.name for p in a.players} ^ {p.name for p in b.players}) / 2 >= 5
    assert not any("fewer players than usual" in b.note for b in builds)


def test_min_unique_can_loosen_the_default_of_three(edge):
    loose = run_iterate(edge, st.RulesText(wanted="cash 0, gpp 8", build="min unique 1"))
    strict = run_iterate(edge, st.RulesText(wanted="cash 0, gpp 8", build="min unique 6"))
    assert len(loose.report.builds) >= len(strict.report.builds)


def test_max_from_one_game_holds_in_every_new_build(edge):
    free = run_iterate(edge, st.RulesText(wanted="cash 2, gpp 4"))
    games = game_of(edge)
    worst = max(max(Counter(games[p.name] for p in b.players).values()) for b in free.report.builds)
    assert worst >= 4  # the unconstrained builds do stack a game: the rule has something to do
    capped = run_iterate(edge, st.RulesText(wanted="cash 2, gpp 4", build="max from one game 3"))
    assert capped.report.builds
    for b in capped.report.builds:
        assert max(Counter(games[p.name] for p in b.players).values()) <= 3
    assert li.most_from_one_game(capped.report.builds[0].players) <= 3


# --- the simulator, late swap, Cockpit, Model Check ------------------------------------------------


def test_the_simulator_reads_mypts_and_my_ownership(edge):
    frame = my_frame.apply(edge, {"T00 WR1": Adjustment(ADD, 4)}, {"T00 WR1": 0.5})
    spec = build_specs(frame.edge)["T00 WR1"]
    assert spec.projection == pytest.approx(pts(edge, "T00 WR1") + 4)
    assert build_specs(edge)["T00 WR1"].projection == pytest.approx(pts(edge, "T00 WR1"))


class FrameClient(lsc.StubClient):
    """The late-swap stub sheet with a Suggested tab whose rules box holds `adjust`."""

    def __init__(self, lineup, pool, adjust):
        super().__init__(lineup, pool)
        self.grid = [["Adjust", *[""] * (NAME_INDEX - 1), adjust]]

    def tab_exists(self, tab):
        return tab == "Suggested"  # no Cockpit

    def read_tab(self, tab):
        return self.grid


def test_late_swap_ranks_on_mypts(lsc_cfg, late_edge, capsys):
    plain = lsc._run(lsc_cfg, capsys, pool=["Better RB"])
    assert "a. Best full re-fill: +4.0 pts" in plain and "Your frame" not in plain
    cli._run_late_swap(
        lsc_cfg, top=3, metric="ProjPts", all_players=False, now=lsc.NOW,
        client=FrameClient(lsc.LINEUP, ["Better RB"], "Better RB +10"),
    )  # fmt: skip
    out = capsys.readouterr().out
    assert "Your frame: Better RB +10" in out and "a. Best full re-fill: +14.0 pts" in out


@pytest.fixture
def lsc_cfg():
    return SimpleNamespace(
        google_sheets=SimpleNamespace(),
        lineups=SimpleNamespace(builder_tab="Lineups", player_pool_tab="Player Pool", salary_cap=50000),
    )


@pytest.fixture(autouse=False)
def late_edge(monkeypatch):
    monkeypatch.setattr(cli.store, "load_current", lambda name: lsc._edge())


def test_the_cockpit_names_the_active_adjustments(edge):
    from test_cockpit import inputs

    layout = cockpit.build_layout(inputs(adjustments=("Nacua +2", "Kyren 40% owned")))
    line = next(r for r in layout.rows if r[1] == "YOUR FRAME")
    assert "Nacua +2, Kyren 40% owned" in line[2]
    assert not any("YOUR FRAME" in str(c) for r in cockpit.build_layout(inputs()).rows for c in r)


def test_model_check_and_the_stored_slate_keep_raw_calpts():
    """The frame is a copy made in the lineup commands; nothing that scores the model imports it."""
    src = Path(__file__).parent.parent / "src" / "dfs"
    for name in (
        "results_loop.py",
        "results_analysis.py",
        "results_signals.py",
        "sheet_model_check.py",
        "perf.py",
    ):
        assert "my_frame" not in (src / name).read_text(), name


def test_a_fresh_suggest_applies_adjust_and_says_so(monkeypatch, capsys):
    import test_lineups_suggest_cli as sc

    frame = syn.slate(teams=4, seed=2)
    frame["Own%"] = 0.05
    monkeypatch.setattr(cli.store, "load_current", lambda name: frame)
    seen = {}

    def sim(cfg, client, edge, *a, **k):
        seen["wr"] = pts(edge, "T00 WR1")
        return build_specs(edge), SimSettings(145.0, 190.0, "cash")

    monkeypatch.setattr(cli, "_late_swap_sim_setup", sim)
    monkeypatch.setattr(cli, "write_suggested", lambda *a, **k: "Suggested: ok")
    grid = [["Adjust", *[""] * (NAME_INDEX - 1), "T00 WR1 +5, T00 WR2 15%"]]
    client = sc.StubClient(frame, dict.fromkeys(frame["Name"], "Both"), suggested=grid)
    cfg = SimpleNamespace(
        google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}),
        lineups=SimpleNamespace(salary_cap=50000),
        projection=SimpleNamespace(default="CalPts"),
    )
    cli._run_suggest(cfg, now=sc.NOW, client=client, cash=1, gpp=1, write=True)
    out = capsys.readouterr().out
    assert seen["wr"] == pytest.approx(pts(frame, "T00 WR1") + 5)
    assert "Your frame: T00 WR1 +5." in out and "Adjust: cannot read 'T00 WR2 15%'" in out
    assert "only Adjust and Ownership applied" in out
