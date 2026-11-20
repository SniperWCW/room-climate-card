"""Tests for the calculated comfort categories (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "room_climate_comfort",
    Path(__file__).parents[1] / "custom_components" / "room_climate" / "comfort.py",
)
comfort = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(comfort)


def test_dew_point_matches_reference_values():
    assert abs(comfort.dew_point(20.0, 50.0) - 9.3) < 0.2
    assert abs(comfort.dew_point(30.0, 70.0) - 23.9) < 0.3
    assert abs(comfort.dew_point(10.0, 100.0) - 10.0) < 0.1


def test_humidex_is_temperature_in_dry_air_and_higher_when_humid():
    assert comfort.humidex(20.0, 30.0) < 20.0
    assert 40 <= comfort.humidex(30.0, 70.0) <= 44


def test_heated_living_room_is_comfortable_everywhere():
    result = comfort.calculate_comfort(21.0, 48.0)
    assert result["dewpoint"] == "dry"
    assert result["humidex"] == "comfortable"
    assert result["simmer"] == "slightly_cool"
    assert result["scharlau"] == "comfortable"


def test_hot_humid_room_is_graded_up():
    result = comfort.calculate_comfort(32.0, 70.0)
    assert result["humidex"] in {"great_discomfort", "dangerous_discomfort"}
    assert result["simmer"] in {"extremely_warm", "danger_of_heatstroke"}
    assert result["dewpoint"] in {"quite_uncomfortable", "extremely_uncomfortable"}


def test_scharlau_is_outside_range_for_cold_or_dry_rooms():
    assert comfort.summer_scharlau_perception(16.0, 50.0) == "outside_calculable_range"
    assert comfort.summer_scharlau_perception(21.0, 25.0) == "outside_calculable_range"


def test_simmer_index_is_temperature_when_cool():
    assert comfort.summer_simmer_index(10.0, 50.0) == 10.0


def test_no_values_without_input():
    assert comfort.calculate_comfort(None, 50.0) == {}
    assert comfort.calculate_comfort(21.0, None) == {}
    assert comfort.calculate_comfort(21.0, 0.0) == {}


def test_humidity_above_100_is_clamped():
    assert comfort.calculate_comfort(21.0, 104.0) == comfort.calculate_comfort(21.0, 100.0)


def test_configured_sensor_wins_and_unavailable_is_replaced():
    metrics = {
        "temperature": 21.0,
        "humidity": 48.0,
        "scharlau": "highly_uncomfortable",
        "humidex": "unavailable",
        "simmer": None,
        "dewpoint": "comfortable",
        "humidex_value": None,
    }
    comfort.fill_comfort_metrics(metrics)
    assert metrics["scharlau"] == "highly_uncomfortable"
    assert metrics["dewpoint"] == "comfortable"
    assert metrics["humidex"] == "comfortable"
    assert metrics["simmer"] == "slightly_cool"
    assert metrics["comfort_source"] == "mixed"


def test_everything_is_calculated_without_sensors():
    metrics = {"temperature": 21.0, "humidity": 48.0}
    comfort.fill_comfort_metrics(metrics)
    assert metrics["comfort_source"] == "calculated"
    assert metrics["humidex_value"] is not None


def test_nothing_is_calculated_when_inputs_are_missing():
    metrics = {"temperature": None, "humidity": 48.0}
    comfort.fill_comfort_metrics(metrics)
    assert metrics["comfort_source"] == "sensor"
    assert metrics.get("dewpoint") is None
