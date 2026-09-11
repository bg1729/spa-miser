"""The spa-miser integration: cost-optimizing hot tub thermal control."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import SpaMiserCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SpaMiserCoordinator(hass, entry)
    coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # No options-update listener: this integration persists its own runtime
    # state (enabled/away_mode/comfort temps) into entry.options purely so it
    # survives a restart. Reloading the entry on every switch/number change
    # would tear down and re-create the coordinator (losing the fitted model
    # and energy-day baseline) on every toggle, which is not what's wanted.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SpaMiserCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()
    return unloaded
