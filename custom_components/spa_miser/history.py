"""Pulls historical hourly long-term statistics from HA's recorder.

Used to build the dataset the thermal model is fit against. Statistics (not
raw history) are used deliberately: 5-minute-resolution history is only kept
~10 days by default, while hourly statistics are retained indefinitely
(subject to the user's own recorder purge settings) - exactly what's needed
for fitting against several weeks of data without any extra database.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .thermal_model import HourlySample

# Power sensors are assumed to report Watts (the HA `power` device_class
# convention); statistics are converted to kW for the model.
WATTS_PER_KW = 1000.0


def _parse_stat_start(value) -> datetime | None:
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value)
    return None


async def async_fetch_hourly_means(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Return {hour_start_utc: mean_value} from one entity's long-term statistics."""
    instance = get_instance(hass)
    stats = await instance.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {entity_id},
        "hour",
        None,
        {"mean"},
    )
    result: dict[datetime, float] = {}
    for row in stats.get(entity_id, []):
        mean = row.get("mean")
        hour = _parse_stat_start(row.get("start"))
        if mean is None or hour is None:
            continue
        result[hour] = float(mean)
    return result


async def async_build_hourly_samples(
    hass: HomeAssistant,
    *,
    water_temp_entity: str,
    outdoor_temp_entity: str | None,
    wind_speed_entity: str | None,
    power_entity: str,
    start: datetime,
    end: datetime,
) -> list[HourlySample]:
    """Build hourly (water_temp, delta, ambient, wind, power) samples for fitting.

    Requires a real outdoor temperature sensor's history: without it there is
    no signal to separate heat loss from heat input, and any "fit" would be
    meaningless rather than merely less accurate. Returns [] in that case.
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
