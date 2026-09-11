"""Regression test for the entity-picker "No items available" bug.

HA's EntitySelector runs its config through voluptuous on construction,
which calls cv.ensure_list() on device_class - and cv.ensure_list(None) is
[], not "no filter". Passing device_class=None explicitly therefore
serializes as device_class: [], which matches zero entities in the picker
regardless of what actually exists. Assert directly against the selector's
own serialized config (the same shape sent to the frontend) so this can't
silently regress again.
"""
from __future__ import annotations

from custom_components.spa_miser.config_flow import STEP_OCTOPUS_SCHEMA, STEP_USER_SCHEMA
from custom_components.spa_miser.const import (
    CONF_CLIMATE_ENTITY,
    CONF_HEATING_STATE_ENTITY,
    CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY,
    CONF_POWER_ENTITY,
    CONF_WATER_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
)


def _selector_for(schema, conf_key):
    for key, value in schema.schema.items():
        if str(key) == conf_key:
            return value
    raise AssertionError(f"{conf_key} not found in schema")


def test_unfiltered_fields_omit_device_class_key():
    for conf_key in (CONF_CLIMATE_ENTITY, CONF_HEATING_STATE_ENTITY, CONF_WEATHER_ENTITY):
        sel = _selector_for(STEP_USER_SCHEMA, conf_key)
        assert "device_class" not in sel.config, (
            f"{conf_key}: device_class must be omitted, not None "
            f"(config={sel.config!r})"
        )
        assert sel.config["domain"], f"{conf_key}: domain must be non-empty"


def test_octopus_event_selector_omits_device_class():
    sel = _selector_for(STEP_OCTOPUS_SCHEMA, CONF_OCTOPUS_CURRENT_DAY_RATES_ENTITY)
    assert "device_class" not in sel.config
    assert sel.config["domain"] == ["event"]


def test_filtered_fields_have_non_empty_device_class():
    for conf_key, expected_domain, expected_device_class in (
        (CONF_WATER_TEMP_SENSOR, "sensor", "temperature"),
        (CONF_POWER_ENTITY, "sensor", "power"),
    ):
        sel = _selector_for(STEP_USER_SCHEMA, conf_key)
        assert sel.config["domain"] == [expected_domain]
        assert sel.config["device_class"] == [expected_device_class]
