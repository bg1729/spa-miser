"""Fallback price source for users without a supported tariff integration.

Generates a synthetic forecast from a fixed set of "cheap hours of day" plus
two flat rates, so spa-miser is still useful (heat preferentially overnight,
say) without requiring Octopus Agile or another dynamic tariff.
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DECISION_LOOKAHEAD_HOURS
from . import PriceSlot, PriceSource


class ManualPriceSource(PriceSource):
    """Synthesises a forecast from a fixed daily cheap-hours window."""

    def __init__(
        self, cheap_hours: list[int], cheap_rate: float, standard_rate: float
    ) -> None:
        self._cheap_hours = set(cheap_hours)
        self._cheap_rate = cheap_rate
        self._standard_rate = standard_rate

    async def async_get_forecast(self, hass: HomeAssistant) -> list[PriceSlot]:
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now).replace(minute=0, second=0, microsecond=0)

        slots: list[PriceSlot] = []
        for i in range(DECISION_LOOKAHEAD_HOURS):
            start_local = local_now + timedelta(hours=i)
            end_local = start_local + timedelta(hours=1)
            price = (
                self._cheap_rate
                if start_local.hour in self._cheap_hours
                else self._standard_rate
            )
            slots.append(
                PriceSlot(
                    start=dt_util.as_utc(start_local),
                    end=dt_util.as_utc(end_local),
                    price=price,
                )
            )
        return slots
