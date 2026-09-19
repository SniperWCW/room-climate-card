from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
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
from .house_ventilation import evaluate_house_ventilation
from .ventilation_session import update_sessions
from .logic import RoomResult, as_float, evaluate_room

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IntegrationData:
    coordinator: "RoomClimateCoordinator"


class RoomClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
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
        """Refresh on contact transitions and every minute while windows are open."""
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
            if self._sessions or any(
                self._get_state(entity) in {"on", "open", "tilted"}
                for entity in entities
            ):
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
            return []

        payload = response.get(entity_id, {}) if isinstance(response, dict) else {}
        forecast = payload.get("forecast", []) if isinstance(payload, dict) else []
        self._forecast_cache = forecast if isinstance(forecast, list) else []
        return self._forecast_cache

    def _collect_outside_weather(self) -> dict[str, Any]:
        entity_id = self.config.get(CONF_OUTSIDE_WEATHER)
        attrs = self._get_attrs(entity_id)
        return {
            "temperature": as_float(attrs.get("temperature")),
            "humidity": as_float(attrs.get("humidity")),
            "wind_speed": as_float(attrs.get("wind_speed")),
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
            NOTIFICATION_CLOSE_WINDOW: "Fenster wieder schließen. Die aktuelle Außenluft bringt keinen Vorteil mehr.",
            NOTIFICATION_CLOSE_COVER: "Rollladen schließen empfohlen. Direkter Sonneneintrag heizt den Raum aktuell weiter auf.",
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

    async def _async_process_notifications(self, rooms: dict[str, RoomResult]) -> None:
        cooldown_minutes = int(self.config.get(CONF_NOTIFICATION_COOLDOWN, 120))
        cooldown = timedelta(minutes=max(5, cooldown_minutes))
        now = dt_util.now()
        state_changed = False

        for room_id, room in rooms.items():
            if not room.notifications_enabled:
                continue

            flags = {
                NOTIFICATION_VENTILATE: room.ventilate_now,
                NOTIFICATION_CLOSE_WINDOW: room.close_window,
                NOTIFICATION_CLOSE_COVER: room.close_cover,
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
            day_type, icon = "Hitzetag", "mdi:weather-sunny-alert"
        elif high is not None and high >= 25:
            day_type, icon = "Sommertag", "mdi:weather-sunny"
        elif high is not None and high >= 18:
            day_type, icon = "Milder Tag", "mdi:weather-partly-cloudy"
        else:
            day_type, icon = "Kuehler Tag", "mdi:weather-cloudy"

        ventilate = [room.name for room in rooms.values() if room.ventilate_now]
        close_windows = [room.name for room in rooms.values() if room.close_window]
        close_covers = [room.name for room in rooms.values() if room.close_cover]
        actions: list[str] = []
        if ventilate:
            actions.append(f"Jetzt lueften: {', '.join(ventilate)}.")
        if close_covers:
            actions.append(f"Sonne aussperren: {', '.join(close_covers)}.")
        if close_windows:
            actions.append(f"Fenster schliessen: {', '.join(close_windows)}.")

        if not actions:
            if day_type in {"Hitzetag", "Sommertag"}:
                actions.append("Tagsueber Fenster und Beschattung geschlossen halten; zum Abkuehlen das naechste Lueftungsfenster abwarten.")
            else:
                actions.append("Aktuell besteht keine vorrangige Massnahme.")

        scored_rooms = [room for room in rooms.values() if room.score is not None]
        scores = [room.score for room in scored_rooms]
        worst_room = min(scored_rooms, key=lambda room: room.score, default=None)
        return {
            "day_type": day_type,
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

    async def _async_update_data(self) -> dict[str, Any]:
        outside_abs = as_float(self._get_state(self.config.get(CONF_OUTSIDE_ABSOLUTE_HUMIDITY)))
        outside_weather = self._collect_outside_weather()
        sun = self._collect_sun()
        outside_weather["sun_azimuth"] = sun["azimuth"]
        forecast = await self._async_get_forecast(self.config.get(CONF_OUTSIDE_WEATHER))

        room_results: dict[str, RoomResult] = {}
        for room in self.config.get(CONF_ROOMS, []):
            metrics = self._collect_room_metrics(room)
            result = evaluate_room(room, metrics, outside_abs, outside_weather, sun, forecast)
            room_results[result.room_id] = result

        await self._async_process_notifications(room_results)
        overview = self._build_overview(room_results, outside_weather, forecast)
        house_ventilation = evaluate_house_ventilation(
            self.config.get(CONF_ROOMS, []),
            room_results,
            outside_abs,
            outside_weather,
            forecast,
            now=dt_util.now(),
        )
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

        previous_sessions = deepcopy(self._sessions)
        sessions = update_sessions(
            self._sessions,
            windows,
            room_results,
            dt_util.utcnow(),
            house_ventilation.duration_minutes,
            outside_weather.get("temperature"),
        )
        house_ventilation.sessions = sessions
        finish = [session for session in sessions if session["phase"] == "finish"]
        continued = [session for session in sessions if session["phase"] == "continue"]
        if finish:
            house_ventilation.mode = "close_windows"
            house_ventilation.title = "Lüften beenden: " + ", ".join(session["room_name"] for session in finish)
            house_ventilation.title = house_ventilation.title[:250]
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
            house_ventilation.title = "Weiterlüften – Vorteil besteht noch"
            house_ventilation.reason = (
                "Die berechnete Mindestzeit ist erreicht. Feuchte- oder "
                "Temperaturvorteil besteht weiterhin."
            )
            house_ventilation.session_phase = "continue"
            house_ventilation.session_detail = (
                f"Seit ca. {round(elapsed)} Min. geöffnet · spätestens in "
                f"{remaining} Min. erneut schließen und prüfen"
            )
            house_ventilation.session_elapsed_minutes = round(elapsed)
            house_ventilation.session_remaining_minutes = remaining
        elif sessions:
            remaining = min(session["remaining_minutes"] for session in sessions)
            elapsed = max(session["elapsed_minutes"] for session in sessions)
            unavailable = any(
                session["data_quality"] == "unavailable" for session in sessions
            )
            if unavailable:
                house_ventilation.mode = "ventilation_active_limited"
                house_ventilation.title = (
                    f"Lüftung läuft · noch max. {remaining} Min."
                )
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
            if not unavailable:
                house_ventilation.title += f" · noch ca. {remaining} Min."
            house_ventilation.session_phase = "active"
            house_ventilation.session_detail = (
                f"Seit ca. {round(elapsed)} Min. geöffnet · Raumwerte fehlen"
                if unavailable
                else (
                    f"Seit ca. {round(elapsed)} Min. geöffnet · laufende "
                    "Empfehlung anhand der aktuellen Raumwerte"
                )
            )
            house_ventilation.session_elapsed_minutes = round(elapsed)
            house_ventilation.session_remaining_minutes = remaining
        if previous_sessions != self._sessions:
            await self._session_store.async_save(self._sessions)
        return {
            "rooms": room_results,
            "overview": overview,
            "house_ventilation": house_ventilation,
            "forecast": forecast,
            "outside_weather": outside_weather,
            "sun": sun,
        }
