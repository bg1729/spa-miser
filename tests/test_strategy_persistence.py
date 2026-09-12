"""The committed daily strategy must survive a Home Assistant restart -
otherwise sensor.spa_miser_daily_strategy's "Expected temperature" series
loses its history-look, and _decide() falls back to the cruder greedy
heuristic for a cycle every time HA restarts (e.g. after a code update).
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spa_miser.const import (
    CONF_CLIMATE_ENTITY,
    CONF_ENERGY_ENTITY,
    CONF_HEATING_STATE_ENTITY,
    CONF_MANUAL_CHEAP_HOURS,
    CONF_MANUAL_CHEAP_RATE,
    CONF_MANUAL_OVERRIDE_MINUTES,
    CONF_MANUAL_STANDARD_RATE,
    CONF_MAX_COMFORT_TEMP,
    CONF_MIN_AWAY_TEMP,
    CONF_MIN_COMFORT_TEMP,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
)
from custom_components.spa_miser.coordinator import SpaMiserCoordinator
from custom_components.spa_miser.strategy import serialize_strategy
from custom_components.spa_miser.thermal_model import ThermalModelParams

CLIMATE_ENTITY = "climate.balboa_spa"

MODEL = ThermalModelParams(
    loss_coefficient=0.05,
    wind_coefficient=0.0,
    input_coefficient=0.5,
    thermal_mass_kwh_per_c=1.9,
    r_squared=0.99,
    n_samples=200,
)


def _storage_key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_daily_strategy"


async def _setup_and_compute_strategy(hass: HomeAssistant) -> MockConfigEntry:
    """Real end-to-end setup (same pattern as test_daily_strategy_sensor.py)
    that ends with the coordinator holding a genuinely computed plan."""
    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {"current_temperature": 39.5, "temperature": 39.5, "preset_mode": "High Range"},
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", "39.5")
    hass.states.async_set("sensor.balboa_spa_heating_state", "Idle / not heating")
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "50.0")

    async def _forecast_handler(call):
        now = dt_util.utcnow()
        return {
            "weather.home": {
                "forecast": [
                    {
                        "datetime": (now + timedelta(hours=i + 1)).isoformat(),
                        "temperature": 10.0,
                        "wind_speed": 0.0,
                    }
                    for i in range(24)
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", _forecast_handler, supports_response=SupportsResponse.ONLY
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIMATE_ENTITY: CLIMATE_ENTITY,
            CONF_WATER_TEMP_SENSOR: "sensor.balboa_spa_current_temperature",
            CONF_HEATING_STATE_ENTITY: "sensor.balboa_spa_heating_state",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_POWER_ENTITY: "sensor.spa_heater_power",
            CONF_ENERGY_ENTITY: "sensor.spa_heater_energy",
            CONF_MAX_COMFORT_TEMP: 39.5,
            CONF_MIN_COMFORT_TEMP: 25.0,
            CONF_MIN_AWAY_TEMP: 20.0,
            CONF_MANUAL_OVERRIDE_MINUTES: 120,
            CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL,
            CONF_MANUAL_CHEAP_HOURS: list(range(24)),
            CONF_MANUAL_CHEAP_RATE: 0.10,
            CONF_MANUAL_STANDARD_RATE: 0.10,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._model = MODEL
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.strategy is not None, "coordinator computed no strategy at all"
    return entry


async def test_computing_a_strategy_persists_it_to_storage(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations, hass_storage
):
    entry = await _setup_and_compute_strategy(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    key = _storage_key(entry)
    assert key in hass_storage
    assert hass_storage[key]["data"] == serialize_strategy(coordinator.strategy)


async def test_strategy_is_restored_across_a_simulated_restart(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations, hass_storage
):
    entry = await _setup_and_compute_strategy(hass)
    original = hass.data[DOMAIN][entry.entry_id].strategy

    # Simulate a restart: a fresh coordinator instance for the same entry,
    # with nothing but async_setup() run (no refresh yet) - mirrors exactly
    # what __init__.py does before the first refresh on real startup.
    restarted = SpaMiserCoordinator(hass, entry)
    await restarted.async_setup()

    assert restarted.strategy is not None
    assert restarted.strategy == original


async def test_a_restored_but_expired_strategy_still_triggers_recompute(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations, hass_storage
):
    entry = await _setup_and_compute_strategy(hass)

    # Overwrite storage with a plan that's already fully in the past.
    long_ago = dt_util.utcnow() - timedelta(days=2)
    restarted = SpaMiserCoordinator(hass, entry)
    hass_storage[_storage_key(entry)] = {
        "version": 1,
        "data": {
            "computed_at": long_ago.isoformat(),
            "slots": [
                {
                    "start": (long_ago).isoformat(),
                    "end": (long_ago + timedelta(hours=1)).isoformat(),
                    "price": 0.1,
                    "planned_temp_c": 36.0,
                    "heat_on": False,
                }
            ],
        },
    }
    await restarted.async_setup()
    assert restarted.strategy is not None
    assert restarted.strategy.end is not None
    assert restarted.strategy.end < dt_util.utcnow()  # confirmed stale before refreshing

    restarted._model = MODEL
    await restarted.async_refresh()
    await hass.async_block_till_done()

    assert restarted.strategy is not None
    assert restarted.strategy.end > dt_util.utcnow()  # recomputed to a current plan
