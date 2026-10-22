"""How the commands reach the Player card: `setup player-card`, the sync hook and the `player_log` source."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from typer.testing import CliRunner

from dfs import cli, sheet_player_card
from dfs import player_card as pc
from dfs.sources import SOURCES, player_log
from dfs.sources.base import SyncContext

runner = CliRunner()
EDGE = pd.DataFrame({"Name": ["A"], "Position": ["QB"], "Team": ["ATL"]})


class Client:
    def describe(self):
        return "Week 7", "https://example.test/sheet"


def _setup(monkeypatch):
    cfg = SimpleNamespace(google_sheets=SimpleNamespace(model_copy=lambda update: update))
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: Client())
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)


def test_setup_player_card_builds_on_the_named_sheet_and_prints_every_step(monkeypatch):
    _setup(monkeypatch)
    seen = {}

    def fake(client, cfg, edge, week, season):
        seen.update(edge=edge, week=week, season=season)
        return ["Player card data: 1 players", "Player: placed right after Edge Finder"]

    monkeypatch.setattr(sheet_player_card, "setup", fake)
    result = runner.invoke(cli.app, ["setup", "player-card", "--sheet-id", "abc"])
    assert result.exit_code == 0, result.output
    assert "Building the Player card on: Week 7" in result.output
    assert "placed right after Edge Finder" in result.output
    assert seen["edge"] is EDGE


def test_setup_player_card_stops_clearly_without_a_synced_edge_or_on_a_sheets_error(monkeypatch):
    _setup(monkeypatch)

    def no_edge(name):
        raise FileNotFoundError("no edge snapshot")

    monkeypatch.setattr(cli.store, "load_current", no_edge)
    result = runner.invoke(cli.app, ["setup", "player-card"])
    assert result.exit_code == 1 and "Run `dfs sync` first" in result.output

    _setup(monkeypatch)
    monkeypatch.setattr(
        sheet_player_card,
        "setup",
        lambda *a: (_ for _ in ()).throw(cli.SheetsError("quota")),
    )
    result = runner.invoke(cli.app, ["setup", "player-card"])
    assert result.exit_code == 1 and "Sheets error" in result.output


def test_sync_hook_prints_the_ok_line_stays_silent_without_a_tab_and_warns_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: Client())
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    cfg, ctx = SimpleNamespace(google_sheets=None), SimpleNamespace(week=7, season=2026)

    monkeypatch.setattr(
        sheet_player_card, "refresh_raw_if_present", lambda *a: (True, "Player card data: ok")
    )
    cli._refresh_player_card(cfg, ctx)
    assert "Player card data: ok" in capsys.readouterr().out

    monkeypatch.setattr(sheet_player_card, "refresh_raw_if_present", lambda *a: (True, ""))
    cli._refresh_player_card(cfg, ctx)
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(sheet_player_card, "refresh_raw_if_present", lambda *a: (False, "not refreshed (x)"))
    cli._refresh_player_card(cfg, ctx)
    assert "not refreshed (x)" in capsys.readouterr().out


def test_sync_hook_rebuilds_the_card_when_edgeraw_widened_this_run_and_says_so(monkeypatch, capsys):
    from dfs.derived import EDGE_DATA_OFFSET, EDGE_SHEET_ORDER

    width = len(EDGE_SHEET_ORDER) + EDGE_DATA_OFFSET
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: Client())
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(sheet_player_card, "refresh_raw_if_present", lambda *a: (True, ""))
    cfg, ctx = SimpleNamespace(google_sheets=None), SimpleNamespace(week=7, season=2026)
    monkeypatch.setattr(
        sheet_player_card, "rebuild_card_if_widened", lambda c, g, b, a: (b != a, f"{b}->{a}")
    )

    cli._refresh_player_card(cfg, ctx, edge_width_before=width - 16)
    assert f"{width - 16}->{width}" in capsys.readouterr().out
    monkeypatch.setattr(
        sheet_player_card, "rebuild_card_if_widened", lambda c, g, b, a: (True, "" if b is None else "x")
    )
    cli._refresh_player_card(cfg, ctx)  # nothing known about the old width: the hook prints nothing
    assert capsys.readouterr().out == ""


def test_sync_hook_does_nothing_without_a_saved_edge(monkeypatch):
    def no_edge(name):
        raise FileNotFoundError(name)

    called = []
    monkeypatch.setattr(cli.store, "load_current", no_edge)
    monkeypatch.setattr(sheet_player_card, "refresh_raw_if_present", lambda *a: called.append(1))
    cli._refresh_player_card(SimpleNamespace(google_sheets=None), SimpleNamespace(week=7, season=2026))
    assert called == []


def test_player_log_is_a_registered_sheetless_source_that_runs_after_usage():
    names = list(SOURCES)
    assert names.index("player_log") == names.index("usage") + 1
    assert SOURCES["player_log"].uploads_to_sheet is False


def test_player_log_returns_an_empty_frame_when_the_stats_file_is_unavailable(monkeypatch):
    def unavailable(ctx):
        raise player_log.NflverseUsageFetchError("offline")

    monkeypatch.setattr(player_log, "_stats", unavailable)
    frame = player_log.PlayerLogSource().fetch(SyncContext(week=7, season=2026))
    assert frame.empty and list(frame.columns) == pc.LOG_COLUMNS


def test_player_log_blanks_the_columns_whose_file_failed(monkeypatch):
    stats = pd.DataFrame(
        [
            {
                "player_id": "w1",
                "player_display_name": "Wide One",
                "position": "WR",
                "season_type": "REG",
                "week": 6,
                "team": "AAA",
                "opponent_team": "BBB",
                "targets": 5,
                "carries": 0,
                "receiving_air_yards": 0,
                "passing_air_yards": 0,
            }
        ]
    )
    monkeypatch.setattr(player_log, "_stats", lambda ctx: stats)

    def offline(*a, **k):
        raise player_log.NflverseUsageFetchError("offline")

    monkeypatch.setattr(player_log, "_download", offline)
    frame = player_log.PlayerLogSource().fetch(SyncContext(week=7, season=2026))
    row = frame.iloc[0]
    assert row["Tgt"] == 5 and pd.isna(row["RZ"]) and pd.isna(row["Snap%"])


def test_week_new_rewrites_the_raw_tabs_on_the_new_sheet_and_never_fails(monkeypatch, capsys):
    seen = {}

    def fake(client, cfg, edge, week, season):
        seen.update(client=client, edge=edge, week=week, season=season)
        return True, "Player card data: written"

    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(sheet_player_card, "refresh_raw_if_present", fake)
    cli._rebuild_player_card_on_new_sheet("cfg", "new-client", SimpleNamespace(week=8, season=2026))
    assert seen == {"client": "new-client", "edge": EDGE, "week": 8, "season": 2026}
    assert "Player card data: written" in capsys.readouterr().out

    def missing(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(cli.store, "load_current", missing)
    cli._rebuild_player_card_on_new_sheet("cfg", "new-client", SimpleNamespace(week=8, season=2026))
