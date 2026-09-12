"""The comfort number entities must stay within the spa's real per-preset
setpoint bands, not just TEMP_MIN/TEMP_MAX.

max_comfort_temp and min_away_temp are written straight to the spa as the
High Range / Low Range preset's setpoint (coordinator._async_apply_decision)
- the Balboa protocol rejects/clamps a setpoint outside the band matching
the currently-selected preset (verified against the gateway firmware's own
spaProtocolActiveSetpointBand()), so these entities must never offer a value
outside that band in the first place.
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
    HIGH_RANGE_MAX_C,
    HIGH_RANGE_MIN_C,
    LOW_RANGE_MAX_C,
    LOW_RANGE_MIN_C,
    PRICE_SOURCE_MANUAL,
    TEMP_MAX,
    TEMP_MIN,
)

CLIMATE_ENTITY = "climate.balboa_spa"


async def test_comfort_number_bounds_match_the_spa_preset_bands(
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
            CONF_MIN_COMFORT_TEMP: 30.0,
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

    max_comfort = hass.states.get("number.spa_miser_max_comfort_temp")
    assert float(max_comfort.attributes["min"]) == HIGH_RANGE_MIN_C
    assert float(max_comfort.attributes["max"]) == HIGH_RANGE_MAX_C

    min_away = hass.states.get("number.spa_miser_min_away_temp")
    assert float(min_away.attributes["min"]) == LOW_RANGE_MIN_C
    assert float(min_away.attributes["max"]) == LOW_RANGE_MAX_C

    # Not written to the spa directly - not bound to either preset's band.
    min_comfort = hass.states.get("number.spa_miser_min_comfort_temp")
    assert float(min_comfort.attributes["min"]) == TEMP_MIN
    assert float(min_comfort.attributes["max"]) == TEMP_MAX
