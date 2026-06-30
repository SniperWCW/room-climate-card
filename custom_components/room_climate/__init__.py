from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CARD_FILENAME, CARD_URL_PATH, DOMAIN, PLATFORMS
from .coordinator import IntegrationData, RoomClimateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = RoomClimateCoordinator(hass, entry)
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = IntegrationData(coordinator=coordinator)

    if not domain_data.get("card_registered"):
        card_path = Path(__file__).parent / "www" / CARD_FILENAME
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_URL_PATH, str(card_path), cache_headers=False)]
            )
        except RuntimeError as err:
            if "method GET is already registered" not in str(err):
                raise
            _LOGGER.debug("Static path %s was already registered, reusing it", CARD_URL_PATH)
        add_extra_js_url(hass, CARD_URL_PATH)
        domain_data["card_registered"] = True

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        domain_data = hass.data[DOMAIN]
        domain_data.pop(entry.entry_id, None)
        remaining_entries = [key for key in domain_data if key != "card_registered"]
        if not remaining_entries and domain_data.get("card_registered"):
            remove_extra_js_url(hass, CARD_URL_PATH)
            domain_data["card_registered"] = False
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
