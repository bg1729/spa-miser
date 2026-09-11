"""Unit tests for the pure-python thermal model (no Home Assistant dependency)."""
from __future__ import annotations

import math

import pytest

from custom_components.spa_miser.thermal_model import HourlySample, fit

TRUE_LOSS = 0.06
TRUE_WIND = 0.015
TRUE_INPUT = 0.45  # degC/h per kW


def _generate_samples(n: int) -> list[HourlySample]:
    """Simulate a noiseless trajectory under the model's own equations."""
    samples: list[HourlySample] = []
    temp = 38.0
    for i in range(n):
        ambient = 10.0 + 5.0 * math.sin(i / 12.0)
        wind = 2.0 + 1.5 * math.sin(i / 7.0 + 1.0)
        power = 3.0 if i % 4 == 0 else 0.0  # heater cycles on one hour in four

        delta = -(TRUE_LOSS + TRUE_WIND * wind) * (temp - ambient) + TRUE_INPUT * power
        samples.append(
            HourlySample(
                water_temp_c=temp,
                delta_temp_c=delta,
                ambient_temp_c=ambient,
                wind_speed_ms=wind,
                heater_power_kw=power,
            )
        )
        temp += delta
    return samples


def test_fit_recovers_known_coefficients_from_noiseless_data():
    samples = _generate_samples(200)

    params = fit(samples)

    assert params is not None
    assert params.loss_coefficient == pytest.approx(TRUE_LOSS, rel=1e-3, abs=1e-4)
    assert params.wind_coefficient == pytest.approx(TRUE_WIND, rel=1e-3, abs=1e-4)
    assert params.input_coefficient == pytest.approx(TRUE_INPUT, rel=1e-3, abs=1e-4)
    assert params.r_squared > 0.999
    assert params.thermal_mass_kwh_per_c is not None
    assert params.thermal_mass_kwh_per_c > 0


def test_fit_returns_none_with_too_few_samples():
    samples = _generate_samples(5)

    assert fit(samples) is None


def test_fit_clips_pathological_loss_coefficient():
    # Degenerate/contradictory data (temperature moving away from ambient
    # despite no heater input) would otherwise push the least-squares
    # solution to a negative or absurd loss coefficient.
    samples = [
        HourlySample(
            water_temp_c=30.0,
            delta_temp_c=5.0,  # heating up despite ambient below water temp
            ambient_temp_c=10.0,
            wind_speed_ms=0.0,
            heater_power_kw=0.0,
        )
        for _ in range(30)
    ]

    params = fit(samples)

    assert params is not None
    assert 0.0 <= params.loss_coefficient <= 2.0
