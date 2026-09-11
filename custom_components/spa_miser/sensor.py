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

from .const import DOMAIN
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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpaMiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SpaMiserSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
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
