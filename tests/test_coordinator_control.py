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
    CONF_AWAY_MODE,
    CONF_MAX_COMFORT_TEMP,
    CONF_MAX_PRICE,
    CONF_MIN_AWAY_TEMP,
    CONF_MIN_COMFORT_TEMP,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
    PRESET_HIGH_RANGE,
    PRESET_LOW_RANGE,
)
from custom_components.spa_miser.thermal_model import ThermalModelParams

CLIMATE_ENTITY = "climate.balboa_spa"


async def _setup_coordinator(
    hass: HomeAssistant,
    *,
    cheap_now: bool,
    fit_model: bool = True,
    extra_options: dict | None = None,
    initial_preset: str = "Low Range",
    current_temp: float = 37.0,
    initial_target_temp: float = 36.0,
):
    """Shared harness: real climate/sensor entities, mocked climate/weather
    services (recording every call), a config entry, and (optionally) a
    directly-injected known-good thermal model - same pattern as
    test_enabled_coordinator_drives_climate_entity, factored out so the
    price-cap/never-off cases below don't each repeat it.

    `initial_preset` should be the preset *opposite* the one a test expects
    to end up in - otherwise the coordinator sees no change needed and
    never issues a set_preset_mode call to observe.
    """
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
        CONF_MANUAL_CHEAP_HOURS: [current_hour] if cheap_now else [],
        CONF_MANUAL_CHEAP_RATE: 0.05,
        CONF_MANUAL_STANDARD_RATE: 0.30,
    }

    hass.states.async_set(
        CLIMATE_ENTITY,
        "off",
        {
            "current_temperature": current_temp,
            "temperature": initial_target_temp,
            "preset_mode": initial_preset,
        },
    )
    hass.states.async_set("sensor.balboa_spa_current_temperature", str(current_temp))
    hass.states.async_set("sensor.spa_heater_power", "0")
    hass.states.async_set("sensor.spa_heater_energy", "50.0")

    calls: list[ServiceCall] = []

    async def _record_call(call: ServiceCall):
        calls.append(call)
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

    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, options=extra_options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    if fit_model:
        coordinator._model = ThermalModelParams(
            loss_coefficient=0.05,
            wind_coefficient=0.0,
            input_coefficient=0.5,
            thermal_mass_kwh_per_c=1.9,
            r_squared=0.99,
            n_samples=200,
        )

    return coordinator, calls


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


async def test_coast_sets_comfort_floor_not_off(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """Coasting must never turn the heater fully off - the onboard
    thermostat should keep defending the comfort floor instead, so a HA
    outage mid-coast still can't leave the spa undefended."""
    # Already at the ceiling - heating further has no benefit, so both the
    # DP and the greedy fallback should agree there's nothing to gain by
    # heating right now, deterministically producing heat_recommended=False.
    # initial_target_temp starts away from the comfort floor so the switch
    # down to it is an observable set_temperature call.
    coordinator, calls = await _setup_coordinator(
        hass, cheap_now=False, current_temp=39.5, initial_target_temp=39.5
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.decision is not None
    assert coordinator.data.decision.heat_recommended is False

    service_calls = {c.service: c.data for c in calls}
    assert service_calls["set_preset_mode"]["preset_mode"] == PRESET_HIGH_RANGE
    assert service_calls["set_temperature"]["temperature"] == 36.0  # comfort floor
    assert service_calls["set_hvac_mode"]["hvac_mode"] == "heat"
    assert all(c.data.get("hvac_mode") != "off" for c in calls if c.service == "set_hvac_mode")

    assert coordinator.data.active_preset == PRESET_HIGH_RANGE
    assert coordinator.data.range_reason == "normal"


async def test_price_cap_overrides_a_heat_recommendation(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """A price cap below the current price forces Low Range/min_away_temp
    even though the DP/decision engine (left unaware of the cap) would
    otherwise recommend heating."""
    coordinator, calls = await _setup_coordinator(
        hass,
        cheap_now=True,
        extra_options={CONF_MAX_PRICE: 0.01},
        initial_preset="High Range",
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    # The DP/decision engine itself is unaware of the cap and still wants
    # to heat - it's the actuation layer that overrides it.
    assert coordinator.data.decision is not None
    assert coordinator.data.decision.heat_recommended is True

    service_calls = {c.service: c.data for c in calls}
    assert service_calls["set_preset_mode"]["preset_mode"] == PRESET_LOW_RANGE
    assert service_calls["set_temperature"]["temperature"] == 25.0  # min_away_temp
    assert service_calls["set_hvac_mode"]["hvac_mode"] == "heat"

    assert coordinator.data.active_preset == PRESET_LOW_RANGE
    assert coordinator.data.range_reason == "price_cap"

    active_range = hass.states.get("sensor.spa_miser_active_range")
    assert active_range.state == PRESET_LOW_RANGE
    assert active_range.attributes["reason"] == "Price cap exceeded"


async def test_away_mode_takes_precedence_over_price_cap_in_reason(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """When both away mode and the price cap would force Low Range, the
    reason shown to the user is the deliberate one (away mode)."""
    coordinator, _calls = await _setup_coordinator(
        hass,
        cheap_now=True,
        extra_options={CONF_MAX_PRICE: 0.01, CONF_AWAY_MODE: True},
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.active_preset == PRESET_LOW_RANGE
    assert coordinator.data.range_reason == "away_mode"


async def test_range_enforced_even_without_a_decision_yet(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """Away mode (and the price cap) must hold a safe floor even before
    the thermal model has ever produced a decision - e.g. a fresh install,
    or a model that never successfully fits. Previously this safety
    enforcement only ran as part of applying a decision, so it silently
    never engaged in this case."""
    coordinator, calls = await _setup_coordinator(
        hass,
        cheap_now=False,
        fit_model=False,
        extra_options={CONF_AWAY_MODE: True},
        initial_preset="High Range",
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.decision is None

    service_calls = {c.service: c.data for c in calls}
    assert service_calls["set_preset_mode"]["preset_mode"] == PRESET_LOW_RANGE
    assert service_calls["set_temperature"]["temperature"] == 25.0  # min_away_temp
    assert service_calls["set_hvac_mode"]["hvac_mode"] == "heat"
    assert coordinator.data.active_preset == PRESET_LOW_RANGE
    assert coordinator.data.range_reason == "away_mode"
