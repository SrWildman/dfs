"""Test-wide safety nets."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_cockpit_store(tmp_path, monkeypatch):
    """`dfs lineups late-swap` saves its run under `data/` for the Cockpit tab; `data/` is a symlink to a real
    week's data in a worktree, so no test may write there."""
    from dfs import late_swap_store

    monkeypatch.setattr(late_swap_store, "STORE_DIR", tmp_path / "cockpit")


@pytest.fixture(autouse=True)
def _isolated_signal_table(tmp_path, monkeypatch):
    """`dfs results update` keeps the signal records for the Player card under `data/results/`; no test may
    write there (it is a symlink to a real week's data in a worktree)."""
    from dfs import results_update

    monkeypatch.setattr(
        results_update, "signal_table_path", lambda season: tmp_path / f"signals_{season}.csv"
    )
