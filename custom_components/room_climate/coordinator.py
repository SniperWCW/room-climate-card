from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_WALL_QUALITY,
    DEFAULT_WALL_QUALITY,
    NOTIFICATION_MOLD_RISK,
    CONF_NOTIFICATION_COOLDOWN,
    CONF_NOTIFICATION_ENABLED,
    CONF_NOTIFY_SERVICE,
    CONF_OUTSIDE_ABSOLUTE_HUMIDITY,
    CONF_OUTSIDE_WEATHER,
    CONF_ROOMS,
    CONF_SUN_ENTITY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_PRIMARY_INPUT_AGE,
    NOTIFICATION_CLOSE_COVER,
    NOTIFICATION_CLOSE_WINDOW,
    NOTIFICATION_VENTILATE,
)
from .house_ventilation import absolute_humidity, evaluate_house_ventilation
from .ventilation_session import OPEN_STATES, update_sessions
from .logic import RoomResult, as_float, evaluate_room

_LOGGER = logging.getLogger(__name__)

# Retry quickly when the weather entity is not ready yet (e.g. right after a
# restart) instead of waiting for the next regular forecast refresh.
FORECAST_RETRY_SECONDS = 30
DAY_TYPE_LABELS = {
    "hot": ("Hitzetag", "mdi:weather-sunny-alert"),
    "summer": ("Sommertag", "mdi:weather-sunny"),
    "mild": ("Milder Tag", "mdi:weather-partly-cloudy"),
    "cool": ("Kühler Tag", "mdi:weather-cloudy"),
}

# Short humidity peaks (showering, cooking) are normal. A mould risk is only
# reported once it has lasted this long.
MOLD_CONFIRM_MINUTES = 60

# A window that was just opened gets a short grace period before a
# "close window" push is sent, e.g. when someone briefly airs the room.
CLOSE_WINDOW_GRACE_MINUTES = 3

_WIND_TO_KMH = {
    "km/h": 1.0,
    "m/s": 3.6,
    "mph": 1.609344,
    "kn": 1.852,
    "ft/s": 1.09728,
}


def _temperature_to_celsius(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    if unit in {"°F", "F"}:
        return (value - 32) * 5 / 9
    if unit == "K":
        return value - 273.15
    return value


def _wind_to_kmh(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    return value * _WIND_TO_KMH.get(unit or "km/h", 1.0)


type RoomClimateConfigEntry = ConfigEntry["RoomClimateCoordinator"]


class RoomClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_store = Store[dict[str, dict[str, Any]]](
            hass, 1, f"{DOMAIN}.{entry.entry_id}.ventilation_sessions"
        )
        self._forecast_cache: list[dict[str, Any]] = []
        self._forecast_requested_at: datetime | None = None
        self._forecast_retry_unsub = None
        self._climate_entity_cache: dict[str, str | None] = {}
        self._mold_since: dict[str, datetime] = {}
        entry.async_on_unload(self._cancel_forecast_retry)
        self.last_notification_at: dict[str, datetime] = {}
        self.last_flag_state: dict[str, bool] = {}
        self._notification_store = Store[dict[str, Any]](
            hass, 1, f"{DOMAIN}.{entry.entry_id}.notification_state"
        )

    async def _async_restore_notification_state(self) -> None:
        """Restore notification state before the first coordinator refresh."""
        stored_state = await self._notification_store.async_load() or {}
        stored_times = stored_state.get("last_notification_at", {})
        if isinstance(stored_times, dict):
            self.last_notification_at = {
                key: timestamp
                for key, value in stored_times.items()
                if isinstance(key, str)
                and isinstance(value, str)
                and (timestamp := dt_util.parse_datetime(value)) is not None
            }
        stored_flags = stored_state.get("last_flag_state", {})
        if isinstance(stored_flags, dict):
            self.last_flag_state = {
                key: value for key, value in stored_flags.items() if isinstance(key, str) and isinstance(value, bool)
            }

    async def async_config_entry_first_refresh(self) -> None:
        """Load persisted state before calculating recommendations."""
        await self._async_restore_notification_state()
        stored_sessions = await self._session_store.async_load()
        self._sessions = stored_sessions if isinstance(stored_sessions, dict) else {}
        await super().async_config_entry_first_refresh()

    def start_window_tracking(self) -> None:
        """Refresh on contact transitions and every minute while windows are open.

        Also reload the forecast as soon as the weather entity becomes available,
        so the forecast is not missing for minutes after a restart.
        """
        weather_entity = self.config.get(CONF_OUTSIDE_WEATHER)
        if weather_entity:

            async def weather_changed(event) -> None:
                old = event.data.get("old_state")
                if not self._forecast_cache and (old is None or old.state in {"unavailable", "unknown"}):
                    self._forecast_requested_at = None
                    await self.async_request_refresh()

            self.entry.async_on_unload(
                async_track_state_change_event(self.hass, [weather_entity], weather_changed)
            )

        entities = {
            room["window"]
            for room in self.config.get(CONF_ROOMS, [])
            if room.get("window")
        }

        async def window_changed(event) -> None:
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if old is None or new is None or old.state != new.state:
                await self.async_request_refresh()

        async def session_tick(_now: datetime) -> None:
            if any(self._get_state(entity) in OPEN_STATES for entity in entities):
                await self.async_request_refresh()

        if entities:
            self.entry.async_on_unload(
                async_track_state_change_event(self.hass, entities, window_changed)
            )
            self.entry.async_on_unload(
                async_track_time_interval(
                    self.hass, session_tick, timedelta(minutes=1)
                )
            )

    async def _async_save_notification_state(self) -> None:
        await self._notification_store.async_save(
            {
                "last_notification_at": {
                    key: timestamp.isoformat() for key, timestamp in self.last_notification_at.items()
                },
                "last_flag_state": self.last_flag_state,
            }
        )

    @property
    def config(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    def _get_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    def _get_attrs(self, entity_id: str | None) -> dict[str, Any]:
        if not entity_id:
            return {}
        state = self.hass.states.get(entity_id)
        return dict(state.attributes) if state else {}

    async def _async_get_forecast(self, entity_id: str | None) -> list[dict[str, Any]]:
        if not entity_id:
            return []

        now = dt_util.utcnow()
        if (
            self._forecast_requested_at
            and now - self._forecast_requested_at < DEFAULT_SCAN_INTERVAL
        ):
            return self._forecast_cache
        self._forecast_requested_at = now

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "hourly", "entity_id": [entity_id]},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Forecast lookup failed for %s: %s", entity_id, err)
            self._forecast_cache = []
            self._schedule_forecast_retry()
            return []

        payload = response.get(entity_id, {}) if isinstance(response, dict) else {}
        forecast = payload.get("forecast", []) if isinstance(payload, dict) else []
        if not isinstance(forecast, list):
            forecast = []
        attrs = self._get_attrs(entity_id)
        temperature_unit = attrs.get("temperature_unit")
        wind_unit = attrs.get("wind_speed_unit")
        normalized: list[dict[str, Any]] = []
        for entry in forecast:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            for key in ("temperature", "templow", "apparent_temperature", "dew_point"):
                if key in item:
                    item[key] = _temperature_to_celsius(as_float(item[key]), temperature_unit)
            if "wind_speed" in item:
                item["wind_speed"] = _wind_to_kmh(as_float(item["wind_speed"]), wind_unit)
            normalized.append(item)
        self._forecast_cache = normalized
        if not normalized:
            self._schedule_forecast_retry()
        return self._forecast_cache

    def _schedule_forecast_retry(self) -> None:
        if self._forecast_retry_unsub is not None:
            return

        async def retry(_now: datetime) -> None:
            self._forecast_retry_unsub = None
            self._forecast_requested_at = None
            await self.async_request_refresh()

        self._forecast_retry_unsub = async_call_later(self.hass, FORECAST_RETRY_SECONDS, retry)

    def _cancel_forecast_retry(self) -> None:
        if self._forecast_retry_unsub is not None:
            self._forecast_retry_unsub()
            self._forecast_retry_unsub = None

    def _resolve_climate_entity(self, room: dict[str, Any]) -> str | None:
        """Return the room's thermostat.

        Rooms configured before thermostats were supported have no "climate"
        key; for them the thermostat in the same area as the temperature sensor
        is used. An explicit empty value means "no thermostat".
        """
        if CONF_CLIMATE in room:
            return room.get(CONF_CLIMATE) or None
        room_id = room.get("id", "")
        if room_id in self._climate_entity_cache:
            return self._climate_entity_cache[room_id]
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        def area_of(entity_id: str | None) -> str | None:
            entry = entity_registry.async_get(entity_id) if entity_id else None
            if entry is None:
                return None
            if entry.area_id:
                return entry.area_id
            device = device_registry.async_get(entry.device_id) if entry.device_id else None
            return device.area_id if device else None

        area_id = area_of(room.get("temperature"))
        found = None
        if area_id:
            found = next(
                (
                    entry.entity_id
                    for entry in er.async_entries_for_area(entity_registry, area_id)
                    if entry.domain == "climate" and not entry.disabled_by
                ),
                None,
            )
            if found is None:
                for device in dr.async_entries_for_area(device_registry, area_id):
                    found = next(
                        (
                            entry.entity_id
                            for entry in er.async_entries_for_device(entity_registry, device.id)
                            if entry.domain == "climate" and not entry.disabled_by
                        ),
                        None,
                    )
                    if found:
                        break
        self._climate_entity_cache[room_id] = found
        return found

    def _collect_outside_weather(self) -> dict[str, Any]:
        entity_id = self.config.get(CONF_OUTSIDE_WEATHER)
        attrs = self._get_attrs(entity_id)
        # All logic works in °C and km/h; convert whatever the weather entity uses.
        return {
            "temperature": _temperature_to_celsius(
                as_float(attrs.get("temperature")), attrs.get("temperature_unit")
            ),
            "humidity": as_float(attrs.get("humidity")),
            "wind_speed": _wind_to_kmh(
                as_float(attrs.get("wind_speed")), attrs.get("wind_speed_unit")
            ),
            "wind_bearing": as_float(attrs.get("wind_bearing")),
            "cloud_coverage": as_float(attrs.get("cloud_coverage")),
            "sun_azimuth": None,
        }

    def _collect_sun(self) -> dict[str, Any]:
        entity_id = self.config.get(CONF_SUN_ENTITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        attrs = dict(state.attributes) if state else {}
        return {
            "azimuth": as_float(attrs.get("azimuth")),
            "elevation": as_float(attrs.get("elevation")),
            "above_horizon": state.state == "above_horizon" if state else False,
        }

    def _collect_room_metrics(self, room: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for key in (
            "temperature",
            "humidity",
            "inside_absolute_humidity",
            "humidex_value",
            "scharlau",
            "humidex",
            "simmer",
            "dewpoint",
            "window",
            "cover",
        ):
            entity_id = room.get(key)
            if key in {"scharlau", "humidex", "simmer", "dewpoint", "window", "cover"}:
                metrics[key] = self._get_state(entity_id)
            else:
                metrics[key] = as_float(self._get_state(entity_id))
        primary_input_ages: dict[str, float | None] = {}
        for key in ("temperature", "humidity"):
            entity_id = room.get(key)
            state = self.hass.states.get(entity_id) if entity_id else None
            if state:
                last_reported = getattr(state, "last_reported", state.last_updated)
                primary_input_ages[key] = round(
                    (dt_util.utcnow() - last_reported).total_seconds() / 60,
                    1,
                )
            else:
                primary_input_ages[key] = None
        missing_inputs = [key for key in ("temperature", "humidity") if metrics.get(key) is None]
        stale_inputs = [
            key
            for key, age in primary_input_ages.items()
            if age is not None and age > MAX_PRIMARY_INPUT_AGE.total_seconds() / 60
        ]
        if metrics.get("inside_absolute_humidity") is not None:
            metrics["inside_absolute_humidity_source"] = "sensor"
        else:
            calculated = absolute_humidity(metrics.get("temperature"), metrics.get("humidity"))
            metrics["inside_absolute_humidity"] = round(calculated, 2) if calculated is not None else None
            metrics["inside_absolute_humidity_source"] = "calculated" if calculated is not None else None
        climate_entity = self._resolve_climate_entity(room)
        climate_state = self.hass.states.get(climate_entity) if climate_entity else None
        metrics["climate_entity"] = climate_entity
        metrics["climate"] = (
            {
                "state": climate_state.state,
                "hvac_action": climate_state.attributes.get("hvac_action"),
                "target_temperature": climate_state.attributes.get("temperature"),
                "current_temperature": climate_state.attributes.get("current_temperature"),
            }
            if climate_state
            else None
        )
        metrics["input_age_minutes"] = primary_input_ages
        metrics["inputs_available"] = not missing_inputs and not stale_inputs
        metrics["data_quality"] = (
            "unavailable" if missing_inputs else "stale" if stale_inputs else "good"
        )
        return metrics

    async def _async_send_notification(self, room: RoomResult, notification_type: str) -> None:
        if not self.config.get(CONF_NOTIFICATION_ENABLED, True):
            return
        service_name = self.config.get(CONF_NOTIFY_SERVICE)
        if not service_name:
            return
        if "." not in service_name:
            return

        domain, service = service_name.split(".", 1)
        title = f"Raumklima: {room.name}"
        message_map = {
            NOTIFICATION_VENTILATE: f"Lüften lohnt sich jetzt. {room.recommendation}",
            NOTIFICATION_CLOSE_WINDOW: room.close_window_reason
            or "Fenster wieder schließen. Die aktuelle Außenluft bringt keinen Vorteil mehr.",
            NOTIFICATION_CLOSE_COVER: "Rollladen schließen empfohlen. Direkter Sonneneintrag heizt den Raum aktuell weiter auf.",
            NOTIFICATION_MOLD_RISK: room.mold_risk_reason or "Schimmelrisiko an kalten Wänden. Bitte lüften und gleichmäßig heizen.",
        }
        message = message_map[notification_type]

        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"title": title, "message": message},
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Notification via %s failed: %s", service_name, err)

    async def _async_process_notifications(
        self,
        rooms: dict[str, RoomResult],
        open_minutes: dict[str, float] | None = None,
    ) -> None:
        cooldown_minutes = int(self.config.get(CONF_NOTIFICATION_COOLDOWN, 120))
        cooldown = timedelta(minutes=max(5, cooldown_minutes))
        now = dt_util.now()
        state_changed = False

        for room_id, room in rooms.items():
            if not room.notifications_enabled:
                continue

            # Defer "close window" until the window has been open for a moment.
            # The edge detection below only sees the deferred value, so the push
            # is sent once the grace period has passed instead of being lost.
            window_open_for = (open_minutes or {}).get(room_id)
            close_window_due = room.close_window and (
                window_open_for is None or window_open_for >= CLOSE_WINDOW_GRACE_MINUTES
            )
            flags = {
                NOTIFICATION_VENTILATE: room.ventilate_now,
                NOTIFICATION_CLOSE_WINDOW: close_window_due,
                NOTIFICATION_CLOSE_COVER: room.close_cover,
                NOTIFICATION_MOLD_RISK: room.mold_risk,
            }

            for notification_type, is_active in flags.items():
                key = f"{room_id}:{notification_type}"
                was_active = self.last_flag_state.get(key, False)
                if self.last_flag_state.get(key) != is_active:
                    self.last_flag_state[key] = is_active
                    state_changed = True

                if not is_active or was_active:
                    continue

                last_sent = self.last_notification_at.get(key)
                if last_sent and now - last_sent < cooldown:
                    continue

                await self._async_send_notification(room, notification_type)
                self.last_notification_at[key] = now
                state_changed = True

        if state_changed:
            await self._async_save_notification_state()

    @staticmethod
    def _build_overview(
        rooms: dict[str, RoomResult],
        outside_weather: dict[str, Any],
        forecast: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a short, actionable house-wide briefing from room decisions."""
        temperatures = [
            temperature
            for entry in forecast[:24]
            if (temperature := as_float(entry.get("temperature"))) is not None
        ]
        current_temperature = outside_weather.get("temperature")
        high = max(temperatures) if temperatures else current_temperature
        low = min(temperatures) if temperatures else current_temperature

        if high is not None and high >= 30:
            day_type_key = "hot"
        elif high is not None and high >= 25:
            day_type_key = "summer"
        elif high is not None and high >= 18:
            day_type_key = "mild"
        else:
            day_type_key = "cool"
        day_type, icon = DAY_TYPE_LABELS[day_type_key]

        ventilate = [room.name for room in rooms.values() if room.ventilate_now]
        close_windows = [room.name for room in rooms.values() if room.close_window]
        close_covers = [room.name for room in rooms.values() if room.close_cover]
        actions: list[str] = []
        if ventilate:
            actions.append(f"Jetzt lüften: {', '.join(ventilate)}.")
        if close_covers:
            actions.append(f"Sonne aussperren: {', '.join(close_covers)}.")
        if close_windows:
            actions.append(f"Fenster schließen: {', '.join(close_windows)}.")
        mold_rooms = [room.name for room in rooms.values() if room.mold_risk]
        if mold_rooms:
            actions.append(f"Schimmelrisiko beachten: {', '.join(mold_rooms)}.")
        reduce_heating = [room.name for room in rooms.values() if room.reduce_heating]
        if reduce_heating:
            actions.append(f"Heizung herunterregeln: {', '.join(reduce_heating)}.")

        if not actions:
            if day_type_key in {"hot", "summer"}:
                actions.append("Tagsüber Fenster und Beschattung geschlossen halten; zum Abkühlen das nächste Lüftungsfenster abwarten.")
            else:
                actions.append("Aktuell besteht keine vorrangige Maßnahme.")

        scored_rooms = [room for room in rooms.values() if room.score is not None]
        scores = [room.score for room in scored_rooms]
        worst_room = min(scored_rooms, key=lambda room: room.score, default=None)
        return {
            "day_type": day_type,
            "day_type_key": day_type_key,
            "has_actions": bool(ventilate or close_windows or close_covers or mold_rooms or reduce_heating),
            "mold_risk_count": len(mold_rooms),
            "reduce_heating_count": len(reduce_heating),
            "icon": icon,
            "summary": " ".join(actions),
            "actions": actions,
            "outdoor_temperature": current_temperature,
            "forecast_high": high,
            "forecast_low": low,
            "average_score": round(sum(scores) / len(scores)) if scores else None,
            "room_count": len(rooms),
            "ventilate_count": len(ventilate),
            "close_window_count": len(close_windows),
            "close_cover_count": len(close_covers),
            "worst_room": worst_room.name if worst_room else None,
            "worst_room_score": worst_room.score if worst_room else None,
        }

    def _confirm_mold_risk(self, rooms: dict[str, RoomResult], now: datetime) -> None:
        for room_id, room in rooms.items():
            if room.attributes.get("mold_risk") != "high":
                self._mold_since.pop(room_id, None)
                continue
            since = self._mold_since.setdefault(room_id, now)
            minutes = (now - since).total_seconds() / 60
            room.attributes["mold_risk_minutes"] = round(minutes)
            if minutes < MOLD_CONFIRM_MINUTES:
                room.set_mold_pending()

    def _outside_absolute_humidity(self, outside_weather: dict[str, Any]) -> tuple[float | None, str | None]:
        """Use the configured sensor, otherwise calculate from the weather entity."""
        sensor_value = as_float(self._get_state(self.config.get(CONF_OUTSIDE_ABSOLUTE_HUMIDITY)))
        if sensor_value is not None:
            return sensor_value, "sensor"
        calculated = absolute_humidity(outside_weather.get("temperature"), outside_weather.get("humidity"))
        if calculated is None:
            return None, None
        return round(calculated, 2), "calculated"

    def _window_states(self) -> dict[str, tuple[str, datetime]]:
        windows: dict[str, tuple[str, datetime]] = {}
        for room in self.config.get(CONF_ROOMS, []):
            entity_id = room.get("window")
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            windows[room["id"]] = (
                state.state if state else "unknown",
                state.last_changed if state else dt_util.utcnow(),
            )
        return windows

    @staticmethod
    def _apply_sessions(house_ventilation: Any, sessions: list[dict[str, Any]]) -> None:
        """Merge the live ventilation sessions into the house-wide recommendation.

        The countdown is kept out of the sensor state (title) so the state only
        changes when the phase changes; minutes are exposed as attributes and in
        ``session_detail``.
        """
        house_ventilation.sessions = sessions
        finish = [session for session in sessions if session["phase"] == "finish"]
        continued = [session for session in sessions if session["phase"] == "continue"]
        if finish:
            house_ventilation.mode = "close_windows"
            house_ventilation.title = ("Lüften beenden: " + ", ".join(session["room_name"] for session in finish))[:250]
            house_ventilation.reason = " ".join(dict.fromkeys(session["reason"] for session in finish))
            house_ventilation.icon = "mdi:window-closed-variant"
            house_ventilation.color = "orange"
            house_ventilation.duration_minutes = None
            house_ventilation.session_phase = "finish"
            house_ventilation.session_detail = house_ventilation.reason
            house_ventilation.session_elapsed_minutes = round(
                max(session["elapsed_minutes"] for session in finish)
            )
            house_ventilation.session_remaining_minutes = 0
        elif continued:
            remaining = min(session["remaining_minutes"] for session in continued)
            elapsed = max(session["elapsed_minutes"] for session in continued)
            house_ventilation.mode = "ventilation_continue"
            house_ventilation.title = "Weiterlüften – Vorteil besteht noch"
            house_ventilation.icon = "mdi:window-open-variant"
            house_ventilation.color = "green"
            house_ventilation.reason = (
                "Die berechnete Mindestzeit ist erreicht. Feuchte- oder "
                "Temperaturvorteil besteht weiterhin."
            )
            house_ventilation.session_phase = "continue"
            house_ventilation.session_detail = (
                f"Seit ca. {round(elapsed)} Min. geöffnet · spätestens in "
                f"{remaining} Min. schließen und prüfen"
            )
            house_ventilation.session_elapsed_minutes = round(elapsed)
            house_ventilation.session_remaining_minutes = remaining
        elif sessions:
            remaining = min(session["remaining_minutes"] for session in sessions)
            elapsed = max(session["elapsed_minutes"] for session in sessions)
            unavailable = all(
                session["data_quality"] == "unavailable" for session in sessions
            )
            if unavailable:
                house_ventilation.mode = "ventilation_active_limited"
                house_ventilation.title = "Lüftung läuft (ohne aktuelle Raumwerte)"
                house_ventilation.reason = (
                    "Aktuelle Raumwerte fehlen. Nach der berechneten Zeit "
                    "Fenster schließen und Werte prüfen."
                )
                house_ventilation.icon = "mdi:window-open-variant"
                house_ventilation.color = "amber"
            elif house_ventilation.mode not in {
                "cross_ventilation_active",
                "ventilation_active",
            }:
                house_ventilation.mode = "ventilation_active"
                house_ventilation.title = "Lüften aktiv"
                house_ventilation.icon = "mdi:window-open-variant"
                house_ventilation.color = "green"
            else:
                house_ventilation.title = house_ventilation.title.replace(
                    " (maximaler Luftaustausch)", ""
                )
            house_ventilation.session_phase = "active"
            house_ventilation.session_detail = (
                f"Seit ca. {round(elapsed)} Min. geöffnet · noch ca. {remaining} Min."
                + (" · Raumwerte fehlen" if unavailable else "")
            )
            house_ventilation.session_elapsed_minutes = round(elapsed)
            house_ventilation.session_remaining_minutes = remaining

    async def _async_update_data(self) -> dict[str, Any]:
        outside_weather = self._collect_outside_weather()
        outside_abs, outside_abs_source = self._outside_absolute_humidity(outside_weather)
        sun = self._collect_sun()
        outside_weather["sun_azimuth"] = sun["azimuth"]
        forecast = await self._async_get_forecast(self.config.get(CONF_OUTSIDE_WEATHER))

        room_results: dict[str, RoomResult] = {}
        for room in self.config.get(CONF_ROOMS, []):
            metrics = self._collect_room_metrics(room)
            result = evaluate_room(
                room,
                metrics,
                outside_abs,
                outside_weather,
                sun,
                forecast,
                self.config.get(CONF_WALL_QUALITY, DEFAULT_WALL_QUALITY),
            )
            result.attributes["outside_absolute_humidity_source"] = outside_abs_source
            room_results[result.room_id] = result

        self._confirm_mold_risk(room_results, dt_util.utcnow())

        house_ventilation = evaluate_house_ventilation(
            self.config.get(CONF_ROOMS, []),
            room_results,
            outside_abs,
            outside_weather,
            forecast,
            now=dt_util.now(),
        )

        previous_sessions = deepcopy(self._sessions)
        sessions = update_sessions(
            self._sessions,
            self._window_states(),
            room_results,
            dt_util.utcnow(),
            house_ventilation.duration_minutes,
            outside_weather.get("temperature"),
        )
        # A finished session (maximum time reached, benefit gone or values
        # missing) must also reach the per-room close-window sensor and push.
        for session in sessions:
            if session["phase"] == "finish" and (room := room_results.get(session["room_id"])):
                room.apply_session_finish(session.get("reason") or "Fenster schließen.")
        self._apply_sessions(house_ventilation, sessions)
        if previous_sessions != self._sessions:
            await self._session_store.async_save(self._sessions)

        await self._async_process_notifications(
            room_results,
            {session["room_id"]: session["elapsed_minutes"] for session in sessions},
        )
        overview = self._build_overview(room_results, outside_weather, forecast)
        return {
            "rooms": room_results,
            "overview": overview,
            "house_ventilation": house_ventilation,
            "forecast": forecast,
            "outside_weather": outside_weather,
            "sun": sun,
        }
