"""v1 greedy price/thermal lookahead scheduler.

The spa's own onboard controller already regulates to its setpoint whenever
it's in heat mode (mode=heat/"Ready") — spa-miser doesn't need to compute
exact reheat energy, just decide *when* heat mode should be allowed to run so
that regulation happens during cheap periods rather than continuously.

Algorithm per tick:
1. Simulate a pure coast-down (heater off) from the current temperature across
   the forecast horizon to find when the comfort floor would be breached.
2. Rank the price slots between now and that breach point (plus a look-ahead
   buffer used purely to establish what "cheap" means) by price; negative
   prices always count as cheap, otherwise the cheapest `CHEAP_PERCENTILE`
   fraction of slots in view does.
3. If the floor is already breached (or about to be, within one tick),
   heating is forced on regardless of price — comfort floor is a hard
   constraint.
4. Otherwise heat is recommended only when the current price slot is cheap
   AND the tub isn't already at/near the ceiling (no benefit to heating a
   full tub even if the slot is cheap).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .price_sources import PriceSlot
from .thermal_model import ThermalModelParams, predict_trajectory

CHEAP_PERCENTILE = 0.3
CEILING_MARGIN_C = 0.3
FLOOR_IMMINENT_HOURS = 0.5
MIN_CHEAP_WINDOW_HOURS = 4.0


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """One forecast timestep, aligned to a price slot."""

    at: datetime
    ambient_temp_c: float
    wind_speed_ms: float


@dataclass(frozen=True, slots=True)
class Decision:
    heat_recommended: bool
    reason: str
    floor_breach_hours: float | None
    cheap_price_threshold: float | None
    current_price: float | None


def _hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def _find_floor_breach_hours(
    now: datetime,
    current_temp_c: float,
    floor_c: float,
    forecast: list[ForecastPoint],
    model: ThermalModelParams,
) -> float | None:
    if current_temp_c <= floor_c:
        return 0.0
    if not forecast:
        return None

    ambient = [p.ambient_temp_c for p in forecast]
    wind = [p.wind_speed_ms for p in forecast]
    zero_power = [0.0] * len(forecast)
    trajectory = predict_trajectory(model, current_temp_c, ambient, wind, zero_power)

    for point, temp in zip(forecast, trajectory, strict=True):
        if temp <= floor_c:
            return _hours_between(now, point.at)
    return None


def _cheap_threshold(prices: list[float]) -> float | None:
    """Return the cheapest-tier price cutoff, or None if nothing stands out.

    With no price variation in the window (e.g. a flat/manual tariff with no
    cheap-hours match), every slot is trivially "cheap relative to itself" -
    returning None here means the caller falls back to only the negative-price
    safety net rather than treating a uniform price as always cheap.
    """
    if not prices:
        return None
    if min(prices) == max(prices):
        return None
    ordered = sorted(prices)
    idx = max(0, int(len(ordered) * CHEAP_PERCENTILE) - 1)
    return ordered[idx]


def decide(
    *,
    now: datetime,
    current_temp_c: float,
    floor_c: float,
    ceiling_c: float,
    forecast: list[ForecastPoint],
    price_slots: list[PriceSlot],
    model: ThermalModelParams,
) -> Decision:
    floor_breach_hours = _find_floor_breach_hours(
        now, current_temp_c, floor_c, forecast, model
    )

    if floor_breach_hours is not None and floor_breach_hours <= FLOOR_IMMINENT_HOURS:
        return Decision(
            heat_recommended=True,
            reason="comfort floor reached or imminent",
            floor_breach_hours=floor_breach_hours,
            cheap_price_threshold=None,
            current_price=None,
        )

    # The ranking window always covers at least MIN_CHEAP_WINDOW_HOURS, even
    # when the floor would be breached sooner: a window of just the next
    # slot or two has no meaningful spread to rank "cheap" against (relative
    # cheapness needs something to be relatively cheaper than).
    window_hours = max(floor_breach_hours or 0, MIN_CHEAP_WINDOW_HOURS)
    horizon_end = now.timestamp() + window_hours * 3600
    # `end > now` (not `start >= now`) so the currently-active slot - whose
    # start is necessarily in the past once we're partway through it - is
    # still included; excluding it left the window blind to exactly the
    # slot the decision is being made about.
    window_prices = [
        s for s in price_slots if s.end > now and s.start.timestamp() <= horizon_end
    ]
    if not window_prices:
        window_prices = [s for s in price_slots if s.end > now]

    threshold = _cheap_threshold([s.price for s in window_prices])

    current_slot = next(
        (s for s in price_slots if s.start <= now < s.end),
        price_slots[0] if price_slots else None,
    )
    current_price = current_slot.price if current_slot else None

    if current_temp_c >= ceiling_c - CEILING_MARGIN_C:
        return Decision(
            heat_recommended=False,
            reason="already at or near ceiling",
            floor_breach_hours=floor_breach_hours,
            cheap_price_threshold=threshold,
            current_price=current_price,
        )

    if current_price is None:
        return Decision(
            heat_recommended=False,
            reason="no price forecast available",
            floor_breach_hours=floor_breach_hours,
            cheap_price_threshold=threshold,
            current_price=current_price,
        )

    # threshold is None when the visible window has no price variation at
    # all (e.g. a flat tariff outside its cheap hours) - only the negative
    # safety net applies then, since nothing is relatively cheap.
    is_cheap = current_price <= 0 or (threshold is not None and current_price <= threshold)
    return Decision(
        heat_recommended=is_cheap,
        reason="current slot is cheap" if is_cheap else "waiting for a cheaper slot",
        floor_breach_hours=floor_breach_hours,
        cheap_price_threshold=threshold,
        current_price=current_price,
    )
