"""`dfs lineups suggest` end to end on a stub sheet and a synthetic slate (no network, no real sheet)."""

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, "tests")
import test_lineup_suggest as syn  # noqa: E402

from dfs import cli, suggest_state  # noqa: E402
from dfs.derived import edge_sheet_index  # noqa: E402
from dfs.late_swap_sim import SimSettings  # noqa: E402
from dfs.sim_inputs import build_specs  # noqa: E402

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class StubClient:
    def __init__(self, edge, tags, suggested=None):
        self.edge, self.tags = edge, tags
        self.written = None
        self.suggested = suggested  # the Suggested tab's grid, when a test wants one on the sheet

    def tab_exists(self, tab):
        return self.suggested is not None

    def read_tab(self, tab):
        return self.suggested or []

    def describe(self):
        return ("Week 6", "https://example.test/sheet")

    def read_range(self, tab, a1):
        """EdgeRaw: Pool in column A, Name / Position / Salary where `pool.read_players` reads them."""
        width = max(edge_sheet_index(c) for c in ("Name", "Position", "Salary")) + 1
        rows = []
        for _, r in self.edge.iterrows():
            row = [""] * width
            row[0] = self.tags.get(r["Name"], "")
            row[edge_sheet_index("Name")] = r["Name"]
            row[edge_sheet_index("Position")] = r["Position"]
            row[edge_sheet_index("Salary")] = str(r["Salary"])
            rows.append(row)
        return rows


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Rounds are saved to data/suggest_state/<week>.json: never the real one."""
    monkeypatch.setattr(suggest_state, "STATE_DIR", tmp_path / "suggest_state")
    return tmp_path / "suggest_state"


@pytest.fixture
def cfg():
    return SimpleNamespace(
        google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}),
        lineups=SimpleNamespace(salary_cap=50000),
        projection=SimpleNamespace(default="CalPts"),
    )


@pytest.fixture
def edge(monkeypatch):
    frame = syn.slate(teams=4, seed=2)
    monkeypatch.setattr(cli.store, "load_current", lambda name: frame)
    monkeypatch.setattr(
        cli, "_late_swap_sim_setup", lambda *a, **k: (build_specs(frame), SimSettings(145.0, 190.0, "cash"))
    )
    return frame


def run(cfg, edge, tags, capsys, **kw):
    client = StubClient(edge, tags)
    kw.setdefault("cash", 1)
    kw.setdefault("gpp", 1)
    kw.setdefault("write", True)
    cli._run_suggest(cfg, now=NOW, client=client, **kw)
    return capsys.readouterr().out


def test_it_prints_each_build_and_writes_the_suggested_tab(cfg, edge, capsys, monkeypatch):
    written = {}
    monkeypatch.setattr(
        cli,
        "write_suggested",
        lambda client, report, frame, **kw: written.update(report=report, **kw) or "Suggested: ok",
    )
    out = run(cfg, edge, dict.fromkeys(edge["Name"], "Both"), capsys)
    assert "Projection: CalPts" in out and "Cash build 1" in out and "GPP build 2" in out
    assert out.count("QB   ") == 2 and "Suggested: ok" in out  # nine lines per build, the QB line each
    assert (
        written["metric"] == "CalPts" and written["cash_line"] == 145.0 and len(written["report"].builds) == 2
    )


def test_it_prints_how_long_every_phase_took_in_the_order_they_ran(cfg, edge, capsys, monkeypatch):
    """Sam, 2026-10-17: it felt idle after printing the builds. Every phase is timed and shown, the sheet ones
    with their request counts; `--no-write` shows the phases that ran without the two sheet-write ones."""

    def fake_write(client, report, frame, *, timings=None, **kw):
        timings["write tab"], timings["format tab"] = (0.4, 2), (1.2, 3)
        return "Suggested: ok"

    monkeypatch.setattr(cli, "write_suggested", fake_write)
    out = run(cfg, edge, dict.fromkeys(edge["Name"], "Both"), capsys)
    block = out[out.index("Phase times:") :]
    names = [
        "read sheet",
        "read tab",  # before the simulator: the tab's rules box holds your Adjust / Ownership lines
        "set up simulator",
        "candidates",
        "search",
        "simulate",
        "choose",
        "write tab",
        "format tab",
    ]
    positions = [block.index(f"  {n}") for n in names]
    assert positions == sorted(positions) and "total" in block
    assert "3 requests" in block and "2 requests" in block  # the sheet phases show their request counts
    quiet = run(cfg, edge, dict.fromkeys(edge["Name"], "Both"), capsys, write=False)
    assert "Phase times:" in quiet and "write tab" not in quiet and "search" in quiet


def test_seconds_sets_the_search_time_limit_and_defaults_to_two_minutes(cfg, edge, capsys, monkeypatch):
    seen = []
    real = cli.suggest_builds
    monkeypatch.setattr(cli, "suggest_builds", lambda *a, **k: seen.append(k["time_limit"]) or real(*a, **k))
    tags = dict.fromkeys(edge["Name"], "Both")
    run(cfg, edge, tags, capsys, write=False)
    run(cfg, edge, tags, capsys, write=False, seconds=180.0)
    assert seen == [120.0, 180.0]


def test_no_write_leaves_the_tab_alone(cfg, edge, capsys, monkeypatch):
    monkeypatch.setattr(cli, "write_suggested", lambda *a, **k: pytest.fail("the tab must not be written"))
    out = run(cfg, edge, dict.fromkeys(edge["Name"], "Both"), capsys, write=False)
    assert "--no-write" in out and "Build 1" in out


def test_an_empty_pool_says_so_and_exits(cfg, edge, capsys):
    client = StubClient(edge, {})
    with pytest.raises(cli.typer.Exit):
        cli._run_suggest(cfg, cash=1, gpp=1, write=True, now=NOW, client=client)
    assert "Your pool is empty" in capsys.readouterr().out


def test_the_command_is_registered_with_the_documented_options():
    names = {c.name for c in cli.lineups_app.registered_commands}
    assert {"suggest", "late-swap", "clear"} <= names
    suggest = next(c for c in cli.lineups_app.registered_commands if c.name == "suggest")
    assert suggest.callback is cli.lineups_suggest


def test_rank_defaults_to_the_top_1_chance_and_share_is_opt_in(cfg, edge, capsys, monkeypatch):
    seen = []
    real = cli.suggest_builds
    monkeypatch.setattr(cli, "suggest_builds", lambda *a, **k: seen.append(k["rank"]) or real(*a, **k))
    tags = dict.fromkeys(edge["Name"], "Both")
    run(cfg, edge, tags, capsys, write=False)
    run(cfg, edge, tags, capsys, write=False, rank="share")
    assert seen == ["top1", "share"]


def test_the_rank_option_takes_top1_or_share_and_defaults_to_top1():
    from typer.testing import CliRunner

    suggest = next(c for c in cli.lineups_app.registered_commands if c.name == "suggest")
    import inspect

    default = inspect.signature(suggest.callback).parameters["rank"].default.default
    assert default is cli.SuggestRank.top1
    bad = CliRunner().invoke(cli.app, ["lineups", "suggest", "--rank", "ev"])
    assert bad.exit_code == 2 and "top1" in bad.output and "share" in bad.output


def test_rounds_are_kept_and_iterate_and_round_restore_work_end_to_end(
    cfg, edge, capsys, monkeypatch, state_dir
):
    """fresh run = round 1; the tab it wrote, edited, is what --iterate reads (round 2); --round 1 brings
    round
    1 back as round 3. Nothing but the (faked) tab write and the state file is touched."""
    from dfs import sheet_suggested as ss

    written = []
    monkeypatch.setattr(
        cli, "write_suggested", lambda client, report, frame, **kw: written.append(report) or "Suggested: ok"
    )
    tags = dict.fromkeys(edge["Name"], "Both")

    def go(**kw):
        client = StubClient(edge, tags, suggested=kw.pop("grid", None))
        kw.setdefault("cash", 1)
        kw.setdefault("gpp", 2)
        kw.setdefault("write", True)
        cli._run_suggest(cfg, now=NOW, client=client, **kw)
        return capsys.readouterr().out

    out = go()
    first = written[-1]
    assert first.round == 1 and "round 1 saved" in out and (state_dir / "6.json").exists()
    grid = ss.build_layout(first, edge, metric="CalPts", cash_line=145.0, gpp_target=190.0).rows
    grid = [list(r) for r in grid]
    cash_bar = next(r for r in grid if r[0] == "Build 1")
    cash_bar[ss.DECISION_INDEX] = "Keep"
    second_bar = next(r for r in grid if r[0] == "Build 2")
    second_bar[ss.DECISION_INDEX] = "Drop"
    out = go(iterate=True, grid=grid, seconds=20.0)
    second = written[-1]
    assert second.round == 2 and "Round 2 (rerun)" in out and "what changed" not in out
    assert [b.origin for b in second.builds].count("kept") >= 1 and len(second.builds) == len(first.builds)
    assert second.history[0].startswith("Round 1") and second.history[1].startswith("Round 2")
    state = suggest_state.load_state(6)
    assert [r.round for r in state.rounds] == [1, 2] and state.rounds[1].command == "iterate"
    out = go(
        restore_round=1, grid=ss.build_layout(second, edge, metric="CalPts", cash_line=1, gpp_target=2).rows
    )
    third = written[-1]
    assert third.round == 3 and "round 1 restored" in out
    assert [[p.name for p in b.players] for b in third.builds] == [
        [p.name for p in b.players] for b in first.builds
    ]
    assert suggest_state.load_state(6).rounds[2].restored_from == 1
    with pytest.raises(cli.typer.Exit):
        go(restore_round=9)
    assert "There is no round 9" in capsys.readouterr().out
    go(iterate=True, write=False, grid=grid)  # --no-write: no round is saved
    assert len(suggest_state.load_state(6).rounds) == 3
