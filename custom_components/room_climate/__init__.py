from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CARD_FILENAME, CARD_URL_PATH, CONF_ROOMS, DOMAIN, PLATFORMS
from .coordinator import IntegrationData, RoomClimateCoordinator

_LOGGER = logging.getLogger(__name__)
ROOM_ENTITY_SUFFIXES = (
    "_score",
    "_recommendation",
    "_ventilate_now",
    "_close_window",
    "_close_cover",
)


def _card_resource_url(card_path: Path) -> str:
    # Use the file modification time as a lightweight cache buster so Home Assistant
    # loads the updated card bundle after integration upgrades.
    version_token = int(card_path.stat().st_mtime)
    return f"{CARD_URL_PATH}?v={version_token}"


def _configured_room_ids(entry: ConfigEntry) -> set[str]:
    config = {**entry.data, **entry.options}
    return {
        room["id"]
        for room in config.get(CONF_ROOMS, [])
        if isinstance(room, dict) and room.get("id")
    }


def _extract_room_id_from_unique_id(unique_id: str, entry_id: str) -> str | None:
    prefix = f"{entry_id}_"
    if not unique_id.startswith(prefix):
        return None

    remainder = unique_id[len(prefix):]
    for suffix in ROOM_ENTITY_SUFFIXES:
        if remainder.endswith(suffix):
            return remainder[: -len(suffix)]
    return None


def _extract_room_id_from_identifiers(identifiers: set[tuple[str, str]], entry_id: str) -> str | None:
    prefix = f"{entry_id}_"
    for domain, identifier in identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        return identifier[len(prefix):]
    return None


def _prune_removed_room_registry_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    configured_room_ids = _configured_room_ids(entry)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        room_id = _extract_room_id_from_unique_id(entity_entry.unique_id, entry.entry_id)
        if room_id and room_id not in configured_room_ids:
            entity_registry.async_remove(entity_entry.entity_id)

    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        room_id = _extract_room_id_from_identifiers(device_entry.identifiers, entry.entry_id)
        if room_id and room_id not in configured_room_ids:
            device_registry.async_remove_device(device_entry.id)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = RoomClimateCoordinator(hass, entry)
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = IntegrationData(coordinator=coordinator)

    if not domain_data.get("card_registered"):
        card_path = Path(__file__).parent / "www" / CARD_FILENAME
        card_resource_url = _card_resource_url(card_path)
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_URL_PATH, str(card_path), cache_headers=False)]
            )
        except RuntimeError as err:
            if "method GET is already registered" not in str(err):
                raise
            _LOGGER.debug("Static path %s was already registered, reusing it", CARD_URL_PATH)
        add_extra_js_url(hass, card_resource_url)
        domain_data["card_registered"] = True
        domain_data["card_resource_url"] = card_resource_url

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _prune_removed_room_registry_entries(hass, entry)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        domain_data = hass.data[DOMAIN]
        domain_data.pop(entry.entry_id, None)
        remaining_entries = [
            key for key in domain_data if key not in {"card_registered", "card_resource_url"}
        ]
        if not remaining_entries and domain_data.get("card_registered"):
            remove_extra_js_url(hass, domain_data.get("card_resource_url", CARD_URL_PATH))
            domain_data["card_registered"] = False
            domain_data.pop("card_resource_url", None)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
