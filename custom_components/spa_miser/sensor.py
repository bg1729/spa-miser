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


@dataclass(frozen=True, kw_only=True)
class SourceSensorDescription(SensorEntityDescription):
    """Describes a diagnostic sensor that just reports a configured entity_id.

    Not driven by the coordinator - these reflect the config entry's own
    data (set once at setup), so they exist purely so the device page shows,
    at a glance, exactly which entity each field is reading from. That's
    otherwise invisible: the source entities belong to other integrations'
    devices, so HA has no built-in way to surface "what this integration
    depends on" without something like this.
    """

    conf_key: str = ""


SOURCE_SENSOR_DESCRIPTIONS: tuple[SourceSensorDescription, ...] = (
    SourceSensorDescription(
        key="source_climate_entity",
        translation_key="source_climate_entity",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_CLIMATE_ENTITY,
    ),
    SourceSensorDescription(
        key="source_water_temp_sensor",
        translation_key="source_water_temp_sensor",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_WATER_TEMP_SENSOR,
    ),
    SourceSensorDescription(
        key="source_heating_state_entity",
        translation_key="source_heating_state_entity",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_HEATING_STATE_ENTITY,
    ),
    SourceSensorDescription(
        key="source_weather_entity",
        translation_key="source_weather_entity",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_WEATHER_ENTITY,
    ),
    SourceSensorDescription(
        key="source_power_entity",
        translation_key="source_power_entity",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_POWER_ENTITY,
    ),
    SourceSensorDescription(
        key="source_energy_entity",
        translation_key="source_energy_entity",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_ENERGY_ENTITY,
    ),
    SourceSensorDescription(
        key="source_outdoor_temp_sensor",
        translation_key="source_outdoor_temp_sensor",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_OUTDOOR_TEMP_SENSOR,
    ),
    SourceSensorDescription(
        key="source_wind_speed_sensor",
        translation_key="source_wind_speed_sensor",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        conf_key=CONF_WIND_SPEED_SENSOR,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpaMiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [SpaMiserSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS]
        + [
            ConfiguredSourceSensor(coordinator, description)
            for description in SOURCE_SENSOR_DESCRIPTIONS
        ]
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


class ConfiguredSourceSensor(SpaMiserEntity, SensorEntity):
    entity_description: SourceSensorDescription

    def __init__(
        self, coordinator: SpaMiserCoordinator, description: SourceSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str:
        value = self.coordinator.entry.data.get(self.entity_description.conf_key)
        return value or "Not configured"
