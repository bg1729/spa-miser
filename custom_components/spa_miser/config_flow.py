"""Config flow for spa-miser.

One-time setup: pick the entities to read from/control, and choose a price
source. Day-to-day tuning (enabled/away mode/comfort temperatures) happens
live via the switch/number entities this integration creates, not here -
changing those doesn't require reconfiguring the integration.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import selector

from .const import (
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
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_SENSOR,
    DEFAULT_MANUAL_CHEAP_HOURS,
    DEFAULT_MANUAL_CHEAP_RATE,
    DEFAULT_MANUAL_OVERRIDE_MINUTES,
    DEFAULT_MANUAL_STANDARD_RATE,
    DEFAULT_MAX_COMFORT_TEMP,
    DEFAULT_MIN_AWAY_TEMP,
    DEFAULT_MIN_COMFORT_TEMP,
    DOMAIN,
    PRICE_SOURCE_MANUAL,
    PRICE_SOURCE_OCTOPUS_AGILE,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
)


def _entity_selector(domain: str, device_class: str | None = None) -> selector.EntitySelector:
    # The real bug (verified against HA's own selector source): EntitySelector
    # runs its config through voluptuous immediately on construction, which
    # calls cv.ensure_list() on device_class - and cv.ensure_list(None) is
    # [], not "no filter". An explicit device_class=None therefore becomes
    # "device_class must be in []", matching nothing, for every field that
    # doesn't filter by device_class. Fix: omit the key entirely instead of
    # passing None.
    config: dict[str, Any] = {"domain": [domain]}
    if device_class:
        config["device_class"] = [device_class]
    return selector.EntitySelector(selector.EntitySelectorConfig(**config))


def _temp_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=TEMP_MIN, max=TEMP_MAX, step=TEMP_STEP, unit_of_measurement="°C"
        )
    )


STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIMATE_ENTITY): _entity_selector("climate"),
        vol.Required(CONF_WATER_TEMP_SENSOR): _entity_selector("sensor", "temperature"),
        vol.Required(CONF_HEATING_STATE_ENTITY): _entity_selector("sensor"),
        vol.Required(CONF_WEATHER_ENTITY): _entity_selector("weather"),
        vol.Required(CONF_POWER_ENTITY): _entity_selector("sensor", "power"),
        vol.Required(CONF_ENERGY_ENTITY): _entity_selector("sensor", "energy"),
        vol.Optional(CONF_OUTDOOR_TEMP_SENSOR): _entity_selector("sensor", "temperature"),
        vol.Optional(CONF_WIND_SPEED_SENSOR): _entity_selector("sensor", "wind_speed"),
        vol.Required(
            CONF_MAX_COMFORT_TEMP, default=DEFAULT_MAX_COMFORT_TEMP
        ): _temp_selector(),
        vol.Required(
            CONF_MIN_COMFORT_TEMP, default=DEFAULT_MIN_COMFORT_TEMP
        ): _temp_selector(),
        vol.Required(
            CONF_MIN_AWAY_TEMP, default=DEFAULT_MIN_AWAY_TEMP
        ): _temp_selector(),
        vol.Required(
            CONF_MANUAL_OVERRIDE_MINUTES, default=DEFAULT_MANUAL_OVERRIDE_MINUTES
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=1440, step=15, unit_of_measurement="min")
        ),
        vol.Required(CONF_PRICE_SOURCE, default=PRICE_SOURCE_OCTOPUS_AGILE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[PRICE_SOURCE_OCTOPUS_AGILE, PRICE_SOURCE_MANUAL],
                translation_key="price_source",
            )
        ),
    }
)

STEP_OCTOPUS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY): _entity_selector("event"),
    }
)

STEP_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_MANUAL_CHEAP_HOURS, default=DEFAULT_MANUAL_CHEAP_HOURS
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[str(h) for h in range(24)], multiple=True
            )
        ),
        vol.Required(
            CONF_MANUAL_CHEAP_RATE, default=DEFAULT_MANUAL_CHEAP_RATE
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-1, max=2, step=0.01)
        ),
        vol.Required(
            CONF_MANUAL_STANDARD_RATE, default=DEFAULT_MANUAL_STANDARD_RATE
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-1, max=2, step=0.01)
        ),
    }
)


class SpaMiserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for spa-miser."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._user_data = user_input
            if user_input[CONF_PRICE_SOURCE] == PRICE_SOURCE_OCTOPUS_AGILE:
                return await self.async_step_octopus()
            return await self.async_step_manual_price()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    async def async_step_octopus(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            data = {**self._user_data, **user_input}
            return self._async_create(data)
        return self.async_show_form(step_id="octopus", data_schema=STEP_OCTOPUS_SCHEMA)

    async def async_step_manual_price(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            manual_hours = [int(h) for h in user_input[CONF_MANUAL_CHEAP_HOURS]]
            data = {**self._user_data, **user_input, CONF_MANUAL_CHEAP_HOURS: manual_hours}
            return self._async_create(data)
        return self.async_show_form(step_id="manual_price", data_schema=STEP_MANUAL_SCHEMA)

    def _async_create(self, data: dict[str, Any]):
        return self.async_create_entry(title="Spa Miser", data=data)
