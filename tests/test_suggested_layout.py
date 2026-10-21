"""A Suggested tab in the layout before the rules box and the lock column: `doctor` names it, `--iterate`
refuses it with the fix instead of misreading it, and `dfs week new` resets the new week's tab to the current
empty layout (never carrying last week's builds). Stub sheets only (no network, no real sheet)."""

import inspect
import sys

import pytest

sys.path.insert(0, "tests")
import test_sheet_suggested as tss  # noqa: E402
from test_lineups_suggest_cli import cfg, edge, state_dir  # noqa: E402, F401 - the module's fixtures

from dfs import cli, doctor, suggest_state  # noqa: E402
from dfs import sheet_suggested as ss  # noqa: E402

FIX = "run a plain `dfs lineups suggest`"


def old_grid(builds=2):
    """What Week 7's tab held: the layout before the lock column and the rules box (Name in column B)."""
    rows = [
        [ss.TITLE],
        ["CalPts  ·  pool 43 for Cash / 69 for GPP  ·  12 builds in 55 s"],
        ["To use some: Lineup Tools > Copy builds to Lineups (type 1,4,6 or all)."],
        [],
    ]
    slots = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
    for n in range(1, builds + 1):
        rows.append([f"Build {n}", "Cash", f"Cash build {n}"])
        rows.append(["Slot", "Name", "Team", "Opp", "Salary", "CalPts"])
        rows += [[slot, f"Player {n}{i}", "LV", "BUF", "$5,000", "12.0"] for i, slot in enumerate(slots)]
        rows += [["Total", "", "", "", "$50,000", "141.8"], []]
    return rows


def current_grid(*builds):
    return ss.build_layout(
        tss._report(*builds),
        tss._edge(list(builds)),
        metric="CalPts",
        cash_line=1.0,
        gpp_target=2.0,
    ).rows


# --- the detector ------------------------------------------------------------------------------------------


def test_an_old_layout_tab_with_builds_says_to_run_a_plain_suggest():
    problem = ss.layout_problem(old_grid())
    assert problem == ss.OLD_LAYOUT_MESSAGE and FIX in problem
    assert "Name in column B" in problem and "no lock column" in problem


def test_the_current_layout_is_fine_with_builds_and_without():
    assert ss.layout_problem(current_grid(tss._build(1, "cash", "Cash build 1"))) is None
    assert ss.layout_problem(current_grid()) is None  # empty, but with the rules box
    assert ss.layout_problem([]) is None  # nothing to judge


def test_an_empty_old_tab_without_the_rules_box_says_to_run_the_setup_step():
    grid = [[ss.TITLE], ["Not run yet: `dfs lineups suggest` fills this tab from your pool."], [], []]
    assert "suggested-controls" in ss.layout_problem(grid)


def test_a_five_line_rules_box_with_the_lock_column_is_not_called_old():
    """The layout of two weeks ago still iterates; only the one without a lock column is refused."""
    grid = current_grid(tss._build(1, "cash", "Cash build 1"))
    assert (
        ss.layout_problem([r for r in grid if not (r and r[0] in ("Adjust", "Ownership", "Build rules"))])
        is None
    )


def test_parse_state_carries_the_verdict_to_the_rerun():
    assert ss.parse_state(old_grid()).layout_problem == ss.OLD_LAYOUT_MESSAGE
    assert ss.parse_state(current_grid(tss._build(1, "cash", "Cash build 1"))).layout_problem is None


# --- doctor ------------------------------------------------------------------------------------------------


class ReadOnly:
    def __init__(self, grid):
        self.grid, self.asked = grid, []

    def read_range(self, tab, a1):
        self.asked.append((tab, a1))
        return [row[:3] for row in self.grid[:40]]


def test_doctor_flags_an_old_layout_with_the_fix_and_passes_the_current_one():
    client = ReadOnly(old_grid())
    [issue] = doctor._check_suggested_layout(client, {"Suggested"})
    assert issue.check == "suggested-layout" and FIX in issue.detail and "'Suggested'" in issue.detail
    assert client.asked == [("Suggested", "A1:C40")]  # the top of the tab only
    assert doctor._check_suggested_layout(ReadOnly(current_grid()), {"Suggested"}) == []
    assert doctor._check_suggested_layout(ReadOnly(old_grid()), {"Board"}) == []  # no such tab


def test_run_doctor_includes_the_check():
    assert "_check_suggested_layout(client, tab_titles)" in inspect.getsource(doctor.run_doctor)


# --- --iterate ---------------------------------------------------------------------------------------------


def test_iterate_refuses_an_old_layout_tab_before_reading_or_writing_anything(cfg, edge, capsys, monkeypatch):  # noqa: F811
    from test_lineups_suggest_cli import NOW, StubClient

    monkeypatch.setattr(cli, "write_suggested", lambda *a, **k: pytest.fail("the tab must not be written"))
    client = StubClient(edge, dict.fromkeys(edge["Name"], "Both"), suggested=old_grid())
    with pytest.raises(cli.typer.Exit) as stopped:
        cli._run_suggest(cfg, now=NOW, client=client, cash=1, gpp=1, write=True, iterate=True)
    out = " ".join(capsys.readouterr().out.split())
    assert stopped.value.exit_code == 1 and FIX in out and "cannot read" in out
    assert "Round" not in out and not suggest_state.state_path(6).exists()


def test_a_plain_run_still_replaces_an_old_layout_tab(cfg, edge, capsys, monkeypatch):  # noqa: F811
    from test_lineups_suggest_cli import NOW, StubClient

    written = []
    monkeypatch.setattr(cli, "write_suggested", lambda c, report, *a, **k: written.append(report) or "ok")
    client = StubClient(edge, dict.fromkeys(edge["Name"], "Both"), suggested=old_grid())
    cli._run_suggest(cfg, now=NOW, client=client, cash=1, gpp=1, write=True)
    assert written and written[0].builds  # the fix the message names works


# --- dfs week new ------------------------------------------------------------------------------------------


def test_the_reset_rewrites_the_tab_empty_even_when_it_holds_builds_and_rules():
    cash = tss._build(1, "cash", "Cash build 1")
    grid = current_grid(cash)
    grid[[r[0] for r in grid].index("Never use")][ss.NAME_INDEX] = "Kyren"
    client = tss._protectable(tss._Client(exists=True), grid)
    lines = ss.setup_controls(client, reset=True)
    state = ss.parse_state(client.written[1])
    assert state.builds == [] and state.rules.blank() and state.layout_problem is None
    assert "reset" in lines[0] and client.protected == [("Suggested", ["B:B", "C:C"])]
    assert (
        len(client.written[1]) == ss.FIRST_BUILD_ROW
    )  # the rules box and the empty-state line, nothing of last week


def test_the_reset_also_replaces_an_old_layout_tab_and_creates_a_missing_one():
    old = tss._protectable(tss._Client(exists=True), old_grid())
    ss.setup_controls(old, reset=True)
    assert ss.layout_problem(old.written[1]) is None
    missing = tss._protectable(tss._Client(exists=False), [])
    assert ss.setup_controls(missing, reset=True) and missing.written is not None


def test_week_new_resets_suggested_never_fails_over_it_and_does_not_stop_on_the_old_layout(
    capsys, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        cli, "setup_controls", lambda client, reset=False: seen.append(reset) or ["Suggested: ok"]
    )
    cli._reset_suggested_on_new_sheet(object())
    assert seen == [True] and "Suggested: ok" in capsys.readouterr().out

    def boom(client, reset=False):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(cli, "setup_controls", boom)
    cli._reset_suggested_on_new_sheet(object())  # must not raise
    out = capsys.readouterr().out
    assert (
        "Suggested tab not reset" in out and "quota exceeded" in out and "dfs setup suggested-controls" in out
    )

    source = inspect.getsource(cli.week_new)
    cleared = source.index("clear_previous_week(")
    reset = source.index("_reset_suggested_on_new_sheet(new_client)")
    synced = source.index("run_sync(")
    assert cleared < reset < synced
    # the check that stops `week new` before any write lets the old layout through: the reset fixes it
    assert source.index('i.check != "suggested-layout"') < source.index("passed structural checks")
