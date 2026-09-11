"""Coordinator: pulls forecasts, refits the thermal model, and decides/acts."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    CONF_MIN_AWAY_TEMP,
    CONF_MIN_COMFORT_TEMP,
    CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY,
    CONF_OCTOPUS_REGION,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_POWER_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WIND_SPEED_SENSOR,
    COORDINATOR_UPDATE_INTERVAL_MINUTES,
    DECISION_LOOKAHEAD_HOURS,
    DEFAULT_AWAY_MODE,
    DEFAULT_ENABLED,
    DEFAULT_MANUAL_CHEAP_HOURS,
    DEFAULT_MANUAL_CHEAP_RATE,
    DEFAULT_MANUAL_OVERRIDE_MINUTES,
    DEFAULT_MANUAL_STANDARD_RATE,
    DEFAULT_MAX_COMFORT_TEMP,
    DEFAULT_MIN_AWAY_TEMP,
    DEFAULT_MIN_COMFORT_TEMP,
    HEATING_STATE_ACTIVE_VALUES,
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
from .spa_control import SpaControl
from .strategy import DailyStrategy, compute_strategy
from .thermal_model import ThermalModelParams
from .thermal_model import fit as fit_model
from .thermal_model import predict_trajectory

# A new plan is only worth recomputing when the price forecast has grown by
# meaningfully more than this - avoids re-triggering on tiny/noise coverage
# differences between ticks, while still reliably catching "tomorrow's
# Agile rates just published" (which extends coverage by ~24h).
STRATEGY_RECOMPUTE_COVERAGE_MARGIN_HOURS = 1

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpaMiserData:
    model: ThermalModelParams | None = None
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
    # Independent of the thermal model/decision (which both need ~24h of
    # history before producing anything) - lets a price source be verified
    # as actually wired up and returning real data immediately.
    current_price: float | None = None
    price_slots_count: int = 0
    # Raw current readings of the configured input entities, so what
    # spa-miser is actually seeing can be checked at a glance rather than
    # cross-referencing sensor.spa_miser_configured_sources' entity_ids
    # against those entities' own states elsewhere in HA.
    heating_state: str | None = None
    outdoor_temperature_c: float | None = None


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
        self._heater_power_kw: float = history.DEFAULT_HEATER_POWER_KW

        self._enabled: bool = entry.options.get(CONF_ENABLED, DEFAULT_ENABLED)
        self._away_mode: bool = entry.options.get(CONF_AWAY_MODE, DEFAULT_AWAY_MODE)
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

        self._energy_day: date | None = None
        self._energy_baseline_kwh: float | None = None

    def async_setup(self) -> None:
        self.spa_control.async_setup()

    def async_unload(self) -> None:
        self.spa_control.async_unload()

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
    def strategy(self) -> DailyStrategy | None:
        """The current committed daily plan, if one has been computed yet.

        Read directly by sensor.py (not routed through SpaMiserData) - same
        pattern as entry.data for configured_sources: it changes far less
        often than every coordinator tick, so there's no need to copy it
        into the per-tick snapshot.
        """
        return self._strategy

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

        current_temp = self._read_state_float(self.entry.data[CONF_WATER_TEMP_SENSOR])
        price_slots = await self.price_source.async_get_forecast(self.hass)
        forecast = await self._async_get_weather_forecast()

        floor = self._min_away_c if self._away_mode else self._min_comfort_c
        ceiling = self._max_comfort_c

        if self._should_recompute_strategy(price_slots):
            await self._async_recompute_strategy(current_temp, floor, ceiling, forecast, price_slots)

        decision = self._decide(current_temp, floor, ceiling, forecast, price_slots)

        if (
            self._enabled
            and decision is not None
            and self.spa_control.is_available
            and not self.spa_control.is_manually_overridden
        ):
            await self._async_apply_decision(decision)

        model_temp = self._predict_model_temperature(current_temp, forecast)
        predicted_kwh = self._estimate_predicted_kwh_today(forecast)
        actual_kwh = self._read_actual_kwh_today()
        cost_saved = self._estimate_cost_saved_today(price_slots, actual_kwh, ceiling)
        heating_state = self._read_state_string(self.entry.data.get(CONF_HEATING_STATE_ENTITY))
        outdoor_temp = self._read_state_float(self.entry.data.get(CONF_OUTDOOR_TEMP_SENSOR))

        return SpaMiserData(
            model=self._model,
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
            current_price=self._read_current_price(price_slots),
            price_slots_count=len(price_slots),
        )

    # --- spa actuation ----------------------------------------------------

    async def _async_apply_decision(self, decision: Decision) -> None:
        target_preset = PRESET_LOW_RANGE if self._away_mode else PRESET_HIGH_RANGE
        target_temp = self._min_away_c if self._away_mode else self._max_comfort_c

        if self.spa_control.preset_mode != target_preset:
            await self.spa_control.async_set_preset(target_preset)
        current_target = self._read_state_float(
            self.entry.data[CONF_CLIMATE_ENTITY], attribute="temperature"
        )
        if current_target is None or abs(current_target - target_temp) > 0.25:
            await self.spa_control.async_set_target_temperature(target_temp)

        heat_on = self.spa_control.hvac_mode == "heat"
        if decision.heat_recommended and not heat_on:
            await self.spa_control.async_set_heat_enabled(True)
        elif not decision.heat_recommended and heat_on:
            await self.spa_control.async_set_heat_enabled(False)

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
                    reason=f"daily strategy: {action} (plan targets {slot.planned_temp_c:.1f}°C)",
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

        self._heater_power_kw = await history.async_estimate_heater_power_kw(
            self.hass,
            power_entity=self.entry.data[CONF_POWER_ENTITY],
            start=dt_util.utcnow() - timedelta(days=MODEL_FIT_LOOKBACK_DAYS),
            end=dt_util.utcnow(),
        )

        strategy = compute_strategy(
            now=dt_util.utcnow(),
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
        self._strategy = strategy
        _LOGGER.info(
            "Computed new daily strategy: %d slots, %d heat-on, heater_power_kw=%.2f",
            len(strategy.slots),
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
            points.append(
                ForecastPoint(
                    at=at, ambient_temp_c=float(temp), wind_speed_ms=float(wind or 0.0)
                )
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
        self, current_temp: float | None, forecast: list[ForecastPoint]
    ) -> float | None:
        if self._model is None or current_temp is None or not forecast:
            return None
        heat_on = self.spa_control.hvac_mode == "heat"
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
