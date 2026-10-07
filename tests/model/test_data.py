"""Fetching and caching: release-asset URLs only, clear failures, completed seasons fetched once."""

import io
import re

import httpx
import pandas as pd
import pytest

from dfs.model import data


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path / "model_cache")
    return tmp_path / "model_cache"


def _parquet(**cols) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(cols or {"x": [1, 2]}).to_parquet(buf)
    return buf.getvalue()


def test_urls_are_release_assets_never_raw_github_paths():
    urls = [*(u.format(season=2024) for u in data.SEASON_SOURCES.values()), data.GAMES_URL]
    assert all("/releases/download/" in u for u in urls)
    assert not any("/raw/" in u for u in urls)


def test_team_codes_are_normalised():
    assert [data.normalize_team(t) for t in ("LA", "STL", "OAK", "SD", "KC", " sf ")] == [
        "LAR",
        "LAR",
        "LV",
        "LAC",
        "KC",
        "SF",
    ]
    assert pd.isna(data.normalize_team(float("nan")))


def test_a_season_file_is_downloaded_and_cached(httpx_mock, cache):
    httpx_mock.add_response(url=data.STATS_PLAYER_URL.format(season=2024), content=_parquet(a=[1, 2, 3]))
    path = data.fetch_season_file("stats_player", 2024)
    assert path == cache / "stats_player_2024.parquet"
    assert len(data.read_season_files("stats_player", [2024])) == 3


def test_a_failed_request_raises_rather_than_returning_an_empty_frame(httpx_mock):
    httpx_mock.add_response(url=data.GAMES_URL, status_code=404)
    with pytest.raises(data.ModelDataError, match="games.parquet"):
        data.fetch_games()


def test_a_network_error_raises(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("no route"), url=data.GAMES_URL)
    with pytest.raises(data.ModelDataError, match="failed"):
        data.fetch_games()


def test_a_non_parquet_response_is_rejected_and_not_cached(httpx_mock, cache):
    httpx_mock.add_response(url=data.GAMES_URL, content=b"<html>rate limited</html>")
    with pytest.raises(data.ModelDataError, match="not a readable parquet"):
        data.fetch_games()
    assert not (cache / "games.parquet").exists()


def test_reading_an_uncached_season_says_to_fetch():
    with pytest.raises(data.ModelDataError, match="dfs model fetch"):
        data.read_season_files("ep_weekly", [2020])
    with pytest.raises(data.ModelDataError, match="dfs model fetch"):
        data.read_games()


def test_read_season_files_concatenates_and_selects_columns(httpx_mock):
    for season in (2023, 2024):
        httpx_mock.add_response(
            url=data.EP_WEEKLY_URL.format(season=season), content=_parquet(a=[season], b=[0], c=[1])
        )
        data.fetch_season_file("ep_weekly", season)
    frame = data.read_season_files("ep_weekly", [2023, 2024], columns=["a", "c", "absent"])
    assert frame.columns.tolist() == ["a", "c"] and frame["a"].tolist() == [2023, 2024]


def _serve_everything(httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*\.parquet$"), content=_parquet(), is_reusable=True)


def test_completed_seasons_are_fetched_once_and_the_current_season_every_time(httpx_mock, monkeypatch):
    monkeypatch.setattr(data, "FIRST_SEASON", 2023)
    _serve_everything(httpx_mock)
    first = data.fetch_history(2025)
    assert first == [2023, 2024]
    requested = [r.url.path.rsplit("/", 1)[1] for r in httpx_mock.get_requests()]
    assert (
        len(requested) == 2 * 3 + 3 + 1
    )  # two completed seasons x 3 sources, current x 3, plus the schedule
    assert "stats_player_week_2025.parquet" in requested and "games.parquet" in requested

    httpx_mock.reset()
    _serve_everything(httpx_mock)
    assert data.fetch_history(2025) == []  # nothing completed was missing
    again = [r.url.path.rsplit("/", 1)[1] for r in httpx_mock.get_requests()]
    assert sorted(again) == sorted(
        [
            "games.parquet",
            "stats_player_week_2025.parquet",
            "stats_team_week_2025.parquet",
            "ep_weekly_2025.parquet",
        ]
    )

    httpx_mock.reset()
    _serve_everything(httpx_mock)
    assert data.fetch_history(2025, refresh_all=True) == [2023, 2024]


def test_a_season_with_no_stats_yet_is_skipped_not_an_error(httpx_mock):
    httpx_mock.add_response(url=data.GAMES_URL, content=_parquet())
    for source in data.SEASON_SOURCES:
        httpx_mock.add_response(url=data.SEASON_SOURCES[source].format(season=2026), status_code=404)
    messages = []
    data.refresh_season(2026, messages.append)  # must not raise
    assert len(messages) == 3 and all("not available yet" in m for m in messages)
