"""Reporting sensors: model output, forecasts, kWh, and cost savings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, UnitOfVolume
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
from .strategy import serialize_strategy
from .thermal_model import estimated_volume_liters


@dataclass(frozen=True, kw_only=True)
class SpaMiserSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SpaMiserData], float | str | datetime | None] = lambda data: None


SENSOR_DESCRIPTIONS: tuple[SpaMiserSensorDescription, ...] = (
    # Raw current readings of the configured inputs - what spa-miser is
    # actually seeing right now, without cross-referencing entity_ids from
    # sensor.spa_miser_configured_sources against those entities elsewhere.
    # Diagnostic: these are pass-through telemetry, not spa-miser's own
    # output, so they belong in the device page's collapsed section rather
    # than crowding the headline sensors below.
    SpaMiserSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.current_temperature_c,
    ),
    SpaMiserSensorDescription(
        key="heating_state",
        translation_key="heating_state",
        icon="mdi:fire",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.heating_state,
    ),
    SpaMiserSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.outdoor_temperature_c,
    ),
    # --- Model state & outputs: the headline numbers, kept in the default
    # (non-diagnostic) section so they're visible without expanding anything.
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
        icon="mdi:message-text-outline",
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
        # Displayed as pence/kWh (e.g. "20.1 p/kWh") rather than pounds -
        # device_class=monetary would force an ISO currency code (GBP) as
        # the unit, which can't express a "p/kWh" rate; PriceSlot.price is
        # pounds internally (matches how the price sources parse it), so
        # convert here at the display boundary only.
        native_unit_of_measurement="p/kWh",
        suggested_display_precision=1,
        value_fn=lambda d: d.current_price * 100 if d.current_price is not None else None,
    ),
    # Fitted thermal model internals - grouped as "Thermal model diagnostics"
    # in the example dashboard, distinct from the raw input readings above
    # (the device page itself has no third tier to separate them into;
    # these were disabled-by-default until enabled here for that dashboard
    # section to actually have something to show).
    SpaMiserSensorDescription(
        key="loss_coefficient",
        translation_key="loss_coefficient",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Fraction of the water-ambient temperature gap lost per hour.
        native_unit_of_measurement="1/h",
        suggested_display_precision=4,
        value_fn=lambda d: d.model.loss_coefficient if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="wind_coefficient",
        translation_key="wind_coefficient",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Additional loss_coefficient per m/s of wind - same "per hour" basis,
        # scaled by wind speed, hence the compound unit.
        native_unit_of_measurement="1/h per m/s",
        suggested_display_precision=4,
        value_fn=lambda d: d.model.wind_coefficient if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="thermal_mass",
        translation_key="thermal_mass",
        entity_category=EntityCategory.DIAGNOSTIC,
        # No device_class fits: this isn't an energy reading, it's a fitted
        # capacity coefficient (kWh of heat input per °C of water temperature
        # rise) - so just label the unit directly.
        native_unit_of_measurement="kWh/°C",
        suggested_display_precision=2,
        value_fn=lambda d: d.model.thermal_mass_kwh_per_c if d.model else None,
    ),
    SpaMiserSensorDescription(
        key="model_fit_quality",
        translation_key="model_fit_quality",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=3,
        # Rounded here, not just via suggested_display_precision: that's
        # only a rendering hint some card types (plain entities-card rows
        # included) don't apply, leaving the raw unrounded float displayed.
        value_fn=lambda d: round(d.model.r_squared, 3) if d.model else None,
    ),
    # A plain diagnostic in its own right ("is my model still fresh"), and
    # - since HA's recorder keeps history for any sensor automatically -
    # doubles as a ready-made log of past refit times, with no separate
    # bookkeeping needed: see the "Model refresh" series on the example
    # dashboard, which reads this sensor's own history as marker points.
    SpaMiserSensorDescription(
        key="model_last_refit",
        translation_key="model_last_refit",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_fit,
    ),
    # Not used by the model itself - a sanity check for the fit as a whole.
    # An implied volume wildly off from the tub's actual rated capacity
    # (e.g. a few hundred litres, or tens of thousands) is a much more
    # legible red flag for a bad fit than staring at model_fit_quality alone.
    SpaMiserSensorDescription(
        key="estimated_water_volume",
        translation_key="estimated_water_volume",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda d: estimated_volume_liters(d.model) if d.model else None,
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
        + [
            ConfiguredSourcesSensor(coordinator),
            DailyStrategySensor(coordinator),
            PriceForecastSensor(coordinator),
            ControlStatusSensor(coordinator),
            ActiveRangeSensor(coordinator),
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
    def suggested_object_id(self) -> str:
        # Overridden: suggested_object_id otherwise slugifies the full
        # translated display name (e.g. "Fitted loss coefficient",
        # "Estimated water volume (sanity check)"), giving an entity_id that
        # drifts from description.key and from what the README documents.
        # Pinning it to the key keeps entity_id stable and predictable
        # regardless of how verbose/descriptive the display name is.
        return self.entity_description.key

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


class DailyStrategySensor(SpaMiserEntity, SensorEntity):
    """The committed 24h heating plan: state is when it was computed, the
    full plan (planned temperature + heat on/off + price per slot) is in
    attributes for a chart (e.g. apexcharts-card) to plot as a future
    series - HA's built-in history/statistics-graph cards only render
    recorded history, not a forecast, so this is the only way to expose it.
    """

    _attr_translation_key = "daily_strategy"
    _attr_icon = "mdi:calendar-clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "daily_strategy")

    @property
    def suggested_object_id(self) -> str:
        # Keeps entity_id as sensor.spa_miser_daily_strategy regardless of
        # the friendly name shown to the user (see the "Heating strategy"
        # rename) - same reasoning as SpaMiserSensor/PriceForecastSensor.
        return "daily_strategy"

    @property
    def native_value(self):
        strategy = self.coordinator.strategy
        return strategy.computed_at if strategy else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        strategy = self.coordinator.strategy
        if strategy is None:
            return {"slots": []}
        return {"slots": serialize_strategy(strategy)["slots"]}


class PriceForecastSensor(SpaMiserEntity, SensorEntity):
    """The raw price forecast exactly as received from the price source.

    Deliberately independent of sensor.spa_miser_daily_strategy: that plan
    only exists once the thermal model has fit and computed a strategy from
    this same data, so a price-only chart shouldn't have to wait on (or
    depend on the success of) either of those - this exposes the forecast
    the moment the price source itself returns it. State is the slot count
    (unchanged from before this became a dedicated sensor); attributes carry
    the full curve for a chart's data_generator, since HA's built-in history
    cards can't render future timestamps.
    """

    _attr_translation_key = "price_slots_available"
    _attr_icon = "mdi:format-list-numbered"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "price_slots_available")

    @property
    def suggested_object_id(self) -> str:
        return "price_slots_available"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.price_slots_count

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "slots": [
                {
                    "start": slot.start.isoformat(),
                    "end": slot.end.isoformat(),
                    "price": slot.price,
                }
                for slot in self.coordinator.data.price_slots
            ]
        }


class ControlStatusSensor(SpaMiserEntity, SensorEntity):
    """Explains, in plain language, whether spa-miser is actually able to
    act right now. Most importantly: a manual-override pause (see
    spa_control.py) is otherwise invisible from every other entity, since
    it deliberately looks identical to "nothing to do right now" - this is
    the one place that distinction is surfaced at all.
    """

    _attr_translation_key = "control_status"
    _attr_icon = "mdi:robot-outline"

    _LABELS = {
        "disabled": "Disabled",
        "paused_manual_override": "Paused (manual override)",
        "unavailable": "Unavailable",
        "no_decision": "Not ready yet",
        "active": "Active",
    }

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "control_status")

    @property
    def native_value(self) -> str:
        status = self.coordinator.data.control_status
        return self._LABELS.get(status, status)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        override_until = self.coordinator.data.control_override_until
        return {"override_until": override_until.isoformat() if override_until else None}


class ActiveRangeSensor(SpaMiserEntity, SensorEntity):
    """Which hardware preset is currently commanded, and why.

    Away mode and the price cap (see coordinator._compute_active_range)
    both force Low Range, and would otherwise be indistinguishable from
    each other or from normal operation - the spa behaves identically
    regardless of which one caused it.
    """

    _attr_translation_key = "active_range"
    _attr_icon = "mdi:swap-horizontal"

    _REASON_LABELS = {
        "normal": "Normal",
        "away_mode": "Away mode",
        "price_cap": "Price cap exceeded",
    }

    def __init__(self, coordinator: SpaMiserCoordinator) -> None:
        super().__init__(coordinator, "active_range")

    @property
    def native_value(self) -> str:
        return self.coordinator.data.active_preset

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        reason = self.coordinator.data.range_reason
        return {"reason": self._REASON_LABELS.get(reason, reason)}
