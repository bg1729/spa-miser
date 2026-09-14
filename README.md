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
  setback floor for extended absences). These are spa-miser's own concepts,
  not the tub's - see [Terminology](#terminology-spa-miser-concepts-vs-the-tubs-own) if
  you're coming from the gateway project's own vocabulary (`High Range` /
  `Low Range`, `setTemp`, etc.).
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

## Terminology: spa-miser concepts vs. the tub's own

Two different vocabularies are in play, and they don't map 1:1 - this is a
common source of confusion (including for the author, mid-project):

| spa-miser concept | The tub's own concept | Relationship |
|---|---|---|
| `max_comfort_temp` (the ceiling) | `High Range` preset | Written to the spa **as** the High Range setpoint whenever `heat_recommended` is true and High Range is in force. Bounded 26-40°C - the real hardware's own valid range for High Range (see below). |
| `min_comfort_temp` (the day-to-day floor) | `High Range` preset | Written to the spa **as** the High Range setpoint while coasting (`heat_recommended` false) - see "How it decides" below for why this is a real setpoint now, not just a soft threshold. |
| `min_away_temp` (the away/price-cap setback) | `Low Range` preset | Written to the spa **as** the Low Range setpoint whenever Low Range is in force. Bounded 10-26°C - the real hardware's own valid range for Low Range. |
| `away_mode` (a switch) / `max_price` (a threshold) | *(nothing - no equivalent)* | spa-miser's own ideas; either one being active is what decides *which* of the two presets/setpoints above is in force - see `sensor.spa_miser_active_range`. |

The `High Range` / `Low Range` names and their 26-40°C / 10-26°C bands come
from the Balboa protocol itself (verified directly against the gateway
firmware's own `spaProtocolActiveSetpointBand()`) - they're just two halves
of one continuous setpoint dial, with **no inherent meaning** ("high" isn't
"comfort", "low" isn't "away"). Attaching that meaning - ceiling/floor ↔
High Range, away/price-cap setback ↔ Low Range - is entirely spa-miser's
own design choice.

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
| `number.spa_miser_max_comfort_temp` / `min_comfort_temp` / `min_away_temp` | Live-adjustable comfort window - `max_comfort_temp` (26-40°C) and `min_away_temp` (10-26°C) are each bounded to the real hardware's valid setpoint range for the High Range / Low Range preset they're written to; `min_comfort_temp` is a decision threshold that's also written to the spa (as the High Range setpoint) while coasting - see "How it decides" below |
| `number.spa_miser_max_price` | Above this price (p/kWh), fall back to the Low Range/`min_away_temp` safety floor instead of paying to defend the comfort window - a decision threshold only, never written to the spa directly. Defaults high enough to be a no-op until you lower it |
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
| `sensor.spa_miser_control_status` | Whether spa-miser can actually act right now: `Disabled`, `Paused (manual override)`, `Unavailable`, `Not ready yet`, or `Active` - in particular, a manual-override pause (see `manual_override_minutes`) is otherwise invisible from every other entity, since it looks identical to "nothing to do right now". `override_until` attribute has the resume time when paused. |
| `sensor.spa_miser_current_price` | Current price (p/kWh) from whichever price source is configured - populates immediately, doesn't need the model |
| `sensor.spa_miser_daily_strategy` | The committed heating strategy: state is when it was last computed, `slots` attribute has the planned temperature/price/heat-on per slot as parallel arrays (`start`/`end` epoch seconds, `price`/`planned_temp_c` rounded floats, `heat_on` 0/1 - see [Example dashboard](#example-dashboard)) - despite the entity name, it's no longer strictly "daily": see `strategy_recompute_interval_hours` below |
| `sensor.spa_miser_active_range` | Which hardware preset is actually in force right now (`High Range` / `Low Range`) and why - `reason` attribute is `Normal`, `Away mode`, or `Price cap exceeded` |

**Diagnostic (collapsed by default on the device page):**

| Entity | Purpose |
|---|---|
| `sensor.spa_miser_water_temperature` / `heating_state` / `outdoor_temperature` | Raw current readings of the configured input entities |
| `sensor.spa_miser_configured_sources` | Which entity is wired to each role (state = count configured; attributes = the full mapping) |
| `sensor.spa_miser_price_slots_available` | State = how many price slots the price source returned; `slots` attribute has the whole day's curve (past and future, start/end/price) - independent of whether a strategy has been computed from it |
| `sensor.spa_miser_loss_coefficient` / `wind_coefficient` / `thermal_mass` / `model_fit_quality` | Fitted thermal model internals - see [Thermal model diagnostics](#example-dashboard) |
| `sensor.spa_miser_model_last_refit` | When the thermal model coefficients were last refitted - a plain diagnostic ("is my model still fresh"), and since HA's recorder keeps history for any sensor automatically, this entity's own history doubles as a ready-made log of past refit times with no separate bookkeeping - see the "Model refresh" marker series on the example dashboard |
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
arbitrary partial height. Both are pure fills with `stroke_width: 0` - no
outline, just the rectangle - since a border adds nothing a solid fill
doesn't already show. Actual heating is red, planned heating is burnt
orange: same warm family, but visually distinct from each other and from
price (yellow, not orange, precisely so it doesn't get lost among them).
This is what actually shows *when* the heater ran (or will run) against
*when* electricity was cheap.

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

The visible window is a rolling 36h ending exactly where real price data
ends, not a fixed calendar span - "beyond that is pointless, we have no
useful data" for the right edge, and "right edge minus 36h" for the left,
so old, no-longer-relevant history keeps sliding out on its own as new
price data arrives. apexcharts-card alone can't do this - `graph_span` only
takes a fixed duration and `apex_config` fields are static YAML, not
templated against entity state - so the chart is wrapped in
[config-template-card](https://github.com/iantrich/config-template-card)
(also via HACS), which can replace *any* nested field in another card's
config with a live JavaScript expression, re-evaluated whenever a watched
entity changes:

```yaml
variables:
  PRICE_END_OFFSET: >-
    (() => { const s =
    (states['sensor.spa_miser_price_slots_available'].attributes.slots)||[];
    const end = s.length ? Math.max(...s.map(x => new Date(x.end).getTime()))
    : Date.now(); const m = Math.round((end - Date.now()) / 60000);
    return (m >= 0 ? '+' : '') + m + 'min'; })()
card:
  span:
    end: minute
    offset: ${PRICE_END_OFFSET}
  graph_span: 36h
```

apexcharts-card requires `span.offset` to start with an explicit `+` or
`-` - even for a positive value, plain `"837min"` is rejected with
`'span.offset: 837min' should start with a '+' or a '-'`, so the sign has
to be added explicitly rather than relying on JS's default (sign-less)
number-to-string conversion.

`PRICE_END_OFFSET` finds the latest slot end across the whole price
forecast (sourced from `sensor.spa_miser_price_slots_available`,
independent of the strategy, so this works even before/without one - see
above) and expresses it as minutes-from-now, e.g. `"842min"`.

This templates `span.offset` itself, **not** `apex_config.xaxis.min`/`max`
- an earlier version of this did the latter and it silently didn't work:
apexcharts-card recomputes its own `xaxis.min`/`max` from `span`+
`graph_span` on *every* data refresh and pushes that via ApexCharts'
`updateOptions()`, overwriting any static or templated `apex_config.xaxis`
override within moments of it being applied. Templating `span.offset`
instead drives the mechanism the card actually uses internally, so the
computed window survives every refresh rather than being clobbered by the
next one. `span: {end: 'minute', offset: ...}` anchors the *right* edge at
"now + that many minutes" (i.e. the price data's real end), and
`graph_span: 36h` extends backward from there - no separate wide-fetch
workaround needed, since this is now the same value driving both what gets
fetched and what gets displayed. Because the right edge tracks real data
exactly, this also fixes what a fixed span never could: the moment
Octopus publishes tomorrow's rates (~4pm), the window jumps forward with
it - no waiting for a calendar boundary, no clipping part of a plan that's
already known.

(Also worth knowing since it cost real debugging time: config-template-card
evaluates each `variables:` entry with `eval()` on its own, *before* any
of them - including itself - are injected into scope; only the *final*
templated fields inside `card:` get every variable injected first. A
second variable referencing `PRICE_END_OFFSET` in its own definition would
throw a `ReferenceError` on every render, and since nothing in the call
chain catches that, the whole card just silently renders blank - which
looks identical to a data or range problem from the outside. Keeping this
to one self-contained variable sidesteps that entirely.)

Two more series, `Max comfort`/`Min comfort`, draw dotted horizontal
reference lines for the comfort window - bound directly to
`number.spa_miser_max_comfort_temp`/`min_comfort_temp` rather than
hardcoded numbers, so they stay correct if you ever adjust those sliders.
Unlike the price/plan series, `extend_to: end` is intentional here: a
comfort bound isn't time-varying forecast data that could be wrong beyond
some horizon, it's a live config value that applies uniformly across the
whole chart.

`Setpoint` plots the spa's real `climate` entity's own `temperature`
attribute (via apexcharts-card's `attribute` option, not `data_generator`
- it's ordinary recorder history, not a computed series) - what spa-miser
(or a manual override) actually told the tub to hold, distinct from both
`Expected temperature` (the thermal model's *prediction* of what the water
will do) and `Actual temperature` (what the water *actually* did). All
three diverging is informative: model vs. actual reveals fit quality,
setpoint vs. actual reveals how fast the tub responds (or whether
something's stopping it from reaching target at all).

`0p` marks where negative pricing begins - a light dotted horizontal line,
via a `data_generator` returning a flat 0 across a fixed, deliberately huge
date range (year 2000 to 2100), not tied to any real data. It has to be a
series like this, not an ApexCharts `annotations.yaxis` entry: apexcharts-
card's own per-refresh update only ever re-injects `points`/`xaxis`
annotations from `apex_config` (there's no equivalent code path for
`yaxis`), so a yaxis annotation gets silently dropped the moment any data
refreshes - which for a chart backed by fast-changing entities is
effectively immediately. No explicit "only if in range" handling is
needed either way: the price axis has no min/max set, so it's fully
auto-scaled, and the line simply doesn't render on days real prices never
cross zero.

```yaml
type: custom:config-template-card
entities:
  # Only entities referenced inside `variables` need listing here - it's
  # what triggers re-evaluating them, not what the wrapped chart itself
  # reads (that's handled by apexcharts-card as normal).
  - sensor.spa_miser_price_slots_available
variables:
  PRICE_END_OFFSET: >-
    (() => { const s =
    (states['sensor.spa_miser_price_slots_available'].attributes.slots)||[];
    const end = s.length ? Math.max(...s.map(x => new Date(x.end).getTime()))
    : Date.now(); const m = Math.round((end - Date.now()) / 60000);
    return (m >= 0 ? '+' : '') + m + 'min'; })()
# grid_options belongs on this outer card - the section's grid layout
# doesn't look inside `card:` for it. columns: "full" (not a number) is
# also deliberate: a section's own column_span scales its *total* internal
# grid units too (12 * column_span), so a fixed numeric columns value ends
# up as a smaller fraction of a wider section - "full" is a dedicated
# value (renders as grid-column: 1 / -1) that bypasses that scaling
# entirely and always spans the whole section regardless of its
# column_span.
grid_options:
  columns: full
  # "auto", not a number: a numeric rows value forces an explicit
  # container height (rows * row-height) via CSS regardless of the chart's
  # actual rendered height - with a much shorter chart than the forced
  # container, that left a large dead-space gap below it. "auto" sizes the
  # container to the chart's real height instead.
  rows: auto
card:
  type: custom:apexcharts-card
  header:
    title: Spa heating plan vs. actual
  apex_config:
    chart:
      height: 900
    grid:
      # Default ApexCharts grid lines read as too bright against HA's dark
      # theme.
      borderColor: "rgba(255, 255, 255, 0.12)"
      strokeDashArray: 3
    stroke:
      # One entry per series below, in order - 3 dots Expected temperature,
      # 2 dots the 0p reference line, 4 dots the two comfort-window
      # reference lines, 0 (solid, or moot at stroke_width: 0) elsewhere.
      dashArray: [0, 3, 0, 0, 2, 0, 0, 4, 4]
  # Drives both what gets fetched and what gets displayed - see prose
  # above for why this has to be span.offset, not apex_config.xaxis.
  span:
    end: minute
    offset: ${PRICE_END_OFFSET}
  graph_span: 36h
  now:
    show: true
    label: Now
  yaxis:
    - id: temp
      # min is a HARD floor (no "~"), deliberately: Setpoint tracks the
      # real climate entity's target, which legitimately drops to ~10-26°C
      # in away mode (Low Range) - a soft bound there would auto-expand the
      # whole axis downward every time away mode engaged, compressing the
      # 30-42°C range everything else actually lives in. Below 30°C,
      # Setpoint (and only Setpoint - nothing else should ever go there)
      # just clips off the bottom of the chart instead. max stays soft
      # ("~42"): nothing on this axis has an equivalent reason to blow past
      # it, so letting it expand if it ever does is fine. Without bounds at
      # all, apexcharts-card auto-scales tightly around whatever narrow
      # range is currently visible, which makes an ordinary ~0.5°C
      # fluctuation look like a dramatic swing. decimals: 1 (not 0) so two
      # distinct nearby values (e.g. 37.6 and 38.2) don't round to the same
      # tick label and appear to duplicate.
      min: 30
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
      color: "#9467bd"
      curve: stepline
      stroke_width: 1.5
      extend_to: false
      show:
        legend_value: false
      data_generator: |
        return entity.attributes.slots.end.map((end, i) => [
          end * 1000, entity.attributes.slots.planned_temp_c[i]
        ]);
    - entity: climate.balboa_spa_spa_controls
      attribute: temperature
      name: Setpoint
      yaxis_id: temp
      color: "#2ca02c"
      curve: stepline
      stroke_width: 1.5
      extend_to: now
      show:
        legend_value: false
    - entity: sensor.spa_miser_price_slots_available
      name: Price
      yaxis_id: price
      color: "#f1c40f"
      curve: stepline
      stroke_width: 1.5
      extend_to: false
      show:
        legend_value: false
      data_generator: |
        return entity.attributes.slots.map((slot) => [
          new Date(slot.start).getTime(), slot.price * 100
        ]);
    - entity: sensor.spa_miser_price_slots_available
      name: 0p
      yaxis_id: price
      color: "rgba(255, 255, 255, 0.3)"
      stroke_width: 1
      show:
        legend_value: false
      data_generator: |
        return [[new Date(2000, 0, 1).getTime(), 0], [new Date(2100, 0, 1).getTime(), 0]];
    - entity: sensor.spa_miser_heating_state
      name: Actual heating
      yaxis_id: heat
      type: area
      color: "#d62728"
      opacity: 0.25
      curve: stepline
      stroke_width: 0
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
      stroke_width: 0
      extend_to: false
      show:
        legend_value: false
      data_generator: |
        return entity.attributes.slots.start.map((start, i) => [
          start * 1000, entity.attributes.slots.heat_on[i]
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

**Primary path - the heating strategy.** Once new price data arrives (Octopus
Agile publishes tomorrow's rates ~4pm; the manual source just rolls over
daily), Spa Miser refits the thermal model, then computes a plan covering
the available forecast: a dynamic-programming search over discretized
temperature that picks heat-on/off per slot to minimize total cost,
constrained to never drop below the comfort floor and never exceed the
ceiling (the spa's own thermostat wouldn't overshoot it anyway). Negative
prices are naturally preferred - minimizing signed cost already rewards
consuming during them, no special-casing needed. That plan is then held
fixed and followed until new price data arrives, the plan's own coverage
runs out, or `strategy_recompute_interval_hours` (default 6) elapses since
it was last computed - whichever comes first (see
`sensor.spa_miser_daily_strategy`). That last one exists because a plan's
ambient/wind assumptions are only as fresh as the forecast available when
it was computed - live-observed drift from forecast error alone reached
~0.5°C over one overnight period before the next day's rates would
otherwise have forced a refresh.

The plan's slots sit on a wall-clock 30-minute grid (`:00`/`:30`), and a
dedicated refresh fires right at each boundary so the setpoint for a new
slot applies within seconds of it starting - it doesn't wait on the
general 30-minute polling cycle, which has no fixed relation to wall-clock
time and could otherwise apply a new slot's setpoint anywhere up to ~30
minutes late.

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

**Never fully "off", and a price cap.** Whichever path produces a
`heat_recommended` decision, spa-miser never commands the spa's `hvac_mode`
to `off` - only ever `heat`, with the *setpoint* varying between the
comfort ceiling and floor. This means the spa's own onboard thermostat is
always actively defending something, so a Home Assistant or spa-miser
outage can't leave the heater fully undefended mid-coast.

Separately, `number.spa_miser_max_price` lets you cap what you're willing
to spend defending the *comfort* window at all: above that price, spa-miser
falls back to the Low Range/`min_away_temp` safety floor instead - the same
one `away_mode` uses - accepting discomfort rather than an expensive bill,
while still actively heating to that floor if needed. This check is
deliberately independent of the DP/decision engine above (which stays
unaware the cap exists, and would otherwise keep recommending heating to
defend the comfort floor even at extreme prices) - it's a simple,
stateless override applied only when actually commanding the spa, evaluated
fresh every cycle against the current price. `sensor.spa_miser_active_range`
shows which preset is actually in force right now, and why.

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

## Known limitations

- **Water temperature isn't measured continuously.** The Balboa hardware
  only reads it when water is actually flowing through the heater - i.e.
  while a pump is circulating (heating, filtering, or otherwise running).
  Between those cycles, `sensor.spa_miser_water_temperature` (and whatever
  upstream sensor it's reading from) simply holds its last value; it isn't
  a live continuous feed. Checked against ~3 days of one real tub's actual
  history: readings cluster into ~80 distinct circulation bursts, with the
  gap between bursts typically 20-30 minutes or ~2 hours (a regular
  filtration cycle is visible in the data), occasionally stretching to
  ~5 hours during quieter periods. So in the common case this is a minor
  effect, but not always negligible. Two concrete effects on spa-miser's
  own design:
  - `history.py`'s hourly aggregation forward-fills the last known reading
    across hours with no new data point, on the basis that "no update"
    usually just means "hasn't changed" for a slow-moving sensor (see the
    comments there) - this happens to be the *structurally correct*
    interpretation given the above, not merely a reasonable approximation:
    between pump cycles there genuinely is no new information to have
    missed.
  - Each time the heating strategy recomputes, it anchors its plan to
    whatever `current_temp_c` reads *at that moment* - which, per the
    above, is usually fresh within the last half hour or so, but can
    occasionally be a couple of hours old. The one-slot heating "grace" in
    `strategy.py` (see its comments) - originally motivated by the water
    temperature and the model's belief being able to lag each other -
    turns out to be doing double duty here: heating requires pump
    circulation, which is *also* the only time a fresh, trustworthy
    reading becomes available at all. So that window is both the real
    system's chance to thermally catch up to the model, and the
    only chance to confirm (or correct) the model's belief with real data.

- **A single bad forecast data point can distort one slot's plan.**
  `weather.get_forecasts` responses aren't otherwise validated - a
  Fahrenheit-scaled value has been observed slipping through unconverted
  from the upstream weather integration (~78°C for what was actually a
  normal ~22°C September afternoon), which fed straight into the thermal
  model and showed up as a brief, physically-impossible uptick in the
  "Expected temperature" chart series during a coast period. Forecast
  points outside a plausible range (`MIN_PLAUSIBLE_AMBIENT_TEMP_C`/
  `MAX_PLAUSIBLE_AMBIENT_TEMP_C` in `const.py`, currently -20°C to 45°C)
  are now dropped with a logged warning instead of being used. Every
  strategy recompute also logs its full inputs (current temp, comfort
  bounds, fitted model coefficients, heater power estimate, and every
  forecast point used) at `info` level unconditionally - recomputes only
  happen a handful of times a day, so this is a few KB/day at most, nowhere
  near a disk-space concern.

- **`actual_kwh_today` and `cost_saved_today` measure the whole spa, not
  just heating.** The configured `energy_entity`/`power_entity` are
  typically a single whole-circuit meter (e.g. a Shelly EM clamp on the
  spa's incoming supply) rather than a heater-isolated one, so they also
  capture the circulation pump's filtration cycles and baseline standby
  draw. Measured directly against three representative days of one real
  tub's history (energy-integrated against actual `heating_state`
  transitions, not just sampled): **circulation was ~25% of total
  measured energy, standby ~1.5%, heating the remaining ~73%** - so these
  two sensors should be read as "total spa energy," not "heating energy."
  Deliberately not corrected in software: the natural fix (a flat
  efficiency-style discount) would only be honest applied to these two
  purely-informational sensors - applying it anywhere upstream (the
  thermal model fit, or `heater_power_kw`) would either do nothing
  (uniformly rescaling a regression's input is absorbed by the fitted
  coefficient) or actively corrupt predictions (if the correction isn't
  applied identically at both fit-time and decision-time). The DP's own
  `heater_power_kw` estimate isn't meaningfully affected by this at all -
  it already isolates real heater draw via a 90th-percentile-of-hourly-*max*
  estimate, and the heater's peak power (~3.1kW, measured) dominates the
  pump's (~0.4kW) by roughly 8x.

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
