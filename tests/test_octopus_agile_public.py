"""Tests for the public-API Octopus Agile price source (no account needed)."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.spa_miser.price_sources.octopus_agile_public import (
    PRODUCTS_URL,
    OctopusAgilePublicPriceSource,
)

PRODUCTS_RESPONSE = {
    "results": [
        {"code": "AGILE-23-12-06", "available_to": "2024-10-01T00:00:00Z"},
        {"code": "AGILE-24-10-01", "available_to": None},
        {"code": "VAR-22-11-01", "available_to": None},  # not an Agile product
        # A real, still-active Octopus product sharing the "AGILE-" prefix,
        # but a different tariff entirely (export, not import) - this is
        # exactly what a naive "pick the lexicographically greatest
        # AGILE-prefixed code" would wrongly select instead, since it sorts
        # after any AGILE-YY-MM-DD code (found live against the real API).
        {"code": "AGILE-OUTGOING-19-05-13", "available_to": None},
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

    before = dt_util.utcnow()
    slots = await source.async_get_forecast(hass)
    await hass.async_block_till_done()

    assert len(slots) == 2
    assert slots[0].price == 0.155  # pence -> pounds
    assert slots[1].price == -0.032
    assert aioclient_mock.call_count == 2

    rates_call = next(c for c in aioclient_mock.mock_calls if rates_url in str(c[1]))
    period_from = dt_util.parse_datetime(rates_call[1].query["period_from"])
    assert period_from is not None
    assert dt_util.as_local(period_from).date() == dt_util.as_local(before).date() - timedelta(
        days=2
    ), (
        "chart/dashboard rely on this reaching back far enough to show "
        "yesterday's prices even right after local midnight"
    )


async def test_excludes_agile_outgoing_and_other_non_import_products(hass, aioclient_mock):
    aioclient_mock.get(PRODUCTS_URL, json=PRODUCTS_RESPONSE)
    # If the outgoing product were wrongly selected, this is the rates URL
    # it would hit instead - asserting it's never called is as important as
    # asserting the correct URL is.
    wrong_rates_url = (
        "https://api.octopus.energy/v1/products/AGILE-OUTGOING-19-05-13/"
        "electricity-tariffs/E-1R-AGILE-OUTGOING-19-05-13-E/standard-unit-rates/"
    )
    correct_rates_url = (
        "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/"
        "E-1R-AGILE-24-10-01-E/standard-unit-rates/"
    )
    aioclient_mock.get(correct_rates_url, json=RATES_RESPONSE)

    source = OctopusAgilePublicPriceSource(region="E")
    slots = await source.async_get_forecast(hass)

    assert len(slots) == 2
    called_urls = {str(c[1]) for c in aioclient_mock.mock_calls}
    assert not any(wrong_rates_url in url for url in called_urls)


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
