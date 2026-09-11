"""Tests for the Octopus Agile and manual price source adapters."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.spa_miser.price_sources.manual import ManualPriceSource
from custom_components.spa_miser.price_sources.octopus_agile import (
    OctopusAgilePriceSource,
    _next_day_entity_id,
)


def test_next_day_entity_id_swaps_suffix():
    assert (
        _next_day_entity_id("event.octopus_energy_electricity_1234_5678_current_day_rates")
        == "event.octopus_energy_electricity_1234_5678_next_day_rates"
    )


async def test_octopus_agile_parses_and_concatenates_rates(hass: HomeAssistant) -> None:
    now = dt_util.utcnow()
    current_entity = "event.octopus_energy_electricity_1234_5678_current_day_rates"
    next_entity = "event.octopus_energy_electricity_1234_5678_next_day_rates"

    hass.states.async_set(
        current_entity,
        "2026-01-01T00:00:00+00:00",
        {
            "rates": [
                {
                    "start": (now + timedelta(minutes=30)).isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                    "value_inc_vat": 15.5,
                },
                {
                    # already in the past - should be filtered out
                    "start": (now - timedelta(hours=2)).isoformat(),
                    "end": (now - timedelta(hours=1, minutes=30)).isoformat(),
                    "value_inc_vat": 99.0,
                },
            ]
        },
    )
    hass.states.async_set(
        next_entity,
        "2026-01-02T00:00:00+00:00",
        {
            "rates": [
                {
                    "start": (now + timedelta(hours=25)).isoformat(),
                    "end": (now + timedelta(hours=25, minutes=30)).isoformat(),
                    "value_inc_vat": -3.2,
                }
            ]
        },
    )

    source = OctopusAgilePriceSource(current_entity)
    slots = await source.async_get_forecast(hass)

    assert len(slots) == 2
    assert slots[0].price == 0.155  # pence -> pounds
    assert slots[1].price == -0.032
    assert slots[0].start < slots[1].start


async def test_octopus_agile_missing_entities_returns_empty(hass: HomeAssistant) -> None:
    source = OctopusAgilePriceSource("event.does_not_exist_current_day_rates")

    slots = await source.async_get_forecast(hass)

    assert slots == []


async def test_manual_price_source_marks_configured_hours_cheap(hass: HomeAssistant) -> None:
    source = ManualPriceSource(cheap_hours=[2, 3], cheap_rate=0.10, standard_rate=0.30)

    slots = await source.async_get_forecast(hass)

    assert len(slots) > 0
    for slot in slots:
        local_hour = dt_util.as_local(slot.start).hour
        expected = 0.10 if local_hour in (2, 3) else 0.30
        assert slot.price == expected
