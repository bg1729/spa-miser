"""Unit tests for the dynamic-programming daily strategy optimizer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.spa_miser.decision_engine import ForecastPoint
from custom_components.spa_miser.price_sources import PriceSlot
from custom_components.spa_miser.strategy import compute_strategy
from custom_components.spa_miser.thermal_model import ThermalModelParams

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

MODEL = ThermalModelParams(
    loss_coefficient=0.05,
    wind_coefficient=0.0,
    input_coefficient=0.5,
    thermal_mass_kwh_per_c=1.9,
    r_squared=0.99,
    n_samples=200,
)

FLOOR_C = 25.0
CEILING_C = 39.5
HEATER_POWER_KW = 3.0


def _hourly_forecast(hours: int, ambient_c: float = 10.0) -> list[ForecastPoint]:
    return [
        ForecastPoint(at=NOW + timedelta(hours=i), ambient_temp_c=ambient_c, wind_speed_ms=0.0)
        for i in range(hours)
    ]


def _hourly_slots(prices: list[float]) -> list[PriceSlot]:
    return [
        PriceSlot(start=NOW + timedelta(hours=i), end=NOW + timedelta(hours=i + 1), price=p)
        for i, p in enumerate(prices)
    ]


def test_prefers_heating_during_the_cheap_window():
    # Coasting alone from ceiling breaches the floor at ~13.5h (verified by
    # hand against this model's decay curve), so some heating within the 24h
    # horizon is unavoidable - hours 0-11 are expensive, 12-23 are cheap, so
    # the optimizer should defer all heating into the cheap window rather
    # than heat early just because the floor is *eventually* at risk.
    prices = [0.30] * 12 + [0.05] * 12
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots(prices),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )

    assert strategy is not None
    assert len(strategy.slots) == 24

    expensive_slots = strategy.slots[:12]
    cheap_slots = strategy.slots[12:]
    assert not any(s.heat_on for s in expensive_slots), (
        "heated during the expensive window when it could have waited"
    )
    assert any(s.heat_on for s in cheap_slots), (
        "never heated at all - floor should have forced some heating"
    )


def test_never_breaches_floor_or_exceeds_ceiling():
    prices = [0.30] * 12 + [0.05] * 12
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots(prices),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )

    assert strategy is not None
    for slot in strategy.slots:
        assert slot.planned_temp_c >= FLOOR_C - 0.15, f"breached floor: {slot}"
        assert slot.planned_temp_c <= CEILING_C + 1e-6, f"exceeded ceiling: {slot}"


def test_negative_price_is_preferred_over_merely_cheap():
    # A negative-price slot should be at least as attractive as heating
    # during a positive-price cheap slot - check the optimizer actually
    # uses the negative slot when both would otherwise satisfy the floor.
    prices = [0.30] * 12 + [0.05] * 11 + [-0.10]
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots(prices),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )

    assert strategy is not None
    assert strategy.slots[-1].heat_on, "didn't take the negative-price slot"


def test_total_cost_beats_a_naive_always_heat_baseline():
    prices = [0.30] * 12 + [0.05] * 12
    slots = _hourly_slots(prices)
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=slots,
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )
    assert strategy is not None

    strategy_cost = sum(
        s.price * HEATER_POWER_KW * 1.0 for s in strategy.slots if s.heat_on
    )
    naive_always_heat_cost = sum(s.price * HEATER_POWER_KW * 1.0 for s in slots)

    assert strategy_cost < naive_always_heat_cost


def test_no_forecast_overlap_returns_none():
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=[],
        price_slots=_hourly_slots([0.2] * 24),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )
    assert strategy is None


def test_slot_at_looks_up_the_containing_slot():
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots([0.2] * 24),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )
    assert strategy is not None
    found = strategy.slot_at(NOW + timedelta(hours=5, minutes=30))
    assert found is not None
    assert found.start == NOW + timedelta(hours=5)
    assert strategy.slot_at(NOW + timedelta(hours=100)) is None
