"""`dfs lineups suggest` end to end on a stub sheet and a synthetic slate (no network, no real sheet)."""

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, "tests")
import test_lineup_suggest as syn  # noqa: E402

from dfs import cli  # noqa: E402
from dfs.derived import edge_sheet_index  # noqa: E402
from dfs.late_swap_sim import SimSettings  # noqa: E402
from dfs.sim_inputs import build_specs  # noqa: E402

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class StubClient:
    def __init__(self, edge, tags):
        self.edge, self.tags = edge, tags
        self.written = None

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


def test_seconds_sets_the_search_time_limit_and_defaults_to_a_minute(cfg, edge, capsys, monkeypatch):
    seen = []
    real = cli.suggest_builds
    monkeypatch.setattr(cli, "suggest_builds", lambda *a, **k: seen.append(k["time_limit"]) or real(*a, **k))
    tags = dict.fromkeys(edge["Name"], "Both")
    run(cfg, edge, tags, capsys, write=False)
    run(cfg, edge, tags, capsys, write=False, seconds=180.0)
    assert seen == [60.0, 180.0]


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
