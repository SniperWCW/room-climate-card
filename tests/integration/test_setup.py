"""Set up the integration in a real Home Assistant and check its entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.room_climate.const import DOMAIN, HOUSE_VENTILATION_MODES

from .conftest import set_room_states, set_weather


def _entity_id(hass, platform: str, unique_suffix: str, entry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{unique_suffix}")
    assert entity_id, f"{platform} {unique_suffix} not registered"
    return entity_id


async def test_setup_creates_entities_with_stable_states(hass, web_stack, config_entry):
    set_room_states(hass)
    set_weather(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    house = hass.states.get(_entity_id(hass, "sensor", "house_ventilation", config_entry))
    assert house.state in HOUSE_VENTILATION_MODES
    assert house.attributes["title"]

    overview = hass.states.get(_entity_id(hass, "sensor", "overview", config_entry))
    assert overview.state in {"hot", "summer", "mild", "cool"}

    score = hass.states.get(_entity_id(hass, "sensor", "wohnzimmer_score", config_entry))
    assert 0 <= int(score.state) <= 100
    assert score.attributes["room_name"] == "Wohnzimmer"
    assert score.attributes["inside_absolute_humidity_source"] == "calculated"
    assert score.attributes["outside_absolute_humidity_source"] == "calculated"
    assert score.attributes["climate_entity"] == "climate.wohnzimmer"

    for flag in ("ventilate_now", "close_window", "close_cover", "mold_risk", "reduce_heating"):
        assert hass.states.get(_entity_id(hass, "binary_sensor", f"wohnzimmer_{flag}", config_entry))

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_window_change_updates_immediately(hass, web_stack, config_entry):
    set_room_states(hass, hvac_action="heating")
    set_weather(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    reduce_heating = _entity_id(hass, "binary_sensor", "wohnzimmer_reduce_heating", config_entry)
    assert hass.states.get(reduce_heating).state == "off"

    hass.states.async_set("binary_sensor.wohnzimmer_fenster", "on")
    await hass.async_block_till_done()
    assert hass.states.get(reduce_heating).state == "on"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_weather_units_are_converted(hass, web_stack, config_entry):
    """41 °F outside is 5 °C; without conversion it would look like a hot day."""
    set_room_states(hass)
    set_weather(hass, temperature=41.0, unit="°F", wind=5.0, wind_unit="m/s")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    score = hass.states.get(_entity_id(hass, "sensor", "wohnzimmer_score", config_entry))
    assert round(score.attributes["outside_temperature"], 1) == 5.0
    assert round(score.attributes["outside_wind_speed"], 1) == 18.0

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_stale_values_do_not_trigger_actions(hass, web_stack, config_entry):
    set_room_states(hass, humidity="unavailable")
    set_weather(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    score = hass.states.get(_entity_id(hass, "sensor", "wohnzimmer_score", config_entry))
    assert score.state == "unknown"
    ventilate = hass.states.get(_entity_id(hass, "binary_sensor", "wohnzimmer_ventilate_now", config_entry))
    assert ventilate.state == "off"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_mold_risk_needs_to_persist_before_it_is_reported(hass, web_stack, config_entry, freezer):
    """A humidity peak after a shower must not raise the mould alarm right away."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    set_room_states(hass, temperature=20.0, humidity=62.0)
    set_weather(hass, temperature=-5.0, humidity=90.0)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mold = _entity_id(hass, "binary_sensor", "wohnzimmer_mold_risk", config_entry)
    score = _entity_id(hass, "sensor", "wohnzimmer_score", config_entry)
    assert hass.states.get(score).attributes["mold_risk"] == "high"
    assert hass.states.get(mold).state == "off"

    freezer.tick(timedelta(minutes=65))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(mold).state == "on"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
