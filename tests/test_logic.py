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
    try:  # Use the real Home Assistant when it is installed (integration tests).
        import homeassistant.util.dt  # noqa: F401

        return
    except ImportError:
        pass
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
    # Living-room maximum is 55 %, lowered to 51.7 % at 4 °C outside.
    assert _evaluate(_metrics(21.0, 51.0, window="off"), weather).ventilate_now is False
    assert _evaluate(_metrics(21.0, 51.0, window="on"), weather).close_window is False
    assert _evaluate(_metrics(21.0, 47.0, window="on"), weather).close_window is True


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


def test_bedroom_slightly_above_profile_is_not_ideal():
    """Regression: a 22 °C bedroom (profile up to 20 °C) scored 100 with comfort sensors."""
    metrics = {
        "temperature": 22.2,
        "humidity": 49.0,
        "scharlau": "comfortable",
        "humidex": "comfortable",
        "simmer": "comfortable",
        "dewpoint": "very_comfortable",
    }
    assert logic.calculate_score({"room_type": "bedroom"}, metrics) < 90
    assert logic.calculate_score({"room_type": "living"}, {**metrics, "temperature": 21.4}) == 100


def test_mold_risk_on_cold_walls():
    risk = logic.get_mold_risk(20.0, 60.0, 0.0, 0.7)
    assert risk["level"] == "high"
    assert risk["surface_temperature"] == 14.0
    assert risk["surface_humidity"] > 80
    assert logic.get_mold_risk(21.0, 45.0, 10.0, 0.7)["level"] == "low"
    # Better insulated walls stay warmer.
    assert logic.get_mold_risk(20.0, 60.0, 0.0, 0.8)["surface_humidity"] < risk["surface_humidity"]


def test_mold_risk_makes_drying_worthwhile_below_humidity_maximum():
    # 54 % is below the living room maximum of 55 %, but the walls are cold.
    weather = _weather(-2.0, 85.0)
    result = _evaluate(_metrics(20.0, 54.0), weather)
    assert result.mold_risk is True
    assert result.ventilate_now is True
    assert "Schimmelrisiko" in result.mold_risk_reason
    assert result.attributes["notification_flags"]["mold_risk"] is True


def test_open_window_with_heating_on_suggests_turning_it_down():
    metrics = _metrics(21.0, 50.0, window="on")
    metrics["climate"] = {"state": "heat", "hvac_action": "heating", "target_temperature": 21, "current_temperature": 20.5}
    result = _evaluate(metrics, _weather(5.0, 80.0))
    assert result.reduce_heating is True
    assert "Fenster offen" in result.reduce_heating_reason


def test_frost_protection_is_not_reported_as_heating():
    metrics = _metrics(21.0, 50.0, window="on")
    metrics["climate"] = {"state": "heat", "hvac_action": "idle", "target_temperature": 7, "current_temperature": 20.5}
    assert _evaluate(metrics, _weather(5.0, 80.0)).reduce_heating is False


def test_heating_thermostat_blocks_cooling_advice_on_mild_days():
    # 16 °C outside is above the heating-season threshold, but the thermostat
    # is actually heating, so ventilating to cool would waste energy.
    metrics = _metrics(25.0, 45.0)
    metrics["climate"] = {"state": "heat", "hvac_action": "heating", "target_temperature": 26, "current_temperature": 25}
    result = _evaluate(metrics, _weather(16.0, 60.0))
    assert result.ventilate_now is False
    assert "Heizung läuft" in result.attributes["cooling_advice"]
    assert result.reduce_heating is True


# --- Fresh air (CO2 sensor optional) ---------------------------------------

def _fresh(**overrides):
    params = {
        "room_type": "living",
        "co2": None,
        "window_open": False,
        "window_open_minutes": None,
        "hours_since_aired": None,
        "local_hour": 12,
        "outside_temp": 4.0,
        "inside_temp": 21.0,
        "too_humid_outside": False,
        "occupied": None,
    }
    params.update(overrides)
    return logic.evaluate_fresh_air(**params)


def test_co2_moderate_and_strong_levels():
    assert _fresh(co2=700)["active"] is False
    moderate = _fresh(co2=1100)
    assert moderate["active"] is True and moderate["level"] == "short" and moderate["strong"] is False
    strong = _fresh(co2=1600)
    assert strong["level"] == "recommended" and strong["strong"] is True
    assert strong["duration"] > moderate["duration"]


def test_co2_hysteresis_while_window_is_open():
    assert _fresh(co2=900)["active"] is False
    assert _fresh(co2=900, window_open=True)["active"] is True
    assert _fresh(co2=700, window_open=True)["active"] is False


def test_co2_overrides_good_outside_conditions_only_when_strong():
    assert _fresh(co2=1100, too_humid_outside=True)["active"] is False
    assert _fresh(co2=1600, too_humid_outside=True)["active"] is True
    assert _fresh(co2=1100, outside_temp=30.0, inside_temp=24.0)["active"] is False


def test_time_based_reminder_without_sensor():
    assert _fresh(hours_since_aired=2.0)["active"] is False
    reminder = _fresh(hours_since_aired=6.0)
    assert reminder["active"] is True and reminder["source"] == "time"
    # Not at night, not in bathroom/basement, not while unknown.
    assert _fresh(hours_since_aired=6.0, local_hour=2)["active"] is False
    assert _fresh(hours_since_aired=6.0, room_type="bathroom")["active"] is False
    assert _fresh(hours_since_aired=None)["active"] is False


def test_time_based_airing_ends_after_planned_duration():
    assert _fresh(window_open=True, window_open_minutes=2.0)["active"] is True
    assert _fresh(window_open=True, window_open_minutes=30.0)["active"] is False


def test_room_recommends_fresh_air_in_winter_without_humidity_or_cooling_reason():
    weather = _weather(4.0, 80.0)
    quiet = _evaluate(_metrics(21.0, 45.0), weather)
    assert quiet.ventilate_now is False

    stale_air = _evaluate(_metrics(21.0, 45.0, co2=1500.0), weather)
    assert stale_air.ventilate_now is True
    assert stale_air.attributes["fresh_air_needed"] is True
    assert stale_air.attributes["fresh_air_source"] == "co2"
    assert stale_air.recommendation.startswith("Frischluft: Fenster öffnen")
    assert "geschlossen halten" not in stale_air.recommendation

    # The same room with the window open keeps the advice until CO2 drops.
    opened = _evaluate(_metrics(21.0, 45.0, window="on", co2=1500.0), weather)
    assert opened.close_window is False
    aired = _evaluate(_metrics(21.0, 45.0, window="on", co2=600.0), weather)
    assert aired.close_window is True


def test_room_without_window_sensor_still_uses_measured_co2():
    room = {k: v for k, v in ROOM.items() if k not in {"window"}}
    weather = _weather(4.0, 80.0)
    result = _evaluate(_metrics(21.0, 45.0, window=None, co2=1500.0), weather, room)
    assert result.ventilate_now is True


def test_room_time_reminder_requires_window_sensor(monkeypatch):
    noon = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(logic.dt_util, "now", lambda: noon)
    weather = _weather(4.0, 80.0)
    with_sensor = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0), weather)
    assert with_sensor.attributes["fresh_air_needed"] is True
    assert with_sensor.attributes["fresh_air_source"] == "time"
    assert with_sensor.ventilate_now is True
    room = {k: v for k, v in ROOM.items() if k not in {"window"}}
    without = _evaluate(_metrics(21.0, 45.0, window=None, hours_since_aired=7.0), weather, room)
    assert without.attributes["fresh_air_needed"] is False


# --- Presence sensor and push gating ----------------------------------------

def test_presence_sensor_silences_empty_rooms():
    assert _fresh(co2=1500, occupied=False)["active"] is False
    assert _fresh(hours_since_aired=7.0, occupied=False)["active"] is False
    assert _fresh(co2=1500, occupied=True)["active"] is True
    assert _fresh(co2=1500, occupied=None)["active"] is True
    # An already open window keeps its advice.
    assert _fresh(co2=1500, occupied=False, window_open=True)["active"] is True


def test_parse_presence():
    assert logic.parse_presence("on") is True
    assert logic.parse_presence("off") is False
    assert logic.parse_presence("unavailable") is None
    assert logic.parse_presence(None) is None


def test_time_reminder_only_pushes_when_room_is_occupied(monkeypatch):
    noon = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(logic.dt_util, "now", lambda: noon)
    weather = _weather(4.0, 80.0)
    flag = logic.NOTIFICATION_VENTILATE

    plain = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0), weather)
    assert plain.ventilate_now is True
    assert plain.attributes["notification_flags"][flag] is False

    occupied = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0, presence="on"), weather)
    assert occupied.attributes["notification_flags"][flag] is True
    assert "stoßlüften" in occupied.attributes["ventilate_advice"]

    empty = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0, presence="off"), weather)
    assert empty.ventilate_now is False


def test_co2_pushes_and_message_uses_only_the_active_reason():
    weather = _weather(4.0, 80.0)
    result = _evaluate(_metrics(21.0, 45.0, co2=1500.0), weather)
    assert result.attributes["notification_flags"][logic.NOTIFICATION_VENTILATE] is True
    advice = result.attributes["ventilate_advice"]
    assert advice.startswith("Fenster öffnen") and "geschlossen halten" not in advice


def test_household_presence_is_the_fallback_for_missing_room_sensor(monkeypatch):
    noon = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(logic.dt_util, "now", lambda: noon)
    weather = _weather(4.0, 80.0)
    away = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0, home_presence=False), weather)
    assert away.ventilate_now is False
    home = _evaluate(_metrics(21.0, 45.0, hours_since_aired=7.0, home_presence=True), weather)
    assert home.attributes["notification_flags"][logic.NOTIFICATION_VENTILATE] is True
    # A room sensor wins over the household sensor.
    room_empty = _evaluate(
        _metrics(21.0, 45.0, hours_since_aired=7.0, presence="off", home_presence=True), weather
    )
    assert room_empty.ventilate_now is False


# --- Seasonal humidity targets ----------------------------------------------

LIVING = {"humidity_min": 40, "humidity_max": 55, "temp_min": 16, "temp_max": 22}


def _targets(outside, room_type="living"):
    adjusted = logic.apply_seasonal_humidity(room_type, LIVING, outside)
    return adjusted["humidity_min"], adjusted["humidity_max"]


def test_targets_follow_the_outside_temperature():
    assert _targets(None) == (40, 55)
    assert _targets(15.0) == (40, 55)
    assert _targets(10.0) == (40, 53.5)
    assert _targets(0.0) == (40, 50.5)
    assert _targets(-10.0) == (36.0, 47.5)
    assert _targets(-30.0) == (36.0, 47.0)  # drops are capped
    assert _targets(25.0) == (40, 60)
    assert _targets(25.0, "basement")[1] == 55  # a basement gets no summer bonus


def test_upper_target_never_falls_below_the_lower_one():
    adjusted = logic.apply_seasonal_humidity(
        "living", {"humidity_min": 40, "humidity_max": 44, "temp_min": 16, "temp_max": 22}, -10.0
    )
    assert adjusted["humidity_max"] >= adjusted["humidity_min"] + 8


def test_season_names():
    assert logic.get_season(2.0) == "winter"
    assert logic.get_season(12.0) == "transition"
    assert logic.get_season(22.0) == "summer"
    assert logic.get_season(None) is None


def test_effective_outside_temperature_uses_the_next_24_hours():
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    forecast = [
        {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 10.0 - h * 0.5}
        for h in range(1, 13)
    ]
    effective = logic.get_effective_outside_temperature(10.0, forecast, now)
    assert effective is not None and effective < 10.0
    # Too few hourly entries (e.g. a daily forecast): the current value is used.
    assert logic.get_effective_outside_temperature(10.0, forecast[:2], now) == 10.0
    assert logic.get_effective_outside_temperature(None, [], now) is None


def test_cold_weather_makes_a_formerly_fine_humidity_worth_drying():
    # 52 % is fine in mild weather but too humid for a living room at 0 °C outside.
    mild = _evaluate(_metrics(21.0, 52.0), _weather(16.0, 60.0))
    assert mild.attributes["comfort_humidity_max"] == 55
    cold = _evaluate(_metrics(21.0, 52.0), _weather(0.0, 70.0))
    assert cold.attributes["comfort_humidity_max"] == 50.5
    assert cold.attributes["humidity_target_season"] == "winter"
    assert cold.attributes["comfort_humidity_base_max"] == 55
    assert cold.ventilate_now is True and mild.ventilate_now is False
