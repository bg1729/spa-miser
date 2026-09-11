"""Config flow for spa-miser.

Sets up which entities to read from/control and which price source to use.
Day-to-day tuning (enabled/away mode/comfort temperatures) happens live via
the switch/number entities this integration creates, not here. Everything
else - including switching price source - goes through "Reconfigure" on the
entry (async_step_reconfigure below), which walks the same steps pre-filled
with current values and updates the entry in place rather than requiring
removal and re-adding.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow
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
    CONF_OCTOPUS_REGION,
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
    OCTOPUS_REGIONS,
    PRICE_SOURCE_MANUAL,
    PRICE_SOURCE_OCTOPUS_AGILE,
    PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC,
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
                options=[
                    PRICE_SOURCE_OCTOPUS_AGILE,
                    PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC,
                    PRICE_SOURCE_MANUAL,
                ],
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

STEP_OCTOPUS_PUBLIC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OCTOPUS_REGION): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=letter, label=f"{name} ({letter})")
                    for letter, name in OCTOPUS_REGIONS.items()
                ],
            )
        ),
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
    """Handle a config flow for spa-miser, including reconfigure."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_data: dict[str, Any] = {}
        self._reconfigure_entry: ConfigEntry | None = None

    def _current_data(self) -> dict[str, Any]:
        """Values to pre-fill a step's form with.

        Reconfiguring an existing entry, this is its current data (so fields
        that aren't changing don't need re-entering); fresh setup, it's
        whatever earlier steps in this same flow already collected.
        """
        if self._reconfigure_entry is not None:
            return {**self._reconfigure_entry.data, **self._user_data}
        return self._user_data

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        self._reconfigure_entry = self._get_reconfigure_entry()
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._user_data = user_input
            price_source = user_input[CONF_PRICE_SOURCE]
            if price_source == PRICE_SOURCE_OCTOPUS_AGILE:
                return await self.async_step_octopus()
            if price_source == PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC:
                return await self.async_step_octopus_public()
            return await self.async_step_manual_price()

        schema = self.add_suggested_values_to_schema(STEP_USER_SCHEMA, self._current_data())
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_octopus(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            data = {**self._user_data, **user_input}
            return self._async_finish(data)
        schema = self.add_suggested_values_to_schema(
            STEP_OCTOPUS_SCHEMA, self._current_data()
        )
        return self.async_show_form(step_id="octopus", data_schema=schema)

    async def async_step_octopus_public(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            data = {**self._user_data, **user_input}
            return self._async_finish(data)
        schema = self.add_suggested_values_to_schema(
            STEP_OCTOPUS_PUBLIC_SCHEMA, self._current_data()
        )
        return self.async_show_form(step_id="octopus_public", data_schema=schema)

    async def async_step_manual_price(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            manual_hours = [int(h) for h in user_input[CONF_MANUAL_CHEAP_HOURS]]
            data = {**self._user_data, **user_input, CONF_MANUAL_CHEAP_HOURS: manual_hours}
            return self._async_finish(data)
        schema = self.add_suggested_values_to_schema(
            STEP_MANUAL_SCHEMA, self._current_data()
        )
        return self.async_show_form(step_id="manual_price", data_schema=schema)

    def _async_finish(self, data: dict[str, Any]):
        if self._reconfigure_entry is not None:
            return self.async_update_reload_and_abort(self._reconfigure_entry, data=data)
        return self.async_create_entry(title="Spa Miser", data=data)
