"""Tests for the spa-miser config flow."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
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
    CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY,
    CONF_OCTOPUS_REGION,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
    PRICE_SOURCE_OCTOPUS_AGILE,
    PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC,
)
from custom_components.spa_miser.price_sources.octopus_agile_public import (
    PRODUCTS_URL,
)

BASE_USER_INPUT = {
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
}


async def test_octopus_flow_creates_entry(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**BASE_USER_INPUT, CONF_PRICE_SOURCE: PRICE_SOURCE_OCTOPUS_AGILE},
    )
    assert result["step_id"] == "octopus"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY: (
                "event.octopus_energy_electricity_1234_5678_current_day_rates"
            )
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_PRICE_SOURCE] == PRICE_SOURCE_OCTOPUS_AGILE
    assert (
        result["data"][CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY]
        == "event.octopus_energy_electricity_1234_5678_current_day_rates"
    )
    # Omitted from BASE_USER_INPUT - the schema's own default should apply.
    assert (
        result["data"][CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS]
        == DEFAULT_STRATEGY_RECOMPUTE_INTERVAL_HOURS
    )


async def test_octopus_public_flow_creates_entry(
    hass: HomeAssistant, enable_custom_integrations: None, aioclient_mock
) -> None:
    # Completing this flow triggers a real config entry setup, which does
    # an immediate coordinator refresh - without this, the product lookup
    # hits the real network (api.octopus.energy), which is slow/flaky in CI
    # and can trip pytest-homeassistant-custom-component's leaked-thread
    # check if DNS resolution fails oddly in the test environment.
    aioclient_mock.get(PRODUCTS_URL, status=500)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**BASE_USER_INPUT, CONF_PRICE_SOURCE: PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC},
    )
    assert result["step_id"] == "octopus_public"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_OCTOPUS_REGION: "L"}
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_PRICE_SOURCE] == PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC
    assert result["data"][CONF_OCTOPUS_REGION] == "L"


async def test_manual_price_flow_creates_entry(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**BASE_USER_INPUT, CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL},
    )
    assert result["step_id"] == "manual_price"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_MANUAL_CHEAP_HOURS: ["1", "2", "3"],
            CONF_MANUAL_CHEAP_RATE: 0.1,
            CONF_MANUAL_STANDARD_RATE: 0.3,
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_MANUAL_CHEAP_HOURS] == [1, 2, 3]


async def test_reconfigure_switches_price_source_without_a_new_entry(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **BASE_USER_INPUT,
            CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL,
            CONF_MANUAL_CHEAP_HOURS: [1, 2, 3],
            CONF_MANUAL_CHEAP_RATE: 0.1,
            CONF_MANUAL_STANDARD_RATE: 0.3,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "user"
    # Existing values must be pre-filled, not blank, so switching just the
    # price source doesn't require re-picking every entity.
    assert result["data_schema"]({**BASE_USER_INPUT, CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**BASE_USER_INPUT, CONF_PRICE_SOURCE: PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC},
    )
    assert result["step_id"] == "octopus_public"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_OCTOPUS_REGION: "E"}
    )
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"

    # Updated in place - still exactly one config entry for this domain.
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_PRICE_SOURCE] == PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC
    assert entries[0].data[CONF_OCTOPUS_REGION] == "E"
    # Stale manual-price fields from before the switch must not linger.
    assert CONF_MANUAL_CHEAP_HOURS not in entries[0].data


async def test_strategy_recompute_interval_can_be_overridden(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **BASE_USER_INPUT,
            CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS: 12,
            CONF_PRICE_SOURCE: PRICE_SOURCE_MANUAL,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "manual_cheap_hours": ["0", "1", "2", "3", "4", "5"],
            "manual_cheap_rate": 0.10,
            "manual_standard_rate": 0.30,
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS] == 12
