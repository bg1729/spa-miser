"""A configured input entity coming back online should trigger an
immediate coordinator refresh, not wait for the next scheduled one.

Without this, the coordinator's first refresh (which runs immediately at
setup) can easily race ahead of MQTT-based entities reconnecting after a
restart - leaving sensors reading unknown/unavailable until the next
scheduled refresh, up to COORDINATOR_UPDATE_INTERVAL_MINUTES (30) later,
despite the underlying data actually being ready almost immediately.
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
    DOMAIN,
    PRICE_SOURCE_MANUAL,
)

CLIMATE_ENTITY = "climate.balboa_spa"
WATER_TEMP_ENTITY = "sensor.balboa_spa_current_temperature"


async def test_water_temp_becoming_available_triggers_immediate_refresh(
    recorder_mock, hass, enable_custom_integrations
):
    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {"current_temperature": 37.0, "temperature": 36.0, "preset_mode": "Low Range"},
    )
    # Not yet reported - simulates the MQTT entity not having reconnected
    # yet when the coordinator's first refresh runs.
    hass.states.async_set(WATER_TEMP_ENTITY, "unavailable")
    hass.states.async_set("sensor.balboa_spa_heating_state", "Idle / not heating")
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "50.0")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIMATE_ENTITY: CLIMATE_ENTITY,
            CONF_WATER_TEMP_SENSOR: WATER_TEMP_ENTITY,
            CONF_HEATING_STATE_ENTITY: "sensor.balboa_spa_heating_state",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_POWER_ENTITY: "sensor.spa_heater_power",
            CONF_ENERGY_ENTITY: "sensor.spa_heater_energy",
            CONF_MAX_COMFORT_TEMP: 39.5,
            CONF_MIN_COMFORT_TEMP: 36.0,
            CONF_MIN_AWAY_TEMP: 25.0,
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
    assert coordinator.data.current_temperature_c is None

    # The entity "reconnecting" - no manual coordinator.async_refresh() call.
    hass.states.async_set(WATER_TEMP_ENTITY, "37.0")
    await hass.async_block_till_done()

    assert coordinator.data.current_temperature_c == 37.0
