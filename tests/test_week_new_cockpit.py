"""`dfs week new` rebuilds the Cockpit on the new sheet: only when there is one, from the edge frame the sync
just saved, and never failing the week. The sheet and the cache are replaced by fakes."""

import inspect

import pandas as pd

from dfs import cli, sheet_cockpit, store

EDGE = pd.DataFrame({"Id": [1], "Name": ["A"]})


def test_the_cockpit_is_rebuilt_from_the_saved_edge_frame_on_the_new_sheet(monkeypatch, capsys):
    seen = {}

    def fake_refresh(client, cfg, *, edge, previous_edge=None, now=None):
        seen.update(client=client, cfg=cfg, edge=edge, previous=previous_edge)
        return True, "Cockpit: written"

    monkeypatch.setattr(store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(sheet_cockpit, "refresh_if_present", fake_refresh)
    cli._rebuild_cockpit_on_new_sheet("new-cfg", "new-client")
    assert seen == {"client": "new-client", "cfg": "new-cfg", "edge": EDGE, "previous": None}
    assert "Cockpit: written" in capsys.readouterr().out


def test_a_sheet_without_a_cockpit_tab_prints_nothing_and_writes_nothing(monkeypatch, capsys):
    class Client:
        def tab_exists(self, tab):
            return False

        def __getattr__(self, name):  # any write would land here
            raise AssertionError(f"unexpected sheet call {name}")

    monkeypatch.setattr(store, "load_current", lambda name: EDGE)
    cli._rebuild_cockpit_on_new_sheet(None, Client())
    assert capsys.readouterr().out == ""


def test_no_saved_edge_frame_means_nothing_to_render_and_no_failure(monkeypatch, capsys):
    def missing(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(store, "load_current", missing)
    monkeypatch.setattr(
        sheet_cockpit, "refresh_if_present", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    cli._rebuild_cockpit_on_new_sheet(None, None)
    assert capsys.readouterr().out == ""


def test_anything_going_wrong_is_reported_and_skipped_never_raised(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(store, "load_current", lambda name: EDGE)
    monkeypatch.setattr(sheet_cockpit, "refresh_if_present", boom)
    cli._rebuild_cockpit_on_new_sheet(None, None)  # must not raise
    out = capsys.readouterr().out
    assert "Cockpit not rebuilt" in out and "quota exceeded" in out and "dfs setup cockpit" in out
    monkeypatch.setattr(store, "load_current", boom)  # even the cache read is covered
    cli._rebuild_cockpit_on_new_sheet(None, None)


def test_week_new_calls_it_after_the_sync_and_before_it_can_exit_on_a_failed_source():
    source = inspect.getsource(cli.week_new)
    sync = source.index("run_sync(")
    rebuild = source.index("_rebuild_cockpit_on_new_sheet(new_cfg, new_client)")
    exit_on_failure = source.index("if any_failed:")
    assert sync < rebuild < exit_on_failure
