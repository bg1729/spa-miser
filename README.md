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
| `number.spa_miser_max_comfort_temp` / `min_comfort_temp` / `min_away_temp` | Live-adjustable comfort window - `max_comfort_temp` (26-40°C) and `min_away_temp` (10-26°C) are each bounded to the real hardware's valid setpoint range for the High Range / Low Range preset they're written to; `min_comfort_temp` is a decision threshold only, never written to the spa directly |
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
| `sensor.spa_miser_price_slots_available` | State = how many price slots the price source returned; `slots` attribute has the whole day's curve (past and future, start/end/price) - independent of whether a strategy has been computed from it |
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
      - entity: number.spa_miser_max_comfort_temp
      - entity: number.spa_miser_min_comfort_temp
      - entity: number.spa_miser_min_away_temp

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
`history-graph`/`statistics-graph` only show recorded history, because a
sensor's recorder history is only ever written as time actually passes -
there's no way for it to hold tomorrow's rows today. Price is the one
exception where that limitation doesn't have to matter: an Agile rate is
fixed the moment it's published and never revised, so there's no "actual
vs. forecast" distinction to draw for it in the first place - unlike
temperature or heating state, which really do depend on what happens (or
the strategy's decisions, which really are a plan). All three price sources
now return the *whole* day's rates, including hours that have already
elapsed - so `sensor.spa_miser_price_slots_available`'s `slots` attribute
alone is the complete price curve, past and future, straight from
whichever price source is configured. Install
[apexcharts-card](https://github.com/RomRider/apexcharts-card) via HACS and
use its `data_generator` option to plot that as a single "Price" series,
alongside `sensor.spa_miser_daily_strategy`'s `slots` for the genuinely
forward-looking planned temperature/heating (spa-miser's own decisions,
which *do* need a successful thermal-model fit first - so this one stays
its own series, separate from the ever-present price curve), and the real
past data from `sensor.spa_miser_water_temperature` / `heating_state`.
Temperature and price get their own y-axes; heating on/off (both planned
and actual) is drawn as a warm-colored, full-height rectangle on a third,
hidden 0-1 axis - the y-position isn't meaningful (on/off has no
magnitude), only the x-extent (when it was/will be on) matters, so the fill
deliberately spans the whole chart height rather than sitting at some
arbitrary partial height. Actual heating is solid red; planned heating is a
dotted burnt-orange outline (`apex_config.stroke.dashArray`, one entry per
series) - same warm family, but visually distinct from both each other and
from the (also-orange) price line. This is what actually shows *when* the
heater ran (or will run) against *when* electricity was cheap.

Every series sets `show.legend_value: false`. apexcharts-card's default
legend shows each series' *last value within the currently-visible time
range* - for a forecast series that's the prediction at the far future edge
of the chart, easily misread as "right now." The chart already has an
explicit `now` marker for that, so the legend numbers are redundant at best
and misleading at worst.

Every series also sets `extend_to`, overriding apexcharts-card's default of
`'end'` (flat-line the last known value all the way to the edge of the
visible chart). Left at the default, the price/planned series would draw a
straight, misleadingly-confident line across however much of the chart has
no real data yet - most of the time we only have real Agile rates through
midnight tonight (tomorrow's don't publish until ~4pm), so a chunk of the
chart is often genuinely unknown, and pretending otherwise by repeating the
last known price is actively wrong, not just uninformative. `extend_to:
false` on `Expected temperature`/`Price`/`Planned heating` stops each line
exactly where its real data ends, leaving the rest of the chart blank until
the next price update actually extends it - which is also what "start a
new chart" on each re-plan amounts to in practice: the same persistent
card just always draws exactly as much real data as currently exists, no
more. `extend_to: 'now'` on the two `Actual` series (instead of the same
default) extends *those* only up to the present moment, not into the
future - they're real recorded state, but still can't have data beyond
"now" either.

`graph_span: 36h` is a compromise, not an exact fit: apexcharts-card has no
way to bind the visible x-axis range to actual entity data (`graph_span`
only takes a fixed duration, and `apex_config` fields aren't templated
against entity state), so it can't be made to end exactly where our price
data ends - that boundary moves during the day anyway. Real coverage is
actually bimodal, not gradual: before Octopus publishes tomorrow's rates
(~4pm) we only have ~24h of real data (today, from local midnight); after
~4pm we have a full ~48h (today + tomorrow). 36h splits the difference -
away from the default 48h, a lot of blank canvas after a plan recomputes
mid-morning would be visible; fixed at 24h, part of tomorrow's already-known
plan would be clipped off every evening. Either edge of that range is a
defensible choice; this just picks the middle.

Two more series, `Max comfort`/`Min comfort`, draw dotted horizontal
reference lines for the comfort window - bound directly to
`number.spa_miser_max_comfort_temp`/`min_comfort_temp` rather than
hardcoded numbers, so they stay correct if you ever adjust those sliders.
Unlike the price/plan series, `extend_to: end` is intentional here: a
setpoint isn't time-varying forecast data that could be wrong beyond some
horizon, it's a live config value that applies uniformly across the whole
chart.

```yaml
type: custom:apexcharts-card
header:
  title: Spa heating plan vs. actual
apex_config:
  chart:
    height: 450
  stroke:
    # One entry per series below, in order - 3 dots the two heating
    # rectangles' border, 4 dots the two setpoint reference lines, 0
    # (solid) for every other series.
    dashArray: [0, 0, 0, 3, 3, 4, 4]
graph_span: 36h
span:
  start: day
now:
  show: true
  label: Now
yaxis:
  - id: temp
    # Soft bounds spanning the tub's realistic operating range (still
    # auto-expands if real data ever goes outside them, e.g. away mode) -
    # without these, apexcharts-card auto-scales tightly around whatever
    # narrow range is currently visible, which makes an ordinary ~0.5°C
    # fluctuation look like a dramatic swing. decimals: 1 (not 0) so two
    # distinct nearby values (e.g. 37.6 and 38.2) don't round to the same
    # tick label and appear to duplicate.
    min: "~30"
    max: "~42"
    decimals: 1
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
    max: 1
series:
  - entity: sensor.spa_miser_water_temperature
    name: Actual temperature
    yaxis_id: temp
    color: "#1f77b4"
    stroke_width: 1.5
    extend_to: now
    show:
      legend_value: false
  - entity: sensor.spa_miser_daily_strategy
    name: Expected temperature
    yaxis_id: temp
    color: "#17becf"
    curve: stepline
    stroke_width: 1.5
    extend_to: false
    show:
      legend_value: false
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.planned_temp_c
      ]);
  - entity: sensor.spa_miser_price_slots_available
    name: Price
    yaxis_id: price
    color: "#ff7f0e"
    curve: stepline
    stroke_width: 1.5
    extend_to: false
    show:
      legend_value: false
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.price * 100
      ]);
  - entity: sensor.spa_miser_heating_state
    name: Actual heating
    yaxis_id: heat
    type: area
    color: "#d62728"
    opacity: 0.25
    curve: stepline
    stroke_width: 2
    extend_to: now
    show:
      legend_value: false
    transform: 'return (x === "Heating (active)" || x === "Heating (alternate stage)") ? 1 : 0;'
  - entity: sensor.spa_miser_daily_strategy
    name: Planned heating
    yaxis_id: heat
    type: area
    color: "#d95f02"
    opacity: 0.15
    curve: stepline
    stroke_width: 2
    extend_to: false
    show:
      legend_value: false
    data_generator: |
      return entity.attributes.slots.map((slot) => [
        new Date(slot.start).getTime(), slot.heat_on ? 1 : 0
      ]);
  - entity: number.spa_miser_max_comfort_temp
    name: Max comfort
    yaxis_id: temp
    color: "#595959"
    type: line
    stroke_width: 1
    extend_to: end
    show:
      legend_value: false
  - entity: number.spa_miser_min_comfort_temp
    name: Min comfort
    yaxis_id: temp
    color: "#a5a5a5"
    type: line
    stroke_width: 1
    extend_to: end
    show:
      legend_value: false
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
