"""Tests for push-notification handling and the persisted airing timer.

The coordinator needs Home Assistant, which is stubbed here so that the
notification rules can be tested without installing it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "room_climate"
UTC = timezone.utc


def _stub_module(name: str, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        if not hasattr(module, key):
            setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return sys.modules[name]


class _Generic:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, *args, **kwargs):
        pass


def _load_coordinator():
    for name in (
        "homeassistant",
        "homeassistant.helpers",
        "homeassistant.util",
    ):
        _stub_module(name)
    _stub_module("homeassistant.util.dt", now=lambda: datetime.now(UTC), as_local=lambda v: v)
    _stub_module("homeassistant.config_entries", ConfigEntry=_Generic)
    _stub_module("homeassistant.core", HomeAssistant=object)
    _stub_module("homeassistant.helpers.event", async_call_later=MagicMock(), async_track_state_change_event=MagicMock(), async_track_time_interval=MagicMock())
    _stub_module("homeassistant.helpers.storage", Store=_Generic)
    _stub_module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=_Generic)
    sys.modules["homeassistant.helpers"].device_registry = MagicMock()
    sys.modules["homeassistant.helpers"].entity_registry = MagicMock()
    package_name = "room_climate_notif_under_test"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.coordinator")


coordinator_module = _load_coordinator()
Coordinator = coordinator_module.RoomClimateCoordinator


class FakeDt:
    def __init__(self, now: datetime):
        self.current = now

    def now(self):
        return self.current

    def utcnow(self):
        return self.current

    @staticmethod
    def as_local(value):
        return value

    @staticmethod
    def parse_time(value):
        try:
            return datetime.strptime(value, "%H:%M:%S").time()
        except ValueError:
            return None

    @staticmethod
    def parse_datetime(value):
        return datetime.fromisoformat(value)


def _room(room_id="wohnzimmer", **flags):
    attrs = {
        "notification_flags": {"ventilate_now": flags.get("ventilate", False)},
        "ventilate_advice": "Fenster öffnen. Die Luft ist verbraucht.",
    }
    return SimpleNamespace(
        room_id=room_id,
        name="Wohnzimmer",
        notifications_enabled=True,
        close_window=flags.get("close_window", False),
        close_window_reason="Fenster schließen.",
        close_cover=flags.get("close_cover", False),
        mold_risk=flags.get("mold", False),
        mold_risk_reason="Schimmelrisiko.",
        ventilate_now=flags.get("ventilate", False),
        recommendation="",
        attributes=attrs,
    )


def _coordinator(now: datetime, **config):
    fake_dt = FakeDt(now)
    coordinator_module.dt_util = fake_dt
    coord = object.__new__(Coordinator)
    base = {"notify_service": "notify.mobile_app_test", "notification_cooldown": 120}
    base.update(config)
    coord.entry = SimpleNamespace(data=base, options={})
    coord.hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock()),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    coord.last_notification_at = {}
    coord.last_flag_state = {}
    coord._notification_store = SimpleNamespace(async_save=AsyncMock())
    coord._states = {}
    coord._get_state = lambda entity_id: coord._states.get(entity_id)
    return coord, fake_dt


def _run(coord, rooms, open_minutes=None):
    asyncio.run(coord._async_process_notifications(rooms, open_minutes))


def _calls(coord):
    return [call.args[2] for call in coord.hass.services.async_call.call_args_list]


NOON = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_push_is_sent_once_per_event():
    coord, _ = _coordinator(NOON)
    rooms = {"wohnzimmer": _room(ventilate=True)}
    _run(coord, rooms)
    _run(coord, rooms)
    assert len(_calls(coord)) == 1
    assert _calls(coord)[0]["message"] == "Fenster öffnen. Die Luft ist verbraucht."


def test_close_window_ignores_cooldown_of_earlier_push():
    coord, clock = _coordinator(NOON)
    open_for = {"wohnzimmer": 10}
    rooms = {"wohnzimmer": _room(close_window=True)}
    _run(coord, rooms, open_for)
    _run(coord, {"wohnzimmer": _room()}, open_for)
    clock.current += timedelta(minutes=30)  # well inside the 2 h cooldown
    _run(coord, rooms, open_for)
    assert len(_calls(coord)) == 2


def test_ventilate_still_respects_cooldown():
    coord, clock = _coordinator(NOON)
    _run(coord, {"wohnzimmer": _room(ventilate=True)})
    _run(coord, {"wohnzimmer": _room()})
    clock.current += timedelta(minutes=30)
    _run(coord, {"wohnzimmer": _room(ventilate=True)})
    assert len(_calls(coord)) == 1


def test_quiet_hours_hold_back_and_send_later():
    night = datetime(2026, 11, 2, 23, 30, tzinfo=UTC)
    coord, clock = _coordinator(night, quiet_hours_start="22:00:00", quiet_hours_end="07:00:00")
    rooms = {"wohnzimmer": _room(ventilate=True, close_cover=True)}
    _run(coord, rooms)
    assert _calls(coord) == []
    clock.current = datetime(2026, 11, 3, 7, 30, tzinfo=UTC)
    _run(coord, rooms)
    assert len(_calls(coord)) == 2


def test_first_mould_warning_ignores_quiet_hours_and_absence():
    night = datetime(2026, 11, 2, 23, 30, tzinfo=UTC)
    coord, _ = _coordinator(
        night, quiet_hours_start="22:00:00", quiet_hours_end="07:00:00", home_presence="person.steffen"
    )
    coord._states["person.steffen"] = "not_home"
    _run(coord, {"wohnzimmer": _room(mold=True)})
    assert len(_calls(coord)) == 1


def test_nobody_home_holds_back_ventilation_but_not_close_window():
    coord, _ = _coordinator(NOON, home_presence="binary_sensor.home")
    coord._states["binary_sensor.home"] = "off"
    rooms = {"wohnzimmer": _room(ventilate=True, close_window=True)}
    _run(coord, rooms, {"wohnzimmer": 10})
    messages = [call["message"] for call in _calls(coord)]
    assert messages == ["Fenster schließen."]
    coord._states["binary_sensor.home"] = "on"
    _run(coord, rooms, {"wohnzimmer": 10})
    assert len(_calls(coord)) == 2


def test_failed_send_is_retried_and_cooldown_not_set():
    coord, _ = _coordinator(NOON)
    coord.hass.services.async_call.side_effect = [RuntimeError("down"), None]
    rooms = {"wohnzimmer": _room(ventilate=True)}
    _run(coord, rooms)
    assert coord.last_notification_at == {}
    assert coord.last_flag_state.get("wohnzimmer:ventilate_now") is None
    _run(coord, rooms)
    assert "wohnzimmer:ventilate_now" in coord.last_notification_at


def test_mould_risk_is_repeated_daily():
    coord, clock = _coordinator(NOON)
    rooms = {"wohnzimmer": _room(mold=True)}
    _run(coord, rooms)
    clock.current += timedelta(hours=23)
    _run(coord, rooms)
    assert len(_calls(coord)) == 1
    clock.current += timedelta(hours=2)
    _run(coord, rooms)
    calls = _calls(coord)
    assert len(calls) == 2 and calls[1]["message"].startswith("Erinnerung:")


def test_tags_replace_and_clear_pushes():
    coord, _ = _coordinator(NOON, notification_tags=True)
    _run(coord, {"wohnzimmer": _room(ventilate=True)})
    sent = _calls(coord)[0]
    assert sent["data"]["tag"] == "room_climate_wohnzimmer_ventilate_now"
    _run(coord, {"wohnzimmer": _room()})
    cleared = _calls(coord)[1]
    assert cleared["message"] == "clear_notification"
    assert cleared["data"]["tag"] == sent["data"]["tag"]


def test_no_tag_data_without_the_option():
    coord, _ = _coordinator(NOON)
    _run(coord, {"wohnzimmer": _room(ventilate=True)})
    assert "data" not in _calls(coord)[0]


def test_last_aired_timer_survives_and_resets_after_airing():
    coord, clock = _coordinator(NOON)
    coord._last_aired = {"wohnzimmer": NOON - timedelta(hours=6)}  # restored from storage
    coord._was_open = {}
    coord._aired_dirty = False
    room = {"id": "wohnzimmer"}
    assert coord._update_last_aired(room, "off") == 6.0
    assert coord._aired_dirty is False
    assert coord._update_last_aired(room, "on") is None  # airing
    clock.current += timedelta(minutes=8)
    assert coord._update_last_aired(room, "off") == 0.0
    assert coord._aired_dirty is True


def test_unknown_room_starts_counting_without_nagging():
    coord, _ = _coordinator(NOON)
    coord._last_aired = {}
    coord._was_open = {}
    coord._aired_dirty = False
    assert coord._update_last_aired({"id": "neu"}, "off") == 0.0
    assert coord._aired_dirty is True
