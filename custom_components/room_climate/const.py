from __future__ import annotations

from datetime import timedelta

DOMAIN = "room_climate"
PLATFORMS = ["sensor", "binary_sensor"]

CONF_ROOMS = "rooms"
CONF_OUTSIDE_ABSOLUTE_HUMIDITY = "outside_absolute_humidity"
CONF_OUTSIDE_WEATHER = "outside_weather"
CONF_SUN_ENTITY = "sun_entity"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFICATION_ENABLED = "notification_enabled"
CONF_NOTIFICATION_COOLDOWN = "notification_cooldown"

CONF_ROOM_ID = "id"
CONF_ROOM_NAME = "name"
CONF_ROOM_TYPE = "room_type"
CONF_WINDOW_ORIENTATION = "window_orientation"
CONF_TEMPERATURE = "temperature"
CONF_HUMIDITY = "humidity"
CONF_INSIDE_ABSOLUTE_HUMIDITY = "inside_absolute_humidity"
CONF_HUMIDEX_VALUE = "humidex_value"
CONF_SCHARLAU = "scharlau"
CONF_HUMIDEX = "humidex"
CONF_SIMMER = "simmer"
CONF_DEWPOINT = "dewpoint"
CONF_WINDOW = "window"
CONF_COVER = "cover"
CONF_ROOM_NOTIFICATIONS = "notifications_enabled"

DEFAULT_NAME = "Room Climate"
DEFAULT_NOTIFICATION_ENABLED = True
DEFAULT_NOTIFICATION_COOLDOWN = 120
DEFAULT_UPDATE_MINUTES = 5
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_MINUTES)

CARD_FILENAME = "room-climate-card.js"
CARD_URL_PATH = f"/room-climate/{CARD_FILENAME}"

ROOM_TYPE_OPTIONS = [
    "default",
    "living",
    "bedroom",
    "child",
    "bathroom",
    "kitchen",
    "basement",
    "office",
]

WINDOW_ORIENTATION_OPTIONS = ["", "N", "NO", "O", "SO", "S", "SW", "W", "NW"]

NOTIFICATION_VENTILATE = "ventilate_now"
NOTIFICATION_CLOSE_WINDOW = "close_window"
NOTIFICATION_CLOSE_COVER = "close_cover"
