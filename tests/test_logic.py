"""Tests for the per-room decision logic.

``logic.py`` imports ``homeassistant.util.dt`` and sibling modules, so the
package is loaded under a synthetic name with a minimal Home Assistant stub.
This keeps the tests runnable without installing Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
import types

PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "room_climate"


def _install_homeassistant_stub() -> None:
    if "homeassistant.util.dt" in sys.modules:
        return
    homeassistant = types.ModuleType("homeassistant")
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime.now(timezone.utc)
    dt.as_local = lambda value: value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    homeassistant.util = util
    util.dt = dt
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.util", util)
    sys.modules.setdefault("homeassistant.util.dt", dt)


def _load(module: str):
    _install_homeassistant_stub()
    package_name = "room_climate_under_test"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.{module}")


logic = _load("logic")
house_ventilation = _load("house_ventilation")
ventilation_session = _load("ventilation_session")

ROOM = {
    "id": "wohnzimmer",
    "name": "Wohnzimmer",
    "room_type": "living",
    "window": "binary_sensor.wohnzimmer_fenster",
    "window_orientation": "S",
}
NO_SUN = {"azimuth": None, "elevation": None, "above_horizon": False}


def _weather(temperature: float, humidity: float) -> dict:
    return {
        "temperature": temperature,
        "humidity": humidity,
        "wind_speed": 5.0,
        "wind_bearing": 0.0,
        "cloud_coverage": 90.0,
        "sun_azimuth": None,
    }


def _metrics(temperature: float, humidity: float, window: str = "off", **extra) -> dict:
    metrics = {
        "temperature": temperature,
        "humidity": humidity,
        "inside_absolute_humidity": house_ventilation.absolute_humidity(temperature, humidity),
        "window": window,
        "inputs_available": True,
        "data_quality": "good",
    }
    metrics.update(extra)
    return metrics


def _evaluate(metrics: dict, weather: dict, room: dict | None = None):
    outside_abs = house_ventilation.absolute_humidity(weather["temperature"], weather["humidity"])
    return logic.evaluate_room(room or ROOM, metrics, outside_abs, weather, NO_SUN, [])


def test_house_and_room_agree_on_cooling():
    """A house-wide cooling advice must not be rejected by the room once opened."""
    weather = _weather(18.0, 60.0)
    closed = _evaluate(_metrics(25.5, 50.0), weather)
    house = house_ventilation.evaluate_house_ventilation(
        [ROOM], {"wohnzimmer": closed}, closed.attributes["outside_absolute_humidity"], weather, []
    )
    assert closed.ventilate_now is True
    assert house.mode == "shock_ventilation_recommended"

    opened = _evaluate(_metrics(25.5, 50.0, window="on"), weather)
    assert opened.close_window is False
    now = datetime.now(timezone.utc)
    sessions = ventilation_session.update_sessions(
        {}, {"wohnzimmer": ("on", now - timedelta(minutes=1))}, {"wohnzimmer": opened}, now, 8, 18.0
    )
    assert sessions[0]["phase"] == "active"


def test_no_house_cooling_advice_when_rooms_are_comfortable():
    weather = _weather(18.0, 60.0)
    room = _evaluate(_metrics(21.0, 50.0), weather)
    house = house_ventilation.evaluate_house_ventilation(
        [ROOM], {"wohnzimmer": room}, room.attributes["outside_absolute_humidity"], weather, []
    )
    assert room.ventilate_now is False
    assert house.mode == "keep_windows_closed"


def test_stale_inputs_never_trigger_actions():
    result = _evaluate(
        _metrics(22.0, 68.0, inputs_available=False, data_quality="stale"),
        _weather(4.0, 80.0),
    )
    assert result.score is None
    assert result.ventilate_now is False
    assert result.close_window is False


def test_hysteresis_keeps_window_open_slightly_below_threshold():
    weather = _weather(4.0, 80.0)
    # Profile maximum for living rooms is 55 %.
    assert _evaluate(_metrics(21.0, 54.0, window="off"), weather).ventilate_now is False
    assert _evaluate(_metrics(21.0, 54.0, window="on"), weather).close_window is False
    assert _evaluate(_metrics(21.0, 50.0, window="on"), weather).close_window is True


def test_session_finish_forces_close_window():
    weather = _weather(4.0, 80.0)
    result = _evaluate(_metrics(21.0, 62.0, window="on"), weather)
    assert result.close_window is False  # outside air is still drier

    now = datetime.now(timezone.utc)
    sessions = ventilation_session.update_sessions(
        {}, {"wohnzimmer": ("on", now - timedelta(minutes=11))}, {"wohnzimmer": result}, now, 7, 4.0
    )
    assert sessions[0]["phase"] == "finish"
    result.apply_session_finish(sessions[0]["reason"])
    assert result.close_window is True
    assert result.attributes["notification_flags"]["close_window"] is True
    assert "Maximale Lüftungsdauer" in result.close_window_reason


def test_cooling_window_text_only_for_warm_rooms():
    result = _evaluate(_metrics(21.0, 50.0), _weather(4.0, 80.0))
    assert result.next_window is None


def test_basement_is_never_ventilated_for_cooling():
    basement = {**ROOM, "id": "keller", "name": "Keller", "room_type": "basement"}
    result = _evaluate(_metrics(22.0, 55.0), _weather(15.0, 60.0), basement)
    assert result.ventilate_now is False
    assert result.attributes["cooling_level"] == "neutral"


def test_score_without_comfort_sensors_reaches_ideal():
    assert logic.calculate_score({"room_type": "living"}, {"temperature": 21.0, "humidity": 48.0}) >= 90


def test_score_reflects_cold_and_dry_rooms():
    score = logic.calculate_score({"room_type": "kitchen"}, {"temperature": 16.0, "humidity": 30.0})
    assert score < 60


def test_score_renormalises_partial_comfort_sensors():
    metrics = {"temperature": 21.0, "humidity": 48.0, "dewpoint": "comfortable", "simmer": "comfortable"}
    assert logic.calculate_score({"room_type": "living"}, metrics) >= 90


def test_description_names_cold_and_dry_causes_neutrally():
    kitchen = {**ROOM, "id": "kueche", "name": "Küche", "room_type": "kitchen"}
    result = _evaluate(_metrics(16.0, 30.0), _weather(4.0, 80.0), kitchen)
    assert result.description.startswith("Küche:")
    assert "zu kühl" in result.description
    assert "zu trocken" in result.description


def test_humid_outside_cooling_text_matches_flag():
    # Warm room, outside cooler but much more humid -> no ventilation, and the
    # text must not tell the user to open the window.
    weather = _weather(20.0, 95.0)
    result = _evaluate(_metrics(25.0, 40.0), weather)
    assert result.ventilate_now is False
    assert result.attributes["cooling_level"] == "avoid"
    assert "geschlossen halten" in result.attributes["cooling_advice"]


def test_forecast_label_mentions_other_days():
    now = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)
    start = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
    label = house_ventilation.format_time_range(start, start + timedelta(hours=2), now)
    assert label == "morgen 06:00–08:00 Uhr"
    today = house_ventilation.format_time_range(now, now + timedelta(hours=1), now)
    assert today == "20:00–21:00 Uhr"


def test_heated_rooms_in_autumn_get_no_cooling_advice():
    """Regression: 22 °C kitchen/bedroom at 7 °C outside asked for cooling."""
    weather = _weather(7.4, 85.0)
    for room_type in ("kitchen", "bedroom"):
        room = {**ROOM, "room_type": room_type}
        result = _evaluate(_metrics(22.6, 48.0), weather, room)
        assert result.ventilate_now is False, room_type
        assert result.attributes["cooling_level"] == "neutral"


def test_warm_room_in_cold_weather_is_told_to_reduce_heating():
    result = _evaluate(_metrics(25.0, 45.0), _weather(5.0, 80.0))
    assert result.ventilate_now is False
    assert "Heizung reduzieren" in result.attributes["cooling_advice"]
    assert result.next_window is None


def test_summer_night_cooling_still_recommended():
    bedroom = {**ROOM, "room_type": "bedroom"}
    result = _evaluate(_metrics(26.0, 50.0), _weather(16.0, 60.0), bedroom)
    assert result.ventilate_now is True
