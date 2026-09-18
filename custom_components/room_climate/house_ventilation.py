from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable


ORIENTATION_AZIMUTH = {
    "N": 0,
    "NO": 45,
    "O": 90,
    "SO": 135,
    "S": 180,
    "SW": 225,
    "W": 270,
    "NW": 315,
}
ORIENTATION_LABEL = {
    "N": "Nord",
    "NO": "Nordost",
    "O": "Ost",
    "SO": "Südost",
    "S": "Süd",
    "SW": "Südwest",
    "W": "West",
    "NW": "Nordwest",
}

MIN_DRYING_DELTA = 0.8
MIN_COOLING_DELTA = 1.5
MAX_FORECAST_HOURS = 36


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value in ("", "unknown", "unavailable"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def absolute_humidity(temperature: float | None, humidity: float | None) -> float | None:
    """Return absolute humidity in g/m³ for temperature in °C and RH in percent."""
    if temperature is None or humidity is None or not 0 <= humidity <= 100:
        return None
    saturation_pressure = 6.112 * math.exp((17.67 * temperature) / (temperature + 243.5))
    return 2.1674 * saturation_pressure * humidity / (273.15 + temperature)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _angular_difference(first: float, second: float) -> float:
    difference = abs(first - second) % 360
    return 360 - difference if difference > 180 else difference


def _configured_window_sides(rooms: list[dict[str, Any]]) -> dict[str, list[str]]:
    sides: dict[str, list[str]] = {}
    for room in rooms:
        orientation = str(room.get("window_orientation", "")).upper()
        if not room.get("window") or orientation not in ORIENTATION_AZIMUTH:
            continue
        sides.setdefault(orientation, []).append(str(room.get("name") or room.get("id") or "Raum"))
    return sides


def _best_cross_pair(sides: dict[str, list[str]]) -> tuple[str, str] | None:
    best_pair: tuple[str, str] | None = None
    best_difference = 0.0
    orientations = list(sides)
    for index, first in enumerate(orientations):
        for second in orientations[index + 1 :]:
            difference = _angular_difference(
                ORIENTATION_AZIMUTH[first], ORIENTATION_AZIMUTH[second]
            )
            if difference >= 135 and difference > best_difference:
                best_pair = first, second
                best_difference = difference
    return best_pair


def _duration_minutes(humidity_delta: float | None, wind_speed: float | None) -> int:
    if humidity_delta is not None and humidity_delta >= 4:
        duration = 5
    elif humidity_delta is not None and humidity_delta >= 2.5:
        duration = 7
    elif humidity_delta is not None and humidity_delta >= 1.5:
        duration = 9
    else:
        duration = 12

    if wind_speed is not None:
        if wind_speed >= 20:
            duration -= 2
        elif wind_speed >= 12:
            duration -= 1
        elif wind_speed <= 4:
            duration += 2
    return max(3, duration)


def _forecast_is_dry(entry: dict[str, Any]) -> bool:
    condition = str(entry.get("condition", "")).lower()
    if any(token in condition for token in ("rain", "pour", "lightning", "snow", "hail")):
        return False
    precipitation = _as_float(entry.get("precipitation"))
    precipitation_probability = _as_float(entry.get("precipitation_probability"))
    if precipitation is not None and precipitation > 0.2:
        return False
    return precipitation_probability is None or precipitation_probability < 60


@dataclass(slots=True)
class ForecastWindow:
    available: bool = False
    start: str | None = None
    end: str | None = None
    label: str = "Kein Lüftungsfenster"
    reason: str = "Keine geeigneten Forecast-Daten verfügbar."
    expected_temperature: float | None = None
    expected_absolute_humidity: float | None = None
    humidity_delta: float | None = None
    cooling_delta: float | None = None
    quality: str = "unknown"

    def attributes(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HouseVentilationResult:
    mode: str
    title: str
    icon: str
    color: str
    reason: str
    inside_absolute_humidity: float | None
    outside_absolute_humidity: float | None
    humidity_delta: float | None
    inside_temperature: float | None
    outside_temperature: float | None
    duration_minutes: int | None
    recommended_rooms: list[str]
    recommended_orientations: list[str]
    open_rooms: list[str]
    cross_ventilation_available: bool
    data_quality: str
    next_window: ForecastWindow

    def attributes(self) -> dict[str, Any]:
        attributes = asdict(self)
        attributes.pop("title", None)
        attributes.pop("icon", None)
        attributes["house_ventilation"] = True
        return attributes


def _forecast_window(
    forecast: list[dict[str, Any]],
    inside_absolute_humidity: float | None,
    inside_temperature: float | None,
    now: datetime,
) -> ForecastWindow:
    if inside_absolute_humidity is None and inside_temperature is None:
        return ForecastWindow(reason="Innenwerte für die Bewertung fehlen.")

    candidates: list[dict[str, Any]] = []
    forecast_limit = now + timedelta(hours=MAX_FORECAST_HOURS)
    for entry in forecast:
        entry_time = _parse_datetime(entry.get("datetime"))
        if entry_time is None or entry_time < now or entry_time > forecast_limit:
            continue

        temperature = _as_float(entry.get("temperature"))
        humidity = _as_float(entry.get("humidity"))
        forecast_absolute_humidity = absolute_humidity(temperature, humidity)
        humidity_delta = (
            inside_absolute_humidity - forecast_absolute_humidity
            if inside_absolute_humidity is not None and forecast_absolute_humidity is not None
            else None
        )
        cooling_delta = (
            inside_temperature - temperature
            if inside_temperature is not None and temperature is not None
            else None
        )
        drying_benefit = humidity_delta is not None and humidity_delta >= MIN_DRYING_DELTA
        cooling_benefit = (
            inside_temperature is not None
            and inside_temperature >= 24
            and cooling_delta is not None
            and cooling_delta >= MIN_COOLING_DELTA
            and (humidity_delta is None or humidity_delta >= -0.8)
        )
        if not _forecast_is_dry(entry) or not (drying_benefit or cooling_benefit):
            continue

        score = max(humidity_delta or 0, 0) * 2 + max(cooling_delta or 0, 0)
        candidates.append(
            {
                "datetime": entry_time,
                "temperature": temperature,
                "absolute_humidity": forecast_absolute_humidity,
                "humidity_delta": humidity_delta,
                "cooling_delta": cooling_delta,
                "score": score,
            }
        )

    if not candidates:
        reason = (
            "Forecast vorhanden, aber innerhalb der nächsten 36 Stunden ist kein klarer "
            "Feuchte- oder Temperaturvorteil erkennbar."
            if forecast
            else "Wetterdaten werden aktualisiert oder enthalten keinen Stunden-Forecast."
        )
        return ForecastWindow(reason=reason)

    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        if not groups or candidate["datetime"] - groups[-1][-1]["datetime"] > timedelta(minutes=90):
            groups.append([candidate])
        else:
            groups[-1].append(candidate)

    best_group = max(
        groups,
        key=lambda group: (
            sum(item["score"] for item in group) / len(group),
            -group[0]["datetime"].timestamp(),
        ),
    )
    best_entry = max(best_group, key=lambda item: item["score"])
    start = best_group[0]["datetime"]
    end = best_group[-1]["datetime"] + timedelta(hours=1)
    average_score = sum(item["score"] for item in best_group) / len(best_group)
    quality = "excellent" if average_score >= 8 else "good" if average_score >= 4 else "moderate"

    display_timezone = now.tzinfo or timezone.utc
    return ForecastWindow(
        available=True,
        start=start.isoformat(),
        end=end.isoformat(),
        label=(
            f"{start.astimezone(display_timezone).strftime('%H:%M')}–"
            f"{end.astimezone(display_timezone).strftime('%H:%M')} Uhr"
        ),
        reason="Außenluft bietet in diesem Zeitraum den besten Feuchte- und Temperaturvorteil.",
        expected_temperature=round(best_entry["temperature"], 1) if best_entry["temperature"] is not None else None,
        expected_absolute_humidity=(
            round(best_entry["absolute_humidity"], 2)
            if best_entry["absolute_humidity"] is not None
            else None
        ),
        humidity_delta=round(best_entry["humidity_delta"], 2) if best_entry["humidity_delta"] is not None else None,
        cooling_delta=round(best_entry["cooling_delta"], 1) if best_entry["cooling_delta"] is not None else None,
        quality=quality,
    )


def evaluate_house_ventilation(
    configured_rooms: list[dict[str, Any]],
    room_results: dict[str, Any],
    outside_absolute_humidity: float | None,
    outside_weather: dict[str, Any],
    forecast: list[dict[str, Any]],
    now: datetime | None = None,
) -> HouseVentilationResult:
    """Build one house-wide ventilation decision from the configured rooms."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    usable_rooms = [
        result
        for result in room_results.values()
        if result.attributes.get("inputs_available", True)
    ]
    inside_absolute_humidity = _average(
        _as_float(room.attributes.get("inside_absolute_humidity")) for room in usable_rooms
    )
    inside_temperature = _average(
        _as_float(room.attributes.get("temperature")) for room in usable_rooms
    )
    outside_temperature = _as_float(outside_weather.get("temperature"))
    humidity_delta = (
        inside_absolute_humidity - outside_absolute_humidity
        if inside_absolute_humidity is not None and outside_absolute_humidity is not None
        else None
    )
    cooling_delta = (
        inside_temperature - outside_temperature
        if inside_temperature is not None and outside_temperature is not None
        else None
    )

    sides = _configured_window_sides(configured_rooms)
    cross_pair = _best_cross_pair(sides)
    open_rooms = [room.name for room in usable_rooms if room.window_open]
    open_orientations = {
        str(room.get("window_orientation", "")).upper()
        for room in configured_rooms
        if room.get("window")
        and room_results.get(room.get("id")) is not None
        and room_results[room["id"]].window_open
    }
    open_cross_pair = _best_cross_pair(
        {orientation: sides[orientation] for orientation in open_orientations if orientation in sides}
    )

    recommended_rooms = [
        room.name
        for room in usable_rooms
        if room.attributes.get("ventilation_beneficial", room.ventilate_now)
    ]
    cooling = (
        inside_temperature is not None
        and inside_temperature >= 24
        and cooling_delta is not None
        and cooling_delta >= MIN_COOLING_DELTA
        and (humidity_delta is None or humidity_delta >= -0.8)
    )
    # The humidity difference alone is not a reason to ventilate. At least one
    # room must currently request ventilation, unless there is a clear cooling
    # benefit for the house.
    beneficial = bool(recommended_rooms or cooling)
    recommended_orientations = (
        [ORIENTATION_LABEL[item] for item in cross_pair] if cross_pair and beneficial else []
    )
    duration = (
        _duration_minutes(humidity_delta, _as_float(outside_weather.get("wind_speed")))
        if beneficial
        else None
    )

    if not usable_rooms:
        mode = "unknown"
        title = "Lüftung kann nicht bewertet werden"
        icon = "mdi:alert-circle-outline"
        color = "grey"
        reason = "Aktuelle Raumwerte fehlen oder sind veraltet."
        data_quality = "unavailable"
    elif open_rooms and not beneficial:
        mode = "close_windows"
        title = "Fenster schließen"
        icon = "mdi:window-closed-variant"
        color = "orange"
        reason = "Die Außenluft bringt aktuell keinen klaren Feuchte- oder Temperaturvorteil."
        data_quality = "good" if outside_absolute_humidity is not None else "limited"
    elif open_cross_pair and beneficial:
        labels = [ORIENTATION_LABEL[item] for item in open_cross_pair]
        mode = "cross_ventilation_active"
        title = "Querlüften aktiv (maximaler Luftaustausch)"
        icon = "mdi:window-open-variant"
        color = "green"
        reason = f"Fenster auf {labels[0]}- und {labels[1]}seite sind geöffnet."
        recommended_orientations = labels
        data_quality = "good" if outside_absolute_humidity is not None else "limited"
    elif open_rooms and beneficial:
        mode = "ventilation_active"
        title = "Lüften aktiv"
        icon = "mdi:window-open"
        color = "green"
        reason = "Die geöffneten Fenster nutzen den aktuellen Außenluftvorteil."
        data_quality = "good" if outside_absolute_humidity is not None else "limited"
    elif cross_pair and beneficial:
        labels = [ORIENTATION_LABEL[item] for item in cross_pair]
        mode = "cross_ventilation_recommended"
        title = f"Querlüften empfohlen ({labels[0]} & {labels[1]})"
        icon = "mdi:air-filter"
        color = "green"
        reason = "Gegenüberliegende Fenster ermöglichen einen besonders wirksamen Luftaustausch."
        data_quality = "good" if outside_absolute_humidity is not None else "limited"
    elif beneficial:
        mode = "shock_ventilation_recommended"
        title = "Stoßlüften empfohlen"
        icon = "mdi:window-open-variant"
        color = "amber"
        reason = "Die Außenluft ist aktuell für Entfeuchtung oder Abkühlung geeignet."
        data_quality = "good" if outside_absolute_humidity is not None else "limited"
    else:
        mode = "keep_windows_closed"
        title = "Aktuell nicht lüften"
        icon = "mdi:window-closed"
        color = "blue"
        if humidity_delta is not None and humidity_delta < MIN_DRYING_DELTA:
            reason = "Außen ist derzeit nicht ausreichend trockener und es besteht kein klarer Kühlvorteil."
        else:
            reason = "Aktuell besteht kein ausreichender Feuchte- oder Temperaturvorteil."
        data_quality = "good" if outside_absolute_humidity is not None else "limited"

    return HouseVentilationResult(
        mode=mode,
        title=title,
        icon=icon,
        color=color,
        reason=reason,
        inside_absolute_humidity=round(inside_absolute_humidity, 2) if inside_absolute_humidity is not None else None,
        outside_absolute_humidity=round(outside_absolute_humidity, 2) if outside_absolute_humidity is not None else None,
        humidity_delta=round(humidity_delta, 2) if humidity_delta is not None else None,
        inside_temperature=round(inside_temperature, 1) if inside_temperature is not None else None,
        outside_temperature=round(outside_temperature, 1) if outside_temperature is not None else None,
        duration_minutes=duration,
        recommended_rooms=recommended_rooms,
        recommended_orientations=recommended_orientations,
        open_rooms=open_rooms,
        cross_ventilation_available=cross_pair is not None,
        data_quality=data_quality,
        next_window=_forecast_window(
            forecast,
            inside_absolute_humidity,
            inside_temperature,
            now,
        ),
    )
