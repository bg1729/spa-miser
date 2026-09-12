"""Price source adapter for the Octopus Energy (BottlecapDave) integration.

Reads the `event.*_current_day_rates` / `event.*_next_day_rates` entities the
integration creates for an electricity meter point. Their `rates` attribute is
a list of `{"start": iso, "end": iso, "value_inc_vat": float}` dicts (pence,
typically GBP/kWh * 100). `next_day_rates` only populates once tomorrow's
Agile rates are published (usually ~4pm), so the forecast horizon naturally
shrinks/grows through the day — callers should tolerate a short forecast.
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import PriceSlot, PriceSource

_LOGGER = logging.getLogger(__name__)

# event entities store price in pence/kWh; normalise to pounds/kWh so the
# decision engine and cost sensors work in one consistent unit.
_PENCE_PER_POUND = 100.0


def _next_day_entity_id(current_day_entity_id: str) -> str:
    if current_day_entity_id.endswith("_current_day_rates"):
        return current_day_entity_id[: -len("_current_day_rates")] + "_next_day_rates"
    # Fall back to a best-effort guess if the naming doesn't match what we
    # expect (e.g. the user picked an already-renamed entity_id).
    return current_day_entity_id.replace("current_day", "next_day")


def _parse_rates(hass: HomeAssistant, entity_id: str) -> list[PriceSlot]:
    state = hass.states.get(entity_id)
    if state is None:
        return []
    rates = state.attributes.get("rates")
    if not rates:
        return []

    slots: list[PriceSlot] = []
    for rate in rates:
        try:
            start = dt_util.parse_datetime(rate["start"])
            end = dt_util.parse_datetime(rate["end"])
            price = float(rate["value_inc_vat"]) / _PENCE_PER_POUND
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("Skipping unparseable rate entry on %s: %s", entity_id, rate)
            continue
        if start is None or end is None:
            continue
        slots.append(PriceSlot(start=start, end=end, price=price))
    return slots


class OctopusAgilePriceSource(PriceSource):
    """Reads forecast rates from the Octopus Energy integration's event entities."""

    def __init__(self, current_day_rates_entity_id: str) -> None:
        self._current_day_entity_id = current_day_rates_entity_id
        self._next_day_entity_id = _next_day_entity_id(current_day_rates_entity_id)

    async def async_get_forecast(self, hass: HomeAssistant) -> list[PriceSlot]:
        slots = _parse_rates(hass, self._current_day_entity_id)
        slots += _parse_rates(hass, self._next_day_entity_id)

        # Deliberately not filtered to s.end > now: Agile rates are fixed
        # once published (never revised), so today's already-elapsed slots
        # are just as much "the price" as the future ones - callers
        # (charting in particular) shouldn't have to reconstruct them from
        # recorder history. Decision-making code separately filters to
        # s.end > now wherever only the remaining window matters.
        slots.sort(key=lambda s: s.start)

        if not slots:
            _LOGGER.warning(
                "No Octopus Agile rate data available from %s / %s; "
                "spa-miser cannot make price-aware decisions until rates publish",
                self._current_day_entity_id,
                self._next_day_entity_id,
            )
        return slots
