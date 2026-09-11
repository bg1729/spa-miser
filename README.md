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
- **Minimises cost** by computing a committed 24h heating plan once new
  price data arrives (e.g. when Octopus Agile publishes tomorrow's rates),
  choosing when to heat to stay within the comfort window at the lowest
  cost - naturally preferring cheap and negative-price slots without any
  special-casing.
- **Reports** predicted vs. actual kWh, a model-vs-actual temperature graph,
  the plan itself (so you can see what it intends to do, and how that
  lines up with price), and an estimate of cost saved, via ordinary Home
  Assistant sensors.

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
- A time-of-use price source - three options:
  - The [Octopus Energy integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy),
    if you're actually billed on Agile.
  - **Octopus Agile (public rates)** - pulls Agile rates straight from
    Octopus's public API (just a GSP region letter, e.g. `E` for West
    Midlands), no Octopus account needed. Useful for scheduling around real
    Agile pricing even while billed on a different tariff.
  - A manual fixed-cheap-hours fallback if you don't have a dynamic tariff at
    all yet.

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
   (in the [Thermal model diagnostics](#example-dashboard) card) shows the
   fit's R². `sensor.spa_miser_current_price` populates immediately regardless (it
   doesn't depend on the model), so it's the fastest way to confirm a price
   source is actually wired up correctly. If your outdoor temperature sensor
   was only just enabled, the model has nothing to fit against yet either -
   press **`button.spa_miser_estimate_initial_model`** to bootstrap a first
   fit immediately instead of waiting ~24h for real history to build up (see
   [Bootstrapping the model](#bootstrapping-the-model) below for what that
   button actually does).
5. To change entities or price source later - including switching between
   the three price sources - use **Reconfigure** on the integration (⋮ menu
   on its card in Settings → Devices & Services), not remove-and-re-add.
   Every field is pre-filled with its current value.

## Entities created

HA's device page only has two tiers for a sensor (the default section, or a
collapsed "Diagnostic" one) - not arbitrary named groups - so that's how
these are split: raw input readings under Diagnostic, spa-miser's own
model state and outputs in the default section. The [example
dashboard](#example-dashboard) below groups things more thoroughly than the
device page can.

**Controls:**

| Entity | Purpose |
|---|---|
| `switch.spa_miser_enabled` | Master on/off for automatic control (shadow mode when off) |
| `switch.spa_miser_away_mode` | Deep setback to the min/away floor |
| `number.spa_miser_max_comfort_temp` / `min_comfort_temp` / `min_away_temp` | Live-adjustable comfort window |
| `button.spa_miser_estimate_initial_model` | Bootstraps a first model fit now instead of waiting ~24h - see [Bootstrapping the model](#bootstrapping-the-model) |

**Model state & outputs:**

| Entity | Purpose |
|---|---|
| `binary_sensor.spa_miser_heating_recommended` | What the decision engine currently recommends |
| `sensor.spa_miser_model_temperature` | Model-predicted water temperature |
| `sensor.spa_miser_predicted_kwh_today` | Estimated energy needed today |
| `sensor.spa_miser_actual_kwh_today` | Measured energy used today |
| `sensor.spa_miser_cost_saved_today` | Estimated saving vs. a naive always-on baseline |
| `sensor.spa_miser_decision_reason` | Why the current recommendation was made |
| `sensor.spa_miser_current_price` | Current price (p/kWh) from whichever price source is configured - populates immediately, doesn't need the model |
| `sensor.spa_miser_daily_strategy` | The committed 24h plan: state is when it was last computed, `slots` attribute has the planned temperature/price/heat-on per slot (see [Example dashboard](#example-dashboard)) |

**Diagnostic (collapsed by default on the device page):**

| Entity | Purpose |
|---|---|
| `sensor.spa_miser_water_temperature` / `heating_state` / `outdoor_temperature` | Raw current readings of the configured input entities |
| `sensor.spa_miser_configured_sources` | Which entity is wired to each role (state = count configured; attributes = the full mapping) |
| `sensor.spa_miser_price_slots_available` | How many forecast price slots the price source returned |
| `sensor.spa_miser_loss_coefficient` / `wind_coefficient` / `thermal_mass` / `model_fit_quality` | Fitted thermal model internals - see [Thermal model diagnostics](#example-dashboard) |
| `sensor.spa_miser_estimated_water_volume` | Implied tub volume from the fitted thermal mass - a sanity check, not a model input |

## Example dashboard

Uses only built-in Lovelace cards - no extra frontend dependency required.
Named sections here give the full Inputs / Model / Controls grouping the
device page itself can't.

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: climate.balboa_spa

  - type: entities
    title: Controls
    entities:
      - entity: switch.spa_miser_enabled
      - entity: switch.spa_miser_away_mode
      - entity: number.spa_miser_max_comfort_temperature
      - entity: number.spa_miser_min_comfort_temperature
      - entity: number.spa_miser_min_away_temperature

  - type: entities
    title: Inputs
    entities:
      - entity: sensor.spa_miser_water_temperature
      - entity: sensor.spa_miser_outdoor_temperature
      - entity: sensor.spa_miser_heating_state
      - entity: sensor.spa_miser_current_price
      - entity: sensor.spa_miser_price_slots_available

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
    title: Model & decisions
    entities:
      - entity: binary_sensor.spa_miser_heating_recommended
      - entity: sensor.spa_miser_decision_reason
      - entity: sensor.spa_miser_cost_saved_today

  - type: entities
    title: Thermal model diagnostics
    entities:
      - entity: sensor.spa_miser_loss_coefficient
      - entity: sensor.spa_miser_wind_coefficient
      - entity: sensor.spa_miser_thermal_mass
      - entity: sensor.spa_miser_model_fit_quality
      - entity: sensor.spa_miser_estimated_water_volume
```

`estimated_water_volume` isn't used by the model itself - it's a plain-English
sanity check, backing out an implied water volume from the fitted thermal
mass (via water's specific heat capacity). If it's wildly off from the tub's
actual rated capacity, that's a much easier way to spot a bad fit than
squinting at `model_fit_quality` alone.

The fitted coefficients above are kept separate from the day-to-day cards -
they're only useful when judging whether the model's fit is trustworthy
(e.g. a low `model_fit_quality` explains why the plan looks off), not
something you'd want cluttering a glance at today's status.

### Plan vs. actual, with price overlaid (apexcharts-card)

The cards above are all HA has built in, and can't render *future* data -
`history-graph`/`statistics-graph` only show recorded history. To actually
see the plan (not just today's numbers), install
[apexcharts-card](https://github.com/RomRider/apexcharts-card) via HACS and
use its `data_generator` option to plot `sensor.spa_miser_daily_strategy`'s
`slots` attribute as a forecast series, alongside the real past data from
`sensor.spa_miser_water_temperature` / `current_price` / `heating_state`.
Temperature and price get their own y-axes; heating on/off (both planned and
actual) is drawn as a low-opacity band on a third, hidden axis so it's
visible without dominating the chart - this is what actually shows *when*
the heater ran (or will run) against *when* electricity was cheap.

```yaml
type: custom:apexcharts-card
header:
  title: Spa heating plan vs. actual
graph_span: 48h
span:
  start: day
now:
  show: true
  label: Now
yaxis:
  - id: temp
    decimals: 0
    apex_config:
      title:
        text: "°C"
  - id: price
    opposite: true
    decimals: 0
    apex_config:
      title:
        text: "p/kWh"
  - id: heat
    show: false
    min: 0
    max: 4
series:
  - entity: sensor.spa_miser_water_temperature
    name: Actual temperature
    yaxis_id: temp
    color: "#1f77b4"
  - entity: sensor.spa_miser_daily_strategy
    name: Planned temperature
    yaxis_id: temp
    color: "#1f77b4"
    opacity: 0.5
    curve: stepline
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.planned_temp_c
      ]);
  - entity: sensor.spa_miser_current_price
    name: Actual price
    yaxis_id: price
    color: "#ff7f0e"
  - entity: sensor.spa_miser_daily_strategy
    name: Planned price
    yaxis_id: price
    color: "#ff7f0e"
    opacity: 0.5
    curve: stepline
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.price * 100
      ]);
  - entity: sensor.spa_miser_heating_state
    name: Actual heating
    yaxis_id: heat
    type: area
    color: "#2ca02c"
    opacity: 0.3
    transform: 'return (x === "Heating (active)" || x === "Heating (alternate stage)") ? 1 : 0;'
  - entity: sensor.spa_miser_daily_strategy
    name: Planned heating
    yaxis_id: heat
    type: area
    color: "#2ca02c"
    opacity: 0.15
    curve: stepline
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.heat_on ? 1 : 0
      ]);
```

## How it decides

**Primary path - the daily strategy.** Once new price data arrives (Octopus
Agile publishes tomorrow's rates ~4pm; the manual source just rolls over
daily), Spa Miser refits the thermal model, then computes a plan covering
the available forecast: a dynamic-programming search over discretized
temperature that picks heat-on/off per slot to minimize total cost,
constrained to never drop below the comfort floor and never exceed the
ceiling (the spa's own thermostat wouldn't overshoot it anyway). Negative
prices are naturally preferred - minimizing signed cost already rewards
consuming during them, no special-casing needed. That plan is then held
fixed and followed until the next price update or comfort-window change
triggers a recompute (see `sensor.spa_miser_daily_strategy`).

**Fallback path - a greedy heuristic**, used only when no plan is available
yet (e.g. before the first successful model fit): simulate a pure coast-down
from the current temperature to find when the floor would be breached, and
heat only during the cheapest ~30% of the price window up to that point (or
immediately, if the breach is imminent - the floor is a hard constraint
either way).

Both paths respect `switch.spa_miser_enabled` the same way - shadow mode
(the switch off) computes and reports everything identically, it just never
calls the climate services, so `sensor.spa_miser_daily_strategy` and the
model-vs-actual graph work the same whether or not spa-miser is actually
driving the tub.

## Bootstrapping the model

The thermal model needs real hourly history for your water temperature,
outdoor temperature, and heater power to fit against. If your outdoor
temperature sensor was only just enabled, HA has no history for it yet
(disabled entities record nothing) - normally that just means waiting
~24h for enough hours to accumulate.

`button.spa_miser_estimate_initial_model` skips that wait. Pressing it:

1. Fetches real historical outdoor temperature/wind for your area from
   [Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api)'s
   free, public historical weather archive - no account or API key, just
   your HA instance's own configured latitude/longitude (Settings → System
   → General). That's the only thing this sends anywhere; nothing else
   leaves your instance.
2. Uses that estimate to fill in **only the hours your real outdoor sensor
   has no reading for** - real sensor data always wins where both exist -
   then immediately attempts a fit.
3. Stays enabled going forward, so future automatic refits (daily, or on
   every cycle until the first fit succeeds) also get to use it, not just
   this one press - until your real sensor has built up about a week of its
   own history, at which point spa-miser stops calling Open-Meteo at all
   since it's no longer needed.

This is entirely optional - if you'd rather just wait for real history to
accumulate on its own, don't press the button and nothing external is ever
contacted.

## Not yet implemented (ideas, not commitments)

- Anticipating planned usage ("warm by 6pm") rather than the comfort window
  alone.
- Scheduled away periods (currently `away_mode` is a manual switch).
- Deliberately drifting below the normal comfort floor during a defined
  low-priority window, to defer energy use entirely to an exceptionally
  cheap or negative slot (today the floor is always a hard constraint).
- Continuous (rolling-horizon) strategy recomputation instead of once per
  price update.
- A local outdoor weather station as a live-current-conditions input
  alongside (not instead of) the forecast, which is what the decision engine
  actually needs.
- Additional price-source adapters (Nord Pool, Amber).

## License

MIT - see [LICENSE](LICENSE).
