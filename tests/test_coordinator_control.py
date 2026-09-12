"""End-to-end test of a control cycle: decision -> actual climate service calls."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
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
    PRESET_HIGH_RANGE,
)
from custom_components.spa_miser.thermal_model import ThermalModelParams

CLIMATE_ENTITY = "climate.balboa_spa"


async def test_enabled_coordinator_drives_climate_entity(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    # dt_util's default time zone is only set once the hass fixture has
    # configured the test instance, so "current hour" must be computed here
    # rather than at module import time (computing it earlier drove a flaky
    # local-time mismatch against what ManualPriceSource resolves at runtime).
    current_hour = dt_util.now().hour
    entry_data = {
        CONF_CLIMATE_ENTITY: CLIMATE_ENTITY,
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
        # Mark the current hour cheap so the decision engine recommends heating.
        CONF_MANUAL_CHEAP_HOURS: [current_hour],
        CONF_MANUAL_CHEAP_RATE: 0.05,
        CONF_MANUAL_STANDARD_RATE: 0.30,
    }

    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {"current_temperature": 37.0, "temperature": 36.0, "preset_mode": "Low Range"},
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", "37.0")
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "50.0")

    calls: list[ServiceCall] = []

    async def _record_call(call: ServiceCall):
        calls.append(call)
        # Real entities attribute the resulting state write to the same
        # Context as the service call that caused it (the entity platform
        # calls entity.async_set_context(call.context) before invoking the
        # service). Preserve that here so SpaControl's own calls aren't
        # mistaken for a manual override of the entity it just changed.
        current = hass.states.get(CLIMATE_ENTITY)
        if call.service == "set_hvac_mode":
            hass.states.async_set(
                CLIMATE_ENTITY, call.data["hvac_mode"], current.attributes, context=call.context
            )
        elif call.service == "set_preset_mode":
            attrs = {**current.attributes, "preset_mode": call.data["preset_mode"]}
            hass.states.async_set(CLIMATE_ENTITY, current.state, attrs, context=call.context)
        elif call.service == "set_temperature":
            attrs = {**current.attributes, "temperature": call.data["temperature"]}
            hass.states.async_set(CLIMATE_ENTITY, current.state, attrs, context=call.context)

    for service in ("set_hvac_mode", "set_preset_mode", "set_temperature"):
        hass.services.async_register("climate", service, _record_call)

    async def _forecast_handler(call: ServiceCall):
        now = dt_util.utcnow()
        return {
            "weather.home": {
                "forecast": [
                    {
                        "datetime": (now + timedelta(hours=i + 1)).isoformat(),
                        "temperature": 15.0,
                        "wind_speed": 0.0,
                    }
                    for i in range(24)
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", _forecast_handler, supports_response=SupportsResponse.ONLY
    )

    entry = MockConfigEntry(domain=DOMAIN, data=entry_data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    # Bypass recorder-based fitting (no long-term statistics exist in this
    # test) with a known-good model directly, as test_thermal_model.py
    # already covers the fitting math in isolation.
    coordinator._model = ThermalModelParams(
        loss_coefficient=0.05,
        wind_coefficient=0.0,
        input_coefficient=0.5,
        thermal_mass_kwh_per_c=1.9,
        r_squared=0.99,
        n_samples=200,
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.decision is not None
    assert coordinator.data.decision.heat_recommended is True

    service_calls = {c.service: c.data for c in calls}
    assert service_calls["set_preset_mode"]["preset_mode"] == PRESET_HIGH_RANGE
    assert service_calls["set_temperature"]["temperature"] == 39.5
    assert service_calls["set_hvac_mode"]["hvac_mode"] == "heat"

    # SpaControl tags its own calls with its own Context; they must not be
    # mistaken for a manual override of the entity it just changed.
    assert coordinator.spa_control.is_manually_overridden is False
    assert coordinator.data.control_status == "active"

    # A state change from outside spa-miser's own service calls (a
    # different Context - e.g. someone using the thermostat card directly)
    # should be detected as a manual override, exactly the situation
    # sensor.spa_miser_control_status exists to surface: everything else
    # about this cycle looks identical to "nothing to do right now".
    hass.states.async_set(
        CLIMATE_ENTITY, "heat", {**hass.states.get(CLIMATE_ENTITY).attributes, "temperature": 38.0}
    )
    # The state-changed event (and SpaControl's own listener that sets the
    # override) must be fully processed before refreshing - otherwise the
    # coordinator's update cycle can run ahead of the override being set.
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.spa_control.is_manually_overridden is True
    assert coordinator.data.control_status == "paused_manual_override"
    assert coordinator.data.control_override_until is not None


async def test_control_status_is_disabled_when_switch_is_off(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {"current_temperature": 37.0, "temperature": 36.0, "preset_mode": "Low Range"},
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", "37.0")
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
    assert coordinator.data.enabled is False
    assert coordinator.data.control_status == "disabled"
