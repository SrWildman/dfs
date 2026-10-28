import pytest


@pytest.fixture(autouse=True)
def _isolated_season_archives(tmp_path, monkeypatch):
    """The season commands keep dated copies of the market's and our ranks under `data/season/`; `data/` is a
    symlink to real data in a worktree, so no test may write there."""
    from dfs.season_long import consensus, consensus_check

    monkeypatch.setattr(consensus, "HISTORY_DIR", tmp_path / "fp_history")
    monkeypatch.setattr(consensus_check, "OURS_DIR", tmp_path / "ros_history")
