"""Unit tests for the dynamic-programming daily strategy optimizer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.spa_miser.decision_engine import ForecastPoint
from custom_components.spa_miser.price_sources import PriceSlot
from custom_components.spa_miser.strategy import (
    compute_strategy,
    deserialize_strategy,
    serialize_strategy,
    slots_for_display,
)
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

# A real, well-insulated hot tub's loss is tiny next to its heater's power -
# unlike MODEL above (a high-loss fixture that happens to work for the other
# tests, but whose equilibrium temperature sits barely above CEILING_C,
# creating a degenerate case where the model asymptotically creeps toward
# but never cleanly crosses the ceiling in a single step). REALISTIC_MODEL
# mirrors coefficients fitted against a real gateway this session
# (loss_coefficient=0.0057 1/h, thermal_mass=1.63 kWh/C) and is used
# specifically for tests about ceiling-crossing behaviour, where that
# distinction matters.
REALISTIC_MODEL = ThermalModelParams(
    loss_coefficient=0.0057,
    wind_coefficient=0.00023,
    input_coefficient=0.95 / 1.63,
    thermal_mass_kwh_per_c=1.63,
    r_squared=0.57,
    n_samples=200,
)


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


def test_stops_heating_at_ceiling_instead_of_running_the_whole_negative_window():
    # Real hardware doesn't keep drawing full power once at target - a real
    # thermostat cuts off well before a full slot's worth of power is drawn.
    # Before this was fixed, the optimizer treated continued "heating" at
    # the ceiling as pure profit for as long as price stayed negative,
    # front-loading far more heating time than it actually needed.
    prices = [-0.20] * 20 + [0.30] * 4
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C - 0.3,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots(prices),
        model=REALISTIC_MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )

    assert strategy is not None

    negative_price_slots = strategy.slots[:20]
    assert not all(s.heat_on for s in negative_price_slots), (
        "heated through the entire negative-price window - "
        "phantom continued-heating profit at the ceiling wasn't capped"
    )

    # One dedicated pair of full-cost slots is allowed each time the ceiling
    # is (re-)reached - the real system's best chance to actually catch up
    # to the model - but never more than that before a forced coast.
    max_consecutive_heat_on = 0
    current_run = 0
    for slot in strategy.slots:
        current_run = current_run + 1 if slot.heat_on else 0
        max_consecutive_heat_on = max(max_consecutive_heat_on, current_run)
    assert max_consecutive_heat_on <= 2, (
        f"heated for {max_consecutive_heat_on} consecutive slots at the ceiling, expected at most 2"
    )


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


def test_serialize_then_deserialize_round_trips_to_an_equal_strategy():
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots([0.30] * 12 + [0.05] * 12),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )
    assert strategy is not None

    restored = deserialize_strategy(serialize_strategy(strategy))

    assert restored == strategy


def test_slots_for_display_is_columnar_and_matches_the_source_slots():
    strategy = compute_strategy(
        now=NOW,
        current_temp_c=CEILING_C,
        floor_c=FLOOR_C,
        ceiling_c=CEILING_C,
        forecast=_hourly_forecast(24),
        price_slots=_hourly_slots([0.30] * 12 + [0.05] * 12),
        model=MODEL,
        heater_power_kw=HEATER_POWER_KW,
    )
    assert strategy is not None

    display = slots_for_display(strategy)

    assert set(display.keys()) == {"start", "end", "price", "planned_temp_c", "heat_on"}
    n = len(strategy.slots)
    assert all(len(v) == n for v in display.values())
    first = strategy.slots[0]
    assert display["start"][0] == int(first.start.timestamp())
    assert display["end"][0] == int(first.end.timestamp())
    assert display["price"][0] == round(first.price, 4)
    assert display["planned_temp_c"][0] == round(first.planned_temp_c, 2)
    assert display["heat_on"][0] == (1 if first.heat_on else 0)
    # Doesn't touch the Store-facing shape - that must stay stable across
    # restarts independent of how the display shape evolves.
    assert set(serialize_strategy(strategy)["slots"][0].keys()) == {
        "start",
        "end",
        "price",
        "planned_temp_c",
        "heat_on",
    }
    assert isinstance(serialize_strategy(strategy)["slots"], list)


def test_deserialize_returns_none_for_malformed_input():
    assert deserialize_strategy({}) is None
    assert deserialize_strategy({"computed_at": "not a datetime", "slots": []}) is None
    assert deserialize_strategy({"computed_at": NOW.isoformat(), "slots": "not a list"}) is None
    assert (
        deserialize_strategy(
            {"computed_at": NOW.isoformat(), "slots": [{"start": NOW.isoformat()}]}
        )
        is None
    )
