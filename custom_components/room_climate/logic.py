from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
    NOTIFICATION_CLOSE_COVER,
    NOTIFICATION_CLOSE_WINDOW,
    NOTIFICATION_VENTILATE,
)


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


ORIENTATION_AZIMUTH = {"N": 0, "NO": 45, "O": 90, "SO": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
ORIENTATION_LABEL = {
    "N": "Nord",
    "NO": "Nordost",
    "O": "Ost",
    "SO": "Suedost",
    "S": "Sued",
    "SW": "Suedwest",
    "W": "West",
    "NW": "Nordwest",
}


def angular_difference(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return 360 - diff if diff > 180 else diff


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


def get_recommended_cool_temp_threshold(profile: dict[str, float], solar_exposure: dict[str, str]) -> float:
    threshold = min(profile["temp_max"] + 4, 26)
    if solar_exposure["level"] == "direct":
        threshold -= 1.5
    elif solar_exposure["level"] == "indirect":
        threshold -= 0.5
    return max(20.0, threshold)


def format_forecast_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().strftime("%H:%M")


def get_next_cooling_window(room: dict[str, Any], forecast: list[dict[str, Any]], solar_exposure: dict[str, str]) -> dict[str, Any]:
    profile = get_room_profile(room.get(CONF_ROOM_TYPE, "default"))
    threshold = get_recommended_cool_temp_threshold(profile, solar_exposure)
    for entry in forecast:
        temp = as_float(entry.get("temperature"))
        if temp is not None and temp <= threshold:
            return {"threshold": threshold, "time_text": format_forecast_time(entry.get("datetime")), "entry": entry}
    return {"threshold": threshold, "time_text": None, "entry": None}


def get_ventilation_duration(room_type: str, diff: float, wind_speed: float | None) -> int:
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
    return max(3, duration)


def get_cooling_duration(cooling_delta: float, wind_speed: float | None) -> int:
    duration = 15 if cooling_delta >= 8 else 12 if cooling_delta >= 6 else 10 if cooling_delta >= 4 else 8
    if wind_speed is not None:
        if wind_speed >= 20:
            duration -= 2
        elif wind_speed >= 12:
            duration -= 1
        elif wind_speed <= 5:
            duration += 1
    return max(5, duration)


def get_display_level(score: int, temp: float | None, humidex_value: float | None, simmer: str | None) -> dict[str, str]:
    if (temp is not None and temp >= 30) or (humidex_value is not None and humidex_value >= 38) or simmer in {"danger_of_heatstroke", "extreme_danger_of_heatstroke", "circulatory_collapse_imminent"}:
        return {"label": "Hitzekritisch", "cls": "critical", "icon": "🆘"}
    if (temp is not None and temp >= 28) or (humidex_value is not None and humidex_value >= 34) or simmer in {"extremely_warm", "increasing_discomfort"}:
        return {"label": "Stark waermebelastet", "cls": "bad", "icon": "🔴"}
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


def get_description(room: dict[str, Any], score: int, temp: float | None, dew_text: str, humidex_text: str, humidex_value: float | None) -> str:
    name = room[CONF_ROOM_NAME]
    temp_text = "warm" if temp is None else "sehr warm" if temp >= 29 else "warm" if temp >= 27 else "etwas warm" if temp >= 24 else "angenehm"
    if score >= 90:
        return f"Im {name} herrscht derzeit ein nahezu ideales Raumklima."
    if score >= 75:
        return f"Im {name} ist es aktuell {temp_text}. Die Luft wirkt {dew_text}, insgesamt ist das Raumklima noch recht angenehm."
    if score >= 60:
        return f"Im {name} ist es aktuell {temp_text}. Zusammen mit der {dew_text}en Luft wirkt das Raumklima bereits leicht belastend."
    if score >= 45:
        return f"Im {name} ist das Raumklima spuerbar belastend. Es ist {temp_text}, die Luft wirkt {dew_text} und der Humidex ist {humidex_text}."
    if score >= 30:
        return f"Im {name} ist es aktuell {temp_text}. Das Raumklima wirkt bereits leicht waermebelastet, auch wenn die Luft {dew_text} erscheint."
    if temp is not None and humidex_value is not None:
        return f"Im {name} ist es mit {temp:.1f} °C sehr warm. Das Raumklima ist trotz {dew_text}er Luft spuerbar belastend."
    return f"Im {name} herrscht aktuell ein kritisches Raumklima."


def get_dew_text(value: str | None) -> str:
    mapping = {
        "dry": "trocken",
        "very_comfortable": "sehr angenehm",
        "comfortable": "angenehm",
        "ok_but_humid": "leicht schwuel",
        "somewhat_uncomfortable": "schwuel",
        "quite_uncomfortable": "deutlich schwuel",
        "extremely_uncomfortable": "sehr schwuel",
        "severely_high": "extrem schwuel",
    }
    return mapping.get(value or "", "unauffaellig")


def get_humidex_text(value: str | None) -> str:
    mapping = {
        "comfortable": "angenehm",
        "noticeable_discomfort": "spuerbar",
        "evident_discomfort": "deutlich",
        "great_discomfort": "hoch",
        "dangerous_discomfort": "kritisch",
        "heat_stroke": "extrem",
    }
    return mapping.get(normalize_humidex_state(value), "neutral")


def get_simmer_text(value: str | None) -> str:
    mapping = {
        "cool": "kuehl",
        "slightly_cool": "leicht kuehl",
        "comfortable": "angenehm",
        "slightly_warm": "leicht warm",
        "increasing_discomfort": "zunehmend belastend",
        "extremely_warm": "sehr warm",
        "danger_of_heatstroke": "Hitzeschlaggefahr",
        "extreme_danger_of_heatstroke": "akute Hitzeschlaggefahr",
        "circulatory_collapse_imminent": "Kreislaufkollaps moeglich",
    }
    return mapping.get(value or "", "unbekannt")


def calculate_score(room: dict[str, Any], metrics: dict[str, Any]) -> int:
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

    score = round(
        (scharlau_score.get(scharlau, 70) * 0.25)
        + (humidex_score.get(humidex, 70) * 0.25)
        + (dewpoint_score.get(dewpoint, 70) * 0.2)
        + (simmer_score.get(simmer, 70) * 0.3)
    )

    if temp is not None:
        if temp >= 32:
            score -= 40
        elif temp >= 30:
            score -= 28
        elif temp >= 28:
            score -= 18
        elif temp >= 26:
            score -= 10
        elif temp <= 15:
            score -= 20
        elif temp <= 17:
            score -= 10

    if humidex_value is not None:
        if humidex_value >= 40:
            score -= 35
        elif humidex_value >= 35:
            score -= 25
        elif humidex_value >= 30:
            score -= 15
        elif humidex_value >= 27:
            score -= 8

    if rel_humidity is not None:
        if rel_humidity < max(profile["humidity_min"] - 5, 35):
            score -= 10
        elif rel_humidity > profile["humidity_max"] + 10:
            score -= 18
        elif rel_humidity > profile["humidity_max"] + 5:
            score -= 10

    return max(0, min(100, score))


@dataclass(slots=True)
class RoomResult:
    room_id: str
    name: str
    score: int
    level_label: str
    level_icon: str
    description: str
    recommendation: str
    next_window: str | None
    ventilate_now: bool
    close_window: bool
    close_cover: bool
    window_open: bool
    orientation_label: str | None
    solar_label: str
    attributes: dict[str, Any]
    notifications_enabled: bool


def evaluate_room(
    room: dict[str, Any],
    metrics: dict[str, Any],
    outside_abs: float | None,
    outside_weather: dict[str, Any],
    sun: dict[str, Any],
    forecast: list[dict[str, Any]],
) -> RoomResult:
    profile = get_room_profile(room.get(CONF_ROOM_TYPE, "default"))
    inside_temp = metrics.get(CONF_TEMPERATURE)
    inside_rel = metrics.get(CONF_HUMIDITY)
    inside_abs = metrics.get(CONF_INSIDE_ABSOLUTE_HUMIDITY)
    outside_temp = outside_weather.get("temperature")
    outside_wind = outside_weather.get("wind_speed")
    window_state = metrics.get(CONF_WINDOW)
    cover_state = metrics.get(CONF_COVER)
    window_open = window_state in {"on", "open", "tilted"}
    solar_exposure = get_solar_exposure(room, sun, outside_weather)
    orientation_label = ORIENTATION_LABEL.get(str(room.get(CONF_WINDOW_ORIENTATION, "")).upper())

    humidity_high = inside_rel is not None and inside_rel >= profile["humidity_max"]
    humidity_very_high = inside_rel is not None and inside_rel >= profile["humidity_max"] + 5
    humidity_too_dry = inside_rel is not None and inside_rel < max(profile["humidity_min"] - 5, 35)
    diff = inside_abs - outside_abs if inside_abs is not None and outside_abs is not None else None
    cooling_delta = inside_temp - outside_temp if inside_temp is not None and outside_temp is not None else None
    required_cooling_delta = 2 + (1 if solar_exposure["level"] == "direct" else 0.5 if solar_exposure["level"] == "indirect" else 0)
    strong_cooling_delta = 4 + (1 if solar_exposure["level"] == "direct" else 0.5 if solar_exposure["level"] == "indirect" else 0)
    can_cool = cooling_delta is not None and inside_temp is not None and inside_temp >= 27 and cooling_delta >= required_cooling_delta
    strong_cooling = cooling_delta is not None and inside_temp is not None and inside_temp >= 29 and cooling_delta >= strong_cooling_delta
    cooling_window = get_next_cooling_window(room, forecast, solar_exposure)
    dehumidify_beneficial = bool(
        room.get(CONF_WINDOW)
        and diff is not None
        and (
            (humidity_very_high and diff >= profile["vent_strong"])
            or (humidity_high and diff >= profile["vent_moderate"])
        )
    )
    cooling_beneficial = bool(
        room.get(CONF_WINDOW)
        and can_cool
        and not (diff is not None and diff <= -1.5 and not strong_cooling)
    )

    dehumidify_text = None
    dehumidify_level = None
    if room.get(CONF_WINDOW):
        if window_open:
            dehumidify_text = "Fenster ist bereits offen"
            dehumidify_level = "open"
        elif inside_rel is None:
            dehumidify_text = "Luftfeuchtigkeit nicht verfuegbar"
            dehumidify_level = "unknown"
        elif humidity_too_dry:
            dehumidify_text = f"Luft zu trocken ({inside_rel:.0f} %)"
            dehumidify_level = "dry"
        elif inside_abs is None or outside_abs is None:
            dehumidify_text = (
                "Raum ist feucht, aber ohne Innen-/Aussenvergleich keine sichere Entfeuchtungsaussage"
                if humidity_very_high
                else "Fenster geschlossen halten. Fuer Entfeuchtung fehlt aktuell ein belastbarer Aussenvergleich."
            )
            dehumidify_level = "observe" if humidity_very_high else "neutral"
        else:
            duration = get_ventilation_duration(room.get(CONF_ROOM_TYPE, "default"), diff or 0, outside_wind)
            if humidity_very_high and diff is not None and diff >= profile["vent_strong"]:
                dehumidify_text = f"Entfeuchten sinnvoll, ca. {duration} Min."
                dehumidify_level = "recommended"
            elif humidity_high and diff is not None and diff >= profile["vent_moderate"]:
                dehumidify_text = f"Leichtes Entfeuchten sinnvoll, ca. {duration} Min."
                dehumidify_level = "short"
            elif humidity_high and diff is not None and diff <= 0.3:
                dehumidify_text = "Nicht sinnvoll - aussen kaum trockener"
                dehumidify_level = "avoid"
            elif humidity_high:
                dehumidify_text = "Feuchte erhoeht, aber aktuell kein klarer Entfeuchtungsvorteil"
                dehumidify_level = "observe"
            else:
                dehumidify_text = "Fenster geschlossen halten. Eine Entfeuchtung durch Lueften ist aktuell nicht noetig."
                dehumidify_level = "neutral"

    cooling_text = None
    cooling_level = None
    if room.get(CONF_WINDOW):
        if window_open:
            cooling_text = "Fenster ist bereits offen"
            cooling_level = "open"
        elif inside_temp is None or outside_temp is None or cooling_delta is None:
            cooling_text = "Fenster geschlossen halten. Fuer Abkuehlung fehlt aktuell ein belastbarer Aussenvergleich."
            cooling_level = "neutral"
        elif not can_cool:
            sun_text = (
                " Zusaetzlicher Sonneneintrag spricht aktuell ebenfalls gegen Lueften."
                if solar_exposure["level"] == "direct"
                else " Leichter Sonneneintrag bremst die Abkuehlung zusaetzlich."
                if solar_exposure["level"] == "indirect"
                else ""
            )
            cooling_text = f"Fenster geschlossen halten. Aussenluft bringt derzeit keine nennenswerte Abkuehlung.{sun_text}"
            cooling_level = "neutral"
        else:
            duration = get_cooling_duration(cooling_delta, outside_wind)
            if strong_cooling and (diff is None or diff > -1.0):
                cooling_text = f"Fenster oeffnen. Abkuehlen ist jetzt sinnvoll, ca. {duration} Min."
                cooling_level = "cooling"
            elif diff is not None and diff <= -1.5:
                cooling_text = f"Fenster nur kurz oeffnen. Abkuehlung ist moeglich, aber aussen ist es deutlich feuchter ({duration} Min.)."
                cooling_level = "observe"
            else:
                cooling_text = f"Fenster kurz oeffnen. Leichtes Abkuehlen ist sinnvoll, ca. {duration} Min."
                cooling_level = "cooling"

    next_window = None
    if room.get(CONF_WINDOW) and cooling_level not in {"cooling", "open"}:
        if cooling_window["time_text"]:
            next_window = (
                f"ab {cooling_window['time_text']} Uhr, wenn die Aussentemperatur unter "
                f"{cooling_window['threshold']:.1f} °C faellt."
            )
        else:
            next_window = f"sobald die Aussentemperatur unter {cooling_window['threshold']:.1f} °C faellt."

    score = calculate_score(room, metrics)
    humidex_value = metrics.get(CONF_HUMIDEX_VALUE)
    level = get_display_level(score, inside_temp, humidex_value, metrics.get(CONF_SIMMER))
    dew_text = get_dew_text(metrics.get(CONF_DEWPOINT))
    humidex_text = get_humidex_text(metrics.get(CONF_HUMIDEX))
    description = get_description(room, score, inside_temp, dew_text, humidex_text, humidex_value)

    recommendation_parts = []
    if dehumidify_text:
        recommendation_parts.append(f"Entfeuchtung: {dehumidify_text}")
    if cooling_text:
        recommendation_parts.append(f"Abkuehlung: {cooling_text}")
    recommendation = " | ".join(recommendation_parts) if recommendation_parts else "Keine Fenstersensor-Empfehlung verfuegbar"

    ventilate_now = bool(dehumidify_beneficial or cooling_beneficial)
    close_window = bool(window_open and not ventilate_now)
    close_cover = bool(
        room.get(CONF_COVER)
        and cover_state not in {"closed", "closing"}
        and solar_exposure["level"] == "direct"
        and inside_temp is not None
        and inside_temp >= 25
        and outside_temp is not None
        and outside_temp >= 24
    )

    attributes = {
        "room_type": room.get(CONF_ROOM_TYPE, "default"),
        "temperature": inside_temp,
        "humidity": inside_rel,
        "inside_absolute_humidity": inside_abs,
        "outside_absolute_humidity": outside_abs,
        "outside_temperature": outside_temp,
        "outside_wind_speed": outside_wind,
        "humidex": humidex_value,
        "humidex_felt": metrics.get(CONF_HUMIDEX),
        "scharlau_felt": metrics.get(CONF_SCHARLAU),
        "simmer_felt": metrics.get(CONF_SIMMER),
        "dewpoint_felt": metrics.get(CONF_DEWPOINT),
        "dehumidify_advice": dehumidify_text,
        "cooling_advice": cooling_text,
        "next_ventilation_window": next_window,
        "window_open": window_open,
        "window_orientation": orientation_label,
        "solar_exposure": solar_exposure["label"],
        "cover_state": cover_state,
        "cover_entity": room.get(CONF_COVER),
        "notification_flags": {
            NOTIFICATION_VENTILATE: ventilate_now,
            NOTIFICATION_CLOSE_WINDOW: close_window,
            NOTIFICATION_CLOSE_COVER: close_cover,
        },
    }

    return RoomResult(
        room_id=room[CONF_ROOM_ID],
        name=room[CONF_ROOM_NAME],
        score=score,
        level_label=level["label"],
        level_icon=level["icon"],
        description=description,
        recommendation=recommendation,
        next_window=next_window,
        ventilate_now=ventilate_now,
        close_window=close_window,
        close_cover=close_cover,
        window_open=window_open,
        orientation_label=orientation_label,
        solar_label=solar_exposure["label"],
        attributes=attributes,
        notifications_enabled=bool(room.get(CONF_ROOM_NOTIFICATIONS, True)),
    )
