"""SpaControl's manual-override detection must not fire for an entity
recovering from unavailable/unknown - e.g. an MQTT entity reconnecting
after a HA restart, which typically rebuilds its state through several
incremental changes (state, then preset_mode, then temperature). Without
this, every restart re-armed a fresh override_minutes-long pause, even
though nobody touched the tub.
"""
from __future__ import annotations

from homeassistant.core import Context, HomeAssistant

from custom_components.spa_miser.spa_control import SpaControl

CLIMATE_ENTITY = "climate.balboa_spa"


async def test_reconnecting_from_unavailable_does_not_trigger_override(
    hass: HomeAssistant,
):
    hass.states.async_set(
        CLIMATE_ENTITY,
        "heat",
        {"current_temperature": 37.0, "temperature": 37.0, "preset_mode": "High Range"},
    )
    await hass.async_block_till_done()

    control = SpaControl(hass, CLIMATE_ENTITY, override_minutes=120)
    control.async_setup()

    # Simulates a real MQTT-entity restart sequence: briefly unavailable,
    # then rebuilt through several incremental changes before settling
    # back on the exact same values as before.
    hass.states.async_set(CLIMATE_ENTITY, "unavailable", {})
    await hass.async_block_till_done()
    hass.states.async_set(CLIMATE_ENTITY, "unknown", {})
    await hass.async_block_till_done()
    hass.states.async_set(CLIMATE_ENTITY, "heat", {"preset_mode": "High Range"})
    await hass.async_block_till_done()
    hass.states.async_set(
        CLIMATE_ENTITY,
        "heat",
        {"current_temperature": 37.0, "temperature": 37.0, "preset_mode": "High Range"},
    )
    await hass.async_block_till_done()

    assert control.is_manually_overridden is False

    control.async_unload()


async def test_a_real_external_change_still_triggers_override(hass: HomeAssistant):
    hass.states.async_set(
        CLIMATE_ENTITY,
        "heat",
        {"current_temperature": 37.0, "temperature": 37.0, "preset_mode": "High Range"},
    )
    await hass.async_block_till_done()

    control = SpaControl(hass, CLIMATE_ENTITY, override_minutes=120)
    control.async_setup()

    # A genuine change between two already-available states (e.g. someone
    # used the thermostat card directly), tagged with a context SpaControl
    # doesn't recognize as its own.
    hass.states.async_set(
        CLIMATE_ENTITY,
        "heat",
        {"current_temperature": 37.0, "temperature": 38.0, "preset_mode": "High Range"},
        context=Context(),
    )
    await hass.async_block_till_done()

    assert control.is_manually_overridden is True

    control.async_unload()
