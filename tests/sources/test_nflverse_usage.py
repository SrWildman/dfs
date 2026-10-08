"""`NflverseUsageSource`: fail soft, never last season, raw snapshot."""

import io

import pandas as pd

from dfs.sources import nflverse_usage
from dfs.sources.base import SyncContext
from dfs.usage_metrics import USAGE_SOURCE_COLUMNS


def _stats_parquet() -> bytes:
    rows = []
    for week in (1, 2, 3):
        rows += [
            {
                "player_id": "w1",
                "player_display_name": "Wide One",
                "position": "WR",
                "season_type": "REG",
                "week": week,
                "team": "AAA",
                "targets": 6,
                "carries": 0,
                "receiving_air_yards": 60,
                "passing_air_yards": 0,
            },
            {
                "player_id": "q1",
                "player_display_name": "Quarter Back",
                "position": "QB",
                "season_type": "REG",
                "week": week,
                "team": "AAA",
                "targets": 0,
                "carries": 2,
                "receiving_air_yards": 0,
                "passing_air_yards": 100,
            },
        ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_parquet(buf)
    return buf.getvalue()


def _redirect_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(nflverse_usage, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(nflverse_usage, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(nflverse_usage, "ensure_data_dirs", lambda: None)


def test_stats_file_unavailable_returns_an_empty_frame_with_a_warning_not_an_error(monkeypatch, tmp_path):
    _redirect_raw(monkeypatch, tmp_path)

    def boom(url, *, what):
        raise nflverse_usage.NflverseUsageFetchError(f"Request for {what} failed: 404")

    monkeypatch.setattr(nflverse_usage, "_download", boom)
    frame = nflverse_usage.NflverseUsageSource().fetch(SyncContext(week=1, season=2026))
    assert frame.empty
    assert list(frame.columns) == USAGE_SOURCE_COLUMNS  # also overwrites any stale current csv
    assert not (tmp_path / "raw").exists()  # nothing to snapshot


def test_fetch_computes_usage_and_snapshots_the_raw_stats_file(monkeypatch, tmp_path):
    _redirect_raw(monkeypatch, tmp_path)
    stats_bytes = _stats_parquet()
    urls = []

    def fake_download(url, *, what):
        urls.append(url)
        if "stats_player" in url:
            return stats_bytes
        raise nflverse_usage.NflverseUsageFetchError("pbp down")

    monkeypatch.setattr(nflverse_usage, "_download", fake_download)
    frame = nflverse_usage.NflverseUsageSource().fetch(SyncContext(week=4, season=2026))

    assert "stats_player_week_2026.parquet" in urls[0]  # this season's file, never last season's
    assert all("2025" not in u for u in urls)
    wide = frame.set_index("GsisId").loc["w1"]
    assert wide["Tgt%"] == 1.0  # all of AAA's targets
    assert pd.isna(wide["RZ/G"])  # pbp failed: only the red-zone columns blank
    snapshots = list((tmp_path / "raw" / "stats_player").glob("*.parquet"))
    assert len(snapshots) == 1 and snapshots[0].read_bytes() == stats_bytes


def test_usage_source_has_no_sheet_tab_and_sits_before_edge():
    from dfs.sources import SOURCES

    assert nflverse_usage.NflverseUsageSource.uploads_to_sheet is False
    names = list(SOURCES)
    assert names.index("usage") < names.index("edge")
    assert names.index("pbp") < names.index("edge")


def test_the_weekly_red_zone_table_is_saved_and_removed_when_pbp_is_missing(monkeypatch, tmp_path):
    _redirect_raw(monkeypatch, tmp_path)
    saved = tmp_path / "current" / nflverse_usage.REDZONE_WEEKLY_FILE
    pbp = pd.DataFrame(
        {
            "game_id": ["g1"],
            "week": [1],
            "season_type": ["REG"],
            "play_type": ["pass"],
            "pass": [1],
            "rush": [0],
            "yardline_100": [8],
            "receiver_player_id": ["w1"],
            "rusher_player_id": [None],
            "two_point_attempt": [0],
            "play_deleted": [0],
        }
    )
    nflverse_usage._save_redzone_weekly(pbp)
    weekly = pd.read_csv(saved)
    assert weekly.loc[0, "GsisId"] == "w1" and weekly.loc[0, "rz"] == 1 and weekly.loc[0, "hvt"] == 1
    nflverse_usage._save_redzone_weekly(None)  # a failed pbp fetch must not leave last sync's counts behind
    assert not saved.exists()
