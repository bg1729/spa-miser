"""End-to-end test of a control cycle: decision -> actual climate service calls."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spa_miser.strategy import DailyStrategy, StrategySlot

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


async def test_model_temperature_ignores_a_stale_overridden_hvac_mode(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """sensor.spa_miser_model_temperature must reflect the coordinator's own
    current intent (decision/active_preset), not spa_control.hvac_mode - a
    manual override (e.g. a pump-speed change incidentally flipping the
    gateway's reported hvac_mode, as seen live this session) can freeze
    that at a value with nothing to do with the current decision."""
    # Already at the ceiling with nothing cheap to wait for - both the DP
    # and the greedy fallback should agree there's no reason to heat now.
    coordinator, _calls = await _setup_coordinator(
        hass, cheap_now=False, current_temp=39.5, initial_target_temp=39.5
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.decision is not None
    assert coordinator.data.decision.heat_recommended is False
    assert coordinator.data.model_temperature_c is not None
    assert coordinator.data.model_temperature_c <= 39.5 + 0.01, (
        "predicted a rise while coasting at the ceiling"
    )

    # Simulate the live incident: hvac_mode is already "heat" (spa-miser's
    # own doing - it's never "off" any more), so a same-value rewrite of it
    # alone wouldn't register as a change at all. What actually happened
    # live was a pump-speed change tripping the override via a different
    # attribute while hvac_mode stayed frozen at "heat" underneath it -
    # reproduce that by changing preset_mode externally instead, leaving
    # hvac_mode genuinely stuck at "heat" while the decision still says
    # coast, exactly like the live incident.
    current = hass.states.get(CLIMATE_ENTITY)
    assert current.state == "heat"
    hass.states.async_set(
        CLIMATE_ENTITY,
        "heat",
        {**current.attributes, "preset_mode": "Low Range"},
        context=Context(),
    )
    await hass.async_block_till_done()
    assert coordinator.spa_control.is_manually_overridden is True

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # The decision itself hasn't changed - still coasting at the ceiling -
    # so the model prediction must not change either, even though the real
    # (now-overridden) entity's hvac_mode reports "heat".
    assert coordinator.data.decision.heat_recommended is False
    assert coordinator.data.model_temperature_c is not None
    assert coordinator.data.model_temperature_c <= 39.5 + 0.01, (
        "predicted a rise from a stale overridden hvac_mode"
    )


async def test_model_temperature_predicts_heating_toward_the_away_floor(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """In Low Range (away mode here), the model should predict heating
    once below min_away_temp, independent of the DP's own decision (which
    is computed against the normal comfort window and ignored while Low
    Range is in force) - and should predict no heating while still above
    that floor."""
    coordinator, _calls = await _setup_coordinator(
        hass,
        cheap_now=False,
        current_temp=20.0,  # below min_away_temp (25.0)
        initial_target_temp=25.0,
        extra_options={CONF_AWAY_MODE: True},
        initial_preset="Low Range",
    )

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.data.active_preset == PRESET_LOW_RANGE
    assert coordinator.data.model_temperature_c is not None
    assert coordinator.data.model_temperature_c > 20.0, (
        "did not predict heating toward the away floor while below it"
    )

    # Now above the floor - the onboard thermostat wouldn't be drawing
    # power, so neither should the model.
    hass.states.async_set(
        "sensor.balboa_spa_current_temperature", "30.0", context=Context()
    )
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.model_temperature_c is not None
    assert coordinator.data.model_temperature_c <= 30.0 + 0.01, (
        "predicted heating above the away floor"
    )


async def test_strategy_recomputes_once_the_configured_interval_elapses(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """A plan's ambient/wind assumptions are only as fresh as the forecast
    available when it was computed - strategy_recompute_interval_hours
    bounds how long that can go uncorrected, independent of whether the
    plan's own coverage has run out or new price data has arrived."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)
    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()
    first_strategy = coordinator.strategy
    assert first_strategy is not None

    # Neither the plan's coverage nor the price forecast have changed -
    # only the configured interval has elapsed (simulated directly rather
    # than waiting in real time).
    coordinator._strategy_recompute_interval_hours = 0.0

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.strategy is not None
    assert coordinator.strategy.computed_at > first_strategy.computed_at


async def test_strategy_does_not_recompute_before_the_interval_elapses(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)
    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()
    first_strategy = coordinator.strategy
    assert first_strategy is not None

    # Default interval (6h) - nowhere near elapsed a moment later, and
    # nothing else about coverage/price has changed either.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.strategy is not None
    assert coordinator.strategy.computed_at == first_strategy.computed_at


async def test_model_last_refit_is_exposed_and_tracked(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """sensor.spa_miser_model_last_refit both reports model freshness
    directly and - since HA's recorder keeps history for any sensor
    automatically - doubles as the data source for the chart's refit
    marker series, with no separate bookkeeping needed."""
    # fit_model=False: _last_fit is set unconditionally by the coordinator's
    # own automatic first-refit attempt (coordinator.py:_async_refit_model),
    # regardless of whether that attempt found enough real history to
    # actually produce a model - this isolates testing that automatic path
    # rather than the test helper's direct model injection.
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True, fit_model=False)

    first_fit = coordinator.data.last_fit
    assert first_fit is not None

    state = hass.states.get("sensor.spa_miser_model_last_refit")
    assert state is not None
    assert state.state != "unknown"
    # HA's timestamp device_class serializes state to whole-second
    # precision, dropping microseconds - compare at that precision.
    assert dt_util.parse_datetime(state.state) == first_fit.replace(microsecond=0)

    # A later refit updates both the coordinator's own value and the
    # entity's state, matching a genuinely new history entry the chart's
    # marker series can pick up.
    coordinator._last_fit = None  # forces _should_refit() to fire again
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.last_fit is not None
    assert coordinator.data.last_fit > first_fit
    state = hass.states.get("sensor.spa_miser_model_last_refit")
    assert dt_util.parse_datetime(state.state) == coordinator.data.last_fit.replace(
        microsecond=0
    )


async def test_recompute_preserves_the_previous_plans_elapsed_slots(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """A recompute must not discard the previous plan's already-elapsed
    slots - compute_strategy itself only ever returns slots from `now`
    forward, so without this, each recompute (now up to several times a
    day via strategy_recompute_interval_hours) would wipe whatever the
    "Expected temperature" chart series had shown as history."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)
    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()
    assert coordinator.strategy is not None

    now = dt_util.utcnow()
    past_slot = StrategySlot(
        start=now - timedelta(hours=2),
        end=now - timedelta(hours=1, minutes=30),
        price=0.1,
        planned_temp_c=37.0,
        heat_on=True,
    )
    # Replace with a synthetic plan whose only slot is already in the past,
    # isolating the merge behaviour from real elapsed time.
    coordinator._strategy = DailyStrategy(computed_at=now, slots=[past_slot])
    coordinator._strategy_recompute_interval_hours = 0.0

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.strategy is not None
    assert past_slot in coordinator.strategy.slots, (
        "recompute discarded an already-elapsed slot from the previous plan"
    )
    assert any(s.end > now for s in coordinator.strategy.slots), (
        "recompute produced no new forward-looking slots"
    )


async def test_slot_boundary_trigger_is_registered_on_setup(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """Price/strategy slots sit on a wall-clock 30-minute grid, but
    update_interval is a rolling timer with no fixed relation to wall-clock
    time - without a dedicated :00/:30 trigger, a new slot's setpoint could
    apply anywhere from immediately to nearly 30 minutes late."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)
    assert coordinator._unsub_slot_boundary is not None


async def test_slot_boundary_requests_a_refresh(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)
    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    calls = []
    original = coordinator.async_request_refresh

    async def _spy():
        calls.append(True)
        await original()

    coordinator.async_request_refresh = _spy

    await coordinator._handle_slot_boundary(dt_util.utcnow())

    assert calls, "slot boundary firing did not request a refresh"


async def test_implausible_forecast_points_are_dropped(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations, caplog
):
    """A single bad upstream forecast value (observed in practice: a
    Fahrenheit-scaled temperature slipping through unconverted, ~78C) must
    not feed straight into the thermal model with no sanity check."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)

    now = dt_util.utcnow()

    async def _bad_forecast_handler(call: ServiceCall):
        return {
            "weather.home": {
                "forecast": [
                    {
                        "datetime": (now + timedelta(hours=1)).isoformat(),
                        "temperature": 18.0,
                        "wind_speed": 2.0,
                    },
                    {
                        # The exact kind of garbage value that motivated this
                        # check - implausible for any real UK forecast.
                        "datetime": (now + timedelta(hours=2)).isoformat(),
                        "temperature": 78.5,
                        "wind_speed": 2.0,
                    },
                    {
                        "datetime": (now + timedelta(hours=3)).isoformat(),
                        "temperature": -55.0,
                        "wind_speed": 2.0,
                    },
                    {
                        "datetime": (now + timedelta(hours=4)).isoformat(),
                        "temperature": 12.0,
                        "wind_speed": 2.0,
                    },
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", _bad_forecast_handler, supports_response=SupportsResponse.ONLY
    )

    with caplog.at_level("WARNING"):
        points = await coordinator._async_get_weather_forecast()

    assert [p.ambient_temp_c for p in points] == [18.0, 12.0]
    assert "implausible" in caplog.text
    assert "78.5" in caplog.text


async def test_recompute_logs_its_inputs(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations, caplog
):
    """Enough detail to diagnose a bad recompute after the fact (e.g. the
    implausible-forecast case above) without needing debug logging
    pre-emptively enabled before it happens."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True)

    with caplog.at_level("INFO"):
        await coordinator.async_set_enabled(True)
        await hass.async_block_till_done()

    assert "Recomputing strategy" in caplog.text
    assert "model(loss=" in caplog.text
    assert "forecast=" in caplog.text


async def test_strategy_anchor_temp_c_tracks_the_real_recompute_input(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations
):
    """A fresh recompute's first slot simulates forward from the real
    current_temp_c, not from wherever the previous plan's simulated belief
    had drifted to for that same instant - so the "Expected temperature"
    chart's preserved-past segment and its fresh segment can legitimately
    show different values at the same boundary. That's not a bug, but it
    does mean the chart needs the real anchor value to annotate exactly
    when/what a recompute corrected, rather than that only becoming visible
    up to 30 minutes later at the next slot boundary."""
    coordinator, _calls = await _setup_coordinator(hass, cheap_now=True, current_temp=37.5)
    assert coordinator.strategy_anchor_temp_c is None

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.strategy_anchor_temp_c == 37.5
