"""End-to-end smoke test: config entry setup creates the expected entities."""
from __future__ import annotations

from datetime import timedelta

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

ENTRY_DATA = {
    CONF_CLIMATE_ENTITY: "climate.balboa_spa",
    CONF_WATER_TEMP_SENSOR: "sensor.balboa_spa_current_temperature",
    CONF_HEATING_STATE_ENTITY: "sensor.balboa_spa_heating_state",
    CONF_WEATHER_ENTITY: "weather.home",
    CONF_POWER_ENTITY: "sensor.spa_heater_power",
    CONF_ENERGY_ENTITY: "sensor.spa_heater_energy",
    CONF_MAX_COMFORT_TEMP: 39.5,
    CONF_MIN_COMFORT_TEMP: 36.0,
    CONF_MIN_AWAY_TEMP: 25.0,
    CONF_MANUAL_OVERRIDE_MINUTES: 120,
    CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL,
    CONF_MANUAL_CHEAP_HOURS: [1, 2, 3],
    CONF_MANUAL_CHEAP_RATE: 0.1,
    CONF_MANUAL_STANDARD_RATE: 0.3,
}


async def test_setup_entry_creates_platform_entities(hass, enable_custom_integrations):
    hass.states.async_set(
        "climate.balboa_spa",
        "heat",
        {"current_temperature": 37.5, "temperature": 39.5, "preset_mode": "High Range"},
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", "37.5")
    hass.states.async_set("weather.home", "cloudy")
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "100.0")

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state.value == "loaded"

    expected_entities = [
        "switch.spa_miser_automatic_control_enabled",
        "switch.spa_miser_away_mode",
        "number.spa_miser_max_comfort_temperature",
        "number.spa_miser_min_comfort_temperature",
        "number.spa_miser_min_away_temperature",
        "binary_sensor.spa_miser_heating_recommended",
        "sensor.spa_miser_predicted_kwh_today",
        "sensor.spa_miser_actual_kwh_today",
    ]
    for entity_id in expected_entities:
        state = hass.states.get(entity_id)
        assert state is not None, f"expected entity {entity_id} to exist"

    # Source sensors report exactly the entity_id configured for each role,
    # so the device page shows what this integration actually reads from.
    source_state = hass.states.get("sensor.spa_miser_climate_entity_source")
    assert source_state.state == "climate.balboa_spa"
    unconfigured_state = hass.states.get("sensor.spa_miser_outdoor_temperature_source")
    assert unconfigured_state.state == "Not configured"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
