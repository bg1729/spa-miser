"""number.spa_miser_{max_comfort,min_comfort,min_away}_temp - live-adjustable comfort window."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    HIGH_RANGE_MAX_C,
    HIGH_RANGE_MIN_C,
    LOW_RANGE_MAX_C,
    LOW_RANGE_MIN_C,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
)
from .coordinator import SpaMiserCoordinator
from .entity import SpaMiserEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpaMiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            _SpaMiserNumber(
                coordinator,
                key="max_comfort_temp",
                icon="mdi:thermometer-high",
                # Written to the spa as the High Range preset's setpoint
                # (see coordinator._async_apply_decision) - bounded to that
                # preset's real valid band, not the full TEMP_MIN/TEMP_MAX.
                min_value=HIGH_RANGE_MIN_C,
                max_value=HIGH_RANGE_MAX_C,
                get_fn=lambda c: c.max_comfort_c,
                set_fn=lambda c, v: c.async_set_max_comfort_c(v),
            ),
            _SpaMiserNumber(
                coordinator,
                key="min_comfort_temp",
                icon="mdi:thermometer",
                # Never written to the spa directly - purely a decision
                # threshold (when to start heating) - so it isn't bound to
                # either preset's hardware band.
                min_value=TEMP_MIN,
                max_value=TEMP_MAX,
                get_fn=lambda c: c.min_comfort_c,
                set_fn=lambda c, v: c.async_set_min_comfort_c(v),
            ),
            _SpaMiserNumber(
                coordinator,
                key="min_away_temp",
                icon="mdi:thermometer-low",
                # Written to the spa as the Low Range preset's setpoint (see
                # coordinator._async_apply_decision) - bounded to that
                # preset's real valid band, not the full TEMP_MIN/TEMP_MAX.
                min_value=LOW_RANGE_MIN_C,
                max_value=LOW_RANGE_MAX_C,
                get_fn=lambda c: c.min_away_c,
                set_fn=lambda c, v: c.async_set_min_away_c(v),
            ),
        ]
    )


class _SpaMiserNumber(SpaMiserEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = TEMP_STEP
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SpaMiserCoordinator,
        *,
        key: str,
        icon: str,
        min_value: float,
        max_value: float,
        get_fn: Callable[[SpaMiserCoordinator], float],
        set_fn: Callable[[SpaMiserCoordinator, float], Coroutine[Any, Any, None]],
    ) -> None:
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._get_fn = get_fn
        self._set_fn = set_fn

    @property
    def suggested_object_id(self) -> str:
        # Overridden: suggested_object_id otherwise slugifies the full
        # translated display name (e.g. "max_comfort_temp" -> "Max comfort
        # temperature" -> entity_id ...max_comfort_temperature), drifting
        # from the key and from what the README documents. See the
        # identical fix on SpaMiserSensor in sensor.py.
        return self._key

    @property
    def native_value(self) -> float:
        return self._get_fn(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self._set_fn(self.coordinator, value)
