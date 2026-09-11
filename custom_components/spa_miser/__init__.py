"""The spa-miser integration: cost-optimizing hot tub thermal control."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import SpaMiserCoordinator

# unique_id suffixes from the short-lived per-field "source" sensor design
# (one sensor.spa_miser_source_* per configured entity), replaced by the
# single sensor.spa_miser_configured_sources whose attributes carry the same
# information. HA's entity registry doesn't drop entities an integration
# stops creating on its own, so these linger as "unavailable" until removed.
_STALE_SENSOR_UNIQUE_ID_SUFFIXES = (
    "source_climate_entity",
    "source_water_temp_sensor",
    "source_heating_state_entity",
    "source_weather_entity",
    "source_power_entity",
    "source_energy_entity",
    "source_outdoor_temp_sensor",
    "source_wind_speed_sensor",
)


def _async_remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    registry = er.async_get(hass)
    for suffix in _STALE_SENSOR_UNIQUE_ID_SUFFIXES:
        unique_id = f"{entry.entry_id}_{suffix}"
        if entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id):
            registry.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _async_remove_stale_entities(hass, entry)

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
