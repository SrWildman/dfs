import pytest


@pytest.fixture(autouse=True)
def _isolated_season_archives(tmp_path, monkeypatch):
    """The season commands keep dated copies of the market's and our ranks under `data/season/`; `data/` is a
    symlink to real data in a worktree, so no test may write there."""
    from dfs.season_long import consensus, consensus_check

    monkeypatch.setattr(consensus, "HISTORY_DIR", tmp_path / "fp_history")
    monkeypatch.setattr(consensus_check, "OURS_DIR", tmp_path / "ros_history")


@pytest.fixture(autouse=True)
def _no_sleeper_stats(monkeypatch):
    """Sleeper's weekly stats come from the network (api.sleeper.com) through a cache under `data/season/`.
    Before the tests were hermetic, two waiver tests read whatever that real cache held (so they passed or
    failed by machine and test order) and, with no cache (the cloud), called the API for real and got a 403.
    Every test here sees "no stats published yet" unless it fakes its own."""
    from dfs.season_long import sleeper_league as sl

    monkeypatch.setattr(sl, "fetch_week_stats", lambda season, week, final=False: [])
