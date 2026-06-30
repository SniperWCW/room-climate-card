from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RoomClimateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RoomClimateCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    rooms = coordinator.config.get("rooms", [])
    entities: list[BinarySensorEntity] = []
    for room in rooms:
        room_id = room["id"]
        entities.extend(
            [
                RoomClimateFlagSensor(coordinator, entry, room_id, "ventilate_now", "Lueften jetzt", "mdi:window-open-variant"),
                RoomClimateFlagSensor(coordinator, entry, room_id, "close_window", "Fenster schliessen", "mdi:window-closed-variant"),
                RoomClimateFlagSensor(coordinator, entry, room_id, "close_cover", "Rollladen schliessen", "mdi:roller-shade-closed"),
            ]
        )
    async_add_entities(entities)


class RoomClimateFlagSensor(CoordinatorEntity[RoomClimateCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RoomClimateCoordinator,
        entry: ConfigEntry,
        room_id: str,
        flag: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.room_id = room_id
        self.flag = flag
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_{flag}"

    @property
    def room(self):
        return self.coordinator.data["rooms"][self.room_id]

    @property
    def is_on(self):
        return getattr(self.room, self.flag)

    @property
    def extra_state_attributes(self):
        return {
            "recommendation": self.room.recommendation,
            "next_ventilation_window": self.room.next_window,
            "description": self.room.description,
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.entry.entry_id}_{self.room_id}")},
            "name": f"Room Climate {self.room.name}",
            "manufacturer": "SniperWCW",
            "model": "Room Climate",
        }
