"""Pulls historical hourly long-term statistics from HA's recorder.

Used to build the dataset the thermal model is fit against. Statistics (not
raw history) are used deliberately: 5-minute-resolution history is only kept
~10 days by default, while hourly statistics are retained indefinitely
(subject to the user's own recorder purge settings) - exactly what's needed
for fitting against several weeks of data without any extra database.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import open_meteo
from .thermal_model import HourlySample

# Power sensors are assumed to report Watts (the HA `power` device_class
# convention); statistics are converted to kW for the model.
WATTS_PER_KW = 1000.0

# Once the real outdoor sensor has at least this many hours of its own
# history, stop bothering to call Open-Meteo at all - the whole point is
# bridging the gap right after that sensor is first enabled (HA records no
# history for a disabled entity), not an ongoing dependency once real data
# is plentiful.
BACKFILL_TRIGGER_MAX_REAL_HOURS = 24 * 7


def _parse_stat_start(value) -> datetime | None:
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value)
    return None


async def _async_fetch_hourly_stat(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    stat_type: str,
) -> dict[datetime, float]:
    """Return {hour_start_utc: value} from one entity's long-term statistics.

    Falls back to aggregating raw recorder history ourselves if the entity
    has none at all - discovered live against a real gateway whose water-
    temperature sensor never declares a state_class in its own MQTT
    discovery config, which silently means HA generates no long-term
    statistics for it, however long you wait. Raw history is only kept
    ~10 days by default, but that's still comfortably more than
    MIN_SAMPLES_FOR_FIT needs.
    """
    instance = get_instance(hass)
    stats = await instance.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {entity_id},
        "hour",
        None,
        {stat_type},
    )
    result: dict[datetime, float] = {}
    for row in stats.get(entity_id, []):
        value = row.get(stat_type)
        hour = _parse_stat_start(row.get("start"))
        if value is None or hour is None:
            continue
        result[hour] = float(value)

    if result:
        return result
    return await _async_fetch_hourly_from_raw_history(hass, entity_id, start, end, stat_type)


async def _async_fetch_hourly_from_raw_history(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    stat_type: str,
) -> dict[datetime, float]:
    instance = get_instance(hass)
    changes = await instance.async_add_executor_job(
        state_changes_during_period, hass, start, end, entity_id
    )
    points: list[tuple[datetime, float]] = []
    for state in changes.get(entity_id, []):
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            continue
        points.append((dt_util.as_utc(state.last_updated), value))
    points.sort(key=lambda p: p[0])
    if not points:
        return {}

    if stat_type == "max":
        # A genuinely quiet hour (no recorded point in it) has no evidence
        # of what the peak was during it - unlike "mean" below, carrying a
        # forward-filled value into it would understate a real spike that
        # happened to fall in a gap, so only hours with an actual point are
        # reported here.
        buckets: dict[datetime, list[float]] = {}
        for ts, value in points:
            hour = ts.replace(minute=0, second=0, microsecond=0)
            buckets.setdefault(hour, []).append(value)
        return {hour: max(values) for hour, values in buckets.items()}

    # "mean": forward-fill. Diagnosed live against a real gateway - a
    # slowly-varying sensor (like water temperature) that hasn't reported a
    # new state within a given hour overwhelmingly means the value simply
    # hasn't changed (exactly why an MQTT-sourced sensor wouldn't
    # republish), not that it's unknown for that hour. Sampling the last
    # known value at each hour boundary - rather than only counting hours
    # with a literal change event in them - turned a sparse ~20% delta-pair
    # yield into near-complete coverage for a tub that changes temperature
    # slowly for hours at a time.
    result: dict[datetime, float] = {}
    hour = dt_util.as_utc(start).replace(minute=0, second=0, microsecond=0)
    end_hour = dt_util.as_utc(end).replace(minute=0, second=0, microsecond=0)
    idx = 0
    last_value: float | None = None
    while hour <= end_hour:
        while idx < len(points) and points[idx][0] <= hour:
            last_value = points[idx][1]
            idx += 1
        if last_value is not None:
            result[hour] = last_value
        hour += timedelta(hours=1)
    return result


async def async_fetch_hourly_means(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Return {hour_start_utc: mean_value} from one entity's long-term statistics."""
    return await _async_fetch_hourly_stat(hass, entity_id, start, end, "mean")


DEFAULT_HEATER_POWER_KW = 3.0
# Enum sensors (like heating_state) don't get numeric long-term statistics,
# so "which hours was the heater active" isn't directly queryable that way.
# Instead: take a high percentile of hourly *max* power readings - most
# hours the heater isn't firing at all (low max, near-standby draw), while
# the hours it did fire show a distinctly higher max, so a high percentile
# picks those out without needing to correlate against heating_state at all.
HEATER_POWER_PERCENTILE = 90
MIN_HOURS_FOR_POWER_ESTIMATE = 24


async def async_estimate_heater_power_kw(
    hass: HomeAssistant, *, power_entity: str, start: datetime, end: datetime
) -> float:
    """Estimate the heater's real power draw when actively heating, in kW.

    Falls back to DEFAULT_HEATER_POWER_KW when there's not yet enough
    history to estimate confidently.
    """
    hourly_max_w = await _async_fetch_hourly_stat(hass, power_entity, start, end, "max")
    if len(hourly_max_w) < MIN_HOURS_FOR_POWER_ESTIMATE:
        return DEFAULT_HEATER_POWER_KW

    percentile_w = float(np.percentile(list(hourly_max_w.values()), HEATER_POWER_PERCENTILE))
    if percentile_w <= 0:
        return DEFAULT_HEATER_POWER_KW
    return percentile_w / WATTS_PER_KW


async def async_build_hourly_samples(
    hass: HomeAssistant,
    *,
    water_temp_entity: str,
    outdoor_temp_entity: str | None,
    wind_speed_entity: str | None,
    power_entity: str,
    start: datetime,
    end: datetime,
    allow_weather_backfill: bool = False,
) -> list[HourlySample]:
    """Build hourly (water_temp, delta, ambient, wind, power) samples for fitting.

    Requires a real outdoor temperature sensor's history: without it there is
    no signal to separate heat loss from heat input, and any "fit" would be
    meaningless rather than merely less accurate. Returns [] in that case.

    allow_weather_backfill (opt-in - see button.spa_miser_estimate_initial_model)
    fills GAPS in that real history from Open-Meteo's public historical
    archive, so a fit can succeed well before the real sensor has
    accumulated enough history on its own. Real data always wins where it
    exists; this only ever fills hours the real sensor has no reading for.
    """
    if outdoor_temp_entity is None:
        return []

    water = await async_fetch_hourly_means(hass, water_temp_entity, start, end)
    power = await async_fetch_hourly_means(hass, power_entity, start, end)
    ambient = await async_fetch_hourly_means(hass, outdoor_temp_entity, start, end)
    wind = (
        await async_fetch_hourly_means(hass, wind_speed_entity, start, end)
        if wind_speed_entity
        else {}
    )

    if allow_weather_backfill and len(ambient) < BACKFILL_TRIGGER_MAX_REAL_HOURS:
        backfill = await open_meteo.async_fetch_historical_ambient(hass, start, end)
        for hour, (temp_c, wind_ms) in backfill.items():
            ambient.setdefault(hour, temp_c)
            wind.setdefault(hour, wind_ms)

    samples: list[HourlySample] = []
    for hour in sorted(water):
        next_hour = hour + timedelta(hours=1)
        if next_hour not in water or hour not in power or hour not in ambient:
            continue
        water_temp = water[hour]
        samples.append(
            HourlySample(
                water_temp_c=water_temp,
                delta_temp_c=water[next_hour] - water_temp,
                ambient_temp_c=ambient[hour],
                wind_speed_ms=wind.get(hour, 0.0),
                heater_power_kw=power[hour] / WATTS_PER_KW,
            )
        )
    return samples
