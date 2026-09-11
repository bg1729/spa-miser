"""Unit tests for the greedy price/thermal lookahead scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.spa_miser.decision_engine import ForecastPoint, decide
from custom_components.spa_miser.price_sources import PriceSlot
from custom_components.spa_miser.thermal_model import ThermalModelParams

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

MODEL = ThermalModelParams(
    loss_coefficient=0.05,
    wind_coefficient=0.0,
    input_coefficient=0.5,
    thermal_mass_kwh_per_c=1.9,
    r_squared=0.99,
    n_samples=100,
)


def _hourly_forecast(hours: int, ambient_c: float = 5.0) -> list[ForecastPoint]:
    return [
        ForecastPoint(at=NOW + timedelta(hours=i + 1), ambient_temp_c=ambient_c, wind_speed_ms=0.0)
        for i in range(hours)
    ]


def _flat_price_slots(hours: int, price: float) -> list[PriceSlot]:
    return [
        PriceSlot(
            start=NOW + timedelta(hours=i),
            end=NOW + timedelta(hours=i + 1),
            price=price,
        )
        for i in range(hours)
    ]


def test_forces_heat_when_already_at_or_below_floor():
    decision = decide(
        now=NOW,
        current_temp_c=36.0,
        floor_c=36.0,
        ceiling_c=39.5,
        forecast=_hourly_forecast(24),
        price_slots=_flat_price_slots(24, price=1.0),  # expensive throughout
        model=MODEL,
    )

    assert decision.heat_recommended is True
    assert decision.floor_breach_hours == 0.0


def test_no_heat_when_far_from_floor_and_price_expensive():
    # Small water/ambient delta + short horizon means the floor won't be
    # reached soon, and every slot is priced the same (nothing is "cheap").
    decision = decide(
        now=NOW,
        current_temp_c=39.0,
        floor_c=30.0,
        ceiling_c=39.5,
        forecast=_hourly_forecast(24, ambient_c=25.0),
        price_slots=_flat_price_slots(24, price=1.0),
        model=MODEL,
    )

    assert decision.heat_recommended is False


def test_heats_during_negative_price_slot_even_when_far_from_floor():
    slots = _flat_price_slots(24, price=1.0)
    slots[0] = PriceSlot(start=NOW, end=NOW + timedelta(hours=1), price=-0.05)

    decision = decide(
        now=NOW,
        current_temp_c=37.0,
        floor_c=30.0,
        ceiling_c=39.5,
        forecast=_hourly_forecast(24, ambient_c=25.0),
        price_slots=slots,
        model=MODEL,
    )

    assert decision.heat_recommended is True
    assert decision.current_price == -0.05


def test_no_heat_when_already_at_ceiling_even_if_cheap():
    decision = decide(
        now=NOW,
        current_temp_c=39.5,
        floor_c=30.0,
        ceiling_c=39.5,
        forecast=_hourly_forecast(24, ambient_c=25.0),
        price_slots=_flat_price_slots(24, price=-0.05),
        model=MODEL,
    )

    assert decision.heat_recommended is False
    assert decision.reason == "already at or near ceiling"


def test_no_forecast_data_does_not_crash_and_declines_to_heat():
    decision = decide(
        now=NOW,
        current_temp_c=37.0,
        floor_c=30.0,
        ceiling_c=39.5,
        forecast=[],
        price_slots=_flat_price_slots(24, price=0.2),
        model=MODEL,
    )

    assert decision.heat_recommended is False
    assert decision.floor_breach_hours is None
