"""button.spa_miser_estimate_initial_model's effect on the coordinator."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spa_miser import coordinator as coordinator_module
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
    CONF_WEATHER_BACKFILL_ENABLED,
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


async def test_trigger_enables_backfill_and_refits_immediately(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._weather_backfill_enabled is False

    with patch.object(
        coordinator_module.history, "async_build_hourly_samples", new=AsyncMock(return_value=[])
    ) as mock_build_samples:
        await coordinator.async_trigger_initial_estimate()

    assert coordinator._weather_backfill_enabled is True
    assert entry.options[CONF_WEATHER_BACKFILL_ENABLED] is True
    mock_build_samples.assert_awaited()
    assert mock_build_samples.await_args.kwargs["allow_weather_backfill"] is True
