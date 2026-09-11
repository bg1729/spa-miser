"""Tests for the recorder-statistics-to-HourlySample alignment logic.

Mocks the recorder plumbing itself (get_instance / statistics_during_period)
rather than seeding a real recorder database - history.py's own logic is the
row alignment/filtering below, not the statistics engine, which is HA's own
tested code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from custom_components.spa_miser import history

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=5)


def _hour(i: int) -> datetime:
    return START + timedelta(hours=i)


class _FakeRecorderInstance:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _stats_for(values_by_entity: dict[str, dict[int, float]]):
    def _fake_statistics_during_period(hass, start, end, entity_ids, period, units, types):
        result = {}
        for entity_id in entity_ids:
            hourly = values_by_entity.get(entity_id, {})
            result[entity_id] = [
                {"start": _hour(i), "mean": value} for i, value in hourly.items()
            ]
        return result

    return _fake_statistics_during_period


async def test_build_hourly_samples_aligns_and_converts_units(hass):
    water = {0: 38.0, 1: 37.8, 2: 37.6}
    ambient = {0: 5.0, 1: 5.0, 2: 5.0}
    power_w = {0: 3000.0, 1: 0.0, 2: 0.0}

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for(
                {
                    "sensor.water": water,
                    "sensor.outdoor": ambient,
                    "sensor.power": power_w,
                }
            ),
        ),
    ):
        samples = await history.async_build_hourly_samples(
            hass,
            water_temp_entity="sensor.water",
            outdoor_temp_entity="sensor.outdoor",
            wind_speed_entity=None,
            power_entity="sensor.power",
            start=START,
            end=END,
        )

    # Hour 2 has no hour-3 water reading to compute a delta against, so only
    # hours 0 and 1 produce a usable sample.
    assert len(samples) == 2
    assert samples[0].water_temp_c == 38.0
    assert samples[0].delta_temp_c == 37.8 - 38.0
    assert samples[0].ambient_temp_c == 5.0
    assert samples[0].wind_speed_ms == 0.0
    assert samples[0].heater_power_kw == 3.0  # 3000 W -> 3 kW


async def test_build_hourly_samples_requires_outdoor_sensor(hass):
    samples = await history.async_build_hourly_samples(
        hass,
        water_temp_entity="sensor.water",
        outdoor_temp_entity=None,
        wind_speed_entity=None,
        power_entity="sensor.power",
        start=START,
        end=END,
    )

    assert samples == []


async def test_build_hourly_samples_skips_hours_missing_power_data(hass):
    # Hour 0 has everything it needs (including a water reading for hour 1,
    # to compute its delta) and should still produce a sample even though
    # hour 1 itself is missing a power reading and can't form its own.
    water = {0: 38.0, 1: 37.8}
    ambient = {0: 5.0, 1: 5.0}
    power_w = {0: 3000.0}  # hour 1 missing

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for(
                {"sensor.water": water, "sensor.outdoor": ambient, "sensor.power": power_w}
            ),
        ),
    ):
        samples = await history.async_build_hourly_samples(
            hass,
            water_temp_entity="sensor.water",
            outdoor_temp_entity="sensor.outdoor",
            wind_speed_entity=None,
            power_entity="sensor.power",
            start=START,
            end=END,
        )

    assert len(samples) == 1
    assert samples[0].water_temp_c == 38.0
    assert samples[0].heater_power_kw == 3.0
