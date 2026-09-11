"""binary_sensor.spa_miser_heating_recommended - visible even in shadow mode."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
    async_add_entities([HeatingRecommendedBinarySensor(coordinator)])


class HeatingRecommendedBinarySensor(SpaMiserEntity, BinarySensorEntity):
    """Whether spa-miser's decision engine currently recommends heating.

    Reflects the *recommendation*, not necessarily reality: while
    `switch.spa_miser_enabled` is off, spa-miser computes this without
    touching the spa, so it can be validated before automatic control is
    switched on.
    """

    _attr_translation_key = "heating_recommended"
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "heating_recommended")

    @property
    def is_on(self) -> bool | None:
        decision = self.coordinator.data.decision if self.coordinator.data else None
        return decision.heat_recommended if decision else None

    @property
    def extra_state_attributes(self):
        decision = self.coordinator.data.decision if self.coordinator.data else None
        if decision is None:
            return {}
        return {
            "reason": decision.reason,
            "floor_breach_hours": decision.floor_breach_hours,
            "cheap_price_threshold": decision.cheap_price_threshold,
            "current_price": decision.current_price,
        }
