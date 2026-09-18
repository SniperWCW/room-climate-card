from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "room_climate"
    / "house_ventilation.py"
)
SPEC = spec_from_file_location("room_climate_house_ventilation", MODULE_PATH)
assert SPEC and SPEC.loader
house_ventilation = module_from_spec(SPEC)
sys.modules[SPEC.name] = house_ventilation
SPEC.loader.exec_module(house_ventilation)


def _room_result(
    name: str,
    *,
    absolute_humidity: float = 11.0,
    temperature: float = 25.0,
    ventilate_now: bool = True,
    window_open: bool = False,
):
    return SimpleNamespace(
        name=name,
        ventilate_now=ventilate_now,
        window_open=window_open,
        attributes={
            "inputs_available": True,
            "inside_absolute_humidity": absolute_humidity,
            "temperature": temperature,
            "ventilation_beneficial": ventilate_now or window_open,
        },
    )


def _configured_rooms():
    return [
        {
            "id": "north",
            "name": "Nordzimmer",
            "window": "binary_sensor.north",
            "window_orientation": "N",
        },
        {
            "id": "south",
            "name": "Südzimmer",
            "window": "binary_sensor.south",
            "window_orientation": "S",
        },
    ]


def test_absolute_humidity_uses_temperature_and_relative_humidity():
    result = house_ventilation.absolute_humidity(20.0, 50.0)
    assert result is not None
    assert round(result, 2) == 8.64


def test_recommends_cross_ventilation_for_opposing_windows():
    rooms = {
        "north": _room_result("Nordzimmer"),
        "south": _room_result("Südzimmer", ventilate_now=False),
    }
    result = house_ventilation.evaluate_house_ventilation(
        _configured_rooms(),
        rooms,
        8.0,
        {"temperature": 20.0, "wind_speed": 8.0},
        [],
        now=datetime(2026, 9, 18, 18, tzinfo=timezone.utc),
    )

    assert result.mode == "cross_ventilation_recommended"
    assert result.recommended_orientations == ["Nord", "Süd"]
    assert result.duration_minutes is not None


def test_detects_active_cross_ventilation():
    rooms = {
        "north": _room_result("Nordzimmer", ventilate_now=False, window_open=True),
        "south": _room_result("Südzimmer", ventilate_now=False, window_open=True),
    }
    result = house_ventilation.evaluate_house_ventilation(
        _configured_rooms(),
        rooms,
        8.0,
        {"temperature": 20.0, "wind_speed": 8.0},
        [],
        now=datetime(2026, 9, 18, 18, tzinfo=timezone.utc),
    )

    assert result.mode == "cross_ventilation_active"
    assert result.open_rooms == ["Nordzimmer", "Südzimmer"]


def test_dry_outside_air_alone_does_not_trigger_ventilation():
    rooms = {
        "north": _room_result("Nordzimmer", ventilate_now=False, temperature=21.0),
        "south": _room_result("Südzimmer", ventilate_now=False, temperature=21.0),
    }
    result = house_ventilation.evaluate_house_ventilation(
        _configured_rooms(),
        rooms,
        6.0,
        {"temperature": 20.0, "wind_speed": 8.0},
        [],
        now=datetime(2026, 9, 18, 18, tzinfo=timezone.utc),
    )

    assert result.mode == "keep_windows_closed"


def test_recommends_closing_open_windows_without_benefit():
    north = _room_result(
        "Nordzimmer", ventilate_now=False, window_open=True, temperature=21.0
    )
    north.attributes["ventilation_beneficial"] = False
    south = _room_result("Südzimmer", ventilate_now=False, temperature=21.0)
    south.attributes["ventilation_beneficial"] = False
    result = house_ventilation.evaluate_house_ventilation(
        _configured_rooms(),
        {"north": north, "south": south},
        11.2,
        {"temperature": 23.0, "wind_speed": 8.0},
        [],
        now=datetime(2026, 9, 18, 18, tzinfo=timezone.utc),
    )

    assert result.mode == "close_windows"
    assert result.open_rooms == ["Nordzimmer"]


def test_forecast_builds_best_contiguous_window():
    now = datetime(2026, 9, 18, 18, tzinfo=timezone.utc)
    forecast = [
        {
            "datetime": (now + timedelta(hours=hour)).isoformat(),
            "temperature": temperature,
            "humidity": humidity,
            "condition": "partlycloudy",
            "precipitation": 0,
        }
        for hour, temperature, humidity in (
            (1, 24.0, 65.0),
            (2, 19.0, 50.0),
            (3, 18.0, 50.0),
            (4, 22.0, 80.0),
        )
    ]
    rooms = {
        "north": _room_result("Nordzimmer", absolute_humidity=11.5, temperature=25.0),
        "south": _room_result("Südzimmer", absolute_humidity=11.5, temperature=25.0),
    }
    result = house_ventilation.evaluate_house_ventilation(
        _configured_rooms(),
        rooms,
        10.0,
        {"temperature": 23.0, "wind_speed": 8.0},
        forecast,
        now=now,
    )

    assert result.next_window.available is True
    assert result.next_window.start == (now + timedelta(hours=2)).isoformat()
    assert result.next_window.end == (now + timedelta(hours=4)).isoformat()
    assert result.next_window.expected_absolute_humidity is not None
