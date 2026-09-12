"""Computes a committed 24h heating plan via dynamic programming.

Unlike decision_engine.decide() (a greedy per-tick heuristic), this looks at
the whole available price/weather forecast at once and finds a genuinely
cost-minimizing heat on/off choice per slot, subject to never breaching the
comfort floor and never exceeding the ceiling (the spa's own thermostat
wouldn't overshoot it anyway). Negative-price slots naturally get preferred
by the optimizer without any special-casing, since minimizing signed cost
already rewards consuming during them.

Dynamic programming over a discretized temperature range, not a MILP solver -
for a single on/off asset with one thermal-mass state variable this is a
well-understood, tractable technique (the same pattern EV/battery smart-
charging schedulers use), and needs no dependency beyond numpy (already
used by thermal_model).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .decision_engine import ForecastPoint
from .price_sources import PriceSlot
from .thermal_model import ThermalModelParams, predict_trajectory

TEMP_STEP_C = 0.1
# Dominates any realistic price-scale cost (£ per slot is normally well
# under 1), so the optimizer avoids a floor breach whenever any feasible
# alternative exists, without needing to hard-prune states (hard pruning
# risks leaving a step with zero candidates - a soft penalty never does).
FLOOR_VIOLATION_PENALTY_PER_DEGREE = 1000.0


@dataclass(frozen=True, slots=True)
class StrategySlot:
    start: datetime
    end: datetime
    price: float
    planned_temp_c: float
    heat_on: bool


@dataclass(frozen=True, slots=True)
class DailyStrategy:
    computed_at: datetime
    slots: list[StrategySlot]

    @property
    def end(self) -> datetime | None:
        return self.slots[-1].end if self.slots else None

    def slot_at(self, at: datetime) -> StrategySlot | None:
        for slot in self.slots:
            if slot.start <= at < slot.end:
                return slot
        return None


def _ambient_for_slot(
    slot: PriceSlot, forecast: list[ForecastPoint]
) -> ForecastPoint | None:
    """Nearest forecast point at/after the slot start, or the last known one."""
    upcoming = [p for p in forecast if p.at >= slot.start]
    if upcoming:
        return min(upcoming, key=lambda p: p.at)
    return forecast[-1] if forecast else None


def _bucket_temps(floor_c: float, ceiling_c: float) -> list[float]:
    span = max(ceiling_c - floor_c, TEMP_STEP_C)
    n = max(1, round(span / TEMP_STEP_C))
    return [floor_c + i * span / n for i in range(n + 1)]


def compute_strategy(
    *,
    now: datetime,
    current_temp_c: float,
    floor_c: float,
    ceiling_c: float,
    forecast: list[ForecastPoint],
    price_slots: list[PriceSlot],
    model: ThermalModelParams,
    heater_power_kw: float,
) -> DailyStrategy | None:
    slots = sorted((s for s in price_slots if s.end > now), key=lambda s: s.start)
    if not slots or not forecast:
        return None

    buckets = _bucket_temps(floor_c, ceiling_c)

    def snap(temp: float) -> int:
        clamped = min(max(temp, floor_c), ceiling_c)
        return min(range(len(buckets)), key=lambda i: abs(buckets[i] - clamped))

    start_idx = snap(current_temp_c)
    top_idx = len(buckets) - 1
    # State is (bucket_idx, grace_used) - see below for what grace_used
    # means. dp[step]: state -> cumulative cost. parent[step]: state ->
    # (prev_state, heat_on).
    State = tuple[int, bool]
    dp: list[dict[State, float]] = [{(start_idx, False): 0.0}]
    parent: list[dict[State, tuple[State, bool]]] = []
    used_slots: list[PriceSlot] = []

    for slot in slots:
        point = _ambient_for_slot(slot, forecast)
        if point is None:
            break
        dt_hours = (slot.end - slot.start).total_seconds() / 3600.0
        current_layer = dp[-1]
        next_layer: dict[State, float] = {}
        step_parent: dict[State, tuple[State, bool]] = {}

        for (idx, grace_used), cost_so_far in current_layer.items():
            temp = buckets[idx]
            at_ceiling_start = idx == top_idx
            for heat_on in (False, True):
                power_kw = heater_power_kw if heat_on else 0.0
                trajectory = predict_trajectory(
                    model,
                    temp,
                    [point.ambient_temp_c],
                    [point.wind_speed_ms],
                    [power_kw],
                    dt_hours=dt_hours,
                )
                new_temp = trajectory[0]
                overshoots_ceiling = at_ceiling_start and new_temp > ceiling_c

                # Real hardware doesn't keep drawing full power once at
                # target - spa-miser only ever commands hvac_mode=heat with
                # the setpoint at the ceiling, and the spa's own onboard
                # thermostat (using its own real sensor) governs actual
                # heating from there, cutting off well before a full slot's
                # worth of power is drawn. Charging a full slot's cost for
                # every subsequent heat_on=True slot once already at the
                # ceiling treats that fictional continued draw as pure
                # profit whenever price is negative, which biased the
                # optimizer toward front-loading far more "heating" time
                # than it actually needs.
                #
                # grace_used tracks whether this "ceiling excursion" has
                # already had one dedicated heat_on=True slot since it was
                # last below the ceiling. That one slot always gets full
                # real cost, deliberately not reduced: the model's belief
                # and the real water temperature can lag each other (fit
                # error, thermal lag, and - see "Known limitations" in the
                # README - the water temperature sensor itself is only
                # ever updated while a pump is actually circulating, so
                # this slot doubles as the only chance to get a fresh real
                # reading at all, not just to thermally catch up), so it's
                # the real system's best chance to actually catch up to the
                # model, not just the model reaching its own target on
                # paper. Once
                # the grace is spent, heat_on=True is dropped as an option
                # entirely for this state rather than merely priced at 0:
                # a genuinely free (0-cost) branch is still selectable, and
                # the optimizer could "pay" one real slot's cost specifically
                # to reach a state that then rides free - re-opening a
                # smaller version of the same bug. Removing the option
                # outright leaves only heat_on=False, which was already
                # free anyway (power_kw=0), so there's nothing left to
                # game. Coasting (heat_on=False) at the ceiling neither
                # spends nor restores the grace - it has no bearing on
                # whether a real heating event has actually happened.
                if heat_on and overshoots_ceiling and grace_used:
                    continue

                new_grace_used = grace_used
                if heat_on and overshoots_ceiling:
                    new_grace_used = True  # this slot just spent the grace

                penalty = (
                    (floor_c - new_temp) * FLOOR_VIOLATION_PENALTY_PER_DEGREE
                    if new_temp < floor_c
                    else 0.0
                )
                new_idx = snap(min(new_temp, ceiling_c))
                if new_idx != top_idx:
                    new_grace_used = False  # dropped below - next excursion starts fresh
                total_cost = cost_so_far + (slot.price * power_kw * dt_hours) + penalty
                new_state = (new_idx, new_grace_used)
                if new_state not in next_layer or total_cost < next_layer[new_state]:
                    next_layer[new_state] = total_cost
                    step_parent[new_state] = ((idx, grace_used), heat_on)

        dp.append(next_layer)
        parent.append(step_parent)
        used_slots.append(slot)

    if len(dp) <= 1:
        return None  # no forecast overlap at all - can't plan anything

    final_layer = dp[-1]
    best_state = min(final_layer, key=lambda s: final_layer[s])
    chosen_heat: list[bool] = [False] * len(used_slots)
    state = best_state
    for step in range(len(used_slots) - 1, -1, -1):
        state, heat_on = parent[step][state]
        chosen_heat[step] = heat_on

    # Re-simulate forward with the chosen heat_on sequence to record the
    # actual (unbucketed) planned temperature at each slot.
    result_slots: list[StrategySlot] = []
    temp = current_temp_c
    for slot, heat_on in zip(used_slots, chosen_heat, strict=True):
        point = _ambient_for_slot(slot, forecast)
        dt_hours = (slot.end - slot.start).total_seconds() / 3600.0
        power_kw = heater_power_kw if heat_on else 0.0
        trajectory = predict_trajectory(
            model, temp, [point.ambient_temp_c], [point.wind_speed_ms], [power_kw], dt_hours=dt_hours
        )
        temp = min(trajectory[0], ceiling_c)
        result_slots.append(
            StrategySlot(
                start=slot.start,
                end=slot.end,
                price=slot.price,
                planned_temp_c=temp,
                heat_on=heat_on,
            )
        )

    return DailyStrategy(computed_at=now, slots=result_slots)
