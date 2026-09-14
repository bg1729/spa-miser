"""sensor.spa_miser_daily_strategy exposes the coordinator's committed plan."""
from __future__ import annotations

from datetime import timedelta

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


async def test_daily_strategy_sensor_reflects_the_computed_plan(
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

    from homeassistant.core import SupportsResponse

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
            CONF_MANUAL_CHEAP_HOURS: list(range(24)),  # everything cheap - simplest case
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

    state = hass.states.get("sensor.spa_miser_daily_strategy")
    assert state is not None
    assert state.state != "unknown"  # computed_at should be set

    # Columnar (parallel arrays), not a list of per-slot objects - see
    # strategy.slots_for_display for why (fits under the recorder's
    # 16KB-per-attribute limit that the old per-slot-object shape hit).
    slots = state.attributes["slots"]
    first_slot = coordinator.strategy.slots[0]
    assert len(slots["start"]) == len(coordinator.strategy.slots)
    assert slots["start"][0] == int(first_slot.start.timestamp())
    assert slots["end"][0] == int(first_slot.end.timestamp())
    assert slots["price"][0] == round(first_slot.price, 4)
    assert slots["heat_on"][0] in (0, 1)
    assert isinstance(slots["planned_temp_c"][0], float)
