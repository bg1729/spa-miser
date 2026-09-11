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
    # dp[step]: bucket_idx -> cumulative cost. parent[step]: bucket_idx -> (prev_idx, heat_on).
    dp: list[dict[int, float]] = [{start_idx: 0.0}]
    parent: list[dict[int, tuple[int, bool]]] = []
    used_slots: list[PriceSlot] = []

    for slot in slots:
        point = _ambient_for_slot(slot, forecast)
        if point is None:
            break
        dt_hours = (slot.end - slot.start).total_seconds() / 3600.0
        current_layer = dp[-1]
        next_layer: dict[int, float] = {}
        step_parent: dict[int, tuple[int, bool]] = {}

        for idx, cost_so_far in current_layer.items():
            temp = buckets[idx]
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
                penalty = (
                    (floor_c - new_temp) * FLOOR_VIOLATION_PENALTY_PER_DEGREE
                    if new_temp < floor_c
                    else 0.0
                )
                new_idx = snap(min(new_temp, ceiling_c))
                total_cost = (
                    cost_so_far + (slot.price * power_kw * dt_hours) + penalty
                )
                if new_idx not in next_layer or total_cost < next_layer[new_idx]:
                    next_layer[new_idx] = total_cost
                    step_parent[new_idx] = (idx, heat_on)

        dp.append(next_layer)
        parent.append(step_parent)
        used_slots.append(slot)

    if len(dp) <= 1:
        return None  # no forecast overlap at all - can't plan anything

    final_layer = dp[-1]
    best_idx = min(final_layer, key=lambda i: final_layer[i])
    chosen_heat: list[bool] = [False] * len(used_slots)
    idx = best_idx
    for step in range(len(used_slots) - 1, -1, -1):
        idx, heat_on = parent[step][idx]
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
