# Spa Miser

Cost-optimizing thermal control for a Balboa hot tub in Home Assistant.

Spa Miser is a [HACS](https://hacs.xyz/)-installable custom integration that
sits on top of an existing spa `climate` entity (for example, the one
published by [esp32_balboa_spa](https://github.com/shomanjk/esp32_balboa_spa)
via MQTT + Home Assistant discovery) and:

- **Models heat loss** from the tub's own temperature history plus outdoor
  temperature and wind, using a simple fitted thermal (Newton's-law-of-cooling)
  model.
- **Holds temperature in a 3-tier window**: *max comfort* (ceiling), *min
  comfort* (day-to-day floor, always enforced), and *min/away* (a deeper
  setback floor for extended absences).
- **Minimises cost** by preferring to heat during cheap or negative-price
  periods (e.g. Octopus Agile), using the thermal model to judge how much
  slack there is before the comfort floor would be breached.
- **Reports** predicted vs. actual kWh, a model-vs-actual temperature graph,
  and an estimate of cost saved, via ordinary Home Assistant sensors.

Spa Miser makes **no firmware changes** and doesn't talk MQTT directly - it
drives the tub purely through the `climate` entity your gateway already
exposes (`climate.set_temperature` / `set_hvac_mode` / `set_preset_mode`), and
detects manual intervention so it won't fight anyone who adjusts the tub by
hand.

## Requirements

- A spa `climate` entity with `heat`/`off` modes and `Low Range`/`High Range`
  presets (as published by esp32_balboa_spa's MQTT discovery).
- A numeric water-temperature `sensor` entity for the same tub (the gateway
  publishes one separately from the climate entity's attribute - HA's
  long-term statistics only track plain sensor entities, not climate
  attributes, and the model needs that history).
- A `weather` entity supporting hourly forecasts (`weather.get_forecasts`).
- A power sensor (device_class `power`) and an energy sensor (device_class
  `energy`) on the heater's circuit.
- Recommended: an outdoor temperature `sensor` entity with its own history.
  Without one, the thermal model has no ambient signal to fit against and
  stays disabled (predictions/decisions won't run) until you add one.
- Optional: an outdoor wind speed `sensor` entity, for a wind-chill term in
  the model.
- A time-of-use price source: the
  [Octopus Energy integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)
  (Agile tariff) is supported directly, or use the manual fixed-cheap-hours
  fallback if you don't have a dynamic tariff yet.

## Setup

1. Install via HACS (custom repository until listed) or copy
   `custom_components/spa_miser` into your `config/custom_components/`.
2. Restart Home Assistant, then add the **Spa Miser** integration and pick
   your entities.
3. Leave **Automatic control enabled** (`switch.spa_miser_enabled`) **off**
   at first. Spa Miser will still compute and expose
   `binary_sensor.spa_miser_heating_recommended` and the model sensors in
   this "shadow mode" so you can sanity-check its decisions for a few days
   before handing over control.
4. The thermal model needs a few weeks of history (via HA's recorder
   long-term statistics) before it fits well - `sensor.spa_miser_model_fit_quality`
   (disabled by default; enable it in the entity list) shows the fit's R².

## Entities created

| Entity | Purpose |
|---|---|
| `switch.spa_miser_enabled` | Master on/off for automatic control (shadow mode when off) |
| `switch.spa_miser_away_mode` | Deep setback to the min/away floor |
| `number.spa_miser_max_comfort_temp` / `min_comfort_temp` / `min_away_temp` | Live-adjustable comfort window |
| `binary_sensor.spa_miser_heating_recommended` | What the decision engine currently recommends |
| `sensor.spa_miser_model_temperature` | Model-predicted water temperature |
| `sensor.spa_miser_predicted_kwh_today` | Estimated energy needed today |
| `sensor.spa_miser_actual_kwh_today` | Measured energy used today |
| `sensor.spa_miser_cost_saved_today` | Estimated saving vs. a naive always-on baseline |
| `sensor.spa_miser_decision_reason` | Why the current recommendation was made |
| `sensor.spa_miser_loss_coefficient` / `wind_coefficient` / `thermal_mass` / `model_fit_quality` | Fitted model diagnostics (disabled by default) |

## Example dashboard

Uses only built-in Lovelace cards - no extra frontend dependency required.

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: climate.balboa_spa
  - type: glance
    entities:
      - entity: switch.spa_miser_enabled
      - entity: switch.spa_miser_away_mode
      - entity: binary_sensor.spa_miser_heating_recommended
  - type: history-graph
    title: Model vs. actual temperature
    hours_to_show: 72
    entities:
      - entity: sensor.spa_miser_model_temperature
        name: Model
      - entity: sensor.balboa_spa_current_temperature
        name: Actual
  - type: statistics-graph
    title: Daily energy
    period: day
    days_to_show: 14
    entities:
      - sensor.spa_miser_predicted_kwh_today
      - sensor.spa_miser_actual_kwh_today
  - type: entities
    title: Cost
    entities:
      - sensor.spa_miser_cost_saved_today
      - sensor.spa_miser_decision_reason
```

For nicer overlaid line charts, the HACS card
[apexcharts-card](https://github.com/RomRider/apexcharts-card) works well with
the same entities - not required, just a suggestion.

## How it decides

Each cycle, Spa Miser simulates a pure coast-down (heater off) from the
current temperature to find when the comfort floor would be breached. If
that's imminent, heating is forced on regardless of price - the comfort floor
is a hard constraint. Otherwise, it looks at the price forecast between now
and that breach point: negative prices always count as "cheap", and
otherwise the cheapest ~30% of the visible window does. Heat is only
recommended during a cheap slot, and only while there's room left below the
ceiling.

This is a deliberately simple greedy heuristic - not a full optimizer - which
keeps it easy to reason about for a single on/off heating asset. A proper
linear-program scheduler is a natural future enhancement.

## Not yet implemented (ideas, not commitments)

- Anticipating planned usage ("warm by 6pm") rather than purely reactive
  control.
- Scheduled away periods (currently `away_mode` is a manual switch).
- A local outdoor weather station as a live-current-conditions input
  alongside (not instead of) the forecast, which is what the decision engine
  actually needs.
- Additional price-source adapters (Nord Pool, Amber).

## License

MIT - see [LICENSE](LICENSE).
