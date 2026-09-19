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
    / "ventilation_session.py"
)
SPEC = spec_from_file_location("room_climate_ventilation_session", MODULE_PATH)
assert SPEC and SPEC.loader
ventilation_session = module_from_spec(SPEC)
sys.modules[SPEC.name] = ventilation_session
SPEC.loader.exec_module(ventilation_session)


def _room(*, beneficial: bool = True, available: bool = True):
    return SimpleNamespace(
        name="Küche",
        attributes={
            "inputs_available": available,
            "ventilation_beneficial": beneficial,
        },
    )


def test_session_counts_down_then_continues_while_beneficial():
    opened = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)
    sessions = {}
    windows = {"kitchen": ("on", opened)}
    rooms = {"kitchen": _room()}

    active = ventilation_session.update_sessions(
        sessions, windows, rooms, opened + timedelta(minutes=2), 7, 13.0
    )
    continued = ventilation_session.update_sessions(
        sessions, windows, rooms, opened + timedelta(minutes=8), 7, 13.0
    )

    assert active[0]["phase"] == "active"
    assert active[0]["remaining_minutes"] == 5
    assert continued[0]["phase"] == "continue"
    assert continued[0]["remaining_minutes"] == 12


def test_session_finishes_when_benefit_ends():
    opened = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)
    sessions = {}
    windows = {"kitchen": ("on", opened)}
    ventilation_session.update_sessions(
        sessions, windows, {"kitchen": _room()}, opened, 7, 13.0
    )

    result = ventilation_session.update_sessions(
        sessions,
        windows,
        {"kitchen": _room(beneficial=False)},
        opened + timedelta(minutes=4),
        7,
        13.0,
    )

    assert result[0]["phase"] == "finish"
    assert result[0]["remaining_minutes"] == 0


def test_cold_weather_caps_session_and_closing_clears_it():
    opened = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)
    sessions = {}
    rooms = {"kitchen": _room()}
    ventilation_session.update_sessions(
        sessions, {"kitchen": ("on", opened)}, rooms, opened, 12, 2.0
    )

    finished = ventilation_session.update_sessions(
        sessions,
        {"kitchen": ("on", opened)},
        rooms,
        opened + timedelta(minutes=10),
        12,
        2.0,
    )
    closed = ventilation_session.update_sessions(
        sessions,
        {"kitchen": ("off", opened + timedelta(minutes=11))},
        rooms,
        opened + timedelta(minutes=11),
        12,
        2.0,
    )

    assert finished[0]["phase"] == "finish"
    assert sessions == {}
    assert closed == []
