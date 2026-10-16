"""Config and options flow against a real Home Assistant."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, entity_registry as er

from custom_components.room_climate.const import DOMAIN

from .conftest import ROOM


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Room Climate", "notification_enabled": False, "notification_cooldown": 120, "wall_quality": "old"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["wall_quality"] == "old"
    assert result["data"]["rooms"] == []


def _add_detectable_room(hass):
    """An area with a temperature and humidity sensor that the import step offers."""
    area = ar.async_get(hass).async_create("Küche")
    registry = er.async_get(hass)
    for object_id, device_class, unit, value in (
        ("kuche_temperatur", "temperature", "°C", "21"),
        ("kuche_luftfeuchtigkeit", "humidity", "%", "50"),
    ):
        entry = registry.async_get_or_create("sensor", "test", object_id, suggested_object_id=object_id)
        registry.async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(entry.entity_id, value, {"device_class": device_class, "unit_of_measurement": unit})


async def test_manual_room_is_saved_while_detected_rooms_exist(hass, config_entry):
    """Regression: the manual form was routed to the import step and dropped."""
    _add_detectable_room(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "import_detected_rooms" in result["menu_options"]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_room_manual"})
    assert result["step_id"] == "add_room_manual"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Büro",
            "room_type": "office",
            "temperature": "sensor.buero_temperatur",
            "humidity": "sensor.buero_luftfeuchtigkeit",
            "notifications_enabled": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    rooms = config_entry.options["rooms"]
    office = next(room for room in rooms if room["name"] == "Büro")
    # Optional entities not chosen are stored as "" (no auto-detection).
    assert office["window"] == ""
    assert office["climate"] == ""


async def test_optional_entity_can_be_removed(hass, config_entry):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_room_select"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"id": ROOM["id"]})
    assert result["step_id"] == "edit_room"

    user_input = {
        "name": ROOM["name"],
        "room_type": ROOM["room_type"],
        "window_orientation": ROOM["window_orientation"],
        "temperature": ROOM["temperature"],
        "humidity": ROOM["humidity"],
        "climate": ROOM["climate"],
        "notifications_enabled": False,
    }  # window field cleared in the UI -> key missing
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    room = config_entry.options["rooms"][0]
    assert room["window"] == ""
    assert room["climate"] == "climate.wohnzimmer"
