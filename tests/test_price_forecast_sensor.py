"""sensor.spa_miser_price_slots_available exposes the raw price forecast.

Deliberately independent of sensor.spa_miser_daily_strategy: a price-only
chart shouldn't have to wait on (or depend on the success of) a thermal
model fit and strategy computation just to show the already-known-ahead
price curve.
"""
from __future__ import annotations

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
    DECISION_LOOKAHEAD_HOURS,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
)

CLIMATE_ENTITY = "climate.balboa_spa"


async def test_price_forecast_populates_without_a_strategy(
    recorder_mock, hass, enable_custom_integrations
):
    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {"current_temperature": 39.5, "temperature": 39.5, "preset_mode": "High Range"},
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", "39.5")
    hass.states.async_set("sensor.balboa_spa_heating_state", "Idle / not heating")
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "50.0")

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
    # No model fit triggered (no recorder history, no weather.home service) -
    # so no strategy is ever computed. The forecast should populate anyway.
    assert coordinator._model is None
    assert coordinator.strategy is None

    state = hass.states.get("sensor.spa_miser_price_slots_available")
    assert state is not None

    slots = state.attributes["slots"]
    assert state.state == str(len(slots))
    # At least the full lookahead window - the manual source also includes
    # today's already-elapsed hours, so this can be more than
    # DECISION_LOOKAHEAD_HOURS depending on what time of day the test runs.
    assert len(slots) >= DECISION_LOOKAHEAD_HOURS
    assert slots[0]["price"] == 0.10
    assert "start" in slots[0] and "end" in slots[0]
    # Not a strategy - no planned_temp_c/heat_on, just the raw price data.
    assert "planned_temp_c" not in slots[0]
    assert "heat_on" not in slots[0]
