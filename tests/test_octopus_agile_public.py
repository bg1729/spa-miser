"""Tests for the public-API Octopus Agile price source (no account needed)."""
from __future__ import annotations

from custom_components.spa_miser.price_sources.octopus_agile_public import (
    PRODUCTS_URL,
    OctopusAgilePublicPriceSource,
)

PRODUCTS_RESPONSE = {
    "results": [
        {"code": "AGILE-23-12-06", "available_to": "2024-10-01T00:00:00Z"},
        {"code": "AGILE-24-10-01", "available_to": None},
        {"code": "VAR-22-11-01", "available_to": None},  # not an Agile product
    ]
}

RATES_RESPONSE = {
    "results": [
        {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-01-01T00:30:00Z",
            "value_inc_vat": 15.5,
        },
        {
            "valid_from": "2026-01-01T00:30:00Z",
            "valid_to": "2026-01-01T01:00:00Z",
            "value_inc_vat": -3.2,
        },
    ]
}


async def test_fetches_rates_for_the_latest_active_agile_product(hass, aioclient_mock):
    aioclient_mock.get(PRODUCTS_URL, json=PRODUCTS_RESPONSE)
    rates_url = (
        "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/"
        "E-1R-AGILE-24-10-01-E/standard-unit-rates/"
    )
    aioclient_mock.get(rates_url, json=RATES_RESPONSE)

    source = OctopusAgilePublicPriceSource(region="e")  # lowercase, should uppercase

    slots = await source.async_get_forecast(hass)
    await hass.async_block_till_done()

    assert len(slots) == 2
    assert slots[0].price == 0.155  # pence -> pounds
    assert slots[1].price == -0.032
    assert aioclient_mock.call_count == 2


async def test_no_active_agile_product_returns_empty(hass, aioclient_mock):
    aioclient_mock.get(
        PRODUCTS_URL,
        json={"results": [{"code": "AGILE-23-12-06", "available_to": "2024-10-01T00:00:00Z"}]},
    )

    source = OctopusAgilePublicPriceSource(region="L")

    slots = await source.async_get_forecast(hass)

    assert slots == []


async def test_products_request_failure_returns_empty_not_raises(hass, aioclient_mock):
    aioclient_mock.get(PRODUCTS_URL, status=500)

    source = OctopusAgilePublicPriceSource(region="L")

    slots = await source.async_get_forecast(hass)

    assert slots == []


async def test_product_code_is_cached_across_calls(hass, aioclient_mock):
    aioclient_mock.get(PRODUCTS_URL, json=PRODUCTS_RESPONSE)
    rates_url = (
        "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/"
        "E-1R-AGILE-24-10-01-L/standard-unit-rates/"
    )
    aioclient_mock.get(rates_url, json=RATES_RESPONSE)

    source = OctopusAgilePublicPriceSource(region="L")

    await source.async_get_forecast(hass)
    await source.async_get_forecast(hass)

    # products/ hit once (cached the second time), rates/ hit both times.
    products_calls = [c for c in aioclient_mock.mock_calls if c[1].host == "api.octopus.energy" and c[1].path == "/v1/products/"]
    assert len(products_calls) == 1
