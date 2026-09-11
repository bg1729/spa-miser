"""Tests for the Open-Meteo historical-weather backfill adapter."""
from __future__ import annotations

from datetime import datetime, timezone

from custom_components.spa_miser.open_meteo import (
    ARCHIVE_URL,
    async_fetch_historical_ambient,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 2, tzinfo=timezone.utc)

ARCHIVE_RESPONSE = {
    "hourly": {
        "time": ["2026-01-01T00:00", "2026-01-01T01:00", "2026-01-01T02:00"],
        "temperature_2m": [5.1, 4.8, None],
        "wind_speed_10m": [3.2, 3.5, 3.0],
    }
}


async def test_fetches_and_parses_hourly_ambient_data(hass, aioclient_mock):
    hass.config.latitude = 51.9
    hass.config.longitude = -2.1
    aioclient_mock.get(ARCHIVE_URL, json=ARCHIVE_RESPONSE)

    result = await async_fetch_historical_ambient(hass, START, END)

    assert len(result) == 2  # the None-temperature hour is skipped
    hour0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert result[hour0] == (5.1, 3.2)


async def test_missing_location_returns_empty_without_a_request(hass, aioclient_mock):
    hass.config.latitude = None
    hass.config.longitude = None

    result = await async_fetch_historical_ambient(hass, START, END)

    assert result == {}
    assert aioclient_mock.call_count == 0


async def test_request_failure_returns_empty_not_raises(hass, aioclient_mock):
    hass.config.latitude = 51.9
    hass.config.longitude = -2.1
    aioclient_mock.get(ARCHIVE_URL, status=500)

    result = await async_fetch_historical_ambient(hass, START, END)

    assert result == {}


async def test_equatorial_location_is_not_treated_as_missing(hass, aioclient_mock):
    hass.config.latitude = 0.0
    hass.config.longitude = 0.0
    aioclient_mock.get(ARCHIVE_URL, json=ARCHIVE_RESPONSE)

    result = await async_fetch_historical_ambient(hass, START, END)

    assert len(result) == 2
    assert aioclient_mock.call_count == 1
