"""The Stacks tab: the sheet side against a recording fake, the live-slate inputs and the commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from dfs import cli, sheet_stacks, stacks, stacks_inputs, stacks_tab
from dfs.sim import field_gen
from dfs.sim.stacks_retro import FieldModel
from tests.test_stacks_tab import games, table

runner = CliRunner()
NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, tabs=("Board", "Suggested", "Lineups")):
        self.tabs = list(tabs)
        self.calls: list[tuple] = []

    def describe(self):
        return "Week 7", "https://example.test/sheet"

    def tab_exists(self, tab):
        return tab in self.tabs

    def list_tabs(self):
        return [SimpleNamespace(title=t) for t in self.tabs]

    def write_tab(self, tab, rows, **kw):
        self.calls.append(("write_tab", tab, rows))
        if tab not in self.tabs:
            self.tabs.append(tab)
        return len(rows)

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))

        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def layout():
    result = stacks.StackResult(table=table(), games=games(), meta={"n_sims": 4000})
    return stacks_tab.build_layout(
        stacks_tab.StackTabInputs(result=result, sheet_title="Week 7", now=NOW, week=7, verdict="tested")
    )


# ---- writing ----------------------------------------------------------------------------------------------


def test_write_stacks_writes_the_rows_then_styles_cues_formats_and_freezes():
    client, lay = FakeClient(), layout()
    msg = sheet_stacks.write_stacks(client, lay)
    assert msg == f"Stacks: built ({len(lay.rows)} rows)"
    assert client.calls[0][:2] == ("write_tab", "Stacks")
    assert client.calls[0][2] == lay.rows
    formatted = {c[2] for c in client.named("format_range")}
    for row in lay.cue_rows:
        assert f"A{row}" in formatted
    for rng, _p, _k in lay.formats:
        assert rng in formatted
    assert client.named("freeze")[0][1] == "Stacks"
    assert client.named("set_column_widths")[0][2]["M"] == sheet_stacks.COLUMN_PX["M"]


def test_the_tab_is_placed_right_after_suggested_in_the_build_colour():
    client = FakeClient(tabs=("Board", "Suggested", "Lineups", "Stacks"))
    assert sheet_stacks.place_after_suggested(client) == "Stacks: placed right after Suggested"
    (call,) = client.named("set_tab_properties")
    assert call[1] == "Stacks" and call[-1]["index"] == 2 and call[-1]["hidden"] is False
    client = FakeClient(tabs=("Stacks", "Board", "Suggested", "Lineups"))  # moving it from before Suggested
    sheet_stacks.place_after_suggested(client)
    assert client.named("set_tab_properties")[0][-1]["index"] == 2
    client = FakeClient(tabs=("Board",))
    assert "left where it was" in sheet_stacks.place_after_suggested(client)


def test_refresh_if_present_is_silent_without_the_tab_and_soft_when_it_cannot_build(monkeypatch):
    edge = pd.DataFrame({"Name": ["A"]})
    assert sheet_stacks.refresh_if_present(FakeClient(), SimpleNamespace(), edge=edge) == (True, "")
    client = FakeClient(tabs=("Stacks",))
    monkeypatch.setattr(stacks_inputs, "load_field_model", lambda path=None: None)
    ok, line = sheet_stacks.refresh_if_present(client, SimpleNamespace(), edge=edge)
    assert not ok and "dfs stacks --refit" in line
    assert not client.named("write_tab")

    def boom(*a, **k):
        raise RuntimeError("sheet unreachable")

    monkeypatch.setattr(sheet_stacks, "refresh", boom)
    ok, line = sheet_stacks.refresh_if_present(client, SimpleNamespace(), edge=edge)
    assert not ok and "sheet unreachable" in line and "dfs stacks" in line


def test_refresh_computes_and_writes_the_tab(monkeypatch):
    result = stacks.StackResult(table=table(), games=games(), meta={"n_sims": 4000})
    live = stacks_inputs.LiveSlate(
        slate=None, specs=[], ownership_published=True, game_info=pd.DataFrame(), week=7
    )
    model = FieldModel(rates=None, herd=None, weeks=(4, 5))
    monkeypatch.setattr(sheet_stacks, "compute", lambda edge, **kw: (result, live, model))
    monkeypatch.setattr(
        stacks_inputs, "load_retro", lambda path=None: {"line": "Track record: x", "verdict": "weak"}
    )
    client = FakeClient(tabs=("Stacks",))
    msg = sheet_stacks.refresh(client, SimpleNamespace(), edge=pd.DataFrame(), now=NOW)
    assert msg.startswith("Stacks: built")
    rows = client.named("write_tab")[0][2]
    assert any("Track record: x" in str(r[1]) for r in rows)
    assert any("Lev (weak signal)" in str(c) for r in rows for c in r)


# ---- the live slate ---------------------------------------------------------------------------------------


def edge_frame(own=True):
    rows = []
    for team, opp in (("AAA", "BBB"), ("BBB", "AAA")):
        rows.append((f"{team} QB", "QB", team, opp, 6000, 20.0, 0.10, "", 53.5, 3.0, 35.0))
        for k in range(3):
            rows.append(
                (f"{team} WR{k}", "WR", team, opp, 5000 - 500 * k, 14.0 - 2 * k, 0.12, "", 53.5, 3.0, 35.0)
            )
        rows.append((f"{team} RB", "RB", team, opp, 5500, 13.0, 0.15, "", 53.5, 3.0, 35.0))
        rows.append((f"{team} TE", "TE", team, opp, 3500, 8.0, 0.08, "OUT", 53.5, 3.0, 35.0))
        rows.append((f"{team} DST", "DST", team, opp, 2500, 6.0, 0.05, "", 53.5, 3.0, 35.0))
    df = pd.DataFrame(
        rows,
        columns=[
            "Name",
            "Position",
            "Team",
            "Opp",
            "Salary",
            "CalPts",
            "Own%",
            "Avail",
            "OverUnder",
            "Spread",
            "Pace",
        ],
    )
    if not own:
        df["Own%"] = np.nan
    return df


def test_ownership_is_published_when_any_player_is_above_zero():
    assert stacks_inputs.ownership_published(edge_frame())
    assert not stacks_inputs.ownership_published(edge_frame(own=False))
    assert not stacks_inputs.ownership_published(edge_frame().drop(columns="Own%"))
    assert not stacks_inputs.ownership_published(edge_frame().assign(**{"Own%": 0.0}))


def test_the_slate_frame_leaves_out_players_who_are_out_and_keeps_published_ownership():
    frame = stacks_inputs.slate_frame(edge_frame())
    assert "AAA TE" not in set(frame["name"]) and len(frame) == 14 - 2
    row = frame[frame["name"] == "AAA WR0"].iloc[0]
    assert row["own"] == pytest.approx(0.12) and row["salary"] == 5000


def test_the_stand_in_gives_each_position_the_share_of_the_slots_it_fills():
    edge = edge_frame(own=False)
    frame = stacks_inputs.slate_frame(edge)
    assert frame.loc[frame["position"] == "QB", "own"].sum() == pytest.approx(1.0)
    assert frame.loc[frame["position"] == "DST", "own"].sum() == pytest.approx(1.0)
    assert frame.loc[frame["position"].isin(["RB", "WR", "TE"]), "own"].sum() == pytest.approx(7.0)
    wr = frame[frame["position"] == "WR"].sort_values("name")
    assert wr["own"].is_monotonic_decreasing or wr["own"].nunique() > 1  # higher CalPts, higher share


def test_game_info_has_one_row_per_team_with_total_spread_and_pace():
    info = stacks_inputs.game_info(edge_frame()).set_index("team")
    assert list(info.index) == ["AAA", "BBB"]
    assert info.loc["AAA", "total"] == 53.5 and info.loc["AAA", "pace"] == 35.0


def test_the_json_caches_round_trip_and_a_missing_or_broken_file_reads_as_none(tmp_path):
    path = tmp_path / "x.json"
    assert stacks_inputs.load_json(path) is None
    stacks_inputs.save_json(path, {"a": 1})
    assert stacks_inputs.load_json(path) == {"a": 1}
    path.write_text("{not json")
    assert stacks_inputs.load_json(path) is None
    model = FieldModel(
        rates=field_gen.FieldRates(0.6, 0.2, 0.5, 0.3, (0.0, 100.0)),
        herd=field_gen.HerdModel(share=0.02, decay=0.5, k=20, lead_decay=0.6),
        weeks=(4, 5),
    )
    stacks_inputs.save_json(path, model.to_dict())
    back = stacks_inputs.load_field_model(path)
    assert back.rates == model.rates and back.herd == model.herd and back.weeks == (4, 5)
    stacks_inputs.save_json(path, {"rates": {}})
    assert stacks_inputs.load_field_model(path) is None
    assert stacks_inputs.load_retro(path) is None
    stacks_inputs.save_json(path, {"line": "x", "verdict": "weak"})
    assert stacks_inputs.load_retro(path)["verdict"] == "weak"


# ---- the commands -----------------------------------------------------------------------------------------

EDGE = pd.DataFrame({"Name": ["A"], "Position": ["QB"], "Team": ["ATL"]})


class Client:
    def describe(self):
        return "Week 7", "https://example.test/sheet"


def test_the_sync_hook_prints_the_ok_line_and_stays_quiet_without_a_tab(monkeypatch, capsys):
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(cli, "_depth_and_gsis", lambda: (None, {}))
    monkeypatch.setattr(sheet_stacks, "refresh_if_present", lambda *a, **k: (True, "Stacks: built (70 rows)"))
    cli._refresh_stacks(SimpleNamespace(), Client())
    assert "Stacks: built (70 rows)" in capsys.readouterr().out
    monkeypatch.setattr(sheet_stacks, "refresh_if_present", lambda *a, **k: (True, ""))
    cli._refresh_stacks(SimpleNamespace(), Client())
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(
        sheet_stacks, "refresh_if_present", lambda *a, **k: (False, "Stacks not refreshed (x)")
    )
    cli._refresh_stacks(SimpleNamespace(), Client())
    assert "Stacks not refreshed (x)" in capsys.readouterr().out

    def no_edge(name):
        raise FileNotFoundError(name)

    called = []
    monkeypatch.setattr(sheet_stacks, "refresh_if_present", lambda *a, **k: called.append(1))
    monkeypatch.setattr(cli.store, "load_current", no_edge)
    cli._refresh_stacks(SimpleNamespace(), Client())
    assert called == []


def _setup(monkeypatch):
    cfg = SimpleNamespace(google_sheets=SimpleNamespace(model_copy=lambda update: update))
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: Client())
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(cli, "_depth_and_gsis", lambda: (None, {}))


def test_setup_stacks_builds_then_places_the_tab_and_names_the_sheet(monkeypatch):
    _setup(monkeypatch)
    order = []
    monkeypatch.setattr(
        sheet_stacks, "refresh", lambda c, cfg, **kw: order.append("refresh") or "Stacks: built (60 rows)"
    )
    monkeypatch.setattr(
        sheet_stacks,
        "place_after_suggested",
        lambda c: order.append("place") or "Stacks: placed right after Suggested",
    )
    result = runner.invoke(cli.app, ["setup", "stacks", "--sheet-id", "abc"])
    assert result.exit_code == 0, result.output
    assert order == ["refresh", "place"]
    assert (
        "Building the Stacks tab on: Week 7" in result.output and "Stacks: built (60 rows)" in result.output
    )


def test_setup_stacks_says_what_to_run_when_there_is_no_field_model_or_no_edge(monkeypatch):
    _setup(monkeypatch)

    def no_model(*a, **k):
        raise sheet_stacks.StacksUnavailable("no fitted field model yet: run `dfs stacks --refit`")

    monkeypatch.setattr(sheet_stacks, "refresh", no_model)
    result = runner.invoke(cli.app, ["setup", "stacks"])
    assert result.exit_code == 1 and "dfs stacks --refit" in result.output

    def no_edge(name):
        raise FileNotFoundError("no edge snapshot")

    monkeypatch.setattr(cli.store, "load_current", no_edge)
    result = runner.invoke(cli.app, ["setup", "stacks"])
    assert result.exit_code == 1 and "Run `dfs sync` first" in result.output


def test_the_stacks_command_prints_the_page_and_writes_only_where_the_tab_exists(monkeypatch):
    _setup(monkeypatch)
    result_obj = stacks.StackResult(
        table=table(), games=games(), meta={"n_sims": 4000, "stacks": 25, "seconds": 3.0}
    )
    live = stacks_inputs.LiveSlate(None, [], True, pd.DataFrame(), 7)
    model = FieldModel(rates=None, herd=None, weeks=(4,))
    monkeypatch.setattr(sheet_stacks, "compute", lambda edge, **kw: (result_obj, live, model))
    monkeypatch.setattr(stacks_inputs, "load_retro", lambda path=None: None)
    written = []
    monkeypatch.setattr(
        sheet_stacks, "write_stacks", lambda c, lay: written.append(lay) or "Stacks: built (x rows)"
    )
    Client.tab_exists = lambda self, tab: False
    out = runner.invoke(cli.app, ["stacks"])
    assert out.exit_code == 0, out.output
    assert "TOP 20 QB STACKS" in out.output and "QB 0" in out.output
    assert "no Stacks tab" in out.output and not written
    Client.tab_exists = lambda self, tab: True
    out = runner.invoke(cli.app, ["stacks"])
    assert "Stacks: built (x rows)" in out.output and len(written) == 1
    written.clear()
    out = runner.invoke(cli.app, ["stacks", "--no-sheet"])
    assert out.exit_code == 0 and not written and "Sheet:" not in out.output
    del Client.tab_exists


def test_the_stacks_command_stops_with_a_message_when_no_field_model_is_fitted(monkeypatch):
    _setup(monkeypatch)

    def no_model(edge, **kw):
        raise sheet_stacks.StacksUnavailable("no fitted field model yet: run `dfs stacks --refit` once")

    monkeypatch.setattr(sheet_stacks, "compute", no_model)
    out = runner.invoke(cli.app, ["stacks", "--no-sheet"])
    assert out.exit_code == 1 and "dfs stacks --refit" in out.output


def test_compute_needs_a_fitted_field_model(monkeypatch):
    monkeypatch.setattr(stacks_inputs, "load_field_model", lambda path=None: None)
    with pytest.raises(sheet_stacks.StacksUnavailable, match="--refit"):
        sheet_stacks.compute(edge_frame())


def test_the_protection_list_and_doctor_know_the_tab():
    from dfs.sheet_protection import FULLY_PROTECTED_TABS

    assert stacks_tab.STACKS_TAB in FULLY_PROTECTED_TABS
    assert json.dumps(stacks_tab.STACKS_TAB) == '"Stacks"'


def test_compute_runs_the_whole_pipeline_on_a_synthetic_edge_frame(monkeypatch, tmp_path):
    from tests.test_stacks import RATES, TEAMS, make_slate

    slate, specs = make_slate()
    rows = []
    for i, (name, spec) in enumerate(zip(slate.names, specs, strict=True)):
        rows.append(
            {
                "Id": 1000 + i,
                "Name": name,
                "Position": spec.position,
                "Team": spec.team,
                "Opp": spec.opp,
                "Salary": spec.salary,
                "CalPts": spec.projection,
                "Own%": float(slate.own[i]),
                "Avail": "",
                "GameID": spec.game_id,
                "Tgt%": spec.tshare,
                "OverUnder": 45.0,
                "Spread": 3.0,
                "Pace": 33.0,
            }
        )
    edge = pd.DataFrame(rows)
    assert set(edge["Team"]) == set(TEAMS)
    path = tmp_path / "stack_field.json"
    model = FieldModel(rates=RATES, herd=field_gen.HerdModel(share=0.0), weeks=(4, 5))
    stacks_inputs.save_json(path, model.to_dict())
    monkeypatch.setattr(stacks_inputs, "FIELD_MODEL_FILE", path)
    result, live, back = sheet_stacks.compute(edge, week=7, n_sims=250)
    assert live.ownership_published and live.week == 7
    assert back.weeks == (4, 5)
    assert len(result.table) > 20 and result.table["qb"].nunique() == len(TEAMS)
    assert set(result.games["game"]) <= {"AAA v BBB", "CCC v DDD", "EEE v FFF"}
    assert result.games["total"].eq(45.0).all()
    unpublished = sheet_stacks.compute(edge.assign(**{"Own%": np.nan}), week=7, n_sims=250)[1]
    assert not unpublished.ownership_published


def test_the_sync_hook_never_raises_even_when_the_depth_chart_read_blows_up(monkeypatch, capsys):
    monkeypatch.setattr(cli.store, "load_current", lambda name: EDGE)

    def boom():
        raise RuntimeError("depth chart unreadable")

    monkeypatch.setattr(cli, "_depth_and_gsis", boom)
    cli._refresh_stacks(SimpleNamespace(), Client())
    assert "Stacks not refreshed (depth chart unreadable)" in capsys.readouterr().out
