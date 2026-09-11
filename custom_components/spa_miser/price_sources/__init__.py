"""Price source abstraction.

Different time-of-use tariff integrations expose forecast pricing in
incompatible shapes (there is no HA-core-wide standard the way there is for
`weather.get_forecasts`). Each supported tariff gets its own adapter module
implementing `PriceSource`; callers only depend on this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """A single priced time slot."""

    start: datetime
    end: datetime
    price: float  # currency per kWh, e.g. GBP; may be negative


class PriceSource(ABC):
    """Returns a forecast of upcoming price slots."""

    @abstractmethod
    async def async_get_forecast(self, hass) -> list[PriceSlot]:
        """Return known price slots, soonest first.

        Implementations should return whatever they actually have (which may
        be a short horizon, e.g. only "today" before ~4pm for Octopus Agile)
        rather than raising, so the decision engine can degrade gracefully.
        """
