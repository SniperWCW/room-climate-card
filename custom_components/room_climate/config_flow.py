from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
import unicodedata

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

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


def _normalized_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    ascii_text = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def _guess_room_type(name: str) -> str:
    value = _normalized_slug(name)
    if "bad" in value or "badezimmer" in value:
        return "bathroom"
    if "kuche" in value or "kueche" in value:
        return "kitchen"
    if "keller" in value:
        return "basement"
    if "kind" in value:
        return "child"
    if "schlaf" in value:
        return "bedroom"
    if "buro" in value or "buero" in value:
        return "office"
    if "wohn" in value or "essen" in value:
        return "living"
    return "default"


def _find_global_outside_humidity(hass) -> str:
    states = list(hass.states.async_entity_ids())
    return next(
        (
            entity_id
            for entity_id in states
            if ("aussen" in entity_id or "außen" in entity_id or "outside" in entity_id)
            and ("absolute_luftfeuchtigkeit" in entity_id or "absolute" in entity_id)
        ),
        "",
    )


def _find_global_weather_entity(hass) -> str:
    return next((entity_id for entity_id in hass.states.async_entity_ids() if entity_id.startswith("weather.")), "")


def _find_global_sun_entity(hass) -> str:
    entity_ids = list(hass.states.async_entity_ids())
    return next(
        (entity_id for entity_id in entity_ids if entity_id == "sun.sun"),
        next((entity_id for entity_id in entity_ids if entity_id.startswith("sun.")), ""),
    )


def _optional_entity_field(
    key: str,
    defaults: dict[str, Any],
    domain: str,
) -> tuple[vol.Marker, selector.EntitySelector]:
    if defaults.get(key):
        marker = vol.Optional(key, default=defaults[key])
    else:
        marker = vol.Optional(key)
    return marker, selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _detect_rooms(hass) -> list[dict[str, Any]]:
    states = list(hass.states.async_entity_ids())
    areas = ar.async_get(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    def is_entity_in_area(entity_id: str, area_id: str) -> bool:
        entity_entry = entities.async_get(entity_id)
        device_entry = devices.async_get(entity_entry.device_id) if entity_entry and entity_entry.device_id else None
        return bool(
            (entity_entry and entity_entry.area_id == area_id)
            or (device_entry and device_entry.area_id == area_id)
        )

    def find_entity_for_area(area_id: str, patterns: tuple[str, ...], domains: tuple[str, ...] = ("sensor",)) -> str:
        return next(
            (
                entity_id
                for entity_id in states
                if entity_id.split(".")[0] in domains
                and is_entity_in_area(entity_id, area_id)
                and all(pattern in _normalized_slug(entity_id) for pattern in patterns)
            ),
            "",
        )

    room_map: dict[str, dict[str, Any]] = {}
    ignore_parts = {
        "sensor",
        "thermal",
        "comfort",
        "absolute",
        "luftfeuchtigkeit",
        "humidity",
        "temperatur",
        "temperature",
        "humidex",
        "simmer",
        "taupunkt",
        "gefuhlt",
        "sommer",
        "scharlau",
        "window",
        "fenster",
        "contact",
        "kontakt",
        "value",
        "cover",
        "shade",
        "rollladen",
        "rollerladen",
    }

    def ensure_room(name: str) -> dict[str, Any]:
        key = _normalized_slug(name or "raum")
        if key not in room_map:
            room_map[key] = {
                CONF_ROOM_NAME: name or "Raum",
                CONF_ROOM_TYPE: _guess_room_type(name or "Raum"),
                CONF_WINDOW_ORIENTATION: "",
                CONF_TEMPERATURE: "",
                CONF_HUMIDITY: "",
                CONF_INSIDE_ABSOLUTE_HUMIDITY: "",
                CONF_WINDOW: "",
                CONF_COVER: "",
                CONF_HUMIDEX_VALUE: "",
                CONF_SCHARLAU: "",
                CONF_HUMIDEX: "",
                CONF_SIMMER: "",
                CONF_DEWPOINT: "",
                CONF_ROOM_NOTIFICATIONS: True,
            }
        return room_map[key]

    for area in areas.async_list_areas():
        room = ensure_room(area.name)
        room[CONF_ROOM_TYPE] = _guess_room_type(area.name)
        room[CONF_TEMPERATURE] = room[CONF_TEMPERATURE] or find_entity_for_area(area.id, ("temperatur",)) or find_entity_for_area(area.id, ("temperature",))
        room[CONF_HUMIDITY] = room[CONF_HUMIDITY] or find_entity_for_area(area.id, ("luftfeuchtigkeit",)) or find_entity_for_area(area.id, ("humidity",))
        room[CONF_INSIDE_ABSOLUTE_HUMIDITY] = (
            room[CONF_INSIDE_ABSOLUTE_HUMIDITY]
            or find_entity_for_area(area.id, ("absolute_luftfeuchtigkeit",))
            or find_entity_for_area(area.id, ("absolute_humidity",))
        )
        room[CONF_WINDOW] = (
            room[CONF_WINDOW]
            or find_entity_for_area(area.id, ("fenster",), ("binary_sensor",))
            or find_entity_for_area(area.id, ("window",), ("binary_sensor",))
        )
        room[CONF_COVER] = (
            room[CONF_COVER]
            or find_entity_for_area(area.id, ("cover",), ("cover",))
            or find_entity_for_area(area.id, ("rollladen",), ("cover",))
            or find_entity_for_area(area.id, ("rollerladen",), ("cover",))
            or find_entity_for_area(area.id, ("shade",), ("cover",))
        )
        room[CONF_HUMIDEX_VALUE] = room[CONF_HUMIDEX_VALUE] or find_entity_for_area(area.id, ("thermal_comfort", "humidex"))
        room[CONF_SCHARLAU] = room[CONF_SCHARLAU] or find_entity_for_area(area.id, ("thermal_comfort", "sommer_scharlau_gefuhlt"))
        room[CONF_HUMIDEX] = room[CONF_HUMIDEX] or find_entity_for_area(area.id, ("thermal_comfort", "humidex_gefuhlt"))
        room[CONF_SIMMER] = room[CONF_SIMMER] or find_entity_for_area(area.id, ("thermal_comfort", "sommer_simmer_gefuhlt"))
        room[CONF_DEWPOINT] = room[CONF_DEWPOINT] or find_entity_for_area(area.id, ("thermal_comfort", "taupunkt_gefuhlt"))

    for entity_id in states:
        domain, _, object_id = entity_id.partition(".")
        if domain not in {"sensor", "binary_sensor", "cover"} or not object_id:
            continue
        slug = _normalized_slug(object_id)
        parts = [part for part in slug.split("_") if part and part not in ignore_parts]
        if not parts:
            continue
        room_name = " ".join(part.capitalize() for part in parts)
        room = ensure_room(room_name)

        if domain == "binary_sensor" and re.search(r"(fenster|window|kontakt|contact)", slug):
            room[CONF_WINDOW] = room[CONF_WINDOW] or entity_id
        elif domain == "cover" and re.search(r"(cover|shade|rollladen|rollerladen)", slug):
            room[CONF_COVER] = room[CONF_COVER] or entity_id
        elif re.search(r"(^|_)(temperatur|temperature)($|_)", slug):
            room[CONF_TEMPERATURE] = room[CONF_TEMPERATURE] or entity_id
        elif re.search(r"(^|_)(luftfeuchtigkeit|humidity)($|_)", slug) and "absolute" not in slug:
            room[CONF_HUMIDITY] = room[CONF_HUMIDITY] or entity_id
        elif "absolute_luftfeuchtigkeit" in slug or "absolute_humidity" in slug:
            room[CONF_INSIDE_ABSOLUTE_HUMIDITY] = room[CONF_INSIDE_ABSOLUTE_HUMIDITY] or entity_id
        elif "scharlau" in slug:
            room[CONF_SCHARLAU] = room[CONF_SCHARLAU] or entity_id
        elif re.search(r"humidex.*gefuhlt|gefuhlt.*humidex", slug):
            room[CONF_HUMIDEX] = room[CONF_HUMIDEX] or entity_id
        elif "humidex" in slug:
            room[CONF_HUMIDEX_VALUE] = room[CONF_HUMIDEX_VALUE] or entity_id
        elif "simmer" in slug:
            room[CONF_SIMMER] = room[CONF_SIMMER] or entity_id
        elif "taupunkt" in slug:
            room[CONF_DEWPOINT] = room[CONF_DEWPOINT] or entity_id

    return [
        room
        for room in room_map.values()
        if any(
            room.get(key)
            for key in (
                CONF_TEMPERATURE,
                CONF_HUMIDITY,
                CONF_INSIDE_ABSOLUTE_HUMIDITY,
                CONF_WINDOW,
                CONF_COVER,
                CONF_HUMIDEX_VALUE,
                CONF_SCHARLAU,
                CONF_HUMIDEX,
                CONF_SIMMER,
                CONF_DEWPOINT,
            )
        )
    ]


def _next_detected_room_defaults(hass, configured_rooms: list[dict[str, Any]]) -> dict[str, Any]:
    configured_ids = {room.get(CONF_ROOM_ID) for room in configured_rooms}
    configured_names = {room.get(CONF_ROOM_NAME) for room in configured_rooms}
    for room in _detect_rooms(hass):
        if slugify(room.get(CONF_ROOM_NAME, "")) not in configured_ids and room.get(CONF_ROOM_NAME) not in configured_names:
            return room
    return {}


def _available_detected_rooms(hass, configured_rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    configured_ids = {room.get(CONF_ROOM_ID) for room in configured_rooms}
    configured_names = {room.get(CONF_ROOM_NAME) for room in configured_rooms}
    available: list[dict[str, Any]] = []
    for room in _detect_rooms(hass):
        room_name = room.get(CONF_ROOM_NAME, "")
        room_slug = slugify(room_name)
        if room_slug in configured_ids or room_name in configured_names:
            continue
        available.append(room)
    return available


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
    outside_abs_field = _optional_entity_field(CONF_OUTSIDE_ABSOLUTE_HUMIDITY, defaults, "sensor")
    outside_weather_field = _optional_entity_field(CONF_OUTSIDE_WEATHER, defaults, "weather")
    sun_field = _optional_entity_field(CONF_SUN_ENTITY, defaults, "sun")
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("title", DEFAULT_NAME)): selector.TextSelector(),
            outside_abs_field[0]: outside_abs_field[1],
            outside_weather_field[0]: outside_weather_field[1],
            sun_field[0]: sun_field[1],
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
    inside_abs_field = _optional_entity_field(CONF_INSIDE_ABSOLUTE_HUMIDITY, defaults, "sensor")
    window_field = _optional_entity_field(CONF_WINDOW, defaults, "binary_sensor")
    cover_field = _optional_entity_field(CONF_COVER, defaults, "cover")
    humidex_value_field = _optional_entity_field(CONF_HUMIDEX_VALUE, defaults, "sensor")
    scharlau_field = _optional_entity_field(CONF_SCHARLAU, defaults, "sensor")
    humidex_field = _optional_entity_field(CONF_HUMIDEX, defaults, "sensor")
    simmer_field = _optional_entity_field(CONF_SIMMER, defaults, "sensor")
    dewpoint_field = _optional_entity_field(CONF_DEWPOINT, defaults, "sensor")
    return vol.Schema(
        {
            vol.Required(CONF_ROOM_NAME, default=defaults.get(CONF_ROOM_NAME, "")): selector.TextSelector(),
            vol.Required(CONF_ROOM_TYPE, default=defaults.get(CONF_ROOM_TYPE, "default")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=room_type_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_WINDOW_ORIENTATION, default=defaults.get(CONF_WINDOW_ORIENTATION, "")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=orientation_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_TEMPERATURE, default=defaults.get(CONF_TEMPERATURE, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_HUMIDITY, default=defaults.get(CONF_HUMIDITY, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            inside_abs_field[0]: inside_abs_field[1],
            window_field[0]: window_field[1],
            cover_field[0]: cover_field[1],
            humidex_value_field[0]: humidex_value_field[1],
            scharlau_field[0]: scharlau_field[1],
            humidex_field[0]: humidex_field[1],
            simmer_field[0]: simmer_field[1],
            dewpoint_field[0]: dewpoint_field[1],
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

        defaults = {
            "title": DEFAULT_NAME,
            CONF_OUTSIDE_ABSOLUTE_HUMIDITY: _find_global_outside_humidity(self.hass),
            CONF_OUTSIDE_WEATHER: _find_global_weather_entity(self.hass),
            CONF_SUN_ENTITY: _find_global_sun_entity(self.hass),
            CONF_NOTIFICATION_ENABLED: DEFAULT_NOTIFICATION_ENABLED,
            CONF_NOTIFICATION_COOLDOWN: DEFAULT_NOTIFICATION_COOLDOWN,
        }
        return self.async_show_form(step_id="user", data_schema=_global_schema(self.hass, defaults))


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
        options = ["global"]
        if _available_detected_rooms(self.hass, self.rooms):
            options.append("import_detected_rooms")
        options.append("add_room_manual")
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
        if _available_detected_rooms(self.hass, self.rooms):
            return await self.async_step_import_detected_rooms(user_input)
        return await self.async_step_add_room_manual(user_input)

    async def async_step_add_room_manual(self, user_input: dict[str, Any] | None = None):
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
        defaults = _next_detected_room_defaults(self.hass, self.rooms)
        return self.async_show_form(step_id="add_room", data_schema=_room_schema(defaults))

    async def async_step_import_detected_rooms(self, user_input: dict[str, Any] | None = None):
        detected_rooms = _available_detected_rooms(self.hass, self.rooms)
        room_options = [
            selector.SelectOptionDict(
                value=slugify(room.get(CONF_ROOM_NAME, "")),
                label=room.get(CONF_ROOM_NAME, "Raum"),
            )
            for room in detected_rooms
        ]

        if user_input is not None:
            selected_ids = set(user_input.get("selected_rooms", []))
            rooms = self.rooms
            existing_ids = {room[CONF_ROOM_ID] for room in rooms}

            for detected_room in detected_rooms:
                base_id = slugify(detected_room.get(CONF_ROOM_NAME, "room"))
                if base_id not in selected_ids:
                    continue

                room_id = base_id
                counter = 2
                while room_id in existing_ids:
                    room_id = f"{base_id}_{counter}"
                    counter += 1

                rooms.append({CONF_ROOM_ID: room_id, **detected_room})
                existing_ids.add(room_id)

            return self._store({CONF_ROOMS: rooms})

        schema = vol.Schema(
            {
                vol.Required("selected_rooms", default=[option["value"] for option in room_options]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=room_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="import_detected_rooms", data_schema=schema)

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
