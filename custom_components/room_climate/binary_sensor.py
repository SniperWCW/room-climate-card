from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RoomClimateConfigEntry, RoomClimateCoordinator

# flag attribute on RoomResult -> (translation key, icon, reason attribute, device class)
FLAGS = (
    ("ventilate_now", "mdi:window-open-variant", None, None),
    ("close_window", "mdi:window-closed-variant", "close_window_reason", None),
    ("close_cover", "mdi:roller-shade-closed", "close_cover_reason", None),
    ("mold_risk", "mdi:water-alert", "mold_risk_reason", BinarySensorDeviceClass.PROBLEM),
    ("reduce_heating", "mdi:radiator-off", "reduce_heating_reason", None),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    rooms = coordinator.config.get("rooms", [])
    async_add_entities(
        RoomClimateFlagSensor(coordinator, entry, room["id"], flag, icon, reason, device_class)
        for room in rooms
        for flag, icon, reason, device_class in FLAGS
    )


class RoomClimateFlagSensor(CoordinatorEntity[RoomClimateCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset(
        {"recommendation", "next_ventilation_window", "description", "reason"}
    )

    def __init__(
        self,
        coordinator: RoomClimateCoordinator,
        entry: RoomClimateConfigEntry,
        room_id: str,
        flag: str,
        icon: str,
        reason_attribute: str | None,
        device_class: BinarySensorDeviceClass | None,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.room_id = room_id
        self.flag = flag
        self._reason_attribute = reason_attribute
        self._attr_translation_key = flag
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_{flag}"

    @property
    def room(self):
        return self.coordinator.data["rooms"][self.room_id]

    @property
    def is_on(self):
        return bool(getattr(self.room, self.flag))

    @property
    def extra_state_attributes(self):
        return {
            "reason": getattr(self.room, self._reason_attribute) if self._reason_attribute else None,
            "recommendation": self.room.recommendation,
            "next_ventilation_window": self.room.next_window,
            "description": self.room.description,
            # Kept for automations written before 0.4.
            "close_window_reason": self.room.close_window_reason,
            "close_cover_reason": self.room.close_cover_reason,
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.entry.entry_id}_{self.room_id}")},
            "name": f"Room Climate {self.room.name}",
            "manufacturer": "SniperWCW",
            "model": "Room Climate",
        }
