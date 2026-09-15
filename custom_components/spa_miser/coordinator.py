"""Coordinator: pulls forecasts, refits the thermal model, and decides/acts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import history
from .const import (
    CONF_AWAY_MODE,
    CONF_CLIMATE_ENTITY,
    CONF_ENABLED,
    CONF_ENERGY_ENTITY,
    CONF_HEATING_STATE_ENTITY,
    CONF_MANUAL_CHEAP_HOURS,
    CONF_MANUAL_CHEAP_RATE,
    CONF_MANUAL_OVERRIDE_MINUTES,
    CONF_MANUAL_STANDARD_RATE,
    CONF_MAX_COMFORT_TEMP,
    CONF_MAX_PRICE,
    CONF_MIN_AWAY_TEMP,
    CONF_MIN_COMFORT_TEMP,
    CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY,
    CONF_OCTOPUS_REGION,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_BACKFILL_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_SENSOR,
    COORDINATOR_UPDATE_INTERVAL_MINUTES,
    DECISION_LOOKAHEAD_HOURS,
    DEFAULT_AWAY_MODE,
    DEFAULT_ENABLED,
    DEFAULT_WEATHER_BACKFILL_ENABLED,
    DEFAULT_MANUAL_CHEAP_HOURS,
    DEFAULT_MANUAL_CHEAP_RATE,
    DEFAULT_MANUAL_OVERRIDE_MINUTES,
    DEFAULT_MANUAL_STANDARD_RATE,
    DEFAULT_MAX_COMFORT_TEMP,
    DEFAULT_MAX_PRICE,
    DEFAULT_MIN_AWAY_TEMP,
    DEFAULT_MIN_COMFORT_TEMP,
    DEFAULT_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
    DOMAIN,
    HEATING_STATE_ACTIVE_VALUES,
    MAX_PLAUSIBLE_AMBIENT_TEMP_C,
    MIN_PLAUSIBLE_AMBIENT_TEMP_C,
    MODEL_FIT_LOOKBACK_DAYS,
    MODEL_REFIT_INTERVAL_HOURS,
    PRESET_HIGH_RANGE,
    PRESET_LOW_RANGE,
    PRICE_SOURCE_OCTOPUS_AGILE,
    PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC,
)
from .decision_engine import Decision, ForecastPoint, decide
from .price_sources import PriceSlot, PriceSource
from .price_sources.manual import ManualPriceSource
from .price_sources.octopus_agile import OctopusAgilePriceSource
from .price_sources.octopus_agile_public import OctopusAgilePublicPriceSource
from .spa_control import UNAVAILABLE_STATES, SpaControl
from .strategy import DailyStrategy, compute_strategy, deserialize_strategy, serialize_strategy
from .thermal_model import ThermalModelParams
from .thermal_model import fit as fit_model
from .thermal_model import predict_trajectory

# A new plan is only worth recomputing when the price forecast has grown by
# meaningfully more than this - avoids re-triggering on tiny/noise coverage
# differences between ticks, while still reliably catching "tomorrow's
# Agile rates just published" (which extends coverage by ~24h).
STRATEGY_RECOMPUTE_COVERAGE_MARGIN_HOURS = 1
# Bump if the persisted shape (see strategy.serialize_strategy) ever
# changes incompatibly - old stores are simply discarded, not migrated,
# since a fresh strategy just gets recomputed on the next cycle anyway.
STRATEGY_STORAGE_VERSION = 1
# How far back a recompute preserves the previous plan's already-elapsed
# slots (see _async_recompute_strategy) - comfortably more than any
# reasonable dashboard graph_span, so the chart's "history" never gets
# wiped by a recompute, while still bounding how far this can grow across
# many recomputes rather than accumulating forever.
STRATEGY_HISTORY_RETENTION_HOURS = 48

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpaMiserData:
    model: ThermalModelParams | None = None
    last_fit: datetime | None = None
    model_temperature_c: float | None = None
    current_temperature_c: float | None = None
    decision: Decision | None = None
    predicted_kwh_today: float | None = None
    actual_kwh_today: float | None = None
    cost_saved_today: float | None = None
    away_mode: bool = False
    enabled: bool = False
    max_comfort_c: float = DEFAULT_MAX_COMFORT_TEMP
    min_comfort_c: float = DEFAULT_MIN_COMFORT_TEMP
    min_away_c: float = DEFAULT_MIN_AWAY_TEMP
    max_price: float = DEFAULT_MAX_PRICE
    # Which hardware preset is actually in force right now, and why - see
    # coordinator._compute_active_range. Populated every tick regardless
    # of whether control is actually being applied (disabled/paused/
    # unavailable), so sensor.spa_miser_active_range stays accurate even
    # then.
    active_preset: str = PRESET_HIGH_RANGE
    range_reason: str = "normal"
    # Independent of the thermal model/decision (which both need ~24h of
    # history before producing anything) - lets a price source be verified
    # as actually wired up and returning real data immediately.
    current_price: float | None = None
    price_slots_count: int = 0
    # The raw forecast as received from the price source - independent of
    # whether a daily strategy has been computed from it, so a price chart
    # can show the full known-ahead curve even before/without a strategy
    # (e.g. the thermal model hasn't fit yet, or a fit failed).
    price_slots: list[PriceSlot] = field(default_factory=list)
    # Raw current readings of the configured input entities, so what
    # spa-miser is actually seeing can be checked at a glance rather than
    # cross-referencing sensor.spa_miser_configured_sources' entity_ids
    # against those entities' own states elsewhere in HA.
    heating_state: str | None = None
    outdoor_temperature_c: float | None = None
    # Answers "why isn't it doing anything right now" without digging
    # through logs - e.g. a manual-override pause (see SpaControl) is
    # invisible from every other entity, since it deliberately looks
    # identical to "nothing to do right now".
    control_status: str = "disabled"
    control_override_until: datetime | None = None


class SpaMiserCoordinator(DataUpdateCoordinator[SpaMiserData]):
    """Ties price/weather forecasts, the thermal model, and spa control together."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="spa_miser",
            update_interval=timedelta(minutes=COORDINATOR_UPDATE_INTERVAL_MINUTES),
        )
        self.entry = entry
        self.spa_control = SpaControl(
            hass,
            entry.data[CONF_CLIMATE_ENTITY],
            entry.data.get(
                CONF_MANUAL_OVERRIDE_MINUTES, DEFAULT_MANUAL_OVERRIDE_MINUTES
            ),
        )
        self.price_source: PriceSource = self._build_price_source()

        self._model: ThermalModelParams | None = None
        self._last_fit: datetime | None = None
        self._strategy: DailyStrategy | None = None
        # The real current_temp_c a fresh plan's first slot actually
        # simulated forward from - see strategy_anchor_temp_c. Deliberately
        # NOT part of DailyStrategy/serialize_strategy: it's a display-only
        # annotation for the chart, and keeping it out of the Store-
        # persisted shape means it can't ever risk that format's backward
        # compatibility across restarts (see slots_for_display for the same
        # reasoning applied to the slots attribute). It just starts back at
        # None after a restart until the next real recompute repopulates it
        # - a harmless, temporary gap in one chart annotation, not a bug.
        self._strategy_anchor_temp_c: float | None = None
        self._heater_power_kw: float = history.DEFAULT_HEATER_POWER_KW
        # Persists the committed plan across restarts - see async_setup
        # (restore) and _async_recompute_strategy (save). Keyed per entry
        # so multiple spa-miser instances wouldn't collide.
        self._strategy_store: Store = Store(
            hass, STRATEGY_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_daily_strategy"
        )
        self._strategy_recompute_interval_hours: float = entry.data.get(
            CONF_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
            DEFAULT_STRATEGY_RECOMPUTE_INTERVAL_HOURS,
        )

        self._enabled: bool = entry.options.get(CONF_ENABLED, DEFAULT_ENABLED)
        self._away_mode: bool = entry.options.get(CONF_AWAY_MODE, DEFAULT_AWAY_MODE)
        self._weather_backfill_enabled: bool = entry.options.get(
            CONF_WEATHER_BACKFILL_ENABLED, DEFAULT_WEATHER_BACKFILL_ENABLED
        )
        # Comfort temps are set once in entry.data by the config flow, then
        # potentially overridden live via the number entities into
        # entry.options (see _persist_option). Options - the more recent of
        # the two - must win, falling back to the setup-time value, falling
        # back to the hardcoded default only if neither is present.
        self._max_comfort_c: float = entry.options.get(
            CONF_MAX_COMFORT_TEMP,
            entry.data.get(CONF_MAX_COMFORT_TEMP, DEFAULT_MAX_COMFORT_TEMP),
        )
        self._min_comfort_c: float = entry.options.get(
            CONF_MIN_COMFORT_TEMP,
            entry.data.get(CONF_MIN_COMFORT_TEMP, DEFAULT_MIN_COMFORT_TEMP),
        )
        self._min_away_c: float = entry.options.get(
            CONF_MIN_AWAY_TEMP,
            entry.data.get(CONF_MIN_AWAY_TEMP, DEFAULT_MIN_AWAY_TEMP),
        )
        # Options-only, no config-flow step - see CONF_MAX_PRICE in const.py.
        self._max_price: float = entry.options.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE)

        self._energy_day: date | None = None
        self._energy_baseline_kwh: float | None = None
        self._unsub_input_ready = None
        self._unsub_slot_boundary = None

    async def async_setup(self) -> None:
        self.spa_control.async_setup()
        input_entities = [
            entity_id
            for entity_id in (
                self.entry.data.get(CONF_WATER_TEMP_SENSOR),
                self.entry.data.get(CONF_CLIMATE_ENTITY),
                self.entry.data.get(CONF_HEATING_STATE_ENTITY),
                self.entry.data.get(CONF_OUTDOOR_TEMP_SENSOR),
            )
            if entity_id
        ]
        self._unsub_input_ready = async_track_state_change_event(
            self.hass, input_entities, self._handle_input_ready
        )
        # Price/strategy slots sit on a wall-clock 30-minute grid (:00/:30),
        # but update_interval below is a rolling timer with no fixed relation
        # to wall-clock time - left to its own devices, the setpoint for a
        # new slot could apply anywhere from immediately to nearly 30 minutes
        # into that slot, undermining the whole point of planning around
        # half-hourly prices. This fires a refresh right at each boundary so
        # _decide()/_async_apply_control pick up the new slot within seconds,
        # not whenever the rolling timer's phase happens to next land.
        self._unsub_slot_boundary = async_track_time_change(
            self.hass, self._handle_slot_boundary, minute=[0, 30], second=0
        )
        await self._async_load_persisted_strategy()

    async def _handle_slot_boundary(self, now: datetime) -> None:
        await self.async_request_refresh()

    async def _async_load_persisted_strategy(self) -> None:
        """Restore the committed plan across a restart, before the first
        refresh runs - see strategy.serialize_strategy/deserialize_strategy
        and _async_recompute_strategy (which keeps the store up to date).
        """
        data = await self._strategy_store.async_load()
        if data is None:
            return
        strategy = deserialize_strategy(data)
        if strategy is not None:
            self._strategy = strategy
            _LOGGER.info(
                "Restored persisted daily strategy (%d slots, computed at %s)",
                len(strategy.slots),
                strategy.computed_at,
            )

    def async_unload(self) -> None:
        self.spa_control.async_unload()
        if self._unsub_input_ready is not None:
            self._unsub_input_ready()
            self._unsub_input_ready = None
        if self._unsub_slot_boundary is not None:
            self._unsub_slot_boundary()
            self._unsub_slot_boundary = None

    async def _handle_input_ready(self, event: Event[EventStateChangedData]) -> None:
        """A configured input entity just reported new data.

        Two triggers share this handler:

        - Recovery right after restart: the coordinator's first refresh
          runs immediately at setup, which can easily race ahead of
          MQTT-based entities reconnecting (they can take anywhere from
          seconds to a couple of minutes). Without this, that unlucky
          first snapshot - sensors reading unknown/unavailable - would
          otherwise persist until the next scheduled refresh, up to
          COORDINATOR_UPDATE_INTERVAL_MINUTES (30) later, despite the
          underlying data actually being ready almost immediately.
        - An ordinary value change while already available (real heating
          starting, a fresh temperature reading arriving from the next
          pump-circulation cycle). Without reacting to this too, the
          mirrored sensors this integration exposes (water_temperature,
          heating_state, ...) only ever reflect data as fresh as the last
          scheduled tick - up to 30 minutes stale relative to the real
          controller, since DataUpdateCoordinator's update_interval is a
          rolling timer with no fixed relation to wall-clock time.

        Skips a new state that is itself unavailable: that's a source
        dropping out, not new data, and refreshing into it would just
        replace a good snapshot with a worse one before the coordinator's
        own staleness handling has a chance to matter.
        """
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in UNAVAILABLE_STATES:
            return
        was_unavailable = old_state is None or old_state.state in UNAVAILABLE_STATES
        value_changed = old_state is not None and old_state.state != new_state.state
        if was_unavailable or value_changed:
            await self.async_request_refresh()

    # --- public control surface used by switch.py / number.py -------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def away_mode(self) -> bool:
        return self._away_mode

    @property
    def max_comfort_c(self) -> float:
        return self._max_comfort_c

    @property
    def min_comfort_c(self) -> float:
        return self._min_comfort_c

    @property
    def min_away_c(self) -> float:
        return self._min_away_c

    @property
    def max_price(self) -> float:
        return self._max_price

    @property
    def strategy(self) -> DailyStrategy | None:
        """The current committed daily plan, if one has been computed yet.

        Read directly by sensor.py (not routed through SpaMiserData) - same
        pattern as entry.data for configured_sources: it changes far less
        often than every coordinator tick, so there's no need to copy it
        into the per-tick snapshot.
        """
        return self._strategy

    @property
    def strategy_anchor_temp_c(self) -> float | None:
        """The real current_temp_c the current plan's first fresh slot was
        actually simulated forward from - None until a real recompute has
        happened (e.g. right after a restart). Exists purely so the chart
        can plot the exact moment/value a recompute corrected the model's
        belief, instead of that correction only becoming visible up to 30
        minutes later at the next slot boundary."""
        return self._strategy_anchor_temp_c

    async def async_set_enabled(self, value: bool) -> None:
        self._enabled = value
        self._persist_option(CONF_ENABLED, value)
        await self.async_request_refresh()

    async def async_set_away_mode(self, value: bool) -> None:
        self._away_mode = value
        self._persist_option(CONF_AWAY_MODE, value)
        await self.async_request_refresh()

    async def async_set_max_comfort_c(self, value: float) -> None:
        self._max_comfort_c = value
        self._persist_option(CONF_MAX_COMFORT_TEMP, value)
        await self.async_request_refresh()

    async def async_set_min_comfort_c(self, value: float) -> None:
        self._min_comfort_c = value
        self._persist_option(CONF_MIN_COMFORT_TEMP, value)
        await self.async_request_refresh()

    async def async_set_min_away_c(self, value: float) -> None:
        self._min_away_c = value
        self._persist_option(CONF_MIN_AWAY_TEMP, value)
        await self.async_request_refresh()

    async def async_set_max_price(self, value: float) -> None:
        self._max_price = value
        self._persist_option(CONF_MAX_PRICE, value)
        await self.async_request_refresh()

    async def async_trigger_initial_estimate(self) -> None:
        """Bootstrap a first model fit right now using Open-Meteo backfill.

        Called from button.spa_miser_estimate_initial_model. Enables
        weather backfill (persisted - future automatic refits get to use it
        too, not just this one call) and immediately refits, rather than
        waiting for the normal refit cadence to happen to pick it up.
        """
        self._weather_backfill_enabled = True
        self._persist_option(CONF_WEATHER_BACKFILL_ENABLED, True)
        await self._async_refit_model()
        await self.async_request_refresh()

    def _persist_option(self, key: str, value) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )

    # --- price source --------------------------------------------------

    def _build_price_source(self) -> PriceSource:
        price_source = self.entry.data[CONF_PRICE_SOURCE]
        if price_source == PRICE_SOURCE_OCTOPUS_AGILE:
            return OctopusAgilePriceSource(
                self.entry.data[CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY]
            )
        if price_source == PRICE_SOURCE_OCTOPUS_AGILE_PUBLIC:
            return OctopusAgilePublicPriceSource(self.entry.data[CONF_OCTOPUS_REGION])
        return ManualPriceSource(
            self.entry.data.get(CONF_MANUAL_CHEAP_HOURS, DEFAULT_MANUAL_CHEAP_HOURS),
            self.entry.data.get(CONF_MANUAL_CHEAP_RATE, DEFAULT_MANUAL_CHEAP_RATE),
            self.entry.data.get(
                CONF_MANUAL_STANDARD_RATE, DEFAULT_MANUAL_STANDARD_RATE
            ),
        )

    # --- main update loop -----------------------------------------------

    async def _async_update_data(self) -> SpaMiserData:
        if self._should_refit():
            await self._async_refit_model()

        # This is only as fresh as the last time water actually flowed
        # through the heater (the hardware can't read temperature
        # otherwise) - usually well under an hour given real-world
        # circulation cycles, occasionally a couple of hours - see "Known
        # limitations" in the README. The daily strategy anchors its whole
        # plan to whatever this reads at recompute time regardless.
        current_temp = self._read_state_float(self.entry.data[CONF_WATER_TEMP_SENSOR])
        price_slots = await self.price_source.async_get_forecast(self.hass)
        forecast = await self._async_get_weather_forecast()
        current_price = self._read_current_price(price_slots)
        active_preset, range_reason = self._compute_active_range(current_price)

        floor = self._min_away_c if self._away_mode else self._min_comfort_c
        ceiling = self._max_comfort_c

        if self._should_recompute_strategy(price_slots):
            await self._async_recompute_strategy(current_temp, floor, ceiling, forecast, price_slots)

        decision = self._decide(current_temp, floor, ceiling, forecast, price_slots)

        # Range/safety-floor enforcement runs whenever spa-miser is allowed
        # to act at all - deliberately not gated on `decision is not None`,
        # so away mode / the price cap still hold a safe floor even before
        # the thermal model has ever produced a decision (fresh install,
        # stale/unfittable model). Only the comfort ceiling-vs-floor choice
        # within High Range actually needs a real decision - see
        # _async_apply_control.
        if (
            self._enabled
            and self.spa_control.is_available
            and not self.spa_control.is_manually_overridden
        ):
            await self._async_apply_control(decision, active_preset)

        model_temp = self._predict_model_temperature(
            current_temp, forecast, decision, active_preset
        )
        predicted_kwh = self._estimate_predicted_kwh_today(forecast)
        actual_kwh = self._read_actual_kwh_today()
        cost_saved = self._estimate_cost_saved_today(price_slots, actual_kwh, ceiling)
        heating_state = self._read_state_string(self.entry.data.get(CONF_HEATING_STATE_ENTITY))
        outdoor_temp = self._read_state_float(self.entry.data.get(CONF_OUTDOOR_TEMP_SENSOR))

        return SpaMiserData(
            model=self._model,
            last_fit=self._last_fit,
            model_temperature_c=model_temp,
            current_temperature_c=current_temp,
            heating_state=heating_state,
            outdoor_temperature_c=outdoor_temp,
            decision=decision,
            predicted_kwh_today=predicted_kwh,
            actual_kwh_today=actual_kwh,
            cost_saved_today=cost_saved,
            away_mode=self._away_mode,
            enabled=self._enabled,
            max_comfort_c=ceiling,
            min_comfort_c=self._min_comfort_c,
            min_away_c=self._min_away_c,
            max_price=self._max_price,
            active_preset=active_preset,
            range_reason=range_reason,
            current_price=current_price,
            price_slots_count=len(price_slots),
            price_slots=price_slots,
            control_status=self._compute_control_status(decision),
            control_override_until=self.spa_control.override_until,
        )

    def _compute_control_status(self, decision: Decision | None) -> str:
        """Mirrors the gating in _async_update_data's apply-decision check,
        so this always explains exactly why that check did or didn't fire -
        in particular, a manual-override pause is otherwise invisible from
        every other entity, since it deliberately looks identical to
        "nothing to do right now".
        """
        if not self._enabled:
            return "disabled"
        if self.spa_control.is_manually_overridden:
            return "paused_manual_override"
        if not self.spa_control.is_available:
            return "unavailable"
        if decision is None:
            return "no_decision"
        return "active"

    # --- spa actuation ----------------------------------------------------

    def _compute_active_range(self, current_price: float | None) -> tuple[str, str]:
        """Which hardware preset should be in force right now, and why.

        Deliberately stateless and independent of the DP/decision_engine -
        the optimizer keeps computing a High Range setpoint exactly as
        before; this is a simple override applied only at actuation time,
        so a sustained expensive spell lets comfort lapse without ever
        leaving the spa's onboard thermostat undefended. Away mode takes
        precedence over the price cap for display purposes when both
        apply - it's a deliberate, already-visible user choice, whereas
        the price cap firing is the more surprising case worth calling out.
        """
        if self._away_mode:
            return PRESET_LOW_RANGE, "away_mode"
        if current_price is not None and current_price > self._max_price:
            return PRESET_LOW_RANGE, "price_cap"
        return PRESET_HIGH_RANGE, "normal"

    async def _async_apply_control(
        self, decision: Decision | None, active_preset: str
    ) -> None:
        if active_preset == PRESET_LOW_RANGE:
            target_temp = self._min_away_c
        else:
            heat_to_ceiling = decision is not None and decision.heat_recommended
            target_temp = self._max_comfort_c if heat_to_ceiling else self._min_comfort_c

        if self.spa_control.preset_mode != active_preset:
            await self.spa_control.async_set_preset(active_preset)
        current_target = self._read_state_float(
            self.entry.data[CONF_CLIMATE_ENTITY], attribute="temperature"
        )
        if current_target is None or abs(current_target - target_temp) > 0.25:
            await self.spa_control.async_set_target_temperature(target_temp)

        # hvac_mode is always "heat", never "off" - the spa's own onboard
        # thermostat then keeps actively defending whichever target above
        # is currently in force (comfort ceiling/floor, or the away/price-
        # cap safety value) even if HA or spa-miser itself stops
        # responding. Previously this toggled hvac_mode off during normal
        # coast periods, which left the heater with no active target at
        # all if HA died mid-coast.
        if self.spa_control.hvac_mode != "heat":
            await self.spa_control.async_set_heat_enabled(True)

    # --- daily strategy --------------------------------------------------

    def _decide(
        self,
        current_temp: float | None,
        floor: float,
        ceiling: float,
        forecast: list[ForecastPoint],
        price_slots: list[PriceSlot],
    ) -> Decision | None:
        """What to do right now: the committed plan's slot if one covers
        "now", falling back to the greedy per-tick heuristic (decide()) when
        no plan is available yet - e.g. before the first successful model
        fit, or before enough price/weather data exists to compute one."""
        if self._strategy is not None:
            slot = self._strategy.slot_at(dt_util.utcnow())
            if slot is not None:
                action = "heat" if slot.heat_on else "coast"
                return Decision(
                    heat_recommended=slot.heat_on,
                    reason=f"heating strategy: {action} (plan targets {slot.planned_temp_c:.1f}°C)",
                    floor_breach_hours=None,
                    cheap_price_threshold=None,
                    current_price=slot.price,
                )
        if self._model is not None and current_temp is not None and forecast:
            return decide(
                now=dt_util.utcnow(),
                current_temp_c=current_temp,
                floor_c=floor,
                ceiling_c=ceiling,
                forecast=forecast,
                price_slots=price_slots,
                model=self._model,
            )
        return None

    def _should_recompute_strategy(self, price_slots: list[PriceSlot]) -> bool:
        if self._model is None or not price_slots:
            return False
        if self._strategy is None:
            return True
        now = dt_util.utcnow()
        strategy_end = self._strategy.end
        if strategy_end is None or now >= strategy_end:
            return True  # the committed plan no longer covers "now"
        # A plan's ambient/wind assumptions are only as good as the forecast
        # available when it was computed - the "new price data" trigger
        # below alone can leave that stale for up to a full day (observed
        # live: up to ~0.5C of overnight drift from forecast error alone)
        # before the next day's rates force a refresh. This bounds the
        # worst case independent of price-source publishing cadence.
        recompute_interval = timedelta(hours=self._strategy_recompute_interval_hours)
        if now - self._strategy.computed_at >= recompute_interval:
            return True
        latest_price_end = max(s.end for s in price_slots)
        margin = timedelta(hours=STRATEGY_RECOMPUTE_COVERAGE_MARGIN_HOURS)
        return latest_price_end > strategy_end + margin

    async def _async_recompute_strategy(
        self,
        current_temp: float | None,
        floor: float,
        ceiling: float,
        forecast: list[ForecastPoint],
        price_slots: list[PriceSlot],
    ) -> None:
        if current_temp is None or not forecast or self._model is None:
            return
        # Refit right before committing to a new plan, in addition to the
        # normal 24h timer, so each day's plan uses the freshest model.
        await self._async_refit_model()
        if self._model is None:
            return

        now = dt_util.utcnow()
        self._heater_power_kw = await history.async_estimate_heater_power_kw(
            self.hass,
            power_entity=self.entry.data[CONF_POWER_ENTITY],
            start=now - timedelta(days=MODEL_FIT_LOOKBACK_DAYS),
            end=now,
        )

        # Full snapshot of every input compute_strategy is about to see -
        # logged unconditionally (not gated behind debug) so a bad input
        # (e.g. a corrupt forecast point) is diagnosable after the fact
        # without having needed to pre-emptively raise log verbosity before
        # it happened. Cheap to keep: recomputes only happen a handful of
        # times a day (see _should_recompute_strategy), so this adds at most
        # a few KB/day to the log - see the README for why that's not a
        # disk-space concern.
        _LOGGER.info(
            "Recomputing strategy: current_temp=%.2f floor=%.2f ceiling=%.2f "
            "model(loss=%.4f wind=%.6f input=%.4f thermal_mass=%s r2=%.3f n=%d) "
            "heater_power_kw=%.3f price_slots=%d[%s..%s] forecast=%d points[%s]",
            current_temp,
            floor,
            ceiling,
            self._model.loss_coefficient,
            self._model.wind_coefficient,
            self._model.input_coefficient,
            self._model.thermal_mass_kwh_per_c,
            self._model.r_squared,
            self._model.n_samples,
            self._heater_power_kw,
            len(price_slots),
            price_slots[0].start.isoformat() if price_slots else "-",
            price_slots[-1].end.isoformat() if price_slots else "-",
            len(forecast),
            ", ".join(
                f"{p.at.strftime('%H:%M')}={p.ambient_temp_c:.1f}C/{p.wind_speed_ms:.1f}m/s"
                for p in forecast
            ),
        )

        strategy = compute_strategy(
            now=now,
            current_temp_c=current_temp,
            floor_c=floor,
            ceiling_c=ceiling,
            forecast=forecast,
            price_slots=price_slots,
            model=self._model,
            heater_power_kw=self._heater_power_kw,
        )
        if strategy is None:
            _LOGGER.warning("Could not compute a daily strategy (insufficient forecast data)")
            return

        # compute_strategy only ever returns slots from `now` forward (see
        # its own s.end > now filter) - a fresh plan on its own would wipe
        # out the previous plan's already-elapsed slots, discarding exactly
        # the portion of the "Expected temperature" chart series that looks
        # like history. Preserve it by carrying forward the old plan's past
        # slots rather than just replacing them outright - capped to how
        # far back any reasonable dashboard would look, so this can't grow
        # unbounded across many recomputes.
        retention_cutoff = now - timedelta(hours=STRATEGY_HISTORY_RETENTION_HOURS)
        preserved_past_slots = (
            [s for s in self._strategy.slots if retention_cutoff < s.end <= now]
            if self._strategy is not None
            else []
        )
        strategy = DailyStrategy(
            computed_at=strategy.computed_at,
            slots=preserved_past_slots + strategy.slots,
        )

        self._strategy = strategy
        self._strategy_anchor_temp_c = current_temp
        await self._strategy_store.async_save(serialize_strategy(strategy))
        _LOGGER.info(
            "Computed new daily strategy: %d slots (%d preserved history), %d heat-on, heater_power_kw=%.2f",
            len(strategy.slots),
            len(preserved_past_slots),
            sum(1 for s in strategy.slots if s.heat_on),
            self._heater_power_kw,
        )

    # --- model fitting ------------------------------------------------

    def _should_refit(self) -> bool:
        # Until a model has fit successfully even once, retry every cycle
        # rather than waiting out the normal 24h cadence - _last_fit is set
        # unconditionally on every attempt (see _async_refit_model), so
        # without this a single early failure (not enough history yet, which
        # is the normal case right after setup) would block the next retry
        # for a full day even once enough data existed well before that.
        if self._model is None:
            return True
        if self._last_fit is None:
            return True
        return dt_util.utcnow() - self._last_fit >= timedelta(
            hours=MODEL_REFIT_INTERVAL_HOURS
        )

    async def _async_refit_model(self) -> None:
        self._last_fit = dt_util.utcnow()
        end = dt_util.utcnow()
        start = end - timedelta(days=MODEL_FIT_LOOKBACK_DAYS)
        samples = await history.async_build_hourly_samples(
            self.hass,
            water_temp_entity=self.entry.data[CONF_WATER_TEMP_SENSOR],
            outdoor_temp_entity=self.entry.data.get(CONF_OUTDOOR_TEMP_SENSOR),
            wind_speed_entity=self.entry.data.get(CONF_WIND_SPEED_SENSOR),
            power_entity=self.entry.data[CONF_POWER_ENTITY],
            start=start,
            end=end,
            allow_weather_backfill=self._weather_backfill_enabled,
        )
        fitted = fit_model(samples)
        if fitted is None:
            _LOGGER.warning(
                "Not enough recorder history yet to fit the thermal model "
                "(%d usable hourly samples, outdoor_temp_sensor configured: %s)",
                len(samples),
                self.entry.data.get(CONF_OUTDOOR_TEMP_SENSOR) is not None,
            )
            return
        self._model = fitted
        _LOGGER.info(
            "Refit thermal model: loss=%.4f wind=%.4f input=%.4f thermal_mass=%s "
            "r2=%.3f n=%d",
            fitted.loss_coefficient,
            fitted.wind_coefficient,
            fitted.input_coefficient,
            fitted.thermal_mass_kwh_per_c,
            fitted.r_squared,
            fitted.n_samples,
        )

    # --- weather -----------------------------------------------------

    async def _async_get_weather_forecast(self) -> list[ForecastPoint]:
        weather_entity = self.entry.data[CONF_WEATHER_ENTITY]
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception:  # noqa: BLE001 - forecast failures shouldn't crash the coordinator
            _LOGGER.exception("Failed to fetch weather forecast from %s", weather_entity)
            return []

        raw = (response or {}).get(weather_entity, {}).get("forecast", [])
        points: list[ForecastPoint] = []
        for item in raw[:DECISION_LOOKAHEAD_HOURS]:
            at = dt_util.parse_datetime(item.get("datetime", ""))
            temp = item.get("temperature")
            wind = item.get("wind_speed", 0.0)
            if at is None or temp is None:
                continue
            temp = float(temp)
            # A single bad upstream data point (observed in practice: a
            # Fahrenheit-scaled value slipping through unconverted, giving
            # ~78C) feeds straight into predict_trajectory with no other
            # validation - drop it rather than let it silently distort the
            # simulated trajectory for whichever slot it lands on.
            if not (MIN_PLAUSIBLE_AMBIENT_TEMP_C <= temp <= MAX_PLAUSIBLE_AMBIENT_TEMP_C):
                _LOGGER.warning(
                    "Ignoring implausible forecast point from %s at %s: "
                    "temperature=%.1f (outside plausible range %.0f..%.0f)",
                    weather_entity,
                    at,
                    temp,
                    MIN_PLAUSIBLE_AMBIENT_TEMP_C,
                    MAX_PLAUSIBLE_AMBIENT_TEMP_C,
                )
                continue
            points.append(
                ForecastPoint(at=at, ambient_temp_c=temp, wind_speed_ms=float(wind or 0.0))
            )
        return points

    # --- reporting helpers ---------------------------------------------

    def _read_state_float(
        self, entity_id: str | None, attribute: str | None = None
    ) -> float | None:
        # entity_id is None whenever an optional field (e.g. outdoor temp
        # sensor) isn't configured - hass.states.get(None) isn't just "not
        # found", it raises (it calls entity_id.lower() internally), so this
        # must short-circuit before reaching it.
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        value = state.attributes.get(attribute) if attribute else state.state
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _read_state_string(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state

    def _predict_model_temperature(
        self,
        current_temp: float | None,
        forecast: list[ForecastPoint],
        decision: Decision | None,
        active_preset: str,
    ) -> float | None:
        """One-tick-ahead prediction, for sensor.spa_miser_model_temperature.

        Deliberately derived from the coordinator's own current intent
        (decision/active_preset) rather than the live spa_control.hvac_mode:
        that used to double as a reasonable "is the heater actually
        drawing power" proxy back when coasting genuinely toggled hvac_mode
        to off, but since coasting now only changes the *setpoint* (see
        _async_apply_control), hvac_mode is essentially always "heat" -
        and a manual-override pause can freeze it at a value from before
        the pause that no longer means anything, e.g. a pump-speed change
        incidentally flipping the gateway's reported hvac_mode. Basing
        this on intent instead means it reflects what spa-miser's own
        logic currently calls for even while shadow mode or an override
        means nothing is actually being applied - consistent with how
        sensor.spa_miser_daily_strategy already behaves.
        """
        if self._model is None or current_temp is None or not forecast:
            return None
        if active_preset == PRESET_LOW_RANGE:
            heat_on = current_temp < self._min_away_c
        else:
            heat_on = decision is not None and decision.heat_recommended
        power_kw = (
            0.0
            if not heat_on or self._model.input_coefficient <= 0
            else self._heater_power_kw
        )
        trajectory = predict_trajectory(
            self._model,
            current_temp,
            [forecast[0].ambient_temp_c],
            [forecast[0].wind_speed_ms],
            [power_kw],
            dt_hours=COORDINATOR_UPDATE_INTERVAL_MINUTES / 60,
        )
        return trajectory[0] if trajectory else None

    def _estimate_predicted_kwh_today(self, forecast: list[ForecastPoint]) -> float | None:
        """Energy needed for the rest of today.

        When a daily strategy exists, this sums the plan's actual remaining
        heat-on slots for today - precise, since it's the same schedule the
        coordinator is following. Otherwise falls back to a rough heuristic
        (energy to replace heat lost while held near mid-band) for the
        window before the first plan is ever computed.
        """
        if self._strategy is not None:
            now = dt_util.utcnow()
            today_local = dt_util.now().date()
            total_kwh = 0.0
            for slot in self._strategy.slots:
                if not slot.heat_on or slot.end <= now:
                    continue
                if dt_util.as_local(slot.start).date() != today_local:
                    continue
                dt_hours = (slot.end - slot.start).total_seconds() / 3600.0
                total_kwh += self._heater_power_kw * dt_hours
            return total_kwh

        if self._model is None or not forecast or self._model.input_coefficient <= 0:
            return None
        now_local = dt_util.now()
        remaining_hours = 24 - now_local.hour - now_local.minute / 60
        representative_temp = (self._max_comfort_c + self._min_comfort_c) / 2

        total_kwh = 0.0
        hours_covered = 0.0
        for point in forecast:
            if hours_covered >= remaining_hours:
                break
            loss_rate = (
                self._model.loss_coefficient
                + self._model.wind_coefficient * point.wind_speed_ms
            ) * (representative_temp - point.ambient_temp_c)
            power_kw = max(0.0, loss_rate) / self._model.input_coefficient
            total_kwh += power_kw * 1.0
            hours_covered += 1.0
        return total_kwh

    def _read_actual_kwh_today(self) -> float | None:
        energy_entity = self.entry.data.get(CONF_ENERGY_ENTITY)
        if not energy_entity:
            return None
        current = self._read_state_float(energy_entity)
        if current is None:
            return None

        today = dt_util.now().date()
        if self._energy_day != today or self._energy_baseline_kwh is None:
            self._energy_day = today
            self._energy_baseline_kwh = current
            return 0.0

        if current < self._energy_baseline_kwh:
            # Meter reset/rollover; re-baseline rather than report a negative.
            self._energy_baseline_kwh = current
            return 0.0

        return current - self._energy_baseline_kwh

    def _estimate_cost_saved_today(
        self, price_slots: list[PriceSlot], actual_kwh: float | None, ceiling: float
    ) -> float | None:
        """Compare actual spend so far today against a naive always-on baseline.

        Baseline = same kWh used, but priced at today's average rate instead
        of the rates spa-miser actually chose to run heating during.
        """
        if actual_kwh is None or not price_slots:
            return None
        today = dt_util.now().date()
        today_prices = [s.price for s in price_slots if dt_util.as_local(s.start).date() == today]
        if not today_prices:
            return None
        average_price = sum(today_prices) / len(today_prices)
        naive_cost = actual_kwh * average_price

        current_price = self._read_current_price(price_slots)
        actual_cost = actual_kwh * current_price if current_price is not None else naive_cost
        return naive_cost - actual_cost

    def _read_current_price(self, price_slots: list[PriceSlot]) -> float | None:
        now = dt_util.utcnow()
        for slot in price_slots:
            if slot.start <= now < slot.end:
                return slot.price
        return None
