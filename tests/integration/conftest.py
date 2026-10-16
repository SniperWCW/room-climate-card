from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.room_climate.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    yield


@pytest.fixture
def web_stack(hass):
    """Pretend http/frontend are ready; the card registration is not under test."""
    hass.config.components.update({"http", "frontend"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with (
        patch("custom_components.room_climate.add_extra_js_url"),
        patch("custom_components.room_climate.remove_extra_js_url"),
    ):
        yield


ROOM = {
    "id": "wohnzimmer",
    "name": "Wohnzimmer",
    "room_type": "living",
    "window_orientation": "S",
    "temperature": "sensor.wohnzimmer_temperatur",
    "humidity": "sensor.wohnzimmer_luftfeuchtigkeit",
    "inside_absolute_humidity": "",
    "window": "binary_sensor.wohnzimmer_fenster",
    "cover": "",
    "climate": "climate.wohnzimmer",
    "humidex_value": "",
    "scharlau": "",
    "humidex": "",
    "simmer": "",
    "dewpoint": "",
    "notifications_enabled": False,
}


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Room Climate",
        unique_id=DOMAIN,
        data={
            "title": "Room Climate",
            "outside_absolute_humidity": "",
            "outside_weather": "weather.zuhause",
            "sun_entity": "",
            "notification_enabled": False,
            "notify_service": "",
            "notification_cooldown": 120,
            "wall_quality": "standard",
            "rooms": [dict(ROOM)],
        },
    )
    entry.add_to_hass(hass)
    return entry


def set_room_states(hass, *, temperature=21.0, humidity=50.0, window="off", hvac_action="idle", target=21.0):
    hass.states.async_set(
        "sensor.wohnzimmer_temperatur",
        str(temperature),
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set(
        "sensor.wohnzimmer_luftfeuchtigkeit",
        str(humidity),
        {"device_class": "humidity", "unit_of_measurement": "%"},
    )
    hass.states.async_set("binary_sensor.wohnzimmer_fenster", window)
    hass.states.async_set(
        "climate.wohnzimmer",
        "heat",
        {"hvac_action": hvac_action, "temperature": target, "current_temperature": temperature},
    )


def set_weather(hass, *, temperature=5.0, humidity=80.0, unit="°C", wind=10.0, wind_unit="km/h"):
    hass.states.async_set(
        "weather.zuhause",
        "cloudy",
        {
            "temperature": temperature,
            "temperature_unit": unit,
            "humidity": humidity,
            "wind_speed": wind,
            "wind_speed_unit": wind_unit,
            "wind_bearing": 180,
            "cloud_coverage": 90,
        },
    )
