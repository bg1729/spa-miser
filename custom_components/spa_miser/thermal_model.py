"""Lumped-capacitance thermal model of the hot tub.

    C * dT/dt = -(U0 + U1*wind) * (T - T_ambient) + P_heater * eta

Discretised hourly this is linear in the unknowns a=U0/C, b=U1/C, c=eta/C, so
it's fit with an ordinary least-squares solve rather than anything heavier.
Only the ratios a/b/c are identifiable from temperature data alone; to turn
`c` into a human-meaningful "thermal mass" we assume a resistive heater
efficiency (~95% of electrical power reaches the water) — good enough for a
diagnostic sensor, not a claim of precise calibration.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ASSUMED_HEATER_EFFICIENCY = 0.95

# Specific heat capacity of water: 4186 J/(kg*C), converted to kWh/(kg*C).
# Used only to turn the fitted thermal mass into a volume sanity check - not
# part of the fit itself.
SPECIFIC_HEAT_WATER_KWH_PER_KG_C = 4186.0 / 3_600_000.0
# Water density is ~992 kg/m3 at typical spa temperatures (35-40C) vs 1000
# at 4C, a <1% difference - ignored here since this is a rough sanity check,
# not a calibration input.
WATER_DENSITY_KG_PER_LITER = 1.0

# Guard against degenerate fits (e.g. too little data, or a period with no
# temperature variation) producing nonsensical coefficients.
MIN_SAMPLES_FOR_FIT = 24
MAX_LOSS_COEFFICIENT = 2.0  # °C lost per hour per °C of delta-T would be absurd


@dataclass(frozen=True, slots=True)
class HourlySample:
    """One hour of aligned recorder-statistics data."""

    water_temp_c: float
    delta_temp_c: float  # water_temp_c at end of hour minus this sample's water_temp_c
    ambient_temp_c: float
    wind_speed_ms: float
    heater_power_kw: float


@dataclass(frozen=True, slots=True)
class ThermalModelParams:
    loss_coefficient: float  # a: °C/h per °C of (water - ambient)
    wind_coefficient: float  # b: additional a per m/s of wind
    input_coefficient: float  # c: °C/h per kW of heater input
    thermal_mass_kwh_per_c: float | None  # eta / c, None if c is ~0
    r_squared: float
    n_samples: int


def fit(samples: list[HourlySample]) -> ThermalModelParams | None:
    """Fit model coefficients from a batch of hourly samples.

    Returns None if there isn't enough usable data to fit confidently.
    """
    if len(samples) < MIN_SAMPLES_FOR_FIT:
        return None

    delta_t = np.array([s.water_temp_c - s.ambient_temp_c for s in samples])
    wind_delta_t = np.array(
        [s.wind_speed_ms * (s.water_temp_c - s.ambient_temp_c) for s in samples]
    )
    power = np.array([s.heater_power_kw for s in samples])
    target = np.array([s.delta_temp_c for s in samples])

    design = np.column_stack([-delta_t, -wind_delta_t, power])

    coeffs, residuals, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    a, b, c = coeffs

    predicted = design @ coeffs
    ss_res = float(np.sum((target - predicted) ** 2))
    ss_tot = float(np.sum((target - np.mean(target)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    if not np.isfinite([a, b, c]).all():
        return None
    a = float(np.clip(a, 0.0, MAX_LOSS_COEFFICIENT))
    b = float(b)
    c = float(c)

    thermal_mass = ASSUMED_HEATER_EFFICIENCY / c if c > 1e-6 else None

    return ThermalModelParams(
        loss_coefficient=a,
        wind_coefficient=b,
        input_coefficient=c,
        thermal_mass_kwh_per_c=thermal_mass,
        r_squared=r_squared,
        n_samples=len(samples),
    )


def estimated_volume_liters(params: ThermalModelParams) -> float | None:
    """Back out an implied water volume from the fitted thermal mass.

    Not used by the model itself - purely a sanity check: thermal_mass_kwh_per_c
    is the tub's fitted heat capacity, so dividing by water's specific heat
    gives an implied mass (~volume) that should land somewhere near the tub's
    actual rated capacity if the fit is trustworthy. A wildly off value (e.g.
    a fraction of, or many times, the real capacity) is a sign the fit itself
    is bad, independent of r_squared.
    """
    if params.thermal_mass_kwh_per_c is None:
        return None
    mass_kg = params.thermal_mass_kwh_per_c / SPECIFIC_HEAT_WATER_KWH_PER_KG_C
    return mass_kg / WATER_DENSITY_KG_PER_LITER


def predict_trajectory(
    params: ThermalModelParams,
    start_temp_c: float,
    ambient_temps_c: list[float],
    wind_speeds_ms: list[float],
    heater_power_kw: list[float],
    dt_hours: float = 1.0,
) -> list[float]:
    """Forward-simulate water temperature using Euler integration.

    `ambient_temps_c`, `wind_speeds_ms`, and `heater_power_kw` must be the
    same length; returns a list of the same length, one predicted temperature
    per step (the temperature *at the end of* each step).
    """
    temp = start_temp_c
    trajectory: list[float] = []
    for ambient, wind, power in zip(
        ambient_temps_c, wind_speeds_ms, heater_power_kw, strict=True
    ):
        loss_rate = (params.loss_coefficient + params.wind_coefficient * wind) * (
            temp - ambient
        )
        gain_rate = params.input_coefficient * power
        temp = temp + (gain_rate - loss_rate) * dt_hours
        trajectory.append(temp)
    return trajectory
