import re

import pandas as pd
import pytest

from dfs.sources import weather as weather_module
from dfs.sources.base import SyncContext
from dfs.sources.weather import (
    OPEN_METEO_URL,
    WeatherFetchError,
    WeatherSource,
    outdoor_games_needing_weather,
    parse_forecast_response,
)


def _games(rows: list[dict]) -> pd.DataFrame:
    base = {
        "GameId": "2026_01_A_B",
        "Away": "A",
        "Home": "B",
        "Date": "2026-09-13",
        "Time": "13:00",
        "Stadium": "Lumen Field",
        "Roof": "outdoors",
        "Surface": "grass",
        "AwayRest": 7,
        "HomeRest": 7,
        "DivGame": 0,
        "Spread": -3.0,
        "Total": 45.0,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def _forecast_payload(
    times: list[str], temp: list[float], wind: list[float], gust: list[float], precip: list[float]
):
    return {
        "hourly": {
            "time": times,
            "temperature_2m": temp,
            "wind_speed_10m": wind,
            "wind_gusts_10m": gust,
            "precipitation": precip,
        }
    }


def test_outdoor_games_needing_weather_filters_by_roof():
    games = _games(
        [
            {"GameId": "outdoor", "Roof": "outdoors"},
            {"GameId": "dome", "Roof": "dome"},
            {"GameId": "unknown_roof", "Roof": ""},
        ]
    )
    outdoor = outdoor_games_needing_weather(games)
    assert list(outdoor["GameId"]) == ["outdoor"]


def test_outdoor_games_needing_weather_raises_on_unknown_stadium():
    games = _games([{"Stadium": "Some Brand New Stadium Nobody Has Coordinates For"}])
    with pytest.raises(WeatherFetchError, match="No coordinates on file"):
        outdoor_games_needing_weather(games)


def test_parse_forecast_response_rounds_kickoff_to_nearest_hour():
    payload = _forecast_payload(
        times=["2026-09-13T12:00", "2026-09-13T13:00"],
        temp=[70.0, 68.0],
        wind=[5.0, 22.0],
        gust=[10.0, 30.0],
        precip=[0.0, 0.1],
    )
    result = parse_forecast_response(payload, "2026-09-13", "13:20")
    assert result == {"Temp": 68.0, "Wind": 22.0, "Gust": 30.0, "Precip": 0.1}


def test_parse_forecast_response_raises_when_hour_missing():
    payload = _forecast_payload(
        times=["2026-09-13T12:00"], temp=[70.0], wind=[5.0], gust=[10.0], precip=[0.0]
    )
    with pytest.raises(WeatherFetchError, match="no hour matching"):
        parse_forecast_response(payload, "2026-09-13", "18:00")


def test_fetch_builds_weather_frame_and_flags_high_wind(monkeypatch, httpx_mock):
    games = _games(
        [
            {"GameId": "windy", "Stadium": "Lumen Field", "Date": "2026-09-13", "Time": "13:00"},
            {"GameId": "calm", "Stadium": "Soldier Field", "Date": "2026-09-13", "Time": "16:00"},
        ]
    )
    monkeypatch.setattr(weather_module.store, "load_current", lambda name: games)

    httpx_mock.add_response(
        url=re.compile(re.escape(OPEN_METEO_URL)),
        json=_forecast_payload(
            times=["2026-09-13T13:00"], temp=[65.0], wind=[25.0], gust=[35.0], precip=[0.0]
        ),
    )
    httpx_mock.add_response(
        url=re.compile(re.escape(OPEN_METEO_URL)),
        json=_forecast_payload(
            times=["2026-09-13T16:00"], temp=[72.0], wind=[8.0], gust=[12.0], precip=[0.0]
        ),
    )

    df = WeatherSource().fetch(SyncContext(week=1, season=2026))

    assert len(df) == 2
    windy = df[df["GameId"] == "windy"].iloc[0]
    calm = df[df["GameId"] == "calm"].iloc[0]
    assert windy["Flag"] == "WIND"
    assert calm["Flag"] == ""


def test_fetch_returns_empty_frame_when_no_outdoor_games(monkeypatch):
    games = _games([{"Roof": "dome"}])
    monkeypatch.setattr(weather_module.store, "load_current", lambda name: games)

    df = WeatherSource().fetch(SyncContext(week=1, season=2026))
    assert df.empty
    assert list(df.columns) == ["GameId", "Away", "Home", "Stadium", "Temp", "Wind", "Gust", "Precip", "Flag"]
