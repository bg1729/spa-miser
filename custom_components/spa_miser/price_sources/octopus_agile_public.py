"""Price source adapter reading Agile rates from Octopus's public API.

Unlike octopus_agile.py (which reads the BottlecapDave integration's event
entities and therefore reflects whatever tariff the user is actually billed
on), this hits https://api.octopus.energy/v1/ directly - no Octopus account,
API key, or Agile subscription required. It lets the decision engine
schedule against real regional Agile pricing even for a user on a different
real tariff, to "mirror Agile behaviour" without switching.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import PriceSlot, PriceSource

_LOGGER = logging.getLogger(__name__)

PRODUCTS_URL = "https://api.octopus.energy/v1/products/"
RATES_URL_TEMPLATE = (
    "https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/"
    "E-1R-{product_code}-{region}/standard-unit-rates/"
)
PENCE_PER_POUND = 100.0
FORECAST_HOURS = 48
# The default page size (100) can be smaller than a full day-plus-lookahead
# span of half-hour slots once today's already-elapsed slots are included
# too; request enough that pagination never silently truncates the result.
RATES_PAGE_SIZE = 1500
# How long to trust a discovered "current Agile product code" before
# re-checking - Octopus rotates the active Agile product every few months,
# not every request.
PRODUCT_CACHE_MINUTES = 60
REQUEST_TIMEOUT_SECONDS = 10
# Matches only the standard import Agile product's date-coded naming (e.g.
# AGILE-24-10-01). A bare `code.startswith("AGILE-")` check also matches
# other real Octopus products sharing that prefix - notably
# AGILE-OUTGOING-19-05-13, the *export* tariff for solar, which has an
# entirely different (much lower/negative) price structure and would
# otherwise get silently selected instead (it sorts after any AGILE-YY-...
# code lexicographically, so a naive "pick the greatest" heuristic picks it).
AGILE_IMPORT_PRODUCT_CODE_RE = re.compile(r"^AGILE-\d{2}-\d{2}-\d{2}$")


class OctopusAgilePublicPriceSource(PriceSource):
    """Reads forecast Agile rates from Octopus's public product API."""

    def __init__(self, region: str) -> None:
        self._region = region.upper()
        self._cached_product_code: str | None = None
        self._cached_at: datetime | None = None

    async def _async_get_active_product_code(self, hass: HomeAssistant) -> str | None:
        now = dt_util.utcnow()
        if (
            self._cached_product_code is not None
            and self._cached_at is not None
            and now - self._cached_at < timedelta(minutes=PRODUCT_CACHE_MINUTES)
        ):
            return self._cached_product_code

        session = async_get_clientsession(hass)
        try:
            response = await session.get(PRODUCTS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = await response.json()
        except Exception:  # noqa: BLE001 - network/API failures shouldn't crash the coordinator
            _LOGGER.exception("Failed to fetch Octopus product list")
            return self._cached_product_code  # fall back to last-known-good, if any

        # Product codes are date-stamped (e.g. AGILE-24-10-01); among
        # currently-active *import* Agile products, the lexicographically
        # greatest code is the newest (date-coded, so lexicographic order
        # matches chronological order once non-matching codes are excluded).
        candidates = sorted(
            p["code"]
            for p in data.get("results", [])
            if AGILE_IMPORT_PRODUCT_CODE_RE.match(p.get("code", ""))
            and p.get("available_to") is None
        )
        if not candidates:
            _LOGGER.warning("No currently-active Octopus Agile product found")
            return self._cached_product_code

        self._cached_product_code = candidates[-1]
        self._cached_at = now
        return self._cached_product_code

    async def async_get_forecast(self, hass: HomeAssistant) -> list[PriceSlot]:
        product_code = await self._async_get_active_product_code(hass)
        if product_code is None:
            return []

        now = dt_util.utcnow()
        url = RATES_URL_TEMPLATE.format(product_code=product_code, region=self._region)
        # From the start of today, not from "now": Agile rates are fixed once
        # published (never revised), so today's already-elapsed slots are
        # just as much "the price" as the future ones - callers (charting in
        # particular) shouldn't have to reconstruct them from recorder
        # history. Decision-making code separately filters to s.end > now
        # wherever only the remaining window matters.
        period_from = dt_util.as_utc(dt_util.start_of_local_day(now))
        params = {
            "period_from": period_from.isoformat(),
            "period_to": (now + timedelta(hours=FORECAST_HOURS)).isoformat(),
            "page_size": RATES_PAGE_SIZE,
        }

        session = async_get_clientsession(hass)
        try:
            response = await session.get(
                url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = await response.json()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to fetch Octopus Agile public rates from %s", url)
            return []

        slots: list[PriceSlot] = []
        for item in data.get("results", []):
            try:
                start = dt_util.parse_datetime(item["valid_from"])
                end = dt_util.parse_datetime(item["valid_to"])
                price = float(item["value_inc_vat"]) / PENCE_PER_POUND
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping unparseable Agile rate entry: %s", item)
                continue
            if start is None or end is None:
                continue
            slots.append(PriceSlot(start=start, end=end, price=price))

        slots.sort(key=lambda s: s.start)
        return slots
