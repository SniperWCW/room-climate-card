from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
    NOTIFICATION_CLOSE_COVER,
    NOTIFICATION_CLOSE_WINDOW,
    NOTIFICATION_VENTILATE,
)
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
        self.last_notification_at: dict[str, datetime] = {}
        self.last_flag_state: dict[str, bool] = {}

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
            return []

        payload = response.get(entity_id, {}) if isinstance(response, dict) else {}
        forecast = payload.get("forecast", []) if isinstance(payload, dict) else []
        return forecast if isinstance(forecast, list) else []

    def _collect_outside_weather(self) -> dict[str, Any]:
        entity_id = self.config.get(CONF_OUTSIDE_WEATHER)
        attrs = self._get_attrs(entity_id)
        return {
            "temperature": as_float(attrs.get("temperature")),
            "humidity": as_float(attrs.get("humidity")),
            "wind_speed": as_float(attrs.get("wind_speed")),
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
            NOTIFICATION_VENTILATE: f"Lueften lohnt sich jetzt. {room.recommendation}",
            NOTIFICATION_CLOSE_WINDOW: "Fenster wieder schliessen. Die aktuelle Aussenluft bringt keinen Vorteil mehr.",
            NOTIFICATION_CLOSE_COVER: "Rollladen schliessen empfohlen. Direkter Sonneneintrag heizt den Raum aktuell weiter auf.",
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
        now = datetime.now().astimezone()

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
                self.last_flag_state[key] = is_active

                if not is_active or was_active:
                    continue

                last_sent = self.last_notification_at.get(key)
                if last_sent and now - last_sent < cooldown:
                    continue

                await self._async_send_notification(room, notification_type)
                self.last_notification_at[key] = now

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
        return {
            "rooms": room_results,
            "forecast": forecast,
            "outside_weather": outside_weather,
            "sun": sun,
        }
