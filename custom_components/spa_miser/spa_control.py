"""Drives the spa's existing `climate` entity and detects manual overrides.

spa-miser never talks to the ESP32 gateway's MQTT topics directly — it issues
normal `climate.*` service calls against the entity the gateway's own Home
Assistant MQTT Discovery already created, and reads that entity's state back.
Every call it makes is tagged with its own `Context`; if the entity changes
under a different context (someone used the HA UI, the physical panel is
reflected back through the gateway, etc.) automatic control is suspended for
`override_minutes` so spa-miser doesn't fight the household.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import HVAC_MODE_HEAT, HVAC_MODE_OFF

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE_STATES = {"unavailable", "unknown", None}


class SpaControl:
    """Wraps the spa climate entity with override detection and availability checks."""

    def __init__(
        self, hass: HomeAssistant, climate_entity_id: str, override_minutes: int
    ) -> None:
        self._hass = hass
        self._entity_id = climate_entity_id
        self._override_minutes = override_minutes
        self._own_context_ids: set[str] = set()
        self._override_until: datetime | None = None
        self._unsub = None

    def async_setup(self) -> None:
        self._unsub = async_track_state_change_event(
            self._hass, [self._entity_id], self._handle_state_change
        )

    def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @property
    def is_available(self) -> bool:
        state = self._hass.states.get(self._entity_id)
        return state is not None and state.state not in UNAVAILABLE_STATES

    @property
    def is_manually_overridden(self) -> bool:
        if self._override_until is None:
            return False
        return dt_util.utcnow() < self._override_until

    @property
    def override_until(self) -> datetime | None:
        """When the current manual-override pause ends, if one is active."""
        return self._override_until if self.is_manually_overridden else None

    @property
    def current_temperature(self) -> float | None:
        state = self._hass.states.get(self._entity_id)
        if state is None:
            return None
        value = state.attributes.get("current_temperature")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> str | None:
        state = self._hass.states.get(self._entity_id)
        return state.state if state is not None else None

    @property
    def preset_mode(self) -> str | None:
        state = self._hass.states.get(self._entity_id)
        return state.attributes.get("preset_mode") if state is not None else None

    def _handle_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        if new_state is None or old_state is None:
            return
        if event.context.id in self._own_context_ids:
            self._own_context_ids.discard(event.context.id)
            return
        if (
            new_state.state == old_state.state
            and new_state.attributes.get("temperature")
            == old_state.attributes.get("temperature")
            and new_state.attributes.get("preset_mode")
            == old_state.attributes.get("preset_mode")
        ):
            return
        _LOGGER.info(
            "Detected manual change to %s outside spa-miser; pausing automatic "
            "control for %s minutes",
            self._entity_id,
            self._override_minutes,
        )
        self._override_until = dt_util.utcnow() + timedelta(
            minutes=self._override_minutes
        )

    async def _async_call(self, service: str, data: dict) -> None:
        context = Context()
        self._own_context_ids.add(context.id)
        await self._hass.services.async_call(
            "climate",
            service,
            {"entity_id": self._entity_id, **data},
            blocking=True,
            context=context,
        )

    async def async_set_heat_enabled(self, enabled: bool) -> None:
        await self._async_call(
            "set_hvac_mode",
            {"hvac_mode": HVAC_MODE_HEAT if enabled else HVAC_MODE_OFF},
        )

    async def async_set_preset(self, preset: str) -> None:
        await self._async_call("set_preset_mode", {"preset_mode": preset})

    async def async_set_target_temperature(self, temperature: float) -> None:
        await self._async_call("set_temperature", {"temperature": temperature})
