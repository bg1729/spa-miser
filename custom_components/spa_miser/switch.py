"""switch.spa_miser_enabled (live-control kill switch) and switch.spa_miser_away_mode."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SpaMiserCoordinator
from .entity import SpaMiserEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpaMiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            _SpaMiserSwitch(
                coordinator,
                key="enabled",
                icon="mdi:auto-mode",
                is_on_fn=lambda c: c.enabled,
                set_fn=lambda c, v: c.async_set_enabled(v),
            ),
            _SpaMiserSwitch(
                coordinator,
                key="away_mode",
                icon="mdi:home-export-outline",
                is_on_fn=lambda c: c.away_mode,
                set_fn=lambda c, v: c.async_set_away_mode(v),
            ),
        ]
    )


class _SpaMiserSwitch(SpaMiserEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: SpaMiserCoordinator,
        *,
        key: str,
        icon: str,
        is_on_fn: Callable[[SpaMiserCoordinator], bool],
        set_fn: Callable[[SpaMiserCoordinator, bool], Coroutine[Any, Any, None]],
    ) -> None:
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        self._is_on_fn = is_on_fn
        self._set_fn = set_fn

    @property
    def suggested_object_id(self) -> str:
        # Overridden: suggested_object_id otherwise slugifies the full
        # translated display name (e.g. "enabled" -> "Automatic control
        # enabled" -> entity_id ...automatic_control_enabled), drifting from
        # the key and from what the README documents. See the identical fix
        # on SpaMiserSensor in sensor.py.
        return self._key

    @property
    def is_on(self) -> bool:
        return self._is_on_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_fn(self.coordinator, False)
