"""Thermal comfort indices calculated from temperature and relative humidity.

The Room Climate score used to depend on the Thermal Comfort integration. All
of its categories are plain functions of temperature and relative humidity, so
they are calculated here. The state keys are the ones the Thermal Comfort
integration uses (including its "noticable_discomfort" spelling is normalised
in logic.py), which keeps existing sensors configured as override compatible.

Formulas and sources:
- Dew point: Magnus-type approximation, http://wahiduddin.net/calc/density_algorithms.htm
- Humidex: https://simple.wikipedia.org/wiki/Humidex#Humidex_formula
- Summer Simmer Index: https://www.vcalc.com/wiki/rklarsen/Summer+Simmer+Index
- Scharlau index: https://revistadechimie.ro/pdf/16%20RUSANESCU%204%2019.pdf
"""

from __future__ import annotations

import math
from typing import Any

# Keys of the metrics dict that hold a calculated comfort category.
COMFORT_KEYS = ("scharlau", "humidex", "simmer", "dewpoint")


def dew_point(temperature: float, humidity: float) -> float | None:
    """Dew point in °C."""
    if humidity <= 0:
        return None
    a0 = 373.15 / (273.15 + temperature)
    total = -7.90298 * (a0 - 1)
    total += 5.02808 * math.log(a0, 10)
    total += -1.3816e-7 * (math.pow(10, 11.344 * (1 - 1 / a0)) - 1)
    total += 8.1328e-3 * (math.pow(10, -3.49149 * (a0 - 1)) - 1)
    total += math.log(1013.246, 10)
    vapour_pressure = math.pow(10, total - 3) * humidity
    ratio = math.log(vapour_pressure / 0.61078)
    return (241.88 * ratio) / (17.558 - ratio)


def humidex(temperature: float, humidity: float) -> float | None:
    """Humidex (felt temperature in °C)."""
    dewpoint = dew_point(temperature, humidity)
    if dewpoint is None:
        return None
    vapour = 6.11 * math.exp(5417.7530 * ((1 / 273.16) - (1 / (dewpoint + 273.15))))
    return temperature + 0.5555 * (vapour - 10.0)


def summer_simmer_index(temperature: float, humidity: float) -> float:
    """Summer Simmer Index in °C (only defined above 58 °F, else the temperature)."""
    fahrenheit = temperature * 9 / 5 + 32
    if fahrenheit < 58:
        return temperature
    index = 1.98 * (fahrenheit - (0.55 - 0.0055 * humidity) * (fahrenheit - 58.0)) - 56.83
    return (index - 32) * 5 / 9


def dewpoint_perception(dewpoint: float) -> str:
    if dewpoint < 10:
        return "dry"
    if dewpoint < 13:
        return "very_comfortable"
    if dewpoint < 16:
        return "comfortable"
    if dewpoint < 18:
        return "ok_but_humid"
    if dewpoint < 21:
        return "somewhat_uncomfortable"
    if dewpoint < 24:
        return "quite_uncomfortable"
    if dewpoint < 26:
        return "extremely_uncomfortable"
    return "severely_high"


def humidex_perception(value: float) -> str:
    if value > 54:
        return "heat_stroke"
    if value >= 45:
        return "dangerous_discomfort"
    if value >= 40:
        return "great_discomfort"
    if value >= 35:
        return "evident_discomfort"
    if value >= 30:
        return "noticeable_discomfort"
    return "comfortable"


def simmer_perception(index: float) -> str:
    if index < 21.1:
        return "cool"
    if index < 25.0:
        return "slightly_cool"
    if index < 28.3:
        return "comfortable"
    if index < 32.8:
        return "slightly_warm"
    if index < 37.8:
        return "increasing_discomfort"
    if index < 44.4:
        return "extremely_warm"
    if index < 51.7:
        return "danger_of_heatstroke"
    if index < 65.6:
        return "extreme_danger_of_heatstroke"
    return "circulatory_collapse_imminent"


def summer_scharlau_perception(temperature: float, humidity: float) -> str:
    """Summer Scharlau category; only valid for 17-39 °C and at least 30 % humidity."""
    if temperature < 17 or temperature > 39 or humidity < 30:
        return "outside_calculable_range"
    index = (-17.089 * math.log(humidity) + 94.979) - temperature
    if index <= -3:
        return "highly_uncomfortable"
    if index <= -1:
        return "moderately_uncomfortable"
    if index < 0:
        return "slightly_uncomfortable"
    return "comfortable"


def calculate_comfort(temperature: float | None, humidity: float | None) -> dict[str, Any]:
    """Return all comfort categories and the humidex value, or {} without input."""
    if temperature is None or humidity is None or humidity <= 0:
        return {}
    humidity = min(humidity, 100.0)
    dewpoint = dew_point(temperature, humidity)
    humidex_value = humidex(temperature, humidity)
    if dewpoint is None or humidex_value is None:
        return {}
    return {
        "dewpoint": dewpoint_perception(dewpoint),
        "humidex": humidex_perception(humidex_value),
        "humidex_value": round(humidex_value, 1),
        "simmer": simmer_perception(summer_simmer_index(temperature, humidity)),
        "scharlau": summer_scharlau_perception(temperature, humidity),
    }


def fill_comfort_metrics(metrics: dict[str, Any]) -> None:
    """Fill comfort values a configured sensor did not deliver, in place.

    A configured sensor wins; unknown/unavailable values are replaced by the
    calculation. ``comfort_source`` becomes "sensor", "calculated" or "mixed".
    """
    calculated = calculate_comfort(metrics.get("temperature"), metrics.get("humidity"))
    keys = (*COMFORT_KEYS, "humidex_value")
    filled = [
        key
        for key in keys
        if key in calculated and metrics.get(key) in (None, "unknown", "unavailable", "")
    ]
    for key in filled:
        metrics[key] = calculated[key]
    if filled and len(filled) == len(keys):
        metrics["comfort_source"] = "calculated"
    else:
        metrics["comfort_source"] = "mixed" if filled else "sensor"
