"""Wind/precipitation/temperature for this week's outdoor games, from
Open-Meteo -- free, no API key at all (unlike WeatherAPI.com, which
docs/HANDOFF.md originally suggested).

Depends on `nflverse_games` having synced first (reads `GamesRaw` via
`store.load_current`, same pattern as `edge.py`) -- `Roof == "outdoors"` is
what decides which games even need a lookup, so no separate dome/stadium
type table is needed beyond that.

nflverse's `gametime` (and therefore this week's synced `Time` column) is
in US/Eastern regardless of the stadium's own timezone -- confirmed against
known kickoff slots (an 20:15 MNF game at Arrowhead, Kansas City, which is
Central). Rather than maintaining a per-stadium timezone table just to
convert back and forth, every Open-Meteo request is pinned to
`timezone=America/New_York`, so the hourly timestamps it returns line up
directly with `Time` with no conversion at all.

Stadium coordinates are a static table (`STADIUM_COORDINATES`) built from
every stadium name nflverse used across the full 2026 schedule, including
this season's international games (Madrid, Mexico City, Munich, Rio,
Melbourne, twice in London). An outdoor game at a stadium *not* in this
table raises rather than silently skipping -- deliberately, since Week 1
2026 already has a real game at the Melbourne Cricket Ground (nflverse
currently classifies its roof as "dome", so it happens not to trigger a
lookup this particular week, but that's nflverse's data, not something to
rely on)."""

from __future__ import annotations

import httpx
import pandas as pd

from dfs import store
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WIND_FLAG_THRESHOLD_MPH = 20.0

WEATHER_COLUMNS = ["GameId", "Away", "Home", "Stadium", "Temp", "Wind", "Gust", "Precip", "Flag"]

# (latitude, longitude) for every stadium nflverse names across the full
# 2026 schedule (`games.csv`, season 2026, every week) -- see module
# docstring for why an outdoor game at a stadium missing here raises
# instead of being silently skipped.
STADIUM_COORDINATES: dict[str, tuple[float, float]] = {
    "AT&T Stadium": (32.7473, -97.0945),
    "Acrisure Stadium": (40.4468, -80.0158),
    "Allegiant Stadium": (36.0909, -115.1833),
    "Bank of America Stadium": (35.2258, -80.8528),
    "Bernabeu": (40.4531, -3.6883),
    "Caesars Superdome": (29.9511, -90.0812),
    "Empower Field at Mile High": (39.7439, -105.0201),
    "Estadio Banorte": (19.3029, -99.1505),
    "EverBank Stadium": (30.3239, -81.6373),
    "FC Bayern Munich Stadium": (48.2188, 11.6247),
    "Ford Field": (42.3400, -83.0456),
    "GEHA Field at Arrowhead Stadium": (39.0489, -94.4839),
    "Gillette Stadium": (42.0909, -71.2643),
    "Hard Rock Stadium": (25.9580, -80.2389),
    "Highmark Stadium": (42.7738, -78.7870),
    "Huntington Bank Field": (41.5061, -81.6995),
    "Lambeau Field": (44.5013, -88.0622),
    "Levi's Stadium": (37.4032, -121.9698),
    "Lincoln Financial Field": (39.9008, -75.1675),
    "Lucas Oil Stadium": (39.7601, -86.1639),
    "Lumen Field": (47.5952, -122.3316),
    "M&T Bank Stadium": (39.2780, -76.6227),
    "Maracana Stadium": (-22.9121, -43.2302),
    "Melbourne Cricket Ground": (-37.8199, 144.9834),
    "Mercedes-Benz Stadium": (33.7554, -84.4008),
    "MetLife Stadium": (40.8135, -74.0745),
    "Nissan Stadium": (36.1665, -86.7713),
    "Northwest Stadium": (38.9077, -76.8645),
    "Paycor Stadium": (39.0955, -84.5160),
    "Raymond James Stadium": (27.9759, -82.5033),
    "Reliant Stadium": (29.6847, -95.4107),
    "SoFi Stadium": (33.9535, -118.3392),
    "Soldier Field": (41.8623, -87.6167),
    "Stade de France": (48.9244, 2.3601),
    "State Farm Stadium": (33.5276, -112.2626),
    "Tottenham Hotspur Stadium": (51.6043, -0.0664),
    "U.S. Bank Stadium": (44.9737, -93.2577),
    "Wembley Stadium": (51.5560, -0.2795),
}


class WeatherFetchError(Exception):
    pass


def outdoor_games_needing_weather(games: pd.DataFrame) -> pd.DataFrame:
    """This week's games with Roof == "outdoors" -- raises if any of them
    is at a stadium missing from STADIUM_COORDINATES, rather than silently
    dropping it from the weather tab."""
    outdoor = games[games["Roof"] == "outdoors"].copy()
    unknown = sorted(set(outdoor["Stadium"]) - set(STADIUM_COORDINATES))
    if unknown:
        raise WeatherFetchError(
            f"No coordinates on file for stadium(s) {unknown} -- add them to "
            "STADIUM_COORDINATES in src/dfs/sources/weather.py before this week's "
            "weather can sync."
        )
    return outdoor


def _round_to_hour(time_str: str) -> int:
    hour, minute = (int(p) for p in time_str.split(":"))
    return (hour + 1) % 24 if minute >= 30 else hour


def parse_forecast_response(payload: dict, date: str, kickoff_time: str) -> dict:
    """One Open-Meteo hourly response -> the single hour matching kickoff.
    `date`/`kickoff_time` are `GamesRaw`'s Date ("YYYY-MM-DD") and Time
    ("HH:MM", US/Eastern) columns."""
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    target = f"{date}T{_round_to_hour(kickoff_time):02d}:00"
    try:
        idx = times.index(target)
    except ValueError as e:
        raise WeatherFetchError(
            f"Open-Meteo's forecast for {date} had no hour matching kickoff ({target!r})."
        ) from e

    return {
        "Temp": hourly["temperature_2m"][idx],
        "Wind": hourly["wind_speed_10m"][idx],
        "Gust": hourly["wind_gusts_10m"][idx],
        "Precip": hourly["precipitation"][idx],
    }


class WeatherSource(Source):
    name = "weather"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        # ctx unused: the week is implicit in the already-synced GamesRaw.
        games = store.load_current("nflverse_games")
        outdoor = outdoor_games_needing_weather(games)
        log.info("fetching weather for %d outdoor game(s)", len(outdoor))

        rows = []
        for _, game in outdoor.iterrows():
            lat, lon = STADIUM_COORDINATES[game["Stadium"]]
            try:
                resp = httpx.get(
                    OPEN_METEO_URL,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m",
                        "start_date": game["Date"],
                        "end_date": game["Date"],
                        "timezone": "America/New_York",
                        "temperature_unit": "fahrenheit",
                        "wind_speed_unit": "mph",
                        "precipitation_unit": "inch",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise WeatherFetchError(f"Open-Meteo request failed for {game['Stadium']}: {e}") from e

            weather = parse_forecast_response(resp.json(), game["Date"], game["Time"])
            rows.append(
                {
                    "GameId": game["GameId"],
                    "Away": game["Away"],
                    "Home": game["Home"],
                    "Stadium": game["Stadium"],
                }
                | weather
            )

        df = pd.DataFrame(rows, columns=WEATHER_COLUMNS[:-1])
        df["Flag"] = df["Wind"].apply(
            lambda w: "WIND" if pd.notna(w) and w >= WIND_FLAG_THRESHOLD_MPH else ""
        )
        return df[WEATHER_COLUMNS]
