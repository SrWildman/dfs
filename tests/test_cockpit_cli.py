"""How the commands reach the Cockpit: `dfs setup cockpit`, and the refresh hook `sync --live` uses."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from typer.testing import CliRunner

from dfs import cli, sheet_cockpit

runner = CliRunner()
EDGE = pd.DataFrame({"Name": ["A"], "Position": ["QB"], "Team": ["ATL"]})


class Client:
    def describe(self):
        return "Week 7", "https://example.test/sheet"


def test_refresh_hook_prints_the_ok_line_and_hands_over_both_edges(monkeypatch, capsys):
    seen = {}

    def fake(client, cfg, *, edge, previous_edge=None, now=None):
        seen.update(edge=edge, previous=previous_edge)
        return True, "Cockpit: built (70 rows)"

    monkeypatch.setattr(sheet_cockpit, "refresh_if_present", fake)
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    before = EDGE.assign(Avail="Q")
    cli._refresh_cockpit(SimpleNamespace(), Client(), previous_edge=before)
    assert seen["edge"] is EDGE and seen["previous"] is before
    assert "Cockpit: built (70 rows)" in capsys.readouterr().out


def test_refresh_hook_is_silent_without_a_cockpit_tab_and_without_a_saved_edge(monkeypatch, capsys):
    monkeypatch.setattr(sheet_cockpit, "refresh_if_present", lambda *a, **k: (True, ""))
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    cli._refresh_cockpit(SimpleNamespace(), Client())
    assert capsys.readouterr().out == ""

    def no_edge(name):
        raise FileNotFoundError(name)

    called = []
    monkeypatch.setattr(sheet_cockpit, "refresh_if_present", lambda *a, **k: called.append(1))
    monkeypatch.setattr(cli.store, "load_current", no_edge)
    cli._refresh_cockpit(SimpleNamespace(), Client())
    assert called == []


def test_refresh_hook_warns_and_carries_on_when_the_refresh_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        sheet_cockpit, "refresh_if_present", lambda *a, **k: (False, "Cockpit not refreshed (x)")
    )
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    cli._refresh_cockpit(SimpleNamespace(), Client())
    assert "Cockpit not refreshed (x)" in capsys.readouterr().out


def _setup(monkeypatch):
    cfg = SimpleNamespace(google_sheets=SimpleNamespace(model_copy=lambda update: update))
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: Client())
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)


def test_setup_cockpit_builds_then_places_the_tab_and_names_the_sheet(monkeypatch):
    _setup(monkeypatch)
    order = []
    monkeypatch.setattr(
        sheet_cockpit,
        "refresh",
        lambda c, cfg, *, edge: order.append("refresh") or "Cockpit: built (80 rows)",
    )
    monkeypatch.setattr(
        sheet_cockpit,
        "place_before_board",
        lambda c: order.append("place") or "Cockpit: placed right before Board",
    )
    result = runner.invoke(cli.app, ["setup", "cockpit", "--sheet-id", "abc"])
    assert result.exit_code == 0, result.output
    assert order == ["refresh", "place"]
    assert "Building the Cockpit on: Week 7" in result.output and "Cockpit: built (80 rows)" in result.output


def test_setup_cockpit_stops_with_a_clear_message_when_there_is_no_synced_edge(monkeypatch):
    _setup(monkeypatch)

    def no_edge(name):
        raise FileNotFoundError("no edge snapshot")

    monkeypatch.setattr(cli.store, "load_current", no_edge)
    result = runner.invoke(cli.app, ["setup", "cockpit"])
    assert result.exit_code == 1 and "Run `dfs sync` first" in result.output
