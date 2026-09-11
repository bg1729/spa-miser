"""Constants for the spa-miser integration."""
from __future__ import annotations

DOMAIN = "spa_miser"

PLATFORMS = ["sensor", "binary_sensor", "switch", "number", "button"]

# --- Config entry keys (set once, via config flow) ---
CONF_CLIMATE_ENTITY = "climate_entity"
# The gateway also publishes water temperature as a plain numeric `sensor.*`
# entity (distinct from the climate entity's current_temperature attribute).
# That sensor gets normal HA long-term statistics, whereas a climate
# attribute does not - so it's what fitting and live temperature reads use.
CONF_WATER_TEMP_SENSOR = "water_temp_sensor"
CONF_HEATING_STATE_ENTITY = "heating_state_entity"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_POWER_ENTITY = "power_entity"
CONF_ENERGY_ENTITY = "energy_entity"
# Optional: dedicated sensor entities for model-FITTING history. The weather
# entity's forecast attributes work fine for forward-looking predictions via
# weather.get_forecasts, but its current-condition attributes generally are
# NOT captured in HA's recorder long-term statistics the way a plain `sensor.*`
# entity is - so historical ambient/wind data for fitting needs its own
# sensor entities if precise fitting is wanted. Wind fitting is skipped
# (coefficient held at 0) when no wind sensor is configured.
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_WIND_SPEED_SENSOR = "wind_speed_sensor"
CONF_PRICE_SOURCE = "price_source"
CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY = "octopus_current_day_rates_entity"
# For the public-API Agile source: a GSP region letter (A-P), independent of
# any Octopus account - lets the decision engine schedule against real Agile
# pricing even for a user who isn't actually on the Agile tariff.
CONF_OCTOPUS_REGION = "octopus_region"
CONF_MANUAL_CHEAP_HOURS = "manual_cheap_hours"
CONF_MANUAL_CHEAP_RATE = "manual_cheap_rate"
CONF_MANUAL_STANDARD_RATE = "manual_standard_rate"

DEFAULT_MANUAL_CHEAP_HOURS = [0, 1, 2, 3, 4, 5]
DEFAULT_MANUAL_CHEAP_RATE = 0.15
DEFAULT_MANUAL_STANDARD_RATE = 0.30

# GSP Group ID letters used throughout Octopus's public API and tariff codes.
OCTOPUS_REGIONS: dict[str, str] = {
    "A": "Eastern England",
    "B": "East Midlands",
    "C": "London",
    "D": "North Wales, Merseyside and Cheshire",
    "E": "West Midlands",
    "F": "North East England",
    "G": "North West England",
    "H": "Southern England",
    "J": "South East England",
    "K": "South Wales",
    "L": "South West England",
    "M": "Yorkshire",
    "N": "South Scotland",
    "P": "North Scotland",
}

PRICE_SOURCE_OCTOPUS_AGILE = "octopus_agile"
PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC = "octopus_agile_public"
PRICE_SOURCE_MANUAL = "manual"
PRICE_SOURCES = [
    PRICE_SOURCE_OCTOPUS_AGILE,
    PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC,
    PRICE_SOURCE_MANUAL,
]

# --- Options keys (adjustable after setup, also mirrored as number entities) ---
CONF_MAX_COMFORT_TEMP = "max_comfort_temp"
CONF_MIN_COMFORT_TEMP = "min_comfort_temp"
CONF_MIN_AWAY_TEMP = "min_away_temp"
CONF_MANUAL_OVERRIDE_MINUTES = "manual_override_minutes"
# Runtime-adjustable switch state, also persisted to config entry options so
# it survives a restart.
CONF_ENABLED = "enabled"
CONF_AWAY_MODE = "away_mode"
DEFAULT_ENABLED = False
DEFAULT_AWAY_MODE = False
# Opt-in only, via button.spa_miser_estimate_initial_model - see open_meteo.py.
# Off by default so nothing external is contacted without the user
# explicitly choosing to (the button's name/description is the "prompt").
CONF_WEATHER_BACKFILL_ENABLED = "weather_backfill_enabled"
DEFAULT_WEATHER_BACKFILL_ENABLED = False

DEFAULT_MAX_COMFORT_TEMP = 40.0
DEFAULT_MIN_COMFORT_TEMP = 36.0
DEFAULT_MIN_AWAY_TEMP = 25.0
DEFAULT_MANUAL_OVERRIDE_MINUTES = 120

TEMP_MIN = 10.0
TEMP_MAX = 40.5
TEMP_STEP = 0.5

# --- Preset / mode strings used by the esp32_balboa_spa climate entity ---
PRESET_LOW_RANGE = "Low Range"
PRESET_HIGH_RANGE = "High Range"
HVAC_MODE_HEAT = "heat"
HVAC_MODE_OFF = "off"

# heatingState sensor values that mean the element is actually drawing power
HEATING_STATE_ACTIVE_VALUES = {"Heating (active)", "Heating (alternate stage)"}

# --- Timing ---
COORDINATOR_UPDATE_INTERVAL_MINUTES = 30
MODEL_REFIT_INTERVAL_HOURS = 24
MODEL_FIT_LOOKBACK_DAYS = 21
DECISION_LOOKAHEAD_HOURS = 36

# --- Entity unique_id suffixes ---
ATTR_MODEL_TEMPERATURE = "model_temperature"
ATTR_LOSS_COEFFICIENT = "loss_coefficient"
ATTR_WIND_COEFFICIENT = "wind_coefficient"
ATTR_THERMAL_MASS = "thermal_mass"
ATTR_PREDICTED_KWH_TODAY = "predicted_kwh_today"
ATTR_ACTUAL_KWH_TODAY = "actual_kwh_today"
ATTR_COST_SAVED_TODAY = "cost_saved_today"
ATTR_HEATING_RECOMMENDED = "heating_recommended"

SIGNAL_UPDATE = f"{DOMAIN}_update"
