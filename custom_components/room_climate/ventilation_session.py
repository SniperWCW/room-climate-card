"""Track open-window ventilation sessions without claiming measured air exchange."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


OPEN_STATES = {"on", "open", "tilted"}
CLOSED_STATES = {"off", "closed"}
DEFAULT_PLANNED_MINUTES = 10
MAX_COLD_MINUTES = 10
MAX_MILD_MINUTES = 20


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _planned_minutes(duration: int | float | None, maximum: int) -> int:
    if not isinstance(duration, (int, float)) or not math.isfinite(duration):
        duration = DEFAULT_PLANNED_MINUTES
    return min(max(round(duration), 3), maximum)


def update_sessions(
    sessions: dict[str, dict[str, Any]],
    windows: dict[str, tuple[str, datetime]],
    rooms: dict[str, Any],
    now: datetime,
    duration: int | None,
    outside_temperature: float | None,
) -> list[dict[str, Any]]:
    """Update persisted sessions and return dashboard-ready session metadata."""
    for room_id in list(sessions):
        state = windows.get(room_id, ("closed", now))[0]
        if room_id not in windows or state in CLOSED_STATES:
            sessions.pop(room_id)

    results: list[dict[str, Any]] = []
    for room_id, (state, changed) in windows.items():
        if state not in OPEN_STATES:
            continue

        maximum = (
            MAX_COLD_MINUTES
            if outside_temperature is not None and outside_temperature < 5
            else MAX_MILD_MINUTES
        )
        session = sessions.get(room_id)
        started_at = (
            _parse_datetime(session.get("started_at"))
            if isinstance(session, dict)
            else None
        )
        if session is None or started_at is None:
            changed_at = _parse_datetime(changed) or now
            started_at = min(changed_at, now)
            session = {
                "started_at": started_at.isoformat(),
                "planned_minutes": _planned_minutes(duration, maximum),
                "max_minutes": maximum,
                "finished": False,
            }
            sessions[room_id] = session

        elapsed = max(0.0, (now - started_at).total_seconds() / 60)
        room = rooms.get(room_id)
        valid = room is not None and room.attributes.get("inputs_available") is True
        beneficial = valid and room.attributes.get("ventilation_beneficial") is True

        if not session.get("finished"):
            if elapsed >= session["max_minutes"]:
                session["finished"] = True
                session["reason"] = (
                    "Maximale Lüftungsdauer erreicht. Fenster schließen und "
                    "Raumklima erneut prüfen."
                )
            elif valid and not beneficial:
                session["finished"] = True
                session["reason"] = (
                    "Die Außenluft bringt diesem Raum keinen weiteren Feuchte- "
                    "oder Temperaturvorteil."
                )
            elif elapsed >= session["planned_minutes"] and not valid:
                session["finished"] = True
                session["reason"] = (
                    "Empfohlene Zeit erreicht, aktuelle Raumwerte fehlen. "
                    "Fenster schließen und Werte prüfen."
                )

        if session.get("finished"):
            phase = "finish"
            remaining = 0
        elif elapsed >= session["planned_minutes"]:
            phase = "continue"
            remaining = max(0, math.ceil(session["max_minutes"] - elapsed))
        else:
            phase = "active"
            remaining = max(0, math.ceil(session["planned_minutes"] - elapsed))

        results.append(
            {
                **session,
                "room_id": room_id,
                "room_name": room.name if room else room_id,
                "elapsed_minutes": round(elapsed, 1),
                "remaining_minutes": remaining,
                "phase": phase,
                "data_quality": "good" if valid else "unavailable",
            }
        )

    return results
