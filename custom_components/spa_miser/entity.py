"""Shared base entity for spa-miser platforms."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SpaMiserCoordinator


class SpaMiserEntity(CoordinatorEntity[SpaMiserCoordinator]):
    """Base entity sharing one logical 'Spa Miser' device per config entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SpaMiserCoordinator, unique_id_suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Spa Miser",
            manufacturer="spa-miser",
            model="Cost-optimizing spa thermal controller",
        )
