from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_COVER,
    CONF_DEWPOINT,
    CONF_HUMIDEX,
    CONF_HUMIDEX_VALUE,
    CONF_HUMIDITY,
    CONF_INSIDE_ABSOLUTE_HUMIDITY,
    CONF_NOTIFICATION_COOLDOWN,
    CONF_NOTIFICATION_ENABLED,
    CONF_NOTIFY_SERVICE,
    CONF_OUTSIDE_ABSOLUTE_HUMIDITY,
    CONF_OUTSIDE_WEATHER,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOM_NOTIFICATIONS,
    CONF_ROOM_TYPE,
    CONF_ROOMS,
    CONF_SCHARLAU,
    CONF_SIMMER,
    CONF_SUN_ENTITY,
    CONF_TEMPERATURE,
    CONF_WINDOW,
    CONF_WINDOW_ORIENTATION,
    DEFAULT_NAME,
    DEFAULT_NOTIFICATION_COOLDOWN,
    DEFAULT_NOTIFICATION_ENABLED,
    DOMAIN,
    ROOM_TYPE_OPTIONS,
    WINDOW_ORIENTATION_OPTIONS,
)
from .logic import slugify


def _notify_service_options(hass) -> list[selector.SelectOptionDict]:
    services = hass.services.async_services().get("notify", {})
    options = [selector.SelectOptionDict(value="", label="Keine Push-Benachrichtigung")]
    options.extend(
        selector.SelectOptionDict(value=f"notify.{service}", label=f"notify.{service}")
        for service in sorted(services)
    )
    return options


def _global_schema(hass, defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("title", DEFAULT_NAME)): selector.TextSelector(),
            vol.Optional(CONF_OUTSIDE_ABSOLUTE_HUMIDITY, default=defaults.get(CONF_OUTSIDE_ABSOLUTE_HUMIDITY, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_OUTSIDE_WEATHER, default=defaults.get(CONF_OUTSIDE_WEATHER, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(CONF_SUN_ENTITY, default=defaults.get(CONF_SUN_ENTITY, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sun")
            ),
            vol.Required(CONF_NOTIFICATION_ENABLED, default=defaults.get(CONF_NOTIFICATION_ENABLED, DEFAULT_NOTIFICATION_ENABLED)): selector.BooleanSelector(),
            vol.Optional(CONF_NOTIFY_SERVICE, default=defaults.get(CONF_NOTIFY_SERVICE, "")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_notify_service_options(hass), mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_NOTIFICATION_COOLDOWN, default=defaults.get(CONF_NOTIFICATION_COOLDOWN, DEFAULT_NOTIFICATION_COOLDOWN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=1440, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


def _room_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    room_type_options = [selector.SelectOptionDict(value=value, label=value) for value in ROOM_TYPE_OPTIONS]
    orientation_options = [
        selector.SelectOptionDict(value=value, label=value or "Keine Angabe") for value in WINDOW_ORIENTATION_OPTIONS
    ]
    return vol.Schema(
        {
            vol.Required(CONF_ROOM_NAME, default=defaults.get(CONF_ROOM_NAME, "")): selector.TextSelector(),
            vol.Required(CONF_ROOM_TYPE, default=defaults.get(CONF_ROOM_TYPE, "default")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=room_type_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_WINDOW_ORIENTATION, default=defaults.get(CONF_WINDOW_ORIENTATION, "")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=orientation_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_TEMPERATURE, default=defaults.get(CONF_TEMPERATURE, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_HUMIDITY, default=defaults.get(CONF_HUMIDITY, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_INSIDE_ABSOLUTE_HUMIDITY, default=defaults.get(CONF_INSIDE_ABSOLUTE_HUMIDITY, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WINDOW, default=defaults.get(CONF_WINDOW, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Optional(CONF_COVER, default=defaults.get(CONF_COVER, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="cover")
            ),
            vol.Optional(CONF_HUMIDEX_VALUE, default=defaults.get(CONF_HUMIDEX_VALUE, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SCHARLAU, default=defaults.get(CONF_SCHARLAU, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HUMIDEX, default=defaults.get(CONF_HUMIDEX, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SIMMER, default=defaults.get(CONF_SIMMER, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_DEWPOINT, default=defaults.get(CONF_DEWPOINT, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_ROOM_NOTIFICATIONS, default=defaults.get(CONF_ROOM_NOTIFICATIONS, True)): selector.BooleanSelector(),
        }
    )


class RoomClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return RoomClimateOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            data = {
                "title": user_input["name"],
                CONF_OUTSIDE_ABSOLUTE_HUMIDITY: user_input.get(CONF_OUTSIDE_ABSOLUTE_HUMIDITY, ""),
                CONF_OUTSIDE_WEATHER: user_input.get(CONF_OUTSIDE_WEATHER, ""),
                CONF_SUN_ENTITY: user_input.get(CONF_SUN_ENTITY, ""),
                CONF_NOTIFICATION_ENABLED: user_input.get(CONF_NOTIFICATION_ENABLED, True),
                CONF_NOTIFY_SERVICE: user_input.get(CONF_NOTIFY_SERVICE, ""),
                CONF_NOTIFICATION_COOLDOWN: int(user_input.get(CONF_NOTIFICATION_COOLDOWN, DEFAULT_NOTIFICATION_COOLDOWN)),
                CONF_ROOMS: [],
            }
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input["name"], data=data)

        return self.async_show_form(step_id="user", data_schema=_global_schema(self.hass))


class RoomClimateOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry) -> None:
        self.entry = entry
        self._editing_room_id: str | None = None
        self._remove_room_id: str | None = None

    @property
    def config(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return deepcopy(self.config.get(CONF_ROOMS, []))

    def _store(self, updates: dict[str, Any]):
        current = deepcopy(self.config)
        current.update(updates)
        return self.async_create_entry(title=current.get("title", DEFAULT_NAME), data=current)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        options = ["global", "add_room"]
        if self.rooms:
            options.extend(["edit_room_select", "remove_room"])
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_global(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self._store(
                {
                    "title": user_input["name"],
                    CONF_OUTSIDE_ABSOLUTE_HUMIDITY: user_input.get(CONF_OUTSIDE_ABSOLUTE_HUMIDITY, ""),
                    CONF_OUTSIDE_WEATHER: user_input.get(CONF_OUTSIDE_WEATHER, ""),
                    CONF_SUN_ENTITY: user_input.get(CONF_SUN_ENTITY, ""),
                    CONF_NOTIFICATION_ENABLED: user_input.get(CONF_NOTIFICATION_ENABLED, True),
                    CONF_NOTIFY_SERVICE: user_input.get(CONF_NOTIFY_SERVICE, ""),
                    CONF_NOTIFICATION_COOLDOWN: int(user_input.get(CONF_NOTIFICATION_COOLDOWN, DEFAULT_NOTIFICATION_COOLDOWN)),
                }
            )
        return self.async_show_form(step_id="global", data_schema=_global_schema(self.hass, self.config))

    async def async_step_add_room(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            room_id = slugify(user_input[CONF_ROOM_NAME])
            existing_ids = {room[CONF_ROOM_ID] for room in self.rooms}
            base = room_id
            counter = 2
            while room_id in existing_ids:
                room_id = f"{base}_{counter}"
                counter += 1
            room = {CONF_ROOM_ID: room_id, **user_input}
            rooms = self.rooms
            rooms.append(room)
            return self._store({CONF_ROOMS: rooms})
        return self.async_show_form(step_id="add_room", data_schema=_room_schema())

    async def async_step_edit_room_select(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._editing_room_id = user_input[CONF_ROOM_ID]
            return await self.async_step_edit_room()

        options = [
            selector.SelectOptionDict(value=room[CONF_ROOM_ID], label=room[CONF_ROOM_NAME])
            for room in self.rooms
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ROOM_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="edit_room_select", data_schema=schema)

    async def async_step_edit_room(self, user_input: dict[str, Any] | None = None):
        room = next(room for room in self.rooms if room[CONF_ROOM_ID] == self._editing_room_id)
        if user_input is not None:
            rooms = self.rooms
            for index, item in enumerate(rooms):
                if item[CONF_ROOM_ID] == self._editing_room_id:
                    rooms[index] = {CONF_ROOM_ID: self._editing_room_id, **user_input}
                    break
            return self._store({CONF_ROOMS: rooms})
        return self.async_show_form(step_id="edit_room", data_schema=_room_schema(room))

    async def async_step_remove_room(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            rooms = [room for room in self.rooms if room[CONF_ROOM_ID] != user_input[CONF_ROOM_ID]]
            return self._store({CONF_ROOMS: rooms})
        options = [
            selector.SelectOptionDict(value=room[CONF_ROOM_ID], label=room[CONF_ROOM_NAME])
            for room in self.rooms
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ROOM_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="remove_room", data_schema=schema)
