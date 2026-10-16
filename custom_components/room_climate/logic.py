from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_COVER,
    CONF_DEWPOINT,
    CONF_HUMIDEX,
    CONF_HUMIDEX_VALUE,
    CONF_HUMIDITY,
    CONF_INSIDE_ABSOLUTE_HUMIDITY,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOM_NOTIFICATIONS,
    CONF_ROOM_TYPE,
    CONF_SCHARLAU,
    CONF_SIMMER,
    CONF_TEMPERATURE,
    CONF_WINDOW,
    CONF_WINDOW_ORIENTATION,
    DEFAULT_WALL_QUALITY,
    NOTIFICATION_CLOSE_COVER,
    NOTIFICATION_CLOSE_WINDOW,
    NOTIFICATION_MOLD_RISK,
    NOTIFICATION_VENTILATE,
    WALL_QUALITY_FACTORS,
)
from .house_ventilation import (
    ORIENTATION_AZIMUTH,
    ORIENTATION_LABEL,
    absolute_humidity,
    angular_difference,
    day_prefix,
)

__all__ = [
    "ORIENTATION_AZIMUTH",
    "ORIENTATION_LABEL",
    "RoomResult",
    "absolute_humidity",
    "angular_difference",
    "as_float",
    "calculate_score",
    "evaluate_room",
    "slugify",
]

OPEN_WINDOW_STATES = {"on", "open", "tilted"}

# Hysteresis while a window is open: the room keeps "ventilation is useful"
# a little longer than it took to recommend it, so the advice does not flip
# between "open" and "close" around a threshold.
OPEN_HUMIDITY_RELAX = 3.0
OPEN_DIFF_FACTOR = 0.5
OPEN_TEMPERATURE_RELAX = 1.0
OPEN_COOLING_DELTA_RELAX = 0.5

# Rooms start to need cooling this many degrees above the profile's comfort
# maximum. This replaces the former fixed 27 °C room / 24 °C house thresholds.
COOLING_START_OFFSET = 2.0
# Never below this inside temperature: profiles such as kitchen or bedroom have
# a low comfort maximum (20 °C), which made normal heated rooms (22 °C) ask for
# cooling ventilation.
MIN_COOLING_START = 24.0
# Below this outside temperature it is heating season: cooling a room by
# ventilating only wastes heating energy, so no cooling advice is given.
MIN_OUTSIDE_TEMP_FOR_COOLING = 12.0


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or ""))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "room"


def as_float(value: Any) -> float | None:
    try:
        if value is None or value in ("unknown", "unavailable", ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_humidex_state(value: str | None) -> str | None:
    if value == "noticable_discomfort":
        return "noticeable_discomfort"
    return value


def get_room_profile(room_type: str) -> dict[str, float]:
    profiles: dict[str, dict[str, float]] = {
        "living": {"humidity_min": 40, "humidity_max": 55, "temp_min": 16, "temp_max": 22, "vent_moderate": 1.0, "vent_strong": 1.5},
        "bedroom": {"humidity_min": 40, "humidity_max": 55, "temp_min": 16, "temp_max": 20, "vent_moderate": 0.8, "vent_strong": 1.2},
        "child": {"humidity_min": 40, "humidity_max": 55, "temp_min": 16, "temp_max": 22, "vent_moderate": 1.0, "vent_strong": 1.4},
        "bathroom": {"humidity_min": 50, "humidity_max": 65, "temp_min": 20, "temp_max": 23, "vent_moderate": 0.6, "vent_strong": 1.0},
        "kitchen": {"humidity_min": 45, "humidity_max": 60, "temp_min": 18, "temp_max": 20, "vent_moderate": 0.8, "vent_strong": 1.3},
        "basement": {"humidity_min": 50, "humidity_max": 65, "temp_min": 10, "temp_max": 15, "vent_moderate": 1.5, "vent_strong": 2.0},
        "office": {"humidity_min": 40, "humidity_max": 55, "temp_min": 18, "temp_max": 22, "vent_moderate": 1.0, "vent_strong": 1.5},
        "default": {"humidity_min": 40, "humidity_max": 55, "temp_min": 18, "temp_max": 22, "vent_moderate": 1.0, "vent_strong": 1.5},
    }
    return profiles.get(room_type, profiles["default"])


def get_cooling_start(room_type: str, profile: dict[str, float]) -> float | None:
    """Return the inside temperature from which ventilating for cooling is useful.

    Basements are excluded: warm summer air condenses on the cold walls, so
    ventilating a basement for cooling does more harm than good.
    """
    if room_type == "basement":
        return None
    return max(profile["temp_max"] + COOLING_START_OFFSET, MIN_COOLING_START)


def get_dry_threshold(profile: dict[str, float]) -> float:
    return max(profile["humidity_min"] - 5, 35)


def get_solar_exposure(room: dict[str, Any], sun: dict[str, Any], outside_weather: dict[str, Any]) -> dict[str, str]:
    orientation = str(room.get(CONF_WINDOW_ORIENTATION, "")).upper()
    facade_azimuth = ORIENTATION_AZIMUTH.get(orientation)
    azimuth = outside_weather.get("sun_azimuth") if outside_weather.get("sun_azimuth") is not None else sun.get("azimuth")
    elevation = sun.get("elevation")

    if (
        facade_azimuth is None
        or not sun.get("above_horizon")
        or azimuth is None
        or elevation is None
        or elevation < 8
    ):
        return {"level": "none", "label": "kein relevanter Sonneneintrag"}

    angle_diff = angular_difference(azimuth, facade_azimuth)
    cloud_coverage = outside_weather.get("cloud_coverage")
    heavy_clouds = cloud_coverage is not None and cloud_coverage >= 85
    moderate_clouds = cloud_coverage is not None and cloud_coverage >= 65

    if angle_diff <= 65 and elevation >= 12 and not heavy_clouds:
        return {
            "level": "indirect" if moderate_clouds else "direct",
            "label": "leichter Sonneneintrag" if moderate_clouds else "direkter Sonneneintrag",
        }
    if angle_diff <= 95 and elevation >= 10 and not heavy_clouds:
        return {"level": "indirect", "label": "leichter Sonneneintrag"}
    return {"level": "none", "label": "kein relevanter Sonneneintrag"}


def get_wind_angle(room: dict[str, Any], outside_weather: dict[str, Any]) -> float | None:
    orientation = str(room.get(CONF_WINDOW_ORIENTATION, "")).upper()
    facade_azimuth = ORIENTATION_AZIMUTH.get(orientation)
    wind_bearing = outside_weather.get("wind_bearing")
    if facade_azimuth is None or wind_bearing is None:
        return None
    return angular_difference(wind_bearing, facade_azimuth)


def get_wind_duration_adjustment(room: dict[str, Any], outside_weather: dict[str, Any]) -> int:
    wind_speed = outside_weather.get("wind_speed")
    wind_angle = get_wind_angle(room, outside_weather)
    if wind_speed is None or wind_angle is None or wind_speed < 3:
        return 0
    if wind_angle <= 60:
        return -2 if wind_speed >= 16 else -1
    if wind_angle <= 110:
        return -1 if wind_speed >= 12 else 0
    if wind_angle >= 150 and wind_speed >= 12:
        return 1
    return 0


def get_wind_cooling_bonus(room: dict[str, Any], outside_weather: dict[str, Any]) -> float:
    wind_speed = outside_weather.get("wind_speed")
    wind_angle = get_wind_angle(room, outside_weather)
    if wind_speed is None or wind_angle is None or wind_speed < 5:
        return 0.0
    if wind_angle <= 60:
        return 0.8 if wind_speed >= 16 else 0.4
    if wind_angle <= 110:
        return 0.3 if wind_speed >= 12 else 0.0
    if wind_angle >= 150 and wind_speed >= 16:
        return -0.3
    return 0.0


def get_wind_effect_text(room: dict[str, Any], outside_weather: dict[str, Any]) -> str | None:
    """Describe the wind relative to the facade. Wind speed is expected in km/h."""
    wind_speed = outside_weather.get("wind_speed")
    wind_bearing = outside_weather.get("wind_bearing")
    wind_angle = get_wind_angle(room, outside_weather)
    if wind_speed is None or wind_bearing is None or wind_angle is None or wind_speed < 3:
        return None

    directions = ["Nord", "Nordost", "Ost", "Südost", "Süd", "Südwest", "West", "Nordwest"]
    direction_label = directions[int(((wind_bearing % 360) + 22.5) // 45) % len(directions)]

    if wind_angle <= 60:
        effect = "günstig"
    elif wind_angle <= 110:
        effect = "seitlich günstig"
    elif wind_angle >= 150:
        effect = "eher ungünstig"
    else:
        effect = "neutral"

    return f"aus {direction_label}, {effect} bei {wind_speed:.0f} km/h"


def get_recommended_cool_temp_threshold(profile: dict[str, float], solar_exposure: dict[str, str]) -> float:
    threshold = min(profile["temp_max"] + 4, 26)
    if solar_exposure["level"] == "direct":
        threshold -= 1.5
    elif solar_exposure["level"] == "indirect":
        threshold -= 0.5
    return max(20.0, threshold)


def _parse_forecast_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt_util.as_local(parsed)


def format_forecast_time(value: str | None, now: datetime | None = None) -> str | None:
    """Format a forecast timestamp as "HH:MM", prefixed with a day hint if not today."""
    parsed = _parse_forecast_time(value)
    if parsed is None:
        return None
    reference = dt_util.as_local(now or dt_util.now())
    return f"{day_prefix(parsed, reference)}{parsed.strftime('%H:%M')}"


def get_next_cooling_window(room: dict[str, Any], forecast: list[dict[str, Any]], solar_exposure: dict[str, str]) -> dict[str, Any]:
    profile = get_room_profile(room.get(CONF_ROOM_TYPE, "default"))
    threshold = get_recommended_cool_temp_threshold(profile, solar_exposure)
    now = dt_util.now()
    for entry in forecast:
        temp = as_float(entry.get("temperature"))
        entry_dt = _parse_forecast_time(entry.get("datetime"))
        if temp is not None and temp <= threshold and (entry_dt is None or entry_dt >= now):
            return {
                "threshold": threshold,
                "time_text": format_forecast_time(entry.get("datetime"), now),
                "entry": entry,
            }
    return {"threshold": threshold, "time_text": None, "entry": None}


def get_ventilation_duration(room_type: str, diff: float, wind_speed: float | None, wind_adjustment: int = 0) -> int:
    if room_type == "bathroom":
        duration = 5 if diff >= 4 else 7 if diff >= 2.5 else 10 if diff >= 1.5 else 12
    elif room_type == "kitchen":
        duration = 5 if diff >= 3 else 7 if diff >= 2 else 10 if diff >= 1 else 12
    elif room_type == "bedroom":
        duration = 6 if diff >= 3 else 8 if diff >= 2 else 10 if diff >= 1 else 12
    elif room_type == "basement":
        duration = 7 if diff >= 4 else 10 if diff >= 3 else 12 if diff >= 2 else 15
    else:
        duration = 5 if diff >= 4 else 7 if diff >= 3 else 10 if diff >= 2 else 12

    if wind_speed is not None:
        if wind_speed >= 20:
            duration -= 2
        elif wind_speed >= 12:
            duration -= 1
        elif wind_speed <= 5:
            duration += 1
    duration += wind_adjustment
    return max(3, duration)


def get_cooling_duration(cooling_delta: float, wind_speed: float | None, wind_adjustment: int = 0) -> int:
    duration = 15 if cooling_delta >= 8 else 12 if cooling_delta >= 6 else 10 if cooling_delta >= 4 else 8
    if wind_speed is not None:
        if wind_speed >= 20:
            duration -= 2
        elif wind_speed >= 12:
            duration -= 1
        elif wind_speed <= 5:
            duration += 1
    duration += wind_adjustment
    return max(5, duration)


def get_display_level(score: int) -> dict[str, str]:
    if score >= 90:
        return {"label": "Ideal", "cls": "excellent", "icon": "🌿"}
    if score >= 75:
        return {"label": "Angenehm", "cls": "good", "icon": "😊"}
    if score >= 60:
        return {"label": "Okay", "cls": "medium", "icon": "🙂"}
    if score >= 45:
        return {"label": "Belastend", "cls": "warning", "icon": "🟠"}
    if score >= 30:
        return {"label": "Schlecht", "cls": "bad", "icon": "🔴"}
    return {"label": "Kritisch", "cls": "critical", "icon": "🆘"}


def get_dew_text(value: str | None) -> str:
    mapping = {
        "dry": "trocken",
        "very_comfortable": "sehr angenehm",
        "comfortable": "angenehm",
        "ok_but_humid": "leicht schwül",
        "somewhat_uncomfortable": "schwül",
        "quite_uncomfortable": "deutlich schwül",
        "extremely_uncomfortable": "sehr schwül",
        "severely_high": "extrem schwül",
    }
    return mapping.get(value or "", "unauffällig")


def get_humidex_text(value: str | None) -> str:
    mapping = {
        "comfortable": "angenehm",
        "noticeable_discomfort": "spürbar",
        "evident_discomfort": "deutlich",
        "great_discomfort": "hoch",
        "dangerous_discomfort": "kritisch",
        "heat_stroke": "extrem",
    }
    return mapping.get(normalize_humidex_state(value), "neutral")


def get_simmer_text(value: str | None) -> str:
    mapping = {
        "cool": "kühl",
        "slightly_cool": "leicht kühl",
        "comfortable": "angenehm",
        "slightly_warm": "leicht warm",
        "increasing_discomfort": "zunehmend belastend",
        "extremely_warm": "sehr warm",
        "danger_of_heatstroke": "Hitzeschlaggefahr",
        "extreme_danger_of_heatstroke": "akute Hitzeschlaggefahr",
        "circulatory_collapse_imminent": "Kreislaufkollaps möglich",
    }
    return mapping.get(value or "", "unbekannt")


def get_climate_causes(
    metrics: dict[str, Any],
    profile: dict[str, float],
    mold: dict[str, Any] | None = None,
) -> list[str]:
    """Name what is actually wrong with the room climate, cold and dry included."""
    temp = metrics.get(CONF_TEMPERATURE)
    rel_humidity = metrics.get(CONF_HUMIDITY)
    humidex_value = metrics.get(CONF_HUMIDEX_VALUE)
    dewpoint = metrics.get(CONF_DEWPOINT)
    causes: list[str] = []

    if temp is not None:
        if temp >= 29:
            causes.append(f"sehr warm ({temp:.1f} °C)")
        elif temp >= 26:
            causes.append(f"warm ({temp:.1f} °C)")
        elif temp > profile["temp_max"] + 1:
            causes.append(f"etwas warm ({temp:.1f} °C)")
        elif temp < profile["temp_min"]:
            causes.append(f"zu kühl ({temp:.1f} °C)")
        elif temp < profile["temp_min"] + 1:
            causes.append(f"eher kühl ({temp:.1f} °C)")

    if rel_humidity is not None:
        if rel_humidity < get_dry_threshold(profile):
            causes.append(f"Luft zu trocken ({rel_humidity:.0f} %)")
        elif rel_humidity > profile["humidity_max"] + 5:
            causes.append(f"Luft zu feucht ({rel_humidity:.0f} %)")
        elif rel_humidity > profile["humidity_max"]:
            causes.append(f"Luftfeuchte leicht erhöht ({rel_humidity:.0f} %)")

    if dewpoint in {"somewhat_uncomfortable", "quite_uncomfortable", "extremely_uncomfortable", "severely_high"}:
        causes.append(f"Luft wirkt {get_dew_text(dewpoint)}")
    if humidex_value is not None and humidex_value >= 30:
        causes.append(f"Humidex {humidex_value:.0f}")
    if mold and mold.get("level") == "high":
        causes.append("Schimmelrisiko an kalten Wänden")
    elif mold and mold.get("level") == "elevated":
        causes.append("erhöhte Feuchte an kalten Wänden")
    return causes


def get_description(
    room: dict[str, Any],
    score: int,
    metrics: dict[str, Any],
    profile: dict[str, float],
    mold: dict[str, Any] | None = None,
) -> str:
    name = room[CONF_ROOM_NAME]
    if score >= 90:
        phrase = "nahezu ideales Raumklima"
    elif score >= 75:
        phrase = "angenehmes Raumklima"
    elif score >= 60:
        phrase = "noch akzeptables Raumklima"
    elif score >= 45:
        phrase = "spürbar belastendes Raumklima"
    elif score >= 30:
        phrase = "schlechtes Raumklima"
    else:
        phrase = "kritisches Raumklima"

    causes = get_climate_causes(metrics, profile, mold)
    if not causes or score >= 90:
        return f"{name}: {phrase}."
    return f"{name}: {phrase}. Auffällig: {', '.join(causes)}."


def _comfort_band_score(value: float, low: float, high: float, penalty_per_unit: float) -> float:
    if value < low:
        return max(0.0, 100 - (low - value) * penalty_per_unit)
    if value > high:
        return max(0.0, 100 - (value - high) * penalty_per_unit)
    return 100.0


def calculate_score(
    room: dict[str, Any],
    metrics: dict[str, Any],
    mold_risk: str | None = None,
) -> int:
    """Score the room climate from 0 to 100.

    Thermal Comfort categories (Scharlau, Humidex, dew point, Simmer) are used
    when they are configured. Missing categories no longer count as a flat 70,
    which capped rooms without these sensors at "Okay"; instead the weights of
    the available categories are re-normalised. Without any category the base
    score is derived from temperature and humidity against the room profile.
    """
    profile = get_room_profile(room.get(CONF_ROOM_TYPE, "default"))
    scharlau = metrics.get(CONF_SCHARLAU)
    humidex = normalize_humidex_state(metrics.get(CONF_HUMIDEX))
    dewpoint = metrics.get(CONF_DEWPOINT)
    simmer = metrics.get(CONF_SIMMER)
    temp = metrics.get(CONF_TEMPERATURE)
    rel_humidity = metrics.get(CONF_HUMIDITY)
    humidex_value = metrics.get(CONF_HUMIDEX_VALUE)

    scharlau_score = {"comfortable": 100, "slightly_uncomfortable": 75, "moderately_uncomfortable": 55, "highly_uncomfortable": 30, "outside_calculable_range": 70}
    humidex_score = {"comfortable": 100, "noticeable_discomfort": 65, "evident_discomfort": 45, "great_discomfort": 25, "dangerous_discomfort": 10, "heat_stroke": 0}
    dewpoint_score = {"dry": 90, "very_comfortable": 100, "comfortable": 95, "ok_but_humid": 80, "somewhat_uncomfortable": 60, "quite_uncomfortable": 35, "extremely_uncomfortable": 15, "severely_high": 0}
    simmer_score = {"cool": 90, "slightly_cool": 95, "comfortable": 100, "slightly_warm": 78, "increasing_discomfort": 58, "extremely_warm": 35, "danger_of_heatstroke": 15, "extreme_danger_of_heatstroke": 5, "circulatory_collapse_imminent": 0}

    components = [
        (scharlau_score.get(scharlau), 0.25),
        (humidex_score.get(humidex), 0.25),
        (dewpoint_score.get(dewpoint), 0.2),
        (simmer_score.get(simmer), 0.3),
    ]
    available = [(value, weight) for value, weight in components if value is not None]
    uses_comfort_sensors = bool(available)

    if uses_comfort_sensors:
        total_weight = sum(weight for _, weight in available)
        score = round(sum(value * weight for value, weight in available) / total_weight)
    else:
        parts: list[tuple[float, float]] = []
        if temp is not None:
            parts.append((_comfort_band_score(temp, profile["temp_min"], profile["temp_max"], 10), 0.55))
        if rel_humidity is not None:
            parts.append((_comfort_band_score(rel_humidity, profile["humidity_min"], profile["humidity_max"], 5), 0.45))
        if not parts:
            return 0
        score = round(sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts))

    # Heat stress is penalised in both modes: the comfort categories describe
    # perceived humidity, but do not fully reflect absolute room temperature.
    if temp is not None:
        if temp >= 32:
            score -= 40
        elif temp >= 30:
            score -= 28
        elif temp >= 28:
            score -= 18
        elif temp >= 26:
            score -= 10
        elif uses_comfort_sensors:
            # The Thermal Comfort categories are summer indices and do not see
            # cold rooms or rooms a little above the profile (e.g. a 22 °C
            # bedroom); the fallback base score already contains both.
            if temp < profile["temp_min"] - 1:
                score -= 20
            elif temp < profile["temp_min"]:
                score -= 10
            elif temp > profile["temp_max"]:
                score -= round(min(20.0, (temp - profile["temp_max"]) * 5))

    if humidex_value is not None:
        if humidex_value >= 40:
            score -= 35
        elif humidex_value >= 35:
            score -= 25
        elif humidex_value >= 30:
            score -= 15
        elif humidex_value >= 27:
            score -= 8

    if rel_humidity is not None and uses_comfort_sensors:
        if rel_humidity < get_dry_threshold(profile):
            score -= 10
        elif rel_humidity > profile["humidity_max"] + 10:
            score -= 18
        elif rel_humidity > profile["humidity_max"] + 5:
            score -= 10

    if mold_risk == "high":
        score -= 15
    elif mold_risk == "elevated":
        score -= 5

    return max(0, min(100, score))


def saturation_vapour_pressure(temperature: float) -> float:
    """Saturation vapour pressure over water in hPa (Magnus formula)."""
    return 6.112 * math.exp((17.62 * temperature) / (243.12 + temperature))


def get_mold_risk(
    inside_temp: float | None,
    inside_rel: float | None,
    outside_temp: float | None,
    wall_factor: float,
) -> dict[str, Any]:
    """Estimate the relative humidity at the coldest wall surface.

    The surface temperature follows DIN 4108-2: outside + f_Rsi * (inside -
    outside). Mould can grow from about 80 % relative humidity at the surface,
    condensation starts at 100 %.
    """
    if inside_temp is None or inside_rel is None:
        return {"level": None, "surface_temperature": None, "surface_humidity": None}
    surface_temp = inside_temp
    if outside_temp is not None and outside_temp < inside_temp:
        surface_temp = outside_temp + wall_factor * (inside_temp - outside_temp)
    surface_humidity = min(
        100.0,
        inside_rel * saturation_vapour_pressure(inside_temp) / saturation_vapour_pressure(surface_temp),
    )
    if surface_humidity >= 80:
        level = "high"
    elif surface_humidity >= 70:
        level = "elevated"
    else:
        level = "low"
    return {
        "level": level,
        "surface_temperature": round(surface_temp, 1),
        "surface_humidity": round(surface_humidity, 1),
    }


HEATING_ACTIONS = {"heating", "preheating"}
HEATING_MODES = {"heat", "heat_cool", "auto"}


def get_heating_state(climate: dict[str, Any] | None) -> dict[str, Any]:
    """Interpret a climate entity: is heating enabled, and is it heating now?"""
    if not climate or climate.get("state") in (None, "unknown", "unavailable"):
        return {"available": False, "enabled": False, "active": False, "target": None}
    mode = climate.get("state")
    action = climate.get("hvac_action")
    target = as_float(climate.get("target_temperature"))
    current = as_float(climate.get("current_temperature"))
    enabled = mode in HEATING_MODES and (target is None or target > 12)
    if action is not None:
        active = action in HEATING_ACTIONS
    else:
        active = bool(enabled and target is not None and current is not None and target > current + 0.3)
    return {"available": True, "enabled": enabled, "active": active, "target": target, "mode": mode, "action": action}


@dataclass(slots=True)
class RoomResult:
    room_id: str
    name: str
    score: int | None
    level_label: str
    level_icon: str
    level_class: str
    description: str
    recommendation: str
    next_window: str | None
    ventilate_now: bool
    close_window: bool
    close_window_reason: str | None
    close_cover: bool
    close_cover_reason: str | None
    window_open: bool
    orientation_label: str | None
    solar_label: str
    attributes: dict[str, Any]
    notifications_enabled: bool
    mold_risk: bool = False
    mold_risk_reason: str | None = None
    reduce_heating: bool = False
    reduce_heating_reason: str | None = None

    def apply_session_finish(self, reason: str) -> None:
        """Force a close-window advice, e.g. after the maximum ventilation time."""
        if not self.window_open:
            return
        self.close_window = True
        self.close_window_reason = reason
        self.attributes["close_window"] = True
        self.attributes["close_window_reason"] = reason
        self.attributes["notification_flags"][NOTIFICATION_CLOSE_WINDOW] = True


def evaluate_room(
    room: dict[str, Any],
    metrics: dict[str, Any],
    outside_abs: float | None,
    outside_weather: dict[str, Any],
    sun: dict[str, Any],
    forecast: list[dict[str, Any]],
    wall_quality: str = DEFAULT_WALL_QUALITY,
) -> RoomResult:
    room_type = room.get(CONF_ROOM_TYPE, "default")
    profile = get_room_profile(room_type)
    inside_temp = metrics.get(CONF_TEMPERATURE)
    inside_rel = metrics.get(CONF_HUMIDITY)
    inside_abs = metrics.get(CONF_INSIDE_ABSOLUTE_HUMIDITY)
    outside_temp = outside_weather.get("temperature")
    outside_wind = outside_weather.get("wind_speed")
    wind_bearing = outside_weather.get("wind_bearing")
    window_state = metrics.get(CONF_WINDOW)
    cover_state = metrics.get(CONF_COVER)
    has_window = bool(room.get(CONF_WINDOW))
    window_open = window_state in OPEN_WINDOW_STATES
    solar_exposure = get_solar_exposure(room, sun, outside_weather)
    wind_duration_adjustment = get_wind_duration_adjustment(room, outside_weather)
    wind_cooling_bonus = get_wind_cooling_bonus(room, outside_weather)
    wind_effect_text = get_wind_effect_text(room, outside_weather)
    orientation_label = ORIENTATION_LABEL.get(str(room.get(CONF_WINDOW_ORIENTATION, "")).upper())

    inputs_available = bool(
        metrics.get("inputs_available", inside_temp is not None and inside_rel is not None)
    )
    data_quality = str(
        metrics.get(
            "data_quality",
            "good" if inputs_available else "unavailable",
        )
    )

    mold = get_mold_risk(
        inside_temp if inputs_available else None,
        inside_rel if inputs_available else None,
        outside_temp,
        WALL_QUALITY_FACTORS.get(wall_quality, WALL_QUALITY_FACTORS[DEFAULT_WALL_QUALITY]),
    )
    mold_high = mold["level"] == "high"
    heating = get_heating_state(metrics.get("climate"))

    # Thresholds, relaxed while the window is already open (hysteresis).
    humidity_relax = OPEN_HUMIDITY_RELAX if window_open else 0.0
    diff_factor = OPEN_DIFF_FACTOR if window_open else 1.0
    humidity_high = inside_rel is not None and inside_rel >= profile["humidity_max"] - humidity_relax
    humidity_very_high = inside_rel is not None and inside_rel >= profile["humidity_max"] + 5 - humidity_relax
    humidity_too_dry = inside_rel is not None and inside_rel < get_dry_threshold(profile)
    vent_moderate = profile["vent_moderate"] * diff_factor
    vent_strong = profile["vent_strong"] * diff_factor

    diff = inside_abs - outside_abs if inside_abs is not None and outside_abs is not None else None
    cooling_delta = inside_temp - outside_temp if inside_temp is not None and outside_temp is not None else None
    solar_penalty = 1 if solar_exposure["level"] == "direct" else 0.5 if solar_exposure["level"] == "indirect" else 0
    required_cooling_delta = max(1.0, 2 + solar_penalty - wind_cooling_bonus)
    strong_cooling_delta = max(required_cooling_delta + 1.5, 4 + solar_penalty - wind_cooling_bonus)
    if window_open:
        required_cooling_delta = max(0.5, required_cooling_delta - OPEN_COOLING_DELTA_RELAX)

    cooling_start = get_cooling_start(room_type, profile)
    effective_cooling_start = (
        cooling_start - OPEN_TEMPERATURE_RELAX if cooling_start is not None and window_open else cooling_start
    )
    room_warm = cooling_start is not None and inside_temp is not None and inside_temp >= cooling_start
    # Heating season: cold outside, or the room's thermostat is actually heating.
    heating_season = bool(
        (outside_temp is not None and outside_temp < MIN_OUTSIDE_TEMP_FOR_COOLING)
        or heating["active"]
    )
    can_cool = bool(
        effective_cooling_start is not None
        and inside_temp is not None
        and cooling_delta is not None
        and not heating_season
        and inside_temp >= effective_cooling_start
        and cooling_delta >= required_cooling_delta
    )
    strong_cooling = bool(
        can_cool
        and cooling_start is not None
        and inside_temp is not None
        and cooling_delta is not None
        and inside_temp >= cooling_start + 2
        and cooling_delta >= strong_cooling_delta
    )
    too_humid_outside = diff is not None and diff <= -1.5

    # Benefit of the outside air for this room, independent of whether a
    # window contact is configured. Stale or missing room values never count.
    # A mould risk at cold walls also makes drying useful below the profile's
    # humidity maximum.
    dehumidify_climate = bool(
        inputs_available
        and diff is not None
        and (
            (humidity_very_high and diff >= vent_strong)
            or ((humidity_high or mold_high) and diff >= vent_moderate)
        )
    )
    cooling_climate = bool(inputs_available and can_cool and not (too_humid_outside and not strong_cooling))
    outside_air_beneficial = dehumidify_climate or cooling_climate
    ventilation_beneficial = has_window and outside_air_beneficial

    cooling_window = get_next_cooling_window(room, forecast, solar_exposure)

    dehumidify_text = None
    dehumidify_level = None
    if has_window:
        if not inputs_available:
            dehumidify_text = "Aktuelle Raumwerte fehlen oder sind veraltet"
            dehumidify_level = "unknown"
        elif window_open:
            dehumidify_text = (
                "Fenster ist offen, Entfeuchtung läuft"
                if dehumidify_climate
                else "Fenster ist bereits offen"
            )
            dehumidify_level = "open"
        elif humidity_too_dry:
            dehumidify_text = f"Luft zu trocken ({inside_rel:.0f} %)"
            dehumidify_level = "dry"
        elif inside_abs is None or outside_abs is None:
            dehumidify_text = (
                "Raum ist feucht, aber ohne Innen-/Außenvergleich keine sichere Entfeuchtungsaussage"
                if humidity_very_high
                else "Fenster geschlossen halten. Für Entfeuchtung fehlt aktuell ein belastbarer Außenvergleich."
            )
            dehumidify_level = "observe" if humidity_very_high else "neutral"
        else:
            duration = get_ventilation_duration(room_type, diff or 0, outside_wind, wind_duration_adjustment)
            if humidity_very_high and diff is not None and diff >= vent_strong:
                dehumidify_text = f"Entfeuchten sinnvoll, ca. {duration} Min."
                dehumidify_level = "recommended"
            elif humidity_high and diff is not None and diff >= vent_moderate:
                dehumidify_text = f"Leichtes Entfeuchten sinnvoll, ca. {duration} Min."
                dehumidify_level = "short"
            elif mold_high and diff is not None and diff >= vent_moderate:
                dehumidify_text = f"Entfeuchten wegen Schimmelrisiko an kalten Wänden sinnvoll, ca. {duration} Min."
                dehumidify_level = "short"
            elif humidity_high and diff is not None and diff <= 0.3:
                dehumidify_text = "Nicht sinnvoll – außen kaum trockener"
                dehumidify_level = "avoid"
            elif humidity_high:
                dehumidify_text = "Feuchte erhöht, aber aktuell kein klarer Entfeuchtungsvorteil"
                dehumidify_level = "observe"
            else:
                dehumidify_text = "Fenster geschlossen halten. Eine Entfeuchtung durch Lüften ist aktuell nicht nötig."
                dehumidify_level = "neutral"

    cooling_text = None
    cooling_level = None
    if has_window:
        if not inputs_available:
            cooling_text = "Aktuelle Raumwerte fehlen oder sind veraltet"
            cooling_level = "unknown"
        elif window_open:
            cooling_text = "Fenster ist offen, Abkühlung läuft" if cooling_climate else "Fenster ist bereits offen"
            cooling_level = "open"
        elif cooling_start is None:
            cooling_text = "Fenster geschlossen halten. Kühlen durch Lüften ist für diesen Raumtyp nicht sinnvoll (Kondensationsgefahr)."
            cooling_level = "neutral"
        elif inside_temp is None or outside_temp is None or cooling_delta is None:
            cooling_text = "Fenster geschlossen halten. Für Abkühlung fehlt aktuell ein belastbarer Außenvergleich."
            cooling_level = "neutral"
        elif not room_warm:
            cooling_text = "Raum ist nicht zu warm. Abkühlung ist nicht nötig."
            cooling_level = "neutral"
        elif heating_season:
            cooling_text = (
                "Fenster geschlossen halten. Die Heizung läuft gerade – Solltemperatur senken statt zu lüften."
                if heating["active"]
                else (
                    f"Fenster geschlossen halten. Bei {outside_temp:.0f} °C außen würde Lüften zum Kühlen "
                    "nur Heizenergie verschwenden – besser die Heizung reduzieren."
                )
            )
            cooling_level = "neutral"
        elif not can_cool:
            sun_text = (
                " Zusätzlicher Sonneneintrag spricht aktuell ebenfalls gegen Lüften."
                if solar_exposure["level"] == "direct"
                else " Leichter Sonneneintrag bremst die Abkühlung zusätzlich."
                if solar_exposure["level"] == "indirect"
                else ""
            )
            cooling_text = f"Fenster geschlossen halten. Außenluft bringt derzeit keine nennenswerte Abkühlung.{sun_text}"
            cooling_level = "neutral"
        elif not cooling_climate:
            cooling_text = "Fenster geschlossen halten. Abkühlung wäre möglich, außen ist es aber deutlich feuchter."
            cooling_level = "avoid"
        else:
            duration = get_cooling_duration(cooling_delta, outside_wind, wind_duration_adjustment)
            if strong_cooling:
                cooling_text = f"Fenster öffnen. Abkühlen ist jetzt sinnvoll, ca. {duration} Min."
            else:
                cooling_text = f"Fenster kurz öffnen. Leichtes Abkühlen ist sinnvoll, ca. {duration} Min."
            cooling_level = "cooling"

    # The next cooling window is only relevant for rooms that are too warm.
    next_window = None
    if has_window and room_warm and inputs_available and not heating_season and cooling_level not in {"cooling", "open"}:
        if cooling_window["time_text"]:
            next_window = (
                f"Nächstes Lüftungsfenster: frühestens ab {cooling_window['time_text']} Uhr, wenn die Außentemperatur unter "
                f"{cooling_window['threshold']:.1f} °C fällt."
            )
        else:
            next_window = f"Nächstes Lüftungsfenster: sobald die Außentemperatur unter {cooling_window['threshold']:.1f} °C fällt."

    score = calculate_score(room, metrics, mold["level"]) if inputs_available else None
    humidex_value = metrics.get(CONF_HUMIDEX_VALUE)
    level = (
        get_display_level(score)
        if score is not None
        else {"label": "Unbekannt", "cls": "unknown", "icon": "❔"}
    )
    description = (
        get_description(room, score, metrics, profile, mold)
        if score is not None
        else "Für einen Raumklima-Score fehlen aktuelle Temperatur- oder Feuchtewerte."
    )

    recommendation_parts = []
    if dehumidify_text:
        recommendation_parts.append(f"Entfeuchtung: {dehumidify_text}")
    if cooling_text:
        recommendation_parts.append(f"Abkühlung: {cooling_text}")
    recommendation = " | ".join(recommendation_parts) if recommendation_parts else "Keine Fenstersensor-Empfehlung verfügbar"

    ventilate_now = bool(ventilation_beneficial and not window_open)
    close_window = bool(window_open and inputs_available and not ventilation_beneficial)
    close_window_reason = (
        "Fenster wieder schließen. Die aktuelle Außenluft bringt gerade keinen klaren Vorteil mehr."
        if close_window
        else None
    )
    mold_reason = None
    if mold_high:
        mold_reason = (
            f"Schimmelrisiko: An kalten Außenwänden (ca. {mold['surface_temperature']:.1f} °C) erreicht die Luft "
            f"etwa {mold['surface_humidity']:.0f} % Feuchte."
        )
        if dehumidify_climate:
            mold_reason += " Stoßlüften senkt das Risiko."
        elif diff is not None and diff <= 0.3:
            mold_reason += " Die Außenluft ist zu feucht – Feuchtequellen reduzieren und den Raum nicht auskühlen lassen."
        else:
            mold_reason += " Regelmäßig stoßlüften und den Raum gleichmäßig beheizen."

    reduce_heating_reason = None
    if inputs_available and heating["enabled"] and window_open:
        reduce_heating_reason = "Fenster offen – Thermostat herunterregeln, solange gelüftet wird."
    elif (
        inputs_available
        and heating["active"]
        and inside_temp is not None
        and inside_temp > profile["temp_max"] + 1
    ):
        reduce_heating_reason = (
            f"Raum ist mit {inside_temp:.1f} °C wärmer als nötig (bis {profile['temp_max']:.0f} °C), "
            "die Heizung heizt aber noch – Solltemperatur senken."
        )
    reduce_heating = reduce_heating_reason is not None

    cover_temp_threshold = max(profile["temp_max"] + 4, 24.5)
    cover_available = bool(room.get(CONF_COVER))
    cover_can_be_closed = not cover_available or cover_state not in {"closed", "closing"}
    close_cover = bool(
        cover_can_be_closed
        and inputs_available
        and solar_exposure["level"] == "direct"
        and inside_temp is not None
        and inside_temp >= cover_temp_threshold
    )
    close_cover_reason = (
        f"Rollladen schließen. Direkter Sonneneintrag heizt den Raum bei {inside_temp:.1f} °C zusätzlich auf."
        if close_cover and inside_temp is not None
        else None
    )

    attributes = {
        "room_type": room_type,
        "temperature": inside_temp,
        "humidity": inside_rel,
        "inside_absolute_humidity": inside_abs,
        "inside_absolute_humidity_source": metrics.get("inside_absolute_humidity_source"),
        "outside_absolute_humidity": outside_abs,
        "outside_temperature": outside_temp,
        "outside_wind_speed": outside_wind,
        "outside_wind_bearing": wind_bearing,
        "humidex": humidex_value,
        "humidex_felt": metrics.get(CONF_HUMIDEX),
        "scharlau_felt": metrics.get(CONF_SCHARLAU),
        "simmer_felt": metrics.get(CONF_SIMMER),
        "dewpoint_felt": metrics.get(CONF_DEWPOINT),
        "inputs_available": inputs_available,
        "data_quality": data_quality,
        "input_age_minutes": metrics.get("input_age_minutes"),
        "dehumidify_advice": dehumidify_text,
        "dehumidify_level": dehumidify_level,
        "cooling_advice": cooling_text,
        "cooling_level": cooling_level,
        "cooling_start_temperature": cooling_start,
        "next_ventilation_window": next_window,
        "outside_air_beneficial": outside_air_beneficial,
        "ventilation_beneficial": ventilation_beneficial,
        "ventilate_now": ventilate_now,
        "close_window": close_window,
        "close_window_reason": close_window_reason,
        "close_cover": close_cover,
        "close_cover_reason": close_cover_reason,
        "window_open": window_open,
        "window_orientation": orientation_label,
        "solar_exposure": solar_exposure["label"],
        "wind_effect": wind_effect_text,
        "cover_state": cover_state,
        "cover_entity": room.get(CONF_COVER),
        "comfort_temperature_min": profile["temp_min"],
        "comfort_temperature_max": profile["temp_max"],
        "comfort_humidity_min": profile["humidity_min"],
        "comfort_humidity_max": profile["humidity_max"],
        "mold_risk": mold["level"],
        "mold_risk_reason": mold_reason,
        "wall_surface_temperature": mold["surface_temperature"],
        "wall_surface_humidity": mold["surface_humidity"],
        "climate_entity": metrics.get("climate_entity"),
        "heating_enabled": heating["enabled"],
        "heating_active": heating["active"],
        "heating_target_temperature": heating["target"],
        "reduce_heating": reduce_heating,
        "reduce_heating_reason": reduce_heating_reason,
        "notification_flags": {
            NOTIFICATION_VENTILATE: ventilate_now,
            NOTIFICATION_CLOSE_WINDOW: close_window,
            NOTIFICATION_CLOSE_COVER: close_cover,
            NOTIFICATION_MOLD_RISK: mold_high,
        },
    }

    return RoomResult(
        room_id=room[CONF_ROOM_ID],
        name=room[CONF_ROOM_NAME],
        score=score,
        level_label=level["label"],
        level_icon=level["icon"],
        level_class=level["cls"],
        description=description,
        recommendation=recommendation,
        next_window=next_window,
        ventilate_now=ventilate_now,
        close_window=close_window,
        close_window_reason=close_window_reason,
        close_cover=close_cover,
        close_cover_reason=close_cover_reason,
        window_open=window_open,
        orientation_label=orientation_label,
        solar_label=solar_exposure["label"],
        attributes=attributes,
        notifications_enabled=bool(room.get(CONF_ROOM_NOTIFICATIONS, True)),
        mold_risk=mold_high,
        mold_risk_reason=mold_reason,
        reduce_heating=reduce_heating,
        reduce_heating_reason=reduce_heating_reason,
    )
