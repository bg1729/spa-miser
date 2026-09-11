"""button.spa_miser_estimate_initial_model - user-triggered bootstrap fit."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    async_add_entities([EstimateInitialModelButton(coordinator)])


class EstimateInitialModelButton(SpaMiserEntity, ButtonEntity):
    """Bootstraps a first thermal-model fit sooner than waiting ~24h for the
    real outdoor temperature sensor to accumulate its own history.

    Pressing this sends this Home Assistant instance's configured
    latitude/longitude (Settings -> System -> General) to Open-Meteo's
    free, public historical weather API (archive-api.open-meteo.com,
    no account or API key involved) to fill in outdoor temperature/wind for
    any hours the real sensor doesn't have data for yet, then immediately
    attempts a fit. Real sensor data always takes priority over the
    estimate where both exist. This also stays enabled for future automatic
    refits, not just this one press - see CONF_WEATHER_BACKFILL_ENABLED.
    """

    _attr_translation_key = "estimate_initial_model"
    _attr_icon = "mdi:cloud-download-outline"

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "estimate_initial_model")

    @property
    def suggested_object_id(self) -> str:
        # Deliberately overridden: suggested_object_id otherwise slugifies
        # the full (long, on purpose - it's the privacy disclosure) display
        # name, giving an unwieldy entity_id. This keeps entity_id short and
        # stable regardless of how the display name is worded.
        return "estimate_initial_model"

    async def async_press(self) -> None:
        await self.coordinator.async_trigger_initial_estimate()
