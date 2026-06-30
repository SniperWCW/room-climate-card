from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
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
    entities: list[SensorEntity] = []
    for room in rooms:
        room_id = room["id"]
        entities.append(RoomClimateScoreSensor(coordinator, entry, room_id))
        entities.append(RoomClimateRecommendationSensor(coordinator, entry, room_id))
    async_add_entities(entities)


class RoomClimateBaseSensor(CoordinatorEntity[RoomClimateCoordinator], SensorEntity):
    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry, room_id: str) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.room_id = room_id

    @property
    def room(self):
        return self.coordinator.data["rooms"][self.room_id]

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.entry.entry_id}_{self.room_id}")},
            "name": f"Room Climate {self.room.name}",
            "manufacturer": "SniperWCW",
            "model": "Room Climate",
        }


class RoomClimateScoreSensor(RoomClimateBaseSensor):
    _attr_has_entity_name = True
    _attr_name = "Score"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry, room_id: str) -> None:
        super().__init__(coordinator, entry, room_id)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_score"

    @property
    def native_value(self):
        return self.room.score

    @property
    def extra_state_attributes(self):
        attrs = dict(self.room.attributes)
        attrs["managed_by"] = DOMAIN
        attrs["room_id"] = self.room.room_id
        attrs["room_name"] = self.room.name
        attrs["level"] = self.room.level_label
        attrs["level_icon"] = self.room.level_icon
        attrs["level_class"] = self.room.level_class
        attrs["description"] = self.room.description
        attrs["recommendation"] = self.room.recommendation
        return attrs


class RoomClimateRecommendationSensor(RoomClimateBaseSensor):
    _attr_has_entity_name = True
    _attr_name = "Recommendation"
    _attr_icon = "mdi:text-box-check-outline"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry, room_id: str) -> None:
        super().__init__(coordinator, entry, room_id)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_recommendation"

    @property
    def native_value(self):
        return self.room.level_label

    @property
    def extra_state_attributes(self):
        return {
            "managed_by": DOMAIN,
            "room_id": self.room.room_id,
            "room_name": self.room.name,
            "recommendation": self.room.recommendation,
            "next_ventilation_window": self.room.next_window,
            "solar_exposure": self.room.solar_label,
            "window_orientation": self.room.orientation_label,
        }
