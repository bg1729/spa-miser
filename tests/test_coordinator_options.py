"""Regression test: setup-time comfort temps from entry.data must be honored.

The coordinator used to read comfort temperatures only from entry.options
(which starts empty) with a hardcoded default fallback, silently ignoring
whatever the user actually chose in the config flow (which is stored in
entry.data) unless they'd also since adjusted the number entities at least
once.
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
    DEFAULT_MAX_COMFORT_TEMP,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
)


async def test_setup_time_comfort_temps_are_honored(hass, enable_custom_integrations):
    chosen_max_comfort = DEFAULT_MAX_COMFORT_TEMP + 1.5  # deliberately not the default

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIMATE_ENTITY: "climate.balboa_spa",
            CONF_WATER_TEMP_SENSOR: "sensor.balboa_spa_current_temperature",
            CONF_HEATING_STATE_ENTITY: "sensor.balboa_spa_heating_state",
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_POWER_ENTITY: "sensor.spa_heater_power",
            CONF_ENERGY_ENTITY: "sensor.spa_heater_energy",
            CONF_MAX_COMFORT_TEMP: chosen_max_comfort,
            CONF_MIN_COMFORT_TEMP: 36.0,
            CONF_MIN_AWAY_TEMP: 25.0,
            CONF_MANUAL_OVERRIDE_MINUTES: 120,
            CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL,
            CONF_MANUAL_CHEAP_HOURS: [1, 2, 3],
            CONF_MANUAL_CHEAP_RATE: 0.1,
            CONF_MANUAL_STANDARD_RATE: 0.3,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.max_comfort_c == chosen_max_comfort
    assert coordinator.max_comfort_c != DEFAULT_MAX_COMFORT_TEMP
