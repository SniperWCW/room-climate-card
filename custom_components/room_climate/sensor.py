from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DAY_TYPES, DOMAIN, HOUSE_VENTILATION_MODES, LEVEL_CLASSES
from .coordinator import RoomClimateConfigEntry, RoomClimateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
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


def _house_device_info(entry: RoomClimateConfigEntry) -> dict:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Room Climate",
        "manufacturer": "SniperWCW",
        "model": "Room Climate",
    }


class RoomClimateHouseBaseSensor(CoordinatorEntity[RoomClimateCoordinator], SensorEntity):
    """Base class for house-wide Room Climate sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_device_info = _house_device_info(entry)

    @property
    def ventilation(self):
        return self.coordinator.data["house_ventilation"]


class RoomClimateOverviewSensor(RoomClimateHouseBaseSensor):
    """House-wide day briefing. The state is a stable key, translated in the UI."""

    _attr_translation_key = "overview"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = DAY_TYPES

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_overview"

    @property
    def native_value(self):
        return self.coordinator.data["overview"]["day_type_key"]

    @property
    def icon(self):
        return self.coordinator.data["overview"]["icon"]

    @property
    def extra_state_attributes(self):
        return {"managed_by": DOMAIN, **self.coordinator.data["overview"]}


class RoomClimateHouseVentilationSensor(RoomClimateHouseBaseSensor):
    """Current house-wide ventilation recommendation.

    The state is the mode key (e.g. ``keep_windows_closed``) so automations do
    not depend on display text; the German title is in the ``title`` attribute.
    """

    _attr_translation_key = "house_ventilation"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = HOUSE_VENTILATION_MODES
    # Large, frequently changing attributes stay out of the recorder database.
    _unrecorded_attributes = frozenset(
        {"sessions", "next_window", "session_detail", "session_elapsed_minutes", "session_remaining_minutes"}
    )

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_house_ventilation"

    @property
    def native_value(self):
        mode = self.ventilation.mode
        return mode if mode in HOUSE_VENTILATION_MODES else "unknown"

    @property
    def icon(self):
        return self.ventilation.icon

    @property
    def extra_state_attributes(self):
        return {
            "managed_by": DOMAIN,
            "title": self.ventilation.title,
            **self.ventilation.attributes(),
        }


class RoomClimateNextVentilationWindowSensor(RoomClimateHouseBaseSensor):
    """Best forecast ventilation window."""

    _attr_translation_key = "next_ventilation_window"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry) -> None:
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
    _attr_has_entity_name = True

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry, room_id: str) -> None:
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
    _attr_translation_key = "score"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-thermometer"
    _unrecorded_attributes = frozenset(
        {
            "description",
            "recommendation",
            "dehumidify_advice",
            "cooling_advice",
            "next_ventilation_window",
            "close_window_reason",
            "close_cover_reason",
            "mold_risk_reason",
            "reduce_heating_reason",
            "wind_effect",
            "input_age_minutes",
            "notification_flags",
        }
    )

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry, room_id: str) -> None:
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
    """Comfort level of the room as a stable key (excellent … critical)."""

    _attr_translation_key = "recommendation"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LEVEL_CLASSES
    _attr_icon = "mdi:text-box-check-outline"

    def __init__(self, coordinator: RoomClimateCoordinator, entry: RoomClimateConfigEntry, room_id: str) -> None:
        super().__init__(coordinator, entry, room_id)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_recommendation"

    @property
    def native_value(self):
        level = self.room.level_class
        return level if level in LEVEL_CLASSES else "unknown"

    @property
    def extra_state_attributes(self):
        return {
            "managed_by": DOMAIN,
            "room_id": self.room.room_id,
            "room_name": self.room.name,
            "level": self.room.level_label,
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
            "mold_risk": self.room.attributes.get("mold_risk"),
            "reduce_heating": self.room.reduce_heating,
            "inputs_available": self.room.attributes.get("inputs_available"),
            "data_quality": self.room.attributes.get("data_quality"),
        }
