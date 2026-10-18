"""Relative lines wired through the tool: the config block and its defaults, which lines `resolve_lines` picks
(relative / fixed / the blank-ownership fallback), `--fixed-lines` reproducing the old numbers exactly, the
Suggested builds ranked by the new numbers, and the Lineups tab's labels and notes."""

import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

sys.path.insert(0, "tests")
import test_lineup_suggest as syn  # noqa: E402
import test_sheet_lineup_sim as tsls  # noqa: E402

from dfs import cli  # noqa: E402
from dfs import config as C  # noqa: E402
from dfs import lineup_shapes as shapes_mod
from dfs import lineup_suggest as ls
from dfs import sim_inputs as si  # noqa: E402
from dfs.late_swap_sim import SimSettings  # noqa: E402
from dfs.sim.simulate import simulate_lineups  # noqa: E402

# --- the config block -----------------------------------------------------------------------------------


def _minimal(**extra):
    return {
        "google_sheets": {"sheet_id": "abc", "credentials_file": "creds.json", "tab_mappings": {}},
        **extra,
    }


def test_the_lines_block_defaults_are_the_measured_offsets_and_the_curves_are_the_identity():
    lines = C.Config.model_validate(_minimal()).sim.lines
    assert (lines.double_up_offset, lines.gpp_cash_offset, lines.gpp_top10_offset, lines.gpp_top1_offset) == (
        0.6,
        17.9,
        31.9,
        63.2,
    )
    assert lines.fallback_cash_line is None and lines.fallback_top1_line is None  # the old fixed numbers
    assert lines.double_up_own_curve == [] and lines.gpp_own_curve == [] and lines.own_coverage == 0.95
    # the module constants the block is built from are what docs/SIM.md's table says
    assert (C.SIM_DOUBLE_UP_OFFSET, C.SIM_GPP_CASH_OFFSET, C.SIM_GPP_TOP10_OFFSET, C.SIM_GPP_TOP1_OFFSET) == (
        0.6,
        17.9,
        31.9,
        63.2,
    )
    assert C.Config.model_validate(_minimal()).sim.gpp_target == 190.0  # `[sim] gpp_target` is untouched


def test_the_lines_block_is_configurable_and_refuses_what_it_does_not_know():
    sim = {
        "gpp_target": 200,
        "lines": {
            "double_up_offset": 1.5,
            "gpp_top1_offset": 60,
            "fallback_cash_line": 147.56,
            "fallback_top1_line": 190,
            "double_up_own_curve": [[0.05, 0.05], [0.15, 0.39]],
        },
    }
    lines = C.Config.model_validate(_minimal(sim=sim)).sim.lines
    assert lines.double_up_offset == 1.5 and lines.gpp_top1_offset == 60.0 and lines.gpp_cash_offset == 17.9
    assert lines.fallback_cash_line == 147.56 and lines.double_up_own_curve == [(0.05, 0.05), (0.15, 0.39)]
    for bad in (
        {"nope": 1},
        {"double_up_own_curve": [[0.2, 0.3], [0.1, 0.5]]},  # expected ownership falls as projected rises
        {"double_up_own_curve": [[0.1, 0.2], [0.1, 0.3]]},  # a projected value twice
        {"gpp_own_curve": [[1.5, 0.2]]},  # not a fraction
        {"own_coverage": 0},
    ):
        with pytest.raises(ValidationError):
            C.Config.model_validate(_minimal(sim={"lines": bad}))


def test_the_example_config_parses_with_the_documented_defaults():
    path = Path(__file__).resolve().parents[1] / "config.example.toml"
    raw = tomllib.loads(path.read_text())
    lines = C.SimConfig.model_validate(raw["sim"]).lines
    assert lines == C.SimLinesConfig()  # the example file states the defaults, nothing else


# --- resolve_lines: relative, fixed, fallback -----------------------------------------------------------


def _owned_slate(own=True, teams=4):
    edge = syn.slate(teams=teams, seed=2)
    rng = np.random.default_rng(0)
    share = rng.uniform(0.01, 1.0, len(edge))
    edge["Own%"] = share / share.sum() * 9 if own else np.nan  # a column of blanks until TFFB publishes
    return edge


CASH = si.CashLine(147.56, (2, 4, 5), "season median of your typed Cash Line, Weeks 2, 4, 5")


def test_with_published_ownership_the_lines_move_with_the_slate():
    edge = _owned_slate()
    lines = si.resolve_lines(edge, si.build_specs(edge), CASH, 190.0, C.SimLinesConfig())
    assert lines.mode == "relative" and lines.relative and lines.field is not None
    assert (lines.field.cash_offset, lines.field.top_offset) == (0.6, 63.2)
    assert lines.top_label == "Top 1%" and "field average" in lines.note and "+63.2" in lines.note
    assert "field's average score +0.6" in lines.cash_basis
    assert "Top 1%" in lines.status and "field average +0.6" in lines.status


def test_blank_ownership_falls_back_to_the_fixed_lines_and_says_so():
    edge = _owned_slate(own=False)
    specs = si.build_specs(edge)
    lines = si.resolve_lines(edge, specs, CASH, 190.0, C.SimLinesConfig())
    assert lines.mode == "fallback" and lines.field is None
    assert (lines.cash_line, lines.gpp_target) == (147.56, 190.0)  # unset: the old fixed numbers
    assert "Ownership has not published" in lines.note and "FIXED lines" in lines.note
    assert "ownership not published" in lines.cash_basis and lines.top_label == "190+"
    pinned = C.SimLinesConfig(fallback_cash_line=150.0, fallback_top1_line=185.0)
    lines = si.resolve_lines(edge, specs, CASH, 190.0, pinned)
    assert (lines.cash_line, lines.gpp_target, lines.mode) == (150.0, 185.0, "fallback")
    assert "[sim.lines] fallback" in lines.note and lines.top_label == "185+"
    # a missing column, an all-zero column and a column of blanks are all "not published"
    for frame in (edge.drop(columns="Own%"), edge.assign(**{"Own%": 0.0})):
        assert si.resolve_lines(frame, specs, CASH, 190.0, C.SimLinesConfig()).mode == "fallback"


def test_fixed_lines_is_a_choice_even_when_ownership_is_published():
    edge = _owned_slate()
    lines = si.resolve_lines(edge, si.build_specs(edge), CASH, 190.0, C.SimLinesConfig(), fixed_lines=True)
    assert lines.mode == "fixed" and lines.field is None
    assert (lines.cash_line, lines.gpp_target) == (147.56, 190.0)
    assert "--fixed-lines" in lines.note and lines.cash_basis == CASH.basis and lines.top_label == "190+"


def test_ownership_is_read_by_name_first_row_wins_blanks_dropped():
    edge = pd.DataFrame({"Name": ["A", "B", "A", "C"], "Own%": [0.1, np.nan, 0.9, "x"]})
    assert si.ownership_by_name(edge) == {"A": 0.1}
    assert si.ownership_by_name(pd.DataFrame({"Name": ["A"]})) == {}


# --- --fixed-lines reproduces today's numbers exactly ---------------------------------------------------

# Captured from the simulator BEFORE relative lines existed (main at b0488a5): the synthetic 6-team slate,
# two lineups, 20,000 draws, seed 0, cash line 130, GPP target 170.
GOLDEN_A = [
    "T00 QB1",
    "T00 RB1",
    "T00 RB2",
    "T00 WR1",
    "T00 WR2",
    "T00 WR3",
    "T00 TE1",
    "T01 WR1",
    "T01 DST1",
]
GOLDEN_B = [
    "T02 QB1",
    "T02 RB1",
    "T03 RB2",
    "T02 WR1",
    "T03 WR2",
    "T02 WR3",
    "T03 TE1",
    "T02 WR2",
    "T04 DST1",
]


def test_fixed_lines_reproduce_the_numbers_from_before_relative_lines_exactly():
    edge = syn.slate()
    specs = si.build_specs(edge)
    lineups = [[specs[n] for n in GOLDEN_A], [specs[n] for n in GOLDEN_B]]
    plain = simulate_lineups(lineups, n_sims=20000, seed=0, cash_line=130.0, gpp_target=170.0)
    a, b = plain.lineups
    assert (a.p_cash, a.p_gpp) == (0.1214, 0.0051) and (b.p_cash, b.p_gpp) == (0.0929, 0.00425)
    assert a.mean == pytest.approx(102.20139669838329, abs=1e-6)
    assert b.mean == pytest.approx(95.36954786249463, abs=1e-6)
    assert (plain.p_any_cash, plain.p_any_gpp, plain.expected_cashing) == (0.20375, 0.00925, 0.2143)
    assert plain.lines == "fixed" and plain.cash_line == 130.0 and plain.gpp_target == 170.0
    # and `--fixed-lines` is exactly that call: no field, the typed median and gpp_target
    owned = edge.assign(**{"Own%": 1.0 / len(edge) * 9})
    fixed = si.resolve_lines(
        owned, specs, si.CashLine(130.0, (1,), "t"), 170.0, C.SimLinesConfig(), fixed_lines=True
    )
    again = simulate_lineups(
        lineups,
        n_sims=20000,
        seed=0,
        cash_line=fixed.cash_line,
        gpp_target=fixed.gpp_target,
        field=fixed.field,
    )
    assert [(s.p_cash, s.p_gpp, s.mean, s.sd) for s in again.lineups] == [
        (s.p_cash, s.p_gpp, s.mean, s.sd) for s in plain.lineups
    ]
    relative = si.resolve_lines(owned, specs, si.CashLine(130.0, (1,), "t"), 170.0, C.SimLinesConfig())
    new = simulate_lineups(lineups, n_sims=20000, seed=0, field=relative.field)
    assert new.lines == "relative" and new.lineups[0].p_cash != a.p_cash  # the new numbers really differ


# --- the Suggested builds ------------------------------------------------------------------------------


def _suggest(edge, **kw):
    kw.setdefault("n_sims", 1500)
    return ls.suggest_builds(
        edge,
        syn.tags_for(edge),
        si.build_specs(edge),
        now=syn.NOW,
        cap=syn.CAP,
        cash_line=145.0,
        gpp_target=190.0,
        metric="CalPts",
        **kw,
    )


@pytest.fixture(scope="module")
def owned_edge():
    return _owned_slate(teams=4)


@pytest.fixture(scope="module")
def field_for(owned_edge):
    return si.resolve_lines(owned_edge, si.build_specs(owned_edge), CASH, 190.0, C.SimLinesConfig()).field


@pytest.fixture(scope="module")
def relative_report(owned_edge, field_for):
    return _suggest(owned_edge, field=field_for)


@pytest.fixture(scope="module")
def fixed_report(owned_edge):
    return _suggest(owned_edge)


def _key(report):
    return [(b.title, [p.name for p in b.players], b.p_cash, b.p_gpp) for b in report.builds]


def test_cash_builds_rank_by_the_new_cash_percent_and_gpp_builds_by_top_1_percent(
    relative_report, fixed_report
):
    cash = [b for b in relative_report.builds if b.kind == "cash"]
    gpp = [b for b in relative_report.builds if b.kind == "gpp"]
    assert len(cash) == 3 and gpp
    assert [b.p_cash for b in cash] == sorted((b.p_cash for b in cash), reverse=True)
    assert [b.p_gpp for b in gpp[:2]] == sorted((b.p_gpp for b in gpp[:2]), reverse=True)  # best first
    # the numbers are the relative ones: a fixed 145 / 190 on this slate gives different Cash %
    assert [b.p_cash for b in cash] != [b.p_cash for b in fixed_report.builds if b.kind == "cash"]


def test_the_exposure_diversity_and_stack_rules_are_unchanged(relative_report):
    for build in relative_report.builds:
        syn.assert_legal(build)
        if build.kind == "gpp":
            assert build.shapes and all(s in shapes_mod.SHAPES for s in build.shapes)
    for kind, minimum in (("cash", ls.MIN_DIFFERENCE), ("gpp", ls.SECOND_BUILD_MIN_DIFFERENCE)):
        picks = [frozenset(p.name for p in b.players) for b in relative_report.builds if b.kind == kind]
        assert all(len(x - y) >= minimum for i, x in enumerate(picks) for y in picks[:i])


def test_the_builds_are_deterministic_with_a_field(owned_edge, field_for, relative_report):
    assert _key(_suggest(owned_edge, field=field_for)) == _key(relative_report)


def test_without_a_field_the_builds_are_what_they_were(owned_edge, fixed_report):
    # `field=None` is `--fixed-lines` (and the blank-ownership fallback): the old call, the old builds
    assert _key(_suggest(owned_edge, field=None)) == _key(fixed_report)


# --- the CLI plumbing -----------------------------------------------------------------------------------


def test_fixed_lines_is_an_option_of_suggest_late_swap_and_sync():
    from typer.main import get_command

    group = get_command(cli.app)
    for path in (("lineups", "suggest"), ("lineups", "late-swap"), ("sync",)):
        command = group
        for part in path:
            command = command.commands[part]
        option = next(p for p in command.params if "--fixed-lines" in p.opts)
        assert option.is_flag and option.default is False and "FIXED" in option.help
    assert "--fixed-lines" not in {o for p in group.commands["status"].params for o in p.opts}


class _Client:
    def __init__(self, edge):
        self.inner = SimpleNamespace(edge=edge)

    def describe(self):
        return ("Week 6", "https://example.test/sheet")

    def read_range(self, tab, a1):
        from dfs.derived import edge_sheet_index

        edge = self.inner.edge
        width = max(edge_sheet_index(c) for c in ("Name", "Position", "Salary")) + 1
        rows = []
        for _, r in edge.iterrows():
            row = [""] * width
            row[0] = "Both"
            row[edge_sheet_index("Name")] = r["Name"]
            row[edge_sheet_index("Position")] = r["Position"]
            row[edge_sheet_index("Salary")] = str(r["Salary"])
            rows.append(row)
        return rows


def test_suggest_passes_the_flag_the_field_and_the_lines_text_through(monkeypatch, capsys):
    edge = _owned_slate(teams=4)
    monkeypatch.setattr(cli.store, "load_current", lambda name: edge)
    cfg = SimpleNamespace(
        google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}),
        lineups=SimpleNamespace(salary_cap=50000),
        projection=SimpleNamespace(default="CalPts"),
    )
    seen = {}
    lines = si.resolve_lines(edge, si.build_specs(edge), CASH, 190.0, C.SimLinesConfig())

    def setup(cfg_, client, edge_, goal, now, *, fixed_lines=False):
        seen["fixed_lines"] = fixed_lines
        return si.build_specs(edge), SimSettings(147.56, 190.0, "cash", field=lines.field, lines=lines)

    monkeypatch.setattr(cli, "_late_swap_sim_setup", setup)
    real = cli.suggest_builds
    monkeypatch.setattr(cli, "suggest_builds", lambda *a, **k: seen.update(field=k["field"]) or real(*a, **k))
    monkeypatch.setattr(cli, "write_suggested", lambda client, report, frame, **kw: seen.update(kw) or "ok")
    syn_args = {"cash": 1, "gpp": 1, "write": True, "now": datetime(2026, 10, 18, 12, 0, tzinfo=UTC)}
    cli._run_suggest(cfg, client=_Client(edge), **syn_args)
    assert seen["fixed_lines"] is False and seen["field"] is lines.field
    assert "Top 1%" in seen["lines_text"] and "field average" in seen["lines_text"]
    assert "Top 1%" in capsys.readouterr().out  # the terminal summary line names the column
    cli._run_suggest(cfg, client=_Client(edge), fixed_lines=True, **syn_args)
    assert seen["fixed_lines"] is True


# --- the Lineups tab and the Suggested tab --------------------------------------------------------------


def _owned_sheet_edge():
    edge = tsls._edge()
    share = np.linspace(1.0, 2.0, len(edge))
    edge["Own%"] = share / share.sum() * 9
    return edge


def _sheet_run(client, **kw):
    return tsls._run(client, lines_config=C.SimLinesConfig(), **kw)


def test_the_lineups_tab_writes_relative_numbers_and_says_what_they_are():
    edge = _owned_sheet_edge()
    relative_client = tsls.FakeClient([tsls.FULL_A, None])
    report = _sheet_run(relative_client, edge=edge)
    assert report.lines.mode == "relative" and "field average" in report.line() and "Top 1%" in report.line()
    fixed_client = tsls.FakeClient([tsls.FULL_A, None])
    fixed = _sheet_run(fixed_client, edge=edge, fixed_lines=True)
    assert (
        fixed.lines.mode == "fixed"
        and "--fixed-lines: " in fixed.line()
        and "cash line 143.86" in fixed.line()
    )
    first, _, _, last = tsls._slot_letters()
    assert relative_client.updates[f"{first}11:{last}11"] != fixed_client.updates[f"{first}11:{last}11"]
    assert relative_client.updates[f"{first}12:{last}12"] == [["Typical", "Big night", "Cash %", "Top 1%"]]
    notes = dict(relative_client.notes)
    cash_note, top_note = notes[f"{tsls._slot_letters()[2]}12"], notes[f"{last}12"]
    assert "moves with the slate" in cash_note and "0.6" in cash_note
    assert top_note.startswith("TOP 1%") and "63.2" in top_note and "190+" not in top_note
    assert "median of the Cash Lines" in dict(fixed_client.notes)[f"{tsls._slot_letters()[2]}12"]


def test_the_lineups_tab_without_ownership_uses_the_fixed_lines_and_says_so():
    edge = _owned_sheet_edge().assign(**{"Own%": np.nan})
    client = tsls.FakeClient([tsls.FULL_A, None])
    report = _sheet_run(client, edge=edge)
    assert report.lines.mode == "fallback" and "Ownership has not published" in report.line()
    legacy = tsls.FakeClient([tsls.FULL_A, None])
    tsls._run(legacy, edge=edge)  # no lines_config at all: the unchanged code path
    first, _, _, last = tsls._slot_letters()
    assert client.updates[f"{first}11:{last}11"] == legacy.updates[f"{first}11:{last}11"]


def test_the_board_portfolio_line_names_the_top_end_number():
    from dfs.sheet_views import board_portfolio_text

    summary = {"expected_cashes": 2.14, "p_any_cash": 0.913, "p_any_gpp": 0.124}
    assert board_portfolio_text(summary, 6, 190.0, "x", top_label="Top 1%").endswith(
        "P(at least one Top 1%) 12%  ·  x"
    )
    assert "P(at least one 190+)" in board_portfolio_text(summary, 6, 190.0)


def test_the_suggested_status_line_carries_the_lines_text_when_given():
    from dfs import sheet_suggested as ss

    report = ls.SuggestReport(candidates={"cash": 4, "gpp": 5}, seconds=3.0)
    report.builds.append(object())  # only the count is read
    plain = ss.status_line(report, metric="CalPts", cash_line=143.9, gpp_target=190.0)
    assert "cash line 143.9" in plain and "GPP target 190" in plain
    moving = ss.status_line(
        report, metric="CalPts", cash_line=143.9, gpp_target=190.0, lines_text="lines move with the slate"
    )
    assert "lines move with the slate" in moving and "cash line" not in moving
    assert ss.build_summary(ls.Build(1, "gpp", "t", (), 50000, 120.0, 0.51, 0.0123, 110.0, 150.0)).startswith(
        "Build 1  GPP  $50,000  120.0 pts  Cash 51%  Top 1% 1.2%"
    )
