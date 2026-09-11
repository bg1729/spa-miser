"""Tests for the recorder-statistics-to-HourlySample alignment logic.

Mocks the recorder plumbing itself (get_instance / statistics_during_period)
rather than seeding a real recorder database - history.py's own logic is the
row alignment/filtering below, not the statistics engine, which is HA's own
tested code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import State

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
            # Real rows only carry the requested stat type(s), but reporting
            # both here is harmless and lets this one fixture serve tests
            # that query "mean" and tests that query "max".
            result[entity_id] = [
                {"start": _hour(i), "mean": value, "max": value}
                for i, value in hourly.items()
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


async def test_falls_back_to_raw_history_when_entity_has_no_statistics(hass):
    # sensor.water is intentionally absent from the statistics fixture, so
    # statistics_during_period returns [] for it - matching a real gateway
    # sensor observed live to never declare a state_class in its own MQTT
    # discovery config, which silently means HA never generates long-term
    # statistics for it, however long you wait.
    ambient = {0: 5.0, 1: 5.0}
    power_w = {0: 3000.0, 1: 0.0}
    raw_water_states = {
        "sensor.water": [
            State("sensor.water", "38.0", last_updated=_hour(0) + timedelta(minutes=10)),
            State("sensor.water", "38.2", last_updated=_hour(0) + timedelta(minutes=40)),
            State("sensor.water", "37.8", last_updated=_hour(1) + timedelta(minutes=15)),
        ]
    }

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for({"sensor.outdoor": ambient, "sensor.power": power_w}),
        ),
        patch.object(
            history,
            "state_changes_during_period",
            side_effect=lambda hass, start, end, entity_id: {
                entity_id: raw_water_states.get(entity_id, [])
            },
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
    # Hour 0's raw readings (38.0, 38.2) average to 38.1; hour 1 (37.8) is
    # only usable as the delta target here, since there's no hour-2 water
    # reading to compute hour 1's own delta against.
    assert samples[0].water_temp_c == pytest.approx(38.1)
    assert samples[0].delta_temp_c == pytest.approx(37.8 - 38.1)


async def test_build_hourly_samples_backfills_ambient_gaps_when_allowed(hass):
    # Real outdoor sensor only has hour 0 (e.g. just enabled); hours 1 and 2
    # would otherwise be skipped entirely for lack of an ambient reading.
    water = {0: 38.0, 1: 37.8, 2: 37.6, 3: 37.4}
    ambient = {0: 5.0}
    power_w = {0: 3000.0, 1: 0.0, 2: 0.0}
    backfill = {
        _hour(1): (4.5, 2.0),
        _hour(2): (4.0, 2.5),
    }

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for(
                {"sensor.water": water, "sensor.outdoor": ambient, "sensor.power": power_w}
            ),
        ),
        patch.object(
            history.open_meteo,
            "async_fetch_historical_ambient",
            return_value=backfill,
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
            allow_weather_backfill=True,
        )

    # All three hours now have everything needed: hour 0 from real data,
    # hours 1 and 2 only because the ambient gap got backfilled.
    assert len(samples) == 3
    assert samples[0].ambient_temp_c == 5.0  # real data wins over backfill
    assert samples[1].ambient_temp_c == 4.5  # backfilled
    assert samples[1].wind_speed_ms == 2.0
    assert samples[2].ambient_temp_c == 4.0  # backfilled


async def test_build_hourly_samples_skips_backfill_once_real_history_is_plentiful(hass):
    plentiful = {i: 5.0 for i in range(history.BACKFILL_TRIGGER_MAX_REAL_HOURS)}
    water = {i: 38.0 for i in range(history.BACKFILL_TRIGGER_MAX_REAL_HOURS + 1)}
    power_w = {i: 0.0 for i in range(history.BACKFILL_TRIGGER_MAX_REAL_HOURS)}

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for(
                {"sensor.water": water, "sensor.outdoor": plentiful, "sensor.power": power_w}
            ),
        ),
        patch.object(
            history.open_meteo, "async_fetch_historical_ambient"
        ) as mock_backfill,
    ):
        await history.async_build_hourly_samples(
            hass,
            water_temp_entity="sensor.water",
            outdoor_temp_entity="sensor.outdoor",
            wind_speed_entity=None,
            power_entity="sensor.power",
            start=START,
            end=END,
            allow_weather_backfill=True,
        )

    mock_backfill.assert_not_called()


async def test_estimate_heater_power_kw_uses_high_percentile_of_hourly_max(hass):
    # Mostly-idle standby draw (~50W), with a clear minority of hours (20%)
    # the heater actually fired (3000W) - comfortably past the 90th
    # percentile's boundary (at exactly 10% the interpolation blends the
    # two bands rather than cleanly landing on either).
    power_w = {i: 50.0 for i in range(24)}
    power_w.update({i: 3000.0 for i in range(24, 30)})

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for({"sensor.power": power_w}),
        ),
    ):
        estimate = await history.async_estimate_heater_power_kw(
            hass, power_entity="sensor.power", start=START, end=START + timedelta(hours=30)
        )

    assert estimate == pytest.approx(3.0, rel=0.05)


async def test_estimate_heater_power_kw_falls_back_with_too_little_history(hass):
    power_w = {i: 3000.0 for i in range(5)}  # well under MIN_HOURS_FOR_POWER_ESTIMATE

    with (
        patch.object(history, "get_instance", return_value=_FakeRecorderInstance()),
        patch.object(
            history,
            "statistics_during_period",
            side_effect=_stats_for({"sensor.power": power_w}),
        ),
    ):
        estimate = await history.async_estimate_heater_power_kw(
            hass, power_entity="sensor.power", start=START, end=START + timedelta(hours=5)
        )

    assert estimate == history.DEFAULT_HEATER_POWER_KW
