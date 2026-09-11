"""Reporting sensors: model output, forecasts, kWh, and cost savings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_ENERGY_ENTITY,
    CONF_HEATING_STATE_ENTITY,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_POWER_ENTITY,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_SENSOR,
    DOMAIN,
)
from .coordinator import SpaMiserCoordinator, SpaMiserData
from .entity import SpaMiserEntity


@dataclass(frozen=True, kw_only=True)
class SpaMiserSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SpaMiserData], float | str | None] = lambda data: None


SENSOR_DESCRIPTIONS: tuple[SpaMiserSensorDescription, ...] = (
    SpaMiserSensorDescription(
        key="model_temperature",
        translation_key="model_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda d: d.model_temperature_c,
    ),
    # Deliberately no device_class=ENERGY here: that device_class implies
    # monotonically-accumulating statistics (total/total_increasing), but
    # these are same-day estimates that can move up and down as the forecast
    # updates or the day rolls over. MEASUREMENT still gets them proper
    # recorder statistics (mean/min/max) for graphing model-vs-actual.
    SpaMiserSensorDescription(
        key="predicted_kwh_today",
        translation_key="predicted_kwh_today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.predicted_kwh_today,
    ),
    SpaMiserSensorDescription(
        key="actual_kwh_today",
        translation_key="actual_kwh_today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.actual_kwh_today,
    ),
    SpaMiserSensorDescription(
        key="cost_saved_today",
        translation_key="cost_saved_today",
        icon="mdi:cash-plus",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.cost_saved_today,
    ),
    SpaMiserSensorDescription(
        key="decision_reason",
        translation_key="decision_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.decision.reason if d.decision else None,
    ),
    # Independent of the thermal model/decision (both need ~24h of history
    # before producing anything) - the fastest way to confirm a price
    # source is actually wired up and returning real data, without waiting
    # a day for the model to fit.
    SpaMiserSensorDescription(
        key="current_price",
        translation_key="current_price",
        icon="mdi:cash",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: d.current_price,
    ),
    SpaMiserSensorDescription(
        key="price_slots_available",
        translation_key="price_slots_available",
        icon="mdi:format-list-numbered",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.price_slots_count,
    ),
    SpaMiserSensorDescription(
        key="loss_coefficient",
        translation_key="loss_coefficient",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=4,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.model.loss_coefficient if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="wind_coefficient",
        translation_key="wind_coefficient",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=4,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.model.wind_coefficient if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="thermal_mass",
        translation_key="thermal_mass",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.model.thermal_mass_kwh_per_c if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="model_fit_quality",
        translation_key="model_fit_quality",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.model.r_squared if d.model else None,
    ),
)


# (role label, config key, required) - drives the single configured-sources
# sensor below. A plain diagnostic sensor's *state* renders in a compact
# list on the device page; a long entity_id string as that state collided
# with the row's label there. Attributes render fine in the more-info
# dialog instead, so the full mapping lives there on one sensor rather than
# as eight separate hard-to-read rows.
SOURCE_ENTITY_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("climate_entity", CONF_CLIMATE_ENTITY, True),
    ("water_temp_sensor", CONF_WATER_TEMP_SENSOR, True),
    ("heating_state_entity", CONF_HEATING_STATE_ENTITY, True),
    ("weather_entity", CONF_WEATHER_ENTITY, True),
    ("power_entity", CONF_POWER_ENTITY, True),
    ("energy_entity", CONF_ENERGY_ENTITY, True),
    ("outdoor_temp_sensor", CONF_OUTDOOR_TEMP_SENSOR, False),
    ("wind_speed_sensor", CONF_WIND_SPEED_SENSOR, False),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpaMiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [SpaMiserSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS]
        + [ConfiguredSourcesSensor(coordinator)]
    )


class SpaMiserSensor(SpaMiserEntity, SensorEntity):
    entity_description: SpaMiserSensorDescription

    def __init__(
        self, coordinator: SpaMiserCoordinator, description: SpaMiserSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)


class ConfiguredSourcesSensor(SpaMiserEntity, SensorEntity):
    """One compact sensor: state is a count, full mapping is in attributes."""

    _attr_translation_key = "configured_sources"
    _attr_icon = "mdi:link-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "configured_sources")

    @property
    def native_value(self) -> int:
        return sum(
            1
            for _, conf_key, _ in SOURCE_ENTITY_FIELDS
            if self.coordinator.entry.data.get(conf_key)
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            label: self.coordinator.entry.data.get(conf_key) or "Not configured"
            for label, conf_key, _ in SOURCE_ENTITY_FIELDS
        }
