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
    entities: list[SensorEntity] = [
        RoomClimateOverviewSensor(coordinator, entry),
        RoomClimateHouseVentilationSensor(coordinator, entry),
        RoomClimateNextVentilationWindowSensor(coordinator, entry),
    ]
    for room in rooms:
        room_id = room["id"]
        entities.append(RoomClimateScoreSensor(coordinator, entry, room_id))
        entities.append(RoomClimateRecommendationSensor(coordinator, entry, room_id))
    async_add_entities(entities)


class RoomClimateOverviewSensor(CoordinatorEntity[RoomClimateCoordinator], SensorEntity):
    """Expose the house-wide briefing for dashboards and automations."""

    _attr_has_entity_name = True
    _attr_name = "Tageslage"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_overview"

    @property
    def native_value(self):
        return self.coordinator.data["overview"]["day_type"]

    @property
    def icon(self):
        return self.coordinator.data["overview"]["icon"]

    @property
    def extra_state_attributes(self):
        return {"managed_by": DOMAIN, **self.coordinator.data["overview"]}

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Room Climate",
            "manufacturer": "SniperWCW",
            "model": "Room Climate",
        }


class RoomClimateHouseBaseSensor(CoordinatorEntity[RoomClimateCoordinator], SensorEntity):
    """Base class for house-wide Room Climate sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry

    @property
    def ventilation(self):
        return self.coordinator.data["house_ventilation"]

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Room Climate",
            "manufacturer": "SniperWCW",
            "model": "Room Climate",
        }


class RoomClimateHouseVentilationSensor(RoomClimateHouseBaseSensor):
    """Expose the current house-wide ventilation recommendation."""

    _attr_name = "Hauslüftung"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_house_ventilation"

    @property
    def native_value(self):
        return self.ventilation.title

    @property
    def icon(self):
        return self.ventilation.icon

    @property
    def extra_state_attributes(self):
        return {"managed_by": DOMAIN, **self.ventilation.attributes()}


class RoomClimateNextVentilationWindowSensor(RoomClimateHouseBaseSensor):
    """Expose the best forecast ventilation window."""

    _attr_name = "Nächstes Lüftungsfenster"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_ventilation_window"

    @property
    def native_value(self):
        return self.ventilation.next_window.label

    @property
    def extra_state_attributes(self):
        return {
            "managed_by": DOMAIN,
            "ventilation_window": True,
            **self.ventilation.next_window.attributes(),
        }


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
            "wind_effect": self.room.attributes.get("wind_effect"),
            "window_orientation": self.room.orientation_label,
            "ventilate_now": self.room.ventilate_now,
            "close_window": self.room.close_window,
            "close_window_reason": self.room.close_window_reason,
            "close_cover": self.room.close_cover,
            "close_cover_reason": self.room.close_cover_reason,
            "inputs_available": self.room.attributes.get("inputs_available"),
            "data_quality": self.room.attributes.get("data_quality"),
        }
