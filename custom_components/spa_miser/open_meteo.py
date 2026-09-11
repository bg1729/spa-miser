"""Backfills historical outdoor temperature/wind from Open-Meteo's free,
public historical weather archive - no API key or account required.

Used only to fill GAPS in the user's real outdoor temperature sensor's
recorder history (typically right after that sensor is first enabled, since
HA doesn't record history for disabled entities), and only once the user has
explicitly opted in - see button.spa_miser_estimate_initial_model, which is
what actually turns this on. Sends the HA instance's own configured
latitude/longitude (Settings -> System -> General - no more precise than
whatever the user already set there) to archive-api.open-meteo.com.
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SECONDS = 15


async def async_fetch_historical_ambient(
    hass: HomeAssistant, start: datetime, end: datetime
) -> dict[datetime, tuple[float, float]]:
    """Return {hour_start_utc: (temperature_c, wind_speed_ms)}.

    Best-effort supplement, not a required data source: returns {} on any
    failure (network, missing location, unparseable response) rather than
    raising, so a refit still proceeds with whatever real data exists.
    """
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    # Not `if not latitude`: 0.0 is a real location (the equator), not
    # "unset" - only None means HA has no location configured at all.
    if latitude is None or longitude is None:
        return {}

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": dt_util.as_utc(start).date().isoformat(),
        "end_date": dt_util.as_utc(end).date().isoformat(),
        "hourly": "temperature_2m,wind_speed_10m",
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }

    session = async_get_clientsession(hass)
    try:
        response = await session.get(
            ARCHIVE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = await response.json()
    except Exception:  # noqa: BLE001 - a failed backfill must not block fitting
        _LOGGER.exception("Failed to fetch Open-Meteo historical weather")
        return {}

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    winds = hourly.get("wind_speed_10m", [])

    result: dict[datetime, tuple[float, float]] = {}
    for iso_time, temp, wind in zip(times, temps, winds, strict=False):
        if temp is None:
            continue
        parsed = dt_util.parse_datetime(iso_time)
        if parsed is None:
            continue
        hour = parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)
        result[hour] = (float(temp), float(wind) if wind is not None else 0.0)
    return result
